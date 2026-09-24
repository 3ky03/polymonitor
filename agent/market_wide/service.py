from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from agent.common.env import get_int_env
from agent.common.json_utils import compact_text, extract_json_object
from agent.common.llm_client import OpenAICompatibleClient
from agent.common.tavily_client import TavilySearchClient

from .graph import graph_enabled, run_forecast_intelligence_graph
from .prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE


VALID_LENSES = {"overview", "special", "trend"}
LENS_ALIASES = {
    "brief": "overview",
    "flow": "special",
    "oracle": "trend",
    "catalyst": "trend",
    "radar": "trend",
}


class _DeterministicFallbackClient:
    configured = False
    model = "deterministic-fallback"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _fmt_currency(value: Any) -> str:
    return f"${_fmt_compact(value)}"


def _items(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    return value if isinstance(value, list) else []


def _market_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in _items(payload, "markets"):
        if not isinstance(item, dict):
            continue
        candidates.append({
            "id": item.get("id") or item.get("localMarketId"),
            "conditionId": item.get("conditionId"),
            "yesTokenId": item.get("yesTokenId"),
            "noTokenId": item.get("noTokenId"),
            "title": item.get("title") or item.get("slug") or "Untitled market",
            "category": item.get("category") or "market",
            "volume24h": item.get("volume24h"),
            "tradeCount24h": item.get("tradeCount24h"),
            "latestPrice": item.get("latestPrice"),
            "price24hAgo": item.get("price24hAgo"),
            "change24h": item.get("change24h"),
            "bestBid": item.get("bestBid") or item.get("bid") or item.get("yesBid"),
            "bestAsk": item.get("bestAsk") or item.get("ask") or item.get("yesAsk"),
            "endDate": item.get("endDate"),
            "createdAt": item.get("createdAt"),
            "kind": "market",
        })
    for group in _items(payload, "marketGroups"):
        if not isinstance(group, dict):
            continue
        outcomes = group.get("topOutcomes") if isinstance(group.get("topOutcomes"), list) else group.get("outcomes")
        outcomes = outcomes if isinstance(outcomes, list) else []
        prices = [
            _as_float(outcome.get("yesPrice"))
            for outcome in outcomes
            if isinstance(outcome, dict) and outcome.get("yesPrice") not in (None, "")
        ]
        latest_price = prices[0] if prices else None
        candidates.append({
            "title": group.get("title") or group.get("slug") or "Untitled event",
            "category": group.get("category") or "market",
            "volume24h": group.get("volume24h"),
            "tradeCount24h": group.get("tradeCount24h"),
            "latestPrice": latest_price,
            "outcomeCount": group.get("outcomeCount") or len(outcomes),
            "endDate": group.get("endDate"),
            "createdAt": group.get("createdAt"),
            "kind": "group",
            "outcomes": [
                {
                    "label": outcome.get("label") or outcome.get("title"),
                    "yesPrice": outcome.get("yesPrice"),
                    "volume24h": outcome.get("volume24h"),
                    "tradeCount24h": outcome.get("tradeCount24h"),
                }
                for outcome in outcomes[:4]
                if isinstance(outcome, dict)
            ],
        })
    return candidates


def _top_categories(markets: list[Any]) -> list[str]:
    counts: dict[str, int] = {}
    for item in markets:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "market").strip().lower() or "market"
        counts[category] = counts.get(category, 0) + 1
    return [f"{name} {count}" for name, count in sorted(counts.items(), key=lambda pair: pair[1], reverse=True)[:4]]


def _volume_total(items: list[Any]) -> float:
    total = 0.0
    for item in items:
        if isinstance(item, dict):
            total += _as_float(item.get("volume24h"))
    return total


