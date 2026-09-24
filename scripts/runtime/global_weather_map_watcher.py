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
from api.services import global_weather_map_service
from db.db import DEFAULT_DB_PATH, get_connection
from runtime.seed_meta import SeedMetaStore, build_seed_meta_payload
from runtime.snapshot_store import SnapshotStore
from runtime.telegram_panel_publish import publish_cached_panel_snapshot

DEFAULT_INTERVAL_SECONDS = 180
SEED_META_NAMESPACE = "seed-meta:weather"
SEED_META_CACHE_KEY = "global-weather-map"
SEED_META_SERVICE_NAME = "polydata-global-weather-map-seed.service"


class _Logger:
    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[global-weather-map] ERROR {message % args if args else message}", file=sys.stderr)


class _App:
    logger = _Logger()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{prefix or ''}{namespace}:{cache_key}"


def _payload_has_weather_values(payload: Dict[str, Any]) -> bool:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    try:
        if int(summary.get("mappedCount") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        if any(item.get(key) is not None for key in (
            "currentTemp",
            "metarTemp",
            "todayHigh",
            "forecastHigh",
            "currentWindSpeed",
            "currentWindGust",
            "currentPrecipitation",
            "todayWindSpeed",
            "todayWindGust",
            "todayPrecipitationSum",
            "todayPrecipitationProbability",
            "forecastWindSpeedMax",
            "forecastWindGustMax",
            "forecastPrecipitationSum",
            "forecastPrecipitationProbabilityMax",
        )):
            return True
        if item.get("hourly") or item.get("daily"):
            return True
    return False


def _should_preserve_previous(previous: Dict[str, Any], payload: Dict[str, Any]) -> bool:
    if not previous or not _payload_has_weather_values(previous):
        return False
    sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    if sources.get("openMeteo") == "error" and not _payload_has_weather_values(payload):
        return True
    if str(payload.get("status") or "").lower() == "warming" and not _payload_has_weather_values(payload):
        return True
    return False


class GlobalWeatherMapWatcher:
    def __init__(self, *, redis_url: str, redis_prefix: str, snapshot_sqlite_path: str, settings: Any, interval_seconds: int) -> None:
        if not redis_url:
            raise RuntimeError("POLYDATA_REDIS_URL is required for global weather map watcher")
        self.settings = settings
        self.redis_prefix = redis_prefix or ""
        self.interval_seconds = max(60, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.snapshot_store = SnapshotStore(snapshot_sqlite_path)
        self.seed_meta_store = SeedMetaStore(redis_client=self.redis_client, redis_prefix=self.redis_prefix, snapshot_store=self.snapshot_store)
        self.requests = requests.Session()
        self.requests.trust_env = False

    def namespace(self) -> str:
        return global_weather_map_service.GLOBAL_WEATHER_MAP_SNAPSHOT_NAMESPACE

    def cache_key(self) -> str:
        return global_weather_map_service.GLOBAL_WEATHER_MAP_CACHE_KEY

    def redis_key(self) -> str:
        return _redis_key(self.redis_prefix, self.namespace(), self.cache_key())

    def ttl_seconds(self) -> int:
        return max(60, int(getattr(self.settings, "global_weather_map_ttl_seconds", 300) or 300))

    def _http_json_get(self, url: str, *, params: Dict[str, Any] | None = None, timeout: int = 12, headers: Dict[str, str] | None = None) -> Any:
        response = self.requests.get(url, params=params, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.json()

    def context(self) -> Dict[str, Any]:
        return {
            "SETTINGS": self.settings,
            "app": _App(),
            "http_json_get": self._http_json_get,
            "get_clob_session": lambda: self.requests,
            "SNAPSHOT_STORE": self.snapshot_store,
            "get_cached_json": self._get_cached_json,
            "set_cached_json": self._set_cached_json,
            "utc_now_iso": utc_now_iso,
            "get_connection": get_connection,
            "DB_PATH": DEFAULT_DB_PATH,
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
        ttl = self.ttl_seconds()
        self.snapshot_store.set(self.namespace(), self.cache_key(), payload, ttl)
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
            payload = global_weather_map_service.build_global_weather_map_payload(self.context())
        except Exception as exc:
            if previous:
                self.store_payload(previous)
                self.store_meta(status="preserved", record_count=len(previous.get("items") or []), source_states={"weather": "error"}, error_summary=str(exc), preserve=True)
                return {"status": "preserved", "payload": previous, "error": str(exc)}
            self.store_meta(status="error", record_count=0, source_states={"weather": "error"}, error_summary=str(exc), preserve=True)
            return {"status": "error", "error": str(exc)}
        if previous and not payload.get("items"):
            self.store_payload(previous)
            self.store_meta(status="preserved", record_count=len(previous.get("items") or []), source_states=payload.get("sources"), error_summary="Preserved previous snapshot because new weather map payload was empty", preserve=True)
            return {"status": "preserved", "payload": previous}
        if _should_preserve_previous(previous, payload):
            self.store_payload(previous)
            self.store_meta(status="preserved", record_count=len(previous.get("items") or []), source_states=payload.get("sources"), error_summary="Preserved previous snapshot because new weather map payload lost live weather values", preserve=True)
            return {"status": "preserved", "payload": previous}
        payload = global_weather_map_service.merge_weather_series_from_previous(payload, previous)
        payload = {**payload, "cacheMode": "seeded"}
        self.store_payload(payload)
        telegram_sent = publish_cached_panel_snapshot(SEED_META_CACHE_KEY, payload)
        status = "ok" if payload.get("status") == "ok" else str(payload.get("status") or "degraded")
        self.store_meta(status=status, record_count=len(payload.get("items") or []), source_states=payload.get("sources"), cache_mode="seeded", payload_status=payload.get("status"))
        return {"status": "stored", "payload": payload, "telegramSent": telegram_sent}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("POLYDATA_GLOBAL_WEATHER_MAP_WATCH_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)))
    args = parser.parse_args()
    settings = load_api_settings()
    watcher = GlobalWeatherMapWatcher(redis_url=settings.redis_url, redis_prefix=settings.redis_prefix, snapshot_sqlite_path=settings.snapshot_sqlite_path, settings=settings, interval_seconds=args.interval)
    watcher.redis_client.ping()
    print(f"[global-weather-map] redis_key={watcher.redis_key()} sqlite={settings.snapshot_sqlite_path}", file=sys.stderr)
    if not args.watch:
        print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        return 0
    while True:
        try:
            print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            watcher.store_meta(status="error", record_count=0, source_states={"weather": "error"}, error_summary=str(exc), preserve=True)
            print(f"[global-weather-map] ERROR {exc}", file=sys.stderr)
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
