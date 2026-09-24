#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate single-table trades_v2 backfill coverage and field fidelity."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from db import add_db_cli_args, configure_db_from_args, get_connection  # type: ignore
from db.trade_v2 import (  # type: ignore
    LEGACY_TRADES_TABLE,
    TRADE_V2_CORE_TABLE,
    compat_maker_asset_id_sql,
    compat_taker_asset_id_sql,
    ensure_trade_v2_schema,
    sql_identifier,
    uint256_storage_to_text,
)


def _scalar(conn, sql: str, params=()) -> Any:
    cursor = conn.cursor()
    cursor.execute(sql, params)
    row = cursor.fetchone()
    if row is None:
        return None
    return row[0]


def collect_validation_report(
    conn,
    *,
    start_id: int | None = None,
    end_id: int | None = None,
) -> Dict[str, Any]:
    ensure_trade_v2_schema(conn)
    legacy = sql_identifier(LEGACY_TRADES_TABLE)
    core = sql_identifier(TRADE_V2_CORE_TABLE)
    legacy_filters = []
    params = []
    if start_id is not None:
        legacy_filters.append("id >= ?")
        params.append(start_id)
    if end_id is not None:
        legacy_filters.append("id <= ?")
        params.append(end_id)
    legacy_where = f"WHERE {' AND '.join(legacy_filters)}" if legacy_filters else ""
    legacy_where_l = f"WHERE {' AND '.join(f'l.{item}' for item in legacy_filters)}" if legacy_filters else ""

    legacy_count = int(_scalar(conn, f"SELECT COUNT(*) FROM {legacy} {legacy_where}", params) or 0)
    core_count = int(_scalar(conn, f"SELECT COUNT(*) FROM {core}") or 0)

    missing_core = int(
        _scalar(
            conn,
            f"""
            SELECT COUNT(*)
            FROM {legacy} l
            LEFT JOIN {core} c
              ON c.tx_hash = UNHEX(l.tx_hash)
             AND c.log_index = l.log_index
            {legacy_where_l if not legacy_where_l else legacy_where_l + ' AND'} c.tx_hash IS NULL
            """,
            params,
        )
        or 0
    )
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT
            l.id,
            l.price AS legacy_price,
            c.price AS core_price,
            l.size AS legacy_size,
            c.size AS core_size,
            l.token_id AS legacy_token_id,
            c.token_id AS core_token_id,
            l.order_hash AS legacy_order_hash,
            LOWER(HEX(c.order_hash)) AS core_order_hash,
            l.maker_asset_id AS legacy_maker_asset_id,
            {compat_maker_asset_id_sql('c')} AS core_maker_asset_id,
            l.taker_asset_id AS legacy_taker_asset_id,
            {compat_taker_asset_id_sql('c')} AS core_taker_asset_id,
            l.contract AS legacy_contract,
            c.contract AS core_contract
        FROM {legacy} l
        JOIN {core} c
          ON c.tx_hash = UNHEX(l.tx_hash)
         AND c.log_index = l.log_index
        {legacy_where_l}
        ORDER BY l.id ASC
        LIMIT 1000
        """,
        params,
    )
    sample_mismatch = 0
    for row in cursor.fetchall():
        legacy_price = str(row["legacy_price"] or "")
        legacy_size = str(row["legacy_size"] or "")
        try:
            legacy_price_dec = Decimal(legacy_price) if legacy_price else None
        except (InvalidOperation, ValueError):
            legacy_price_dec = None
        try:
            legacy_size_dec = Decimal(legacy_size) if legacy_size else None
        except (InvalidOperation, ValueError):
            legacy_size_dec = None
        price_match = row["core_price"] == legacy_price_dec
        size_match = row["core_size"] == legacy_size_dec
        token_match = (uint256_storage_to_text(row["core_token_id"]) or "") == str(row["legacy_token_id"] or "")
        maker_asset_match = (uint256_storage_to_text(row["core_maker_asset_id"]) or "") == str(row["legacy_maker_asset_id"] or "")
        taker_asset_match = (uint256_storage_to_text(row["core_taker_asset_id"]) or "") == str(row["legacy_taker_asset_id"] or "")
        order_hash_match = (str(row["core_order_hash"] or "")).lower() == str(row["legacy_order_hash"] or "").lower()
        contract_match = str(row["core_contract"] or "").lower() == str(row["legacy_contract"] or "").lower()
        if not all([price_match, size_match, token_match, maker_asset_match, taker_asset_match, order_hash_match, contract_match]):
            sample_mismatch += 1

    return {
        "startId": start_id,
        "endId": end_id,
        "legacyTradeCount": legacy_count,
        "coreTradeCount": core_count,
        "missingCoreTrades": missing_core,
        "sampleMismatchCount": sample_mismatch,
        "ok": missing_core == 0 and sample_mismatch == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate trades_v2 backfill")
    add_db_cli_args(parser)
    parser.add_argument("--start-id", type=int, default=None, help="Inclusive legacy trades.id lower bound")
    parser.add_argument("--end-id", type=int, default=None, help="Inclusive legacy trades.id upper bound")
    args = parser.parse_args()
    configure_db_from_args(args)
    conn = get_connection(getattr(args, "sqlite_path", None))
    try:
        report = collect_validation_report(conn, start_id=args.start_id, end_id=args.end_id)
    finally:
        conn.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
