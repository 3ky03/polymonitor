from __future__ import annotations

import json
from typing import Any, Dict, Optional


def get_top_addresses_payload(ctx: dict, days: Optional[int], limit: int) -> Dict[str, Any]:
    if days is None or days <= 0:
        rows = ctx["query_all"](
            """
            SELECT
                address,
                total_trade_count,
                total_buy_count,
                total_sell_count,
                total_volume_notional,
                first_trade_at,
                last_trade_at
            FROM address_trade_totals
            ORDER BY total_trade_count DESC, total_volume_notional DESC
            LIMIT ?
            """,
            (limit,),
        )
        return {
            "items": [
                {
                    "address": row.get("address"),
                    "tradeCount": int(row.get("total_trade_count") or 0),
                    "buyCount": int(row.get("total_buy_count") or 0),
                    "sellCount": int(row.get("total_sell_count") or 0),
                    "volumeNotional": row.get("total_volume_notional"),
                    "firstTradeAt": row.get("first_trade_at"),
                    "lastTradeAt": row.get("last_trade_at"),
                }
                for row in rows
            ],
            "windowDays": None,
        }

    threshold = ctx["utc_date_days_ago"](days)
    rows = ctx["query_all"](
        """
        SELECT
            address,
            SUM(trade_count) AS total_trade_count,
            SUM(buy_count) AS total_buy_count,
            SUM(sell_count) AS total_sell_count,
            SUM(volume_notional) AS total_volume_notional,
            MAX(last_trade_at) AS last_trade_at
        FROM address_trade_daily_stats
        WHERE trade_date >= ?
        GROUP BY address
        ORDER BY total_trade_count DESC, total_volume_notional DESC
        LIMIT ?
        """,
        (threshold, limit),
    )
    return {
        "items": [
            {
                "address": row.get("address"),
                "tradeCount": int(row.get("total_trade_count") or 0),
                "buyCount": int(row.get("total_buy_count") or 0),
                "sellCount": int(row.get("total_sell_count") or 0),
                "volumeNotional": row.get("total_volume_notional"),
                "lastTradeAt": row.get("last_trade_at"),
            }
            for row in rows
        ],
        "windowDays": days,
    }


def get_active_addresses_payload(ctx: dict, days: int) -> Dict[str, Any]:
    threshold = ctx["utc_date_days_ago"](days)
    rows = ctx["query_all"](
        """
        SELECT
            trade_date AS day,
            COUNT(*) AS active_address_count,
            SUM(trade_count) AS trade_count
        FROM address_trade_daily_stats
        WHERE trade_date >= ?
        GROUP BY trade_date
        ORDER BY trade_date ASC
        """,
        (threshold,),
    )
    return {
        "items": [
            {
                "day": str(row.get("day")) if row.get("day") is not None else None,
                "activeAddressCount": int(row.get("active_address_count") or 0),
                "tradeCount": int(row.get("trade_count") or 0),
            }
            for row in rows
        ],
        "windowDays": days,
    }


def get_address_summary_payload(ctx: dict, address: str, days: int) -> Dict[str, Any]:
    normalized = ctx["normalize_address"](address)
    total_row = ctx["query_one"](
        """
        SELECT
            address,
            total_trade_count,
            total_buy_count,
            total_sell_count,
            total_volume_notional,
            first_trade_at,
            last_trade_at
        FROM address_trade_totals
        WHERE address = ?
        LIMIT 1
        """,
        (normalized,),
    )
    if not total_row:
        return {"address": normalized, "summary": None, "daily": [], "topMarkets": []}

    threshold = ctx["utc_date_days_ago"](days)
    daily_rows = ctx["query_all"](
        """
        SELECT
            trade_date AS day,
            trade_count,
            buy_count,
            sell_count,
            volume_notional,
            last_trade_at
        FROM address_trade_daily_stats
        WHERE address = ? AND trade_date >= ?
        ORDER BY trade_date ASC
        """,
        (normalized, threshold),
    )
    market_rows = ctx["query_all"](
        """
        SELECT
            ams.market_id,
            ams.trade_count,
            ams.volume_notional,
            ams.last_trade_at,
            m.slug,
            m.title
        FROM address_market_stats ams
        LEFT JOIN markets m ON m.id = ams.market_id
        WHERE ams.address = ?
        ORDER BY ams.trade_count DESC, ams.volume_notional DESC
        LIMIT 10
        """,
        (normalized,),
    )
    active_markets_row = ctx["query_one"](
        """
        SELECT COUNT(*) AS active_markets
        FROM address_market_stats
        WHERE address = ?
        """,
        (normalized,),
    )

    return {
        "address": normalized,
        "summary": {
            "tradeCount": int(total_row.get("total_trade_count") or 0),
            "buyCount": int(total_row.get("total_buy_count") or 0),
            "sellCount": int(total_row.get("total_sell_count") or 0),
            "volumeNotional": total_row.get("total_volume_notional"),
            "firstTradeAt": total_row.get("first_trade_at"),
            "lastTradeAt": total_row.get("last_trade_at"),
            "activeMarkets": int(active_markets_row.get("active_markets") or 0),
        },
        "daily": [
            {
                "day": str(row.get("day")) if row.get("day") is not None else None,
                "tradeCount": int(row.get("trade_count") or 0),
                "buyCount": int(row.get("buy_count") or 0),
                "sellCount": int(row.get("sell_count") or 0),
                "volumeNotional": row.get("volume_notional"),
                "lastTradeAt": row.get("last_trade_at"),
            }
            for row in daily_rows
        ],
        "topMarkets": [
            {
                "marketId": row.get("market_id"),
                "localMarketId": row.get("market_id"),
                "slug": row.get("slug"),
                "title": row.get("title"),
                "tradeCount": int(row.get("trade_count") or 0),
                "volumeNotional": row.get("volume_notional"),
                "lastTradeAt": row.get("last_trade_at"),
            }
            for row in market_rows
        ],
    }


