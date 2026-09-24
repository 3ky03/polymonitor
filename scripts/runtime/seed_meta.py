#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Helpers for watcher seed metadata stored in Redis + SQLite snapshot."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional


SEED_META_TTL_SECONDS = 7 * 24 * 60 * 60


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{str(prefix or '')}{namespace}:{cache_key}"


class SeedMetaStore:
    def __init__(self, *, redis_client: Any, redis_prefix: str, snapshot_store: Any) -> None:
        self.redis_client = redis_client
        self.redis_prefix = str(redis_prefix or "")
        self.snapshot_store = snapshot_store

    def _redis_key(self, namespace: str, cache_key: str) -> str:
        return redis_key(self.redis_prefix, namespace, cache_key)

    def load(self, namespace: str, cache_key: str) -> Optional[Dict[str, Any]]:
        try:
            raw = self.redis_client.get(self._redis_key(namespace, cache_key))
            if raw:
                payload = json.loads(str(raw))
                if isinstance(payload, dict):
                    return payload
        except Exception:
            pass
        if self.snapshot_store is None:
            return None
        stale = self.snapshot_store.get_stale(namespace, cache_key)
        return stale if isinstance(stale, dict) else None

    def store(self, namespace: str, cache_key: str, payload: Dict[str, Any], ttl_seconds: int = SEED_META_TTL_SECONDS) -> Dict[str, Any]:
        if self.snapshot_store is not None:
            self.snapshot_store.set(namespace, cache_key, payload, ttl_seconds)
        try:
            serialized = json.dumps(payload, ensure_ascii=True, default=str)
            try:
                self.redis_client.set(self._redis_key(namespace, cache_key), serialized, ex=ttl_seconds)
            except TypeError:
                self.redis_client.set(self._redis_key(namespace, cache_key), serialized)
        except Exception:
            pass
        return payload


def build_seed_meta_payload(
    *,
    panel_id: str,
    namespace: str,
    cache_key: str,
    service_name: str,
    expected_interval_seconds: int,
    status: str,
    last_attempt_at: Optional[str] = None,
    last_success_at: Optional[str] = None,
    record_count: Optional[int] = None,
    source_states: Optional[Dict[str, Any]] = None,
    error_summary: Optional[str] = None,
    cache_mode: Optional[str] = None,
    payload_status: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_status = str(status or "unknown").strip().lower() or "unknown"
    payload: Dict[str, Any] = {
        "panelId": str(panel_id or "").strip(),
        "namespace": str(namespace or "").strip(),
        "cacheKey": str(cache_key or "").strip(),
        "serviceName": str(service_name or "").strip(),
        "status": normalized_status,
        "lastAttemptAt": str(last_attempt_at or utc_now_iso()),
        "lastSuccessAt": str(last_success_at or last_attempt_at or utc_now_iso()),
        "expectedIntervalSeconds": max(1, int(expected_interval_seconds or 60)),
        "recordCount": max(0, int(record_count or 0)),
        "sourceStates": dict(source_states or {}),
        "errorSummary": str(error_summary).strip() if error_summary else None,
        "cacheMode": str(cache_mode).strip() if cache_mode else None,
        "payloadStatus": str(payload_status).strip() if payload_status else None,
        "updatedAt": utc_now_iso(),
    }
    if metadata:
        payload["metadata"] = dict(metadata)
    return payload
