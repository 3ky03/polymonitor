from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Optional


_SNAPSHOT_REFRESH_LOCK = threading.Lock()
_SNAPSHOT_REFRESHING: set[str] = set()
_SNAPSHOT_REFRESH_SLOTS = threading.BoundedSemaphore(
    max(1, min(int(os.environ.get("POLYDATA_SNAPSHOT_REFRESH_WORKERS", "2")), 8))
)


def get_redis_client(ctx: dict):
    if not ctx["REDIS_URL"] or ctx["redis_module"] is None:
        return None
    existing = ctx["get_redis_client_state"]()
    if existing is not None:
        return existing
    with ctx["_redis_init_lock"]:
        existing = ctx["get_redis_client_state"]()
        if existing is not None:
            return existing
        try:
            client = ctx["redis_module"].from_url(
                ctx["REDIS_URL"],
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
                health_check_interval=30,
            )
            client.ping()
            ctx["set_redis_client_state"](client)
            return client
        except Exception:
            ctx["app"].logger.exception("redis-init failed url=%s", ctx["REDIS_URL"])
            ctx["set_redis_client_state"](None)
            return None


def get_cached_runtime_payload(ctx: dict, namespace: str, cache_key: str) -> Optional[Any]:
    now = time.monotonic()
    composite_key = f"{namespace}:{cache_key}"
    with ctx["_clob_price_cache_lock"]:
        cached = ctx["_clob_price_cache"].get(composite_key)
        if not cached or cached.get("expires_at", 0.0) <= now:
            if cached:
                ctx["_clob_price_cache"].pop(composite_key, None)
            return None
        return cached.get("payload")


def set_cached_runtime_payload(ctx: dict, namespace: str, cache_key: str, payload: Any, ttl_seconds: int) -> Any:
    composite_key = f"{namespace}:{cache_key}"
    with ctx["_clob_price_cache_lock"]:
        ctx["_clob_price_cache"][composite_key] = {
            "payload": payload,
            "expires_at": time.monotonic() + max(1, ttl_seconds),
        }
    return payload


def _redis_key(ctx: dict, namespace: str, cache_key: str) -> str:
    return f"{ctx['REDIS_PREFIX']}{namespace}:{cache_key}"


def get_cached_payload(ctx: dict, namespace: str, cache_key: str) -> Optional[Any]:
    client = get_redis_client(ctx)
    if client is None:
        return None
    try:
        raw = client.get(_redis_key(ctx, namespace, cache_key))
    except Exception:
        ctx["app"].logger.exception("redis-get failed namespace=%s key=%s", namespace, cache_key)
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        ctx["app"].logger.exception("redis-json decode failed namespace=%s key=%s", namespace, cache_key)
        return None


def set_cached_payload(ctx: dict, namespace: str, cache_key: str, payload: Any, ttl_seconds: int) -> None:
    client = get_redis_client(ctx)
    if client is None:
        return
    try:
        client.setex(_redis_key(ctx, namespace, cache_key), ttl_seconds, json.dumps(payload, ensure_ascii=True, default=str))
    except Exception:
        ctx["app"].logger.exception("redis-set failed namespace=%s key=%s ttl=%s", namespace, cache_key, ttl_seconds)


def get_cached_json(ctx: dict, namespace: str, cache_key: str) -> Optional[Dict[str, Any]]:
    payload = get_cached_payload(ctx, namespace, cache_key)
    return payload if isinstance(payload, dict) else None


def set_cached_json(ctx: dict, namespace: str, cache_key: str, payload: Dict[str, Any], ttl_seconds: int) -> None:
    set_cached_payload(ctx, namespace, cache_key, payload, ttl_seconds)


def _store_snapshot_payload(ctx: dict, namespace: str, cache_key: str, payload: Any, ttl_seconds: int) -> None:
    ctx["SNAPSHOT_STORE"].set(namespace, cache_key, payload, ttl_seconds)
    set_cached_payload(ctx, namespace, cache_key, payload, ttl_seconds)


