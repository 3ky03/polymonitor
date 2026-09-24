#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seed CPI release calendar snapshots into Redis and SQLite."""

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
from api.services import cpi_release_calendar_service, polymarket_macro_map_service
from runtime.seed_meta import SeedMetaStore, build_seed_meta_payload
from runtime.snapshot_store import SnapshotStore


DEFAULT_INTERVAL_SECONDS = 3600
SEED_META_NAMESPACE = "seed-meta:macro"
SEED_META_CACHE_KEY = "cpi-release-calendar"
SEED_META_SERVICE_NAME = "polydata-cpi-release-calendar-seed.service"


class _LoggerAdapter:
    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[cpi-calendar] ERROR {message % args if args else message}", file=sys.stderr)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[cpi-calendar] WARN {message % args if args else message}", file=sys.stderr)

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[cpi-calendar] INFO {message % args if args else message}", file=sys.stderr)


class _AppAdapter:
    logger = _LoggerAdapter()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{str(prefix or '')}{namespace}:{cache_key}"


class CpiReleaseCalendarWatcher:
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
            raise RuntimeError("POLYDATA_REDIS_URL is required for CPI release calendar watcher")
        self.settings = settings
        self.interval_seconds = max(300, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
        self.redis_prefix = str(redis_prefix or "")
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.snapshot_store = SnapshotStore(snapshot_sqlite_path)
        self.seed_meta_store = SeedMetaStore(redis_client=self.redis_client, redis_prefix=self.redis_prefix, snapshot_store=self.snapshot_store)
        self.requests = requests.Session()
        self.requests.trust_env = False
        self.requests.headers.update({"User-Agent": "polydata-cpi-calendar-watcher/1.0"})

    def namespace(self) -> str:
        return cpi_release_calendar_service.CPI_CALENDAR_SNAPSHOT_NAMESPACE

    def cache_key(self) -> str:
        return cpi_release_calendar_service.CPI_CALENDAR_CACHE_KEY

    def redis_key(self) -> str:
        return _redis_key(self.redis_prefix, self.namespace(), self.cache_key())

    def ttl_seconds(self) -> int:
        return max(300, int(getattr(self.settings, "cpi_calendar_ttl_seconds", 3600) or 3600))

    def _http_text_get(self, url: str, *, timeout: int = 12, headers: Dict[str, str] | None = None) -> str:
        response = self.requests.get(url, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.text

    def service_context(self) -> Dict[str, Any]:
        return {
            "SETTINGS": self.settings,
            "app": _AppAdapter(),
            "BeautifulSoup": BeautifulSoup,
            "http_text_get": self._http_text_get,
            "SNAPSHOT_STORE": self.snapshot_store,
            "get_cached_json": self._get_cached_json,
            "set_cached_json": self._set_cached_json,
            "get_polymarket_macro_map_snapshot": lambda limit=20: polymarket_macro_map_service.get_polymarket_macro_map_snapshot(
                self.service_context(),
                limit=limit,
            ),
            "utc_now_iso": utc_now_iso,
        }

    def _get_cached_json(self, namespace: str, cache_key: str) -> Dict[str, Any] | None:
        try:
            raw = self.redis_client.get(_redis_key(self.redis_prefix, namespace, cache_key))
            if raw:
                payload = json.loads(str(raw))
                return payload if isinstance(payload, dict) else None
        except Exception:
            return None
        return None

    def _set_cached_json(self, namespace: str, cache_key: str, payload: Dict[str, Any], ttl: int) -> None:
        self.redis_client.set(_redis_key(self.redis_prefix, namespace, cache_key), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl)

    def load_previous_payload(self) -> Dict[str, Any]:
        cached = self._get_cached_json(self.namespace(), self.cache_key())
        if isinstance(cached, dict):
            return cached
        stale = self.snapshot_store.get_stale(self.namespace(), self.cache_key())
        return stale if isinstance(stale, dict) else {}

    def store_payload(self, payload: Dict[str, Any]) -> None:
        ttl_seconds = self.ttl_seconds()
        self.snapshot_store.set(self.namespace(), self.cache_key(), payload, ttl_seconds)
        self.redis_client.set(self.redis_key(), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl_seconds)

    def store_seed_meta(
        self,
        *,
        status: str,
        record_count: int,
        source_states: Dict[str, Any] | None = None,
        error_summary: str | None = None,
        cache_mode: str | None = None,
        payload_status: str | None = None,
        preserve_last_success: bool = False,
    ) -> Dict[str, Any]:
        previous = self.seed_meta_store.load(SEED_META_NAMESPACE, SEED_META_CACHE_KEY) or {}
        attempted_at = utc_now_iso()
        last_success_at = previous.get("lastSuccessAt")
        if not preserve_last_success and status in {"ok", "degraded", "preserved"}:
            last_success_at = attempted_at
        payload = build_seed_meta_payload(
            panel_id=SEED_META_CACHE_KEY,
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
            cache_mode=cache_mode,
            payload_status=payload_status,
            metadata={"result": status},
        )
        return self.seed_meta_store.store(SEED_META_NAMESPACE, SEED_META_CACHE_KEY, payload)

    def run_once(self) -> Dict[str, Any]:
        previous = self.load_previous_payload()
        try:
            payload = cpi_release_calendar_service.build_cpi_release_calendar_payload(self.service_context())
        except Exception as exc:
            if previous:
                self.store_payload(previous)
                self.store_seed_meta(
                    status="preserved",
                    record_count=len(previous.get("items") or []),
                    source_states={"calendarSources": "error"},
                    error_summary=str(exc),
                    cache_mode=previous.get("cacheMode"),
                    payload_status=previous.get("status"),
                    preserve_last_success=True,
                )
                return {"status": "preserved", "payload": previous, "error": str(exc)}
            self.store_seed_meta(status="error", record_count=0, source_states={"calendarSources": "error"}, error_summary=str(exc), preserve_last_success=True)
            return {"status": "error", "error": str(exc)}

        record_count = len(payload.get("items") or [])
        if previous and record_count <= 0:
            self.store_payload(previous)
            self.store_seed_meta(
                status="preserved",
                record_count=len(previous.get("items") or []),
                source_states=payload.get("sources"),
                error_summary="Preserved previous snapshot because new CPI calendar payload was empty",
                cache_mode=previous.get("cacheMode"),
                payload_status=previous.get("status"),
                preserve_last_success=True,
            )
            return {"status": "preserved", "payload": previous}

        payload = {**payload, "cacheMode": "seeded"}
        self.store_payload(payload)
        status = "ok" if record_count > 0 and payload.get("status") == "ok" else str(payload.get("status") or "degraded")
        self.store_seed_meta(
            status=status,
            record_count=record_count,
            source_states=payload.get("sources"),
            error_summary=None if record_count else "CPI calendar payload contained no items",
            cache_mode=payload.get("cacheMode"),
            payload_status=payload.get("status"),
        )
        return {"status": "stored", "payload": payload}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed CPI release calendar snapshots into Redis and SQLite")
    parser.add_argument("--watch", action="store_true", help="Run continuously instead of once")
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("POLYDATA_CPI_CALENDAR_WATCH_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)),
        help="Seconds between refresh runs in watch mode",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    settings = load_api_settings()
    watcher = CpiReleaseCalendarWatcher(
        redis_url=settings.redis_url,
        redis_prefix=settings.redis_prefix,
        snapshot_sqlite_path=settings.snapshot_sqlite_path,
        settings=settings,
        interval_seconds=args.interval,
    )
    watcher.redis_client.ping()
    print(f"[cpi-calendar] redis_key={watcher.redis_key()} sqlite={settings.snapshot_sqlite_path}", file=sys.stderr)
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
            watcher.store_seed_meta(
                status="error",
                record_count=0,
                source_states={"calendarSources": "error"},
                error_summary=str(exc),
                preserve_last_success=True,
            )
            print(f"[cpi-calendar] ERROR watch loop failed: {exc}", file=sys.stderr)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
