#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seed finance watch panel snapshots into Redis and SQLite."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
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

from api.clients import market_data_client
from api.config import load_api_settings
from api.services import finance_watch_panels_service
from runtime.seed_meta import SeedMetaStore, build_seed_meta_payload
from runtime.snapshot_store import SnapshotStore


DEFAULT_INTERVAL_SECONDS = 10 * 60
DEFAULT_LIMIT = 24
SEED_META_NAMESPACE = "seed-meta:finance"
SEED_META_SERVICE_NAME = "polydata-finance-watch-panels-seed.service"


class _LoggerAdapter:
    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[finance-watch] ERROR {message % args if args else message}", file=sys.stderr)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[finance-watch] WARN {message % args if args else message}", file=sys.stderr)


class _AppAdapter:
    logger = _LoggerAdapter()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{str(prefix or '')}{namespace}:{cache_key}"


class FinanceWatchPanelsWatcher:
    def __init__(
        self,
        *,
        redis_url: str,
        redis_prefix: str,
        snapshot_sqlite_path: str,
        settings: Any,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        limit: int = DEFAULT_LIMIT,
    ) -> None:
        if redis is None:
            raise RuntimeError("redis package is required. Install scripts/requirements.txt")
        if requests is None:
            raise RuntimeError("requests package is required. Install scripts/requirements.txt")
        if not str(redis_url or "").strip():
            raise RuntimeError("POLYDATA_REDIS_URL is required for finance watch panels watcher")
        self.settings = settings
        self.interval_seconds = max(300, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
        self.limit = max(6, min(36, int(limit or DEFAULT_LIMIT)))
        self.redis_prefix = str(redis_prefix or "")
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.snapshot_store = SnapshotStore(snapshot_sqlite_path)
        self.seed_meta_store = SeedMetaStore(redis_client=self.redis_client, redis_prefix=self.redis_prefix, snapshot_store=self.snapshot_store)
        self.requests = requests.Session()
        trust_env_proxy = str(os.environ.get("POLYDATA_FINANCE_WATCH_PANELS_TRUST_ENV_PROXY") or "").strip().lower()
        self.requests.trust_env = trust_env_proxy in {"1", "true", "yes", "on"}
        self._runtime_cache: Dict[str, Dict[str, Any]] = {}

    def ttl_seconds(self) -> int:
        configured = int(os.environ.get("POLYDATA_FINANCE_WATCH_PANELS_SEED_TTL_SECONDS", "0") or 0)
        if configured > 0:
            return configured
        return max(900, self.interval_seconds * 3)

    def redis_key(self, panel_id: str) -> str:
        return _redis_key(self.redis_prefix, finance_watch_panels_service.finance_watch_namespace(panel_id), finance_watch_panels_service.FINANCE_WATCH_CACHE_KEY)

    def http_json_get(self, url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 12, headers: Optional[Dict[str, str]] = None) -> Any:
        response = self.requests.get(url, params=params, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.json() if response.content else None

    def http_text_get(self, url: str, timeout: int = 12, headers: Optional[Dict[str, str]] = None) -> str:
        response = self.requests.get(url, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.text

    def http_json_post(self, url: str, json_payload: Dict[str, Any], timeout: int = 12, headers: Optional[Dict[str, str]] = None) -> Any:
        response = self.requests.post(url, json=json_payload, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.json() if response.content else None

    def get_cached_json(self, namespace: str, cache_key: str) -> Optional[Dict[str, Any]]:
        raw = self.redis_client.get(_redis_key(self.redis_prefix, namespace, cache_key))
        if not raw:
            payload = self.snapshot_store.get(namespace, cache_key)
            return payload if isinstance(payload, dict) else None
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None

    def set_cached_json(self, namespace: str, cache_key: str, payload: Dict[str, Any], ttl_seconds: int) -> Dict[str, Any]:
        self.snapshot_store.set(namespace, cache_key, payload, ttl_seconds)
        self.redis_client.set(_redis_key(self.redis_prefix, namespace, cache_key), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl_seconds)
        return payload

    def get_cached_runtime_payload(self, namespace: str, cache_key: str) -> Optional[Dict[str, Any]]:
        return self._runtime_cache.get(f"{namespace}:{cache_key}")

    def set_cached_runtime_payload(self, namespace: str, cache_key: str, payload: Dict[str, Any], ttl_seconds: int = 300) -> Dict[str, Any]:
        self._runtime_cache[f"{namespace}:{cache_key}"] = payload
        return payload

    def get_snapshot_payload(self, namespace: str, cache_key: str, builder, *, ttl_seconds: int) -> Any:
        cached = self.get_cached_json(namespace, cache_key)
        if cached is not None:
            return cached
        payload = builder()
        if isinstance(payload, dict):
            self.set_cached_json(namespace, cache_key, payload, ttl_seconds)
        return payload

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
            "FINANCE_RUNTIME_TTL_SECONDS": getattr(self.settings, "finance_runtime_ttl_seconds", 300),
            "SNAPSHOT_STORE": self.snapshot_store,
            "_safe_float": finance_watch_panels_service._safe_float,
            "_clob_price_cache": {},
            "_clob_price_cache_lock": threading.Lock(),
            "app": _AppAdapter(),
            "get_cached_json": self.get_cached_json,
            "set_cached_json": self.set_cached_json,
            "get_cached_runtime_payload": self.get_cached_runtime_payload,
            "set_cached_runtime_payload": self.set_cached_runtime_payload,
            "get_snapshot_payload": self.get_snapshot_payload,
            "http_json_get": self.http_json_get,
            "http_json_post": self.http_json_post,
            "http_text_get": self.http_text_get,
            "get_yahoo_market_snapshot": self.get_yahoo_market_snapshot,
            "utc_now_iso": utc_now_iso,
            "requests": requests,
        }

    def load_previous_payload(self, panel_id: str) -> Dict[str, Any]:
        try:
            raw = self.redis_client.get(self.redis_key(panel_id))
            if raw:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    return payload
        except Exception:
            print(f"[finance-watch] WARN redis read failed panel={panel_id}", file=sys.stderr)
        stale = self.snapshot_store.get_stale(finance_watch_panels_service.finance_watch_namespace(panel_id), finance_watch_panels_service.FINANCE_WATCH_CACHE_KEY)
        return stale if isinstance(stale, dict) else {}

    def store_payload(self, panel_id: str, payload: Dict[str, Any]) -> None:
        ttl_seconds = self.ttl_seconds()
        namespace = finance_watch_panels_service.finance_watch_namespace(panel_id)
        payload = {**payload, "cacheMode": "seeded"}
        self.snapshot_store.set(namespace, finance_watch_panels_service.FINANCE_WATCH_CACHE_KEY, payload, ttl_seconds)
        self.redis_client.set(self.redis_key(panel_id), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl_seconds)

    def store_seed_meta(self, panel_id: str, *, status: str, record_count: int, source_states: Dict[str, Any], error_summary: str | None, preserve_last_success: bool = False) -> None:
        previous = self.seed_meta_store.load(SEED_META_NAMESPACE, panel_id) or {}
        attempted_at = utc_now_iso()
        last_success_at = previous.get("lastSuccessAt")
        if not preserve_last_success and status in {"ok", "partial", "degraded", "preserved"}:
            last_success_at = attempted_at
        payload = build_seed_meta_payload(
            panel_id=panel_id,
            namespace=SEED_META_NAMESPACE,
            cache_key=panel_id,
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
            metadata={"result": "stored", "limit": self.limit},
        )
        self.seed_meta_store.store(SEED_META_NAMESPACE, panel_id, payload)

    def run_once(self) -> Dict[str, Any]:
        ctx = self.service_context()
        payloads = finance_watch_panels_service.build_all_finance_watch_panel_payloads(ctx, limit=self.limit)
        result: Dict[str, Any] = {"status": "ok", "panels": {}}
        for panel_id in finance_watch_panels_service.FINANCE_WATCH_PANEL_IDS:
            payload = payloads.get(panel_id) or {}
            previous = self.load_previous_payload(panel_id)
            record_count = len(payload.get("items") or []) if isinstance(payload, dict) else 0
            status = str(payload.get("status") or ("ok" if record_count else "empty")) if isinstance(payload, dict) else "error"
            replace_with_empty = panel_id == "broker-research-watch" and status == "empty"
            if previous and (status == "error" or record_count <= 0) and not replace_with_empty:
                self.store_payload(panel_id, previous)
                self.store_seed_meta(
                    panel_id,
                    status="preserved",
                    record_count=len(previous.get("items") or []),
                    source_states={"financeWatch": status},
                    error_summary=(payload.get("summary") or {}).get("error") if isinstance(payload.get("summary"), dict) else "empty payload",
                    preserve_last_success=True,
                )
                result["panels"][panel_id] = "preserved"
                continue
            self.store_payload(panel_id, payload)
            self.store_seed_meta(
                panel_id,
                status=status if status in {"ok", "partial", "degraded"} else ("degraded" if record_count else "empty"),
                record_count=record_count,
                source_states=payload.get("sources") if isinstance(payload.get("sources"), dict) else {},
                error_summary=(payload.get("summary") or {}).get("error") if isinstance(payload.get("summary"), dict) else None,
            )
            result["panels"][panel_id] = status
        return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed finance watch panel snapshots into Redis and SQLite")
    parser.add_argument("--watch", action="store_true", help="Run continuously instead of once")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("POLYDATA_FINANCE_WATCH_PANELS_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)))
    parser.add_argument("--limit", type=int, default=int(os.environ.get("POLYDATA_FINANCE_WATCH_PANELS_LIMIT", DEFAULT_LIMIT)))
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    settings = load_api_settings()
    watcher = FinanceWatchPanelsWatcher(
        redis_url=settings.redis_url,
        redis_prefix=settings.redis_prefix,
        snapshot_sqlite_path=settings.snapshot_sqlite_path,
        settings=settings,
        interval_seconds=args.interval,
        limit=args.limit,
    )
    watcher.redis_client.ping()
    print(f"[finance-watch] panels={','.join(finance_watch_panels_service.FINANCE_WATCH_PANEL_IDS)} sqlite={settings.snapshot_sqlite_path}", file=sys.stderr)
    if not args.watch:
        print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        return 0
    interval_seconds = max(300, int(args.interval or DEFAULT_INTERVAL_SECONDS))
    while True:
        try:
            print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"[finance-watch] ERROR watch loop failed: {exc}", file=sys.stderr)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
