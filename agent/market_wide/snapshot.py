from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.common.budget import claim_agent_live_call
from agent.common.gateway_client import call_market_wide_insight_gateway, gateway_configured

from .graph import forecast_run_id
from .service import LENS_ALIASES, VALID_LENSES, build_market_wide_fallback, build_market_wide_insight


SNAPSHOT_NAMESPACE = "agent:market-wide:snapshot"
QUANT_SNAPSHOT_NAMESPACE = "agent:market-wide:quant"
QUANT_HISTORY_NAMESPACE = "agent:market-wide:quant-history"
SNAPSHOT_VERSION = "v1"
DEFAULT_LENSES = ("overview", "special", "trend")
SIGNAL_SNAPSHOT_NAMESPACE_ALPHA = "snapshot:signals:alpha"
SIGNAL_SNAPSHOT_NAMESPACE_WHALES = "snapshot:signals:whales"
SIGNAL_SNAPSHOT_NAMESPACE_SUSPICIOUS = "snapshot:signals:suspicious"


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def snapshot_ttl_seconds() -> int:
    try:
        return max(60, int(os.environ.get("POLYDATA_AGENT_MARKET_WIDE_SNAPSHOT_TTL_SECONDS", "43200")))
    except ValueError:
        return 43200


def quant_snapshot_ttl_seconds() -> int:
    try:
        return max(
            snapshot_ttl_seconds(),
            int(os.environ.get("POLYDATA_AGENT_MARKET_WIDE_QUANT_TTL_SECONDS", str(7 * 86400))),
        )
    except ValueError:
        return 7 * 86400


def snapshot_min_live_interval_seconds() -> int:
    try:
        return max(300, int(os.environ.get("POLYDATA_AGENT_MARKET_WIDE_MIN_LIVE_INTERVAL_SECONDS", "43200")))
    except ValueError:
        return 43200


def normalize_lens(lens: Any) -> str:
    value = str(lens or "overview").strip().lower()
    value = LENS_ALIASES.get(value, value)
    return value if value in VALID_LENSES else "overview"


def snapshot_cache_key(lens: Any) -> str:
    return f"{SNAPSHOT_VERSION}:{normalize_lens(lens)}"


def quant_snapshot_cache_key(lens: Any) -> str:
    return f"{SNAPSHOT_VERSION}:{normalize_lens(lens)}"


def quant_history_cache_key(lens: Any, run_id: Any) -> str:
    run = str(run_id or "unknown").strip() or "unknown"
    return f"{SNAPSHOT_VERSION}:{normalize_lens(lens)}:{run}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _items(payload: Any) -> list[Any]:
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return payload["items"]
    if isinstance(payload, list):
        return payload
    return []


