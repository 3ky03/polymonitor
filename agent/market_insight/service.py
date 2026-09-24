from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from agent.common.env import get_int_env
from agent.common.json_utils import compact_text, extract_json_object
from agent.common.llm_client import OpenAICompatibleClient
from agent.common.tavily_client import TavilySearchClient

from .prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric == numeric else None


def _fmt_percent(value: Any) -> str:
    numeric = _as_float(value)
    if numeric is None:
        return "--"
    return f"{numeric * 100:.1f}%"


def _fmt_compact_currency(value: Any) -> str:
    numeric = _as_float(value)
    if numeric is None:
        return "--"
    if abs(numeric) >= 1_000_000:
        return f"${numeric / 1_000_000:.1f}M"
    if abs(numeric) >= 1_000:
        return f"${numeric / 1_000:.1f}K"
    return f"${numeric:.0f}"


def _market_title(payload: dict[str, Any]) -> str:
    market = payload.get("market") if isinstance(payload.get("market"), dict) else {}
    group = payload.get("selectedGroup") if isinstance(payload.get("selectedGroup"), dict) else {}
    return compact_text(market.get("title") or group.get("title") or payload.get("title") or "Selected market", 220)


def _build_search_query(payload: dict[str, Any]) -> str:
    market = payload.get("market") if isinstance(payload.get("market"), dict) else {}
    tags = market.get("tags") if isinstance(market.get("tags"), list) else []
    title = _market_title(payload)
    category = market.get("category") or ""
    clean_tags = " ".join(str(tag) for tag in tags[:4] if tag)
    return compact_text(f"{title} {category} {clean_tags} latest context", 300)


def _compact_market(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "title": compact_text(value.get("title") or value.get("slug") or "Selected market", 140),
        "category": compact_text(value.get("category"), 40),
        "volume24h": value.get("volume24h"),
        "tradeCount24h": value.get("tradeCount24h"),
        "latestPrice": value.get("latestPrice"),
        "endDate": value.get("endDate"),
        "rules": compact_text(value.get("rules") or value.get("description"), 220),
    }


