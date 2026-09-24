from __future__ import annotations

from decimal import Decimal
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple


MIN_CLUSTER_NOTIONAL = Decimal("250")
MIN_CLUSTER_ADDRESSES = 2


def _safe_decimal(ctx: dict, value: Any, default: Decimal = Decimal("0")) -> Decimal:
    parsed = ctx["_safe_decimal"](value)
    return parsed if parsed is not None else default


def _participant_addresses(ctx: dict, trade: Dict[str, Any]) -> list[str]:
    addresses = [ctx["normalize_address"](trade.get("maker")), ctx["normalize_address"](trade.get("taker"))]
    return [address for address in addresses if address and len(address) == 42]


def collect_trade_addresses(ctx: dict, trades: Iterable[Dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    addresses: list[str] = []
    for trade in trades:
        for address in _participant_addresses(ctx, trade):
            if address in seen:
                continue
            seen.add(address)
            addresses.append(address)
    return addresses


def _format_money(value: Decimal) -> str:
    if value >= Decimal("1000000"):
        return f"${(value / Decimal('1000000')).quantize(Decimal('0.1'))}M"
    if value >= Decimal("1000"):
        return f"${(value / Decimal('1000')).quantize(Decimal('0.1'))}k"
    return f"${value.quantize(Decimal('1'))}"


def _format_count(noun: str, count: int) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _get_market_price_rows(ctx: dict, market_ids: Iterable[Any]) -> Dict[int, Dict[str, Any]]:
    ids = [int(market_id) for market_id in market_ids if market_id is not None]
    if not ids or not ctx["table_exists"]("market_latest_prices"):
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = ctx["query_all"](
        f"""
        SELECT
            market_id,
            latest_price,
            latest_yes_price,
            latest_no_price,
            latest_trade_at
        FROM market_latest_prices
        WHERE market_id IN ({placeholders})
        """,
        tuple(ids),
    )
    return {int(row.get("market_id")): row for row in rows if row.get("market_id") is not None}


def _current_probability(ctx: dict, price_row: Optional[Dict[str, Any]], outcome: Any) -> Any:
    if not price_row:
        return None
    outcome_text = str(outcome or "").upper()
    yes_price = ctx["_safe_decimal"](price_row.get("latest_yes_price") or price_row.get("latest_price"))
    no_price = ctx["_safe_decimal"](price_row.get("latest_no_price"))
    if outcome_text == "YES":
        if yes_price is not None:
            return ctx["format_trade_decimal"](yes_price)
        if no_price is not None:
            return ctx["format_trade_decimal"](Decimal("1") - no_price)
    if outcome_text == "NO":
        if no_price is not None:
            return ctx["format_trade_decimal"](no_price)
        if yes_price is not None:
            return ctx["format_trade_decimal"](Decimal("1") - yes_price)
    return ctx["format_trade_decimal"](yes_price)


def _score_cluster(
    ctx: dict,
    *,
    total_notional: Decimal,
    address_count: int,
    new_address_count: int,
    new_to_market_count: int,
    has_news: bool,
) -> Decimal:
    notional_score = min(Decimal("45"), total_notional / Decimal("120"))
    address_score = min(Decimal("20"), Decimal(address_count) * Decimal("4"))
    new_score = min(Decimal("25"), Decimal(new_address_count) * Decimal("8") + Decimal(new_to_market_count) * Decimal("4"))
    news_score = Decimal("6") if has_news else Decimal("0")
    return notional_score + address_score + new_score + news_score


def _severity(score: Decimal) -> str:
    if score >= Decimal("75"):
        return "critical"
    if score >= Decimal("45"):
        return "elevated"
    return "watch"


def _build_title(market_title: str, outcome: Any, total_notional: Decimal, new_address_count: int, address_count: int) -> str:
    outcome_text = str(outcome or "UNKNOWN").upper()
    if new_address_count >= 2:
        subject = _format_count("new account", new_address_count)
    elif address_count >= 3:
        subject = _format_count("account", address_count)
    else:
        subject = "Cluster"
    return f"{subject} put {_format_money(total_notional)} on {outcome_text}: {market_title}"


def _bias_for(entry: Dict[str, Any]) -> str:
    outcome = str(entry.get("outcome") or "").upper()
    if outcome == "NO":
        return "bearish"
    if outcome == "YES":
        return "bullish"
    side = str(entry.get("side") or "").upper()
    if side == "SELL":
        return "bearish"
    return "bullish"


def _action_for(entry: Dict[str, Any]) -> Dict[str, str]:
    side = str(entry.get("side") or "BUY").upper()
    outcome = str(entry.get("outcome") or "YES").upper()
    return {
        "label": "Sell" if side == "SELL" else "Buy",
        "outcome": "No" if outcome == "NO" else "Yes" if outcome == "YES" else outcome.title(),
    }


def _related_news(ctx: dict, market_id: Optional[int]) -> list[Dict[str, Any]]:
    if market_id is None:
        return []
    enabled = str(os.environ.get("POLYDATA_SIGNAL_RELATED_NEWS_ENABLED", "0")).strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return []
    try:
        payload = ctx["get_related_content_by_market_id"](market_id, limit=2)
    except Exception:
        ctx["app"].logger.exception("signal related news failed market_id=%s", market_id)
        return []
    items = payload.get("items") or []
    return [
        {
            "source": item.get("source") or item.get("contentType") or "intel",
            "title": item.get("title"),
            "url": item.get("url"),
            "publishedAt": item.get("publishedAt") or item.get("published_at"),
            "summary": item.get("summary"),
        }
        for item in items[:2]
    ]


def build_polybeats_clusters(
    ctx: dict,
    trades: list[Dict[str, Any]],
    address_profiles_by_market: Dict[int, Dict[str, Dict[str, Any]]],
    *,
    limit: int = 8,
) -> list[Dict[str, Any]]:
    grouped: Dict[Tuple[Any, str, str], Dict[str, Any]] = {}
    for trade in trades:
        market_id = trade.get("marketId") or trade.get("market_id")
        if market_id is None:
            continue
        outcome = str(trade.get("outcome") or "UNKNOWN").upper()
        side = str(trade.get("side") or "UNKNOWN").upper()
        key = (market_id, outcome, side)
        price = _safe_decimal(ctx, trade.get("price"))
        size = _safe_decimal(ctx, trade.get("size"))
        notional = _safe_decimal(ctx, trade.get("notional"), price * size if price and size else Decimal("0"))
        entry = grouped.setdefault(
            key,
            {
                "marketId": market_id,
                "localMarketId": market_id,
                "marketTitle": trade.get("marketTitle") or trade.get("market_title") or "Market signal",
                "outcome": outcome,
                "side": side,
                "totalNotional": Decimal("0"),
                "weightedPrice": Decimal("0"),
                "size": Decimal("0"),
                "tradeCount": 0,
                "addresses": set(),
                "latestTimestamp": None,
                "txHash": trade.get("txHash") or trade.get("tx_hash"),
            },
        )
        entry["totalNotional"] += notional
        entry["weightedPrice"] += price * size
        entry["size"] += size
        entry["tradeCount"] += 1
        timestamp = trade.get("timestamp")
        if timestamp and (entry["latestTimestamp"] is None or str(timestamp) > str(entry["latestTimestamp"])):
            entry["latestTimestamp"] = timestamp
            entry["txHash"] = trade.get("txHash") or trade.get("tx_hash")
        for address in _participant_addresses(ctx, trade):
            entry["addresses"].add(address)

    price_rows = _get_market_price_rows(ctx, [key[0] for key in grouped])
    clusters: list[Dict[str, Any]] = []
    for entry in grouped.values():
        market_id = int(entry["marketId"])
        addresses = sorted(entry["addresses"])
        total_notional = entry["totalNotional"]
        if total_notional < MIN_CLUSTER_NOTIONAL and len(addresses) < MIN_CLUSTER_ADDRESSES:
            continue
        profiles = address_profiles_by_market.get(market_id, {})
        address_items = []
        new_address_count = 0
        new_to_market_count = 0
        for address in addresses:
            profile = profiles.get(address, {"address": address, "labels": ["tracked"]})
            if profile.get("isNewAddress"):
                new_address_count += 1
            if profile.get("isNewToMarket"):
                new_to_market_count += 1
            address_items.append(
                {
                    "address": address,
                    "shortAddress": f"{address[:6]}...{address[-4:]}",
                    "labels": profile.get("labels") or [],
                    "tradeCount": profile.get("tradeCount"),
                    "volumeNotional": profile.get("volumeNotional"),
                    "activeMarkets": profile.get("activeMarkets"),
                    "marketTradeCount": profile.get("marketTradeCount"),
                    "marketVolumeNotional": profile.get("marketVolumeNotional"),
                    "firstTradeAt": profile.get("firstTradeAt"),
                    "firstMarketTradeAt": profile.get("firstMarketTradeAt"),
                    "isNewAddress": profile.get("isNewAddress"),
                    "isNewToMarket": profile.get("isNewToMarket"),
                }
            )

        related = _related_news(ctx, market_id)
        score = _score_cluster(
            ctx,
            total_notional=total_notional,
            address_count=len(addresses),
            new_address_count=new_address_count,
            new_to_market_count=new_to_market_count,
            has_news=bool(related),
        )
        avg_price = entry["weightedPrice"] / entry["size"] if entry["size"] else Decimal("0")
        current_probability = _current_probability(ctx, price_rows.get(market_id), entry.get("outcome"))
        summary_parts = [
            f"{_format_count('address', len(addresses))} across {entry['tradeCount']} recent trades",
            f"average entry {ctx['format_trade_decimal'](avg_price)}",
        ]
        if new_address_count:
            summary_parts.append(f"{new_address_count} new address signal")
        if related:
            summary_parts.append(f"linked news: {related[0].get('source')}")
        action = _action_for(entry)
        bias = _bias_for(entry)
        source_label = "NEWS+$" if related else "CHAIN+$"
        source_tag = str((related[0].get("source") if related else "FLOW") or "FLOW").upper()[:3]
        clusters.append(
            {
                "kind": "polybeats",
                "severity": _severity(score),
                "bias": bias,
                "sourceLabel": source_label,
                "sourceTag": source_tag,
                "headline": related[0].get("title") if related else f"{len(addresses)} wallet flow clustered on-chain",
                "action": action,
                "title": _build_title(str(entry["marketTitle"]), entry.get("outcome"), total_notional, new_address_count, len(addresses)),
                "summary": "; ".join(summary_parts),
                "timestamp": entry.get("latestTimestamp"),
                "marketId": market_id,
                "localMarketId": market_id,
                "marketTitle": entry.get("marketTitle"),
                "txHash": entry.get("txHash"),
                "side": entry.get("side"),
                "outcome": entry.get("outcome"),
                "price": ctx["format_trade_decimal"](avg_price),
                "notional": ctx["format_trade_decimal"](total_notional),
                "contributors": ["cluster", "address", "news" if related else "flow"],
                "addresses": address_items[:6],
                "relatedContent": related,
                "metrics": {
                    "totalNotional": ctx["format_trade_decimal"](total_notional),
                    "avgPrice": ctx["format_trade_decimal"](avg_price),
                    "currentProbability": current_probability,
                    "accountCount": len(addresses),
                    "newAccountCount": new_address_count,
                    "newToMarketCount": new_to_market_count,
                    "tradeCount": entry["tradeCount"],
                    "score": ctx["format_trade_decimal"](score.quantize(Decimal("0.1"))),
                },
            }
        )

    clusters.sort(
        key=lambda item: (
            _safe_decimal(ctx, item.get("metrics", {}).get("score")),
            _safe_decimal(ctx, item.get("metrics", {}).get("totalNotional")),
        ),
        reverse=True,
    )
    return clusters[:limit]
