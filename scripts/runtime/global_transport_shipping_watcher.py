#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
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
from api.services import global_transport_shipping_service
from runtime.seed_meta import SeedMetaStore, build_seed_meta_payload, utc_now_iso
from runtime.snapshot_store import SnapshotStore
from runtime.telegram_panel_publish import publish_cached_panel_snapshot


DEFAULT_INTERVAL_SECONDS = 3600
DEFAULT_LIMIT = 14
SEED_META_NAMESPACE = "seed-meta:transport"
SEED_META_CACHE_KEY = "global-transport-shipping"
SEED_META_SERVICE_NAME = "polydata-global-transport-shipping-seed.service"


class _Logger:
    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[global-transport-shipping] ERROR {message % args if args else message}", file=sys.stderr)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[global-transport-shipping] WARN {message % args if args else message}", file=sys.stderr)


class _App:
    logger = _Logger()


def _redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{str(prefix or '')}{namespace}:{cache_key}"


class GlobalTransportShippingWatcher:
    def __init__(self, *, redis_url: str, redis_prefix: str, snapshot_sqlite_path: str, settings: Any, limit: int, interval_seconds: int) -> None:
        if redis is None:
            raise RuntimeError("redis package is required. Install scripts/requirements.txt")
        if requests is None:
            raise RuntimeError("requests package is required. Install scripts/requirements.txt")
        if not str(redis_url or "").strip():
            raise RuntimeError("POLYDATA_REDIS_URL is required for Global Transport watcher")
        self.settings = settings
        self.limit = max(1, int(limit or DEFAULT_LIMIT))
        self.interval_seconds = max(300, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
        self.redis_prefix = str(redis_prefix or "")
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.snapshot_store = SnapshotStore(snapshot_sqlite_path)
        self.seed_meta_store = SeedMetaStore(redis_client=self.redis_client, redis_prefix=self.redis_prefix, snapshot_store=self.snapshot_store)
        self.requests = requests.Session()
        self.requests.trust_env = str(os.environ.get("POLYDATA_RUNTIME_TRUST_ENV", "0")).strip().lower() in {"1", "true", "yes", "on"}
        self.requests.headers.update({"User-Agent": "polydata-global-transport/1.0"})

    def ttl_seconds(self) -> int:
        configured = int(os.environ.get("POLYDATA_GLOBAL_TRANSPORT_SEED_TTL_SECONDS", "0") or 0)
        if configured > 0:
            return configured
        return max(1800, self.interval_seconds * 3)

    def redis_key(self) -> str:
        return _redis_key(
            self.redis_prefix,
            global_transport_shipping_service.GLOBAL_TRANSPORT_SNAPSHOT_NAMESPACE,
            global_transport_shipping_service.GLOBAL_TRANSPORT_CACHE_KEY,
        )

    def http_text_get(self, url: str, timeout: int = 18, headers: Optional[Dict[str, str]] = None) -> str:
        response = self.requests.get(url, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.text

    def http_json_get(self, url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 18, headers: Optional[Dict[str, str]] = None) -> Any:
        response = self.requests.get(url, params=params, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.json()

    def http_form_post(self, url: str, data: Dict[str, Any], timeout: int = 18, headers: Optional[Dict[str, str]] = None) -> Any:
        response = self.requests.post(url, data=data, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.json()

    def get_cached_json(self, namespace: str, cache_key: str) -> Optional[Dict[str, Any]]:
        raw = self.redis_client.get(_redis_key(self.redis_prefix, namespace, cache_key))
        if not raw:
            return None
        payload = json.loads(str(raw))
        return payload if isinstance(payload, dict) else None

    def set_cached_json(self, namespace: str, cache_key: str, payload: Dict[str, Any], ttl_seconds: int) -> None:
        self.redis_client.set(_redis_key(self.redis_prefix, namespace, cache_key), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl_seconds)

    def context(self) -> Dict[str, Any]:
        return {
            "SETTINGS": self.settings,
            "SNAPSHOT_STORE": self.snapshot_store,
            "app": _App(),
            "http_text_get": self.http_text_get,
            "http_json_get": self.http_json_get,
            "http_form_post": self.http_form_post,
            "get_cached_json": self.get_cached_json,
            "set_cached_json": self.set_cached_json,
            "utc_now_iso": utc_now_iso,
        }

    def previous(self) -> Dict[str, Any]:
        cached = self.get_cached_json(
            global_transport_shipping_service.GLOBAL_TRANSPORT_SNAPSHOT_NAMESPACE,
            global_transport_shipping_service.GLOBAL_TRANSPORT_CACHE_KEY,
        )
        if cached:
            return cached
        stale = self.snapshot_store.get_stale(
            global_transport_shipping_service.GLOBAL_TRANSPORT_SNAPSHOT_NAMESPACE,
            global_transport_shipping_service.GLOBAL_TRANSPORT_CACHE_KEY,
        )
        return stale if isinstance(stale, dict) else {}

    def store_payload(self, payload: Dict[str, Any]) -> None:
        ttl = self.ttl_seconds()
        self.snapshot_store.set(
            global_transport_shipping_service.GLOBAL_TRANSPORT_SNAPSHOT_NAMESPACE,
            global_transport_shipping_service.GLOBAL_TRANSPORT_CACHE_KEY,
            payload,
            ttl,
        )
        self.redis_client.set(self.redis_key(), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl)

    def store_meta(self, *, status: str, record_count: int, source_states: Dict[str, Any] | None = None, error_summary: str | None = None, cache_mode: str | None = None, payload_status: str | None = None, preserve: bool = False) -> None:
        previous = self.seed_meta_store.load(SEED_META_NAMESPACE, SEED_META_CACHE_KEY) or {}
        attempted = utc_now_iso()
        last_success = previous.get("lastSuccessAt") if preserve else attempted
        payload = build_seed_meta_payload(
            panel_id=SEED_META_CACHE_KEY,
            namespace=SEED_META_NAMESPACE,
            cache_key=SEED_META_CACHE_KEY,
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
        )
        self.seed_meta_store.store(SEED_META_NAMESPACE, SEED_META_CACHE_KEY, payload)

    def run_once(self) -> Dict[str, Any]:
        previous = self.previous()
        try:
            payload = global_transport_shipping_service.build_global_transport_shipping_payload(self.context(), limit=self.limit)
        except Exception as exc:
            if previous:
                preserved = {**previous, "cacheMode": "preserved-seed"}
                self.store_payload(preserved)
                self.store_meta(status="preserved", record_count=len(preserved.get("items") or []), source_states={"transport": "error"}, error_summary=str(exc), preserve=True)
                return {"status": "preserved", "payload": preserved, "error": str(exc)}
            self.store_meta(status="error", record_count=0, source_states={"transport": "error"}, error_summary=str(exc), preserve=True)
            return {"status": "error", "error": str(exc)}

        payload = {**payload, "cacheMode": "seeded", "freshness": "seeded"}
        self.store_payload(payload)
        telegram_sent = publish_cached_panel_snapshot(SEED_META_CACHE_KEY, payload)
        sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
        source_states = {key: value.get("status") if isinstance(value, dict) else value for key, value in sources.items()}
        self.store_meta(status=str(payload.get("status") or "ok"), record_count=len(payload.get("items") or []), source_states=source_states, cache_mode="seeded", payload_status=payload.get("status"))
        return {"status": "stored", "payload": payload, "telegramSent": telegram_sent}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("POLYDATA_GLOBAL_TRANSPORT_WATCH_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)))
    parser.add_argument("--limit", type=int, default=int(os.environ.get("POLYDATA_GLOBAL_TRANSPORT_LIMIT", DEFAULT_LIMIT)))
    args = parser.parse_args()
    settings = load_api_settings()
    watcher = GlobalTransportShippingWatcher(
        redis_url=settings.redis_url,
        redis_prefix=settings.redis_prefix,
        snapshot_sqlite_path=settings.snapshot_sqlite_path,
        settings=settings,
        limit=args.limit,
        interval_seconds=args.interval,
    )
    watcher.redis_client.ping()
    print(f"[global-transport-shipping] redis_key={watcher.redis_key()} sqlite={settings.snapshot_sqlite_path}", file=sys.stderr)
    if not args.watch:
        print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        return 0
    while True:
        try:
            print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            watcher.store_meta(status="error", record_count=0, source_states={"transport": "error"}, error_summary=str(exc), preserve=True)
            print(f"[global-transport-shipping] ERROR {exc}", file=sys.stderr)
        time.sleep(max(300, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
