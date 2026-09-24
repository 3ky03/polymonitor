from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Dict

from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
    resolve_service_callable,
    resolve_service_value,
)
from f1.runtime_feed import build_f1_panel_payload


F1_SNAPSHOT_NAMESPACE = "snapshot:sports:f1"
F1_SELECTION_VERSION = 3


@dataclass(frozen=True)
class F1RuntimeDependencies:
    settings: Any
    application: Any
    requests_lib: Any
    utc_now_iso: Callable[..., Any]
    get_cached_json: Callable[..., Any]
    set_cached_json: Callable[..., Any] | None
    snapshot_store: Any
    sports_runtime_ttl_seconds: int | None

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> F1RuntimeDependencies:
        return cls(
            settings=resolve_service_value(context, "SETTINGS"),
            application=resolve_service_value(context, "app"),
            requests_lib=resolve_optional_service_value(
                context,
                "requests",
            ),
            utc_now_iso=resolve_service_callable(
                context,
                "utc_now_iso",
            ),
            get_cached_json=resolve_service_callable(
                context,
                "get_cached_json",
            ),
            set_cached_json=resolve_optional_service_callable(
                context,
                "set_cached_json",
            ),
            snapshot_store=resolve_service_value(
                context,
                "SNAPSHOT_STORE",
            ),
            sports_runtime_ttl_seconds=resolve_service_value(
                context,
                "SPORTS_RUNTIME_TTL_SECONDS",
            ),
        )


F1RuntimeContext = Mapping[str, Any] | F1RuntimeDependencies


def _dependencies(
    context: F1RuntimeContext,
) -> F1RuntimeDependencies:
    if isinstance(context, F1RuntimeDependencies):
        return context
    return F1RuntimeDependencies.from_context(context)


def build_f1_cache_key(limit: int = 10) -> str:
    return json.dumps({"limit": limit, "version": F1_SELECTION_VERSION}, sort_keys=True, ensure_ascii=True)


def normalize_f1_panel_payload(payload: Any, *, settings: Any, limit: int = 10, generated_at: str | None = None) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "generatedAt": str(generated_at or ""),
            "source": "bwenews-rss",
            "sourceUrl": settings.f1_bwenews_source_url,
            "cards": [],
            "items": [],
            "focusMeeting": None,
            "status": "invalid",
        }
    cards = [item for item in (payload.get("cards") or payload.get("items") or []) if isinstance(item, dict)][:limit]
    return {
        **payload,
        "generatedAt": str(payload.get("generatedAt") or generated_at or ""),
        "source": str(payload.get("source") or "bwenews-rss"),
        "sourceUrl": str(payload.get("sourceUrl") or settings.f1_bwenews_source_url),
        "status": str(payload.get("status") or ("ok" if cards else "empty")),
        "focusMeeting": payload.get("focusMeeting"),
        "cards": cards,
        "items": cards,
    }


def fetch_live_f1_panel_payload(
    ctx: F1RuntimeContext,
    limit: int = 10,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    try:
        payload = build_f1_panel_payload(
            requests_lib=dependencies.requests_lib,
            limit=limit,
            feed_specs=[
                {
                    "source": "BWENews",
                    "url": dependencies.settings.f1_bwenews_rss_url,
                    "source_url": dependencies.settings.f1_bwenews_source_url,
                }
            ],
        )
        return normalize_f1_panel_payload(
            payload,
            settings=dependencies.settings,
            limit=limit,
            generated_at=dependencies.utc_now_iso(),
        )
    except Exception:
        dependencies.application.logger.exception("f1 runtime snapshot build failed")
        return normalize_f1_panel_payload(
            {"status": "error"},
            settings=dependencies.settings,
            limit=limit,
            generated_at=dependencies.utc_now_iso(),
        )


def _with_cache_mode(payload: Dict[str, Any], cache_mode: str) -> Dict[str, Any]:
    return {**payload, "cacheMode": str(payload.get("cacheMode") or cache_mode)}


def _read_seeded_snapshot(
    dependencies: F1RuntimeDependencies,
    *,
    cache_key: str,
    ttl_seconds: int,
) -> Dict[str, Any] | None:
    redis_payload = dependencies.get_cached_json(F1_SNAPSHOT_NAMESPACE, cache_key)
    if isinstance(redis_payload, dict):
        dependencies.snapshot_store.set(F1_SNAPSHOT_NAMESPACE, cache_key, redis_payload, ttl_seconds)
        return _with_cache_mode(redis_payload, "redis-seed")

    sqlite_payload = dependencies.snapshot_store.get(F1_SNAPSHOT_NAMESPACE, cache_key)
    if isinstance(sqlite_payload, dict):
        setter = dependencies.set_cached_json
        if setter is not None:
            setter(F1_SNAPSHOT_NAMESPACE, cache_key, sqlite_payload, ttl_seconds)
        return _with_cache_mode(sqlite_payload, "sqlite-seed")

    stale_payload = dependencies.snapshot_store.get_stale(F1_SNAPSHOT_NAMESPACE, cache_key)
    if isinstance(stale_payload, dict):
        setter = dependencies.set_cached_json
        if setter is not None:
            setter(F1_SNAPSHOT_NAMESPACE, cache_key, stale_payload, min(15, ttl_seconds))
        return _with_cache_mode(stale_payload, "stale-seed")
    return None


def get_f1_panel_snapshot(
    ctx: F1RuntimeContext,
    limit: int = 10,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    ttl_seconds = max(15, int(dependencies.sports_runtime_ttl_seconds))
    cache_key = build_f1_cache_key(limit=limit)
    seeded_payload = _read_seeded_snapshot(dependencies, cache_key=cache_key, ttl_seconds=ttl_seconds)
    if seeded_payload is None and int(limit or 0) != 10:
        seeded_payload = _read_seeded_snapshot(dependencies, cache_key=build_f1_cache_key(limit=10), ttl_seconds=ttl_seconds)
    if seeded_payload is not None:
        return normalize_f1_panel_payload(
            seeded_payload,
            settings=dependencies.settings,
            limit=limit,
            generated_at=dependencies.utc_now_iso(),
        )

    payload = _with_cache_mode(fetch_live_f1_panel_payload(dependencies, limit=limit), "live-fallback")
    dependencies.snapshot_store.set(F1_SNAPSHOT_NAMESPACE, cache_key, payload, ttl_seconds)
    setter = dependencies.set_cached_json
    if setter is not None:
        setter(F1_SNAPSHOT_NAMESPACE, cache_key, payload, ttl_seconds)
    return payload
