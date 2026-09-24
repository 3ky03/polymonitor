#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone watcher for World Cup dashboard snapshots."""

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
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

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
from api.services import worldcup_dashboard_service
from db import DEFAULT_DB_PATH, dict_from_row, get_connection
from runtime.seed_meta import SeedMetaStore, build_seed_meta_payload
from runtime.snapshot_store import SnapshotStore
from runtime.telegram_panel_publish import publish_cached_panel_snapshot
from telegram.topics.client import TelegramClient
from telegram.topics.config import load_settings as load_telegram_settings
from telegram.topics.models import MessageCandidate
from telegram.topics.publisher import publish_candidates
from telegram.topics.state import PublishState, state_lock


DEFAULT_INTERVAL_SECONDS = 300
SEED_META_NAMESPACE = "seed-meta:sports"
SEED_META_CACHE_KEY = "worldcup-dashboard"
SEED_META_SERVICE_NAME = "polydata-worldcup-dashboard-seed.service"
WORLDCUP_WORKSPACE_URL = "https://www.polymonitor.club/?workspace=worldcup"


class _LoggerAdapter:
    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[worldcup-dashboard] ERROR {message % args if args else message}", file=sys.stderr)


class _AppAdapter:
    logger = _LoggerAdapter()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{str(prefix or '')}{namespace}:{cache_key}"


def _record_count(payload: Dict[str, Any]) -> int:
    return sum(
        len(payload.get(key) if isinstance(payload.get(key), list) else [])
        for key in ("cities", "matches", "news", "weather", "odds")
    )


def _odds_key(row: Dict[str, Any]) -> str:
    return str(row.get("matchId") or row.get("slug") or row.get("marketTitle") or row.get("title") or "").strip()


def _new_odds(previous: Dict[str, Any], current: Dict[str, Any]) -> list[Dict[str, Any]]:
    previous_keys = {
        _odds_key(row)
        for row in previous.get("odds", [])
        if isinstance(row, dict) and _odds_key(row)
    }
    rows: list[Dict[str, Any]] = []
    for row in current.get("odds", []):
        if not isinstance(row, dict):
            continue
        key = _odds_key(row)
        if key and key not in previous_keys:
            rows.append(row)
    return rows


def _odds_alert_candidate(row: Dict[str, Any]) -> MessageCandidate:
    home = str(row.get("homeTeam") or "Home").strip()
    away = str(row.get("awayTeam") or "Away").strip()
    title = str(row.get("marketTitle") or row.get("title") or f"{home} vs {away}").strip()
    market_url = str(row.get("marketUrl") or "").strip()
    probabilities = row.get("probabilities") if isinstance(row.get("probabilities"), list) else []
    lines = [
        f"{home} vs {away}",
        title[:180],
    ]
    if probabilities:
        pairs = []
        for item in probabilities[:4]:
            if not isinstance(item, dict):
                continue
            outcome = str(item.get("outcome") or "").strip()
            price = str(item.get("price") or "").strip()
            if outcome and price:
                pairs.append(f"{outcome} {price}")
        if pairs:
            lines.append(" | ".join(pairs))
    if market_url:
        lines.append(f"Market: {market_url}")
    lines.append(f"Workspace: {WORLDCUP_WORKSPACE_URL}")
    return MessageCandidate(
        topic="worldcup",
        dedupe_key=f"worldcup-odds-listed:{_odds_key(row)}",
        text="\n".join(["⚽ World Cup odds listed", "", *lines]),
        priority="normal",
        metadata={"panel": "worldcup-dashboard", "kind": "odds-listed"},
        link_preview=bool(market_url or WORLDCUP_WORKSPACE_URL),
        reply_markup={
            "inline_keyboard": [
                [{"text": "Odds", "url": "https://t.me/PolyMonitorQuery_bot?start=odds"}, {"text": "Match", "url": "https://t.me/PolyMonitorQuery_bot?start=worldcup"}],
                [{"text": "Open Workspace", "url": WORLDCUP_WORKSPACE_URL}],
            ]
        },
    )


