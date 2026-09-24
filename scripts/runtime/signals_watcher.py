#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seed alpha, whale, and suspicious trade signal panel snapshots."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_scripts_root = Path(__file__).resolve().parents[1]
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))
_repo_root = _scripts_root.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

try:
    import redis
except ImportError:
    redis = None

from api.config import load_api_settings
from api.services import polybeats_service, signal_service
from runtime.seed_meta import SeedMetaStore, build_seed_meta_payload
from runtime.snapshot_store import SnapshotStore
from runtime.telegram_panel_publish import publish_cached_panel_snapshot


DEFAULT_INTERVAL_SECONDS = 45
DEFAULT_ALPHA_LIMIT = 8
DEFAULT_WHALE_LIMIT = 14
DEFAULT_SUSPICIOUS_LIMIT = 12
DEFAULT_POLYBEATS_LIMIT = 8
DEFAULT_DB_READ_TIMEOUT_SECONDS = 12
DEFAULT_SIGNAL_DATA_STALE_AFTER_SECONDS = 7 * 24 * 60 * 60
SEED_META_NAMESPACE = "seed-meta:signals"


COMPONENTS = {
    "alpha": {
        "panel_id": "alpha-signal",
        "cache_key": "alpha-signal",
        "namespace": signal_service.SIGNAL_SNAPSHOT_NAMESPACE_ALPHA,
        "service_name": "polydata-alpha-signal-seed.service",
        "limit_env": "POLYDATA_ALPHA_SIGNAL_LIMIT",
        "default_limit": DEFAULT_ALPHA_LIMIT,
        "cache_key_builder": signal_service.build_alpha_signal_cache_key,
        "fetcher": signal_service.fetch_live_alpha_signal_payload,
    },
    "whales": {
        "panel_id": "whale-trades",
        "cache_key": "whale-trades",
        "namespace": signal_service.SIGNAL_SNAPSHOT_NAMESPACE_WHALES,
        "service_name": "polydata-whale-trades-seed.service",
        "limit_env": "POLYDATA_WHALE_TRADES_LIMIT",
        "default_limit": DEFAULT_WHALE_LIMIT,
        "cache_key_builder": signal_service.build_whale_trades_cache_key,
        "fetcher": signal_service.fetch_live_whale_trades_payload,
    },
    "suspicious": {
        "panel_id": "suspicious-trades",
        "cache_key": "suspicious-trades",
        "namespace": signal_service.SIGNAL_SNAPSHOT_NAMESPACE_SUSPICIOUS,
        "service_name": "polydata-suspicious-trades-seed.service",
        "limit_env": "POLYDATA_SUSPICIOUS_TRADES_LIMIT",
        "default_limit": DEFAULT_SUSPICIOUS_LIMIT,
        "cache_key_builder": signal_service.build_suspicious_trades_cache_key,
        "fetcher": signal_service.fetch_live_suspicious_trades_payload,
    },
    "polybeats": {
        "panel_id": polybeats_service.PANEL_ID,
        "cache_key": polybeats_service.PANEL_ID,
        "namespace": polybeats_service.SNAPSHOT_NAMESPACE,
        "service_name": "polydata-polybeats-feed-seed.service",
        "limit_env": "POLYDATA_POLYBEATS_LIMIT",
        "default_limit": DEFAULT_POLYBEATS_LIMIT,
        "cache_key_builder": polybeats_service.build_polybeats_cache_key,
        "fetcher": polybeats_service.fetch_live_polybeats_payload,
    },
}


class _LoggerAdapter:
    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[signals] ERROR {message % args if args else message}", file=sys.stderr)

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[signals] {message % args if args else message}", file=sys.stderr)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[signals] WARN {message % args if args else message}", file=sys.stderr)


class _AppAdapter:
    logger = _LoggerAdapter()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{str(prefix or '')}{namespace}:{cache_key}"


def _record_count(payload: Dict[str, Any]) -> int:
    items = payload.get("items")
    return len(items) if isinstance(items, list) else 0


