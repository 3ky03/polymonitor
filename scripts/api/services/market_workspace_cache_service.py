from __future__ import annotations

import copy
import json
import os
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from api import cache as api_cache
from api.context import resolve_service_callable, resolve_service_value
from api.services import lob_service, market_service


DETAIL_TTL_SECONDS = int(os.environ.get("POLYDATA_MARKET_WORKSPACE_DETAIL_TTL_SECONDS", "120"))
CHART_TTL_SECONDS = int(os.environ.get("POLYDATA_MARKET_WORKSPACE_CHART_TTL_SECONDS", "90"))
ORDERBOOK_TTL_SECONDS = int(os.environ.get("POLYDATA_MARKET_WORKSPACE_ORDERBOOK_TTL_SECONDS", "60"))
FLOW_TTL_SECONDS = int(os.environ.get("POLYDATA_MARKET_WORKSPACE_FLOW_TTL_SECONDS", "8"))

_REFRESH_LOCK = threading.Lock()
_REFRESHING: set[str] = set()
_REFRESH_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(1, min(int(os.environ.get("POLYDATA_MARKET_FOCUS_REFRESH_WORKERS", "2")), 12)),
    thread_name_prefix="market-focus-refresh",
)


@dataclass(frozen=True)
class MarketWorkspaceCacheDependencies:
    source: Mapping[str, Any]
    application: Any
    snapshot_store: Any
    utc_now_iso: Callable[..., str]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> MarketWorkspaceCacheDependencies:
        return cls(
            source=context,
            application=resolve_service_value(context, "app"),
            snapshot_store=resolve_service_value(context, "SNAPSHOT_STORE"),
            utc_now_iso=resolve_service_callable(context, "utc_now_iso"),
        )


MarketWorkspaceCacheContext = (
    Mapping[str, Any] | MarketWorkspaceCacheDependencies
)


def _dependencies(
    context: MarketWorkspaceCacheContext,
) -> MarketWorkspaceCacheDependencies:
    if isinstance(context, MarketWorkspaceCacheDependencies):
        return context
    return MarketWorkspaceCacheDependencies.from_context(context)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _cache_key(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _namespace(layer: str) -> str:
    return f"snapshot:market-workspace:{layer}"


def _copy_payload(payload: Any) -> Any:
    try:
        return copy.deepcopy(payload)
    except Exception:
        return json.loads(json.dumps(payload, default=str))


def _layer_meta(layer: str, mode: str, cache_key: str) -> Dict[str, Any]:
    return {
        "layer": layer,
        "mode": mode,
        "cacheKey": cache_key,
        "generatedAt": _utc_now_iso(),
    }


def _with_cache_meta(payload: Any, layer: str, mode: str, cache_key: str) -> Any:
    copied = _copy_payload(payload)
    if isinstance(copied, dict):
        copied["cacheMode"] = mode
        copied["marketWorkspaceCache"] = _layer_meta(layer, mode, cache_key)
    return copied


def _book_side_has_levels(side: Any) -> bool:
    return isinstance(side, dict) and bool(side.get("bids") or side.get("asks"))


def _lob_has_levels(payload: Any) -> bool:
    return isinstance(payload, dict) and (_book_side_has_levels(payload.get("yes")) or _book_side_has_levels(payload.get("no")))


def _chart_has_points(payload: Any) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("points"), list) and bool(payload.get("points"))


def _flow_has_rows(payload: Any) -> bool:
    if isinstance(payload, dict):
        rows = payload.get("items")
    else:
        rows = payload
    return isinstance(rows, list) and bool(rows)


def _detail_is_usable(payload: Any) -> bool:
    return isinstance(payload, dict) and not payload.get("_status") and isinstance(payload.get("market"), dict)


def _is_empty_replacement(layer: str, payload: Any) -> bool:
    if layer == "detail":
        return not _detail_is_usable(payload)
    if layer == "chart":
        return not _chart_has_points(payload)
    if layer == "orderbook":
        return not _lob_has_levels(payload)
    if layer == "flow":
        return not _flow_has_rows(payload)
    return payload in (None, {}, [])


