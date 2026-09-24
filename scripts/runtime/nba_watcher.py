#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone watcher for NBA sports panel snapshots."""

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

try:
    import redis
except ImportError:
    redis = None

try:
    import requests
except ImportError:
    requests = None

from api.config import load_api_settings
from api.services import runtime_service
from runtime.seed_meta import SeedMetaStore, build_seed_meta_payload
from runtime.snapshot_store import SnapshotStore
from runtime.telegram_panel_publish import publish_cached_panel_snapshot


DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_SCOREBOARD_LIMIT = 10
DEFAULT_INTEL_LIMIT = 12
DEFAULT_PREDICTOR_LIMIT = 8
SEED_META_NAMESPACE = "seed-meta:sports"
SEED_META_CACHE_KEY = "nba"
SEED_META_SERVICE_NAME = "polydata-nba-seed.service"


class _LoggerAdapter:
    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[nba] ERROR {message % args if args else message}", file=sys.stderr)


class _AppAdapter:
    logger = _LoggerAdapter()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{str(prefix or '')}{namespace}:{cache_key}"


def _items(payload: Dict[str, Any]) -> list[Any]:
    items = payload.get("items")
    return items if isinstance(items, list) else []


def _nba_record_count(payload: Dict[str, Any]) -> int:
    return len(_items(payload)) + len(payload.get("lineups") if isinstance(payload.get("lineups"), list) else [])


