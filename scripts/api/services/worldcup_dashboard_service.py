from __future__ import annotations

from typing import Any, Dict, Optional

from api.services.worldcup.builder import (
    build_worldcup_core_payload,
    build_worldcup_dashboard_payload,
    build_worldcup_live_payload,
    merge_worldcup_core_live_payload,
)
from api.services.worldcup.cache import read_cached, store_payload
from api.services.worldcup.payload import (
    fallback_worldcup_dashboard_payload,
    has_generated_fallback_artifacts,
    normalize_payload,
)
from api.services.worldcup.panels import (
    WORLDCUP_PANEL_NAMESPACE,
    build_worldcup_panel_payloads,
    panel_ttl_seconds,
    store_worldcup_panel_payloads,
)
from api.services.worldcup.schedule import OPENFOOTBALL_2026_URL, WORLD_CUP_CITIES

WORLDCUP_DASHBOARD_NAMESPACE = "snapshot:sports:worldcup-dashboard"
WORLDCUP_DASHBOARD_CACHE_KEY = "dashboard-v1"
WORLDCUP_CORE_NAMESPACE = "snapshot:sports:worldcup-core"
WORLDCUP_CORE_CACHE_KEY = "core-v1"
WORLDCUP_LIVE_NAMESPACE = "snapshot:sports:worldcup-live"
WORLDCUP_LIVE_CACHE_KEY = "live-v1"
DEFAULT_TTL_SECONDS = 900
DEFAULT_CORE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_LIVE_TTL_SECONDS = 5 * 60

# Backward-compatible aliases for watcher/API callers that imported old private helpers.
_normalize_payload = normalize_payload
_has_generated_fallback_artifacts = has_generated_fallback_artifacts
_fallback_worldcup_dashboard_payload = fallback_worldcup_dashboard_payload


def _settings_int(ctx: Dict[str, Any], name: str, default: int) -> int:
    settings = ctx.get("SETTINGS")
    try:
        value = int(getattr(settings, name, default) or default)
    except (TypeError, ValueError):
        value = default
    return max(1, value)


def core_ttl_seconds(ctx: Dict[str, Any]) -> int:
    return _settings_int(ctx, "worldcup_core_seed_ttl_seconds", DEFAULT_CORE_TTL_SECONDS)


def live_ttl_seconds(ctx: Dict[str, Any]) -> int:
    return _settings_int(ctx, "worldcup_live_seed_ttl_seconds", DEFAULT_LIVE_TTL_SECONDS)


def dashboard_ttl_seconds(ctx: Dict[str, Any]) -> int:
    return live_ttl_seconds(ctx)


def _read_cached_payload(ctx: Dict[str, Any], *, namespace: str, cache_key: str) -> Optional[Dict[str, Any]]:
    return read_cached(
        ctx,
        namespace=namespace,
        cache_key=cache_key,
        normalize_payload=normalize_payload,
        has_generated_fallback_artifacts=has_generated_fallback_artifacts,
    )


def store_worldcup_core_snapshot(ctx: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = {**normalize_payload(payload), "cacheMode": "remote"}
    store_payload(
        ctx,
        namespace=WORLDCUP_CORE_NAMESPACE,
        cache_key=WORLDCUP_CORE_CACHE_KEY,
        payload=normalized,
        ttl_seconds=core_ttl_seconds(ctx),
    )
    return normalized


def store_worldcup_live_snapshot(ctx: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = {**normalize_payload(payload), "cacheMode": "remote"}
    store_payload(
        ctx,
        namespace=WORLDCUP_LIVE_NAMESPACE,
        cache_key=WORLDCUP_LIVE_CACHE_KEY,
        payload=normalized,
        ttl_seconds=live_ttl_seconds(ctx),
    )
    return normalized


def store_worldcup_dashboard_snapshot(ctx: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_payload(payload)
    store_payload(
        ctx,
        namespace=WORLDCUP_DASHBOARD_NAMESPACE,
        cache_key=WORLDCUP_DASHBOARD_CACHE_KEY,
        payload=normalized,
        ttl_seconds=dashboard_ttl_seconds(ctx),
    )
    store_worldcup_panel_payloads(
        ctx,
        normalized,
        core_ttl=core_ttl_seconds(ctx),
        live_ttl=live_ttl_seconds(ctx),
    )
    return normalized


def get_worldcup_core_snapshot(ctx: Dict[str, Any]) -> Dict[str, Any]:
    cached = _read_cached_payload(ctx, namespace=WORLDCUP_CORE_NAMESPACE, cache_key=WORLDCUP_CORE_CACHE_KEY)
    if cached:
        return cached
    payload = build_worldcup_core_payload(ctx, include_live_market_links=True)
    return store_worldcup_core_snapshot(ctx, payload)


def get_worldcup_live_snapshot(ctx: Dict[str, Any]) -> Dict[str, Any]:
    cached = _read_cached_payload(ctx, namespace=WORLDCUP_LIVE_NAMESPACE, cache_key=WORLDCUP_LIVE_CACHE_KEY)
    if cached:
        return cached
    payload = build_worldcup_live_payload(ctx, include_intel=True)
    return store_worldcup_live_snapshot(ctx, payload)


def get_worldcup_dashboard_snapshot(ctx: Dict[str, Any]) -> Dict[str, Any]:
    try:
        core = get_worldcup_core_snapshot(ctx)
        live = get_worldcup_live_snapshot(ctx)
        payload = merge_worldcup_core_live_payload(core, live)
        cache_modes = {str(core.get("cacheMode") or ""), str(live.get("cacheMode") or "")}
        cache_mode = "redis" if cache_modes == {"redis"} else "sqlite" if cache_modes == {"sqlite"} else "remote"
        return store_worldcup_dashboard_snapshot(ctx, {**payload, "cacheMode": cache_mode})
    except Exception as exc:
        cached = _read_cached_payload(ctx, namespace=WORLDCUP_DASHBOARD_NAMESPACE, cache_key=WORLDCUP_DASHBOARD_CACHE_KEY)
        return fallback_worldcup_dashboard_payload(exc, cached)


def get_worldcup_panel_snapshot(ctx: Dict[str, Any], panel_id: str) -> Dict[str, Any]:
    panel_key = str(panel_id or "").strip()
    if not panel_key:
        return {"status": "error", "error": "missing-panel-id"}
    store = ctx.get("SNAPSHOT_STORE")
    cached = None
    reader = ctx.get("get_cached_json")
    if callable(reader):
        raw = reader(WORLDCUP_PANEL_NAMESPACE, panel_key)
        if isinstance(raw, dict):
            cached = raw
    if cached is None and store is not None:
        raw = store.get(WORLDCUP_PANEL_NAMESPACE, panel_key)
        if isinstance(raw, dict):
            cached = raw
    if isinstance(cached, dict):
        return {**cached, "cacheMode": "redis" if cached.get("cacheMode") == "seeded" else str(cached.get("cacheMode") or "redis")}

    dashboard = get_worldcup_dashboard_snapshot(ctx)
    panels = build_worldcup_panel_payloads(dashboard)
    payload = panels.get(panel_key)
    if not payload:
        return {"status": "not-found", "panelId": panel_key, "available": sorted(panels)}
    store_payload(
        ctx,
        namespace=WORLDCUP_PANEL_NAMESPACE,
        cache_key=panel_key,
        payload=payload,
        ttl_seconds=panel_ttl_seconds(panel_key, core_ttl_seconds(ctx), live_ttl_seconds(ctx)),
    )
    return payload