def _read_redis(
    context: MarketWorkspaceCacheContext,
    namespace: str,
    cache_key: str,
) -> Optional[Any]:
    dependencies = _dependencies(context)
    try:
        return api_cache.get_cached_payload(
            dependencies.source,
            namespace,
            cache_key,
        )
    except Exception:
        dependencies.application.logger.exception(
            "market-workspace-cache redis-read failed namespace=%s key=%s",
            namespace,
            cache_key,
        )
        return None


def _write_cache(
    context: MarketWorkspaceCacheContext,
    namespace: str,
    cache_key: str,
    payload: Any,
    ttl_seconds: int,
) -> None:
    dependencies = _dependencies(context)
    try:
        api_cache.set_cached_runtime_payload(
            dependencies.source,
            namespace,
            cache_key,
            payload,
            ttl_seconds,
        )
    except Exception:
        dependencies.application.logger.exception(
            "market-workspace-cache memory-write failed namespace=%s key=%s",
            namespace,
            cache_key,
        )
    try:
        api_cache.set_cached_payload(
            dependencies.source,
            namespace,
            cache_key,
            payload,
            ttl_seconds,
        )
    except Exception:
        dependencies.application.logger.exception(
            "market-workspace-cache redis-write failed namespace=%s key=%s",
            namespace,
            cache_key,
        )
    try:
        dependencies.snapshot_store.set(
            namespace,
            cache_key,
            payload,
            ttl_seconds,
        )
    except Exception:
        dependencies.application.logger.exception(
            "market-workspace-cache sqlite-write failed namespace=%s key=%s",
            namespace,
            cache_key,
        )


def _refresh_async(
    context: MarketWorkspaceCacheContext,
    *,
    layer: str,
    namespace: str,
    cache_key: str,
    builder: Callable[[], Any],
    ttl_seconds: int,
    stale_payload: Any,
) -> None:
    dependencies = _dependencies(context)
    refresh_key = f"{namespace}:{cache_key}"
    with _REFRESH_LOCK:
        if refresh_key in _REFRESHING:
            return
        _REFRESHING.add(refresh_key)

    def refresh() -> None:
        try:
            payload = builder()
            if _is_empty_replacement(layer, payload) and not _is_empty_replacement(layer, stale_payload):
                dependencies.application.logger.warning(
                    "market-workspace-cache refresh skipped empty layer=%s key=%s",
                    layer,
                    cache_key,
                )
                return
            _write_cache(
                dependencies,
                namespace,
                cache_key,
                _with_cache_meta(payload, layer, "refresh", cache_key),
                ttl_seconds,
            )
        except Exception:
            dependencies.application.logger.exception(
                "market-workspace-cache refresh failed layer=%s key=%s",
                layer,
                cache_key,
            )
        finally:
            with _REFRESH_LOCK:
                _REFRESHING.discard(refresh_key)

    _REFRESH_EXECUTOR.submit(refresh)


