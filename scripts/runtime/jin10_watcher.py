#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone watcher for Jin10 flash panel snapshots.

The watcher fetches Jin10 on a timer, writes the render-ready payload to Redis
and SQLite, and lets API requests read the seeded snapshot instead of calling
the upstream service inline.
"""

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
from api.services import jin10_runtime_service
from runtime.seed_meta import SeedMetaStore, build_seed_meta_payload
from runtime.snapshot_store import SnapshotStore


DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_LIMIT = 24
SEED_META_NAMESPACE = "seed-meta:macro"
SEED_META_CACHE_KEY = "jin10-flash"
SEED_META_SERVICE_NAME = "polydata-jin10-seed.service"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{str(prefix or '')}{namespace}:{cache_key}"


def _items(payload: Dict[str, Any]) -> list[Any]:
    items = payload.get("items")
    return items if isinstance(items, list) else []


class Jin10Watcher:
    def __init__(
        self,
        *,
        redis_url: str,
        redis_prefix: str,
        snapshot_sqlite_path: str,
        settings: Any,
        limit: int = DEFAULT_LIMIT,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        if redis is None:
            raise RuntimeError("redis package is required. Install scripts/requirements.txt")
        if requests is None:
            raise RuntimeError("requests package is required. Install scripts/requirements.txt")
        if not str(redis_url or "").strip():
            raise RuntimeError("POLYDATA_REDIS_URL is required for Jin10 watcher")
        self.settings = settings
        self.limit = max(1, int(limit or DEFAULT_LIMIT))
        self.interval_seconds = max(30, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
        self.redis_prefix = str(redis_prefix or "")
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.snapshot_store = SnapshotStore(snapshot_sqlite_path)
        self.seed_meta_store = SeedMetaStore(
            redis_client=self.redis_client,
            redis_prefix=self.redis_prefix,
            snapshot_store=self.snapshot_store,
        )
        self.requests = requests.Session()
        self.requests.trust_env = False

    def namespace(self) -> str:
        return jin10_runtime_service.JIN10_SNAPSHOT_NAMESPACE

    def cache_key(self) -> str:
        return jin10_runtime_service.build_jin10_cache_key(self.settings, limit=self.limit)

    def redis_key(self) -> str:
        return _redis_key(self.redis_prefix, self.namespace(), self.cache_key())

    def seed_meta_namespace(self) -> str:
        return SEED_META_NAMESPACE

    def seed_meta_cache_key(self) -> str:
        return SEED_META_CACHE_KEY

    def ttl_seconds(self) -> int:
        configured = int(os.environ.get("POLYDATA_JIN10_SEED_TTL_SECONDS", "0") or 0)
        if configured > 0:
            return configured
        return max(120, self.interval_seconds * 3, int(self.settings.signal_runtime_ttl_seconds or 45) * 4)

    def load_seed_meta(self) -> Dict[str, Any]:
        payload = self.seed_meta_store.load(self.seed_meta_namespace(), self.seed_meta_cache_key())
        return payload if isinstance(payload, dict) else {}

    def store_seed_meta(
        self,
        *,
        status: str,
        record_count: int,
        source_states: Optional[Dict[str, Any]] = None,
        error_summary: Optional[str] = None,
        cache_mode: Optional[str] = None,
        payload_status: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        preserve_last_success: bool = False,
    ) -> Dict[str, Any]:
        previous = self.load_seed_meta()
        attempted_at = utc_now_iso()
        last_success_at = previous.get("lastSuccessAt")
        if not preserve_last_success and str(status or "").strip().lower() in {"ok", "degraded", "preserved"}:
            last_success_at = attempted_at
        payload = build_seed_meta_payload(
            panel_id="jin10-flash",
            namespace=self.seed_meta_namespace(),
            cache_key=self.seed_meta_cache_key(),
            service_name=SEED_META_SERVICE_NAME,
            expected_interval_seconds=self.interval_seconds,
            status=status,
            last_attempt_at=attempted_at,
            last_success_at=last_success_at or attempted_at,
            record_count=record_count,
            source_states=source_states,
            error_summary=error_summary,
            cache_mode=cache_mode,
            payload_status=payload_status,
            metadata=metadata,
        )
        return self.seed_meta_store.store(self.seed_meta_namespace(), self.seed_meta_cache_key(), payload)

    def load_previous_payload(self) -> Dict[str, Any]:
        try:
            raw = self.redis_client.get(self.redis_key())
            if raw:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    return payload
        except Exception:
            print("[jin10] WARN redis read failed", file=sys.stderr)
        stale = self.snapshot_store.get_stale(self.namespace(), self.cache_key())
        return stale if isinstance(stale, dict) else {}

    def build_payload(self) -> Dict[str, Any]:
        ctx = {
            "SETTINGS": self.settings,
            "requests": self.requests,
            "utc_now_iso": utc_now_iso,
        }
        return jin10_runtime_service.fetch_live_jin10_panel_payload(ctx, limit=self.limit)

    def store_payload(self, payload: Dict[str, Any]) -> None:
        ttl_seconds = self.ttl_seconds()
        self.snapshot_store.set(self.namespace(), self.cache_key(), payload, ttl_seconds)
        self.redis_client.set(self.redis_key(), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl_seconds)

    def run_once(self) -> Dict[str, Any]:
        previous = self.load_previous_payload()
        try:
            payload = self.build_payload()
        except Exception as exc:
            self.store_seed_meta(
                status="error",
                record_count=len(_items(previous)),
                source_states={"jin10Flash": "error"},
                error_summary=str(exc),
                cache_mode=previous.get("cacheMode"),
                payload_status=previous.get("status"),
                metadata={"result": "exception"},
                preserve_last_success=True,
            )
            raise

        if previous and not _items(payload):
            self.store_payload(previous)
            self.store_seed_meta(
                status="preserved",
                record_count=len(_items(previous)),
                source_states={"jin10Flash": "empty"},
                error_summary="Preserved previous snapshot because new Jin10 payload was empty",
                cache_mode=previous.get("cacheMode"),
                payload_status=previous.get("status"),
                metadata={"result": "preserved"},
                preserve_last_success=True,
            )
            return {"status": "preserved", "payload": previous}

        payload_status = str(payload.get("status") or "unknown").strip().lower()
        item_count = len(_items(payload))
        status = "ok" if payload_status == "ok" and item_count > 0 else "degraded"
        error_summary = None if status == "ok" else "Jin10 payload contained no items"
        payload = {**payload, "cacheMode": "seeded"}
        self.store_payload(payload)
        self.store_seed_meta(
            status=status,
            record_count=item_count,
            source_states={"jin10Flash": "ok" if item_count > 0 else "empty"},
            error_summary=error_summary,
            cache_mode=payload.get("cacheMode"),
            payload_status=payload.get("status"),
            metadata={
                "result": "stored",
                "limit": self.limit,
                "channel": self.settings.jin10_flash_channel,
            },
        )
        return {"status": "stored", "payload": payload}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed Jin10 flash panel snapshots into Redis and SQLite")
    parser.add_argument("--watch", action="store_true", help="Run continuously instead of once")
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("POLYDATA_JIN10_WATCH_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)),
        help="Seconds between refresh runs in watch mode",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.environ.get("POLYDATA_JIN10_LIMIT", DEFAULT_LIMIT)),
        help="Number of Jin10 items to seed",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    settings = load_api_settings()
    watcher = Jin10Watcher(
        redis_url=settings.redis_url,
        redis_prefix=settings.redis_prefix,
        snapshot_sqlite_path=settings.snapshot_sqlite_path,
        settings=settings,
        limit=args.limit,
        interval_seconds=args.interval,
    )
    watcher.redis_client.ping()
    print(f"[jin10] redis_key={watcher.redis_key()} sqlite={settings.snapshot_sqlite_path}", file=sys.stderr)
    if not args.watch:
        result = watcher.run_once()
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return 0

    interval_seconds = max(30, int(args.interval or DEFAULT_INTERVAL_SECONDS))
    while True:
        try:
            result = watcher.run_once()
            print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"[jin10] ERROR watch loop failed: {exc}", file=sys.stderr)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
