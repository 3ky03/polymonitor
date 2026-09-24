from __future__ import annotations

import json
import threading
import time
from datetime import timedelta
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Optional

from . import clickhouse_orderfilled_service


CRITICAL_NOTIONAL = Decimal("2500")
ELEVATED_NOTIONAL = Decimal("1000")
SIGNAL_SNAPSHOT_NAMESPACE_ALPHA = "snapshot:signals:alpha"
SIGNAL_SNAPSHOT_NAMESPACE_WHALES = "snapshot:signals:whales"
SIGNAL_SNAPSHOT_NAMESPACE_SUSPICIOUS = "snapshot:signals:suspicious"
DEFAULT_ALPHA_SIGNAL_LIMIT = 8
DEFAULT_WHALE_TRADES_LIMIT = 14
DEFAULT_SUSPICIOUS_TRADES_LIMIT = 12
DEFAULT_WHALE_TRADES_LOOKBACK_DAYS = 7
_SIGNAL_REFRESH_LOCK = threading.Lock()
_SIGNAL_REFRESH_STATE: Dict[str, bool] = {}


def build_whale_trades_cache_key(limit: int = 14, lookback_days: int = 7) -> str:
    return json.dumps({"limit": limit, "lookbackDays": lookback_days}, sort_keys=True, ensure_ascii=True)


def build_suspicious_trades_cache_key(limit: int = 12) -> str:
    return json.dumps({"limit": limit}, sort_keys=True, ensure_ascii=True)


def build_alpha_signal_cache_key(limit: int = 8) -> str:
    return json.dumps({"limit": limit}, sort_keys=True, ensure_ascii=True)


def normalize_signal_payload(payload: Dict[str, Any], *, generated_at: str, source: str = "polyData signal seed") -> Dict[str, Any]:
    items = payload.get("items")
    normalized = dict(payload)
    normalized["items"] = items if isinstance(items, list) else []
    normalized.setdefault("generatedAt", generated_at)
    normalized.setdefault("source", source)
    normalized.setdefault("status", "ok" if normalized["items"] else "empty")
    normalized.setdefault("cacheMode", "live-build")
    return normalized


def _limit_signal_payload(ctx: dict, payload: Dict[str, Any], *, limit: int) -> Dict[str, Any]:
    normalized = normalize_signal_payload(payload, generated_at=ctx["utc_now_iso"]())
    normalized["items"] = [item for item in normalized.get("items", []) if isinstance(item, dict)][: max(0, int(limit))]
    return normalized


def _severity_for_notional(ctx: dict, notional: Any) -> str:
    value = ctx["_safe_decimal"](notional)
    if value is not None and value >= CRITICAL_NOTIONAL:
        return "critical"
    if value is not None and value >= ELEVATED_NOTIONAL:
        return "elevated"
    return "watch"


def _is_live_signal_source(source_mode: str) -> bool:
    return str(source_mode or "") in {"live-trades", "clickhouse-volume-whales", "clickhouse-volume-alpha"}


def _clickhouse_signal_queries_available(ctx: dict) -> bool:
    return ctx.get("app") is not None and callable(ctx.get("query_all"))


def _format_percent(ctx: dict, value: Any) -> str:
    parsed = ctx["_safe_decimal"](value)
    if parsed is None:
        return "--"
    return f"{(parsed * Decimal('100')).quantize(Decimal('0.1'))}%"


def _money_text(ctx: dict, value: Any) -> str:
    parsed = ctx["_safe_decimal"](value)
    if parsed is None:
        return "$--"
    if parsed >= Decimal("1000000"):
        return f"${(parsed / Decimal('1000000')).quantize(Decimal('0.1'))}M"
    if parsed >= Decimal("1000"):
        return f"${(parsed / Decimal('1000')).quantize(Decimal('0.1'))}k"
    return f"${parsed.quantize(Decimal('1'))}"


def _is_near_resolved_price(ctx: dict, price: Any) -> bool:
    parsed = ctx["_safe_decimal"](price)
    return parsed is not None and (parsed > Decimal("0.98") or parsed < Decimal("0.02"))


def _whale_route_key(row: Dict[str, Any]) -> Optional[tuple[str, str]]:
    market_id = str(row.get("market_id") or row.get("marketId") or "").strip()
    route = str(row.get("taker") or row.get("maker") or "").strip().lower()
    if not market_id or not route:
        return None
    return (market_id, route)