def _cached_layer(
    context: MarketWorkspaceCacheContext,
    *,
    layer: str,
    cache_key: str,
    ttl_seconds: int,
    builder: Callable[[], Any],
    allow_stale_refresh: bool = True,
) -> Dict[str, Any]:
    dependencies = _dependencies(context)
    namespace = _namespace(layer)

    runtime_payload = api_cache.get_cached_runtime_payload(
        dependencies.source,
        namespace,
        cache_key,
    )
    if runtime_payload is not None:
        return {"payload": _with_cache_meta(runtime_payload, layer, "memory-hit", cache_key), "mode": "memory-hit"}

    redis_payload = _read_redis(dependencies, namespace, cache_key)
    if redis_payload is not None:
        api_cache.set_cached_runtime_payload(
            dependencies.source,
            namespace,
            cache_key,
            redis_payload,
            min(ttl_seconds, 30),
        )
        return {"payload": _with_cache_meta(redis_payload, layer, "redis-hit", cache_key), "mode": "redis-hit"}

    sqlite_payload = dependencies.snapshot_store.get(namespace, cache_key)
    if sqlite_payload is not None:
        _write_cache(
            dependencies,
            namespace,
            cache_key,
            sqlite_payload,
            ttl_seconds,
        )
        return {"payload": _with_cache_meta(sqlite_payload, layer, "sqlite-hit", cache_key), "mode": "sqlite-hit"}

    stale_payload = dependencies.snapshot_store.get_stale(
        namespace,
        cache_key,
    )
    if stale_payload is not None:
        dependencies.application.logger.info(
            "market-workspace-cache stale-hit layer=%s key=%s",
            layer,
            cache_key,
        )
        api_cache.set_cached_payload(
            dependencies.source,
            namespace,
            cache_key,
            stale_payload,
            min(15, ttl_seconds),
        )
        if allow_stale_refresh:
            _refresh_async(
                dependencies,
                layer=layer,
                namespace=namespace,
                cache_key=cache_key,
                builder=builder,
                ttl_seconds=ttl_seconds,
                stale_payload=stale_payload,
            )
        return {"payload": _with_cache_meta(stale_payload, layer, "stale-hit", cache_key), "mode": "stale-hit"}

    refresh_key = f"{namespace}:{cache_key}"
    with _REFRESH_LOCK:
        refresh_in_flight = refresh_key in _REFRESHING
    if refresh_in_flight:
        payload = _fallback_payload(layer, cache_key)
        return {"payload": _with_cache_meta(payload, layer, "warming", cache_key), "mode": "warming"}

    try:
        payload = builder()
    except Exception:
        dependencies.application.logger.exception(
            "market-workspace-cache live-build failed layer=%s key=%s",
            layer,
            cache_key,
        )
        payload = _fallback_payload(layer, cache_key)
        return {"payload": _with_cache_meta(payload, layer, "live-error", cache_key), "mode": "live-error"}

    mode = "live-build"
    wrapped = _with_cache_meta(payload, layer, mode, cache_key)
    ttl = ttl_seconds if not _is_empty_replacement(layer, payload) else min(ttl_seconds, 10)
    _write_cache(dependencies, namespace, cache_key, wrapped, ttl)
    return {"payload": _copy_payload(wrapped), "mode": mode}


def _cached_layer_read_only(
    context: MarketWorkspaceCacheContext,
    *,
    layer: str,
    cache_key: str,
    ttl_seconds: int,
    builder: Callable[[], Any],
) -> Dict[str, Any]:
    """Return the best cached layer without making the request wait for a live build."""
    dependencies = _dependencies(context)
    namespace = _namespace(layer)

    runtime_payload = api_cache.get_cached_runtime_payload(
        dependencies.source,
        namespace,
        cache_key,
    )
    if runtime_payload is not None:
        return {"payload": _with_cache_meta(runtime_payload, layer, "memory-hit", cache_key), "mode": "memory-hit"}

    redis_payload = _read_redis(dependencies, namespace, cache_key)
    if redis_payload is not None:
        api_cache.set_cached_runtime_payload(
            dependencies.source,
            namespace,
            cache_key,
            redis_payload,
            min(ttl_seconds, 30),
        )
        return {"payload": _with_cache_meta(redis_payload, layer, "redis-hit", cache_key), "mode": "redis-hit"}

    sqlite_payload = dependencies.snapshot_store.get(namespace, cache_key)
    if sqlite_payload is not None:
        _write_cache(dependencies, namespace, cache_key, sqlite_payload, ttl_seconds)
        return {"payload": _with_cache_meta(sqlite_payload, layer, "sqlite-hit", cache_key), "mode": "sqlite-hit"}

    stale_payload = dependencies.snapshot_store.get_stale(namespace, cache_key)
    if stale_payload is not None:
        _refresh_async(
            dependencies,
            layer=layer,
            namespace=namespace,
            cache_key=cache_key,
            builder=builder,
            ttl_seconds=ttl_seconds,
            stale_payload=stale_payload,
        )
        return {"payload": _with_cache_meta(stale_payload, layer, "stale-hit", cache_key), "mode": "stale-hit"}

    fallback = _fallback_payload(layer, cache_key)
    _refresh_async(
        dependencies,
        layer=layer,
        namespace=namespace,
        cache_key=cache_key,
        builder=builder,
        ttl_seconds=ttl_seconds,
        stale_payload=fallback,
    )
    return {"payload": _with_cache_meta(fallback, layer, "warming", cache_key), "mode": "warming"}


