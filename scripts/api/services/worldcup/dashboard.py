from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from api.services.worldcup.cache import read_cached, refresh_async, store_payload


def get_worldcup_dashboard_snapshot(
    ctx: Dict[str, Any],
    *,
    namespace: str,
    cache_key: str,
    default_ttl_seconds: int,
    build_payload: Callable[..., Dict[str, Any]],
    normalize_payload: Callable[[Dict[str, Any]], Dict[str, Any]],
    has_generated_fallback_artifacts: Callable[[Dict[str, Any]], bool],
    fallback_payload: Callable[[Exception, Optional[Dict[str, Any]]], Dict[str, Any]],
) -> Dict[str, Any]:
    ttl_seconds = max(300, int(getattr(ctx.get("SETTINGS"), "sports_runtime_ttl_seconds", default_ttl_seconds) or default_ttl_seconds))
    cached = read_cached(
        ctx,
        namespace=namespace,
        cache_key=cache_key,
        normalize_payload=normalize_payload,
        has_generated_fallback_artifacts=has_generated_fallback_artifacts,
    )
    if cached:
        cached_odds = cached.get("odds") if isinstance(cached.get("odds"), list) else []
        provider_states = cached.get("providerStates") if isinstance(cached.get("providerStates"), dict) else {}
        if cached.get("cacheMode") == "stale":
            refresh_async(
                ctx,
                namespace=namespace,
                cache_key=cache_key,
                ttl_seconds=ttl_seconds,
                builder=lambda: build_payload(ctx, include_intel=True, include_live_market_links=True),
                has_generated_fallback_artifacts=has_generated_fallback_artifacts,
                stale_payload=cached,
            )
            return {**cached, "status": "stale"}
        if not cached_odds or provider_states.get("odds") in {"deferred", "empty", "source-required"}:
            refresh_async(
                ctx,
                namespace=namespace,
                cache_key=cache_key,
                ttl_seconds=ttl_seconds,
                builder=lambda: build_payload(ctx, include_intel=True, include_live_market_links=True),
                has_generated_fallback_artifacts=has_generated_fallback_artifacts,
                stale_payload=cached,
            )
        return cached
    try:
        payload = build_payload(ctx, include_intel=False, include_live_market_links=False)
        store_payload(ctx, namespace=namespace, cache_key=cache_key, payload=payload, ttl_seconds=ttl_seconds)
        refresh_async(
            ctx,
            namespace=namespace,
            cache_key=cache_key,
            ttl_seconds=ttl_seconds,
            builder=lambda: build_payload(ctx, include_intel=True, include_live_market_links=True),
            has_generated_fallback_artifacts=has_generated_fallback_artifacts,
            stale_payload=payload,
        )
        return payload
    except Exception as exc:
        return fallback_payload(exc, cached)
