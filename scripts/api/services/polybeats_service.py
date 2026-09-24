from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional

from agent.common.budget import claim_agent_live_call
from agent.common.json_utils import compact_text, extract_json_object
from agent.common.llm_client import OpenAICompatibleClient

from . import address_intel_service, signal_cluster_service


PANEL_ID = "polybeats-feed"
SNAPSHOT_NAMESPACE = "snapshot:signals:polybeats"
DEFAULT_LIMIT = 8
MIN_CLUSTER_NOTIONAL = Decimal("250")


def build_polybeats_cache_key(limit: int = DEFAULT_LIMIT) -> str:
    return json.dumps({"limit": int(limit or DEFAULT_LIMIT)}, sort_keys=True, ensure_ascii=True)


def _safe_decimal(ctx: dict, value: Any, default: Decimal = Decimal("0")) -> Decimal:
    parsed = ctx["_safe_decimal"](value)
    return parsed if parsed is not None else default


def _format_money(value: Any) -> str:
    try:
        amount = Decimal(str(value or "0"))
    except Exception:
        amount = Decimal("0")
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount >= Decimal("1000000"):
        return f"{sign}${(amount / Decimal('1000000')).quantize(Decimal('0.1'))}M"
    if amount >= Decimal("1000"):
        return f"{sign}${(amount / Decimal('1000')).quantize(Decimal('0.1'))}k"
    return f"{sign}${amount.quantize(Decimal('1'))}"


def _short_address(address: str) -> str:
    if len(address) < 12:
        return address
    return f"{address[:6]}...{address[-4:]}"


