#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone watcher for finance external source snapshots."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

_scripts_root = Path(__file__).resolve().parents[1]
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

try:
    import redis
except ImportError:
    redis = None

try:
    import requests
except ImportError:
    requests = None

from api.config import load_api_settings
from api.clients import market_data_client
from api.services import finance_external_sources_service
from runtime.seed_meta import SeedMetaStore, build_seed_meta_payload
from runtime.snapshot_store import SnapshotStore


DEFAULT_INTERVAL_SECONDS = 15 * 60
SEED_META_NAMESPACE = "seed-meta:finance"
SEED_META_CACHE_KEY = "external-sources"
SEED_META_SERVICE_NAME = "polydata-finance-external-sources-seed.service"


class _LoggerAdapter:
    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[finance-external] ERROR {message % args if args else message}", file=sys.stderr)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[finance-external] WARN {message % args if args else message}", file=sys.stderr)


class _AppAdapter:
    logger = _LoggerAdapter()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{str(prefix or '')}{namespace}:{cache_key}"


def _items(payload: Dict[str, Any]) -> list[Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    count = sum(
        int(summary.get(key) or 0)
        for key in ("perpCount", "etfCount", "cotCount", "stablecoinCount")
    )
    return [None] * count


class FinanceExternalSourcesWatcher:
    def __init__(
        self,
        *,
        redis_url: str,
        redis_prefix: str,
        snapshot_sqlite_path: str,
        settings: Any,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        if redis is None:
            raise RuntimeError("redis package is required. Install scripts/requirements.txt")
        if requests is None:
            raise RuntimeError("requests package is required. Install scripts/requirements.txt")
        if not str(redis_url or "").strip():
            raise RuntimeError("POLYDATA_REDIS_URL is required for finance external source watcher")
        self.settings = settings
        self.interval_seconds = max(300, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
        self.redis_prefix = str(redis_prefix or "")
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.snapshot_store = SnapshotStore(snapshot_sqlite_path)
        self.seed_meta_store = SeedMetaStore(redis_client=self.redis_client, redis_prefix=self.redis_prefix, snapshot_store=self.snapshot_store)
        self.requests = requests.Session()
        self.requests.trust_env = False

    def namespace(self) -> str:
        return finance_external_sources_service.FINANCE_EXTERNAL_NAMESPACE

    def cache_key(self) -> str:
        return finance_external_sources_service.FINANCE_EXTERNAL_CACHE_KEY

    def redis_key(self) -> str:
        return _redis_key(self.redis_prefix, self.namespace(), self.cache_key())

    def ttl_seconds(self) -> int:
        configured = int(os.environ.get("POLYDATA_FINANCE_EXTERNAL_SOURCES_SEED_TTL_SECONDS", "0") or 0)
        if configured > 0:
            return configured
        return max(900, self.interval_seconds * 3)

    def http_json_get(self, url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 12, headers: Optional[Dict[str, str]] = None) -> Any:
        response = self.requests.get(url, params=params, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.json() if response.content else None

    def http_json_post(self, url: str, json_payload: Dict[str, Any], timeout: int = 12, headers: Optional[Dict[str, str]] = None) -> Any:
        response = self.requests.post(url, json=json_payload, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.json() if response.content else None

    def get_yahoo_market_snapshot(self, symbol: str, interval: str = "30m", range_name: str = "5d", ttl_seconds: Optional[int] = None) -> Optional[Dict[str, Any]]:
        return market_data_client.get_yahoo_market_snapshot(
            self.service_context(),
            symbol,
            interval=interval,
            range_name=range_name,
            ttl_seconds=ttl_seconds,
        )

    def service_context(self) -> Dict[str, Any]:
        return {
            "SETTINGS": self.settings,
            "FINANCE_RUNTIME_TTL_SECONDS": self.settings.finance_runtime_ttl_seconds,
            "_safe_float": _safe_float,
            "_clob_price_cache": {},
            "_clob_price_cache_lock": __import__("threading").Lock(),
            "app": _AppAdapter(),
            "get_cached_runtime_payload": lambda namespace, cache_key: None,
            "set_cached_runtime_payload": lambda namespace, cache_key, payload, ttl_seconds=300: payload,
            "http_json_get": self.http_json_get,
            "http_json_post": self.http_json_post,
            "get_yahoo_market_snapshot": self.get_yahoo_market_snapshot,
            "utc_now_iso": utc_now_iso,
        }

    def load_previous_payload(self) -> Dict[str, Any]:
        try:
            raw = self.redis_client.get(self.redis_key())
            if raw:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    return payload
        except Exception:
            print("[finance-external] WARN redis read failed", file=sys.stderr)
        stale = self.snapshot_store.get_stale(self.namespace(), self.cache_key())
        return stale if isinstance(stale, dict) else {}

    def store_payload(self, payload: Dict[str, Any]) -> None:
        ttl_seconds = self.ttl_seconds()
        self.snapshot_store.set(self.namespace(), self.cache_key(), payload, ttl_seconds)
        self.redis_client.set(self.redis_key(), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl_seconds)

    def store_seed_meta(self, *, status: str, record_count: int, source_states: Dict[str, Any], error_summary: str | None, preserve_last_success: bool = False) -> None:
        previous = self.seed_meta_store.load(SEED_META_NAMESPACE, SEED_META_CACHE_KEY) or {}
        attempted_at = utc_now_iso()
        last_success_at = previous.get("lastSuccessAt")
        if not preserve_last_success and status in {"ok", "partial", "degraded", "preserved"}:
            last_success_at = attempted_at
        payload = build_seed_meta_payload(
            panel_id="finance-external-sources",
            namespace=SEED_META_NAMESPACE,
            cache_key=SEED_META_CACHE_KEY,
            service_name=SEED_META_SERVICE_NAME,
            expected_interval_seconds=self.interval_seconds,
            status=status,
            last_attempt_at=attempted_at,
            last_success_at=last_success_at or attempted_at,
            record_count=record_count,
            source_states=source_states,
            error_summary=error_summary,
            cache_mode="seeded",
            payload_status=status,
            metadata={"result": "stored"},
        )
        self.seed_meta_store.store(SEED_META_NAMESPACE, SEED_META_CACHE_KEY, payload)

    def run_once(self) -> Dict[str, Any]:
        previous = self.load_previous_payload()
        try:
            payload = finance_external_sources_service.build_finance_external_sources_payload(self.service_context())
        except Exception as exc:
            if previous:
                self.store_payload(previous)
                self.store_seed_meta(
                    status="preserved",
                    record_count=len(_items(previous)),
                    source_states={"financeExternal": "error"},
                    error_summary=str(exc),
                    preserve_last_success=True,
                )
                return {"status": "preserved", "payload": previous}
            self.store_seed_meta(status="error", record_count=0, source_states={"financeExternal": "error"}, error_summary=str(exc), preserve_last_success=True)
            raise

        record_count = len(_items(payload))
        if previous and record_count <= 0:
            self.store_payload(previous)
            self.store_seed_meta(
                status="preserved",
                record_count=len(_items(previous)),
                source_states=payload.get("sources") if isinstance(payload.get("sources"), dict) else {"financeExternal": "empty"},
                error_summary="Preserved previous finance external source snapshot because new payload was empty",
                preserve_last_success=True,
            )
            return {"status": "preserved", "payload": previous}

        payload = {**payload, "cacheMode": "seeded"}
        self.store_payload(payload)
        status = str(payload.get("status") or ("ok" if record_count else "degraded"))
        self.store_seed_meta(
            status=status if status in {"ok", "partial", "degraded"} else "degraded",
            record_count=record_count,
            source_states=payload.get("sources") if isinstance(payload.get("sources"), dict) else {},
            error_summary="; ".join((payload.get("errors") or {}).values()) if payload.get("errors") else None,
        )
        return {"status": status, "payload": payload}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed finance external source snapshots into Redis and SQLite")
    parser.add_argument("--watch", action="store_true", help="Run continuously instead of once")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("POLYDATA_FINANCE_EXTERNAL_SOURCES_WATCH_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)))
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    settings = load_api_settings()
    watcher = FinanceExternalSourcesWatcher(
        redis_url=settings.redis_url,
        redis_prefix=settings.redis_prefix,
        snapshot_sqlite_path=settings.snapshot_sqlite_path,
        settings=settings,
        interval_seconds=args.interval,
    )
    watcher.redis_client.ping()
    print(f"[finance-external] redis_key={watcher.redis_key()} sqlite={settings.snapshot_sqlite_path}", file=sys.stderr)
    if not args.watch:
        print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        return 0
    while True:
        try:
            print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"[finance-external] ERROR watch loop failed: {exc}", file=sys.stderr)
        time.sleep(watcher.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