def _signal_items(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return value["items"]
    if isinstance(value, list):
        return value
    return []


def _compact_market(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    return {
        "id": item.get("id") or item.get("localMarketId"),
        "conditionId": item.get("conditionId"),
        "yesTokenId": item.get("yesTokenId"),
        "noTokenId": item.get("noTokenId"),
        "title": compact_text(item.get("title") or item.get("slug") or "Untitled market", 120),
        "category": compact_text(item.get("category") or "market", 40),
        "volume24h": item.get("volume24h"),
        "tradeCount24h": item.get("tradeCount24h"),
        "latestPrice": item.get("latestPrice"),
        "price24hAgo": item.get("price24hAgo"),
        "change24h": item.get("change24h"),
        "bestBid": item.get("bestBid") or item.get("bid") or item.get("yesBid"),
        "bestAsk": item.get("bestAsk") or item.get("ask") or item.get("yesAsk"),
        "endDate": item.get("endDate"),
    }


def _compact_group(group: Any) -> dict[str, Any] | None:
    if not isinstance(group, dict):
        return None
    outcomes = group.get("topOutcomes") if isinstance(group.get("topOutcomes"), list) else group.get("outcomes")
    outcomes = outcomes if isinstance(outcomes, list) else []
    return {
        "title": compact_text(group.get("title") or group.get("slug") or "Untitled event", 120),
        "category": compact_text(group.get("category") or "market", 40),
        "volume24h": group.get("volume24h"),
        "tradeCount24h": group.get("tradeCount24h"),
        "outcomeCount": group.get("outcomeCount") or len(outcomes),
        "endDate": group.get("endDate"),
        "outcomes": [
            {
                "label": compact_text(outcome.get("label") or outcome.get("title"), 56),
                "yesPrice": outcome.get("yesPrice"),
                "volume24h": outcome.get("volume24h"),
                "tradeCount24h": outcome.get("tradeCount24h"),
            }
            for outcome in outcomes[:3]
            if isinstance(outcome, dict)
        ],
    }


def _compact_trade(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    return {
        "marketId": item.get("marketId") or item.get("localMarketId"),
        "conditionId": item.get("conditionId"),
        "market": compact_text(item.get("marketTitle") or item.get("title") or item.get("conditionId"), 90),
        "outcome": compact_text(item.get("outcome") or item.get("assetName"), 48),
        "side": compact_text(item.get("side") or item.get("type"), 20),
        "price": item.get("price"),
        "size": item.get("size") or item.get("amount"),
        "timestamp": item.get("timestamp") or item.get("createdAt"),
    }


def _compact_fill_tape(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    fills = item.get("recentFills") if isinstance(item.get("recentFills"), list) else []
    return {
        "marketId": item.get("marketId"),
        "conditionId": item.get("conditionId"),
        "title": compact_text(item.get("title") or "Untitled market", 120),
        "category": compact_text(item.get("category") or "market", 40),
        "snapshotLatestPrice": item.get("snapshotLatestPrice"),
        "snapshotPrice24hAgo": item.get("snapshotPrice24hAgo"),
        "snapshotChange24h": item.get("snapshotChange24h"),
        "snapshotVolume24h": item.get("snapshotVolume24h"),
        "snapshotTradeCount24h": item.get("snapshotTradeCount24h"),
        "detailLatestPrice": item.get("detailLatestPrice"),
        "detailYesPrice": item.get("detailYesPrice"),
        "detailNoPrice": item.get("detailNoPrice"),
        "detailNoAsYesPrice": item.get("detailNoAsYesPrice"),
        "fillCountLoaded": item.get("fillCountLoaded"),
        "latestFillYesPrice": item.get("latestFillYesPrice"),
        "oldestLoadedFillYesPrice": item.get("oldestLoadedFillYesPrice"),
        "recentFillDrift": item.get("recentFillDrift"),
        "fillYesPriceRange": item.get("fillYesPriceRange"),
        "fillVwapYesPrice": item.get("fillVwapYesPrice"),
        "lastFillAt": item.get("lastFillAt"),
        "fillFreshness": item.get("fillFreshness"),
        "fillFreshnessSeconds": item.get("fillFreshnessSeconds"),
        "pairedFillRatio": item.get("pairedFillRatio"),
        "priceSourceConflict": bool(item.get("priceSourceConflict")),
        "recentFills": [
            {
                "timestamp": fill.get("timestamp"),
                "outcome": compact_text(fill.get("outcome"), 12),
                "side": compact_text(fill.get("side"), 12),
                "yesPrice": fill.get("yesPrice"),
                "size": fill.get("size"),
                "txHash": compact_text(fill.get("txHash"), 24),
            }
            for fill in fills[:8]
            if isinstance(fill, dict)
        ],
    }


def _compact_content(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    return {
        "title": compact_text(item.get("title") or item.get("headline"), 120),
        "source": compact_text(item.get("source") or item.get("publisher"), 40),
        "summary": compact_text(item.get("summary") or item.get("content") or item.get("description"), 180),
        "publishedAt": item.get("publishedAt") or item.get("createdAt"),
    }


def _compact_signal(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    return {
        "title": compact_text(item.get("title") or item.get("marketTitle") or item.get("label"), 110),
        "summary": compact_text(item.get("summary") or item.get("reason") or item.get("description"), 160),
        "severity": compact_text(item.get("severity") or item.get("level"), 20),
        "score": item.get("score") or item.get("value"),
        "timestamp": item.get("timestamp") or item.get("createdAt"),
    }


def _compact_oracle(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    return {
        "title": compact_text(item.get("title") or item.get("marketTitle") or item.get("question"), 110),
        "status": compact_text(item.get("currentStatus") or item.get("status"), 40),
        "summary": compact_text(item.get("summary") or item.get("description") or item.get("resolution"), 160),
        "updatedAt": item.get("updatedAt") or item.get("timestamp"),
    }


def _compact_search_result(item: Any) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    return {
        "title": compact_text(item.get("title"), 100),
        "content": compact_text(item.get("content"), 220),
        "url": compact_text(item.get("url"), 140),
    }


def _compact_forecast_memory(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    observation = item.get("observation") if isinstance(item.get("observation"), dict) else {}
    return {
        "memoryKey": item.get("memoryKey"),
        "runId": item.get("runId"),
        "kind": item.get("kind"),
        "title": compact_text(item.get("title"), 120),
        "lesson": compact_text(item.get("lesson"), 180),
        "createdAt": item.get("createdAt"),
        "brierScore": item.get("brierScore"),
        "observation": {
            "title": compact_text(observation.get("title"), 120),
            "score": observation.get("score"),
            "latestPrice": observation.get("latestPrice"),
            "price24hAgo": observation.get("price24hAgo"),
            "drift24h": observation.get("drift24h"),
            "spread": observation.get("spread"),
            "interpretation": compact_text(observation.get("interpretation"), 180),
        },
    }


def _compact_list(values: list[Any], limit: int, mapper: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for value in values[:limit]:
        compacted = mapper(value)
        if compacted:
            output.append(compacted)
    return output


def _limit_context_chars(context: dict[str, Any], max_chars: int) -> dict[str, Any]:
    if len(json.dumps(context, ensure_ascii=False, default=str)) <= max_chars:
        return context
    reduced = dict(context)
    for key, limit in (
        ("marketCandidates", 12),
        ("markets", 8),
        ("marketGroups", 6),
        ("topMarketFillTape", 5),
        ("content", 4),
        ("oracle", 4),
        ("alphaSignals", 4),
        ("whaleSignals", 4),
        ("suspiciousSignals", 4),
        ("trades", 4),
        ("searchResults", 2),
        ("forecastMemory", 8),
    ):
        if isinstance(reduced.get(key), list):
            reduced[key] = reduced[key][:limit]
    if len(json.dumps(reduced, ensure_ascii=False, default=str)) <= max_chars:
        return reduced
    for key in ("trades", "oracle", "alphaSignals", "whaleSignals", "suspiciousSignals", "searchResults"):
        reduced[key] = []
    if len(json.dumps(reduced, ensure_ascii=False, default=str)) <= max_chars:
        return reduced
    reduced["forecastMemory"] = reduced.get("forecastMemory", [])[:4] if isinstance(reduced.get("forecastMemory"), list) else []
    reduced["markets"] = reduced.get("markets", [])[:4] if isinstance(reduced.get("markets"), list) else []
    reduced["marketGroups"] = reduced.get("marketGroups", [])[:3] if isinstance(reduced.get("marketGroups"), list) else []
    reduced["topMarketFillTape"] = reduced.get("topMarketFillTape", [])[:3] if isinstance(reduced.get("topMarketFillTape"), list) else []
    reduced["marketCandidates"] = reduced.get("marketCandidates", [])[:8] if isinstance(reduced.get("marketCandidates"), list) else []
    return reduced


def _build_agent_context(payload: dict[str, Any], lens: str, search_results: list[dict[str, str]]) -> dict[str, Any]:
    max_chars = max(4_000, get_int_env("POLYDATA_AGENT_CONTEXT_MAX_CHARS", 24_000))
    context = {
        "lens": lens,
        "metrics": _summary_metrics(payload),
        "marketCandidates": _compact_list(_market_candidates(payload), 18, lambda item: {
            "id": item.get("id"),
            "conditionId": item.get("conditionId"),
            "title": compact_text(item.get("title"), 120),
            "category": compact_text(item.get("category"), 40),
            "kind": item.get("kind"),
            "volume24h": item.get("volume24h"),
            "tradeCount24h": item.get("tradeCount24h"),
            "latestPrice": item.get("latestPrice"),
            "price24hAgo": item.get("price24hAgo"),
            "change24h": item.get("change24h"),
            "bestBid": item.get("bestBid"),
            "bestAsk": item.get("bestAsk"),
            "outcomeCount": item.get("outcomeCount"),
            "endDate": item.get("endDate"),
        }),
        "markets": _compact_list(_items(payload, "markets"), 12, _compact_market),
        "marketGroups": _compact_list(_items(payload, "marketGroups"), 10, _compact_group),
        "topMarketFillTape": _compact_list(_items(payload, "topMarketFillTape"), 8, _compact_fill_tape),
        "trades": _compact_list(_items(payload, "trades"), 6, _compact_trade),
        "oracle": _compact_list(_items(payload, "oracle"), 6, _compact_oracle),
        "content": _compact_list(_items(payload, "content"), 6, _compact_content),
        "alphaSignals": _compact_list(_signal_items(payload, "alphaSignals"), 5, _compact_signal),
        "whaleSignals": _compact_list(_signal_items(payload, "whaleSignals"), 5, _compact_signal),
        "suspiciousSignals": _compact_list(_signal_items(payload, "suspiciousSignals"), 5, _compact_signal),
        "searchResults": _compact_list(search_results, 3, _compact_search_result),
        "forecastMemory": _compact_list(_items(payload, "forecastMemory"), 12, _compact_forecast_memory),
    }
    context = _limit_context_chars(context, max_chars)
    context["contextChars"] = len(json.dumps(context, ensure_ascii=False, default=str))
    context["contextMaxChars"] = max_chars
    return context


def _market_reason(candidate: dict[str, Any]) -> tuple[str, str, str]:
    volume = _as_float(candidate.get("volume24h"))
    trades = _as_float(candidate.get("tradeCount24h"))
    price = _as_float(candidate.get("latestPrice"))
    outcome_count = _as_float(candidate.get("outcomeCount"))
    if volume > 0 and volume >= 10_000:
        return ("Liquidity spike", "Volume is unusually visible versus the rest of the loaded market set.", _fmt_currency(volume))
    if trades >= 20:
        return ("Tape active", "Trade count suggests this market is drawing attention today.", f"{_fmt_compact(trades)} trades")
    if price and 0.42 <= price <= 0.58:
        return ("Knife-edge odds", "Pricing is close to 50/50, so small catalysts can move the market quickly.", f"{price * 100:.0f}%")
    if outcome_count >= 8:
        return ("Crowded outcome set", "Many outcomes make this event useful for reading broad narrative dispersion.", f"{_fmt_compact(outcome_count)} outcomes")
    return ("Narrative watch", "This market is part of the current loaded universe and may anchor user attention.", str(candidate.get("category") or "market"))


def _special_markets(payload: dict[str, Any], limit: int = 4) -> list[dict[str, str]]:
    candidates = _market_candidates(payload)
    ranked = sorted(
        candidates,
        key=lambda item: (
            _as_float(item.get("volume24h")) * 3
            + _as_float(item.get("tradeCount24h")) * 250
            + (1500 if 0.42 <= _as_float(item.get("latestPrice")) <= 0.58 and _as_float(item.get("latestPrice")) else 0)
            + _as_float(item.get("outcomeCount")) * 80
        ),
        reverse=True,
    )
    output: list[dict[str, str]] = []
    for candidate in ranked[:limit]:
        trend, why, evidence = _market_reason(candidate)
        severity = "warning" if trend in {"Liquidity spike", "Knife-edge odds"} else "neutral"
        output.append({
            "title": compact_text(candidate.get("title"), 90),
            "why": compact_text(why, 150),
            "trend": trend,
            "severity": severity,
            "evidence": evidence,
        })
    return output


def _fallback_themes(payload: dict[str, Any], lens: str) -> list[dict[str, str]]:
    metrics = _summary_metrics(payload)
    top_categories = ", ".join(metrics["topCategories"]) or "category data loading"
    lead_market = (_special_markets(payload, limit=1) or [{"title": "No standout market yet"}])[0]["title"]
    if lens == "special":
        return [
            {
                "label": "SPECIAL",
                "title": "Unusual-market radar",
                "summary": f"{lead_market} is the strongest current candidate for a closer read.",
                "severity": "neutral",
                "evidence": f"{metrics['coveredMarkets']} covered",
            },
            {
                "label": "ATTENTION",
                "title": "Where attention clusters",
                "summary": top_categories,
                "severity": "neutral",
                "evidence": "categories",
            },
        ]
    if lens == "trend":
        return [
            {
                "label": "TREND",
                "title": "Polymarket narrative breadth",
                "summary": f"Attention is rotating around {top_categories}; watch whether one category becomes the dominant narrative.",
                "severity": "neutral",
                "evidence": f"{metrics['coveredMarkets']} covered",
            },
            {
                "label": "CATALYSTS",
                "title": "Catalyst feed",
                "summary": "News and signal feeds are being used to connect market moves with outside catalysts.",
                "severity": "positive" if metrics["contentItems"] else "warning",
                "evidence": f"{metrics['contentItems']} items",
            },
        ]
    return [
        {
            "label": "BREADTH",
            "title": "Market universe",
            "summary": f"The strongest current read starts with {lead_market}.",
            "severity": "positive" if metrics["coveredMarkets"] else "neutral",
            "evidence": f"{metrics['coveredMarkets']} covered",
        },
        {
            "label": "CONVERGENCE",
            "title": "Where Polymarket attention sits",
            "summary": top_categories,
            "severity": "neutral",
            "evidence": "categories",
        },
    ]


def _fallback_watchlist(payload: dict[str, Any], lens: str) -> list[dict[str, str]]:
    special = _special_markets(payload, limit=2)
    watchlist = [
        {
            "title": item["title"],
            "reason": item["why"],
            "horizon": "today",
            "severity": item["severity"],
        }
        for item in special
    ]
    if lens == "trend":
        watchlist.append({
            "title": "Narrative rotation",
            "reason": "Watch whether volume migrates from isolated events into a category-wide theme.",
            "horizon": "24h",
            "severity": "neutral",
        })
    else:
        watchlist.append({
            "title": "Fresh catalysts",
            "reason": "New information can turn a quiet market into the day's focal point.",
            "horizon": "today",
            "severity": "neutral",
        })
    return watchlist[:3]


def _summary_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    markets = _items(payload, "markets")
    groups = _items(payload, "marketGroups")
    candidates = _market_candidates(payload)
    trades = _items(payload, "trades")
    oracle = _items(payload, "oracle")
    content = _items(payload, "content")
    whales = _signal_items(payload, "whaleSignals")
    suspicious = _signal_items(payload, "suspiciousSignals")
    alpha = _signal_items(payload, "alphaSignals")
    fill_tape = _items(payload, "topMarketFillTape")
    return {
        "activeMarkets": len(markets),
        "marketGroups": len(groups),
        "coveredMarkets": len(candidates),
        "fillTapeMarkets": len(fill_tape),
        "fillTapeConflicts": sum(1 for item in fill_tape if isinstance(item, dict) and item.get("priceSourceConflict")),
        "topCategories": _top_categories(candidates),
        "visible24hVolume": _fmt_currency(_volume_total(candidates)),
        "tradeRows": len(trades),
        "oracleEvents": len(oracle),
        "contentItems": len(content),
        "whaleSignals": len(whales),
        "suspiciousSignals": len(suspicious),
        "alphaSignals": len(alpha),
    }


def _search_query(payload: dict[str, Any], lens: str) -> str:
    markets = _market_candidates(payload)
    titles = " ".join(str(item.get("title") or "") for item in markets[:6])
    categories = " ".join(_top_categories(markets))
    if lens == "special":
        prefix = "Polymarket unusual markets today volume probability trend"
    elif lens == "trend":
        prefix = "Polymarket market trends macro narratives prediction markets today"
    else:
        prefix = "Polymarket market brief today special markets catalysts trends"
    return compact_text(f"{prefix} {categories} {titles}", 320)


def _fallback_focus(payload: dict[str, Any], lens: str, search_results: list[dict[str, str]]) -> list[dict[str, str]]:
    focus = _fallback_themes(payload, lens)
    if search_results:
        top = search_results[0]
        focus.insert(1, {
            "label": "NEWS",
            "title": compact_text(top.get("title") or "External context", 80),
            "summary": compact_text(top.get("content") or "External market context is available.", 180),
            "severity": "neutral",
            "evidence": "Tavily",
        })
    return focus[:5]


def _fallback_response(payload: dict[str, Any], lens: str, *, reason: str, search_results: list[dict[str, str]] | None = None) -> dict[str, Any]:
    search_results = search_results or []
    metrics = _summary_metrics(payload)
    special = _special_markets(payload)
    categories = ", ".join(metrics["topCategories"]) or "categories still loading"
    if lens == "special":
        lead = special[0]["title"] if special else "No standout market"
        brief = f"{lead} is the clearest unusual market on the board. Attention is also clustering around {categories}."
    elif lens == "trend":
        brief = f"Polymarket attention is rotating toward {categories}. Watch whether isolated event interest turns into a category-wide trend."
    else:
        lead = special[0]["title"] if special else categories
        brief = f"{lead} is anchoring the current market-wide read. The broader board is clustering around {categories}."
    evidence = [
        f"{metrics['coveredMarkets']} covered markets",
        f"{metrics['tradeRows']} recent trades",
        f"{len(special)} special markets",
        f"{metrics['visible24hVolume']} visible volume",
    ]
    if search_results:
        evidence[-1] = compact_text(search_results[0].get("title") or evidence[-1], 120)
    return {
        "status": "search-fallback" if reason == "agent-error" and search_results else reason,
        "lens": lens,
        "forecastRunId": compact_text(payload.get("forecastRunId") or payload.get("forecast_run_id"), 48),
        "generatedAt": _utc_now_iso(),
        "model": "deterministic-fallback",
        "brief": compact_text(brief, 260),
        "focus": _fallback_focus(payload, lens, search_results),
        "specialMarkets": special,
        "themes": _fallback_themes(payload, lens),
        "watchlist": _fallback_watchlist(payload, lens),
        "evidence": evidence[:4],
        "metrics": metrics,
        "searchResults": search_results,
        "error": reason,
    }


def _normalize(raw: dict[str, Any], payload: dict[str, Any], lens: str, search_results: list[dict[str, str]], model: str) -> dict[str, Any]:
    fallback = _fallback_response(payload, lens, reason="fallback")
    focus_items = raw.get("focus") if isinstance(raw.get("focus"), list) else []
    focus: list[dict[str, str]] = []
    for item in focus_items[:5]:
        if not isinstance(item, dict):
            continue
        focus.append({
            "label": compact_text(item.get("label") or "SIGNAL", 16).upper(),
            "title": compact_text(item.get("title") or "Market-wide signal", 80),
            "summary": compact_text(item.get("summary") or "", 180),
            "severity": compact_text(item.get("severity") or "neutral", 20).lower(),
            "evidence": compact_text(item.get("evidence") or "", 80),
        })
    evidence = raw.get("evidence") if isinstance(raw.get("evidence"), list) else fallback["evidence"]
    raw_special = raw.get("specialMarkets") if isinstance(raw.get("specialMarkets"), list) else []
    special_markets: list[dict[str, str]] = []
    for item in raw_special[:4]:
        if not isinstance(item, dict):
            continue
        special_markets.append({
            "title": compact_text(item.get("title") or "Special market", 90),
            "why": compact_text(item.get("why") or item.get("summary") or "", 160),
            "trend": compact_text(item.get("trend") or "Watch", 40),
            "severity": compact_text(item.get("severity") or "neutral", 20).lower(),
            "evidence": compact_text(item.get("evidence") or "", 80),
        })
    raw_themes = raw.get("themes") if isinstance(raw.get("themes"), list) else []
    themes: list[dict[str, str]] = []
    for item in raw_themes[:4]:
        if not isinstance(item, dict):
            continue
        themes.append({
            "label": compact_text(item.get("label") or "THEME", 16).upper(),
            "title": compact_text(item.get("title") or "Market theme", 80),
            "summary": compact_text(item.get("summary") or "", 180),
            "severity": compact_text(item.get("severity") or "neutral", 20).lower(),
            "evidence": compact_text(item.get("evidence") or "", 80),
        })
    raw_watchlist = raw.get("watchlist") if isinstance(raw.get("watchlist"), list) else []
    watchlist: list[dict[str, str]] = []
    for item in raw_watchlist[:4]:
        if not isinstance(item, dict):
            continue
        watchlist.append({
            "title": compact_text(item.get("title") or "Watch item", 90),
            "reason": compact_text(item.get("reason") or item.get("summary") or "", 160),
            "horizon": compact_text(item.get("horizon") or "today", 24),
            "severity": compact_text(item.get("severity") or "neutral", 20).lower(),
        })
    return {
        "status": "live",
        "lens": lens,
        "forecastRunId": compact_text(payload.get("forecastRunId") or payload.get("forecast_run_id"), 48),
        "generatedAt": _utc_now_iso(),
        "model": model,
        "brief": compact_text(raw.get("brief") or fallback["brief"], 260),
        "focus": focus or fallback["focus"],
        "specialMarkets": special_markets or fallback["specialMarkets"],
        "themes": themes or fallback["themes"],
        "watchlist": watchlist or fallback["watchlist"],
        "evidence": [compact_text(item, 120) for item in evidence[:4]],
        "metrics": _summary_metrics(payload),
        "searchResults": search_results,
    }


def build_market_wide_insight(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _fallback_response({}, "overview", reason="invalid-payload")
    lens = str(payload.get("lens") or "overview").strip().lower()
    lens = LENS_ALIASES.get(lens, lens)
    if lens not in VALID_LENSES:
        lens = "overview"
    search_results: list[dict[str, str]] = []
    try:
        search_results = TavilySearchClient().search(_search_query(payload, lens))
    except Exception:
        search_results = []
    context = _build_agent_context(payload, lens, search_results)
    client = OpenAICompatibleClient()
    if graph_enabled():
        return run_forecast_intelligence_graph(
            payload,
            lens,
            context,
            search_results,
            client,
            normalize=_normalize,
            fallback=lambda source_payload, source_lens, reason, results: _fallback_response(
                source_payload,
                source_lens,
                reason=reason,
                search_results=results,
            ),
        )
    if not client.configured:
        return _fallback_response(payload, lens, reason="missing-api-key", search_results=search_results)
    try:
        prompt = USER_PROMPT_TEMPLATE.replace("{lens}", lens).replace("{context_json}", json.dumps(context, ensure_ascii=False, default=str))
        raw_text = client.complete_json(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=950,
            workflow_name=f"polydata-market-wide-{lens}",
        )
        raw = extract_json_object(raw_text)
        response = _normalize(raw, payload, lens, search_results, client.model)
        response["agentRuntime"] = client.last_usage.runtime
        response["usage"] = {
            "inputTokens": client.last_usage.input_tokens,
            "outputTokens": client.last_usage.output_tokens,
            "totalTokens": client.last_usage.total_tokens,
            "inputChars": client.last_usage.input_chars,
            "contextChars": context.get("contextChars"),
        }
        return response
    except Exception as exc:
        response = _fallback_response(payload, lens, reason="agent-error", search_results=search_results)
        response["error"] = compact_text(str(exc), 180)
        return response


def build_market_wide_fallback(payload: dict[str, Any], *, reason: str = "cache-warming") -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _fallback_response({}, "overview", reason="invalid-payload")
    lens = str(payload.get("lens") or "overview").strip().lower()
    lens = LENS_ALIASES.get(lens, lens)
    if lens not in VALID_LENSES:
        lens = "overview"
    if graph_enabled():
        context = _build_agent_context(payload, lens, [])
        return run_forecast_intelligence_graph(
            payload,
            lens,
            context,
            [],
            _DeterministicFallbackClient(),
            normalize=_normalize,
            fallback=lambda source_payload, source_lens, _node_reason, results: _fallback_response(
                source_payload,
                source_lens,
                reason=reason,
                search_results=results,
            ),
        )
    return _fallback_response(payload, lens, reason=reason)