def _fallback_payload(layer: str, cache_key: str) -> Any:
    if layer == "flow":
        return {"items": [], "status": "warming"}
    if layer == "chart":
        return {"points": [], "historyStatus": "warming"}
    if layer == "orderbook":
        return {
            "bookStatus": "warming",
            "source": "market-workspace-cache",
            "yes": {"bids": [], "asks": [], "bestBid": None, "bestAsk": None, "spread": None},
            "no": {"bids": [], "asks": [], "bestBid": None, "bestAsk": None, "spread": None},
        }
    return {"status": "warming", "cacheKey": cache_key}


def _truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in {"", "0", "false", "none", "null"}


def _market_is_closed(
    context: MarketWorkspaceCacheContext,
    market_id: int,
) -> bool:
    dependencies = _dependencies(context)
    resolver = dependencies.source.get("get_market_by_id")
    if not callable(resolver):
        return False
    try:
        market = resolver(int(market_id))
    except Exception:
        dependencies.application.logger.exception(
            "market-workspace-cache market-status lookup failed market_id=%s",
            market_id,
        )
        return False
    if not isinstance(market, Mapping):
        return False
    if any(
        _truthy_flag(market.get(key))
        for key in ("is_trading_closed", "gamma_closed", "is_final")
    ):
        return True
    statuses = " ".join(
        str(market.get(key) or "").strip().lower().replace("_", "-")
        for key in ("status", "completion_status")
    )
    return any(
        token in statuses
        for token in ("closed", "settled", "resolved", "final", "cancelled", "expired", "awaiting-oracle", "awaiting oracle")
    )


def _closed_orderbook_result(
    context: MarketWorkspaceCacheContext,
    *,
    market_id: int,
    cache_key: str,
) -> Dict[str, Any]:
    dependencies = _dependencies(context)
    namespace = _namespace("orderbook")
    payload = _fallback_payload("orderbook", cache_key)
    payload.update(
        {
            "marketId": int(market_id),
            "localMarketId": int(market_id),
            "bookStatus": "closed",
            "source": "market-lifecycle",
            "fallbackReason": "Trading is closed; live CLOB levels are not applicable.",
        }
    )
    wrapped = _with_cache_meta(payload, "orderbook", "closed", cache_key)
    _write_cache(
        dependencies,
        namespace,
        cache_key,
        wrapped,
        ORDERBOOK_TTL_SECONDS,
    )
    return {"payload": _copy_payload(wrapped), "mode": "closed"}


def _build_detail(
    context: MarketWorkspaceCacheContext,
    market_id: int,
) -> Dict[str, Any]:
    dependencies = _dependencies(context)
    payload = market_service.get_market_workspace_payload(
        dependencies.source,
        market_id,
    )
    if not isinstance(payload, dict):
        return {"error": "Invalid market workspace payload", "marketId": market_id, "_status": 502}
    detail = dict(payload)
    detail.pop("chart", None)
    detail.pop("trades", None)
    detail.pop("lob", None)
    detail["generatedAt"] = dependencies.utc_now_iso()
    return detail


def get_market_detail_payload(
    context: MarketWorkspaceCacheContext,
    market_id: int,
) -> Dict[str, Any]:
    dependencies = _dependencies(context)
    key = _cache_key({"marketId": int(market_id), "layer": "detail", "v": 3})
    result = _cached_layer(
        dependencies,
        layer="detail",
        cache_key=key,
        ttl_seconds=DETAIL_TTL_SECONDS,
        builder=lambda: _build_detail(dependencies, market_id),
    )
    payload = result["payload"]
    return payload if isinstance(payload, dict) else {"error": "Invalid detail cache payload", "marketId": market_id, "_status": 502}