def get_address_trades_payload(
    ctx: dict,
    address: str,
    *,
    limit: int = 100,
    market_id: Optional[int] = None,
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    before_ts: Optional[str] = None,
    before_block_number: Optional[int] = None,
    before_log_index: Optional[int] = None,
) -> Dict[str, Any]:
    normalized = ctx["normalize_address"](address)
    if not normalized:
        return {"address": normalized, "items": [], "nextCursor": None}

    trade_index_names = ctx["get_trades_index_names"]()
    query_source = ctx["ADDRESS_HISTORY_SOURCE"]
    if query_source == ctx["TRADE_V2_CORE_TABLE"]:
        maker_time_index = "idx_trades_v2_maker_time_log"
        taker_time_index = "idx_trades_v2_taker_time_log"
        maker_market_index = ""
        taker_market_index = ""
        maker_projection = f"""
            t.id AS id,
            LOWER(HEX(t.tx_hash)) AS tx_hash,
            t.log_index AS log_index,
            t.market_id AS market_id,
            CONCAT('0x', LOWER(HEX(t.maker))) AS maker,
            CONCAT('0x', LOWER(HEX(t.taker))) AS taker,
            CAST(t.price AS CHAR) AS price,
            CAST(t.size AS CHAR) AS size,
            CASE t.side_code
                WHEN 1 THEN 'BUY'
                WHEN 2 THEN 'SELL'
                ELSE 'UNKNOWN'
            END AS side,
            CASE t.outcome_code
                WHEN 1 THEN 'YES'
                WHEN 2 THEN 'NO'
                ELSE 'UNKNOWN'
            END AS outcome,
            LOWER(HEX(t.token_id)) AS token_id,
            t.block_number AS block_number,
            DATE_FORMAT(t.block_time, '%%Y-%%m-%%dT%%H:%%i:%%sZ') AS timestamp,
            LOWER(HEX(t.order_hash)) AS order_hash,
            {ctx["compat_maker_asset_id_sql"]('t')} AS maker_asset_id,
            {ctx["compat_taker_asset_id_sql"]('t')} AS taker_asset_id,
            t.maker_amount AS maker_amount,
            t.taker_amount AS taker_amount,
            t.fee AS fee,
            t.contract AS contract
        """
        taker_projection = maker_projection
        maker_filters = ["maker = UNHEX(REPLACE(LOWER(?), '0x', ''))"]
        taker_filters = ["taker = UNHEX(REPLACE(LOWER(?), '0x', ''))"]
    else:
        maker_market_index = "idx_trades_maker_market_time_block_log"
        taker_market_index = "idx_trades_taker_market_time_block_log"
        maker_time_index = "idx_trades_maker_time_block_log"
        taker_time_index = "idx_trades_taker_time_block_log"
        maker_projection = """
            tx_hash, log_index, market_id, maker, taker, price, size, side, outcome,
            token_id, timestamp, block_number, order_hash, maker_asset_id, taker_asset_id,
            maker_amount, taker_amount, fee, contract
        """
        taker_projection = maker_projection
        maker_filters = ["maker = ?"]
        taker_filters = ["taker = ?"]

    maker_hint = ""
    taker_hint = ""
    if (
        market_id is not None
        and maker_market_index
        and taker_market_index
        and maker_market_index in trade_index_names
        and taker_market_index in trade_index_names
    ):
        maker_hint = f" FORCE INDEX ({maker_market_index})"
        taker_hint = f" FORCE INDEX ({taker_market_index})"
    elif maker_time_index in trade_index_names and taker_time_index in trade_index_names:
        maker_hint = f" FORCE INDEX ({maker_time_index})"
        taker_hint = f" FORCE INDEX ({taker_time_index})"
    else:
        return {
            "address": normalized,
            "items": [],
            "nextCursor": None,
            "error": "Required maker/taker address indexes are missing on trades",
        }

    arm_limit = max(100, limit * 2)
    maker_params = [normalized]
    taker_params = [normalized]

    if market_id is not None:
        maker_filters.append("market_id = ?")
        taker_filters.append("market_id = ?")
        maker_params.append(market_id)
        taker_params.append(market_id)
    if start_ts:
        maker_filters.append("timestamp >= ?")
        taker_filters.append("timestamp >= ?")
        maker_params.append(start_ts)
        taker_params.append(start_ts)
    if end_ts:
        maker_filters.append("timestamp < ?")
        taker_filters.append("timestamp < ?")
        maker_params.append(end_ts)
        taker_params.append(end_ts)
    if before_ts and before_block_number is not None and before_log_index is not None:
        cursor_clause = "(timestamp < ? OR (timestamp = ? AND (block_number < ? OR (block_number = ? AND log_index < ?))))"
        maker_filters.append(cursor_clause)
        taker_filters.append(cursor_clause)
        cursor_params = [before_ts, before_ts, before_block_number, before_block_number, before_log_index]
        maker_params.extend(cursor_params)
        taker_params.extend(cursor_params)

    maker_sql = f"""
        SELECT
            'maker' AS address_role,
            {maker_projection}
        FROM {query_source} t
        {maker_hint}
        WHERE {' AND '.join(maker_filters)}
        ORDER BY timestamp DESC, block_number DESC, log_index DESC
        LIMIT {arm_limit}
    """
    taker_sql = f"""
        SELECT
            'taker' AS address_role,
            {taker_projection}
        FROM {query_source} t
        {taker_hint}
        WHERE {' AND '.join(taker_filters)}
        ORDER BY timestamp DESC, block_number DESC, log_index DESC
        LIMIT {arm_limit}
    """
    sql = f"""
        SELECT *
        FROM (
            ({maker_sql})
            UNION ALL
            ({taker_sql})
        ) address_trades
        ORDER BY timestamp DESC, block_number DESC, log_index DESC
        LIMIT {limit + 1}
    """
    rows = ctx["query_all"](sql, [*maker_params, *taker_params])
    has_more = len(rows) > limit
    visible_rows = rows[:limit]
    next_cursor = None
    if has_more and visible_rows:
        last_row = visible_rows[-1]
        next_cursor = {
            "beforeTs": last_row.get("timestamp"),
            "beforeBlockNumber": last_row.get("block_number"),
            "beforeLogIndex": last_row.get("log_index"),
        }

    return {
        "address": normalized,
        "items": [{**ctx["normalize_trade"](row), "addressRole": row.get("address_role")} for row in visible_rows],
        "nextCursor": next_cursor,
    }