def _int_env(name: str, default: int, *, minimum: int = 0, maximum: int = 1000) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _as_float_or_none(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric:
        return None
    return numeric


def _market_id(item: dict[str, Any]) -> Any:
    return item.get("id") or item.get("localMarketId") or item.get("marketId")


def _callable_market_id(value: Any) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _market_fill_tape_limits() -> tuple[int, int]:
    market_limit = _int_env("POLYDATA_AGENT_MARKET_WIDE_FILL_TAPE_MARKETS", 8, minimum=0, maximum=16)
    trade_limit = _int_env("POLYDATA_AGENT_MARKET_WIDE_FILL_TAPE_TRADES", 80, minimum=8, maximum=240)
    return market_limit, trade_limit


def _market_fill_tape_rank(item: dict[str, Any]) -> float:
    volume = _as_float_or_none(item.get("volume24h")) or 0.0
    trades = _as_float_or_none(item.get("tradeCount24h")) or 0.0
    price = _as_float_or_none(item.get("latestPrice"))
    near_50_bonus = 200_000.0 if price is not None and 0.42 <= price <= 0.58 else 0.0
    return volume * 3.0 + trades * 250.0 + near_50_bonus


def _trade_yes_price(trade: dict[str, Any]) -> float | None:
    price = _as_float_or_none(
        trade.get("yesPrice")
        or trade.get("yes_price")
        or trade.get("latestPrice")
        or trade.get("price")
    )
    if price is None or price < 0 or price > 1:
        return None
    outcome = str(trade.get("outcome") or trade.get("assetName") or trade.get("asset_name") or "").strip().lower()
    if outcome == "no":
        return 1.0 - price
    return price


def _trade_size(trade: dict[str, Any]) -> float:
    size = _as_float_or_none(trade.get("size") or trade.get("amount") or trade.get("shares"))
    return size if size is not None and size > 0 else 1.0


def _trade_timestamp(trade: dict[str, Any]) -> Any:
    return trade.get("timestamp") or trade.get("createdAt") or trade.get("created_at") or trade.get("blockTimestamp")


def _freshness_label(timestamp: Any) -> tuple[str, int | None]:
    parsed = _parse_iso(str(timestamp)) if timestamp else None
    if parsed is None:
        return "unknown", None
    seconds = int(max(0.0, (_utc_now() - parsed).total_seconds()))
    if seconds <= 30 * 60:
        return "fresh-fills", seconds
    if seconds <= 24 * 3600:
        return "same-day-fills", seconds
    return "stale-fills", seconds


def _paired_fill_ratio(trades: list[Any]) -> float | None:
    tx_outcomes: dict[str, set[str]] = {}
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        tx_hash = trade.get("txHash") or trade.get("tx_hash") or trade.get("transactionHash")
        if not tx_hash:
            continue
        outcome = str(trade.get("outcome") or trade.get("assetName") or "").strip().upper()
        if outcome not in {"YES", "NO"}:
            continue
        tx_outcomes.setdefault(str(tx_hash), set()).add(outcome)
    if not tx_outcomes:
        return None
    paired = sum(1 for outcomes in tx_outcomes.values() if {"YES", "NO"}.issubset(outcomes))
    return round(paired / len(tx_outcomes), 4)


def _price_source_conflict(prices: list[float | None]) -> bool:
    usable = [price for price in prices if price is not None and 0 <= price <= 1]
    return len(usable) >= 2 and max(usable) - min(usable) >= 0.05


def _fill_tape_for_market(helpers: dict[str, Any], market: dict[str, Any], trade_limit: int) -> dict[str, Any] | None:
    market_id = _market_id(market)
    if market_id in (None, ""):
        return None
    call_id = _callable_market_id(market_id)
    detail = _safe_call(helpers, "get_market_by_id", {}, call_id)
    trades = _items(_safe_call(helpers, "get_trades_by_market_id", [], call_id, trade_limit, 0))
    prices = [_trade_yes_price(trade) for trade in trades if isinstance(trade, dict)]
    prices = [price for price in prices if price is not None]
    if not prices and not isinstance(detail, dict):
        return None

    weighted_total = 0.0
    size_total = 0.0
    recent_fills: list[dict[str, Any]] = []
    for trade in trades[:12]:
        if not isinstance(trade, dict):
            continue
        yes_price = _trade_yes_price(trade)
        size = _trade_size(trade)
        if yes_price is not None:
            weighted_total += yes_price * size
            size_total += size
        recent_fills.append({
            "timestamp": _trade_timestamp(trade),
            "outcome": trade.get("outcome") or trade.get("assetName"),
            "side": trade.get("side") or trade.get("type"),
            "price": trade.get("price"),
            "yesPrice": round(yes_price, 6) if yes_price is not None else None,
            "size": trade.get("size") or trade.get("amount") or trade.get("shares"),
            "txHash": trade.get("txHash") or trade.get("tx_hash") or trade.get("transactionHash"),
        })

    detail = detail if isinstance(detail, dict) else {}
    snapshot_price = _as_float_or_none(market.get("latestPrice"))
    detail_latest = _as_float_or_none(detail.get("latestPrice") or detail.get("latest_price"))
    detail_yes = _as_float_or_none(detail.get("latestYesPrice") or detail.get("latest_yes_price"))
    detail_no = _as_float_or_none(detail.get("latestNoPrice") or detail.get("latest_no_price"))
    detail_no_as_yes = 1.0 - detail_no if detail_no is not None and 0 <= detail_no <= 1 else None
    latest_fill = prices[0] if prices else None
    oldest_loaded_fill = prices[-1] if prices else None
    fill_drift = latest_fill - oldest_loaded_fill if latest_fill is not None and oldest_loaded_fill is not None else None
    price_range = max(prices) - min(prices) if prices else None
    last_fill_at = _trade_timestamp(trades[0]) if trades and isinstance(trades[0], dict) else None
    freshness, freshness_seconds = _freshness_label(last_fill_at)
    conflict = _price_source_conflict([snapshot_price, detail_latest, detail_yes, detail_no_as_yes, latest_fill])
    paired_ratio = _paired_fill_ratio(trades)
    return {
        "marketId": market_id,
        "conditionId": market.get("conditionId") or detail.get("conditionId"),
        "title": market.get("title") or detail.get("title") or detail.get("slug") or "Untitled market",
        "category": market.get("category") or detail.get("category") or "market",
        "snapshotLatestPrice": snapshot_price,
        "snapshotPrice24hAgo": market.get("price24hAgo"),
        "snapshotChange24h": market.get("change24h"),
        "snapshotVolume24h": market.get("volume24h"),
        "snapshotTradeCount24h": market.get("tradeCount24h"),
        "detailLatestPrice": detail_latest,
        "detailYesPrice": detail_yes,
        "detailNoPrice": detail_no,
        "detailNoAsYesPrice": detail_no_as_yes,
        "detailUpdatedAt": detail.get("updatedAt") or detail.get("updated_at"),
        "fillCountLoaded": len(prices),
        "latestFillYesPrice": latest_fill,
        "oldestLoadedFillYesPrice": oldest_loaded_fill,
        "recentFillDrift": fill_drift,
        "fillYesPriceMin": min(prices) if prices else None,
        "fillYesPriceMax": max(prices) if prices else None,
        "fillYesPriceRange": price_range,
        "fillVwapYesPrice": (weighted_total / size_total) if size_total > 0 else None,
        "lastFillAt": last_fill_at,
        "fillFreshness": freshness,
        "fillFreshnessSeconds": freshness_seconds,
        "pairedFillRatio": paired_ratio,
        "priceSourceConflict": conflict,
        "recentFills": recent_fills,
    }


def _top_market_fill_tape_items(helpers: dict[str, Any], markets: list[Any]) -> list[dict[str, Any]]:
    market_limit, trade_limit = _market_fill_tape_limits()
    if market_limit <= 0:
        return []
    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in markets:
        if not isinstance(item, dict):
            continue
        market_id = _market_id(item)
        if market_id in (None, ""):
            continue
        key = str(market_id)
        if key in seen:
            continue
        seen.add(key)
        ranked.append(item)
    output: list[dict[str, Any]] = []
    for market in sorted(ranked, key=_market_fill_tape_rank, reverse=True)[:market_limit]:
        item = _fill_tape_for_market(helpers, market, trade_limit)
        if item is not None:
            output.append(item)
    return output


def _safe_call(helpers: dict[str, Any], name: str, default: Any, *args: Any, **kwargs: Any) -> Any:
    fn = helpers.get(name)
    if not callable(fn):
        return default
    try:
        return fn(*args, **kwargs)
    except Exception:
        logger = getattr(helpers.get("app"), "logger", None)
        if logger is not None:
            logger.exception("agent snapshot source failed source=%s", name)
        return default


def _read_cached_snapshot(helpers: dict[str, Any], namespace: str, cache_key: str) -> dict[str, Any]:
    getter = helpers.get("get_cached_json")
    if callable(getter):
        cached = getter(namespace, cache_key)
        if isinstance(cached, dict):
            return cached
    store = helpers.get("SNAPSHOT_STORE")
    if store is not None and hasattr(store, "get"):
        fresh = store.get(namespace, cache_key)
        if isinstance(fresh, dict):
            return fresh
    if store is not None and hasattr(store, "get_stale"):
        stale = store.get_stale(namespace, cache_key)
        if isinstance(stale, dict):
            return stale
    return {}


def _json_key(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True)


def _json_safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, ensure_ascii=True, default=str))


