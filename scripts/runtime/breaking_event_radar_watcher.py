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
from api.services import breaking_event_radar_service
from runtime.market_search import build_market_search
from runtime.seed_meta import SeedMetaStore, build_seed_meta_payload, utc_now_iso
from runtime.snapshot_store import SnapshotStore


DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_LIMIT = 12
SEED_META_NAMESPACE = "seed-meta:evidence"
SEED_META_CACHE_KEY = "breaking-event-radar"
SEED_META_SERVICE_NAME = "polydata-breaking-event-radar-seed.service"


class _Logger:
    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[breaking-event-radar] ERROR {message % args if args else message}", file=sys.stderr)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[breaking-event-radar] WARN {message % args if args else message}", file=sys.stderr)


class _App:
    logger = _Logger()


def _redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{str(prefix or '')}{namespace}:{cache_key}"


class BreakingEventRadarWatcher:
    def __init__(self, *, redis_url: str, redis_prefix: str, snapshot_sqlite_path: str, settings: Any, limit: int, interval_seconds: int) -> None:
        if redis is None:
            raise RuntimeError("redis package is required. Install scripts/requirements.txt")
        if requests is None:
            raise RuntimeError("requests package is required. Install scripts/requirements.txt")
        if not str(redis_url or "").strip():
            raise RuntimeError("POLYDATA_REDIS_URL is required for breaking event radar watcher")
        self.settings = settings
        self.limit = max(1, int(limit or DEFAULT_LIMIT))
        self.interval_seconds = max(60, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
        self.redis_prefix = str(redis_prefix or "")
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.snapshot_store = SnapshotStore(snapshot_sqlite_path)
        self.seed_meta_store = SeedMetaStore(redis_client=self.redis_client, redis_prefix=self.redis_prefix, snapshot_store=self.snapshot_store)
        self.requests = requests.Session()
        self.requests.trust_env = str(os.environ.get("POLYDATA_RUNTIME_TRUST_ENV", "0")).strip().lower() in {"1", "true", "yes", "on"}
        self.requests.headers.update({"User-Agent": "polydata-breaking-event-radar/1.0"})

    def ttl_seconds(self) -> int:
        configured = int(os.environ.get("POLYDATA_BREAKING_EVENT_RADAR_SEED_TTL_SECONDS", "0") or 0)
        if configured > 0:
            return configured
        return max(300, self.interval_seconds * 3)

    def redis_key(self) -> str:
        return _redis_key(
            self.redis_prefix,
            breaking_event_radar_service.BREAKING_EVENT_RADAR_SNAPSHOT_NAMESPACE,
            breaking_event_radar_service.BREAKING_EVENT_RADAR_CACHE_KEY,
        )

    def http_json_get(self, url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 12, headers: Optional[Dict[str, str]] = None) -> Any:
        response = self.requests.get(url, params=params, timeout=timeout, headers=headers)
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    def service_context(self) -> Dict[str, Any]:
        context: Dict[str, Any] = {
            "SETTINGS": self.settings,
            "SNAPSHOT_STORE": self.snapshot_store,
            "app": _App(),
            "http_json_get": self.http_json_get,
            "utc_now_iso": utc_now_iso,
        }
        if str(os.environ.get("POLYDATA_BREAKING_EVENT_PM_SEARCH_ENABLED", "1")).strip().lower() in {"1", "true", "yes", "on"}:
            try:
                context["search_markets"] = build_market_search(self.settings)
            except Exception as exc:
                print(f"[breaking-event-radar] WARN market search disabled: {exc}", file=sys.stderr)
        return context

    def load_previous_payload(self) -> Dict[str, Any]:
        try:
            raw = self.redis_client.get(self.redis_key())
            if raw:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    return payload
        except Exception:
            print("[breaking-event-radar] WARN redis read failed", file=sys.stderr)
        stale = self.snapshot_store.get_stale(
            breaking_event_radar_service.BREAKING_EVENT_RADAR_SNAPSHOT_NAMESPACE,
            breaking_event_radar_service.BREAKING_EVENT_RADAR_CACHE_KEY,
        )
        return stale if isinstance(stale, dict) else {}

    def store_payload(self, payload: Dict[str, Any]) -> None:
        ttl_seconds = self.ttl_seconds()
        self.snapshot_store.set(
            breaking_event_radar_service.BREAKING_EVENT_RADAR_SNAPSHOT_NAMESPACE,
            breaking_event_radar_service.BREAKING_EVENT_RADAR_CACHE_KEY,
            payload,
            ttl_seconds,
        )
        self.redis_client.set(self.redis_key(), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl_seconds)

    def store_seed_meta(self, *, status: str, record_count: int, source_states: Dict[str, Any], error_summary: str | None, preserve_last_success: bool = False) -> None:
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
            cache_mode="seeded",
            payload_status=status,
            metadata={"workerVersion": "v1", "limit": self.limit},
        )
        self.seed_meta_store.store(SEED_META_NAMESPACE, SEED_META_CACHE_KEY, payload)

    def store_seed_meta_fail_soft(self, **kwargs: Any) -> bool:
        try:
            self.store_seed_meta(**kwargs)
            return True
        except Exception as exc:
            print(
                f"[breaking-event-radar] WARN seed meta write failed: {exc.__class__.__name__}",
                file=sys.stderr,
            )
            return False

    def run_once(self) -> Dict[str, Any]:
        previous = self.load_previous_payload()
        topic_ids = breaking_event_radar_service.breaking_event_topic_ids()
        previous_rotation = (
            previous.get("rotation")
            if isinstance(previous.get("rotation"), dict)
            else {}
        )
        try:
            previous_index = int(previous_rotation.get("index", -1))
        except (TypeError, ValueError):
            previous_index = -1
        rotation_index = (previous_index + 1) % max(1, len(topic_ids))
        topic_id = topic_ids[rotation_index] if topic_ids else ""
        try:
            payload = breaking_event_radar_service.build_breaking_event_radar_payload(
                self.service_context(),
                limit=self.limit,
                topic_ids=[topic_id] if topic_id else None,
            )
            payload = breaking_event_radar_service.merge_breaking_event_radar_payloads(
                previous,
                {
                    **payload,
                    "rotation": {
                        "index": rotation_index,
                        "topicId": topic_id,
                        "totalTopics": len(topic_ids),
                    },
                },
                limit=self.limit,
            )
        except Exception as exc:
            if previous:
                preserved = {**previous, "cacheMode": "preserved"}
                self.store_payload(preserved)
                self.store_seed_meta(status="preserved", record_count=len(previous.get("items") or []), source_states={"breakingEventRadar": "error"}, error_summary=str(exc), preserve_last_success=True)
                return {"status": "preserved", "payload": preserved}
            self.store_seed_meta(status="error", record_count=0, source_states={"breakingEventRadar": "error"}, error_summary=str(exc), preserve_last_success=True)
            raise

        record_count = len(payload.get("items") or [])
        if previous and record_count <= 0:
            preserved = {**previous, "cacheMode": "preserved"}
            self.store_payload(preserved)
            self.store_seed_meta(
                status="preserved",
                record_count=len(previous.get("items") or []),
                source_states=payload.get("sources") if isinstance(payload.get("sources"), dict) else {"breakingEventRadar": "empty"},
                error_summary="Preserved previous breaking event radar snapshot because new payload was empty",
                preserve_last_success=True,
            )
            return {"status": "preserved", "payload": preserved}

        payload = {**payload, "cacheMode": "seeded", "freshness": "seeded"}
        self.store_payload(payload)
        status = str(payload.get("status") or ("ok" if record_count else "degraded"))
        self.store_seed_meta(
            status=status if status in {"ok", "degraded"} else "degraded",
            record_count=record_count,
            source_states=payload.get("sources") if isinstance(payload.get("sources"), dict) else {},
            error_summary="; ".join(payload.get("errors") or []) or None,
        )
        return {"status": status, "payload": payload}