def get_market_chart_payload(
    context: MarketWorkspaceCacheContext,
    market_id: int,
    *,
    range_name: str = "1d",
    interval: str = "5m",
) -> Dict[str, Any]:
    dependencies = _dependencies(context)
    normalized_range = str(range_name or "1d").strip().lower()
    normalized_interval = str(interval or "5m").strip().lower()
    key = _cache_key(
        {
            "marketId": int(market_id),
            "layer": "chart",
            "range": normalized_range,
            "interval": normalized_interval,
            "v": 5,
        }
    )
    result = _cached_layer(
        dependencies,
        layer="chart",
        cache_key=key,
        ttl_seconds=CHART_TTL_SECONDS,
        builder=lambda: market_service.get_market_chart_payload(
            dependencies.source,
            market_id,
            range_name=normalized_range,
            interval=normalized_interval,
        ),
    )
    payload = result["payload"]
    if isinstance(payload, dict):
        payload.setdefault("marketId", market_id)
        payload.setdefault("localMarketId", market_id)
        payload.setdefault("range", normalized_range)
        payload.setdefault("interval", normalized_interval)
        return payload
    return {"marketId": market_id, "localMarketId": market_id, "range": normalized_range, "interval": normalized_interval, "points": []}


def get_market_flow_payload(
    context: MarketWorkspaceCacheContext,
    market_id: int,
    *,
    limit: int = 24,
    offset: int = 0,
) -> Dict[str, Any]:
    dependencies = _dependencies(context)
    safe_limit = min(max(int(limit), 1), 500)
    safe_offset = max(int(offset), 0)
    key = _cache_key(
        {
            "marketId": int(market_id),
            "layer": "flow",
            "limit": safe_limit,
            "offset": safe_offset,
            "v": 1,
        }
    )

    def build() -> Dict[str, Any]:
        return {
            "marketId": market_id,
            "localMarketId": market_id,
            "items": market_service.get_trades_by_market_id(
                dependencies.source,
                market_id,
                limit=safe_limit,
                offset=safe_offset,
            ),
            "generatedAt": dependencies.utc_now_iso(),
        }

    result = _cached_layer(
        dependencies,
        layer="flow",
        cache_key=key,
        ttl_seconds=FLOW_TTL_SECONDS,
        builder=build,
    )
    payload = result["payload"]
    if isinstance(payload, dict):
        payload.setdefault("marketId", market_id)
        payload.setdefault("localMarketId", market_id)
        payload.setdefault("items", [])
        return payload
    return {"marketId": market_id, "localMarketId": market_id, "items": []}


def get_market_flow_rows(
    context: MarketWorkspaceCacheContext,
    market_id: int,
    *,
    limit: int = 24,
    offset: int = 0,
) -> list[Dict[str, Any]]:
    payload = get_market_flow_payload(
        context,
        market_id,
        limit=limit,
        offset=offset,
    )
    rows = payload.get("items") if isinstance(payload, dict) else []
    return rows if isinstance(rows, list) else []


def get_market_orderbook_payload(
    context: MarketWorkspaceCacheContext,
    market_id: int,
) -> Dict[str, Any]:
    dependencies = _dependencies(context)
    key = _cache_key({"marketId": int(market_id), "layer": "orderbook", "v": 1})
    result = (
        _closed_orderbook_result(
            dependencies,
            market_id=market_id,
            cache_key=key,
        )
        if _market_is_closed(dependencies, market_id)
        else _cached_layer(
            dependencies,
            layer="orderbook",
            cache_key=key,
            ttl_seconds=ORDERBOOK_TTL_SECONDS,
            builder=lambda: lob_service.get_runtime_lob_payload(
                dependencies.source,
                market_id,
            ),
        )
    )
    payload = result["payload"]
    if isinstance(payload, dict):
        payload.setdefault("marketId", market_id)
        payload.setdefault("localMarketId", market_id)
        return payload
    fallback = _fallback_payload("orderbook", key)
    fallback["marketId"] = market_id
    fallback["localMarketId"] = market_id
    return fallback


