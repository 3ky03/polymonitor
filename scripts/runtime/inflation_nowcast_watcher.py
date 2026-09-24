#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone watcher for Cleveland Fed inflation nowcast snapshots."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

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

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from api.config import load_api_settings
from api.services import runtime_service
from runtime.seed_meta import SeedMetaStore, build_seed_meta_payload
from runtime.snapshot_store import SnapshotStore


DEFAULT_INTERVAL_SECONDS = 1800
SEED_META_NAMESPACE = "seed-meta:macro"
SEED_META_CACHE_KEY = "inflation-nowcast"
SEED_META_SERVICE_NAME = "polydata-inflation-nowcast-seed.service"


class _LoggerAdapter:
    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[inflation-nowcast] ERROR {message % args if args else message}", file=sys.stderr)


class _AppAdapter:
    logger = _LoggerAdapter()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{str(prefix or '')}{namespace}:{cache_key}"


def _has_nowcast_data(payload: Dict[str, Any]) -> bool:
    return bool(payload.get("monthOverMonth") or payload.get("yearOverYear") or payload.get("quarterly"))


class InflationNowcastWatcher:
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
        if BeautifulSoup is None:
            raise RuntimeError("beautifulsoup4 package is required. Install scripts/requirements.txt")
        if not str(redis_url or "").strip():
            raise RuntimeError("POLYDATA_REDIS_URL is required for inflation nowcast watcher")
        self.settings = settings
        self.interval_seconds = max(300, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
        self.redis_prefix = str(redis_prefix or "")
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.snapshot_store = SnapshotStore(snapshot_sqlite_path)
        self.seed_meta_store = SeedMetaStore(redis_client=self.redis_client, redis_prefix=self.redis_prefix, snapshot_store=self.snapshot_store)
        self.requests = requests.Session()
        self.requests.trust_env = False

    def ttl_seconds(self) -> int:
        configured = int(os.environ.get("POLYDATA_INFLATION_NOWCAST_SEED_TTL_SECONDS", "0") or 0)
        if configured > 0:
            return configured
        return max(3600, self.interval_seconds * 3)

    def namespace(self) -> str:
        return runtime_service.INFLATION_NOWCAST_NAMESPACE

    def cache_key(self) -> str:
        return runtime_service.INFLATION_NOWCAST_CACHE_KEY

    def redis_key(self) -> str:
        return _redis_key(self.redis_prefix, self.namespace(), self.cache_key())

    def service_context(self) -> Dict[str, Any]:
        return {
            "SETTINGS": self.settings,
            "FINANCE_RUNTIME_TTL_SECONDS": self.settings.finance_runtime_ttl_seconds,
            "app": _AppAdapter(),
            "requests": self.requests,
            "BeautifulSoup": BeautifulSoup,
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
            print("[inflation-nowcast] WARN redis read failed", file=sys.stderr)
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
        if not preserve_last_success and status in {"ok", "degraded", "preserved"}:
            last_success_at = attempted_at
        payload = build_seed_meta_payload(
            panel_id="inflation-nowcast",
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
            payload = runtime_service.fetch_live_inflation_nowcast_payload(self.service_context())
        except Exception as exc:
            if previous:
                self.store_payload(previous)
                self.store_seed_meta(
                    status="preserved",
                    record_count=1 if _has_nowcast_data(previous) else 0,
                    source_states={"clevelandFed": "error"},
                    error_summary=str(exc),
                    preserve_last_success=True,
                )
                return {"status": "preserved", "payload": previous}
            self.store_seed_meta(status="error", record_count=0, source_states={"clevelandFed": "error"}, error_summary=str(exc), preserve_last_success=True)
            raise

        if previous and not _has_nowcast_data(payload):
            self.store_payload(previous)
            self.store_seed_meta(
                status="preserved",
                record_count=1 if _has_nowcast_data(previous) else 0,
                source_states={"clevelandFed": "empty"},
                error_summary="Preserved previous nowcast because new payload had no tables",
                preserve_last_success=True,
            )
            return {"status": "preserved", "payload": previous}

        payload = {**payload, "cacheMode": "seeded"}
        self.store_payload(payload)
        record_count = 1 if _has_nowcast_data(payload) else 0
        status = "ok" if record_count else "degraded"
        self.store_seed_meta(
            status=status,
            record_count=record_count,
            source_states={"clevelandFed": "ok" if record_count else "empty"},
            error_summary=None if record_count else "Inflation nowcast payload contained no tables",
        )
        return {"status": status, "payload": payload}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed Cleveland Fed inflation nowcast snapshots into Redis and SQLite")
    parser.add_argument("--watch", action="store_true", help="Run continuously instead of once")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("POLYDATA_INFLATION_NOWCAST_WATCH_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)))
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    settings = load_api_settings()
    watcher = InflationNowcastWatcher(
        redis_url=settings.redis_url,
        redis_prefix=settings.redis_prefix,
        snapshot_sqlite_path=settings.snapshot_sqlite_path,
        settings=settings,
        interval_seconds=args.interval,
    )
    watcher.redis_client.ping()
    print(f"[inflation-nowcast] redis_key={watcher.redis_key()} sqlite={settings.snapshot_sqlite_path}", file=sys.stderr)
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
            print(f"[inflation-nowcast] ERROR watch loop failed: {exc}", file=sys.stderr)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
