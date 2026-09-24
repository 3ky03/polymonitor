"""ClickHouse SQL for clean OrderFilled block close prices.

The production OrderFilled price tape keeps two ideas separate:

* close_price: the traded price of that token in that block.
* yes_probability_close: the same close converted to the market YES
  probability by the Postgres writer, using token_side metadata.

This query only builds the clean token-level tape. It filters known internal
exchange/settlement counterparties, derives trade price from maker/taker
amounts when those fields are available, and keeps enough diagnostics to audit
why a block close looks strange later.
"""

from __future__ import annotations

from typing import Iterable


INTERNAL_COUNTERPARTIES = (
    # NegRisk CTF Exchange / adapter-style internal settlement legs observed in
    # OrderFilled logs. Stored without 0x because orderfilled_fact normalizes
    # address-like fields that way.
    "c5d563a36ae78145c45a50134d48a1215220f80a",
    "e111180000d2663c0091e4f400237545b87b996b",
)


def quote_clickhouse_string(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def orderfilled_block_close_sql(
    *,
    table: str = "orderfilled_fact",
    from_block: int,
    to_block: int,
    market_ids: Iterable[int] | None = None,
    token_ids: Iterable[str] | None = None,
) -> str:
    filters = [f"f.block_number BETWEEN {int(from_block)} AND {int(to_block)}"]
    market_list = sorted({int(market_id) for market_id in market_ids or [] if int(market_id or 0) > 0})
    if market_list:
        filters.append(f"f.market_id IN ({','.join(str(market_id) for market_id in market_list)})")
    token_list = [str(token).lower() for token in token_ids or [] if str(token or "").strip()]
    if token_list:
        quoted = ",".join(quote_clickhouse_string(token) for token in token_list)
        filters.append(f"lower(f.token_id) IN ({quoted})")
    where_sql = " AND ".join(filters)
    internal_addresses = ",".join(quote_clickhouse_string(address) for address in INTERNAL_COUNTERPARTIES)
    return f"""
        WITH
            (SELECT ifNull(max(block_number), 0) FROM {table}) AS max_fact_block,
            (SELECT ifNull(max(block_number), 0) FROM block_timestamps) AS max_ts_block,
            (SELECT ifNull(max(block_time), toDateTime(0, 'UTC')) FROM block_timestamps) AS max_ts_time,
            if(max_ts_block > 0 AND max_ts_time > toDateTime('2000-01-01 00:00:00', 'UTC') AND max_fact_block - max_ts_block <= 7200, max_ts_block, max_fact_block) AS anchor_block,
            if(max_ts_block > 0 AND max_ts_time > toDateTime('2000-01-01 00:00:00', 'UTC') AND max_fact_block - max_ts_block <= 7200, max_ts_time, now('UTC')) AS anchor_time,
            raw AS (
                SELECT
                    f.market_id,
                    f.condition_id,
                    lower(f.token_id) AS token_id,
                    f.outcome_code,
                    f.block_number,
                    if(
                        isNull(bt.block_time),
                        addSeconds(anchor_time, (toInt64(f.block_number) - toInt64(anchor_block)) * 2),
                        bt.block_time
                    ) AS block_timestamp,
                    lower(f.tx_hash) AS tx_hash,
                    f.log_index,
                    lower(replaceRegexpOne(f.maker, '^0x', '')) AS maker,
                    lower(replaceRegexpOne(f.taker, '^0x', '')) AS taker,
                    f.price AS raw_price,
                    f.size,
                    f.maker_amount,
                    f.taker_amount,
                    (
                        maker_amount IS NOT NULL
                        AND taker_amount IS NOT NULL
                        AND maker_amount > 0
                        AND taker_amount > 0
                        AND maker_amount != taker_amount
                    ) AS can_derive_amount_price,
                    if(
                        can_derive_amount_price,
                        toDecimal128(least(assumeNotNull(maker_amount), assumeNotNull(taker_amount)), 10)
                            / toDecimal128(greatest(assumeNotNull(maker_amount), assumeNotNull(taker_amount)), 10),
                        raw_price
                    ) AS trade_price,
                    (maker IN ({internal_addresses}) OR taker IN ({internal_addresses})) AS is_internal_counterparty,
                    (size <= 0) AS is_invalid_size,
                    (trade_price < 0 OR trade_price > 1) AS is_invalid_price,
                    (trade_price <= 0.01 OR trade_price >= 0.99) AS is_extreme_price
                FROM {table} f
                LEFT JOIN block_timestamps bt ON bt.block_number = f.block_number
                WHERE {where_sql}
            ),
            raw_stats AS (
                SELECT
                    token_id,
                    block_number,
                    count() AS raw_trade_count,
                    countIf(is_internal_counterparty) AS internal_filtered_count,
                    countIf(is_invalid_size) AS invalid_size_count,
                    countIf(is_invalid_price) AS invalid_price_count,
                    countIf(can_derive_amount_price) AS amount_ratio_count,
                    countIf(NOT can_derive_amount_price) AS raw_price_fallback_count,
                    countIf(is_extreme_price) AS extreme_trade_count
                FROM raw
                GROUP BY token_id, block_number
            ),
            clean AS (
                SELECT *
                FROM raw
                WHERE
                    NOT is_internal_counterparty
                    AND NOT is_invalid_size
                    AND NOT is_invalid_price
            )
        SELECT
            any(clean.market_id) AS market_id,
            any(clean.condition_id) AS condition_id,
            clean.token_id AS token_id,
            any(clean.outcome_code) AS outcome_code,
            clean.block_number AS block_number,
            formatDateTime(any(clean.block_timestamp), '%Y-%m-%dT%H:%i:%SZ', 'UTC') AS block_timestamp,
            toString(argMax(clean.trade_price, tuple(clean.log_index, clean.tx_hash))) AS close_price,
            toString(argMax(clean.raw_price, tuple(clean.log_index, clean.tx_hash))) AS close_raw_price,
            lower(argMax(tx_hash, tuple(log_index, tx_hash))) AS close_tx_hash,
            toUInt32(argMax(clean.log_index, tuple(clean.log_index, clean.tx_hash))) AS close_log_index,
            toString(argMax(clean.maker_amount, tuple(clean.log_index, clean.tx_hash))) AS close_maker_amount,
            toString(argMax(clean.taker_amount, tuple(clean.log_index, clean.tx_hash))) AS close_taker_amount,
            if(
                argMax(clean.can_derive_amount_price, tuple(clean.log_index, clean.tx_hash)),
                'maker_taker_amount_ratio',
                'raw_orderfilled_price'
            ) AS close_price_source,
            count() AS clean_trade_count,
            raw_stats.raw_trade_count AS raw_trade_count,
            raw_stats.internal_filtered_count AS internal_filtered_count,
            raw_stats.invalid_size_count AS invalid_size_count,
            raw_stats.invalid_price_count AS invalid_price_count,
            raw_stats.amount_ratio_count AS amount_ratio_count,
            raw_stats.raw_price_fallback_count AS raw_price_fallback_count,
            raw_stats.extreme_trade_count AS extreme_trade_count,
            toString(sum(clean.size)) AS volume,
            toString(sum(clean.trade_price * clean.size) / nullIf(sum(clean.size), 0)) AS vwap_price
        FROM clean
        INNER JOIN raw_stats
            ON raw_stats.token_id = clean.token_id
            AND raw_stats.block_number = clean.block_number
        GROUP BY
            clean.token_id,
            clean.block_number,
            raw_stats.raw_trade_count,
            raw_stats.internal_filtered_count,
            raw_stats.invalid_size_count,
            raw_stats.invalid_price_count,
            raw_stats.amount_ratio_count,
            raw_stats.raw_price_fallback_count,
            raw_stats.extreme_trade_count
        ORDER BY clean.token_id ASC, clean.block_number ASC
    """
