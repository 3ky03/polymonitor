#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

_scripts_root = Path(__file__).resolve().parents[1]
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

import redis
import requests

from api.config import load_api_settings
from api.services import live_video_source_service
from runtime.seed_meta import SeedMetaStore, build_seed_meta_payload, utc_now_iso
from runtime.snapshot_store import SnapshotStore


DEFAULT_INTERVAL_SECONDS = 900
SEED_META_NAMESPACE = "seed-meta:content"
SEED_META_CACHE_KEY = "market-tv-wire"
SEED_META_SERVICE_NAME = "polydata-market-tv-wire-seed.service"
YOUTUBE_RSS_REFRESH_STATE_NAMESPACE = "state:content"
YOUTUBE_RSS_REFRESH_STATE_KEY = "market-tv-wire:youtube-rss-refresh"
DEFAULT_YOUTUBE_RSS_REFRESH_SECONDS = 86400


class _Logger:
    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[market-tv-wire] ERROR {message % args if args else message}", file=sys.stderr)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[market-tv-wire] WARN {message % args if args else message}", file=sys.stderr)


class _App:
    logger = _Logger()


def _redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{prefix or ''}{namespace}:{cache_key}"


class MarketTvWireWatcher:
    def __init__(self, *, redis_url: str, redis_prefix: str, snapshot_sqlite_path: str, settings: Any, interval_seconds: int) -> None:
        if not redis_url:
            raise RuntimeError("POLYDATA_REDIS_URL is required for market TV wire watcher")
        self.settings = settings
        self.redis_prefix = redis_prefix or ""
        self.interval_seconds = max(60, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.snapshot_store = SnapshotStore(snapshot_sqlite_path)
        self.seed_meta_store = SeedMetaStore(redis_client=self.redis_client, redis_prefix=self.redis_prefix, snapshot_store=self.snapshot_store)
        self.requests = requests.Session()
        trust_env = str(os.environ.get("POLYDATA_RUNTIME_TRUST_ENV", "0")).strip().lower() in {"1", "true", "yes", "on"}
        self.requests.trust_env = trust_env

    def namespace(self) -> str:
        return live_video_source_service.MARKET_TV_WIRE_SNAPSHOT_NAMESPACE

    def cache_key(self) -> str:
        return live_video_source_service.MARKET_TV_WIRE_CACHE_KEY

    def redis_key(self) -> str:
        return _redis_key(self.redis_prefix, self.namespace(), self.cache_key())

    def _http_text_get(self, url: str, *, timeout: int = 10, headers: Dict[str, str] | None = None) -> str:
        response = self.requests.get(url, timeout=max(3, min(15, int(timeout or 10))), headers=headers)
        response.raise_for_status()
        return response.text

    def _get_cached_json(self, namespace: str, cache_key: str) -> Dict[str, Any] | None:
        raw = self.redis_client.get(_redis_key(self.redis_prefix, namespace, cache_key))
        if not raw:
            return None
        payload = json.loads(str(raw))
        return payload if isinstance(payload, dict) else None

    def _set_cached_json(self, namespace: str, cache_key: str, payload: Dict[str, Any], ttl: int) -> None:
        self.redis_client.set(_redis_key(self.redis_prefix, namespace, cache_key), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl)

    def _youtube_rss_refresh_seconds(self) -> int:
        try:
            return max(3600, int(os.environ.get("POLYDATA_MARKET_YOUTUBE_RSS_REFRESH_SECONDS", DEFAULT_YOUTUBE_RSS_REFRESH_SECONDS)))
        except Exception:
            return DEFAULT_YOUTUBE_RSS_REFRESH_SECONDS

    def _youtube_rss_refresh_due(self) -> bool:
        return not bool(self.redis_client.get(_redis_key(self.redis_prefix, YOUTUBE_RSS_REFRESH_STATE_NAMESPACE, YOUTUBE_RSS_REFRESH_STATE_KEY)))

    def _mark_youtube_rss_refresh_attempted(self) -> None:
        self.redis_client.set(
            _redis_key(self.redis_prefix, YOUTUBE_RSS_REFRESH_STATE_NAMESPACE, YOUTUBE_RSS_REFRESH_STATE_KEY),
            utc_now_iso(),
            ex=self._youtube_rss_refresh_seconds(),
        )

    def context(self, *, youtube_rss_refresh_due: bool = False) -> Dict[str, Any]:
        return {
            "SETTINGS": self.settings,
            "app": _App(),
            "http_text_get": self._http_text_get,
            "SNAPSHOT_STORE": self.snapshot_store,
            "get_cached_json": self._get_cached_json,
            "set_cached_json": self._set_cached_json,
            "utc_now_iso": utc_now_iso,
            "market_tv_youtube_rss_fallback_enabled": youtube_rss_refresh_due,
            "market_tv_youtube_rss_refresh_existing": youtube_rss_refresh_due,
        }

    def previous(self) -> Dict[str, Any]:
        cached = self._get_cached_json(self.namespace(), self.cache_key())
        if cached:
            return cached
        stale = self.snapshot_store.get_stale(self.namespace(), self.cache_key())
        return stale if isinstance(stale, dict) else {}

    def store_payload(self, payload: Dict[str, Any]) -> None:
        ttl = max(60, int(os.environ.get("POLYDATA_MARKET_TV_WIRE_TTL_SECONDS", live_video_source_service.MARKET_TV_WIRE_TTL_SECONDS)))
        self.snapshot_store.set(self.namespace(), self.cache_key(), payload, ttl)
        self.redis_client.set(self.redis_key(), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl)

    def store_meta(
        self,
        *,
        status: str,
        record_count: int,
        source_states: Dict[str, Any] | None = None,
        error_summary: str | None = None,
        preserve: bool = False,
        cache_mode: str | None = None,
        payload_status: str | None = None,
    ) -> None:
        prev = self.seed_meta_store.load(SEED_META_NAMESPACE, SEED_META_CACHE_KEY) or {}
        attempt = utc_now_iso()
        success = prev.get("lastSuccessAt") if preserve else attempt
        payload = build_seed_meta_payload(
            panel_id=SEED_META_CACHE_KEY,
            namespace=SEED_META_NAMESPACE,
            cache_key=SEED_META_CACHE_KEY,
            service_name=SEED_META_SERVICE_NAME,
            expected_interval_seconds=self.interval_seconds,
            status=status,
            last_attempt_at=attempt,
            last_success_at=success or attempt,
            record_count=record_count,
            source_states=source_states,
            error_summary=error_summary,
            cache_mode=cache_mode,
            payload_status=payload_status,
            metadata={"workerVersion": "v1", "gcpOnly": True},
        )
        self.seed_meta_store.store(SEED_META_NAMESPACE, SEED_META_CACHE_KEY, payload)

    def store_meta_fail_soft(self, **kwargs: Any) -> bool:
        try:
            self.store_meta(**kwargs)
            return True
        except Exception as exc:
            print(
                f"[market-tv-wire] WARN seed meta write failed: {exc.__class__.__name__}",
                file=sys.stderr,
            )
            return False

    def run_once(self) -> Dict[str, Any]:
        previous = self.previous()
        youtube_rss_refresh_due = self._youtube_rss_refresh_due()
        try:
            payload = live_video_source_service.build_market_tv_wire_payload(
                self.context(youtube_rss_refresh_due=youtube_rss_refresh_due),
                include_iptv=True,
            )
        except Exception as exc:
            if previous:
                self.store_payload(previous)
                self.store_meta(
                    status="preserved",
                    record_count=len(previous.get("items") or []),
                    source_states={"marketTvWire": {"status": "error"}},
                    error_summary=str(exc),
                    preserve=True,
                )
                return {"status": "preserved", "payload": previous, "error": str(exc)}
            self.store_meta(status="error", record_count=0, source_states={"marketTvWire": {"status": "error"}}, error_summary=str(exc), preserve=True)
            return {"status": "error", "error": str(exc)}

        if previous and not payload.get("items"):
            self.store_payload(previous)
            self.store_meta(
                status="preserved",
                record_count=len(previous.get("items") or []),
                source_states=payload.get("sources"),
                error_summary="Preserved previous snapshot because new market TV wire payload was empty",
                preserve=True,
            )
            return {"status": "preserved", "payload": previous}

        stored_payload = {**payload, "cacheMode": "seeded"}
        self.store_payload(stored_payload)
        if youtube_rss_refresh_due:
            self._mark_youtube_rss_refresh_attempted()
        payload_status = str(stored_payload.get("status") or "degraded")
        meta_status = "ok" if payload_status == "ok" else payload_status
        self.store_meta(
            status=meta_status,
            record_count=len(stored_payload.get("items") or []),
            source_states=stored_payload.get("sources"),
            error_summary="; ".join(stored_payload.get("errors") or []) or None,
            cache_mode="seeded",
            payload_status=payload_status,
        )
        return {"status": "stored", "payload": stored_payload}


def _result_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    return {
        "status": result.get("status"),
        "payloadStatus": payload.get("status"),
        "cacheMode": payload.get("cacheMode"),
        "items": len(payload.get("items") or []),
        "summary": payload.get("summary") if isinstance(payload.get("summary"), dict) else None,
        "errors": payload.get("errors") or ([result.get("error")] if result.get("error") else []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Market TV Wire snapshots into Redis and SQLite")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("POLYDATA_MARKET_TV_WIRE_WATCH_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)))
    args = parser.parse_args()
    settings = load_api_settings()
    watcher = MarketTvWireWatcher(
        redis_url=settings.redis_url,
        redis_prefix=settings.redis_prefix,
        snapshot_sqlite_path=settings.snapshot_sqlite_path,
        settings=settings,
        interval_seconds=args.interval,
    )
    watcher.redis_client.ping()
    print(f"[market-tv-wire] redis_key={watcher.redis_key()} sqlite={settings.snapshot_sqlite_path}", file=sys.stderr)
    if not args.watch:
        print(json.dumps(_result_summary(watcher.run_once()), ensure_ascii=False), file=sys.stderr)
        return 0
    while True:
        try:
            print(json.dumps(_result_summary(watcher.run_once()), ensure_ascii=False), file=sys.stderr)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            watcher.store_meta_fail_soft(status="error", record_count=0, source_states={"marketTvWire": {"status": "error"}}, error_summary=str(exc), preserve=True)
            print(f"[market-tv-wire] ERROR {exc}", file=sys.stderr)
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