def _cached_signal_items(helpers: dict[str, Any], namespace: str, cache_key: str, limit: int) -> list[Any]:
    return _items(_read_cached_snapshot(helpers, namespace, cache_key))[:limit]


def _forecast_memory_items(helpers: dict[str, Any], lens: str, limit: int = 24) -> list[Any]:
    store = helpers.get("SNAPSHOT_STORE")
    if store is not None and hasattr(store, "get_agent_forecast_memory"):
        try:
            items = store.get_agent_forecast_memory(lens, limit=limit)
            if isinstance(items, list):
                return items[:limit]
        except Exception:
            logger = getattr(helpers.get("app"), "logger", None)
            if logger is not None:
                logger.exception("agent forecast memory read failed lens=%s", lens)
    return []


def _snapshot_age_seconds(snapshot: dict[str, Any]) -> float | None:
    data = snapshot.get("data") if isinstance(snapshot.get("data"), dict) else {}
    generated_at = data.get("snapshotGeneratedAt") or data.get("generatedAt") or snapshot.get("generatedAt")
    parsed = _parse_iso(generated_at)
    if parsed is None:
        return None
    return max(0.0, (_utc_now() - parsed).total_seconds())


def _return_skipped_snapshot(snapshot: dict[str, Any], reason: str) -> dict[str, Any]:
    skipped = dict(snapshot)
    data = dict(skipped.get("data") or {})
    data["source"] = "agent-snapshot"
    data["seedSkipped"] = True
    data["skipReason"] = reason
    data["snapshotLiveAttempted"] = False
    skipped["data"] = data
    skipped["skipped"] = True
    skipped["skipReason"] = reason
    skipped["liveAttempted"] = False
    return skipped