def _normalize_addresses(ctx: dict, addresses: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for value in addresses:
        address = ctx["normalize_address"](value)
        if not address or address in seen:
            continue
        seen.add(address)
        normalized.append(address)
    return normalized


def _market_domain(market: Optional[Dict[str, Any]], title: str) -> str:
    text = " ".join(
        str(part or "").lower()
        for part in (
            (market or {}).get("category"),
            (market or {}).get("slug"),
            title,
        )
    )
    if any(term in text for term in ("soccer", "football", "world cup", "champions league", "uefa", "fifa", "nba", "nfl", "mlb", "tennis")):
        return "SPORTS"
    if any(term in text for term in ("bitcoin", "ethereum", "crypto", "token", "airdrop", "tge", "fdv")):
        return "CRYPTO"
    if any(term in text for term in ("election", "trump", "biden", "senate", "president", "parliament", "government")):
        return "GOVERNMENT"
    if any(term in text for term in ("iran", "israel", "ukraine", "war", "peace", "ceasefire", "sanction", "invasion")):
        return "CONFLICT"
    if any(term in text for term in ("fed", "cpi", "rate", "inflation", "oil", "stock", "nasdaq", "s&p", "recession")):
        return "ECONOMIC"
    if any(term in text for term in ("openai", "anthropic", "claude", "gpt", "ai", "model")):
        return "TECH"
    return "PMKT"


def _get_markets(ctx: dict, market_ids: Iterable[Any]) -> Dict[int, Dict[str, Any]]:
    ids = []
    for market_id in market_ids:
        try:
            ids.append(int(market_id))
        except (TypeError, ValueError):
            continue
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    try:
        rows = ctx["query_all"](
            f"""
            SELECT id, title, slug, category, end_date
            FROM markets
            WHERE id IN ({placeholders})
            """,
            tuple(ids),
        )
    except Exception:
        logger = getattr(ctx.get("app"), "logger", None)
        if logger is not None:
            logger.exception("polybeats market metadata query failed")
        rows = []
    return {int(row.get("id")): row for row in rows if row.get("id") is not None}


def _address_cash_pnl(ctx: dict, addresses: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    normalized = _normalize_addresses(ctx, addresses)
    stats = {
        address: {
            "tradeCashPnl": Decimal("0"),
            "redeemCashflow": Decimal("0"),
            "netCashPnlProxy": Decimal("0"),
            "pnlSource": "none",
        }
        for address in normalized
    }
    if not normalized:
        return {}
    placeholders = ", ".join("?" for _ in normalized)
    if ctx["table_exists"]("pnl_trade_cashflows"):
        try:
            rows = ctx["query_all"](
                f"""
                SELECT
                    LOWER(address) AS address,
                    SUM(CASE
                        WHEN UPPER(side) = 'SELL' THEN usdc_amount
                        WHEN UPPER(side) = 'BUY' THEN -usdc_amount
                        ELSE 0
                    END) AS trade_cash_pnl,
                    COUNT(*) AS cashflow_count
                FROM pnl_trade_cashflows
                WHERE LOWER(address) IN ({placeholders})
                GROUP BY LOWER(address)
                """,
                tuple(normalized),
            )
            for row in rows:
                address = ctx["normalize_address"](row.get("address"))
                if address in stats:
                    stats[address]["tradeCashPnl"] = _safe_decimal(ctx, row.get("trade_cash_pnl"))
                    stats[address]["cashflowCount"] = int(row.get("cashflow_count") or 0)
                    stats[address]["pnlSource"] = "trade-cashflow"
        except Exception:
            logger = getattr(ctx.get("app"), "logger", None)
            if logger is not None:
                logger.exception("polybeats trade pnl query failed")
    if ctx["table_exists"]("non_trade_cashflows"):
        try:
            rows = ctx["query_all"](
                f"""
                SELECT
                    LOWER(address) AS address,
                    SUM(CASE
                        WHEN UPPER(cashflow_type) IN ('REDEEM', 'MAKER_REBATE') THEN usdc_amount
                        ELSE 0
                    END) AS redeem_cashflow
                FROM non_trade_cashflows
                WHERE LOWER(address) IN ({placeholders})
                GROUP BY LOWER(address)
                """,
                tuple(normalized),
            )
            for row in rows:
                address = ctx["normalize_address"](row.get("address"))
                if address in stats:
                    stats[address]["redeemCashflow"] = _safe_decimal(ctx, row.get("redeem_cashflow"))
                    stats[address]["pnlSource"] = "cashflow-proxy"
        except Exception:
            logger = getattr(ctx.get("app"), "logger", None)
            if logger is not None:
                logger.exception("polybeats non-trade pnl query failed")
    for address, item in stats.items():
        item["netCashPnlProxy"] = item["tradeCashPnl"] + item["redeemCashflow"]
        item["netCashPnlProxyText"] = ctx["format_trade_decimal"](item["netCashPnlProxy"])
        item["tradeCashPnlText"] = ctx["format_trade_decimal"](item["tradeCashPnl"])
        item["redeemCashflowText"] = ctx["format_trade_decimal"](item["redeemCashflow"])
    return stats


def _smart_score(ctx: dict, profile: Dict[str, Any], pnl: Dict[str, Any], market_domain: str) -> Decimal:
    trade_count = Decimal(str(profile.get("tradeCount") or 0))
    volume = _safe_decimal(ctx, profile.get("volumeNotional"))
    active_markets = Decimal(str(profile.get("activeMarkets") or 0))
    pnl_value = _safe_decimal(ctx, pnl.get("netCashPnlProxy"))
    score = Decimal("0")
    score += min(Decimal("25"), trade_count / Decimal("10"))
    score += min(Decimal("30"), volume / Decimal("5000"))
    score += min(Decimal("15"), active_markets)
    if pnl_value > 0:
        score += min(Decimal("30"), pnl_value / Decimal("3000"))
    if market_domain in {"SPORTS", "CONFLICT", "CRYPTO", "GOVERNMENT"} and active_markets >= 5:
        score += Decimal("5")
    return score.quantize(Decimal("0.1"))


def _top_wallet(wallets: list[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not wallets:
        return None
    return max(wallets, key=lambda item: (Decimal(str(item.get("smartScore") or "0")), Decimal(str(item.get("marketVolumeNotional") or "0"))))


def _template_explanation(item: Dict[str, Any], top_wallet: Optional[Dict[str, Any]]) -> str:
    metrics = item.get("metrics") or {}
    total = _format_money(metrics.get("totalNotional") or item.get("notional"))
    avg = metrics.get("avgPrice") or item.get("price") or "--"
    current = metrics.get("currentProbability") or "--"
    accounts = metrics.get("accountCount") or len(item.get("wallets") or [])
    trades = metrics.get("tradeCount") or "--"
    base = (
        f"{accounts} wallet(s) clustered across {trades} recent fills, putting {total} on "
        f"{str(item.get('outcome') or '').upper()} at average entry {avg}; current probability is {current}."
    )
    if top_wallet:
        pnl = top_wallet.get("netCashPnlProxy")
        pnl_text = _format_money(pnl) if pnl is not None else "--"
        base += (
            f" Lead wallet {_short_address(str(top_wallet.get('address') or ''))} has "
            f"{top_wallet.get('tradeCount') or 0} tracked trades, {top_wallet.get('activeMarkets') or 0} active markets, "
            f"and cash PnL proxy {pnl_text}."
        )
    related = item.get("relatedContent") or []
    if related:
        base += f" Related intel: {related[0].get('title') or related[0].get('source') or 'linked source'}."
    base += " Treat as flow intelligence, not a final-resolution forecast."
    return base


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _llm_enabled() -> bool:
    raw = os.environ.get("POLYDATA_POLYBEATS_LLM_ENABLED")
    if raw is not None and raw.strip():
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return _truthy_env("POLYDATA_AGENT_ENABLED", False)


def _llm_context(item: Dict[str, Any], fallback: str) -> Dict[str, Any]:
    wallets = []
    for wallet in (item.get("wallets") or [])[:3]:
        if not isinstance(wallet, dict):
            continue
        wallets.append(
            {
                "address": wallet.get("shortAddress") or wallet.get("address"),
                "tradeCount": wallet.get("tradeCount"),
                "activeMarkets": wallet.get("activeMarkets"),
                "marketVolumeNotional": wallet.get("marketVolumeNotional"),
                "netCashPnlProxy": wallet.get("netCashPnlProxy"),
                "smartScore": wallet.get("smartScore"),
            }
        )
    related = []
    for source in (item.get("relatedContent") or [])[:2]:
        if not isinstance(source, dict):
            continue
        related.append(
            {
                "title": compact_text(source.get("title") or source.get("headline"), 120),
                "source": compact_text(source.get("source") or source.get("publisher"), 48),
                "summary": compact_text(source.get("summary") or source.get("content") or source.get("description"), 180),
                "publishedAt": source.get("publishedAt") or source.get("createdAt"),
            }
        )
    return {
        "market": compact_text(item.get("marketTitle") or item.get("title"), 160),
        "domain": item.get("domain"),
        "outcome": item.get("outcome"),
        "side": item.get("side"),
        "metrics": item.get("metrics") if isinstance(item.get("metrics"), dict) else {},
        "wallets": wallets,
        "relatedContent": related,
        "fallback": compact_text(fallback, 520),
    }


def _llm_brief(ctx: dict, item: Dict[str, Any], fallback: str) -> str:
    if not _llm_enabled():
        return fallback
    client = OpenAICompatibleClient()
    if not client.configured:
        return fallback
    allowed, budget = claim_agent_live_call("polybeats-brief")
    if not allowed:
        item["dailyBudget"] = budget
        item["narrativeError"] = "agent-budget-exhausted"
        return fallback
    item["dailyBudget"] = budget
    context = _llm_context(item, fallback)
    system_prompt = (
        "You are the polyData PolyBeats flow analyst. Return compact JSON only. "
        "Analyze clustered Polymarket trading flow for a dashboard. "
        "Use the provided wallet history, cash PnL proxy, market probability, and related content. "
        "Be factual, hedge uncertainty, and do not give financial advice."
    )
    user_prompt = (
        "Write one concise PolyBeats explanation from this JSON context.\n\n"
        "Required JSON schema:\n"
        '{"brief":"two short sentences max, English, dashboard-ready"}\n\n'
        f"Context:\n{json.dumps(context, ensure_ascii=False, default=str)}"
    )
    try:
        raw_text = client.complete_json(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=180,
            workflow_name="polydata-polybeats-brief",
        )
        raw = extract_json_object(raw_text)
        brief = compact_text(raw.get("brief"), 700)
        if brief:
            item["agentRuntime"] = client.last_usage.runtime
            item["agentModel"] = client.model
            item["agentUsage"] = {
                "inputTokens": client.last_usage.input_tokens,
                "outputTokens": client.last_usage.output_tokens,
                "totalTokens": client.last_usage.total_tokens,
                "inputChars": client.last_usage.input_chars,
                "outputChars": client.last_usage.output_chars,
            }
            return brief
    except Exception as exc:
        logger = getattr(ctx.get("app"), "logger", None)
        if logger is not None:
            logger.exception("polybeats llm brief failed")
        item["narrativeError"] = compact_text(str(exc), 180)
        return fallback
    return fallback


def _build_payload(ctx: dict, limit: int) -> Dict[str, Any]:
    recent_limit = max(180, int(limit or DEFAULT_LIMIT) * int(os.environ.get("POLYDATA_POLYBEATS_TRADE_MULTIPLIER", "32")))
    try:
        recent_trades = ctx["get_recent_trades"](limit=recent_limit)
    except Exception:
        logger = getattr(ctx.get("app"), "logger", None)
        if logger is not None:
            logger.exception("polybeats recent trade source failed")
        recent_trades = []

    addresses_by_market: Dict[int, set[str]] = {}
    market_notional_rank: Dict[int, Decimal] = {}
    for trade in recent_trades:
        market_id = trade.get("marketId") or trade.get("market_id")
        if market_id is None:
            continue
        try:
            market_id_int = int(market_id)
        except (TypeError, ValueError):
            continue
        addresses_by_market.setdefault(market_id_int, set()).update(signal_cluster_service.collect_trade_addresses(ctx, [trade]))
        price = _safe_decimal(ctx, trade.get("price"))
        size = _safe_decimal(ctx, trade.get("size"))
        notional = _safe_decimal(ctx, trade.get("notional"), price * size)
        market_notional_rank[market_id_int] = market_notional_rank.get(market_id_int, Decimal("0")) + notional

    max_profile_markets = int(os.environ.get("POLYDATA_POLYBEATS_PROFILE_MARKETS", "8"))
    max_profile_addresses = int(os.environ.get("POLYDATA_POLYBEATS_PROFILE_ADDRESSES", "20"))
    profiled_market_ids = {
        market_id
        for market_id, _ in sorted(market_notional_rank.items(), key=lambda item: item[1], reverse=True)[:max_profile_markets]
    }
    address_profiles_by_market = {}
    if recent_trades:
        try:
            address_profiles_by_market = {
                market_id: address_intel_service.get_address_profiles(ctx, list(addresses)[:max_profile_addresses], market_id=market_id)
                for market_id, addresses in addresses_by_market.items()
                if addresses and market_id in profiled_market_ids
            }
        except Exception:
            logger = getattr(ctx.get("app"), "logger", None)
            if logger is not None:
                logger.exception("polybeats address profiles failed")

    clusters = signal_cluster_service.build_polybeats_clusters(ctx, recent_trades, address_profiles_by_market, limit=max(limit * 2, limit))
    clusters = [
        cluster for cluster in clusters
        if _safe_decimal(ctx, (cluster.get("metrics") or {}).get("totalNotional") or cluster.get("notional")) >= MIN_CLUSTER_NOTIONAL
    ][:limit]

    markets = _get_markets(ctx, [cluster.get("marketId") for cluster in clusters])
    all_addresses = []
    for cluster in clusters:
        for address in cluster.get("addresses") or []:
            all_addresses.append(address.get("address"))
    pnl_by_address = _address_cash_pnl(ctx, all_addresses)

    items: list[Dict[str, Any]] = []
    llm_max = int(os.environ.get("POLYDATA_POLYBEATS_LLM_MAX_ITEMS", "3") or 3)
    for index, cluster in enumerate(clusters):
        market_id = int(cluster.get("marketId") or 0)
        market = markets.get(market_id)
        domain = _market_domain(market, str(cluster.get("marketTitle") or ""))
        wallets = []
        for address_item in cluster.get("addresses") or []:
            address = ctx["normalize_address"](address_item.get("address"))
            pnl = pnl_by_address.get(address, {})
            score = _smart_score(ctx, address_item, pnl, domain)
            wallets.append(
                {
                    **address_item,
                    "address": address,
                    "shortAddress": address_item.get("shortAddress") or _short_address(address),
                    "netCashPnlProxy": ctx["format_trade_decimal"](pnl.get("netCashPnlProxy")),
                    "tradeCashPnl": ctx["format_trade_decimal"](pnl.get("tradeCashPnl")),
                    "redeemCashflow": ctx["format_trade_decimal"](pnl.get("redeemCashflow")),
                    "pnlSource": pnl.get("pnlSource") or "none",
                    "smartScore": ctx["format_trade_decimal"](score),
                }
            )
        wallets.sort(key=lambda wallet: _safe_decimal(ctx, wallet.get("smartScore")), reverse=True)
        top = _top_wallet(wallets)
        item = {
            **cluster,
            "id": f"polybeats:{market_id}:{cluster.get('outcome')}:{cluster.get('side')}:{cluster.get('timestamp') or index}",
            "domain": domain,
            "marketSlug": (market or {}).get("slug"),
            "marketCategory": (market or {}).get("category"),
            "wallets": wallets[:6],
            "addresses": wallets[:6],
            "tags": ["PMKT", "CLOB", domain, "SMART" if top and _safe_decimal(ctx, top.get("smartScore")) >= Decimal("25") else "WATCH"],
            "title": cluster.get("title") or cluster.get("marketTitle"),
        }
        fallback = _template_explanation(item, top)
        item["explanation"] = _llm_brief(ctx, item, fallback) if index < llm_max else fallback
        item["narrativeSource"] = "llm" if _llm_enabled() and item["explanation"] != fallback else "template"
        items.append(item)

    return {
        "items": items,
        "generatedAt": ctx["utc_now_iso"](),
        "status": "ok" if items else "empty",
        "cacheMode": "live-build",
        "source": "polyData polybeats flow builder",
        "sources": {
            "trades": "trades_v2",
            "profiles": "address_trade_totals/address_market_stats",
            "pnl": "pnl_trade_cashflows/non_trade_cashflows",
            "intel": "content_links",
            "narrative": "llm" if _llm_enabled() else "template",
        },
    }


def fetch_live_polybeats_payload(ctx: dict, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    return _build_payload(ctx, max(1, int(limit or DEFAULT_LIMIT)))


def get_polybeats_snapshot(ctx: dict, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    cache_key = build_polybeats_cache_key(limit=limit)

    def builder() -> Dict[str, Any]:
        return fetch_live_polybeats_payload(ctx, limit=limit)

    payload = ctx["get_snapshot_payload"](
        SNAPSHOT_NAMESPACE,
        cache_key,
        builder,
        ttl_seconds=ctx["SIGNAL_RUNTIME_TTL_SECONDS"],
    )
    if isinstance(payload, dict):
        payload.setdefault("generatedAt", ctx["utc_now_iso"]())
        payload.setdefault("status", "ok" if payload.get("items") else "empty")
        payload.setdefault("source", "polyData polybeats flow builder")
    return payload