def _parse_item_timestamp(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None


def _payload_timestamp_stats(payload: Dict[str, Any], *, stale_after_seconds: int) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    timestamps: list[datetime] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        for key in ("timestamp", "eventTime", "lastTradeAt"):
            parsed = _parse_item_timestamp(item.get(key))
            if parsed is not None:
                timestamps.append(parsed.astimezone(timezone.utc))
                break
    if not timestamps:
        return {
            "maxItemTimestamp": None,
            "oldestItemTimestamp": None,
            "dataAgeSeconds": None,
            "oldestDataAgeSeconds": None,
            "staleItemCount": 0,
            "freshItemCount": 0,
        }
    newest = max(timestamps)
    oldest = min(timestamps)
    ages = [max(0, int((now - ts).total_seconds())) for ts in timestamps]
    stale_count = sum(1 for age in ages if age > stale_after_seconds)
    return {
        "maxItemTimestamp": newest.isoformat().replace("+00:00", "Z"),
        "oldestItemTimestamp": oldest.isoformat().replace("+00:00", "Z"),
        "dataAgeSeconds": max(0, int((now - newest).total_seconds())),
        "oldestDataAgeSeconds": max(0, int((now - oldest).total_seconds())),
        "staleItemCount": stale_count,
        "freshItemCount": len(timestamps) - stale_count,
    }


class SignalsWatcher:
    def __init__(
        self,
        *,
        redis_url: str,
        redis_prefix: str,
        snapshot_sqlite_path: str,
        component: str,
        limit: int,
        interval_seconds: int,
    ) -> None:
        if redis is None:
            raise RuntimeError("redis package is required. Install scripts/requirements.txt")
        if component not in COMPONENTS:
            raise ValueError(f"Unsupported signal component: {component}")
        if not str(redis_url or "").strip():
            raise RuntimeError("POLYDATA_REDIS_URL is required for signals watcher")
        self.component = component
        self.spec = COMPONENTS[component]
        self.limit = max(1, int(limit or self.spec["default_limit"]))
        self.interval_seconds = max(15, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
        self.redis_prefix = str(redis_prefix or "")
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.snapshot_store = SnapshotStore(snapshot_sqlite_path)
        self.seed_meta_store = SeedMetaStore(redis_client=self.redis_client, redis_prefix=self.redis_prefix, snapshot_store=self.snapshot_store)

    def ttl_seconds(self) -> int:
        configured = int(os.environ.get("POLYDATA_SIGNAL_SEED_TTL_SECONDS", "0") or 0)
        if configured > 0:
            return configured
        return max(60, self.interval_seconds * 3)

    def stale_after_seconds(self) -> int:
        configured = int(os.environ.get("POLYDATA_SIGNAL_DATA_STALE_AFTER_SECONDS", "0") or 0)
        return configured if configured > 0 else DEFAULT_SIGNAL_DATA_STALE_AFTER_SECONDS

    def cache_key(self) -> str:
        builder: Callable[..., str] = self.spec["cache_key_builder"]
        return builder(limit=self.limit)

    def namespace(self) -> str:
        return str(self.spec["namespace"])

    def redis_key(self) -> str:
        return _redis_key(self.redis_prefix, self.namespace(), self.cache_key())

    def load_previous_payload(self) -> Dict[str, Any]:
        try:
            raw = self.redis_client.get(self.redis_key())
            if raw:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    return payload
        except Exception:
            print(f"[signals] WARN redis read failed component={self.component}", file=sys.stderr)
        stale = self.snapshot_store.get_stale(self.namespace(), self.cache_key())
        return stale if isinstance(stale, dict) else {}

    def store_payload(self, payload: Dict[str, Any]) -> None:
        ttl_seconds = self.ttl_seconds()
        self.snapshot_store.set(self.namespace(), self.cache_key(), payload, ttl_seconds)
        self.redis_client.set(self.redis_key(), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl_seconds)

    def load_seed_meta(self) -> Dict[str, Any]:
        payload = self.seed_meta_store.load(SEED_META_NAMESPACE, str(self.spec["cache_key"]))
        return payload if isinstance(payload, dict) else {}

    def store_seed_meta(
        self,
        *,
        status: str,
        record_count: int,
        error_summary: Optional[str] = None,
        preserve_last_success: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        source_states: Optional[Dict[str, Any]] = None,
        payload_status: Optional[str] = None,
    ) -> None:
        previous = self.load_seed_meta()
        attempted_at = utc_now_iso()
        last_success_at = previous.get("lastSuccessAt")
        if not preserve_last_success and str(status or "").strip().lower() in {"ok", "degraded", "empty"}:
            last_success_at = attempted_at
        payload = build_seed_meta_payload(
            panel_id=str(self.spec["panel_id"]),
            namespace=SEED_META_NAMESPACE,
            cache_key=str(self.spec["cache_key"]),
            service_name=str(self.spec["service_name"]),
            expected_interval_seconds=self.interval_seconds,
            status=status,
            last_attempt_at=attempted_at,
            last_success_at=last_success_at or attempted_at,
            record_count=record_count,
            source_states=source_states or {"database": status},
            error_summary=error_summary,
            cache_mode="seeded",
            payload_status=payload_status or status,
            metadata=metadata,
        )
        self.seed_meta_store.store(SEED_META_NAMESPACE, str(self.spec["cache_key"]), payload)

    def service_context(self) -> Dict[str, Any]:
        import api_server

        ctx = dict(api_server.build_service_context())
        ctx["app"] = _AppAdapter()
        ctx["DB_CONNECTION_EXIT_DISABLED"] = True
        return ctx

    def fetch_payload(self) -> Dict[str, Any]:
        fetcher: Callable[..., Dict[str, Any]] = self.spec["fetcher"]
        payload = fetcher(self.service_context(), limit=self.limit)
        return {**payload, "cacheMode": "seeded"}

    def run_once(self) -> Dict[str, Any]:
        previous = self.load_previous_payload()
        try:
            payload = self.fetch_payload()
        except Exception as exc:
            if previous:
                preserved = {**previous, "cacheMode": "seeded", "status": previous.get("status") or "stale"}
                self.store_payload(preserved)
                stats = _payload_timestamp_stats(previous, stale_after_seconds=self.stale_after_seconds())
                self.store_seed_meta(
                    status="preserved",
                    record_count=_record_count(previous),
                    error_summary=str(exc),
                    preserve_last_success=True,
                    metadata={"result": "preserved", "component": self.component, **stats},
                    payload_status=preserved.get("status") or "preserved",
                )
                return {"status": "preserved", "recordCount": _record_count(previous), "error": str(exc)}
            self.store_seed_meta(
                status="error",
                record_count=0,
                error_summary=str(exc),
                preserve_last_success=True,
                metadata={"result": "error", "component": self.component},
            )
            return {"status": "error", "recordCount": 0, "error": str(exc)}

        record_count = _record_count(payload)
        if previous and record_count <= 0:
            preserved = {**previous, "cacheMode": "seeded", "status": previous.get("status") or "stale"}
            self.store_payload(preserved)
            stats = _payload_timestamp_stats(previous, stale_after_seconds=self.stale_after_seconds())
            self.store_seed_meta(
                status="preserved",
                record_count=_record_count(previous),
                error_summary=f"{self.component} returned empty payload",
                preserve_last_success=True,
                metadata={"result": "preserved-empty", "component": self.component, **stats},
                payload_status=preserved.get("status") or "preserved",
            )
            return {"status": "preserved", "recordCount": _record_count(previous), "error": "empty payload"}

        stats = _payload_timestamp_stats(payload, stale_after_seconds=self.stale_after_seconds())
        payload_status = str(payload.get("status") or "").strip().lower()
        status = "ok" if record_count > 0 else "empty"
        if payload_status in {"degraded", "stale", "empty", "error"}:
            status = payload_status
        data_age = stats.get("dataAgeSeconds")
        if record_count > 0 and isinstance(data_age, int) and data_age > self.stale_after_seconds():
            status = "stale"
        stored_payload = {**payload, "status": status, "cacheMode": "seeded"}
        self.store_payload(stored_payload)
        telegram_sent = publish_cached_panel_snapshot(str(self.spec["panel_id"]), stored_payload)
        self.store_seed_meta(
            status=status,
            record_count=record_count,
            error_summary=None if record_count else f"{self.component} payload contained no items",
            metadata={"result": "stored", "component": self.component, "limit": self.limit, "telegramSent": telegram_sent, **stats},
            source_states=payload.get("sourceStates") if isinstance(payload.get("sourceStates"), dict) else {"database": status},
            payload_status=status,
        )
        return {"status": status, "recordCount": record_count, "component": self.component, "telegramSent": telegram_sent}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed signal panel snapshots into Redis and SQLite")
    parser.add_argument("--component", choices=sorted(COMPONENTS.keys()), required=True)
    parser.add_argument("--watch", action="store_true", help="Run continuously instead of once")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("POLYDATA_SIGNAL_WATCH_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)))
    parser.add_argument("--limit", type=int, default=0, help="Override the component default limit")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    db_read_timeout = max(3, int(os.environ.get("POLYDATA_SIGNAL_DB_READ_TIMEOUT_SECONDS", DEFAULT_DB_READ_TIMEOUT_SECONDS)))
    current_read_timeout = int(os.environ.get("POLYMARKET_MYSQL_READ_TIMEOUT", "60") or "60")
    if current_read_timeout > db_read_timeout:
        os.environ["POLYMARKET_MYSQL_READ_TIMEOUT"] = str(db_read_timeout)
    settings = load_api_settings()
    spec = COMPONENTS[args.component]
    limit = args.limit or int(os.environ.get(str(spec["limit_env"]), spec["default_limit"]))
    watcher = SignalsWatcher(
        redis_url=settings.redis_url,
        redis_prefix=settings.redis_prefix,
        snapshot_sqlite_path=settings.snapshot_sqlite_path,
        component=args.component,
        limit=limit,
        interval_seconds=args.interval,
    )
    watcher.redis_client.ping()
    print(f"[signals] component={args.component} redis_key={watcher.redis_key()} sqlite={settings.snapshot_sqlite_path}", file=sys.stderr)
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
            watcher.store_seed_meta(
                status="error",
                record_count=0,
                error_summary=str(exc),
                preserve_last_success=True,
                metadata={"result": "exception", "component": args.component},
            )
            print(f"[signals] ERROR watch loop failed: {exc}", file=sys.stderr)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