def get_top_addresses_cached(ctx: dict, days: Optional[int], limit: int) -> Dict[str, Any]:
    cache_key = json.dumps({"limit": limit, "days": days}, sort_keys=True, ensure_ascii=True)
    return ctx["get_markets_payload_cached"](
        cache_key,
        lambda: get_top_addresses_payload(ctx, days, limit),
        namespace="analytics:top-addresses",
        ttl_seconds=ctx["ADDRESS_CACHE_TTL_SECONDS"],
    )


def get_active_addresses_cached(ctx: dict, days: int) -> Dict[str, Any]:
    cache_key = json.dumps({"days": days}, sort_keys=True, ensure_ascii=True)
    return ctx["get_markets_payload_cached"](
        cache_key,
        lambda: get_active_addresses_payload(ctx, days),
        namespace="analytics:active-addresses",
        ttl_seconds=ctx["ADDRESS_CACHE_TTL_SECONDS"],
    )


def get_address_summary_cached(ctx: dict, address: str, days: int) -> Dict[str, Any]:
    normalized = ctx["normalize_address"](address)
    cache_key = json.dumps({"address": normalized, "days": days}, sort_keys=True, ensure_ascii=True)
    return ctx["get_markets_payload_cached"](
        cache_key,
        lambda: get_address_summary_payload(ctx, normalized, days),
        namespace="analytics:address-summary",
        ttl_seconds=ctx["ADDRESS_CACHE_TTL_SECONDS"],
    )
