#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared runtime helpers for World Cup split seed watchers."""

from __future__ import annotations

import json
import os
import sys
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

SEED_META_NAMESPACE = "seed-meta:sports"


class _LoggerAdapter:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        print(f"[{self.prefix}] ERROR {message % args if args else message}", file=sys.stderr)


class _AppAdapter:
    def __init__(self, prefix: str) -> None:
        self.logger = _LoggerAdapter(prefix)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{str(prefix or '')}{namespace}:{cache_key}"


def record_count(payload: Dict[str, Any]) -> int:
    return sum(
        len(payload.get(key) if isinstance(payload.get(key), list) else [])
        for key in ("cities", "matches", "news", "weather", "odds")
    )


class WorldCupSeedWatcher:
    def __init__(
        self,
        *,
        mode: str,
        service_name: str,
        seed_meta_key: str,
        interval_seconds: int,
    ) -> None:
        if redis is None:
            raise RuntimeError("redis package is required. Install scripts/requirements.txt")
        if requests is None:
            raise RuntimeError("requests package is required. Install scripts/requirements.txt")
        self.mode = mode
        self.service_name = service_name
        self.seed_meta_key = seed_meta_key
        self.interval_seconds = max(300, int(interval_seconds or 300))
        self.settings = load_api_settings()
        if not str(self.settings.redis_url or "").strip():
            raise RuntimeError("POLYDATA_REDIS_URL is required for World Cup seed watcher")
        self.redis_prefix = str(self.settings.redis_prefix or "")
        self.redis_client = redis.from_url(self.settings.redis_url, decode_responses=True)
        self.snapshot_store = SnapshotStore(self.settings.snapshot_sqlite_path)
        self.seed_meta_store = SeedMetaStore(redis_client=self.redis_client, redis_prefix=self.redis_prefix, snapshot_store=self.snapshot_store)
        self.requests = requests.Session()
        self.requests.trust_env = False
        self.requests.headers.update({"User-Agent": f"polydata-worldcup-{mode}-seed/1.0"})

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

    def get_cached_json(self, namespace: str, cache_key: str) -> Any:
        raw = self.redis_client.get(redis_key(self.redis_prefix, namespace, cache_key))
        if not raw:
            return None
        return json.loads(str(raw))

    def set_cached_json(self, namespace: str, cache_key: str, payload: Any, ttl_seconds: int) -> None:
        self.redis_client.set(
            redis_key(self.redis_prefix, namespace, cache_key),
            json.dumps(payload, ensure_ascii=True, default=str),
            ex=max(1, int(ttl_seconds or 1)),
        )

    def service_context(self) -> Dict[str, Any]:
        return {
            "SETTINGS": self.settings,
            "SPORTS_RUNTIME_TTL_SECONDS": self.settings.sports_runtime_ttl_seconds,
            "SNAPSHOT_STORE": self.snapshot_store,
            "app": _AppAdapter(self.mode),
            "BeautifulSoup": BeautifulSoup,
            "get_cached_json": self.get_cached_json,
            "http_json_get": self.http_json_get,
            "http_text_get": self.http_text_get,
            "query_all": self.query_all,
            "requests": requests,
            "redis_client": self.redis_client,
            "redis_prefix": self.redis_prefix,
            "set_cached_json": self.set_cached_json,
            "utc_now_iso": utc_now_iso,
        }

    def store_seed_meta(
        self,
        *,
        status: str,
        record_count_value: int,
        source_states: Dict[str, Any],
        error_summary: Optional[str],
        preserve_last_success: bool = False,
    ) -> None:
        previous = self.seed_meta_store.load(SEED_META_NAMESPACE, self.seed_meta_key) or {}
        attempted_at = utc_now_iso()
        last_success_at = previous.get("lastSuccessAt")
        if not preserve_last_success and status in {"ok", "degraded", "preserved"}:
            last_success_at = attempted_at
        payload = build_seed_meta_payload(
            panel_id=self.seed_meta_key,
            namespace=SEED_META_NAMESPACE,
            cache_key=self.seed_meta_key,
            service_name=self.service_name,
            expected_interval_seconds=self.interval_seconds,
            status=status,
            last_attempt_at=attempted_at,
            last_success_at=last_success_at or attempted_at,
            record_count=record_count_value,
            source_states=source_states,
            error_summary=error_summary,
            cache_mode="verified-cache",
            payload_status=status,
            metadata={"mode": self.mode, "refreshSeconds": self.interval_seconds},
        )
        self.seed_meta_store.store(SEED_META_NAMESPACE, self.seed_meta_key, payload)

    def print_result(self, result: Dict[str, Any]) -> None:
        print(json.dumps(result, ensure_ascii=False, default=str), file=sys.stderr)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default