def build_market_wide_seed_payload(helpers: dict[str, Any], lens: Any, *, run_id: str | None = None) -> dict[str, Any]:
    normalized_lens = normalize_lens(lens)
    active_markets = _safe_call(helpers, "get_active_markets_snapshot", {}, 80, False, True)
    if not _items(active_markets):
        active_markets = _safe_call(helpers, "get_active_markets_snapshot", {}, 80)
    market_groups = _safe_call(helpers, "get_market_groups_payload", {}, "", 1, 60, "active")
    content = _safe_call(helpers, "get_latest_content_payload", {}, 12)
    markets = _items(active_markets)[:80]
    payload = {
        "lens": normalized_lens,
        "markets": markets,
        "marketGroups": _items(market_groups)[:60],
        "topMarketFillTape": _top_market_fill_tape_items(helpers, markets),
        "trades": _safe_call(helpers, "get_recent_trades_snapshot", [], 24)[:24],
        "oracle": _safe_call(helpers, "get_recent_oracle_snapshot", [], 24)[:24],
        "content": _items(content)[:12],
        "alphaSignals": _cached_signal_items(helpers, SIGNAL_SNAPSHOT_NAMESPACE_ALPHA, _json_key({"limit": 8}), 8),
        "whaleSignals": _cached_signal_items(
            helpers,
            SIGNAL_SNAPSHOT_NAMESPACE_WHALES,
            _json_key({"limit": 14, "lookbackDays": 7}),
            10,
        ),
        "suspiciousSignals": _cached_signal_items(helpers, SIGNAL_SNAPSHOT_NAMESPACE_SUSPICIOUS, _json_key({"limit": 12}), 10),
        "forecastMemory": _forecast_memory_items(helpers, normalized_lens, limit=24),
    }
    payload = _json_safe_payload(payload)
    payload["forecastRunId"] = run_id or forecast_run_id(payload)
    return payload