def _clickhouse_source_states(status: str, *, rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    states: Dict[str, Any] = {
        "clickhouse": "ok" if status == "ok" else status,
        "clickhouseMode": clickhouse_orderfilled_service.clickhouse_read_mode(),
    }
    latest_block = None
    for row in rows or []:
        raw = row.get("latest_block") or row.get("block_number")
        try:
            block = int(raw)
        except (TypeError, ValueError):
            continue
        latest_block = block if latest_block is None else max(latest_block, block)
    if latest_block is not None:
        states["clickhouseLatestBlock"] = latest_block
    return states


def _format_trade_item(ctx: dict, row: Dict[str, Any]) -> Dict[str, Any]:
    item = {
        "marketId": row.get("market_id"),
        "localMarketId": row.get("market_id"),
        "marketTitle": row.get("market_title"),
        "timestamp": row.get("timestamp"),
        "txHash": row.get("tx_hash"),
        "outcome": row.get("outcome"),
        "side": row.get("side"),
        "price": ctx["format_trade_decimal"](row.get("price")),
        "size": ctx["format_trade_decimal"](row.get("size")),
        "notional": ctx["format_trade_decimal"](row.get("notional")),
        "maker": ctx["format_trade_address"](row.get("maker")),
        "taker": ctx["format_trade_address"](row.get("taker")),
        "severity": row.get("severity") or _severity_for_notional(ctx, row.get("notional")),
    }
    for source_key, target_key in (
        ("source_mode", "sourceMode"),
        ("signal_type", "signalType"),
        ("threshold_notional", "thresholdNotional"),
        ("elevated_threshold_notional", "elevatedThresholdNotional"),
        ("critical_threshold_notional", "criticalThresholdNotional"),
        ("market_window_notional", "marketWindowNotional"),
        ("market_share", "marketShare"),
        ("entry_yes_price", "entryYesPrice"),
        ("price_after_1m", "priceAfter1m"),
        ("price_after_5m", "priceAfter5m"),
        ("price_after_15m", "priceAfter15m"),
        ("edge_after_fees", "edgeAfterFees"),
        ("edge_fee_probability", "edgeFeeProbability"),
    ):
        if row.get(source_key) is not None:
            item[target_key] = ctx["format_trade_decimal"](row.get(source_key)) if source_key != "source_mode" and source_key != "signal_type" else row.get(source_key)
    return item


def _format_alpha_volume_signal(ctx: dict, row: Dict[str, Any]) -> Dict[str, Any]:
    flow = ctx["format_trade_decimal"](row.get("flow_notional"))
    net_flow = ctx["format_trade_decimal"](row.get("net_flow_notional"))
    max_trade = ctx["format_trade_decimal"](row.get("max_trade_notional"))
    market_share = _format_percent(ctx, row.get("market_share"))
    net_strength = _format_percent(ctx, row.get("net_direction_strength"))
    side = str(row.get("side") or "FLOW").upper()
    outcome = str(row.get("outcome") or "--").upper()
    direction = str(row.get("direction") or ("bearish" if outcome == "NO" else "bullish")).lower()
    market_title = row.get("market_title") or "Market flow"
    window_minutes = row.get("window_minutes") or 15
    score = ctx["format_trade_decimal"](row.get("score"))
    max_trade_text = _money_text(ctx, row.get("max_trade_notional"))
    edge = ctx["format_trade_decimal"](row.get("edge_after_fees"))
    post_5m = ctx["format_trade_decimal"](row.get("price_after_5m"))
    post_summary = f"; 5m edge {edge}" if edge is not None else ""
    return {
        "kind": "volume-flow",
        "severity": row.get("severity") or _severity_for_notional(ctx, row.get("flow_notional")),
        "bias": "bearish" if direction == "bearish" else "bullish",
        "sourceLabel": "FLOW+$",
        "sourceTag": "FLOW",
        "headline": f"{window_minutes}m net directional flow",
        "action": {"label": "Sell" if side == "SELL" else "Buy", "outcome": "No" if outcome == "NO" else "Yes" if outcome == "YES" else outcome.title()},
        "title": f"{side} {outcome} flow {_money_text(ctx, row.get('flow_notional'))}: {market_title}",
        "summary": f"net {net_strength}; {row.get('trade_count') or 0} fills; max fill {max_trade_text}; {market_share} of market baseline{post_summary}",
        "timestamp": row.get("timestamp"),
        "marketId": row.get("market_id"),
        "localMarketId": row.get("market_id"),
        "marketTitle": market_title,
        "txHash": row.get("tx_hash"),
        "side": side,
        "outcome": outcome,
        "price": ctx["format_trade_decimal"](row.get("avg_price")),
        "notional": flow,
        "contributors": ["clickhouse", "volume", "flow"],
        "relatedContent": [],
        "sourceMode": row.get("source_mode") or "clickhouse-volume-alpha",
        "metrics": {
            "flowNotional": flow,
            "netFlowNotional": net_flow,
            "bullishNotional": ctx["format_trade_decimal"](row.get("bullish_notional")),
            "bearishNotional": ctx["format_trade_decimal"](row.get("bearish_notional")),
            "oppositeFlowNotional": ctx["format_trade_decimal"](row.get("opposite_flow_notional")),
            "netDirectionStrength": ctx["format_trade_decimal"](row.get("net_direction_strength")),
            "churnRatio": ctx["format_trade_decimal"](row.get("churn_ratio")),
            "maxTradeNotional": max_trade,
            "marketBaselineNotional": ctx["format_trade_decimal"](row.get("market_baseline_notional")),
            "marketShare": ctx["format_trade_decimal"](row.get("market_share")),
            "uniqueTraderCount": row.get("unique_trader_count"),
            "priceHealth": ctx["format_trade_decimal"](row.get("price_health")),
            "entryYesPrice": ctx["format_trade_decimal"](row.get("entry_yes_price")),
            "priceAfter1m": ctx["format_trade_decimal"](row.get("price_after_1m")),
            "priceAfter5m": post_5m,
            "priceAfter15m": ctx["format_trade_decimal"](row.get("price_after_15m")),
            "edgeAfterFees": edge,
            "edgeFeeProbability": ctx["format_trade_decimal"](row.get("edge_fee_probability")),
            "tradeCount": row.get("trade_count"),
            "score": score,
            "volumeScore": ctx["format_trade_decimal"](row.get("volume_score")),
            "thresholdFlowNotional": ctx["format_trade_decimal"](row.get("threshold_flow_notional")),
        },
    }


def _query_whale_rows(ctx: dict, *, limit: int, lookback_days: int) -> List[Dict[str, Any]]:
    if _clickhouse_signal_queries_available(ctx):
        volume_rows = clickhouse_orderfilled_service.get_volume_whale_rows(ctx, limit=max(limit * 2, limit))
        if volume_rows is not None:
            return volume_rows

    iso_days_before = ctx.get("iso_days_before")
    if callable(iso_days_before):
        threshold = iso_days_before(ctx["utc_now_iso"](), lookback_days) or ctx["utc_date_days_ago"](lookback_days)
    else:
        utc_date_days_ago = ctx.get("utc_date_days_ago")
        if not callable(utc_date_days_ago):
            return []
        threshold = utc_date_days_ago(lookback_days)
    threshold_dt = ctx["parse_iso_datetime"](threshold)
    try:
        recent_trades = ctx["get_recent_trades"](limit=max(160, limit * 24))
    except Exception:
        logger = getattr(ctx.get("app"), "logger", None)
        if logger is not None:
            logger.exception("whale rows trade source failed")
        return _query_market_activity_rows(ctx, limit=limit, threshold_dt=threshold_dt)
    rows: List[Dict[str, Any]] = []
    for trade in recent_trades:
        market_id = trade.get("marketId") or trade.get("market_id")
        if market_id is None:
            continue
        timestamp = trade.get("timestamp")
        timestamp_dt = ctx["parse_iso_datetime"](timestamp)
        if threshold_dt is not None and timestamp_dt is not None and timestamp_dt < threshold_dt:
            continue
        price = ctx["_safe_decimal"](trade.get("price"))
        size = ctx["_safe_decimal"](trade.get("size"))
        notional = ctx["_safe_decimal"](trade.get("notional"))
        if notional is None and price is not None and size is not None:
            notional = price * size
        rows.append(
            {
                "market_id": market_id,
                "market_title": trade.get("marketTitle") or trade.get("market_title"),
                "timestamp": timestamp,
                "tx_hash": trade.get("txHash") or trade.get("tx_hash"),
                "outcome": trade.get("outcome"),
                "side": trade.get("side"),
                "price": price,
                "size": size,
                "notional": notional,
                "maker": trade.get("maker"),
                "taker": trade.get("taker"),
                "source_mode": "live-trades",
            }
        )
    rows.sort(key=lambda row: (ctx["_safe_decimal"](row.get("notional")) or Decimal("0")), reverse=True)
    if not rows:
        return _query_market_activity_rows(ctx, limit=limit, threshold_dt=threshold_dt)
    return rows[: max(limit * 2, limit)]


def _query_market_activity_rows(ctx: dict, *, limit: int, threshold_dt: Any = None) -> List[Dict[str, Any]]:
    """Fallback when raw recent trades are unavailable over the remote DB tunnel."""
    payload = _read_bootstrap_activity_payload(ctx)
    if payload is None:
        try:
            payload = ctx["get_active_markets_snapshot"](page_size=max(12, limit * 2))
        except Exception:
            logger = getattr(ctx.get("app"), "logger", None)
            if logger is not None:
                logger.exception("whale rows active market fallback failed")
            return []

    rows: List[Dict[str, Any]] = []
    for market in (payload or {}).get("items") or []:
        if not isinstance(market, dict) or market.get("id") is None:
            continue
        notional = ctx["_safe_decimal"](market.get("volume24h")) or Decimal("0")
        trade_count = int(market.get("tradeCount24h") or 0)
        if notional <= 0 and trade_count <= 0:
            continue
        timestamp = market.get("lastTradeAt")
        timestamp_dt = ctx["parse_iso_datetime"](timestamp)
        if threshold_dt is not None and (timestamp_dt is None or timestamp_dt < threshold_dt):
            continue
        rows.append(
            {
                "market_id": market.get("id"),
                "market_title": market.get("title"),
                "timestamp": timestamp,
                "tx_hash": None,
                "outcome": None,
                "side": "activity",
                "price": ctx["_safe_decimal"](market.get("latestPrice")),
                "size": None,
                "notional": notional,
                "maker": None,
                "taker": None,
                "source_mode": "market-activity-fallback",
            }
        )
    if not rows:
        rows.extend(_query_bootstrap_trade_rows(ctx, limit=limit, threshold_dt=threshold_dt))
    rows.sort(key=lambda row: (ctx["_safe_decimal"](row.get("notional")) or Decimal("0")), reverse=True)
    return rows[: max(limit * 2, limit)]


def _read_bootstrap_activity_payload(ctx: dict) -> Optional[Dict[str, Any]]:
    payload: Optional[Dict[str, Any]] = None
    reader = ctx.get("get_cached_json")
    if callable(reader):
        cached = reader("bootstrap", "workspace-default-v9")
        if isinstance(cached, dict):
            payload = cached
    if payload is None:
        snapshot_store = ctx.get("SNAPSHOT_STORE")
        if snapshot_store is not None:
            cached = snapshot_store.get_stale("snapshot:bootstrap", "workspace-default-v9")
            if isinstance(cached, dict):
                payload = cached
    items = (payload or {}).get("activeMarketsPreview")
    if isinstance(items, list):
        return {"items": items}
    return None


def _query_bootstrap_trade_rows(ctx: dict, *, limit: int, threshold_dt: Any = None) -> List[Dict[str, Any]]:
    payload = _read_bootstrap_payload(ctx)
    trades = (payload or {}).get("globalTradesPreview")
    if not isinstance(trades, list):
        return []
    rows: List[Dict[str, Any]] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        market_id = trade.get("marketId") or trade.get("market_id")
        if market_id is None:
            continue
        timestamp = trade.get("timestamp")
        timestamp_dt = ctx["parse_iso_datetime"](timestamp)
        if threshold_dt is not None and (timestamp_dt is None or timestamp_dt < threshold_dt):
            continue
        price = ctx["_safe_decimal"](trade.get("price"))
        size = ctx["_safe_decimal"](trade.get("size"))
        notional = ctx["_safe_decimal"](trade.get("notional"))
        if notional is None and price is not None and size is not None:
            notional = price * size
        rows.append(
            {
                "market_id": market_id,
                "market_title": trade.get("marketTitle") or trade.get("market_title"),
                "timestamp": timestamp,
                "tx_hash": trade.get("txHash") or trade.get("tx_hash"),
                "outcome": trade.get("outcome"),
                "side": trade.get("side"),
                "price": price,
                "size": size,
                "notional": notional,
                "maker": trade.get("maker"),
                "taker": trade.get("taker"),
                "source_mode": "bootstrap-trades-fallback",
            }
        )
    rows.sort(key=lambda row: (ctx["_safe_decimal"](row.get("notional")) or Decimal("0")), reverse=True)
    return rows[: max(limit * 2, limit)]


def _read_bootstrap_payload(ctx: dict) -> Optional[Dict[str, Any]]:
    reader = ctx.get("get_cached_json")
    if callable(reader):
        cached = reader("bootstrap", "workspace-default-v9")
        if isinstance(cached, dict):
            return cached
    snapshot_store = ctx.get("SNAPSHOT_STORE")
    if snapshot_store is not None:
        cached = snapshot_store.get_stale("snapshot:bootstrap", "workspace-default-v9")
        if isinstance(cached, dict):
            return cached
    return None


def _store_runtime_snapshot(ctx: dict, namespace: str, cache_key: str, payload: Dict[str, Any], ttl_seconds: int) -> Dict[str, Any]:
    ctx["SNAPSHOT_STORE"].set(namespace, cache_key, payload, ttl_seconds)
    return ctx["set_cached_runtime_payload"](namespace, cache_key, payload, ttl_seconds)


def _refresh_runtime_snapshot(
    ctx: dict,
    *,
    namespace: str,
    cache_key: str,
    ttl_seconds: int,
    builder: Callable[[], Dict[str, Any]],
    refresh_state_key: str,
    label: str,
    reason: str,
) -> Optional[Dict[str, Any]]:
    started_at = time.perf_counter()
    ctx["app"].logger.info("%s refresh-start reason=%s", label, reason)
    try:
        payload = builder()
        stored = _store_runtime_snapshot(ctx, namespace, cache_key, payload, ttl_seconds)
        ctx["app"].logger.info("%s refresh-done reason=%s duration_ms=%.2f", label, reason, (time.perf_counter() - started_at) * 1000)
        return stored
    except Exception:
        ctx["app"].logger.exception("%s refresh-failed reason=%s", label, reason)
        return None
    finally:
        with _SIGNAL_REFRESH_LOCK:
            _SIGNAL_REFRESH_STATE[refresh_state_key] = False


def _schedule_runtime_snapshot_refresh(
    ctx: dict,
    *,
    namespace: str,
    cache_key: str,
    ttl_seconds: int,
    builder: Callable[[], Dict[str, Any]],
    refresh_state_key: str,
    label: str,
    reason: str,
) -> None:
    with _SIGNAL_REFRESH_LOCK:
        if _SIGNAL_REFRESH_STATE.get(refresh_state_key):
            return
        _SIGNAL_REFRESH_STATE[refresh_state_key] = True
    thread = ctx["threading"].Thread(
        target=lambda: _refresh_runtime_snapshot(
            ctx,
            namespace=namespace,
            cache_key=cache_key,
            ttl_seconds=ttl_seconds,
            builder=builder,
            refresh_state_key=refresh_state_key,
            label=label,
            reason=reason,
        ),
        name=f"{label}-refresh",
        daemon=True,
    )
    thread.start()


def _get_stale_first_runtime_snapshot(
    ctx: dict,
    *,
    namespace: str,
    cache_key: str,
    ttl_seconds: int,
    builder: Callable[[], Dict[str, Any]],
    refresh_state_key: str,
    label: str,
    cold_fallback: Optional[Callable[[], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    cached = ctx["get_cached_runtime_payload"](namespace, cache_key)
    if cached is not None:
        return cached

    redis_reader = ctx.get("get_cached_json")
    if callable(redis_reader):
        redis_payload = redis_reader(namespace, cache_key)
        if isinstance(redis_payload, dict):
            ctx["SNAPSHOT_STORE"].set(namespace, cache_key, redis_payload, ttl_seconds)
            return ctx["set_cached_runtime_payload"](namespace, cache_key, redis_payload, ttl_seconds)

    fresh_payload = ctx["SNAPSHOT_STORE"].get(namespace, cache_key)
    if fresh_payload is not None:
        return ctx["set_cached_runtime_payload"](namespace, cache_key, fresh_payload, ttl_seconds)

    stale_payload = ctx["SNAPSHOT_STORE"].get_stale(namespace, cache_key)
    if stale_payload is not None:
        ctx["app"].logger.info("%s stale-hit scheduling_refresh=true", label)
        ctx["set_cached_runtime_payload"](namespace, cache_key, stale_payload, ttl_seconds)
        _schedule_runtime_snapshot_refresh(
            ctx,
            namespace=namespace,
            cache_key=cache_key,
            ttl_seconds=ttl_seconds,
            builder=builder,
            refresh_state_key=refresh_state_key,
            label=label,
            reason="stale-hit",
        )
        return stale_payload

    if cold_fallback is not None:
        ctx["app"].logger.info("%s cold-miss returning_fallback=true scheduling_refresh=true", label)
        _schedule_runtime_snapshot_refresh(
            ctx,
            namespace=namespace,
            cache_key=cache_key,
            ttl_seconds=ttl_seconds,
            builder=builder,
            refresh_state_key=refresh_state_key,
            label=label,
            reason="cold-miss",
        )
        fallback_payload = cold_fallback()
        return ctx["set_cached_runtime_payload"](namespace, cache_key, fallback_payload, min(15, ttl_seconds))

    with _SIGNAL_REFRESH_LOCK:
        if _SIGNAL_REFRESH_STATE.get(refresh_state_key):
            payload = {"items": [], "generatedAt": ctx["utc_now_iso"](), "status": "warming"}
            return ctx["set_cached_runtime_payload"](namespace, cache_key, payload, min(5, ttl_seconds))
        _SIGNAL_REFRESH_STATE[refresh_state_key] = True
    payload = _refresh_runtime_snapshot(
        ctx,
        namespace=namespace,
        cache_key=cache_key,
        ttl_seconds=ttl_seconds,
        builder=builder,
        refresh_state_key=refresh_state_key,
        label=label,
        reason="cold-miss",
    )
    if payload is not None:
        return payload
    raise RuntimeError(f"{label} snapshot refresh failed")


def _set_runtime_payload_if_possible(ctx: dict, namespace: str, cache_key: str, payload: Dict[str, Any], ttl_seconds: int) -> Dict[str, Any]:
    setter = ctx.get("set_cached_runtime_payload")
    if callable(setter):
        return setter(namespace, cache_key, payload, ttl_seconds)
    return payload


def _read_cached_signal_snapshot(ctx: dict, *, namespace: str, cache_key: str, ttl_seconds: int) -> Optional[Dict[str, Any]]:
    runtime_reader = ctx.get("get_cached_runtime_payload")
    if callable(runtime_reader):
        cached = runtime_reader(namespace, cache_key)
        if isinstance(cached, dict):
            return cached

    redis_reader = ctx.get("get_cached_json")
    if callable(redis_reader):
        redis_payload = redis_reader(namespace, cache_key)
        if isinstance(redis_payload, dict):
            snapshot_store = ctx.get("SNAPSHOT_STORE")
            if snapshot_store is not None:
                snapshot_store.set(namespace, cache_key, redis_payload, ttl_seconds)
            return _set_runtime_payload_if_possible(ctx, namespace, cache_key, redis_payload, ttl_seconds)

    snapshot_store = ctx.get("SNAPSHOT_STORE")
    if snapshot_store is None:
        return None
    fresh_payload = snapshot_store.get(namespace, cache_key)
    if isinstance(fresh_payload, dict):
        return _set_runtime_payload_if_possible(ctx, namespace, cache_key, fresh_payload, ttl_seconds)
    stale_payload = snapshot_store.get_stale(namespace, cache_key)
    if isinstance(stale_payload, dict):
        return _set_runtime_payload_if_possible(ctx, namespace, cache_key, stale_payload, min(15, ttl_seconds))
    return None


def _build_whale_trades_payload(ctx: dict, limit: int = 14, lookback_days: int = 7) -> Dict[str, Any]:
    rows = _query_whale_rows(ctx, limit=max(limit * 2, limit), lookback_days=lookback_days)
    items: List[Dict[str, Any]] = []
    seen_hashes: set[str] = set()
    seen_routes: set[tuple[str, str]] = set()
    source_modes: set[str] = set()
    for row in rows:
        tx_hash = str(row.get("tx_hash") or "")
        if tx_hash and tx_hash in seen_hashes:
            continue
        if tx_hash:
            seen_hashes.add(tx_hash)
        if _is_near_resolved_price(ctx, row.get("price")):
            continue
        route_key = _whale_route_key(row)
        if route_key is not None and str(row.get("source_mode") or "") == "clickhouse-volume-whales":
            if route_key in seen_routes:
                continue
            seen_routes.add(route_key)
        source_modes.add(str(row.get("source_mode") or "unknown"))
        items.append(_format_trade_item(ctx, row))
        if len(items) >= limit:
            break
    status = "empty"
    if items:
        status = "ok" if source_modes and all(_is_live_signal_source(mode) for mode in source_modes) else "degraded"
    source_mode = next(iter(source_modes)) if len(source_modes) == 1 else "mixed-live" if source_modes and all(_is_live_signal_source(mode) for mode in source_modes) else "fallback" if source_modes else "none"
    return normalize_signal_payload(
        {
            "items": items,
            "generatedAt": ctx["utc_now_iso"](),
            "status": status,
            "sourceMode": source_mode,
            "sourceStates": _clickhouse_source_states("ok" if status == "ok" else status, rows=rows),
        },
        generated_at=ctx["utc_now_iso"](),
    )


def fetch_live_whale_trades_payload(ctx: dict, limit: int = 14, lookback_days: int = 7) -> Dict[str, Any]:
    return normalize_signal_payload(
        _build_whale_trades_payload(ctx, limit=limit, lookback_days=lookback_days),
        generated_at=ctx["utc_now_iso"](),
    )


def get_whale_trades_snapshot(ctx: dict, limit: int = DEFAULT_WHALE_TRADES_LIMIT, lookback_days: int = DEFAULT_WHALE_TRADES_LOOKBACK_DAYS) -> Dict[str, Any]:
    cache_key = build_whale_trades_cache_key(limit=limit, lookback_days=lookback_days)
    if int(limit or 0) != DEFAULT_WHALE_TRADES_LIMIT and int(lookback_days or 0) == DEFAULT_WHALE_TRADES_LOOKBACK_DAYS:
        default_payload = _read_cached_signal_snapshot(
            ctx,
            namespace=SIGNAL_SNAPSHOT_NAMESPACE_WHALES,
            cache_key=build_whale_trades_cache_key(limit=DEFAULT_WHALE_TRADES_LIMIT, lookback_days=DEFAULT_WHALE_TRADES_LOOKBACK_DAYS),
            ttl_seconds=ctx["SIGNAL_RUNTIME_TTL_SECONDS"],
        )
        if default_payload is not None:
            return _limit_signal_payload(ctx, default_payload, limit=limit)
    return _get_stale_first_runtime_snapshot(
        ctx,
        namespace=SIGNAL_SNAPSHOT_NAMESPACE_WHALES,
        cache_key=cache_key,
        ttl_seconds=ctx["SIGNAL_RUNTIME_TTL_SECONDS"],
        builder=lambda: fetch_live_whale_trades_payload(ctx, limit=limit, lookback_days=lookback_days),
        refresh_state_key=f"whales:{cache_key}",
        label="whales-snapshot",
    )


def _recent_oracle_candidates(ctx: dict, limit: int) -> List[Dict[str, Any]]:
    try:
        events = ctx["get_recent_oracle_events"](limit=max(limit * 2, 16))
    except Exception:
        logger = getattr(ctx.get("app"), "logger", None)
        if logger is not None:
            logger.exception("suspicious oracle source failed")
        return []
    filtered = []
    seen: set[tuple[Any, Any]] = set()
    for event in events:
        market_id = event.get("marketId") or event.get("market_id")
        event_time = event.get("eventTime") or event.get("event_time")
        if market_id is None or not event_time:
            continue
        key = (market_id, event_time)
        if key in seen:
            continue
        seen.add(key)
        filtered.append(event)
    return filtered[: max(limit, 8)]


def get_suspicious_trades_snapshot(ctx: dict, limit: int = DEFAULT_SUSPICIOUS_TRADES_LIMIT) -> Dict[str, Any]:
    cache_key = build_suspicious_trades_cache_key(limit=limit)
    if int(limit or 0) != DEFAULT_SUSPICIOUS_TRADES_LIMIT:
        default_payload = _read_cached_signal_snapshot(
            ctx,
            namespace=SIGNAL_SNAPSHOT_NAMESPACE_SUSPICIOUS,
            cache_key=build_suspicious_trades_cache_key(limit=DEFAULT_SUSPICIOUS_TRADES_LIMIT),
            ttl_seconds=ctx["SIGNAL_RUNTIME_TTL_SECONDS"],
        )
        if default_payload is not None:
            return _limit_signal_payload(ctx, default_payload, limit=limit)
    return _get_stale_first_runtime_snapshot(
        ctx,
        namespace=SIGNAL_SNAPSHOT_NAMESPACE_SUSPICIOUS,
        cache_key=cache_key,
        ttl_seconds=ctx["SIGNAL_RUNTIME_TTL_SECONDS"],
        builder=lambda: fetch_live_suspicious_trades_payload(ctx, limit=limit),
        refresh_state_key=f"suspicious:{cache_key}",
        label="suspicious-snapshot",
    )


def fetch_live_suspicious_trades_payload(ctx: dict, limit: int = 12) -> Dict[str, Any]:
    return normalize_signal_payload(
        {"items": _build_suspicious_trade_items(ctx, limit), "generatedAt": ctx["utc_now_iso"]()},
        generated_at=ctx["utc_now_iso"](),
    )


def _build_suspicious_trade_items(ctx: dict, limit: int = 12) -> List[Dict[str, Any]]:
    oracle_events = _recent_oracle_candidates(ctx, limit)
    try:
        recent_trades = ctx["get_recent_trades"](limit=max(200, limit * 30))
    except Exception:
        logger = getattr(ctx.get("app"), "logger", None)
        if logger is not None:
            logger.exception("suspicious trade source failed")
        recent_trades = []
    if not oracle_events and not recent_trades:
        return [
            {
                **_format_trade_item(ctx, row),
                "eventStatus": "activity-fallback",
                "summary": "Active market volume surfaced while trade/oracle sources are unavailable",
            }
            for row in _query_market_activity_rows(ctx, limit=limit)[:limit]
        ]
    items: List[Dict[str, Any]] = []
    seen_hashes: set[str] = set()
    oracle_by_market: Dict[Any, List[Dict[str, Any]]] = {}
    for event in oracle_events:
        market_id = event.get("marketId") or event.get("market_id")
        if market_id is None:
            continue
        oracle_by_market.setdefault(market_id, []).append(event)

    for trade in recent_trades:
        market_id = trade.get("marketId") or trade.get("market_id")
        if market_id is None or market_id not in oracle_by_market:
            continue
        trade_time = ctx["parse_iso_datetime"](trade.get("timestamp"))
        if trade_time is None:
            continue
        for event in oracle_by_market[market_id]:
            event_time = event.get("eventTime") or event.get("event_time")
            event_dt = ctx["parse_iso_datetime"](event_time)
            if event_dt is None:
                continue
            if not (event_dt - timedelta(hours=6) <= trade_time <= event_dt):
                continue
            tx_hash = str(trade.get("txHash") or trade.get("tx_hash") or "")
            if tx_hash and tx_hash in seen_hashes:
                continue
            if tx_hash:
                seen_hashes.add(tx_hash)
            price = ctx["_safe_decimal"](trade.get("price"))
            size = ctx["_safe_decimal"](trade.get("size"))
            notional = ctx["_safe_decimal"](trade.get("notional"))
            if notional is None and price is not None and size is not None:
                notional = price * size
            item = _format_trade_item(
                ctx,
                {
                    "market_id": market_id,
                    "market_title": trade.get("marketTitle") or trade.get("market_title") or event.get("marketTitle") or event.get("market_title"),
                    "timestamp": trade.get("timestamp"),
                    "tx_hash": tx_hash,
                    "outcome": trade.get("outcome"),
                    "side": trade.get("side"),
                    "price": price,
                    "size": size,
                    "notional": notional,
                    "maker": trade.get("maker"),
                    "taker": trade.get("taker"),
                },
            )
            item.update(
                {
                    "eventStatus": event.get("eventStatus") or event.get("event_status"),
                    "eventTime": event_time,
                    "summary": f"{event.get('eventStatus') or event.get('event_status') or 'oracle'} window trade near oracle event",
                }
            )
            items.append(item)
            break
        if len(items) >= limit:
            break

    if items:
        items.sort(key=lambda item: (ctx["_safe_decimal"](item.get("notional")) or Decimal("0")), reverse=True)
        return items[:limit]

    fallback_items = []
    for row in _query_whale_rows(ctx, limit=limit, lookback_days=1)[:limit]:
        fallback_items.append(
            {
                **_format_trade_item(ctx, row),
                "eventStatus": "heuristic",
                "summary": "Large live trade surfaced by fallback heuristic",
            }
        )
    return fallback_items


def _append_signal(signals: List[Dict[str, Any]], *, kind: str, severity: str, title: Any, summary: str, timestamp: Any, contributors: Iterable[str] | None = None) -> None:
    signals.append(
        {
            "kind": kind,
            "severity": severity,
            "title": title,
            "summary": summary,
            "timestamp": timestamp,
            "contributors": list(contributors or []),
        }
    )


def _build_alpha_signal_payload(ctx: dict, limit: int = 8) -> Dict[str, Any]:
    trade_source_status = "ok"
    signals: List[Dict[str, Any]] = []
    volume_rows = None
    if _clickhouse_signal_queries_available(ctx):
        try:
            volume_rows = clickhouse_orderfilled_service.get_alpha_volume_signal_rows(ctx, limit=limit)
        except Exception:
            logger = getattr(ctx.get("app"), "logger", None)
            if logger is not None:
                logger.exception("alpha volume source failed")
            trade_source_status = "degraded"
    if volume_rows is not None:
        signals.extend(_format_alpha_volume_signal(ctx, row) for row in volume_rows[:limit])
    else:
        trade_source_status = "degraded"

    whale_rows = _query_whale_rows(ctx, limit=6, lookback_days=7)[:6] if len(signals) < limit else []
    if any(not _is_live_signal_source(str(row.get("source_mode") or "")) for row in whale_rows):
        trade_source_status = "degraded"
    whales = [_format_trade_item(ctx, row) for row in whale_rows]
    for trade in whales[:3]:
        if len(signals) >= limit:
            break
        _append_signal(
            signals,
            kind="whale",
            severity=trade.get("severity") or "elevated",
            title=trade.get("marketTitle") or "Whale flow",
            summary=f"{str(trade.get('side') or 'trade').upper()} {trade.get('outcome') or '--'} at {trade.get('price') or '--'} on-chain, notional {trade.get('notional') or '--'}",
            timestamp=trade.get("timestamp"),
            contributors=["whale", "onchain"],
        )

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for signal in signals:
        key = (signal.get("kind"), signal.get("title"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(signal)
        if len(deduped) >= limit:
            break
    status = "degraded" if trade_source_status != "ok" else "ok" if deduped else "empty"
    source_rows = volume_rows or whale_rows
    source_state_status = "ok" if trade_source_status == "ok" else trade_source_status
    return normalize_signal_payload(
        {
            "items": deduped,
            "generatedAt": ctx["utc_now_iso"](),
            "status": status,
            "sourceMode": trade_source_status,
            "sourceStates": _clickhouse_source_states(source_state_status, rows=source_rows),
        },
        generated_at=ctx["utc_now_iso"](),
    )


def _build_alpha_fallback_payload(ctx: dict, limit: int = 8) -> Dict[str, Any]:
    signals: List[Dict[str, Any]] = []
    for row in _query_whale_rows(ctx, limit=min(6, limit), lookback_days=3):
        if len(signals) >= limit:
            break
        trade = _format_trade_item(ctx, row)
        _append_signal(
            signals,
            kind="whale",
            severity=trade.get("severity") or "watch",
            title=trade.get("marketTitle") or "Whale flow",
            summary=f"{str(trade.get('side') or 'trade').upper()} {trade.get('outcome') or '--'} at {trade.get('price') or '--'}, notional {trade.get('notional') or '--'}",
            timestamp=trade.get("timestamp"),
            contributors=["fast-fallback", "whale"],
        )

    get_active_markets_snapshot = ctx.get("get_active_markets_snapshot")
    if len(signals) < limit and callable(get_active_markets_snapshot):
        try:
            fallback_markets = get_active_markets_snapshot(page_size=8).get("items", [])
        except Exception:
            logger = getattr(ctx.get("app"), "logger", None)
            if logger is not None:
                logger.exception("alpha fallback active markets source failed")
            fallback_markets = []
        for market in fallback_markets[:4]:
            if len(signals) >= limit:
                break
            price = ctx["_safe_decimal"](market.get("latestPrice"))
            change_24h = ctx["_safe_decimal"](market.get("change24h"))
            _append_signal(
                signals,
                kind="momentum",
                severity="elevated" if change_24h is not None and abs(change_24h) >= Decimal("0.08") else "watch",
                title=market.get("title"),
                summary=f"Fast fallback: live probability {ctx['format_trade_decimal'](price) or '--'} with 24h change {ctx['format_trade_decimal'](change_24h) or '--'}",
                timestamp=ctx["utc_now_iso"](),
                contributors=["fast-fallback", "market"],
            )
    return {
        **normalize_signal_payload({"items": signals[:limit], "generatedAt": ctx["utc_now_iso"]()}, generated_at=ctx["utc_now_iso"]()),
        "status": "warming",
        "sourceMode": "fast-fallback",
    }


def fetch_live_alpha_signal_payload(ctx: dict, limit: int = 8) -> Dict[str, Any]:
    return normalize_signal_payload(
        _build_alpha_signal_payload(ctx, limit=limit),
        generated_at=ctx["utc_now_iso"](),
    )


def get_alpha_signal_snapshot(ctx: dict, limit: int = DEFAULT_ALPHA_SIGNAL_LIMIT) -> Dict[str, Any]:
    cache_key = build_alpha_signal_cache_key(limit=limit)
    if int(limit or 0) != DEFAULT_ALPHA_SIGNAL_LIMIT:
        default_payload = _read_cached_signal_snapshot(
            ctx,
            namespace=SIGNAL_SNAPSHOT_NAMESPACE_ALPHA,
            cache_key=build_alpha_signal_cache_key(limit=DEFAULT_ALPHA_SIGNAL_LIMIT),
            ttl_seconds=ctx["SIGNAL_RUNTIME_TTL_SECONDS"],
        )
        if default_payload is not None:
            return _limit_signal_payload(ctx, default_payload, limit=limit)
    return _get_stale_first_runtime_snapshot(
        ctx,
        namespace=SIGNAL_SNAPSHOT_NAMESPACE_ALPHA,
        cache_key=cache_key,
        ttl_seconds=ctx["SIGNAL_RUNTIME_TTL_SECONDS"],
        builder=lambda: fetch_live_alpha_signal_payload(ctx, limit=limit),
        refresh_state_key=f"alpha:{cache_key}",
        label="alpha-snapshot",
        cold_fallback=lambda: _build_alpha_fallback_payload(ctx, limit=limit),
    )