class NbaWatcher:
    def __init__(
        self,
        *,
        redis_url: str,
        redis_prefix: str,
        snapshot_sqlite_path: str,
        settings: Any,
        scoreboard_limit: int = DEFAULT_SCOREBOARD_LIMIT,
        intel_limit: int = DEFAULT_INTEL_LIMIT,
        predictor_limit: int = DEFAULT_PREDICTOR_LIMIT,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        if redis is None:
            raise RuntimeError("redis package is required. Install scripts/requirements.txt")
        if requests is None:
            raise RuntimeError("requests package is required. Install scripts/requirements.txt")
        if not str(redis_url or "").strip():
            raise RuntimeError("POLYDATA_REDIS_URL is required for NBA watcher")
        self.settings = settings
        self.scoreboard_limit = max(1, int(scoreboard_limit or DEFAULT_SCOREBOARD_LIMIT))
        self.intel_limit = max(1, int(intel_limit or DEFAULT_INTEL_LIMIT))
        self.predictor_limit = max(1, int(predictor_limit or DEFAULT_PREDICTOR_LIMIT))
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
        self.requests.headers.update({"User-Agent": "polydata-nba-seed/1.0"})

    def ttl_seconds(self) -> int:
        configured = int(os.environ.get("POLYDATA_NBA_SEED_TTL_SECONDS", "0") or 0)
        if configured > 0:
            return configured
        return max(120, self.interval_seconds * 3, int(self.settings.sports_runtime_ttl_seconds or 60) * 4)

    def seed_meta_namespace(self) -> str:
        return SEED_META_NAMESPACE

    def seed_meta_cache_key(self) -> str:
        return SEED_META_CACHE_KEY

    def load_seed_meta(self, cache_key: str = SEED_META_CACHE_KEY) -> Dict[str, Any]:
        payload = self.seed_meta_store.load(self.seed_meta_namespace(), cache_key)
        return payload if isinstance(payload, dict) else {}

    def store_seed_meta(
        self,
        *,
        status: str,
        record_count: int,
        source_states: Optional[Dict[str, Any]] = None,
        error_summary: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        preserve_last_success: bool = False,
        panel_id: str = "nba",
        cache_key: str = SEED_META_CACHE_KEY,
        payload_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        previous = self.load_seed_meta(cache_key)
        attempted_at = utc_now_iso()
        last_success_at = previous.get("lastSuccessAt")
        if not preserve_last_success and str(status or "").strip().lower() in {"ok", "degraded", "preserved"}:
            last_success_at = attempted_at
        payload = build_seed_meta_payload(
            panel_id=panel_id,
            namespace=self.seed_meta_namespace(),
            cache_key=cache_key,
            service_name=SEED_META_SERVICE_NAME,
            expected_interval_seconds=self.interval_seconds,
            status=status,
            last_attempt_at=attempted_at,
            last_success_at=last_success_at or attempted_at,
            record_count=record_count,
            source_states=source_states,
            error_summary=error_summary,
            cache_mode="seeded",
            payload_status=payload_status or status,
            metadata=metadata,
        )
        return self.seed_meta_store.store(self.seed_meta_namespace(), cache_key, payload)

    def store_seed_meta_fail_soft(self, **kwargs: Any) -> bool:
        try:
            self.store_seed_meta(**kwargs)
            return True
        except Exception as exc:
            print(f"[nba] WARN seed meta write failed: {exc.__class__.__name__}", file=sys.stderr)
            return False

    def redis_key(self, namespace: str, cache_key: str) -> str:
        return _redis_key(self.redis_prefix, namespace, cache_key)

    def load_previous_payload(self, namespace: str, cache_key: str) -> Dict[str, Any]:
        try:
            raw = self.redis_client.get(self.redis_key(namespace, cache_key))
            if raw:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    return payload
        except Exception:
            print(f"[nba] WARN redis read failed namespace={namespace}", file=sys.stderr)
        stale = self.snapshot_store.get_stale(namespace, cache_key)
        return stale if isinstance(stale, dict) else {}

    def store_payload(self, namespace: str, cache_key: str, payload: Dict[str, Any]) -> None:
        ttl_seconds = self.ttl_seconds()
        self.snapshot_store.set(namespace, cache_key, payload, ttl_seconds)
        self.redis_client.set(self.redis_key(namespace, cache_key), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl_seconds)

    def http_json_get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: int = 12,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        response = self.requests.get(url, params=params, timeout=timeout, headers=headers)
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    def safe_float(self, value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def service_context(self) -> Dict[str, Any]:
        return {
            "SETTINGS": self.settings,
            "SPORTS_RUNTIME_TTL_SECONDS": self.settings.sports_runtime_ttl_seconds,
            "app": _AppAdapter(),
            "_safe_float": self.safe_float,
            "http_json_get": self.http_json_get,
            "utc_now_iso": utc_now_iso,
        }

    def _run_component(
        self,
        *,
        label: str,
        panel_id: str,
        namespace: str,
        cache_key: str,
        fetcher: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> Dict[str, Any]:
        previous = self.load_previous_payload(namespace, cache_key)
        try:
            payload = fetcher(self.service_context())
        except Exception as exc:
            if previous:
                self.store_payload(namespace, cache_key, previous)
                return {"status": "preserved", "recordCount": _nba_record_count(previous), "error": str(exc)}
            return {"status": "error", "recordCount": 0, "error": str(exc)}

        record_count = _nba_record_count(payload)
        previous_record_count = _nba_record_count(previous)
        if previous_record_count > 0 and record_count <= 0:
            self.store_payload(namespace, cache_key, previous)
            return {"status": "preserved", "recordCount": previous_record_count, "error": f"{label} returned empty payload"}

        payload = {**payload, "cacheMode": "seeded"}
        self.store_payload(namespace, cache_key, payload)
        telegram_sent = publish_cached_panel_snapshot(panel_id, payload)
        status = "ok" if record_count > 0 else "empty"
        return {"status": status, "recordCount": record_count, "error": None, "telegramSent": telegram_sent}

    def run_once(self) -> Dict[str, Any]:
        components = {
            "scoreboard": self._run_component(
                label="scoreboard",
                panel_id="nba-scoreboard",
                namespace=runtime_service.NBA_SCOREBOARD_NAMESPACE,
                cache_key=runtime_service.build_nba_scoreboard_cache_key(limit=self.scoreboard_limit),
                fetcher=lambda ctx: runtime_service.fetch_live_nba_scoreboard_payload(ctx, limit=self.scoreboard_limit),
            ),
            "intel": self._run_component(
                label="intel",
                panel_id="nba-intel",
                namespace=runtime_service.NBA_INTEL_NAMESPACE,
                cache_key=runtime_service.build_nba_intel_cache_key(limit=self.intel_limit),
                fetcher=lambda ctx: runtime_service.fetch_live_nba_intel_payload(ctx, limit=self.intel_limit),
            ),
            "predictor": self._run_component(
                label="predictor",
                panel_id="espn-matchup-predictor",
                namespace=runtime_service.NBA_MATCHUP_PREDICTOR_NAMESPACE,
                cache_key=runtime_service.build_nba_matchup_predictor_cache_key(limit=self.predictor_limit),
                fetcher=lambda ctx: runtime_service.fetch_live_nba_matchup_predictor_payload(ctx, limit=self.predictor_limit),
            ),
        }
        component_panels = {
            "scoreboard": "nba-scoreboard",
            "intel": "nba-intel",
            "predictor": "espn-matchup-predictor",
        }
        for component_name, panel_id in component_panels.items():
            component = components[component_name]
            payload_status = str(component.get("status") or "unknown")
            if payload_status in {"ok", "empty"}:
                operational_status = "ok"
            elif payload_status == "preserved":
                operational_status = "degraded"
            else:
                operational_status = "error"
            self.store_seed_meta(
                status=operational_status,
                record_count=int(component.get("recordCount") or 0),
                source_states={component_name: payload_status},
                error_summary=str(component.get("error") or "") or None,
                metadata={"result": "stored", "component": component_name},
                preserve_last_success=payload_status in {"preserved", "error"},
                panel_id=panel_id,
                cache_key=panel_id,
                payload_status=payload_status,
            )
        source_states = {key: value["status"] for key, value in components.items()}
        record_count = sum(int(value.get("recordCount") or 0) for value in components.values())
        errors = [f"{key}: {value['error']}" for key, value in components.items() if value.get("error")]
        if all(value["status"] in {"ok", "empty"} for value in components.values()):
            status = "ok"
        elif any(value["status"] in {"ok", "preserved"} for value in components.values()):
            status = "degraded"
        else:
            status = "error"
        self.store_seed_meta(
            status=status,
            record_count=record_count,
            source_states=source_states,
            error_summary="; ".join(errors) if errors else None,
            metadata={
                "result": "stored",
                "scoreboardLimit": self.scoreboard_limit,
                "intelLimit": self.intel_limit,
                "predictorLimit": self.predictor_limit,
            },
            preserve_last_success=status == "error",
        )
        return {"status": status, "components": components}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed NBA sports panel snapshots into Redis and SQLite")
    parser.add_argument("--watch", action="store_true", help="Run continuously instead of once")
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("POLYDATA_NBA_WATCH_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)),
        help="Seconds between refresh runs in watch mode",
    )
    parser.add_argument("--scoreboard-limit", type=int, default=int(os.environ.get("POLYDATA_NBA_SCOREBOARD_LIMIT", DEFAULT_SCOREBOARD_LIMIT)))
    parser.add_argument("--intel-limit", type=int, default=int(os.environ.get("POLYDATA_NBA_INTEL_LIMIT", DEFAULT_INTEL_LIMIT)))
    parser.add_argument("--predictor-limit", type=int, default=int(os.environ.get("POLYDATA_NBA_PREDICTOR_LIMIT", DEFAULT_PREDICTOR_LIMIT)))
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    settings = load_api_settings()
    watcher = NbaWatcher(
        redis_url=settings.redis_url,
        redis_prefix=settings.redis_prefix,
        snapshot_sqlite_path=settings.snapshot_sqlite_path,
        settings=settings,
        scoreboard_limit=args.scoreboard_limit,
        intel_limit=args.intel_limit,
        predictor_limit=args.predictor_limit,
        interval_seconds=args.interval,
    )
    watcher.redis_client.ping()
    print(f"[nba] sqlite={settings.snapshot_sqlite_path}", file=sys.stderr)
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
            watcher.store_seed_meta_fail_soft(
                status="error",
                record_count=0,
                source_states={"nba": "error"},
                error_summary=str(exc),
                preserve_last_success=True,
                metadata={"result": "exception"},
            )
            print(f"[nba] ERROR watch loop failed: {exc}", file=sys.stderr)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