def _seed_live_enabled() -> bool:
    return _truthy_env("POLYDATA_AGENT_SEED_ENABLED", False) and _truthy_env("POLYDATA_AGENT_ENABLED", False)


def _snapshot_from_insight(lens: str, insight: dict[str, Any], *, live_attempted: bool, budget: dict[str, Any] | None) -> dict[str, Any]:
    now = _utc_now()
    ttl = snapshot_ttl_seconds()
    data = dict(insight)
    data["lens"] = lens
    data["cacheStatus"] = "snapshot"
    data["source"] = "agent-snapshot"
    data["snapshotGeneratedAt"] = data.get("generatedAt") or _iso(now)
    data["snapshotExpiresAt"] = _iso(now + timedelta(seconds=ttl))
    data["snapshotLiveAttempted"] = live_attempted
    if budget is not None:
        data["dailyBudget"] = budget
    return {
        "schemaVersion": 1,
        "lens": lens,
        "generatedAt": data["snapshotGeneratedAt"],
        "expiresAt": data["snapshotExpiresAt"],
        "liveAttempted": live_attempted,
        "budget": budget,
        "data": data,
    }


def build_market_wide_snapshot(
    helpers: dict[str, Any],
    lens: Any,
    *,
    live: bool = True,
    force: bool = False,
    run_id: str | None = None,
) -> dict[str, Any]:
    normalized_lens = normalize_lens(lens)
    existing = read_market_wide_snapshot(helpers, normalized_lens, allow_stale=True)
    if existing is not None and not force:
        age_seconds = _snapshot_age_seconds(existing)
        if age_seconds is not None and age_seconds < snapshot_min_live_interval_seconds():
            return _return_skipped_snapshot(existing, "fresh-snapshot")
        if not (live and _seed_live_enabled()):
            return _return_skipped_snapshot(existing, "live-disabled-existing-snapshot")

    payload = build_market_wide_seed_payload(helpers, normalized_lens, run_id=run_id)
    live_allowed = bool(live and _seed_live_enabled())
    budget: dict[str, Any] | None = None
    if live_allowed:
        if gateway_configured():
            budget = {
                "enabled": True,
                "delegatedToGateway": True,
                "kind": f"market-wide-seed:{normalized_lens}",
            }
        else:
            live_allowed, budget = claim_agent_live_call(f"market-wide-seed:{normalized_lens}")
    if live_allowed:
        try:
            insight = call_market_wide_insight_gateway(payload) if gateway_configured() else build_market_wide_insight(payload)
        except Exception as exc:
            reason = "gateway-error" if gateway_configured() else "agent-error"
            insight = build_market_wide_fallback(payload, reason=reason)
            insight["error"] = str(exc)[:240]
        insight.setdefault("forecastRunId", payload.get("forecastRunId"))
        return _snapshot_from_insight(normalized_lens, insight, live_attempted=True, budget=budget)
    reason = "seed-disabled" if live else "fallback-only"
    if budget is not None and budget.get("enabled"):
        reason = "seed-budget-exhausted"
    insight = build_market_wide_fallback(payload, reason=reason)
    return _snapshot_from_insight(normalized_lens, insight, live_attempted=False, budget=budget)


def store_market_wide_snapshot(helpers: dict[str, Any], snapshot: dict[str, Any]) -> None:
    lens = normalize_lens(snapshot.get("lens"))
    key = snapshot_cache_key(lens)
    ttl = snapshot_ttl_seconds()
    setter = helpers.get("set_cached_json")
    if callable(setter):
        setter(SNAPSHOT_NAMESPACE, key, snapshot, ttl)
    store = helpers.get("SNAPSHOT_STORE")
    if store is not None and hasattr(store, "set"):
        store.set(SNAPSHOT_NAMESPACE, key, snapshot, ttl)
    store_market_wide_quant_snapshot(helpers, snapshot)
    store_market_wide_agent_events(helpers, snapshot)
    store_market_wide_forecast_memory(helpers, snapshot)


