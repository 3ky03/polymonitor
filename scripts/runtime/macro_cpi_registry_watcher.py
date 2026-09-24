#!/usr/bin/env python3
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

import redis

from api.config import load_api_settings
from api.services import macro_cpi_registry_service
from runtime.seed_meta import SeedMetaStore, build_seed_meta_payload
from runtime.snapshot_store import SnapshotStore
from runtime.telegram_panel_publish import publish_cached_panel_snapshot


DEFAULT_INTERVAL_SECONDS = 1800
SEED_META_NAMESPACE = "seed-meta:macro"
SEED_META_SERVICE_NAME = "polydata-macro-cpi-registry-seed.service"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{prefix or ''}{namespace}:{cache_key}"


class MacroCpiRegistryWatcher:
    def __init__(self, *, redis_url: str, redis_prefix: str, snapshot_sqlite_path: str, settings: Any, interval_seconds: int) -> None:
        if not redis_url:
            raise RuntimeError("POLYDATA_REDIS_URL is required for macro CPI registry watcher")
        self.settings = settings
        self.redis_prefix = redis_prefix or ""
        self.interval_seconds = max(300, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.snapshot_store = SnapshotStore(snapshot_sqlite_path)
        self.seed_meta_store = SeedMetaStore(redis_client=self.redis_client, redis_prefix=self.redis_prefix, snapshot_store=self.snapshot_store)

    def ttl_seconds(self) -> int:
        return macro_cpi_registry_service.ttl_seconds({"SETTINGS": self.settings})

    def namespace(self, panel_id: str) -> str:
        return macro_cpi_registry_service._snapshot_namespace(panel_id)

    def cache_key(self) -> str:
        return macro_cpi_registry_service.CACHE_KEY

    def redis_key(self, panel_id: str) -> str:
        return _redis_key(self.redis_prefix, self.namespace(panel_id), self.cache_key())

    def _get_cached_json(self, namespace: str, cache_key: str) -> Dict[str, Any] | None:
        raw = self.redis_client.get(_redis_key(self.redis_prefix, namespace, cache_key))
        if not raw:
            return None
        payload = json.loads(str(raw))
        return payload if isinstance(payload, dict) else None

    def _set_cached_json(self, namespace: str, cache_key: str, payload: Dict[str, Any], ttl: int) -> None:
        self.redis_client.set(_redis_key(self.redis_prefix, namespace, cache_key), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl)

    def context(self) -> Dict[str, Any]:
        return {
            "SETTINGS": self.settings,
            "SNAPSHOT_STORE": self.snapshot_store,
            "get_cached_json": self._get_cached_json,
            "set_cached_json": self._set_cached_json,
            "utc_now_iso": utc_now_iso,
        }

    def previous(self, panel_id: str) -> Dict[str, Any]:
        cached = self._get_cached_json(self.namespace(panel_id), self.cache_key())
        if cached:
            return cached
        stale = self.snapshot_store.get_stale(self.namespace(panel_id), self.cache_key())
        return stale if isinstance(stale, dict) else {}

    def store_payload(self, panel_id: str, payload: Dict[str, Any]) -> None:
        ttl = self.ttl_seconds()
        self.snapshot_store.set(self.namespace(panel_id), self.cache_key(), payload, ttl)
        self.redis_client.set(self.redis_key(panel_id), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl)

    def store_meta(self, panel_id: str, *, status: str, record_count: int, source_states: Dict[str, Any] | None = None, error_summary: str | None = None, preserve: bool = False, cache_mode: str | None = None, payload_status: str | None = None) -> None:
        previous = self.seed_meta_store.load(SEED_META_NAMESPACE, panel_id) or {}
        attempted = utc_now_iso()
        last_success = previous.get("lastSuccessAt") if preserve else attempted
        payload = build_seed_meta_payload(
            panel_id=panel_id,
            namespace=SEED_META_NAMESPACE,
            cache_key=panel_id,
            service_name=SEED_META_SERVICE_NAME,
            expected_interval_seconds=self.interval_seconds,
            status=status,
            last_attempt_at=attempted,
            last_success_at=last_success or attempted,
            record_count=record_count,
            source_states=source_states,
            error_summary=error_summary,
            cache_mode=cache_mode,
            payload_status=payload_status,
            metadata={"result": status},
        )
        self.seed_meta_store.store(SEED_META_NAMESPACE, panel_id, payload)

    def run_panel(self, panel_id: str) -> Dict[str, Any]:
        previous = self.previous(panel_id)
        try:
            payload = macro_cpi_registry_service.build_macro_cpi_registry_payload(self.context(), panel_id, limit=macro_cpi_registry_service.MAX_ITEM_LIMIT)
        except Exception as exc:
            if previous:
                self.store_payload(panel_id, previous)
                self.store_meta(panel_id, status="preserved", record_count=len(previous.get("items") or []), source_states={"registry": "error"}, error_summary=str(exc), preserve=True)
                return {"panelId": panel_id, "status": "preserved", "error": str(exc)}
            self.store_meta(panel_id, status="error", record_count=0, source_states={"registry": "error"}, error_summary=str(exc), preserve=True)
            return {"panelId": panel_id, "status": "error", "error": str(exc)}
        if previous and not payload.get("items"):
            self.store_payload(panel_id, previous)
            self.store_meta(panel_id, status="preserved", record_count=len(previous.get("items") or []), source_states=payload.get("sources"), error_summary="Preserved previous registry snapshot because new payload was empty", preserve=True)
            return {"panelId": panel_id, "status": "preserved"}
        payload = {**payload, "cacheMode": "redis-seed"}
        self.store_payload(panel_id, payload)
        telegram_sent = publish_cached_panel_snapshot(panel_id, payload)
        status = "ok" if payload.get("status") == "ok" else str(payload.get("status") or "degraded")
        self.store_meta(panel_id, status=status, record_count=len(payload.get("items") or []), source_states=payload.get("sources"), cache_mode="redis-seed", payload_status=payload.get("status"))
        return {"panelId": panel_id, "status": "stored", "payloadStatus": payload.get("status"), "items": len(payload.get("items") or []), "telegramSent": telegram_sent}

    def run_once(self) -> Dict[str, Any]:
        results = [self.run_panel(panel_id) for panel_id in macro_cpi_registry_service.MACRO_CPI_REGISTRY_PANEL_IDS]
        status = "ok" if all(result.get("status") in {"stored", "preserved"} for result in results) else "degraded"
        return {"status": status, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed composed CPI macro registry panel snapshots into Redis and SQLite")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("POLYDATA_MACRO_CPI_REGISTRY_WATCH_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)))
    args = parser.parse_args()
    settings = load_api_settings()
    watcher = MacroCpiRegistryWatcher(redis_url=settings.redis_url, redis_prefix=settings.redis_prefix, snapshot_sqlite_path=settings.snapshot_sqlite_path, settings=settings, interval_seconds=args.interval)
    watcher.redis_client.ping()
    print(f"[macro-cpi-registry] panels={','.join(macro_cpi_registry_service.MACRO_CPI_REGISTRY_PANEL_IDS)} sqlite={settings.snapshot_sqlite_path}", file=sys.stderr)
    if not args.watch:
        print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        return 0
    while True:
        try:
            print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"[macro-cpi-registry] ERROR {exc}", file=sys.stderr)
        time.sleep(max(300, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