def _refresh_snapshot_payload_async(ctx: dict, namespace: str, cache_key: str, builder, ttl_seconds: int) -> None:
    refresh_key = f"{namespace}:{cache_key}"
    with _SNAPSHOT_REFRESH_LOCK:
        if refresh_key in _SNAPSHOT_REFRESHING:
            return
        if not _SNAPSHOT_REFRESH_SLOTS.acquire(blocking=False):
            ctx["app"].logger.info(
                "snapshot-refresh deferred capacity-full namespace=%s key=%s",
                namespace,
                cache_key,
            )
            return
        _SNAPSHOT_REFRESHING.add(refresh_key)

    def refresh() -> None:
        try:
            payload = builder()
            if isinstance(payload, dict) and isinstance(payload.get("items"), list) and not payload.get("items"):
                ctx["app"].logger.warning("snapshot-refresh skipped empty payload namespace=%s key=%s", namespace, cache_key)
                return
            _store_snapshot_payload(ctx, namespace, cache_key, payload, ttl_seconds)
            ctx["app"].logger.info("snapshot-refresh completed namespace=%s key=%s", namespace, cache_key)
        except Exception:
            ctx["app"].logger.exception("snapshot-refresh failed namespace=%s key=%s", namespace, cache_key)
        finally:
            with _SNAPSHOT_REFRESH_LOCK:
                _SNAPSHOT_REFRESHING.discard(refresh_key)
            _SNAPSHOT_REFRESH_SLOTS.release()

    thread = threading.Thread(target=refresh, name=f"snapshot-refresh:{namespace}", daemon=True)
    thread.start()


def get_snapshot_payload(ctx: dict, namespace: str, cache_key: str, builder, *, ttl_seconds: int) -> Any:
    redis_payload = get_cached_payload(ctx, namespace, cache_key)
    if redis_payload is not None:
        return redis_payload

    sqlite_payload = ctx["SNAPSHOT_STORE"].get(namespace, cache_key)
    if sqlite_payload is not None:
        set_cached_payload(ctx, namespace, cache_key, sqlite_payload, ttl_seconds)
        return sqlite_payload

    stale_payload = ctx["SNAPSHOT_STORE"].get_stale(namespace, cache_key)
    if stale_payload is not None:
        ctx["app"].logger.info("snapshot-cache stale-hit namespace=%s key=%s scheduling_refresh=true", namespace, cache_key)
        set_cached_payload(ctx, namespace, cache_key, stale_payload, min(15, ttl_seconds))
        _refresh_snapshot_payload_async(ctx, namespace, cache_key, builder, ttl_seconds)
        return stale_payload

    try:
        payload = builder()
    except Exception:
        ctx["app"].logger.exception("snapshot-builder failed namespace=%s key=%s", namespace, cache_key)
        if stale_payload is not None:
            set_cached_payload(ctx, namespace, cache_key, stale_payload, ttl_seconds)
            return stale_payload
        raise

    if stale_payload is not None and isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list) and not items:
            set_cached_payload(ctx, namespace, cache_key, stale_payload, ttl_seconds)
            return stale_payload

    _store_snapshot_payload(ctx, namespace, cache_key, payload, ttl_seconds)
    return payload


def get_markets_payload_cached(ctx: dict, cache_key: str, builder, *, namespace: str, ttl_seconds: int) -> Dict[str, Any]:
    local_cache_key = f"{namespace}:{cache_key}"
    redis_payload = get_cached_json(ctx, namespace, cache_key)
    if redis_payload is not None:
        ctx["app"].logger.info("%s-cache redis-hit key=%s", namespace, cache_key)
        return redis_payload

    now_monotonic = time.monotonic()
    cached_entry = ctx["_markets_cache"].get(local_cache_key)
    if cached_entry is not None and cached_entry.get("expires_at", 0.0) > now_monotonic:
        ctx["app"].logger.info(
            "markets-cache hit key=%s ttl_remaining_ms=%.2f",
            cache_key,
            (cached_entry["expires_at"] - now_monotonic) * 1000,
        )
        return cached_entry["value"]

    with ctx["_markets_cache_lock"]:
        cached_entry = ctx["_markets_cache"].get(local_cache_key)
        if cached_entry is not None and cached_entry.get("expires_at", 0.0) > time.monotonic():
            ctx["app"].logger.info("markets-cache hit-after-lock key=%s", cache_key)
            return cached_entry["value"]

        payload = builder()
        ctx["_markets_cache"][local_cache_key] = {
            "value": payload,
            "expires_at": time.monotonic() + ttl_seconds,
        }
        set_cached_json(ctx, namespace, cache_key, payload, ttl_seconds)
        expired_keys = [key for key, value in ctx["_markets_cache"].items() if value.get("expires_at", 0.0) <= time.monotonic()]
        for key in expired_keys:
            ctx["_markets_cache"].pop(key, None)
        return payload


def get_bootstrap_component_cached(ctx: dict, component_key: str, builder, *, ttl_seconds: int) -> Any:
    cache_key = json.dumps({"component": component_key, "v": 1}, sort_keys=True, ensure_ascii=True)
    payload = get_markets_payload_cached(
        ctx,
        cache_key,
        lambda: {"value": builder()},
        namespace="bootstrap:component",
        ttl_seconds=ttl_seconds,
    )
    return payload.get("value")