class WorldCupDashboardWatcher:
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
            raise RuntimeError("POLYDATA_REDIS_URL is required for World Cup dashboard watcher")
        self.settings = settings
        self.interval_seconds = max(300, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
        self.redis_prefix = str(redis_prefix or "")
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.snapshot_store = SnapshotStore(snapshot_sqlite_path)
        self.seed_meta_store = SeedMetaStore(redis_client=self.redis_client, redis_prefix=self.redis_prefix, snapshot_store=self.snapshot_store)
        self.requests = requests.Session()
        self.requests.trust_env = False
        self.requests.headers.update({"User-Agent": "polydata-worldcup-dashboard/1.0"})

    def ttl_seconds(self) -> int:
        configured = int(os.environ.get("POLYDATA_WORLDCUP_DASHBOARD_SEED_TTL_SECONDS", "0") or 0)
        if configured > 0:
            return configured
        return max(900, self.interval_seconds * 3, int(getattr(self.settings, "sports_runtime_ttl_seconds", 300) or 300) * 3)

    def redis_key(self) -> str:
        return _redis_key(
            self.redis_prefix,
            worldcup_dashboard_service.WORLDCUP_DASHBOARD_NAMESPACE,
            worldcup_dashboard_service.WORLDCUP_DASHBOARD_CACHE_KEY,
        )

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

    def http_text_get(self, url: str, timeout: int = 12, headers: Optional[Dict[str, str]] = None) -> str:
        response = self.requests.get(url, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.text

    def query_all(self, sql: str, params: Optional[list[Any]] = None) -> list[Dict[str, Any]]:
        conn = get_connection(DEFAULT_DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(sql, tuple(params or ()))
            return [dict_from_row(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def service_context(self) -> Dict[str, Any]:
        return {
            "SETTINGS": self.settings,
            "SPORTS_RUNTIME_TTL_SECONDS": self.settings.sports_runtime_ttl_seconds,
            "SNAPSHOT_STORE": self.snapshot_store,
            "app": _AppAdapter(),
            "BeautifulSoup": BeautifulSoup,
            "http_json_get": self.http_json_get,
            "http_text_get": self.http_text_get,
            "query_all": self.query_all,
            "requests": requests,
            "utc_now_iso": utc_now_iso,
        }

    def load_previous_payload(self) -> Dict[str, Any]:
        try:
            raw = self.redis_client.get(self.redis_key())
            if raw:
                payload = json.loads(raw)
                if isinstance(payload, dict) and not worldcup_dashboard_service._has_generated_fallback_artifacts(payload):
                    return payload
        except Exception:
            print("[worldcup-dashboard] WARN redis read failed", file=sys.stderr)
        stale = self.snapshot_store.get_stale(
            worldcup_dashboard_service.WORLDCUP_DASHBOARD_NAMESPACE,
            worldcup_dashboard_service.WORLDCUP_DASHBOARD_CACHE_KEY,
        )
        if isinstance(stale, dict) and not worldcup_dashboard_service._has_generated_fallback_artifacts(stale):
            return stale
        return {}

    def store_payload(self, payload: Dict[str, Any]) -> None:
        ttl_seconds = self.ttl_seconds()
        self.snapshot_store.set(
            worldcup_dashboard_service.WORLDCUP_DASHBOARD_NAMESPACE,
            worldcup_dashboard_service.WORLDCUP_DASHBOARD_CACHE_KEY,
            payload,
            ttl_seconds,
        )
        self.redis_client.set(self.redis_key(), json.dumps(payload, ensure_ascii=True, default=str), ex=ttl_seconds)

    def publish_new_odds_alerts(self, previous: Dict[str, Any], payload: Dict[str, Any]) -> int:
        rows = _new_odds(previous, payload)
        if not rows:
            return 0
        try:
            settings = load_telegram_settings()
        except Exception as exc:
            print(f"[worldcup-dashboard] WARN telegram settings unavailable for odds alert: {exc}", file=sys.stderr)
            return 0
        if not settings.bot_token and not settings.dry_run:
            return 0
        candidates = [_odds_alert_candidate(row) for row in rows[:8]]
        telegram = TelegramClient(
            bot_token=settings.bot_token,
            api_base=settings.telegram_api_base,
            timeout_seconds=settings.request_timeout_seconds,
        )
        with state_lock(settings.state_path):
            state = PublishState(settings.state_path)
            result = publish_candidates(candidates, settings=settings, state=state, telegram=telegram, dry_run=settings.dry_run)
        if result.sent:
            print(f"[worldcup-dashboard] odds-alert sent={result.sent} candidates={len(candidates)}", file=sys.stderr)
        return result.sent

    def store_seed_meta(
        self,
        *,
        status: str,
        record_count: int,
        source_states: Dict[str, Any],
        error_summary: Optional[str],
        preserve_last_success: bool = False,
    ) -> None:
        previous = self.seed_meta_store.load(SEED_META_NAMESPACE, SEED_META_CACHE_KEY) or {}
        attempted_at = utc_now_iso()
        last_success_at = previous.get("lastSuccessAt")
        if not preserve_last_success and status in {"ok", "degraded", "preserved"}:
            last_success_at = attempted_at
        payload = build_seed_meta_payload(
            panel_id="worldcup-dashboard",
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
            cache_mode="verified-cache",
            payload_status=status,
            metadata={"result": "stored", "refreshSeconds": self.interval_seconds},
        )
        self.seed_meta_store.store(SEED_META_NAMESPACE, SEED_META_CACHE_KEY, payload)

    def run_once(self) -> Dict[str, Any]:
        previous = self.load_previous_payload()
        try:
            payload = worldcup_dashboard_service.build_worldcup_dashboard_payload(self.service_context())
        except Exception as exc:
            if previous:
                preserved = {**previous, "cacheMode": "preserved"}
                self.store_payload(preserved)
                self.store_seed_meta(
                    status="preserved",
                    record_count=_record_count(previous),
                    source_states={"worldcupDashboard": "error"},
                    error_summary=str(exc),
                    preserve_last_success=True,
                )
                return {"status": "preserved", "payload": preserved}
            self.store_seed_meta(
                status="error",
                record_count=0,
                source_states={"worldcupDashboard": "error"},
                error_summary=str(exc),
                preserve_last_success=True,
            )
            raise

        record_count = _record_count(payload)
        if previous and len(payload.get("matches") or []) < 64:
            preserved = {**previous, "cacheMode": "preserved"}
            self.store_payload(preserved)
            self.store_seed_meta(
                status="preserved",
                record_count=_record_count(previous),
                source_states=payload.get("providerStates") if isinstance(payload.get("providerStates"), dict) else {"schedule": "short"},
                error_summary="Preserved previous World Cup dashboard because schedule payload was too small",
                preserve_last_success=True,
            )
            return {"status": "preserved", "payload": preserved}

        payload = {**payload, "cacheMode": "remote"}
        self.store_payload(payload)
        telegram_sent = publish_cached_panel_snapshot("worldcup-dashboard", payload)
        odds_alerts_sent = self.publish_new_odds_alerts(previous, payload)
        status = "ok" if record_count > 0 and len(payload.get("matches") or []) >= 64 else "degraded"
        self.store_seed_meta(
            status=status,
            record_count=record_count,
            source_states=payload.get("providerStates") if isinstance(payload.get("providerStates"), dict) else {},
            error_summary=None if status == "ok" else "World Cup dashboard snapshot is degraded",
        )
        return {"status": status, "payload": payload, "telegramSent": telegram_sent, "oddsAlertsSent": odds_alerts_sent}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed World Cup dashboard snapshots into Redis and SQLite")
    parser.add_argument("--watch", action="store_true", help="Run continuously instead of once")
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("POLYDATA_WORLDCUP_DASHBOARD_WATCH_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)),
        help="Seconds between refresh runs in watch mode",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    settings = load_api_settings()
    watcher = WorldCupDashboardWatcher(
        redis_url=settings.redis_url,
        redis_prefix=settings.redis_prefix,
        snapshot_sqlite_path=settings.snapshot_sqlite_path,
        settings=settings,
        interval_seconds=args.interval,
    )
    watcher.redis_client.ping()
    print(f"[worldcup-dashboard] redis_key={watcher.redis_key()} sqlite={settings.snapshot_sqlite_path}", file=sys.stderr)
    if not args.watch:
        print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        return 0

    while True:
        try:
            print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            watcher.store_seed_meta(
                status="error",
                record_count=0,
                source_states={"worldcupDashboard": "error"},
                error_summary=str(exc),
                preserve_last_success=True,
            )
            print(f"[worldcup-dashboard] ERROR watch loop failed: {exc}", file=sys.stderr)
        time.sleep(watcher.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
