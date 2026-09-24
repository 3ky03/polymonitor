from __future__ import annotations

import json
from typing import Any, Dict, List


RUNTIME_NAMESPACE = "runtime:new-market-signals"
SNAPSHOT_NAMESPACE = "snapshot:markets:new-market-signals"
SNAPSHOT_CACHE_KEY = "items-v1"
SNAPSHOT_TTL_SECONDS = 7 * 24 * 60 * 60
PLACEHOLDER_TITLE_PREFIXES = (
    "On-chain recovered market ",
    "Trade indexer placeholder market ",
)


def _redis_key(ctx: dict, suffix: str) -> str:
    return f"{ctx['REDIS_PREFIX']}{RUNTIME_NAMESPACE}:{suffix}"


def normalize_new_market_signals_payload(
    payload: Any,
    *,
    limit: int = 12,
    generated_at: str | None = None,
    cache_mode: str | None = None,
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "items": [],
            "generatedAt": str(generated_at or ""),
            "status": "invalid",
            "cacheMode": cache_mode or "invalid",
            "source": "polyData new-market-signal seed",
            "sources": {},
        }
    items = [item for item in (payload.get("items") or []) if isinstance(item, dict) and not _is_placeholder_title(item.get("title"))]
    limited_items = items[: min(50, max(1, int(limit)))]
    return {
        **payload,
        "items": limited_items,
        "generatedAt": str(payload.get("generatedAt") or generated_at or ""),
        "status": str(payload.get("status") or ("ok" if limited_items else "empty")),
        "cacheMode": str(payload.get("cacheMode") or cache_mode or "seeded"),
        "source": str(payload.get("source") or "polyData new-market-signal seed"),
        "sources": payload.get("sources") if isinstance(payload.get("sources"), dict) else {"database": "ok", "clob": "ok"},
    }


def _coerce_items(raw: Any) -> List[Dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict) and not _is_placeholder_title(item.get("title"))]


def _is_placeholder_title(value: Any) -> bool:
    title = str(value or "").strip()
    if not title:
        return True
    return any(title.startswith(prefix) for prefix in PLACEHOLDER_TITLE_PREFIXES)


def _read_seeded_snapshot(ctx: dict, *, limit: int) -> Dict[str, Any] | None:
    reader = ctx.get("get_cached_json")
    if callable(reader):
        redis_payload = reader(SNAPSHOT_NAMESPACE, SNAPSHOT_CACHE_KEY)
        if isinstance(redis_payload, dict):
            snapshot_store = ctx.get("SNAPSHOT_STORE")
            if snapshot_store is not None:
                snapshot_store.set(SNAPSHOT_NAMESPACE, SNAPSHOT_CACHE_KEY, redis_payload, SNAPSHOT_TTL_SECONDS)
            return normalize_new_market_signals_payload(
                redis_payload,
                limit=limit,
                generated_at=ctx["utc_now_iso"](),
                cache_mode="redis-seed",
            )

    snapshot_store = ctx.get("SNAPSHOT_STORE")
    if snapshot_store is None:
        return None
    sqlite_payload = snapshot_store.get(SNAPSHOT_NAMESPACE, SNAPSHOT_CACHE_KEY)
    if isinstance(sqlite_payload, dict):
        setter = ctx.get("set_cached_json")
        if callable(setter):
            setter(SNAPSHOT_NAMESPACE, SNAPSHOT_CACHE_KEY, sqlite_payload, SNAPSHOT_TTL_SECONDS)
        return normalize_new_market_signals_payload(
            sqlite_payload,
            limit=limit,
            generated_at=ctx["utc_now_iso"](),
            cache_mode="sqlite-seed",
        )
    stale_payload = snapshot_store.get_stale(SNAPSHOT_NAMESPACE, SNAPSHOT_CACHE_KEY)
    if isinstance(stale_payload, dict):
        setter = ctx.get("set_cached_json")
        if callable(setter):
            setter(SNAPSHOT_NAMESPACE, SNAPSHOT_CACHE_KEY, stale_payload, min(60, SNAPSHOT_TTL_SECONDS))
        return normalize_new_market_signals_payload(
            stale_payload,
            limit=limit,
            generated_at=ctx["utc_now_iso"](),
            cache_mode="stale-seed",
        )
    return None


def get_new_market_signals_snapshot(ctx: dict, limit: int = 12) -> Dict[str, Any]:
    limit = min(50, max(1, int(limit)))
    seeded_payload = _read_seeded_snapshot(ctx, limit=limit)
    if seeded_payload is not None:
        return seeded_payload

    client = ctx["get_redis_client"]()
    if client is None:
        return normalize_new_market_signals_payload(
            {"items": [], "status": "degraded", "sources": {"redis": "unavailable"}},
            limit=limit,
            generated_at=ctx["utc_now_iso"](),
            cache_mode="missing",
        )
    try:
        raw = client.get(_redis_key(ctx, "items"))
    except Exception:
        ctx["app"].logger.exception("new-market-signals redis read failed")
        return normalize_new_market_signals_payload(
            {"items": [], "status": "degraded", "sources": {"redis": "error"}},
            limit=limit,
            generated_at=ctx["utc_now_iso"](),
            cache_mode="missing",
        )
    payload = normalize_new_market_signals_payload(
        {
            "items": _coerce_items(raw),
            "generatedAt": ctx["utc_now_iso"](),
            "source": "polyData new-market-signal seed",
            "sources": {"database": "ok", "clob": "ok", "redis": "legacy-runtime"},
        },
        limit=limit,
        generated_at=ctx["utc_now_iso"](),
        cache_mode="runtime-redis",
    )
    snapshot_store = ctx.get("SNAPSHOT_STORE")
    if snapshot_store is not None:
        snapshot_store.set(SNAPSHOT_NAMESPACE, SNAPSHOT_CACHE_KEY, payload, SNAPSHOT_TTL_SECONDS)
    setter = ctx.get("set_cached_json")
    if callable(setter):
        setter(SNAPSHOT_NAMESPACE, SNAPSHOT_CACHE_KEY, payload, SNAPSHOT_TTL_SECONDS)
    return payload
