#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seed the homepage bootstrap payload into Redis and SQLite."""

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

from api.config import load_api_settings
from api.runtime_panels import get_default_panel_ids
from api.services import bootstrap_service
from runtime.seed_meta import SeedMetaStore, build_seed_meta_payload
from runtime.snapshot_store import SnapshotStore


DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_DB_READ_TIMEOUT_SECONDS = 20
SEED_META_NAMESPACE = "seed-meta:bootstrap"
SEED_META_CACHE_KEY = "bootstrap"
SEED_META_SERVICE_NAME = "polydata-bootstrap-seed.service"


class _LoggerAdapter:
    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[bootstrap] {message % args if args else message}", file=sys.stderr)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[bootstrap] WARN {message % args if args else message}", file=sys.stderr)

    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[bootstrap] ERROR {message % args if args else message}", file=sys.stderr)


class _AppAdapter:
    logger = _LoggerAdapter()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{str(prefix or '')}{namespace}:{cache_key}"


def _record_count(payload: Dict[str, Any]) -> int:
    count = 0
    for key in (
        "activeMarketsPreview",
        "globalTradesPreview",
        "globalOraclePreview",
        "latestContentPreview",
        "recentTradesPreview",
        "oraclePreview",
        "contentPreview",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            count += len(value)
        elif isinstance(value, dict) and isinstance(value.get("items"), list):
            count += len(value["items"])
    return count


def _is_renderable(payload: Dict[str, Any]) -> bool:
    return isinstance(payload.get("defaultWorkspace"), dict) and isinstance(payload.get("activeMarketsPreview"), list)


def _refresh_default_workspace(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(payload)
    workspace = dict(normalized.get("defaultWorkspace") or {})
    workspace["panels"] = get_default_panel_ids()
    normalized["defaultWorkspace"] = workspace
    return normalized


class BootstrapWatcher:
    def __init__(
        self,
        *,
        redis_url: str,
        redis_prefix: str,
        snapshot_sqlite_path: str,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        if redis is None:
            raise RuntimeError("redis package is required. Install scripts/requirements.txt")
        if not str(redis_url or "").strip():
            raise RuntimeError("POLYDATA_REDIS_URL is required for bootstrap watcher")
        self.interval_seconds = max(15, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
        self.redis_prefix = str(redis_prefix or "")
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.snapshot_store = SnapshotStore(snapshot_sqlite_path)
        self.seed_meta_store = SeedMetaStore(redis_client=self.redis_client, redis_prefix=self.redis_prefix, snapshot_store=self.snapshot_store)

    def ttl_seconds(self) -> int:
        configured = int(os.environ.get("POLYDATA_BOOTSTRAP_SEED_TTL_SECONDS", "0") or 0)
        if configured > 0:
            return configured
        return max(60, self.interval_seconds * 3)

    def redis_key(self) -> str:
        return _redis_key(self.redis_prefix, "bootstrap", bootstrap_service.BOOTSTRAP_CACHE_KEY)

    def load_previous_payload(self) -> Dict[str, Any]:
        try:
            raw = self.redis_client.get(self.redis_key())
            if raw:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    return payload
        except Exception:
            print("[bootstrap] WARN redis read failed", file=sys.stderr)
        stale = self.snapshot_store.get_stale(bootstrap_service.BOOTSTRAP_SNAPSHOT_NAMESPACE, bootstrap_service.BOOTSTRAP_CACHE_KEY)
        return stale if isinstance(stale, dict) else {}

    def store_payload(self, payload: Dict[str, Any]) -> None:
        ttl_seconds = self.ttl_seconds()
        payload = _refresh_default_workspace(payload)
        self.snapshot_store.set(bootstrap_service.BOOTSTRAP_SNAPSHOT_NAMESPACE, bootstrap_service.BOOTSTRAP_CACHE_KEY, payload, ttl_seconds)
        self.redis_client.set(self.redis_key(), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl_seconds)

    def load_seed_meta(self) -> Dict[str, Any]:
        payload = self.seed_meta_store.load(SEED_META_NAMESPACE, SEED_META_CACHE_KEY)
        return payload if isinstance(payload, dict) else {}

    def store_seed_meta(
        self,
        *,
        status: str,
        record_count: int,
        error_summary: Optional[str] = None,
        preserve_last_success: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        previous = self.load_seed_meta()
        attempted_at = utc_now_iso()
        last_success_at = previous.get("lastSuccessAt")
        if not preserve_last_success and str(status or "").strip().lower() in {"ok", "degraded", "preserved"}:
            last_success_at = attempted_at
        payload = build_seed_meta_payload(
            panel_id="bootstrap",
            namespace=SEED_META_NAMESPACE,
            cache_key=SEED_META_CACHE_KEY,
            service_name=SEED_META_SERVICE_NAME,
            expected_interval_seconds=self.interval_seconds,
            status=status,
            last_attempt_at=attempted_at,
            last_success_at=last_success_at or attempted_at,
            record_count=record_count,
            source_states={"bootstrap": status},
            error_summary=error_summary,
            cache_mode="seeded",
            payload_status=status,
            metadata=metadata,
        )
        self.seed_meta_store.store(SEED_META_NAMESPACE, SEED_META_CACHE_KEY, payload)

    def service_context(self) -> Dict[str, Any]:
        import api_server

        ctx = api_server.build_service_context()
        ctx["app"] = _AppAdapter()
        ctx["DB_CONNECTION_EXIT_DISABLED"] = True
        return ctx

    def fetch_payload(self) -> Dict[str, Any]:
        payload = bootstrap_service.build_bootstrap_payload(self.service_context())
        return {**payload, "cacheMode": "seeded", "status": "ok"}

    def run_once(self) -> Dict[str, Any]:
        previous = self.load_previous_payload()
        try:
            payload = self.fetch_payload()
        except Exception as exc:
            if previous:
                preserved = _refresh_default_workspace({**previous, "cacheMode": "seeded", "status": "ok"})
                self.store_payload(preserved)
                self.store_seed_meta(
                    status="ok",
                    record_count=_record_count(previous),
                    error_summary=None,
                    metadata={"result": "preserved-current", "lastError": str(exc)},
                )
                return {"status": "ok", "recordCount": _record_count(previous), "preserved": True, "error": str(exc)}
            self.store_seed_meta(status="error", record_count=0, error_summary=str(exc), preserve_last_success=True, metadata={"result": "error"})
            return {"status": "error", "recordCount": 0, "error": str(exc)}

        record_count = _record_count(payload)
        if previous and not _is_renderable(payload):
            preserved = {**previous, "cacheMode": "seeded", "status": previous.get("status") or "stale"}
            self.store_payload(preserved)
            self.store_seed_meta(
                status="preserved",
                record_count=_record_count(previous),
                error_summary="bootstrap payload was not renderable",
                metadata={"result": "preserved-invalid"},
            )
            return {"status": "preserved", "recordCount": _record_count(previous), "error": "invalid payload"}

        status = "ok" if _is_renderable(payload) else "degraded"
        self.store_payload({**payload, "status": status, "cacheMode": "seeded"})
        self.store_seed_meta(status=status, record_count=record_count, error_summary=None, metadata={"result": "stored"})
        return {"status": status, "recordCount": record_count}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed bootstrap payload into Redis and SQLite")
    parser.add_argument("--watch", action="store_true", help="Run continuously instead of once")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("POLYDATA_BOOTSTRAP_SEED_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)))
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    db_read_timeout = max(5, int(os.environ.get("POLYDATA_BOOTSTRAP_DB_READ_TIMEOUT_SECONDS", DEFAULT_DB_READ_TIMEOUT_SECONDS)))
    current_read_timeout = int(os.environ.get("POLYMARKET_MYSQL_READ_TIMEOUT", "60") or "60")
    if current_read_timeout > db_read_timeout:
        os.environ["POLYMARKET_MYSQL_READ_TIMEOUT"] = str(db_read_timeout)
    settings = load_api_settings()
    watcher = BootstrapWatcher(
        redis_url=settings.redis_url,
        redis_prefix=settings.redis_prefix,
        snapshot_sqlite_path=settings.snapshot_sqlite_path,
        interval_seconds=args.interval,
    )
    watcher.redis_client.ping()
    print(f"[bootstrap] redis_key={watcher.redis_key()} sqlite={settings.snapshot_sqlite_path}", file=sys.stderr)
    if not args.watch:
        print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        return 0

    interval_seconds = max(15, int(args.interval or DEFAULT_INTERVAL_SECONDS))
    while True:
        try:
            print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            watcher.store_seed_meta(status="error", record_count=0, error_summary=str(exc), preserve_last_success=True, metadata={"result": "exception"})
            print(f"[bootstrap] ERROR watch loop failed: {exc}", file=sys.stderr)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