def _quant_snapshot_from_market_wide_snapshot(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    data = snapshot.get("data")
    if not isinstance(data, dict):
        return None
    graph = data.get("agentGraph")
    if not isinstance(graph, dict):
        return None
    quant = graph.get("quantForecaster")
    related = graph.get("relatedMarkets")
    if not isinstance(quant, dict) and not isinstance(related, dict):
        return None
    lens = normalize_lens(snapshot.get("lens") or data.get("lens"))
    run_id = data.get("forecastRunId") or graph.get("runId")
    generated_at = data.get("snapshotGeneratedAt") or data.get("generatedAt") or snapshot.get("generatedAt")
    return {
        "schemaVersion": 1,
        "lens": lens,
        "runId": run_id,
        "generatedAt": generated_at,
        "expiresAt": snapshot.get("expiresAt") or data.get("snapshotExpiresAt"),
        "status": data.get("status"),
        "model": data.get("model"),
        "graphVersion": data.get("agentArchitecture") or graph.get("version"),
        "source": "agent-quant-snapshot",
        "sourceSnapshotKey": snapshot_cache_key(lens),
        "quantForecaster": quant if isinstance(quant, dict) else {},
        "relatedMarkets": related if isinstance(related, dict) else {},
    }


def store_market_wide_quant_snapshot(helpers: dict[str, Any], snapshot: dict[str, Any]) -> None:
    payload = _quant_snapshot_from_market_wide_snapshot(snapshot)
    if payload is None:
        return
    lens = normalize_lens(payload.get("lens"))
    latest_key = quant_snapshot_cache_key(lens)
    history_key = quant_history_cache_key(lens, payload.get("runId"))
    ttl = quant_snapshot_ttl_seconds()
    setter = helpers.get("set_cached_json")
    if callable(setter):
        setter(QUANT_SNAPSHOT_NAMESPACE, latest_key, payload, ttl)
        setter(QUANT_HISTORY_NAMESPACE, history_key, payload, ttl)
    store = helpers.get("SNAPSHOT_STORE")
    if store is not None and hasattr(store, "set"):
        store.set(QUANT_SNAPSHOT_NAMESPACE, latest_key, payload, ttl)
        store.set(QUANT_HISTORY_NAMESPACE, history_key, payload, ttl)


def _agent_events_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    data = snapshot.get("data") if isinstance(snapshot.get("data"), dict) else {}
    graph = data.get("agentGraph") if isinstance(data.get("agentGraph"), dict) else {}
    events = graph.get("events") if isinstance(graph.get("events"), list) else []
    lens = normalize_lens(snapshot.get("lens") or data.get("lens"))
    run_id = data.get("forecastRunId") or graph.get("runId")
    output_by_node = {
        "evidence_builder": graph.get("evidenceBuilder"),
        "quant_forecaster": graph.get("quantForecaster"),
        "related_markets": graph.get("relatedMarkets"),
        "reflexion_memory": graph.get("reflexionMemory"),
        "calibration_agent": graph.get("calibrationAgent"),
        "skeptic": graph.get("calibration"),
    }
    for specialist in graph.get("specialists") or []:
        if isinstance(specialist, dict) and specialist.get("node"):
            output_by_node[str(specialist["node"])] = specialist
    normalized: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        item = dict(event)
        node = str(item.get("node") or "")
        item.setdefault("runId", run_id)
        item.setdefault("lens", lens)
        if "outputJson" not in item and isinstance(output_by_node.get(node), dict):
            item["outputJson"] = output_by_node[node]
        normalized.append(item)
    return normalized


def store_market_wide_agent_events(helpers: dict[str, Any], snapshot: dict[str, Any]) -> None:
    store = helpers.get("SNAPSHOT_STORE")
    if store is None or not hasattr(store, "record_agent_node_events"):
        return
    events = _agent_events_from_snapshot(snapshot)
    if not events:
        return
    try:
        store.record_agent_node_events(events)
    except Exception:
        logger = getattr(helpers.get("app"), "logger", None)
        if logger is not None:
            logger.exception("agent node event log write failed")


def store_market_wide_forecast_memory(helpers: dict[str, Any], snapshot: dict[str, Any]) -> None:
    data = snapshot.get("data") if isinstance(snapshot.get("data"), dict) else {}
    graph = data.get("agentGraph") if isinstance(data.get("agentGraph"), dict) else {}
    memory = graph.get("reflexionMemory") if isinstance(graph.get("reflexionMemory"), dict) else {}
    episodes = memory.get("newEpisodes") if isinstance(memory.get("newEpisodes"), list) else []
    if not episodes:
        return
    store = helpers.get("SNAPSHOT_STORE")
    if store is None or not hasattr(store, "upsert_agent_forecast_memory"):
        return
    try:
        store.upsert_agent_forecast_memory(episodes)
    except Exception:
        logger = getattr(helpers.get("app"), "logger", None)
        if logger is not None:
            logger.exception("agent forecast memory write failed")


def read_market_wide_quant_snapshot(helpers: dict[str, Any], lens: Any, *, allow_stale: bool = True) -> dict[str, Any] | None:
    key = quant_snapshot_cache_key(lens)
    getter = helpers.get("get_cached_json")
    if callable(getter):
        cached = getter(QUANT_SNAPSHOT_NAMESPACE, key)
        if isinstance(cached, dict):
            return cached
    store = helpers.get("SNAPSHOT_STORE")
    if store is not None and hasattr(store, "get"):
        cached = store.get(QUANT_SNAPSHOT_NAMESPACE, key)
        if isinstance(cached, dict):
            return cached
    if allow_stale and store is not None and hasattr(store, "get_stale"):
        stale = store.get_stale(QUANT_SNAPSHOT_NAMESPACE, key)
        if isinstance(stale, dict):
            payload = dict(stale)
            payload["cacheStatus"] = "stale-snapshot"
            return payload
    return None


def read_market_wide_snapshot(helpers: dict[str, Any], lens: Any, *, allow_stale: bool = True) -> dict[str, Any] | None:
    key = snapshot_cache_key(lens)
    getter = helpers.get("get_cached_json")
    if callable(getter):
        cached = getter(SNAPSHOT_NAMESPACE, key)
        if isinstance(cached, dict):
            return cached
    store = helpers.get("SNAPSHOT_STORE")
    if store is not None and hasattr(store, "get"):
        cached = store.get(SNAPSHOT_NAMESPACE, key)
        if isinstance(cached, dict):
            return cached
    if allow_stale and store is not None and hasattr(store, "get_stale"):
        stale = store.get_stale(SNAPSHOT_NAMESPACE, key)
        if isinstance(stale, dict):
            stale = dict(stale)
            data = dict(stale.get("data") or {})
            data["cacheStatus"] = "stale-snapshot"
            data["source"] = "agent-snapshot"
            stale["data"] = data
            return stale
    return None


def snapshot_response(snapshot: dict[str, Any]) -> dict[str, Any]:
    data = snapshot.get("data")
    if not isinstance(data, dict):
        return {}
    response = dict(data)
    response.setdefault("lens", normalize_lens(snapshot.get("lens")))
    response.setdefault("source", "agent-snapshot")
    response.setdefault("cacheStatus", "snapshot")
    response.setdefault("snapshotGeneratedAt", snapshot.get("generatedAt"))
    response.setdefault("snapshotExpiresAt", snapshot.get("expiresAt"))
    return response


def seed_market_wide_snapshots(
    helpers: dict[str, Any],
    lenses: list[str] | tuple[str, ...] = DEFAULT_LENSES,
    *,
    live: bool = True,
    force: bool = False,
) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    run_id = f"fig-seed-{_iso(_utc_now()).replace(':', '').replace('-', '')}"
    for lens in lenses:
        snapshot = build_market_wide_snapshot(helpers, lens, live=live, force=force, run_id=run_id)
        if not snapshot.get("skipped"):
            store_market_wide_snapshot(helpers, snapshot)
        snapshots.append(snapshot)
    return snapshots
