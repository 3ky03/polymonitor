from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, Iterable, Optional


NEW_ADDRESS_MAX_TRADES = 3
NEW_ADDRESS_DAYS = 14
NEW_TO_MARKET_DAYS = 7


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


def _decimal_text(ctx: dict, value: Any) -> Any:
    return ctx["format_trade_decimal"](value)


def _is_recent(ctx: dict, value: Any, days: int) -> bool:
    parsed = ctx["parse_iso_datetime"](value)
    now = ctx["parse_iso_datetime"](ctx["utc_now_iso"]())
    if parsed is None or now is None:
        return False
    return parsed >= now - timedelta(days=days)


def _placeholders(values: list[str]) -> str:
    return ", ".join("?" for _ in values)


def _empty_profile(address: str) -> Dict[str, Any]:
    return {
        "address": address,
        "tradeCount": 0,
        "buyCount": 0,
        "sellCount": 0,
        "volumeNotional": None,
        "firstTradeAt": None,
        "lastTradeAt": None,
        "activeMarkets": 0,
        "marketTradeCount": 0,
        "marketVolumeNotional": None,
        "firstMarketTradeAt": None,
        "lastMarketTradeAt": None,
        "isNewAddress": True,
        "isNewToMarket": True,
        "labels": ["new-address"],
    }


def get_address_profiles(ctx: dict, addresses: Iterable[Any], *, market_id: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
    normalized = _normalize_addresses(ctx, addresses)
    profiles = {address: _empty_profile(address) for address in normalized}
    if not normalized:
        return profiles

    if ctx["table_exists"]("address_trade_totals"):
        rows = ctx["query_all"](
            f"""
            SELECT
                address,
                total_trade_count,
                total_buy_count,
                total_sell_count,
                total_volume_notional,
                first_trade_at,
                last_trade_at
            FROM address_trade_totals
            WHERE address IN ({_placeholders(normalized)})
            """,
            tuple(normalized),
        )
        for row in rows:
            address = ctx["normalize_address"](row.get("address"))
            if address not in profiles:
                continue
            trade_count = int(row.get("total_trade_count") or 0)
            is_new = trade_count <= NEW_ADDRESS_MAX_TRADES or _is_recent(ctx, row.get("first_trade_at"), NEW_ADDRESS_DAYS)
            profiles[address].update(
                {
                    "tradeCount": trade_count,
                    "buyCount": int(row.get("total_buy_count") or 0),
                    "sellCount": int(row.get("total_sell_count") or 0),
                    "volumeNotional": _decimal_text(ctx, row.get("total_volume_notional")),
                    "firstTradeAt": row.get("first_trade_at"),
                    "lastTradeAt": row.get("last_trade_at"),
                    "isNewAddress": is_new,
                }
            )

    if ctx["table_exists"]("address_market_stats"):
        rows = ctx["query_all"](
            f"""
            SELECT address, COUNT(*) AS active_markets
            FROM address_market_stats
            WHERE address IN ({_placeholders(normalized)})
            GROUP BY address
            """,
            tuple(normalized),
        )
        for row in rows:
            address = ctx["normalize_address"](row.get("address"))
            if address in profiles:
                profiles[address]["activeMarkets"] = int(row.get("active_markets") or 0)

        if market_id is not None:
            rows = ctx["query_all"](
                f"""
                SELECT
                    address,
                    trade_count,
                    buy_count,
                    sell_count,
                    volume_notional,
                    first_trade_at,
                    last_trade_at
                FROM address_market_stats
                WHERE market_id = ? AND address IN ({_placeholders(normalized)})
                """,
                (market_id, *normalized),
            )
            for row in rows:
                address = ctx["normalize_address"](row.get("address"))
                if address not in profiles:
                    continue
                market_trade_count = int(row.get("trade_count") or 0)
                is_new_to_market = market_trade_count <= 2 or _is_recent(ctx, row.get("first_trade_at"), NEW_TO_MARKET_DAYS)
                profiles[address].update(
                    {
                        "marketTradeCount": market_trade_count,
                        "marketBuyCount": int(row.get("buy_count") or 0),
                        "marketSellCount": int(row.get("sell_count") or 0),
                        "marketVolumeNotional": _decimal_text(ctx, row.get("volume_notional")),
                        "firstMarketTradeAt": row.get("first_trade_at"),
                        "lastMarketTradeAt": row.get("last_trade_at"),
                        "isNewToMarket": is_new_to_market,
                    }
                )

    for profile in profiles.values():
        labels: list[str] = []
        trade_count = int(profile.get("tradeCount") or 0)
        volume = ctx["_safe_decimal"](profile.get("volumeNotional")) or Decimal("0")
        active_markets = int(profile.get("activeMarkets") or 0)
        if profile.get("isNewAddress"):
            labels.append("new-address")
        elif trade_count >= 100 or volume >= Decimal("50000"):
            labels.append("seasoned")
        if profile.get("isNewToMarket"):
            labels.append("new-to-market")
        if active_markets >= 10:
            labels.append("multi-market")
        profile["labels"] = labels or ["tracked"]

    return profiles