def get_market_workspace_payload(
    context: MarketWorkspaceCacheContext,
    market_id: int,
) -> Dict[str, Any]:
    dependencies = _dependencies(context)
    detail_result = get_market_detail_payload(dependencies, market_id)
    if detail_result.get("_status") == 404:
        return detail_result

    with ThreadPoolExecutor(max_workers=3) as executor:
        chart_future = executor.submit(
            get_market_chart_payload,
            dependencies,
            market_id,
            range_name="1d",
            interval="5m",
        )
        flow_future = executor.submit(
            get_market_flow_payload,
            dependencies,
            market_id,
            limit=24,
            offset=0,
        )
        orderbook_future = executor.submit(
            get_market_orderbook_payload,
            dependencies,
            market_id,
        )
        chart = chart_future.result()
        flow = flow_future.result()
        orderbook = orderbook_future.result()

    payload = dict(detail_result)
    payload["chart"] = chart
    payload["trades"] = flow.get("items") if isinstance(flow, dict) else []
    payload["lob"] = orderbook
    payload["cacheLayers"] = {
        "detail": (detail_result.get("marketWorkspaceCache") or {}),
        "chart": (chart.get("marketWorkspaceCache") or {}),
        "flow": (flow.get("marketWorkspaceCache") or {}),
        "orderbook": (orderbook.get("marketWorkspaceCache") or {}),
    }
    payload["marketWorkspaceCache"] = {
        "mode": "layered",
        "layers": payload["cacheLayers"],
        "generatedAt": _utc_now_iso(),
    }
    payload["generatedAt"] = _utc_now_iso()
    return payload


def get_market_focus_tile_payload(
    context: MarketWorkspaceCacheContext,
    market_id: int,
) -> Dict[str, Any]:
    """Serve the selection-critical detail, chart and LOB without cold-path blocking."""
    dependencies = _dependencies(context)
    detail_key = _cache_key({"marketId": int(market_id), "layer": "detail", "v": 3})
    chart_key = _cache_key(
        {
            "marketId": int(market_id),
            "layer": "chart",
            "range": "1d",
            "interval": "5m",
            "v": 5,
        }
    )
    orderbook_key = _cache_key({"marketId": int(market_id), "layer": "orderbook", "v": 1})

    detail_result = _cached_layer_read_only(
        dependencies,
        layer="detail",
        cache_key=detail_key,
        ttl_seconds=DETAIL_TTL_SECONDS,
        builder=lambda: _build_detail(dependencies, market_id),
    )
    chart_result = _cached_layer_read_only(
        dependencies,
        layer="chart",
        cache_key=chart_key,
        ttl_seconds=CHART_TTL_SECONDS,
        builder=lambda: market_service.get_market_chart_payload(
            dependencies.source,
            market_id,
            range_name="1d",
            interval="5m",
        ),
    )
    orderbook_result = (
        _closed_orderbook_result(
            dependencies,
            market_id=market_id,
            cache_key=orderbook_key,
        )
        if _market_is_closed(dependencies, market_id)
        else _cached_layer_read_only(
            dependencies,
            layer="orderbook",
            cache_key=orderbook_key,
            ttl_seconds=ORDERBOOK_TTL_SECONDS,
            builder=lambda: lob_service.get_runtime_lob_payload(
                dependencies.source,
                market_id,
            ),
        )
    )

    detail = detail_result["payload"] if isinstance(detail_result["payload"], dict) else {}
    chart = chart_result["payload"] if isinstance(chart_result["payload"], dict) else _fallback_payload("chart", chart_key)
    orderbook = orderbook_result["payload"] if isinstance(orderbook_result["payload"], dict) else _fallback_payload("orderbook", orderbook_key)
    chart.setdefault("marketId", market_id)
    chart.setdefault("localMarketId", market_id)
    chart.setdefault("range", "1d")
    chart.setdefault("interval", "5m")
    orderbook.setdefault("marketId", market_id)
    orderbook.setdefault("localMarketId", market_id)

    payload = dict(detail)
    payload.setdefault("marketId", market_id)
    payload.setdefault("localMarketId", market_id)
    payload["chart"] = chart
    payload["lob"] = orderbook
    payload["cacheLayers"] = {
        "detail": detail_result["mode"],
        "chart": chart_result["mode"],
        "orderbook": orderbook_result["mode"],
    }
    payload["focusStatus"] = (
        "ready"
        if detail_result["mode"] != "warming" and chart_result["mode"] != "warming" and orderbook_result["mode"] != "warming"
        else "warming"
    )
    payload["generatedAt"] = _utc_now_iso()
    return payload
