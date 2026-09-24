#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone watcher for geo / sanctions shock panel snapshots.

The watcher fetches external sources on a timer, computes the render-ready panel
payload, and stores it in Redis plus the local SQLite snapshot store. The API
path then reads the seeded snapshot instead of fetching upstreams inline.
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
from api.services import geo_sanctions_shock_service
from runtime.seed_meta import SeedMetaStore, build_seed_meta_payload
from runtime.snapshot_store import SnapshotStore


DEFAULT_INTERVAL_SECONDS = 300
SEED_META_NAMESPACE = "seed-meta:world"
SEED_META_CACHE_KEY = "geo-sanctions-shock"
SEED_META_SERVICE_NAME = "polydata-geo-sanctions-shock.service"


class _LoggerAdapter:
    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[geo-shock] INFO {message % args if args else message}", file=sys.stderr)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[geo-shock] WARN {message % args if args else message}", file=sys.stderr)

    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[geo-shock] ERROR {message % args if args else message}", file=sys.stderr)


class _AppAdapter:
    logger = _LoggerAdapter()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{str(prefix or '')}{namespace}:{cache_key}"


def _cache_ttl_seconds(settings: Any) -> int:
    return max(300, int(settings.geo_shock_ttl_seconds or 900))


class GeoSanctionsShockWatcher:
    def __init__(
        self,
        *,
        redis_url: str,
        redis_prefix: str,
        snapshot_sqlite_path: str,
        settings: Any,
    ) -> None:
        if redis is None:
            raise RuntimeError("redis package is required. Install scripts/requirements.txt")
        if requests is None:
            raise RuntimeError("requests package is required. Install scripts/requirements.txt")
        if not str(redis_url or "").strip():
            raise RuntimeError("POLYDATA_REDIS_URL is required for geo shock watcher")
        self.settings = settings
        self.redis_prefix = str(redis_prefix or "")
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.snapshot_store = SnapshotStore(snapshot_sqlite_path)
        self.seed_meta_store = SeedMetaStore(redis_client=self.redis_client, redis_prefix=self.redis_prefix, snapshot_store=self.snapshot_store)
        self.requests = requests.Session()
        self.requests.trust_env = False
        self.requests.headers.update({"User-Agent": "polyData-geo-shock-watcher/1.0"})
        self._acled_auth_state: Dict[str, Any] | None = None

    def cache_key(self) -> str:
        return geo_sanctions_shock_service.GEO_SHOCK_CACHE_KEY

    def namespace(self) -> str:
        return geo_sanctions_shock_service.GEO_SHOCK_SNAPSHOT_NAMESPACE

    def redis_key(self) -> str:
        return _redis_key(self.redis_prefix, self.namespace(), self.cache_key())

    def acled_auth_namespace(self) -> str:
        return geo_sanctions_shock_service.ACLED_AUTH_NAMESPACE

    def acled_auth_cache_key(self) -> str:
        return geo_sanctions_shock_service.ACLED_AUTH_CACHE_KEY

    def acled_auth_redis_key(self) -> str:
        return _redis_key(self.redis_prefix, self.acled_auth_namespace(), self.acled_auth_cache_key())

    def seed_meta_namespace(self) -> str:
        return SEED_META_NAMESPACE

    def seed_meta_cache_key(self) -> str:
        return SEED_META_CACHE_KEY

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
        success_statuses = {"ok", "degraded", "preserved"}
        last_success_at = previous.get("lastSuccessAt")
        if not preserve_last_success and str(status or "").strip().lower() in success_statuses:
            last_success_at = attempted_at
        payload = build_seed_meta_payload(
            panel_id="geo-sanctions-shock",
            namespace=self.seed_meta_namespace(),
            cache_key=self.seed_meta_cache_key(),
            service_name=SEED_META_SERVICE_NAME,
            expected_interval_seconds=DEFAULT_INTERVAL_SECONDS,
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

    def _http_json_get(self, url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 15, headers: Optional[Dict[str, str]] = None) -> Any:
        response = self.requests.get(url, params=params, timeout=timeout, headers=headers)
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    def load_acled_auth_state(self) -> Optional[Dict[str, Any]]:
        if isinstance(self._acled_auth_state, dict):
            return dict(self._acled_auth_state)
        try:
            raw = self.redis_client.get(self.acled_auth_redis_key())
            if raw:
                payload = json.loads(raw)
                normalized = geo_sanctions_shock_service._normalize_acled_auth_state(payload)
                if normalized is not None:
                    self._acled_auth_state = normalized
                    self.snapshot_store.set(
                        self.acled_auth_namespace(),
                        self.acled_auth_cache_key(),
                        normalized,
                        geo_sanctions_shock_service.ACLED_AUTH_TTL_SECONDS,
                    )
                    return dict(normalized)
        except Exception:
            _AppAdapter.logger.exception("geo shock watcher acled auth redis read failed")

        stale = self.snapshot_store.get_stale(self.acled_auth_namespace(), self.acled_auth_cache_key())
        normalized = geo_sanctions_shock_service._normalize_acled_auth_state(stale)
        if normalized is not None:
            self._acled_auth_state = normalized
            return dict(normalized)
        return None

    def store_acled_auth_state(self, payload: Dict[str, Any]) -> None:
        normalized = geo_sanctions_shock_service._normalize_acled_auth_state(payload)
        if normalized is None:
            return
        self._acled_auth_state = normalized
        ttl_seconds = geo_sanctions_shock_service.ACLED_AUTH_TTL_SECONDS
        self.snapshot_store.set(self.acled_auth_namespace(), self.acled_auth_cache_key(), normalized, ttl_seconds)
        self.redis_client.set(self.acled_auth_redis_key(), json.dumps(normalized, ensure_ascii=True, default=str), ex=ttl_seconds)

    def load_previous_payload(self) -> Dict[str, Any]:
        try:
            raw = self.redis_client.get(self.redis_key())
            if raw:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    return payload
        except Exception:
            _AppAdapter.logger.exception("geo shock watcher redis read failed")
        stale = self.snapshot_store.get_stale(self.namespace(), self.cache_key())
        return stale if isinstance(stale, dict) else {}

    def build_payload(self) -> Dict[str, Any]:
        previous = self.load_previous_payload()
        ctx = {
            "SETTINGS": self.settings,
            "app": _AppAdapter(),
            "requests": self.requests,
            "http_json_get": self._http_json_get,
            "get_acled_auth_state": self.load_acled_auth_state,
            "store_acled_auth_state": self.store_acled_auth_state,
            "utc_now_iso": utc_now_iso,
        }
        return geo_sanctions_shock_service.build_geo_sanctions_shock_seed_payload(ctx, previous=previous)

    def store_payload(self, payload: Dict[str, Any]) -> None:
        ttl_seconds = _cache_ttl_seconds(self.settings)
        self.snapshot_store.set(self.namespace(), self.cache_key(), payload, ttl_seconds)
        self.redis_client.set(self.redis_key(), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl_seconds)

    def run_once(self) -> Dict[str, Any]:
        previous = self.load_previous_payload()
        payload = self.build_payload()
        if (
            previous
            and not geo_sanctions_shock_service.payload_has_material_signal(payload)
            and not geo_sanctions_shock_service.payload_has_source_success(payload)
        ):
            _AppAdapter.logger.warning("geo shock watcher preserved previous snapshot because new payload had no material signal")
            self.store_payload(previous)
            self.store_seed_meta(
                status="preserved",
                record_count=len(previous.get("items") or []),
                source_states=payload.get("sources"),
                error_summary="Preserved previous snapshot because new payload had no material signal",
                cache_mode=previous.get("cacheMode"),
                payload_status=previous.get("status"),
                metadata={"result": "preserved"},
                preserve_last_success=True,
            )
            return {"status": "preserved", "payload": previous}
        self.store_payload(payload)
        self.store_seed_meta(
            status=payload.get("status") or "unknown",
            record_count=len(payload.get("items") or []),
            source_states=payload.get("sources"),
            error_summary=None,
            cache_mode=payload.get("cacheMode"),
            payload_status=payload.get("status"),
            metadata={"result": "stored"},
        )
        return {"status": "stored", "payload": payload}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed geo / sanctions shock panel snapshots into Redis and SQLite")
    parser.add_argument("--watch", action="store_true", help="Run continuously instead of once")
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("POLYDATA_GEO_SHOCK_WATCH_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)),
        help="Seconds between refresh runs in watch mode",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    settings = load_api_settings()
    watcher = GeoSanctionsShockWatcher(
        redis_url=settings.redis_url,
        redis_prefix=settings.redis_prefix,
        snapshot_sqlite_path=settings.snapshot_sqlite_path,
        settings=settings,
    )
    watcher.redis_client.ping()
    print(
        f"[geo-shock] redis_key={watcher.redis_key()} sqlite={settings.snapshot_sqlite_path}",
        file=sys.stderr,
    )
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
            watcher.store_seed_meta(
                status="error",
                record_count=0,
                error_summary=str(exc),
                preserve_last_success=True,
                metadata={"result": "exception"},
            )
            print(f"[geo-shock] ERROR watch loop failed: {exc}", file=sys.stderr)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
