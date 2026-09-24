from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone
from typing import Any, Callable

from agent.common.env import get_bool_env, get_int_env
from agent.common.json_utils import compact_text, extract_json_object


GRAPH_VERSION = "forecast-intelligence-graph-v2"
SPECIALIST_NODES = ("microstructure", "catalyst", "resolution")
FORECAST_ANALYST_RULES = """
Prediction-market usefulness rules:
- Do not write generic dashboard observations such as "activity is broad", "sports supplies count", or "liquidity is uneven" unless immediately tied to a named market and a price/volume/trade-count implication.
- Prefer market-level theses: named market, current implied probability, volume/trade-count, deadline or outcome bucket, catalyst, and resolution caveat.
- Surface probability structure: near-50c repricing zones, deadline ladders, term spreads, mutually related markets, one-sided liquidity, stale prices, and group-vs-single-market mismatches.
- Explain what would move the price next: official source, match result, court/government release, shipping/oil data, oracle update, or new large fills.
- Say "no directional edge" when price-change data is missing; then identify what data would be needed. Do not fill the gap with category commentary.
- Keep all claims informational. Do not recommend trades, position sizing, or financial advice.
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_hash(payload: Any) -> str:
    try:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    except Exception:
        raw = str(payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _as_float(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric if numeric == numeric else 0.0


def _fmt_compact(value: Any) -> str:
    numeric = _as_float(value)
    if abs(numeric) >= 1_000_000:
        return f"{numeric / 1_000_000:.1f}M"
    if abs(numeric) >= 1_000:
        return f"{numeric / 1_000:.1f}K"
    if numeric == int(numeric):
        return str(int(numeric))
    return f"{numeric:.1f}"


def _fmt_price(value: Any) -> str:
    numeric = _as_float(value)
    if numeric <= 0:
        return "n/a"
    return f"{numeric * 100:.1f}%"


def _fmt_money(value: Any) -> str:
    return f"${_fmt_compact(value)}"


def forecast_run_id(payload: dict[str, Any]) -> str:
    explicit = payload.get("forecastRunId") or payload.get("forecast_run_id")
    if explicit:
        return compact_text(explicit, 48)
    stable = {key: value for key, value in payload.items() if key not in {"lens", "forecastRunId", "forecast_run_id"}}
    return f"fig-{_json_hash(stable)}"


def graph_enabled() -> bool:
    return get_bool_env("POLYDATA_AGENT_MARKET_WIDE_GRAPH_ENABLED", True)


def langgraph_enabled() -> bool:
    return get_bool_env("POLYDATA_AGENT_MARKET_WIDE_LANGGRAPH_ENABLED", True)


def react_tools_enabled() -> bool:
    return get_bool_env("POLYDATA_AGENT_MARKET_WIDE_REACT_TOOLS_ENABLED", True)


def _agent_limit() -> int:
    return max(0, min(4, get_int_env("POLYDATA_AGENT_MARKET_WIDE_GRAPH_AGENT_LIMIT", 4)))


def _compact_findings(items: Any, limit: int = 4) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    output: list[dict[str, str]] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        output.append({
            "label": compact_text(item.get("label") or "SIGNAL", 16).upper(),
            "title": compact_text(item.get("title") or "Market signal", 90),
            "summary": compact_text(item.get("summary") or item.get("reason") or "", 180),
            "severity": compact_text(item.get("severity") or "neutral", 20).lower(),
            "evidence": compact_text(item.get("evidence") or "", 90),
        })
    return output


def _compact_string_list(items: Any, limit: int = 4) -> list[str]:
    if not isinstance(items, list):
        return []
    return [compact_text(item, 140) for item in items[:limit] if item]


def _normalize_node_output(raw: dict[str, Any], node: str) -> dict[str, Any]:
    return {
        "node": node,
        "findings": _compact_findings(raw.get("findings")),
        "risks": _compact_string_list(raw.get("risks")),
        "watch": _compact_string_list(raw.get("watch")),
        "confidence": compact_text(raw.get("confidence") or raw.get("confidenceLabel") or "medium", 24).lower(),
        "probabilityAdjustment": compact_text(raw.get("probabilityAdjustment") or raw.get("priceAdjustment") or "", 80),
    }


def _compact_quant_for_prompt(quant: dict[str, Any]) -> dict[str, Any]:
    return {
        "topFlowMarkets": (quant.get("topFlowMarkets") or [])[:2],
        "repricingZones": (quant.get("repricingZones") or [])[:2],
        "priceDriftLeaders": (quant.get("priceDriftLeaders") or [])[:2],
        "volatilityLeaders": (quant.get("volatilityLeaders") or [])[:2],
        "fillTapeMicrostructure": (quant.get("fillTapeMicrostructure") or [])[:2],
        "priceSourceConflicts": (quant.get("priceSourceConflicts") or [])[:2],
        "lobSpreads": (quant.get("lobSpreads") or [])[:2],
        "relatedMarketArbitrageScores": (quant.get("relatedMarketArbitrageScores") or [])[:2],
        "anomalies": (quant.get("anomalies") or [])[:2],
        "dataWarnings": (quant.get("dataWarnings") or [])[:2],
    }


def _compact_related_for_prompt(related: dict[str, Any]) -> dict[str, Any]:
    return {
        "ladders": (related.get("ladders") or [])[:3],
        "arbitrageScores": (related.get("arbitrageScores") or [])[:3],
        "anomalies": (related.get("anomalies") or [])[:2],
    }


def _market_prompt_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    return {
        "title": compact_text(item.get("title") or item.get("market") or item.get("question"), 100),
        "category": compact_text(item.get("category"), 32),
        "price": item.get("latestPrice") or item.get("yesPrice"),
        "price24hAgo": item.get("price24hAgo"),
        "change24h": item.get("change24h"),
        "volume24h": item.get("volume24h"),
        "tradeCount24h": item.get("tradeCount24h"),
        "bid": item.get("bestBid"),
        "ask": item.get("bestAsk"),
        "endDate": item.get("endDate"),
    }


def _compact_prompt_list(items: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    output: list[dict[str, Any]] = []
    for item in items[:limit]:
        compacted = _market_prompt_item(item)
        if compacted:
            output.append(compacted)
    return output


def _compact_fill_tape_for_prompt(items: Any, limit: int = 3) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    output: list[dict[str, Any]] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        output.append({
            "title": compact_text(item.get("title"), 100),
            "snapshotLatestPrice": item.get("snapshotLatestPrice"),
            "detailLatestPrice": item.get("detailLatestPrice"),
            "detailYesPrice": item.get("detailYesPrice"),
            "latestFillYesPrice": item.get("latestFillYesPrice"),
            "fillVwapYesPrice": item.get("fillVwapYesPrice"),
            "recentFillDrift": item.get("recentFillDrift"),
            "fillYesPriceRange": item.get("fillYesPriceRange"),
            "fillCountLoaded": item.get("fillCountLoaded"),
            "lastFillAt": item.get("lastFillAt"),
            "fillFreshness": item.get("fillFreshness"),
            "pairedFillRatio": item.get("pairedFillRatio"),
            "priceSourceConflict": item.get("priceSourceConflict"),
        })
    return output


def _compact_memory_for_prompt(items: Any, limit: int = 3) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    output: list[dict[str, Any]] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        observation = item.get("observation") if isinstance(item.get("observation"), dict) else {}
        output.append({
            "kind": item.get("kind"),
            "title": compact_text(item.get("title"), 100),
            "lesson": compact_text(item.get("lesson"), 140),
            "score": observation.get("score"),
            "drift24h": observation.get("drift24h"),
            "spread": observation.get("spread"),
        })
    return output


def _compact_agent_for_prompt(agent: Any) -> dict[str, Any] | None:
    if not isinstance(agent, dict):
        return None
    return {
        "node": agent.get("node"),
        "confidence": agent.get("confidence"),
        "findings": (agent.get("findings") or [])[:2],
        "risks": _compact_string_list(agent.get("risks"), 2),
        "watch": _compact_string_list(agent.get("watch"), 2),
        "probabilityAdjustment": compact_text(agent.get("probabilityAdjustment"), 80),
    }


def _compact_agents_for_prompt(agents: Any, limit: int = 4) -> list[dict[str, Any]]:
    if not isinstance(agents, list):
        return []
    output: list[dict[str, Any]] = []
    for agent in agents[:limit]:
        compacted = _compact_agent_for_prompt(agent)
        if compacted:
            output.append(compacted)
    return output


def _compact_react_observation(tool_name: str, observation: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "quant_snapshot":
        quant = observation.get("quantForecaster") if isinstance(observation.get("quantForecaster"), dict) else {}
        related = observation.get("relatedMarkets") if isinstance(observation.get("relatedMarkets"), dict) else {}
        return {
            "repricingZones": (quant.get("repricingZones") or [])[:2],
            "priceDriftLeaders": (quant.get("priceDriftLeaders") or [])[:2],
            "lobSpreads": (quant.get("lobSpreads") or [])[:2],
            "arbitrageScores": (related.get("arbitrageScores") or [])[:2],
            "warnings": (quant.get("dataWarnings") or [])[:2],
        }
    if tool_name == "catalyst_scan":
        return {
            "content": [
                {
                    "title": compact_text(item.get("title"), 90),
                    "summary": compact_text(item.get("summary"), 120),
                    "publishedAt": item.get("publishedAt"),
                }
                for item in observation.get("content", [])[:2]
                if isinstance(item, dict)
            ],
            "watchSignals": [
                {"title": compact_text(item.get("title"), 90), "summary": compact_text(item.get("summary"), 120)}
                for item in observation.get("watchSignals", [])[:2]
                if isinstance(item, dict)
            ],
        }
    if tool_name == "memory_scan":
        return {"priorEpisodes": _compact_memory_for_prompt(observation.get("priorEpisodes"), 3)}
    if tool_name == "resolution_scan":
        return {
            "deadlineGroups": observation.get("deadlineGroups", [])[:3],
            "oracle": [
                {"title": compact_text(item.get("title"), 90), "status": item.get("status")}
                for item in observation.get("oracle", [])[:2]
                if isinstance(item, dict)
            ],
        }
    return observation


def _evidence_refs(payload: Any, limit: int = 6) -> list[str]:
    refs: list[str] = []
    if isinstance(payload, dict):
        for key in ("title", "evidence", "summary", "probabilityAdjustment"):
            value = payload.get(key)
            if value:
                refs.append(compact_text(value, 120))
        for key in ("findings", "risks", "watch", "topFlowMarkets", "repricingZones", "priceDriftLeaders", "arbitrageScores"):
            value = payload.get(key)
            if isinstance(value, list):
                for item in value[:2]:
                    refs.extend(_evidence_refs(item, limit=2))
    elif isinstance(payload, list):
        for item in payload[:2]:
            refs.extend(_evidence_refs(item, limit=2))
    return list(dict.fromkeys(refs))[:limit]


def _node_event(
    *,
    run_id: str,
    lens: str,
    node: str,
    output: dict[str, Any],
    status: str = "ok",
    input_hash: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    latency_ms: int = 0,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "runId": run_id,
        "lens": lens,
        "node": node,
        "status": status,
        "startedAt": started_at,
        "finishedAt": finished_at or _utc_now_iso(),
        "latencyMs": latency_ms,
        "inputHash": input_hash or _json_hash(output),
        "outputHash": _json_hash(output),
        "outputJson": output,
        "error": error,
        "evidenceRefs": _evidence_refs(output),
    }


def _deterministic_evidence(context: dict[str, Any], lens: str) -> dict[str, Any]:
    metrics = context.get("metrics") if isinstance(context.get("metrics"), dict) else {}
    candidates = context.get("marketCandidates") if isinstance(context.get("marketCandidates"), list) else []
    top = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
    top_categories = metrics.get("topCategories") if isinstance(metrics.get("topCategories"), list) else []
    return {
        "node": "evidence_builder",
        "lens": lens,
        "metrics": metrics,
        "topMarket": {
            "title": compact_text(top.get("title") or "No standout market", 100),
            "category": compact_text(top.get("category") or "market", 40),
            "volume24h": top.get("volume24h"),
            "tradeCount24h": top.get("tradeCount24h"),
            "latestPrice": top.get("latestPrice"),
        },
        "categoryConcentration": ", ".join(str(item) for item in top_categories[:4]) or "categories loading",
        "inputHash": _json_hash({
            "metrics": metrics,
            "candidates": candidates[:12],
            "search": context.get("searchResults", [])[:2],
        }),
    }


def _market_rows(context: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in ("marketCandidates", "markets"):
        values = context.get(key) if isinstance(context.get(key), list) else []
        for item in values:
            if not isinstance(item, dict):
                continue
            title = compact_text(item.get("title") or item.get("market") or "Untitled market", 120)
            if not title or title in seen:
                continue
            seen.add(title)
            rows.append({
                "id": item.get("id") or item.get("localMarketId"),
                "conditionId": item.get("conditionId"),
                "title": title,
                "category": compact_text(item.get("category") or "market", 40),
                "price": _as_float(item.get("latestPrice") or item.get("yesPrice") or item.get("price")),
                "price24hAgo": item.get("price24hAgo") or item.get("price_24h_ago"),
                "volume24h": _as_float(item.get("volume24h")),
                "tradeCount24h": _as_float(item.get("tradeCount24h")),
                "change24h": item.get("change24h"),
                "bestBid": item.get("bestBid") or item.get("bid") or item.get("yesBid"),
                "bestAsk": item.get("bestAsk") or item.get("ask") or item.get("yesAsk"),
                "endDate": item.get("endDate"),
                "kind": item.get("kind"),
            })
    return rows


def _price_band(price: float) -> str:
    if price <= 0:
        return "missing-price"
    if 0.48 <= price <= 0.52:
        return "50c-repricing-zone"
    if 0.42 <= price <= 0.58:
        return "contested-probability"
    if price >= 0.9:
        return "near-certain"
    if price <= 0.1:
        return "long-shot"
    return "priced-view"


def _price_drift(item: dict[str, Any]) -> float | None:
    current = _as_float(item.get("price"))
    previous = _as_float(item.get("price24hAgo"))
    if current > 0 and previous > 0:
        return current - previous
    change = item.get("change24h")
    if change in (None, "", "null"):
        return None
    if isinstance(change, str):
        cleaned = change.strip().lower().replace("%", "").replace("pts", "").replace("pt", "")
        if not cleaned:
            return None
        try:
            parsed = float(cleaned)
        except ValueError:
            return None
        return parsed / 100 if "%" in change else parsed
    try:
        parsed = float(change)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _lob_spread(item: dict[str, Any]) -> tuple[float, float, float] | None:
    bid = _as_float(item.get("bestBid"))
    ask = _as_float(item.get("bestAsk"))
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    return bid, ask, ask - bid


def _trade_volatility(context: dict[str, Any]) -> list[dict[str, Any]]:
    trades = context.get("trades") if isinstance(context.get("trades"), list) else []
    grouped: dict[str, list[float]] = {}
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        title = compact_text(trade.get("market") or trade.get("marketTitle") or trade.get("title"), 120)
        price = _as_float(trade.get("price") or trade.get("latestPrice"))
        if not title or price <= 0 or price > 1:
            continue
        grouped.setdefault(title, []).append(price)

    output: list[dict[str, Any]] = []
    for title, prices in grouped.items():
        if len(prices) < 3:
            continue
        mean = sum(prices) / len(prices)
        variance = sum((price - mean) ** 2 for price in prices) / len(prices)
        stdev = math.sqrt(variance)
        price_range = max(prices) - min(prices)
        output.append({
            "title": title,
            "tradeCount": len(prices),
            "volatility": f"{stdev * 100:.1f} pts",
            "priceRange": f"{price_range * 100:.1f} pts",
            "latestTradePrice": _fmt_price(prices[-1]),
            "interpretation": "Recent fills show enough price dispersion to deserve a catalyst or liquidity check.",
            "_rank": stdev * 10 + price_range,
        })
    return [
        {key: value for key, value in item.items() if key != "_rank"}
        for item in sorted(output, key=lambda item: item["_rank"], reverse=True)[:6]
    ]


def _trade_price_drift(context: dict[str, Any]) -> list[dict[str, Any]]:
    trades = context.get("trades") if isinstance(context.get("trades"), list) else []
    grouped: dict[str, list[float]] = {}
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        title = compact_text(trade.get("market") or trade.get("marketTitle") or trade.get("title"), 120)
        price = _as_float(trade.get("price") or trade.get("latestPrice"))
        if not title or price <= 0 or price > 1:
            continue
        grouped.setdefault(title, []).append(price)

    output: list[dict[str, Any]] = []
    for title, prices in grouped.items():
        if len(prices) < 2:
            continue
        drift = prices[-1] - prices[0]
        if abs(drift) < 0.002:
            continue
        output.append({
            "title": title,
            "source": "recent-fills",
            "price": _fmt_price(prices[-1]),
            "drift24h": "n/a",
            "recentFillDrift": f"{drift * 100:+.1f} pts",
            "observedTrades": len(prices),
            "firstObservedTradePrice": _fmt_price(prices[0]),
            "latestTradePrice": _fmt_price(prices[-1]),
            "interpretation": "Fallback drift from the recent fills loaded into the agent context; not a full 24h market snapshot drift.",
            "_rank": abs(drift) * (1 + math.log10(max(1, len(prices)))),
        })
    return sorted(output, key=lambda item: item["_rank"], reverse=True)[:6]


def _fill_tape_microstructure(context: dict[str, Any]) -> list[dict[str, Any]]:
    tapes = context.get("topMarketFillTape") if isinstance(context.get("topMarketFillTape"), list) else []
    output: list[dict[str, Any]] = []
    for item in tapes:
        if not isinstance(item, dict):
            continue
        title = compact_text(item.get("title") or "Untitled market", 120)
        latest_fill = _as_float(item.get("latestFillYesPrice"))
        oldest_fill = _as_float(item.get("oldestLoadedFillYesPrice"))
        fill_count = int(_as_float(item.get("fillCountLoaded")))
        fill_range = _as_float(item.get("fillYesPriceRange"))
        fill_drift = item.get("recentFillDrift")
        if fill_drift in (None, "", "null") and latest_fill > 0 and oldest_fill > 0:
            fill_drift = latest_fill - oldest_fill
        fill_drift_value = _as_float(fill_drift)
        paired_ratio = item.get("pairedFillRatio")
        conflict = bool(item.get("priceSourceConflict"))
        source_prices = {
            "snapshot": _fmt_price(item.get("snapshotLatestPrice")),
            "detail": _fmt_price(item.get("detailYesPrice") or item.get("detailLatestPrice")),
            "latestFill": _fmt_price(latest_fill),
            "fillVwap": _fmt_price(item.get("fillVwapYesPrice")),
        }
        if conflict:
            interpretation = "Snapshot/detail/recent-fill sources disagree; anchor live commentary to the recent fill tape and discount stale detail prices."
        elif latest_fill > 0 and abs(fill_drift_value) >= 0.02:
            interpretation = "Loaded recent fills show directional repricing; treat this as a low-cost tape drift, not full 24h momentum."
        elif latest_fill > 0 and 0.48 <= latest_fill <= 0.52 and fill_range <= 0.01:
            interpretation = "Recent fills are pinned near 50c; this is a balanced repricing zone unless a named catalyst breaks it."
        elif latest_fill > 0:
            interpretation = "Recent fills provide a fresher implied probability check than the market summary row."
        else:
            interpretation = "Top-market detail loaded, but no usable recent fill prices were available."
        output.append({
            "title": title,
            "snapshotPrice": source_prices["snapshot"],
            "detailPrice": source_prices["detail"],
            "latestFillPrice": source_prices["latestFill"],
            "fillVwapPrice": source_prices["fillVwap"],
            "fillCountLoaded": fill_count,
            "recentFillDrift": f"{fill_drift_value * 100:+.1f} pts" if fill_drift is not None else "n/a",
            "fillPriceRange": f"{fill_range * 100:.1f} pts" if fill_range > 0 else "0.0 pts",
            "lastFillAt": item.get("lastFillAt"),
            "fillFreshness": item.get("fillFreshness"),
            "pairedFillRatio": paired_ratio,
            "priceSourceConflict": conflict,
            "interpretation": interpretation,
            "_rank": (8 if conflict else 0)
                + abs(fill_drift_value) * 20
                + fill_range * 8
                + math.log10(max(1, fill_count)),
        })
    return [
        {key: value for key, value in item.items() if key != "_rank"}
        for item in sorted(output, key=lambda item: item["_rank"], reverse=True)[:8]
    ]


def _build_quant_forecaster(context: dict[str, Any], related_markets: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = _market_rows(context)
    ranked_flow = sorted(
        rows,
        key=lambda item: (item["volume24h"], item["tradeCount24h"]),
        reverse=True,
    )
    near_repricing = sorted(
        [item for item in rows if 0.42 <= item["price"] <= 0.58 and (item["volume24h"] > 0 or item["tradeCount24h"] > 0)],
        key=lambda item: (item["volume24h"] * 2 + item["tradeCount24h"] * 200),
        reverse=True,
    )
    anomalies: list[dict[str, Any]] = []
    drift_rows: list[dict[str, Any]] = []
    lob_rows: list[dict[str, Any]] = []
    for item in rows:
        drift = _price_drift(item)
        if drift is not None and item["price"] > 0:
            drift_rows.append({
                "title": item["title"],
                "price": _fmt_price(item["price"]),
                "source": "market-price-24h",
                "drift24h": f"{drift * 100:+.1f} pts",
                "price24hAgo": _fmt_price(item.get("price24hAgo")),
                "volume24h": _fmt_money(item["volume24h"]),
                "tradeCount24h": int(item["tradeCount24h"]),
                "interpretation": "Directional price move over the last 24h; validate against catalysts and fill quality.",
                "_rank": abs(drift) * (1 + math.log10(max(1.0, item["volume24h"] + item["tradeCount24h"]))),
            })
        spread = _lob_spread(item)
        if spread is not None:
            bid, ask, width = spread
            lob_rows.append({
                "title": item["title"],
                "bid": _fmt_price(bid),
                "ask": _fmt_price(ask),
                "spread": f"{width * 100:.1f} pts",
                "mid": _fmt_price((bid + ask) / 2),
                "interpretation": "Wide displayed spread means the last price may overstate executable confidence.",
                "_rank": width,
            })
        if item["volume24h"] >= 100_000 and item["tradeCount24h"] <= 2:
            anomalies.append({
                "title": item["title"],
                "type": "volume-without-trade-count",
                "evidence": f"{_fmt_money(item['volume24h'])}; {_fmt_compact(item['tradeCount24h'])} trades",
                "interpretation": "Treat as grouped aggregation, stale volume, or ingestion mismatch until fills reconcile.",
            })
        if item["price"] >= 0.98 and item["volume24h"] > 0:
            anomalies.append({
                "title": item["title"],
                "type": "near-certain-live-price",
                "evidence": f"{_fmt_price(item['price'])}; {_fmt_money(item['volume24h'])}",
                "interpretation": "Could be live/resolved/stale rather than a normal ex-ante probability.",
            })
    missing_change = sum(1 for item in rows if item.get("change24h") in (None, "", "null"))
    missing_lob = sum(1 for item in rows if _lob_spread(item) is None)
    drift_titles = {item["title"] for item in drift_rows}
    trade_drift_rows = [item for item in _trade_price_drift(context) if item["title"] not in drift_titles]
    drift_leaders = sorted([*drift_rows, *trade_drift_rows], key=lambda item: item["_rank"], reverse=True)[:6]
    volatility_leaders = _trade_volatility(context)
    related_scores = []
    if isinstance(related_markets, dict):
        related_scores = list(related_markets.get("arbitrageScores") or [])[:6]
    fill_tape_rows = _fill_tape_microstructure(context)
    price_source_conflicts = [
        {
            "title": item.get("title"),
            "snapshotPrice": item.get("snapshotPrice"),
            "detailPrice": item.get("detailPrice"),
            "latestFillPrice": item.get("latestFillPrice"),
            "fillCountLoaded": item.get("fillCountLoaded"),
            "lastFillAt": item.get("lastFillAt"),
            "interpretation": item.get("interpretation"),
        }
        for item in fill_tape_rows
        if item.get("priceSourceConflict")
    ]
    warnings = []
    if missing_change:
        warnings.append(f"{missing_change} market rows have missing change24h/price24hAgo; directional momentum should not be inferred from volume alone.")
    if missing_lob:
        warnings.append(f"{missing_lob} market rows have no bid/ask in the agent context; LOB spread is unavailable until orderbook snapshots are joined.")
    if price_source_conflicts:
        warnings.append(f"{len(price_source_conflicts)} top-market fill-tape rows disagree across snapshot/detail/recent fills; prefer recent-fill evidence for live reads.")
    return {
        "node": "quant_forecaster",
        "universeSize": len(rows),
        "topFlowMarkets": [
            {
                "title": item["title"],
                "price": _fmt_price(item["price"]),
                "band": _price_band(item["price"]),
                "volume24h": _fmt_money(item["volume24h"]),
                "tradeCount24h": int(item["tradeCount24h"]),
                "endDate": item.get("endDate"),
            }
            for item in ranked_flow[:6]
        ],
        "repricingZones": [
            {
                "title": item["title"],
                "price": _fmt_price(item["price"]),
                "volume24h": _fmt_money(item["volume24h"]),
                "tradeCount24h": int(item["tradeCount24h"]),
                "why": "Price sits near 50c/contested territory where fresh information can move probability quickly.",
            }
            for item in near_repricing[:6]
        ],
        "priceDriftLeaders": [
            {key: value for key, value in item.items() if key != "_rank"}
            for item in drift_leaders
        ],
        "volatilityLeaders": volatility_leaders,
        "fillTapeMicrostructure": fill_tape_rows,
        "priceSourceConflicts": price_source_conflicts[:6],
        "lobSpreads": [
            {key: value for key, value in item.items() if key != "_rank"}
            for item in sorted(lob_rows, key=lambda item: item["_rank"], reverse=True)[:6]
        ],
        "relatedMarketArbitrageScores": related_scores,
        "anomalies": anomalies[:6],
        "dataWarnings": warnings[:4],
        "inputHash": _json_hash({
            "rows": rows[:24],
            "volatility": volatility_leaders,
            "fillTape": fill_tape_rows[:8],
            "relatedScores": related_scores[:6],
        }),
    }


def _group_outcomes(group: dict[str, Any]) -> list[dict[str, Any]]:
    raw = group.get("outcomes") if isinstance(group.get("outcomes"), list) else group.get("topOutcomes")
    outcomes: list[dict[str, Any]] = []
    for outcome in raw if isinstance(raw, list) else []:
        if not isinstance(outcome, dict):
            continue
        price = _as_float(outcome.get("yesPrice") or outcome.get("price") or outcome.get("latestPrice"))
        if price <= 0:
            continue
        outcomes.append({
            "label": compact_text(outcome.get("label") or outcome.get("title") or "Outcome", 72),
            "yesPrice": price,
            "volume24h": _as_float(outcome.get("volume24h")),
            "tradeCount24h": _as_float(outcome.get("tradeCount24h")),
        })
    return outcomes


def _build_related_markets(context: dict[str, Any]) -> dict[str, Any]:
    groups = context.get("marketGroups") if isinstance(context.get("marketGroups"), list) else []
    ladders: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    arbitrage_scores: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        title = compact_text(group.get("title") or "Market group", 120)
        outcomes = _group_outcomes(group)
        if len(outcomes) < 2:
            continue
        prices = [item["yesPrice"] for item in outcomes]
        low = min(outcomes, key=lambda item: item["yesPrice"])
        high = max(outcomes, key=lambda item: item["yesPrice"])
        spread = high["yesPrice"] - low["yesPrice"]
        probability_sum = sum(prices)
        sum_deviation = abs(probability_sum - 1.0)
        monotonic_reversals = sum(
            1 for index in range(len(prices) - 1)
            if prices[index + 1] + 0.02 < prices[index]
        )
        near_certain_count = sum(1 for price in prices if price >= 0.98)
        near = [item for item in outcomes if 0.42 <= item["yesPrice"] <= 0.58]
        volume = _as_float(group.get("volume24h"))
        trades = _as_float(group.get("tradeCount24h"))
        mismatch = volume >= 100_000 and trades <= 2
        score = min(
            100.0,
            spread * 60
            + max(0.0, sum_deviation - 0.08) * 80
            + monotonic_reversals * 12
            + max(0, near_certain_count - 1) * 15
            + (8 if mismatch else 0),
        )
        if score >= 12:
            arbitrage_scores.append({
                "title": title,
                "score": round(score, 1),
                "probabilitySum": f"{probability_sum * 100:.1f}%",
                "sumDeviation": f"{sum_deviation * 100:.1f} pts",
                "spread": f"{spread * 100:.1f} pts",
                "monotonicReversals": monotonic_reversals,
                "nearCertainCount": near_certain_count,
                "volumeTradeMismatch": mismatch,
                "interpretation": "Higher score means this related-market group deserves manual inspection for curve, stale-state, or overround/underround inefficiency.",
            })
        if spread >= 0.15 or len(near) >= 2:
            ladders.append({
                "title": title,
                "type": "deadline-or-outcome-spread",
                "spread": f"{spread * 100:.1f} pts",
                "lowOutcome": {"label": low["label"], "price": _fmt_price(low["yesPrice"])},
                "highOutcome": {"label": high["label"], "price": _fmt_price(high["yesPrice"])},
                "near50Outcomes": [
                    {"label": item["label"], "price": _fmt_price(item["yesPrice"])}
                    for item in near[:4]
                ],
                "interpretation": "Related outcomes disagree enough to form a probability curve or repricing spread.",
            })
        if len(outcomes) >= 3 and all(item["yesPrice"] >= 0.98 for item in outcomes[:3]):
            anomalies.append({
                "title": title,
                "type": "multiple-near-certain-outcomes",
                "evidence": ", ".join(f"{item['label']} {_fmt_price(item['yesPrice'])}" for item in outcomes[:3]),
                "interpretation": "Likely live/resolved/stale ladder or feed issue; do not read as normal ex-ante probabilities.",
            })
        if volume >= 100_000 and trades <= 2:
            anomalies.append({
                "title": title,
                "type": "group-volume-without-group-trades",
                "evidence": f"{_fmt_money(volume)} group volume; {_fmt_compact(trades)} group trades",
                "interpretation": "Check underlying child market fills before treating group volume as current flow.",
            })
    return {
        "node": "related_markets",
        "ladders": sorted(ladders, key=lambda item: _as_float(str(item.get("spread", "0")).split()[0]), reverse=True)[:8],
        "arbitrageScores": sorted(arbitrage_scores, key=lambda item: item["score"], reverse=True)[:8],
        "anomalies": anomalies[:8],
        "inputHash": _json_hash(groups[:20]),
    }


def _react_tool_quant_snapshot(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "quantForecaster": _compact_quant_for_prompt(context.get("quantForecaster") or {}),
        "relatedMarkets": _compact_related_for_prompt(context.get("relatedMarkets") or {}),
    }


def _react_tool_data_quality(context: dict[str, Any]) -> dict[str, Any]:
    quant = context.get("quantForecaster") if isinstance(context.get("quantForecaster"), dict) else {}
    metrics = context.get("metrics") if isinstance(context.get("metrics"), dict) else {}
    return {
        "warnings": quant.get("dataWarnings") or [],
        "metrics": metrics,
        "contextChars": context.get("contextChars"),
    }


def _react_tool_catalyst_scan(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": context.get("content", [])[:4],
        "oracle": context.get("oracle", [])[:4],
        "watchSignals": [
            *(context.get("alphaSignals", [])[:2] if isinstance(context.get("alphaSignals"), list) else []),
            *(context.get("whaleSignals", [])[:2] if isinstance(context.get("whaleSignals"), list) else []),
        ],
    }


def _react_tool_resolution_scan(context: dict[str, Any]) -> dict[str, Any]:
    groups = context.get("marketGroups") if isinstance(context.get("marketGroups"), list) else []
    markets = context.get("marketCandidates") if isinstance(context.get("marketCandidates"), list) else []
    return {
        "deadlineGroups": [
            {
                "title": item.get("title"),
                "endDate": item.get("endDate"),
                "outcomeCount": item.get("outcomeCount"),
            }
            for item in [*groups[:3], *markets[:3]]
            if isinstance(item, dict)
        ],
        "oracle": context.get("oracle", [])[:4],
    }


def _react_tool_memory_scan(context: dict[str, Any]) -> dict[str, Any]:
    memory = context.get("forecastMemory") if isinstance(context.get("forecastMemory"), list) else []
    return {"priorEpisodes": memory[:6]}


def _run_react_tools(node: str, context: dict[str, Any]) -> list[dict[str, Any]]:
    if not react_tools_enabled():
        return []
    plan = {
        "microstructure": ["quant_snapshot", "data_quality"],
        "catalyst": ["catalyst_scan", "quant_snapshot"],
        "resolution": ["resolution_scan", "data_quality"],
        "skeptic": ["memory_scan", "data_quality", "quant_snapshot"],
    }.get(node, ["quant_snapshot"])
    tools = {
        "quant_snapshot": _react_tool_quant_snapshot,
        "data_quality": _react_tool_data_quality,
        "catalyst_scan": _react_tool_catalyst_scan,
        "resolution_scan": _react_tool_resolution_scan,
        "memory_scan": _react_tool_memory_scan,
    }
    trace: list[dict[str, Any]] = []
    for index, tool_name in enumerate(plan, start=1):
        tool = tools.get(tool_name)
        if tool is None:
            continue
        observation = tool(context)
        compacted = _compact_react_observation(tool_name, observation)
        trace.append({
            "round": index,
            "thought": f"{node} needs structured evidence before writing claims.",
            "action": tool_name,
            "observation": compacted,
            "observationHash": _json_hash(observation),
        })
    return trace


def _memory_key(lens: str, title: str, kind: str) -> str:
    return f"{lens}:{kind}:{_json_hash(title)}"


def _build_reflexion_memory(context: dict[str, Any], lens: str, quant: dict[str, Any], related: dict[str, Any], run_id: str) -> dict[str, Any]:
    prior = context.get("forecastMemory") if isinstance(context.get("forecastMemory"), list) else []
    items: list[dict[str, Any]] = []
    for item in (quant.get("priceDriftLeaders") or [])[:3]:
        title = compact_text(item.get("title") or "market", 120)
        items.append({
            "memoryKey": _memory_key(lens, title, "price-drift"),
            "lens": lens,
            "runId": run_id,
            "kind": "price-drift",
            "title": title,
            "observation": item,
            "lesson": "Compare this drift against the next run and any resolution/oracle update before treating it as durable signal.",
            "createdAt": _utc_now_iso(),
        })
    for item in (related.get("arbitrageScores") or [])[:3]:
        title = compact_text(item.get("title") or "related-market", 120)
        items.append({
            "memoryKey": _memory_key(lens, title, "related-arbitrage"),
            "lens": lens,
            "runId": run_id,
            "kind": "related-arbitrage",
            "title": title,
            "observation": item,
            "lesson": "Recheck whether the score was true inefficiency, stale/resolved state, or non-exclusive outcome structure.",
            "createdAt": _utc_now_iso(),
        })
    for item in (quant.get("priceSourceConflicts") or [])[:3]:
        title = compact_text(item.get("title") or "fill-tape-conflict", 120)
        items.append({
            "memoryKey": _memory_key(lens, title, "fill-tape-conflict"),
            "lens": lens,
            "runId": run_id,
            "kind": "fill-tape-conflict",
            "title": title,
            "observation": item,
            "lesson": "When summary/detail/recent-fill prices disagree, prefer recent fills for live analysis and flag the ingestion conflict explicitly.",
            "createdAt": _utc_now_iso(),
        })
    return {
        "node": "reflexion_memory",
        "priorEpisodesLoaded": len(prior),
        "newEpisodes": items[:6],
        "inputHash": _json_hash({"prior": prior[:8], "quant": quant.get("inputHash"), "related": related.get("inputHash")}),
    }


def _build_calibration_agent(
    context: dict[str, Any],
    quant: dict[str, Any],
    related: dict[str, Any],
    memory: dict[str, Any],
    specialists: list[dict[str, Any]],
) -> dict[str, Any]:
    warnings = list(quant.get("dataWarnings") or [])
    prior = context.get("forecastMemory") if isinstance(context.get("forecastMemory"), list) else []
    brier_values = []
    for item in prior:
        if not isinstance(item, dict):
            continue
        score = item.get("brierScore")
        try:
            if score is not None:
                brier_values.append(float(score))
        except (TypeError, ValueError):
            continue
    avg_brier = sum(brier_values) / len(brier_values) if brier_values else None
    related_scores = related.get("arbitrageScores") if isinstance(related.get("arbitrageScores"), list) else []
    confidence = "medium"
    if warnings or any(_as_float(item.get("score")) >= 80 for item in related_scores[:3]):
        confidence = "low"
    if avg_brier is not None and avg_brier <= 0.18 and not warnings:
        confidence = "high"
    return {
        "node": "calibration_agent",
        "confidence": confidence,
        "history": {
            "priorEpisodesLoaded": len(prior),
            "brierCount": len(brier_values),
            "avgBrierScore": round(avg_brier, 4) if avg_brier is not None else None,
        },
        "probabilityDiscipline": [
            "Use market-implied price as anchor unless a named catalyst or resolution issue explains the deviation.",
            "Discount signals when change24h, LOB spread, or resolution state is missing.",
        ],
        "discounts": warnings[:4],
        "relatedMarketStress": related_scores[:4],
        "specialistConfidence": [
            {"node": item.get("node"), "confidence": item.get("confidence")}
            for item in specialists[:4]
            if isinstance(item, dict)
        ],
        "inputHash": _json_hash({
            "warnings": warnings,
            "related": related_scores[:4],
            "memory": memory.get("inputHash"),
            "specialists": specialists,
        }),
    }


def _specialist_prompt(
    node: str,
    lens: str,
    context: dict[str, Any],
    evidence: dict[str, Any],
    react_trace: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    role = {
        "microstructure": "You are the market microstructure agent for a Polymarket intelligence graph. Focus on named markets, implied probability, volume, trade-count, liquidity concentration, close probabilities, deadline ladders, and group-vs-single-market mismatches.",
        "catalyst": "You are the catalyst research agent for a Polymarket intelligence graph. Focus on named markets, current prices, concrete external triggers, related-market catalysts, event timing, and what news/data would move probability.",
        "resolution": "You are the resolution-risk agent for a Polymarket intelligence graph. Focus on market wording, deadline buckets, official source hierarchy, oracle/resolution events, ambiguity, and settlement risk that changes how prices should be interpreted.",
        "skeptic": "You are the skeptic and calibration agent for a Polymarket intelligence graph. Challenge weak evidence, missing price-change data, stale signals, narrative overreach, and probability miscalibration.",
    }[node]
    user_context = {
        "lens": lens,
        "evidence": evidence,
        "context": {
            "metrics": context.get("metrics"),
            "marketCandidates": _compact_prompt_list(context.get("marketCandidates"), 5),
            "marketGroups": _compact_prompt_list(context.get("marketGroups"), 3),
            "topMarketFillTape": _compact_fill_tape_for_prompt(context.get("topMarketFillTape"), 3),
            "trades": _compact_prompt_list(context.get("trades"), 2),
            "oracle": context.get("oracle", [])[:2],
            "content": context.get("content", [])[:2],
            "alphaSignals": context.get("alphaSignals", [])[:2],
            "whaleSignals": context.get("whaleSignals", [])[:2],
            "searchResults": context.get("searchResults", [])[:1],
            "specialistAgents": _compact_agents_for_prompt(context.get("specialistAgents"), 4),
            "quantForecaster": _compact_quant_for_prompt(context.get("quantForecaster") or {}),
            "relatedMarkets": _compact_related_for_prompt(context.get("relatedMarkets") or {}),
            "forecastMemory": _compact_memory_for_prompt(context.get("forecastMemory"), 3),
            "reactToolTrace": react_trace or [],
        },
        "requiredSchema": {
            "findings": [{"label": "LIQUIDITY|CATALYST|RESOLUTION|RISK|TREND|PROBABILITY", "title": "named market or spread", "summary": "market-level insight with price/probability and why it matters", "severity": "positive|warning|critical|neutral", "evidence": "price + volume/trades"}],
            "risks": ["up to three risks or caveats"],
            "watch": ["up to three concrete triggers that would move probability"],
            "confidence": "low|medium|high",
            "probabilityAdjustment": "terse note on implied probability, term spread, or no directional edge",
        },
    }
    return role + FORECAST_ANALYST_RULES + "\nReturn compact JSON only.", json.dumps(user_context, ensure_ascii=False, default=str)


def _writer_prompt(
    lens: str,
    context: dict[str, Any],
    evidence: dict[str, Any],
    agents: list[dict[str, Any]],
    calibration: dict[str, Any],
    calibration_agent: dict[str, Any] | None = None,
    memory: dict[str, Any] | None = None,
) -> tuple[str, str]:
    system = """You are the panel writer for polyData's Forecast Intelligence Graph.