def _compact_group(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    outcomes = value.get("topOutcomes") if isinstance(value.get("topOutcomes"), list) else value.get("outcomes")
    outcomes = outcomes if isinstance(outcomes, list) else []
    return {
        "title": compact_text(value.get("title") or value.get("slug"), 140),
        "category": compact_text(value.get("category"), 40),
        "volume24h": value.get("volume24h"),
        "tradeCount24h": value.get("tradeCount24h"),
        "outcomes": [
            {
                "label": compact_text(outcome.get("label") or outcome.get("title"), 56),
                "yesPrice": outcome.get("yesPrice"),
                "volume24h": outcome.get("volume24h"),
            }
            for outcome in outcomes[:4]
            if isinstance(outcome, dict)
        ],
    }


def _compact_trade(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "outcome": compact_text(value.get("outcome") or value.get("assetName"), 48),
        "side": compact_text(value.get("side") or value.get("type"), 20),
        "price": value.get("price"),
        "size": value.get("size") or value.get("amount"),
        "timestamp": value.get("timestamp") or value.get("createdAt"),
    }


def _compact_content(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "title": compact_text(value.get("title") or value.get("headline"), 120),
        "source": compact_text(value.get("source") or value.get("publisher"), 40),
        "summary": compact_text(value.get("summary") or value.get("content") or value.get("description"), 180),
        "publishedAt": value.get("publishedAt") or value.get("createdAt"),
    }


def _compact_oracle(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "status": compact_text(value.get("currentStatus") or value.get("status"), 40),
        "resolution": compact_text(value.get("resolution") or value.get("description") or value.get("summary"), 220),
        "updatedAt": value.get("updatedAt") or value.get("timestamp"),
    }


def _compact_search_result(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    return {
        "title": compact_text(value.get("title"), 100),
        "content": compact_text(value.get("content"), 220),
        "url": compact_text(value.get("url"), 140),
    }


def _compact_list(values: Any, limit: int, mapper: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
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
    reduced["trades"] = reduced.get("trades", [])[:4] if isinstance(reduced.get("trades"), list) else []
    reduced["content"] = reduced.get("content", [])[:3] if isinstance(reduced.get("content"), list) else []
    reduced["searchResults"] = reduced.get("searchResults", [])[:2] if isinstance(reduced.get("searchResults"), list) else []
    if len(json.dumps(reduced, ensure_ascii=False, default=str)) <= max_chars:
        return reduced
    reduced["trades"] = []
    reduced["content"] = []
    reduced["searchResults"] = []
    return reduced


def _build_agent_context(payload: dict[str, Any], search_results: list[dict[str, str]]) -> dict[str, Any]:
    max_chars = max(3_000, get_int_env("POLYDATA_AGENT_CONTEXT_MAX_CHARS", 16_000))
    context = {
        "market": _compact_market(payload.get("market")),
        "selectedGroup": _compact_group(payload.get("selectedGroup")),
        "selectedOutcome": compact_text(payload.get("selectedOutcome"), 80),
        "price": payload.get("price") if isinstance(payload.get("price"), dict) else {},
        "lob": payload.get("lob") if isinstance(payload.get("lob"), dict) else {},
        "trades": _compact_list(payload.get("trades"), 6, _compact_trade),
        "oracle": _compact_oracle(payload.get("oracle")),
        "content": _compact_list(payload.get("content"), 4, _compact_content),
        "searchResults": _compact_list(search_results, 3, _compact_search_result),
    }
    context = _limit_context_chars(context, max_chars)
    context["contextChars"] = len(json.dumps(context, ensure_ascii=False, default=str))
    context["contextMaxChars"] = max_chars
    return context


def _fallback_focus(payload: dict[str, Any]) -> list[dict[str, str]]:
    price = payload.get("price") if isinstance(payload.get("price"), dict) else {}
    lob = payload.get("lob") if isinstance(payload.get("lob"), dict) else {}
    trades = payload.get("trades") if isinstance(payload.get("trades"), list) else []
    oracle = payload.get("oracle") if isinstance(payload.get("oracle"), dict) else {}
    yes_price = price.get("latestYesPrice") or price.get("latestPrice")
    no_price = price.get("latestNoPrice")
    if no_price in (None, "") and _as_float(yes_price) is not None:
        no_price = 1 - float(yes_price)
    yes_lob = lob.get("yes") if isinstance(lob.get("yes"), dict) else {}
    spread = yes_lob.get("spread")
    if _as_float(spread) is not None:
        spread_text = _fmt_percent(spread)
    else:
        spread_text = "--"
    return [
        {
            "label": "ODDS",
            "title": "Market is balanced" if abs((_as_float(yes_price) or 0.5) - 0.5) < 0.08 else "Odds are skewed",
            "summary": f"YES is {_fmt_percent(yes_price)} and NO is {_fmt_percent(no_price)}.",
            "severity": "neutral",
            "evidence": f"YES {_fmt_percent(yes_price)}",
        },
        {
            "label": "LIQUIDITY",
            "title": "Book depth needs attention" if spread_text not in {"--", "0.0%"} else "Book depth is thin",
            "summary": f"Current visible YES spread is {spread_text}.",
            "severity": "warning" if spread_text not in {"--", "0.0%"} else "neutral",
            "evidence": f"spread {spread_text}",
        },
        {
            "label": "FLOW",
            "title": "Recent tape available" if trades else "Trade tape is quiet",
            "summary": f"{len(trades)} recent trade rows are loaded for this market.",
            "severity": "positive" if trades else "neutral",
            "evidence": f"{len(trades)} rows",
        },
        {
            "label": "ORACLE",
            "title": "Resolution state visible" if oracle.get("currentStatus") else "Oracle data pending",
            "summary": f"Oracle status is {oracle.get('currentStatus') or 'not loaded'}.",
            "severity": "neutral",
            "evidence": str(oracle.get("currentStatus") or "--"),
        },
    ]


def _fallback_response(payload: dict[str, Any], *, reason: str = "fallback", search_results: list[dict[str, str]] | None = None) -> dict[str, Any]:
    price = payload.get("price") if isinstance(payload.get("price"), dict) else {}
    volume = price.get("volume24h") or payload.get("volume24h")
    trades = price.get("tradeCount24h")
    title = _market_title(payload)
    search_results = search_results or []
    focus = _fallback_focus(payload)
    if search_results:
        top_result = search_results[0]
        focus.insert(
            1,
            {
                "label": "NEWS",
                "title": compact_text(top_result.get("title") or "External context available", 80),
                "summary": compact_text(top_result.get("content") or "Tavily returned current external context for this market.", 180),
                "severity": "neutral",
                "evidence": "Tavily",
            },
        )
    evidence = [
        f"YES { _fmt_percent(price.get('latestYesPrice') or price.get('latestPrice')) }",
        f"24h volume { _fmt_compact_currency(volume) }",
        f"24h trades {trades if trades not in (None, '') else '--'}",
    ]
    if search_results:
        evidence.append(compact_text(search_results[0].get("title") or "external context", 120))
    return {
        "status": "search-fallback" if reason == "agent-error" and search_results else reason,
        "generatedAt": _utc_now_iso(),
        "model": "deterministic-fallback",
        "brief": compact_text(
            f"{title} is trading near {_fmt_percent(price.get('latestYesPrice') or price.get('latestPrice'))}. "
            f"24h volume is {_fmt_compact_currency(volume)} with {trades if trades not in (None, '') else '--'} trades."
        ),
        "focus": focus[:5],
        "evidence": evidence[:4],
        "searchResults": search_results,
        "error": reason,
    }


def _normalize_model_response(raw: dict[str, Any], payload: dict[str, Any], search_results: list[dict[str, str]], model: str) -> dict[str, Any]:
    fallback = _fallback_response(payload, reason="fallback")
    focus_items = raw.get("focus") if isinstance(raw.get("focus"), list) else []
    normalized_focus: list[dict[str, str]] = []
    for item in focus_items[:5]:
        if not isinstance(item, dict):
            continue
        normalized_focus.append(
            {
                "label": compact_text(item.get("label") or "SIGNAL", 16).upper(),
                "title": compact_text(item.get("title") or "Market signal", 80),
                "summary": compact_text(item.get("summary") or "", 180),
                "severity": compact_text(item.get("severity") or "neutral", 20).lower(),
                "evidence": compact_text(item.get("evidence") or "", 80),
            }
        )
    evidence = raw.get("evidence") if isinstance(raw.get("evidence"), list) else fallback["evidence"]
    return {
        "status": "live",
        "generatedAt": _utc_now_iso(),
        "model": model,
        "brief": compact_text(raw.get("brief") or fallback["brief"], 260),
        "focus": normalized_focus or fallback["focus"],
        "evidence": [compact_text(item, 120) for item in evidence[:4]],
        "searchResults": search_results,
    }


def build_market_insight(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _fallback_response({}, reason="invalid-payload")
    search_results: list[dict[str, str]] = []
    try:
        search_results = TavilySearchClient().search(_build_search_query(payload))
    except Exception:
        search_results = []

    context = _build_agent_context(payload, search_results)
    client = OpenAICompatibleClient()
    if not client.configured:
        return _fallback_response(payload, reason="missing-api-key")
    try:
        prompt = USER_PROMPT_TEMPLATE.replace("{context_json}", json.dumps(context, ensure_ascii=False, default=str))
        raw_text = client.complete_json(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=900,
            workflow_name="polydata-market-insight",
        )
        raw = extract_json_object(raw_text)
        response = _normalize_model_response(raw, payload, search_results, client.model)
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
        response = _fallback_response(payload, reason="agent-error", search_results=search_results)
        response["error"] = compact_text(str(exc), 180)
        return response


def build_market_insight_fallback(payload: dict[str, Any], *, reason: str = "cache-warming") -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _fallback_response({}, reason="invalid-payload")
    return _fallback_response(payload, reason=reason)
