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
from jin10.flash_client import fetch_jin10_panel_payload


JIN10_SNAPSHOT_NAMESPACE = "snapshot:macro:jin10"
JIN10_SELECTION_VERSION = 2


@dataclass(frozen=True)
class Jin10RuntimeDependencies:
    settings: Any
    requests_lib: Any
    utc_now_iso: Callable[..., Any]
    snapshot_store: Any
    get_cached_json: Callable[..., Any]
    set_cached_json: Callable[..., Any] | None
    signal_runtime_ttl_seconds: int | None

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> Jin10RuntimeDependencies:
        return cls(
            settings=resolve_service_value(context, "SETTINGS"),
            requests_lib=resolve_optional_service_value(
                context,
                "requests",
            ),
            utc_now_iso=resolve_service_callable(
                context,
                "utc_now_iso",
            ),
            snapshot_store=resolve_service_value(
                context,
                "SNAPSHOT_STORE",
            ),
            get_cached_json=resolve_service_callable(
                context,
                "get_cached_json",
            ),
            set_cached_json=resolve_optional_service_callable(
                context,
                "set_cached_json",
            ),
            signal_runtime_ttl_seconds=resolve_service_value(
                context,
                "SIGNAL_RUNTIME_TTL_SECONDS",
            ),
        )


Jin10RuntimeContext = Mapping[str, Any] | Jin10RuntimeDependencies


def _dependencies(
    context: Jin10RuntimeContext,
) -> Jin10RuntimeDependencies:
    if isinstance(context, Jin10RuntimeDependencies):
        return context
    return Jin10RuntimeDependencies.from_context(context)


def build_jin10_cache_key(settings: Any, limit: int = 24) -> str:
    return json.dumps(
        {
            "limit": limit,
            "apiUrl": settings.jin10_flash_api_url,
            "channel": settings.jin10_flash_channel,
            "selectionVersion": JIN10_SELECTION_VERSION,
        },
        sort_keys=True,
        ensure_ascii=True,
    )


def normalize_jin10_panel_payload(payload: Any, *, settings: Any, limit: int = 24, generated_at: str | None = None) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "generatedAt": str(generated_at or ""),
            "source": "jin10-flash",
            "sourceUrl": settings.jin10_live_url,
            "status": "invalid",
            "items": [],
        }
    return {
        **payload,
        "generatedAt": str(payload.get("generatedAt") or generated_at or ""),
        "source": str(payload.get("source") or "jin10-flash"),
        "sourceUrl": str(payload.get("sourceUrl") or settings.jin10_live_url),
        "status": str(payload.get("status") or "ok"),
        "items": [item for item in (payload.get("items") or []) if isinstance(item, dict)][:limit],
    }


def fetch_live_jin10_panel_payload(
    ctx: Jin10RuntimeContext,
    limit: int = 24,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    payload = fetch_jin10_panel_payload(
        limit=limit,
        api_url=dependencies.settings.jin10_flash_api_url,
        channel=dependencies.settings.jin10_flash_channel,
        app_id=dependencies.settings.jin10_flash_app_id,
        version=dependencies.settings.jin10_flash_version,
        detail_base_url=dependencies.settings.jin10_flash_detail_base_url,
        live_url=dependencies.settings.jin10_live_url,
        requests_lib=dependencies.requests_lib,
    )
    return normalize_jin10_panel_payload(
        payload,
        settings=dependencies.settings,
        limit=limit,
        generated_at=dependencies.utc_now_iso(),
    )


def _with_cache_mode(payload: Dict[str, Any], cache_mode: str) -> Dict[str, Any]:
    return {**payload, "cacheMode": str(payload.get("cacheMode") or cache_mode)}


def _read_seeded_snapshot(
    dependencies: Jin10RuntimeDependencies,
    *,
    cache_key: str,
    ttl_seconds: int,
) -> Dict[str, Any] | None:
    redis_payload = dependencies.get_cached_json(
        JIN10_SNAPSHOT_NAMESPACE,
        cache_key,
    )
    if isinstance(redis_payload, dict):
        dependencies.snapshot_store.set(
            JIN10_SNAPSHOT_NAMESPACE,
            cache_key,
            redis_payload,
            ttl_seconds,
        )
        return _with_cache_mode(redis_payload, "redis-seed")

    sqlite_payload = dependencies.snapshot_store.get(
        JIN10_SNAPSHOT_NAMESPACE,
        cache_key,
    )
    if isinstance(sqlite_payload, dict):
        if dependencies.set_cached_json is not None:
            dependencies.set_cached_json(
                JIN10_SNAPSHOT_NAMESPACE,
                cache_key,
                sqlite_payload,
                ttl_seconds,
            )
        return _with_cache_mode(sqlite_payload, "sqlite-seed")

    stale_payload = dependencies.snapshot_store.get_stale(
        JIN10_SNAPSHOT_NAMESPACE,
        cache_key,
    )
    if isinstance(stale_payload, dict):
        if dependencies.set_cached_json is not None:
            dependencies.set_cached_json(
                JIN10_SNAPSHOT_NAMESPACE,
                cache_key,
                stale_payload,
                min(15, ttl_seconds),
            )
        return _with_cache_mode(stale_payload, "stale-seed")
    return None


def get_jin10_panel_snapshot(
    ctx: Jin10RuntimeContext,
    limit: int = 24,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    ttl_seconds = max(
        15,
        int(dependencies.signal_runtime_ttl_seconds),
    )
    cache_key = build_jin10_cache_key(
        dependencies.settings,
        limit=limit,
    )
    seeded_payload = _read_seeded_snapshot(
        dependencies,
        cache_key=cache_key,
        ttl_seconds=ttl_seconds,
    )
    if seeded_payload is None and int(limit or 0) != 24:
        default_cache_key = build_jin10_cache_key(
            dependencies.settings,
            limit=24,
        )
        seeded_payload = _read_seeded_snapshot(
            dependencies,
            cache_key=default_cache_key,
            ttl_seconds=ttl_seconds,
        )
    if seeded_payload is not None:
        return normalize_jin10_panel_payload(
            seeded_payload,
            settings=dependencies.settings,
            limit=limit,
            generated_at=dependencies.utc_now_iso(),
        )

    payload = fetch_live_jin10_panel_payload(
        dependencies,
        limit=limit,
    )
    payload = _with_cache_mode(payload, "live-fallback")
    dependencies.snapshot_store.set(
        JIN10_SNAPSHOT_NAMESPACE,
        cache_key,
        payload,
        ttl_seconds,
    )
    if dependencies.set_cached_json is not None:
        dependencies.set_cached_json(
            JIN10_SNAPSHOT_NAMESPACE,
            cache_key,
            payload,
            ttl_seconds,
        )
    return payload