Return compact JSON only. Use the specialist agent outputs as evidence, but write one coherent dashboard payload.
The user does not need a category summary. The user needs prediction-market intelligence: named markets, implied probabilities, why the market is priced that way, what could move it, and what resolution wording can break the read.
Write like a prediction-market analyst, not a dashboard narrator.
""" + FORECAST_ANALYST_RULES
    user = {
        "lens": lens,
        "architecture": GRAPH_VERSION,
        "evidenceBuilder": evidence,
        "specialistAgents": agents,
        "skepticCalibration": calibration,
        "calibrationAgent": calibration_agent or {},
        "reflexionMemory": memory or {},
        "sourceContext": {
            "metrics": context.get("metrics"),
            "quantForecaster": _compact_quant_for_prompt(context.get("quantForecaster") or {}),
            "relatedMarkets": _compact_related_for_prompt(context.get("relatedMarkets") or {}),
            "marketCandidates": _compact_prompt_list(context.get("marketCandidates"), 4),
            "marketGroups": _compact_prompt_list(context.get("marketGroups"), 2),
            "topMarketFillTape": _compact_fill_tape_for_prompt(context.get("topMarketFillTape"), 2),
            "trades": _compact_prompt_list(context.get("trades"), 2),
            "oracle": context.get("oracle", [])[:2],
            "content": context.get("content", [])[:2],
            "searchResults": context.get("searchResults", [])[:1],
            "forecastMemory": _compact_memory_for_prompt(context.get("forecastMemory"), 2),
        },
        "requiredSchema": {
            "brief": "one or two concise English sentences. Must name at least one market and include a price/probability or spread. Avoid generic category/breadth wording.",
            "specialMarkets": [{"title": "exact market/event title", "why": "why this market matters in prediction-market terms: price, volume/trades, catalyst, resolution or term-structure", "trend": "probability structure label such as 50c repricing zone|deadline ladder|term spread|liquidity anomaly|resolution premium", "severity": "positive|warning|critical|neutral", "evidence": "price + volume/trades"}],
            "themes": [{"label": "PROBABILITY|CATALYST|RESOLUTION|LIQUIDITY|SPREAD|RISK|TREND", "title": "named market cluster or relationship", "summary": "specific thesis about pricing, curve, catalyst, or resolution; not category summary", "severity": "positive|warning|critical|neutral", "evidence": "short price/volume/trade evidence"}],
            "watchlist": [{"title": "specific market trigger", "reason": "what update would change implied probability or resolve ambiguity", "horizon": "today|24h|this week|event close", "severity": "positive|warning|critical|neutral"}],
            "focus": [{"label": "PROBABILITY|SPREAD|CATALYSTS|RESOLUTION|LIQUIDITY|RISK", "title": "market-level title", "summary": "one useful sentence with market, price/probability, and interpretation", "severity": "positive|warning|critical|neutral", "evidence": "price + flow evidence"}],
            "evidence": ["up to four terse bullets, each with a named market and numeric evidence"],
        },
    }
    return system, json.dumps(user, ensure_ascii=False, default=str)


def _call_json_node(client: Any, node: str, messages: list[dict[str, str]], *, max_tokens: int, run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    started = _utc_now_iso()
    start_monotonic = time.monotonic()
    event: dict[str, Any] = {
        "runId": run_id,
        "node": node,
        "startedAt": started,
        "inputHash": _json_hash(messages),
        "status": "ok",
    }
    try:
        raw_text = client.complete_json(
            messages,
            max_tokens=max_tokens,
            workflow_name=f"polydata-market-wide-fig-{node}-{run_id}",
        )
        raw = extract_json_object(raw_text)
        output_hash = _json_hash(raw)
        usage = getattr(client, "last_usage", None)
        event.update({
            "finishedAt": _utc_now_iso(),
            "latencyMs": int((time.monotonic() - start_monotonic) * 1000),
            "outputHash": output_hash,
            "outputJson": raw,
            "evidenceRefs": _evidence_refs(raw),
            "model": getattr(client, "model", ""),
            "runtime": getattr(usage, "runtime", ""),
            "usage": {
                "inputTokens": getattr(usage, "input_tokens", 0),
                "outputTokens": getattr(usage, "output_tokens", 0),
                "totalTokens": getattr(usage, "total_tokens", 0),
                "inputChars": getattr(usage, "input_chars", 0),
            },
        })
        return raw, event
    except Exception as exc:
        event.update({
            "finishedAt": _utc_now_iso(),
            "latencyMs": int((time.monotonic() - start_monotonic) * 1000),
            "status": "error",
            "error": compact_text(str(exc), 180),
        })
        return {}, event


def _usage_total(events: list[dict[str, Any]]) -> dict[str, int]:
    total = {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0, "requests": 0}
    for event in events:
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        if usage:
            total["requests"] += 1
        total["inputTokens"] += int(usage.get("inputTokens") or 0)
        total["outputTokens"] += int(usage.get("outputTokens") or 0)
        total["totalTokens"] += int(usage.get("totalTokens") or 0)
    return total


def _graph_context(state: dict[str, Any]) -> dict[str, Any]:
    context = state.get("context") if isinstance(state.get("context"), dict) else {}
    output = {
        **context,
        "quantForecaster": state.get("quantForecaster") or {},
        "relatedMarkets": state.get("relatedMarkets") or {},
        "reflexionMemory": state.get("reflexionMemory") or {},
    }
    if state.get("specialists"):
        output["specialistAgents"] = state.get("specialists")
    if state.get("calibrationAgent"):
        output["calibrationAgent"] = state.get("calibrationAgent")
    return output


def _graph_nodes(limit: int, *, configured: bool = True) -> list[str]:
    if not configured:
        return ["evidence_builder", "related_markets", "quant_forecaster", "reflexion_memory", "calibration_agent", "skeptic", "panel_writer"]
    return [
        "evidence_builder",
        "related_markets",
        "quant_forecaster",
        "reflexion_memory",
        *SPECIALIST_NODES[:limit],
        "calibration_agent",
        *(["skeptic"] if limit >= 4 else []),
        "panel_writer",
    ]


def _graph_response(state: dict[str, Any], *, configured: bool) -> dict[str, Any]:
    response = dict(state.get("response") or {})
    events: list[dict[str, Any]] = list(state.get("events") or [])
    response["forecastRunId"] = state["runId"]
    response["agentArchitecture"] = GRAPH_VERSION
    response["agentGraph"] = {
        "version": GRAPH_VERSION,
        "runId": state["runId"],
        "mode": "langgraph-supervisor-worker" if configured else "deterministic-fallback",
        "runtime": state.get("graphRuntime") or "langgraph-supervisor-stategraph",
        "nodes": _graph_nodes(int(state.get("limit") or 0), configured=configured),
        "events": events,
        "evidenceBuilder": state.get("evidence") or {},
        "quantForecaster": state.get("quantForecaster") or {},
        "relatedMarkets": state.get("relatedMarkets") or {},
        "reflexionMemory": state.get("reflexionMemory") or {},
        "calibrationAgent": state.get("calibrationAgent") or {},
        "specialists": state.get("specialists") or [],
        "calibration": state.get("calibration") or {"node": "skeptic", "findings": [], "risks": [], "watch": [], "confidence": "medium"},
    }
    response["usage"] = {**response.get("usage", {}), **_usage_total(events), "contextChars": (state.get("context") or {}).get("contextChars")}
    response["agentRuntime"] = "forecast-intelligence-graph"
    return response


def _run_langgraph_supervisor(
    state: dict[str, Any],
    client: Any,
    *,
    normalize: Callable[[dict[str, Any], dict[str, Any], str, list[dict[str, str]], str], dict[str, Any]],
    fallback: Callable[[dict[str, Any], str, str, list[dict[str, str]]], dict[str, Any]],
    search_results: list[dict[str, str]],
) -> dict[str, Any] | None:
    if not langgraph_enabled():
        return None
    try:
        from langgraph.graph import END, StateGraph
    except Exception:
        return None
    configured = bool(getattr(client, "configured", False))
    limit = int(state.get("limit") or _agent_limit())

    def evidence_node(current: dict[str, Any]) -> dict[str, Any]:
        evidence = _deterministic_evidence(current["context"], current["lens"])
        current["evidence"] = evidence
        current.setdefault("events", []).append(_node_event(
            run_id=current["runId"],
            lens=current["lens"],
            node="evidence_builder",
            output=evidence,
            input_hash=evidence["inputHash"],
        ))
        return current

    def related_node(current: dict[str, Any]) -> dict[str, Any]:
        related = _build_related_markets(current["context"])
        current["relatedMarkets"] = related
        current.setdefault("events", []).append(_node_event(
            run_id=current["runId"],
            lens=current["lens"],
            node="related_markets",
            output=related,
            input_hash=related["inputHash"],
        ))
        return current

    def quant_node(current: dict[str, Any]) -> dict[str, Any]:
        quant = _build_quant_forecaster(current["context"], current.get("relatedMarkets") or {})
        current["quantForecaster"] = quant
        current.setdefault("events", []).append(_node_event(
            run_id=current["runId"],
            lens=current["lens"],
            node="quant_forecaster",
            output=quant,
            input_hash=quant["inputHash"],
        ))
        return current

    def memory_node(current: dict[str, Any]) -> dict[str, Any]:
        memory = _build_reflexion_memory(
            current["context"],
            current["lens"],
            current.get("quantForecaster") or {},
            current.get("relatedMarkets") or {},
            current["runId"],
        )
        current["reflexionMemory"] = memory
        current.setdefault("events", []).append(_node_event(
            run_id=current["runId"],
            lens=current["lens"],
            node="reflexion_memory",
            output=memory,
            input_hash=memory["inputHash"],
        ))
        return current

    def specialist_node(node: str):
        def run(current: dict[str, Any]) -> dict[str, Any]:
            graph_context = _graph_context(current)
            if not configured:
                output = {"node": node, "findings": [], "risks": ["missing-api-key"], "watch": [], "confidence": "low"}
                current.setdefault("specialists", []).append(output)
                current.setdefault("events", []).append(_node_event(
                    run_id=current["runId"],
                    lens=current["lens"],
                    node=node,
                    output=output,
                    status="skipped",
                    error="missing-api-key",
                ))
                return current
            react_trace = _run_react_tools(node, graph_context)
            system, user = _specialist_prompt(node, current["lens"], graph_context, current.get("evidence") or {}, react_trace)
            raw, event = _call_json_node(
                client,
                node,
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=520,
                run_id=current["runId"],
            )
            event["lens"] = current["lens"]
            event["toolCalls"] = react_trace
            output = _normalize_node_output(raw, node) if raw else {
                "node": node,
                "findings": [],
                "risks": [event.get("error", "agent failed")],
                "watch": [],
                "confidence": "low",
            }
            if isinstance(event.get("outputJson"), dict):
                event["outputJson"]["toolCalls"] = react_trace
            else:
                event["outputJson"] = {**output, "toolCalls": react_trace}
                event["outputHash"] = _json_hash(event["outputJson"])
            current.setdefault("events", []).append(event)
            current.setdefault("specialists", []).append(output)
            return current
        return run

    def calibration_node(current: dict[str, Any]) -> dict[str, Any]:
        graph_context = _graph_context(current)
        calibration_agent = _build_calibration_agent(
            graph_context,
            current.get("quantForecaster") or {},
            current.get("relatedMarkets") or {},
            current.get("reflexionMemory") or {},
            current.get("specialists") or [],
        )
        current["calibrationAgent"] = calibration_agent
        current.setdefault("events", []).append(_node_event(
            run_id=current["runId"],
            lens=current["lens"],
            node="calibration_agent",
            output=calibration_agent,
            input_hash=calibration_agent["inputHash"],
        ))
        return current

    def skeptic_node(current: dict[str, Any]) -> dict[str, Any]:
        if limit < 4:
            current["calibration"] = {"node": "skeptic", "findings": [], "risks": [], "watch": [], "confidence": "medium"}
            return current
        graph_context = _graph_context(current)
        if not configured:
            output = {"node": "skeptic", "findings": [], "risks": ["missing-api-key"], "watch": [], "confidence": "low"}
            current["calibration"] = output
            current.setdefault("events", []).append(_node_event(
                run_id=current["runId"],
                lens=current["lens"],
                node="skeptic",
                output=output,
                status="skipped",
                error="missing-api-key",
            ))
            return current
        react_trace = _run_react_tools("skeptic", graph_context)
        system, user = _specialist_prompt("skeptic", current["lens"], graph_context, current.get("evidence") or {}, react_trace)
        raw, event = _call_json_node(
            client,
            "skeptic",
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=520,
            run_id=current["runId"],
        )
        event["lens"] = current["lens"]
        event["toolCalls"] = react_trace
        output = _normalize_node_output(raw, "skeptic") if raw else {"node": "skeptic", "findings": [], "risks": [], "watch": [], "confidence": "medium"}
        if isinstance(event.get("outputJson"), dict):
            event["outputJson"]["toolCalls"] = react_trace
        else:
            event["outputJson"] = {**output, "toolCalls": react_trace}
            event["outputHash"] = _json_hash(event["outputJson"])
        current.setdefault("events", []).append(event)
        current["calibration"] = output
        return current

    def writer_node(current: dict[str, Any]) -> dict[str, Any]:
        graph_context = _graph_context(current)
        if not configured:
            response = fallback(current["payload"], current["lens"], "missing-api-key", search_results)
            current["response"] = response
            return current
        system, user = _writer_prompt(
            current["lens"],
            graph_context,
            current.get("evidence") or {},
            current.get("specialists") or [],
            current.get("calibration") or {"node": "skeptic", "findings": [], "risks": [], "watch": [], "confidence": "medium"},
            current.get("calibrationAgent") or {},
            current.get("reflexionMemory") or {},
        )
        raw, event = _call_json_node(
            client,
            "panel_writer",
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=950,
            run_id=current["runId"],
        )
        event["lens"] = current["lens"]
        current.setdefault("events", []).append(event)
        if not raw:
            response = fallback(current["payload"], current["lens"], "agent-error", search_results)
        else:
            try:
                response = normalize(raw, current["payload"], current["lens"], search_results, getattr(client, "model", ""))
            except Exception as exc:
                current.setdefault("events", []).append({
                    "node": "response_normalizer",
                    "status": "error",
                    "finishedAt": _utc_now_iso(),
                    "error": compact_text(str(exc), 180),
                })
                response = fallback(current["payload"], current["lens"], "agent-error", search_results)
        current["response"] = response
        return current

    workflow = StateGraph(dict)
    workflow.add_node("evidence_builder", evidence_node)
    workflow.add_node("related_markets", related_node)
    workflow.add_node("quant_forecaster", quant_node)
    workflow.add_node("reflexion_memory", memory_node)
    for node in SPECIALIST_NODES[:limit]:
        workflow.add_node(node, specialist_node(node))
    workflow.add_node("calibration_agent", calibration_node)
    workflow.add_node("skeptic", skeptic_node)
    workflow.add_node("panel_writer", writer_node)
    workflow.set_entry_point("evidence_builder")
    workflow.add_edge("evidence_builder", "related_markets")
    workflow.add_edge("related_markets", "quant_forecaster")
    workflow.add_edge("quant_forecaster", "reflexion_memory")
    previous = "reflexion_memory"
    for node in SPECIALIST_NODES[:limit]:
        workflow.add_edge(previous, node)
        previous = node
    workflow.add_edge(previous, "calibration_agent")
    workflow.add_edge("calibration_agent", "skeptic")
    workflow.add_edge("skeptic", "panel_writer")
    workflow.add_edge("panel_writer", END)
    app = workflow.compile()
    result = app.invoke(state)
    result["graphRuntime"] = "langgraph-supervisor-stategraph"
    return result


def _run_sequential_preflight(state: dict[str, Any]) -> dict[str, Any]:
    evidence = _deterministic_evidence(state["context"], state["lens"])
    related = _build_related_markets(state["context"])
    quant = _build_quant_forecaster(state["context"], related)
    memory = _build_reflexion_memory(state["context"], state["lens"], quant, related, state["runId"])
    state.update({
        "evidence": evidence,
        "relatedMarkets": related,
        "quantForecaster": quant,
        "reflexionMemory": memory,
        "graphRuntime": "sequential-stategraph-fallback",
        "events": [
            _node_event(run_id=state["runId"], lens=state["lens"], node="evidence_builder", output=evidence, input_hash=evidence["inputHash"]),
            _node_event(run_id=state["runId"], lens=state["lens"], node="related_markets", output=related, input_hash=related["inputHash"]),
            _node_event(run_id=state["runId"], lens=state["lens"], node="quant_forecaster", output=quant, input_hash=quant["inputHash"]),
            _node_event(run_id=state["runId"], lens=state["lens"], node="reflexion_memory", output=memory, input_hash=memory["inputHash"]),
        ],
    })
    return state


def run_forecast_intelligence_graph(
    payload: dict[str, Any],
    lens: str,
    context: dict[str, Any],
    search_results: list[dict[str, str]],
    client: Any,
    *,
    normalize: Callable[[dict[str, Any], dict[str, Any], str, list[dict[str, str]], str], dict[str, Any]],
    fallback: Callable[[dict[str, Any], str, str, list[dict[str, str]]], dict[str, Any]],
) -> dict[str, Any]:
    run_id = forecast_run_id(payload)
    state = {
        "payload": payload,
        "lens": lens,
        "context": context,
        "runId": run_id,
        "limit": _agent_limit(),
        "specialists": [],
        "events": [],
    }
    langgraph_state = _run_langgraph_supervisor(
        state,
        client,
        normalize=normalize,
        fallback=fallback,
        search_results=search_results,
    )
    if langgraph_state is not None and isinstance(langgraph_state.get("response"), dict):
        return _graph_response(langgraph_state, configured=bool(getattr(client, "configured", False)))

    state = _run_sequential_preflight(state)
    evidence = state["evidence"]
    related_markets = state["relatedMarkets"]
    quant_forecaster = state["quantForecaster"]
    reflexion_memory = state["reflexionMemory"]
    graph_runtime = state.get("graphRuntime") or "sequential-stategraph-fallback"
    graph_context = {
        **context,
        "quantForecaster": quant_forecaster,
        "relatedMarkets": related_markets,
        "reflexionMemory": reflexion_memory,
    }
    events: list[dict[str, Any]] = list(state.get("events") or [])
    if not getattr(client, "configured", False):
        calibration_agent = _build_calibration_agent(graph_context, quant_forecaster, related_markets, reflexion_memory, [])
        events.append(_node_event(
            run_id=run_id,
            lens=lens,
            node="calibration_agent",
            output=calibration_agent,
            input_hash=calibration_agent["inputHash"],
        ))
        response = fallback(payload, lens, "missing-api-key", search_results)
        response["forecastRunId"] = run_id
        response["agentArchitecture"] = GRAPH_VERSION
        response["agentGraph"] = {
            "version": GRAPH_VERSION,
            "runId": run_id,
            "mode": "deterministic-fallback",
            "runtime": graph_runtime,
            "nodes": ["evidence_builder", "related_markets", "quant_forecaster", "reflexion_memory", "calibration_agent"],
            "events": events,
            "evidenceBuilder": evidence,
            "quantForecaster": quant_forecaster,
            "relatedMarkets": related_markets,
            "reflexionMemory": reflexion_memory,
            "calibrationAgent": calibration_agent,
        }
        return response

    limit = _agent_limit()
    specialists: list[dict[str, Any]] = []
    for node in SPECIALIST_NODES[:limit]:
        react_trace = _run_react_tools(node, graph_context)
        system, user = _specialist_prompt(node, lens, graph_context, evidence, react_trace)
        raw, event = _call_json_node(
            client,
            node,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=520,
            run_id=run_id,
        )
        event["lens"] = lens
        event["toolCalls"] = react_trace
        if isinstance(event.get("outputJson"), dict):
            event["outputJson"]["toolCalls"] = react_trace
        events.append(event)
        specialists.append(_normalize_node_output(raw, node) if raw else {"node": node, "findings": [], "risks": [event.get("error", "agent failed")], "watch": [], "confidence": "low"})

    calibration_agent = _build_calibration_agent(graph_context, quant_forecaster, related_markets, reflexion_memory, specialists)
    events.append(_node_event(
        run_id=run_id,
        lens=lens,
        node="calibration_agent",
        output=calibration_agent,
        input_hash=calibration_agent["inputHash"],
    ))

    calibration: dict[str, Any] = {"node": "skeptic", "findings": [], "risks": [], "watch": [], "confidence": "medium"}
    if limit >= 4:
        skeptic_context = {**graph_context, "specialistAgents": specialists, "calibrationAgent": calibration_agent}
        react_trace = _run_react_tools("skeptic", skeptic_context)
        system, user = _specialist_prompt("skeptic", lens, skeptic_context, evidence, react_trace)
        raw, event = _call_json_node(
            client,
            "skeptic",
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=520,
            run_id=run_id,
        )
        event["lens"] = lens
        event["toolCalls"] = react_trace
        if isinstance(event.get("outputJson"), dict):
            event["outputJson"]["toolCalls"] = react_trace
        events.append(event)
        if raw:
            calibration = _normalize_node_output(raw, "skeptic")

    system, user = _writer_prompt(lens, graph_context, evidence, specialists, calibration, calibration_agent, reflexion_memory)
    raw, event = _call_json_node(
        client,
        "panel_writer",
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=950,
        run_id=run_id,
    )
    event["lens"] = lens
    events.append(event)
    if not raw:
        response = fallback(payload, lens, "agent-error", search_results)
    else:
        try:
            response = normalize(raw, payload, lens, search_results, getattr(client, "model", ""))
        except Exception as exc:
            events.append({
                "node": "response_normalizer",
                "status": "error",
                "finishedAt": _utc_now_iso(),
                "error": compact_text(str(exc), 180),
            })
            response = fallback(payload, lens, "agent-error", search_results)
    response["forecastRunId"] = run_id
    response["agentArchitecture"] = GRAPH_VERSION
    response["agentGraph"] = {
        "version": GRAPH_VERSION,
        "runId": run_id,
        "mode": "langgraph-supervisor-worker" if graph_runtime == "langgraph-stategraph" else "supervisor-worker",
        "runtime": graph_runtime,
        "nodes": ["evidence_builder", "related_markets", "quant_forecaster", "reflexion_memory", *SPECIALIST_NODES[:limit], "calibration_agent", *(["skeptic"] if limit >= 4 else []), "panel_writer"],
        "events": events,
        "evidenceBuilder": evidence,
        "quantForecaster": quant_forecaster,
        "relatedMarkets": related_markets,
        "reflexionMemory": reflexion_memory,
        "calibrationAgent": calibration_agent,
        "specialists": specialists,
        "calibration": calibration,
    }
    response["usage"] = {**response.get("usage", {}), **_usage_total(events), "contextChars": context.get("contextChars")}
    response["agentRuntime"] = "forecast-intelligence-graph"
    return response
