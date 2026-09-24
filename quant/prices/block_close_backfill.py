"""Backfill block-number based OrderFilled close prices."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any

from .block_close_algorithm import orderfilled_block_close_sql
from ..core.db import ClickHouseClient, PostgresSettings, postgres_connection
from ..core.metadata import refresh_market_token_metadata
from ..core.schema import create_schema


SOURCE = "orderfilled_block_close"


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"NULL", "\\N", "NONE", "NAN"}:
        return None
    return Decimal(text)


def _timestamp_or_none(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _yes_probability(token_side: str | None, token_price: Decimal | None) -> Decimal | None:
    if token_price is None:
        return None
    side = str(token_side or "").upper()
    if side == "YES":
        return token_price
    if side == "NO":
        return Decimal("1") - token_price
    return None


def _anomaly_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if int(row.get("internal_filtered_count") or 0) > 0:
        flags.append("internal_counterparty_filtered")
    if int(row.get("invalid_size_count") or 0) > 0:
        flags.append("invalid_size_filtered")
    if int(row.get("invalid_price_count") or 0) > 0:
        flags.append("invalid_price_filtered")
    if int(row.get("extreme_trade_count") or 0) > 0:
        flags.append("extreme_price_trade_present")
    if int(row.get("raw_price_fallback_count") or 0) > 0:
        flags.append("raw_price_fallback_used")
    return flags


def fetch_eligible_block_tokens(conn: Any, *, limit: int | None = None) -> dict[str, dict[str, Any]]:
    params: list[Any] = []
    limit_sql = ""
    if limit is not None:
        limit_sql = "LIMIT %s"
        params.append(int(limit))
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT m.token_id, m.token_id_hex, m.market_id, m.market_slug, m.token_side
            FROM quant.market_token_metadata m
            JOIN quant.market_price_eligibility e ON e.token_id = m.token_id
            WHERE e.eligible = TRUE
              AND m.token_id_hex IS NOT NULL
            ORDER BY m.market_id ASC, m.outcome_index ASC, m.token_id ASC
            {limit_sql}
            """,
            params,
        )
        return {str(row["token_id_hex"]).lower(): dict(row) for row in cur.fetchall()}


def insert_block_close_rows(conn: Any, metadata_by_token: dict[str, dict[str, Any]], rows: list[dict[str, Any]]) -> int:
    values = []
    for row in rows:
        clickhouse_token_id = str(row.get("token_id") or "").lower()
        meta = metadata_by_token.get(clickhouse_token_id)
        if not meta:
            continue
        token_id = str(meta["token_id"])
        close_price = _decimal_or_none(row.get("close_price"))
        vwap_price = _decimal_or_none(row.get("vwap_price"))
        close_raw_price = _decimal_or_none(row.get("close_raw_price"))
        close_maker_amount = _decimal_or_none(row.get("close_maker_amount"))
        close_taker_amount = _decimal_or_none(row.get("close_taker_amount"))
        token_side = meta.get("token_side")
        values.append(
            (
                token_id,
                int(meta["market_id"]),
                meta.get("market_slug"),
                token_side,
                int(row["block_number"]),
                _timestamp_or_none(row.get("block_timestamp")),
                close_price,
                _yes_probability(str(token_side), close_price),
                vwap_price,
                _yes_probability(str(token_side), vwap_price),
                close_raw_price,
                row.get("close_price_source") or "unknown",
                row.get("close_tx_hash"),
                int(row["close_log_index"]) if row.get("close_log_index") is not None else None,
                close_maker_amount,
                close_taker_amount,
                int(row.get("clean_trade_count") or 0),
                int(row.get("raw_trade_count") or 0),
                int(row.get("internal_filtered_count") or 0),
                int(row.get("invalid_size_count") or 0),
                int(row.get("invalid_price_count") or 0),
                int(row.get("amount_ratio_count") or 0),
                int(row.get("raw_price_fallback_count") or 0),
                int(row.get("extreme_trade_count") or 0),
                json.dumps(_anomaly_flags(row), sort_keys=True),
                _decimal_or_none(row.get("volume")) or Decimal("0"),
            )
        )
    if not values:
        return 0
    columns = (
        "token_id", "market_id", "market_slug", "token_side", "block_number",
        "block_timestamp", "close_price", "yes_probability_close", "vwap_price", "yes_probability_vwap",
        "close_raw_price", "close_price_source", "close_tx_hash", "close_log_index",
        "close_maker_amount", "close_taker_amount", "trade_count", "raw_trade_count",
        "internal_filtered_count", "invalid_size_count", "invalid_price_count",
        "amount_ratio_count", "raw_price_fallback_count", "extreme_trade_count",
        "anomaly_flags", "volume",
    )
    column_sql = ", ".join(columns)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TEMP TABLE tmp_market_token_block_close
            ON COMMIT DROP
            AS SELECT {column_sql}
            FROM quant.market_token_block_close
            WHERE FALSE
            """
        )
        with cur.copy(f"COPY tmp_market_token_block_close ({column_sql}) FROM STDIN") as copy:
            for value in values:
                copy.write_row(value)
        cur.execute(
            f"""
            INSERT INTO quant.market_token_block_close ({column_sql})
            SELECT {column_sql}
            FROM tmp_market_token_block_close
            ON CONFLICT (token_id, block_number) DO NOTHING
            """
        )
    return len(values)


def backfill_block_close_prices(
    conn: Any,
    ch: ClickHouseClient,
    *,
    from_block: int,
    to_block: int,
    limit: int | None = None,
) -> dict[str, int]:
    metadata_by_token = fetch_eligible_block_tokens(conn, limit=limit)
    if not metadata_by_token:
        return {"tokens": 0, "rows_written": 0}
    sql = orderfilled_block_close_sql(
        table=ch.settings.orderfilled_table,
        from_block=from_block,
        to_block=to_block,
        token_ids=metadata_by_token.keys(),
    )
    rows = ch.query_json_rows(sql)
    rows_written = insert_block_close_rows(conn, metadata_by_token, rows)
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO quant.market_price_build_market_state (
                source, token_id, market_id, market_slug, token_side,
                status, last_complete_block, attempt_count, updated_at
            ) VALUES (%s, %s, %s, %s, %s, 'complete', %s, 1, now())
            ON CONFLICT (source, token_id) DO UPDATE SET
                market_id = EXCLUDED.market_id,
                market_slug = EXCLUDED.market_slug,
                token_side = EXCLUDED.token_side,
                status = EXCLUDED.status,
                last_complete_block = GREATEST(
                    COALESCE(quant.market_price_build_market_state.last_complete_block, 0),
                    EXCLUDED.last_complete_block
                ),
                attempt_count = quant.market_price_build_market_state.attempt_count + 1,
                last_error = NULL,
                updated_at = now()
            """,
            [
                (
                    SOURCE,
                    meta["token_id"],
                    meta["market_id"],
                    meta.get("market_slug"),
                    meta["token_side"],
                    int(to_block),
                )
                for meta in metadata_by_token.values()
            ],
        )
    return {"tokens": len(metadata_by_token), "rows_written": rows_written}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill quant OrderFilled block close prices once.")
    parser.add_argument("--from-block", type=int, required=True)
    parser.add_argument("--to-block", type=int, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--refresh-metadata", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    with postgres_connection(PostgresSettings()) as conn:
        create_schema(conn)
        if args.refresh_metadata:
            refresh_market_token_metadata(conn)
        result = backfill_block_close_prices(
            conn,
            ClickHouseClient(),
            from_block=args.from_block,
            to_block=args.to_block,
            limit=args.limit,
        )
    print(result)


if __name__ == "__main__":
    main()
