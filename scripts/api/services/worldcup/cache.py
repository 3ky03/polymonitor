from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

_REFRESH_LOCK = threading.Lock()
_REFRESHING: set[str] = set()


def read_cached(
    ctx: Dict[str, Any],
    *,
    namespace: str,
    cache_key: str,
    normalize_payload: Callable[[Dict[str, Any]], Dict[str, Any]],
    has_generated_fallback_artifacts: Callable[[Dict[str, Any]], bool],
) -> Optional[Dict[str, Any]]:
    reader = ctx.get("get_cached_json")
    if callable(reader):
        cached = reader(namespace, cache_key)
        if isinstance(cached, dict):
            if has_generated_fallback_artifacts(cached):
                return None
            return {**normalize_payload(cached), "cacheMode": "redis"}
    store = ctx.get("SNAPSHOT_STORE")
    if store is not None:
        cached = store.get(namespace, cache_key)
        if isinstance(cached, dict):
            if has_generated_fallback_artifacts(cached):
                return None
            return {**normalize_payload(cached), "cacheMode": "sqlite"}
        stale = store.get_stale(namespace, cache_key)
        if isinstance(stale, dict):
            if has_generated_fallback_artifacts(stale):
                return None
            return {**normalize_payload(stale), "cacheMode": "stale"}
    return None


def store_payload(ctx: Dict[str, Any], *, namespace: str, cache_key: str, payload: Dict[str, Any], ttl_seconds: int) -> None:
    store = ctx.get("SNAPSHOT_STORE")
    if store is not None:
        store.set(namespace, cache_key, payload, ttl_seconds)
    setter = ctx.get("set_cached_json")
    if callable(setter):
        setter(namespace, cache_key, payload, ttl_seconds)


def log_exception(ctx: Dict[str, Any], message: str, *args: Any) -> None:
    app = ctx.get("app")
    logger = getattr(app, "logger", None)
    if logger is not None:
        logger.exception(message, *args)


def preserve_stale_sections(payload: Dict[str, Any], stale_payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not stale_payload:
        return payload
    next_payload = dict(payload)
    if not next_payload.get("odds") and isinstance(stale_payload.get("odds"), list):
        next_payload["odds"] = stale_payload.get("odds")
        if isinstance(stale_payload.get("marketLinker"), dict):
            next_payload["marketLinker"] = stale_payload.get("marketLinker")
    if not next_payload.get("news") and isinstance(stale_payload.get("news"), list):
        next_payload["news"] = stale_payload.get("news")
    if not next_payload.get("weather") and isinstance(stale_payload.get("weather"), list):
        next_payload["weather"] = stale_payload.get("weather")
    return next_payload


def refresh_async(
    ctx: Dict[str, Any],
    *,
    namespace: str,
    cache_key: str,
    ttl_seconds: int,
    builder: Callable[[], Dict[str, Any]],
    has_generated_fallback_artifacts: Callable[[Dict[str, Any]], bool],
    stale_payload: Optional[Dict[str, Any]] = None,
) -> None:
    refresh_key = f"{namespace}:{cache_key}"
    with _REFRESH_LOCK:
        if refresh_key in _REFRESHING:
            return
        _REFRESHING.add(refresh_key)

    def refresh() -> None:
        try:
            payload = builder()
            if isinstance(payload, dict) and not has_generated_fallback_artifacts(payload):
                payload = preserve_stale_sections(payload, stale_payload)
                store_payload(ctx, namespace=namespace, cache_key=cache_key, payload=payload, ttl_seconds=ttl_seconds)
        except Exception:
            log_exception(ctx, "worldcup-dashboard async refresh failed key=%s", refresh_key)
        finally:
            with _REFRESH_LOCK:
                _REFRESHING.discard(refresh_key)

    thread = threading.Thread(target=refresh, name="worldcup-dashboard-refresh", daemon=True)
    thread.start()
