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
import requests

from api.config import load_api_settings
from api.services import food_retail_basket_service
from runtime.seed_meta import SeedMetaStore, build_seed_meta_payload
from runtime.snapshot_store import SnapshotStore

DEFAULT_INTERVAL_SECONDS = 21600
SEED_META_NAMESPACE = "seed-meta:macro"
SEED_META_CACHE_KEY = "food-retail-basket-pressure"
SEED_META_SERVICE_NAME = "polydata-food-retail-basket-seed.service"


class _Logger:
    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[food-basket] ERROR {message % args if args else message}", file=sys.stderr)


class _App:
    logger = _Logger()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{prefix or ''}{namespace}:{cache_key}"


class FoodRetailBasketWatcher:
    def __init__(self, *, redis_url: str, redis_prefix: str, snapshot_sqlite_path: str, settings: Any, interval_seconds: int) -> None:
        if not redis_url:
            raise RuntimeError("POLYDATA_REDIS_URL is required for food retail basket watcher")
        self.settings = settings
        self.redis_prefix = redis_prefix or ""
        self.interval_seconds = max(1800, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.snapshot_store = SnapshotStore(snapshot_sqlite_path)
        self.seed_meta_store = SeedMetaStore(redis_client=self.redis_client, redis_prefix=self.redis_prefix, snapshot_store=self.snapshot_store)
        self.requests = requests.Session()
        self.requests.trust_env = False

    def namespace(self) -> str:
        return food_retail_basket_service.FOOD_BASKET_SNAPSHOT_NAMESPACE

    def cache_key(self) -> str:
        return food_retail_basket_service.FOOD_BASKET_CACHE_KEY

    def redis_key(self) -> str:
        return _redis_key(self.redis_prefix, self.namespace(), self.cache_key())

    def _http_text_get(self, url: str, *, timeout: int = 15, headers: Dict[str, str] | None = None) -> str:
        response = self.requests.get(url, timeout=min(12, int(timeout or 12)), headers=headers)
        response.raise_for_status()
        return response.text

    def context(self) -> Dict[str, Any]:
        return {
            "SETTINGS": self.settings,
            "app": _App(),
            "http_text_get": self._http_text_get,
            "SNAPSHOT_STORE": self.snapshot_store,
            "get_cached_json": self._get_cached_json,
            "set_cached_json": self._set_cached_json,
            "utc_now_iso": utc_now_iso,
        }

    def _get_cached_json(self, namespace: str, cache_key: str) -> Dict[str, Any] | None:
        raw = self.redis_client.get(_redis_key(self.redis_prefix, namespace, cache_key))
        if not raw:
            return None
        payload = json.loads(str(raw))
        return payload if isinstance(payload, dict) else None

    def _set_cached_json(self, namespace: str, cache_key: str, payload: Dict[str, Any], ttl: int) -> None:
        self.redis_client.set(_redis_key(self.redis_prefix, namespace, cache_key), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl)

    def previous(self) -> Dict[str, Any]:
        cached = self._get_cached_json(self.namespace(), self.cache_key())
        if cached:
            return cached
        stale = self.snapshot_store.get_stale(self.namespace(), self.cache_key())
        return stale if isinstance(stale, dict) else {}

    def store_payload(self, payload: Dict[str, Any]) -> None:
        ttl = max(1800, int(getattr(self.settings, "food_basket_ttl_seconds", 21600) or 21600))
        self.snapshot_store.set(self.namespace(), self.cache_key(), payload, ttl)
        self.redis_client.set(self.redis_key(), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl)

    def store_meta(self, *, status: str, record_count: int, source_states: Dict[str, Any] | None = None, error_summary: str | None = None, preserve: bool = False, cache_mode: str | None = None, payload_status: str | None = None) -> None:
        prev = self.seed_meta_store.load(SEED_META_NAMESPACE, SEED_META_CACHE_KEY) or {}
        attempt = utc_now_iso()
        success = prev.get("lastSuccessAt") if preserve else attempt
        payload = build_seed_meta_payload(panel_id=SEED_META_CACHE_KEY, namespace=SEED_META_NAMESPACE, cache_key=SEED_META_CACHE_KEY, service_name=SEED_META_SERVICE_NAME, expected_interval_seconds=self.interval_seconds, status=status, last_attempt_at=attempt, last_success_at=success or attempt, record_count=record_count, source_states=source_states, error_summary=error_summary, cache_mode=cache_mode, payload_status=payload_status, metadata={"result": status})
        self.seed_meta_store.store(SEED_META_NAMESPACE, SEED_META_CACHE_KEY, payload)

    def run_once(self) -> Dict[str, Any]:
        previous = self.previous()
        try:
            payload = food_retail_basket_service.build_food_retail_basket_payload(self.context())
        except Exception as exc:
            if previous:
                self.store_payload(previous)
                self.store_meta(status="preserved", record_count=len(previous.get("items") or []), source_states={"fred": "error"}, error_summary=str(exc), preserve=True)
                return {"status": "preserved", "payload": previous, "error": str(exc)}
            self.store_meta(status="error", record_count=0, source_states={"fred": "error"}, error_summary=str(exc), preserve=True)
            return {"status": "error", "error": str(exc)}
        if previous and not payload.get("items"):
            self.store_payload(previous)
            self.store_meta(status="preserved", record_count=len(previous.get("items") or []), source_states=payload.get("sources"), error_summary="Preserved previous snapshot because new food basket payload was empty", preserve=True)
            return {"status": "preserved", "payload": previous}
        payload = {**payload, "cacheMode": "seeded"}
        self.store_payload(payload)
        status = "ok" if payload.get("status") == "ok" else str(payload.get("status") or "degraded")
        self.store_meta(status=status, record_count=len(payload.get("items") or []), source_states=payload.get("sources"), cache_mode="seeded", payload_status=payload.get("status"))
        return {"status": "stored", "payload": payload}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("POLYDATA_FOOD_BASKET_WATCH_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)))
    args = parser.parse_args()
    settings = load_api_settings()
    watcher = FoodRetailBasketWatcher(redis_url=settings.redis_url, redis_prefix=settings.redis_prefix, snapshot_sqlite_path=settings.snapshot_sqlite_path, settings=settings, interval_seconds=args.interval)
    watcher.redis_client.ping()
    print(f"[food-basket] redis_key={watcher.redis_key()} sqlite={settings.snapshot_sqlite_path}", file=sys.stderr)
    if not args.watch:
        print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        return 0
    while True:
        try:
            print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            watcher.store_meta(status="error", record_count=0, source_states={"fred": "error"}, error_summary=str(exc), preserve=True)
            print(f"[food-basket] ERROR {exc}", file=sys.stderr)
        time.sleep(max(1800, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