def _result_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    return {
        "status": result.get("status"),
        "payloadStatus": payload.get("status"),
        "cacheMode": payload.get("cacheMode"),
        "items": len(payload.get("items") or []),
        "sources": sources,
        "errors": payload.get("errors") or [],
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed Breaking Event Radar snapshots into Redis and SQLite")
    parser.add_argument("--watch", action="store_true", help="Run continuously instead of once")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("POLYDATA_BREAKING_EVENT_RADAR_WATCH_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)))
    parser.add_argument("--limit", type=int, default=int(os.environ.get("POLYDATA_BREAKING_EVENT_RADAR_LIMIT", DEFAULT_LIMIT)))
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    settings = load_api_settings()
    watcher = BreakingEventRadarWatcher(
        redis_url=settings.redis_url,
        redis_prefix=settings.redis_prefix,
        snapshot_sqlite_path=settings.snapshot_sqlite_path,
        settings=settings,
        limit=args.limit,
        interval_seconds=args.interval,
    )
    watcher.redis_client.ping()
    print(f"[breaking-event-radar] redis_key={watcher.redis_key()} sqlite={settings.snapshot_sqlite_path}", file=sys.stderr)
    if not args.watch:
        print(json.dumps(_result_summary(watcher.run_once()), ensure_ascii=False), file=sys.stderr)
        return 0
    while True:
        try:
            print(json.dumps(_result_summary(watcher.run_once()), ensure_ascii=False), file=sys.stderr)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            watcher.store_seed_meta_fail_soft(status="error", record_count=0, source_states={"breakingEventRadar": "error"}, error_summary=str(exc), preserve_last_success=True)
            print(f"[breaking-event-radar] ERROR watch loop failed: {exc}", file=sys.stderr)
        time.sleep(watcher.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
