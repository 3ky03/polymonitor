#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild trades_v2 into a compact table that reclaims Data_free and drops asset-id hot columns."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from db import DEFAULT_DB_PATH, add_db_cli_args, configure_db_from_args, describe_db_target, get_connection, init_schema
from db.trade_v2 import (
    TRADE_V2_CORE_TABLE,
    TRADE_V2_READ_VIEW,
    create_trade_v2_core_table,
    create_trade_v2_read_view,
    sql_identifier,
)

DEFAULT_BATCH_SIZE = 50_000
DEFAULT_TARGET_TABLE = "trades_v2_compact"
DEFAULT_BACKUP_TABLE = "trades_v2_pre_rebuild_backup"


def _scalar(conn, sql: str, params=()) -> Any:
    cursor = conn.cursor()
    cursor.execute(sql, params)
    row = cursor.fetchone()
    if row is None:
        return None
    return row[0]


def _table_exists(conn, table_name: str) -> bool:
    cursor = conn.cursor()
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None


def _fetch_bounds(conn, table_name: str) -> Dict[str, int]:
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT COALESCE(MIN(id), 0) AS min_id, COALESCE(MAX(id), 0) AS max_id
        FROM {sql_identifier(table_name)}
        """
    )
    row = cursor.fetchone()
    return {
        "min_id": int(row["min_id"] or 0),
        "max_id": int(row["max_id"] or 0),
    }


def _show_table_status(conn, table_name: str) -> Dict[str, Any]:
    cursor = conn.cursor()
    cursor.execute(f"SHOW TABLE STATUS LIKE %s", (table_name,))
    row = cursor.fetchone()
    if row is None:
        return {}
    return {
        "rows": int(row["Rows"] or 0),
        "data_length": int(row["Data_length"] or 0),
        "index_length": int(row["Index_length"] or 0),
        "data_free": int(row["Data_free"] or 0),
    }


def _copy_batches(conn, *, source_table: str, target_table: str, batch_size: int) -> Dict[str, int]:
    source = sql_identifier(source_table)
    target = sql_identifier(target_table)
    print(f"Fetching source bounds from {source_table}...", file=sys.stderr)
    bounds = _fetch_bounds(conn, source_table)
    current = bounds["min_id"]
    final_id = bounds["max_id"]
    total_inserted = 0
    if current <= 0 or final_id <= 0 or current > final_id:
        return {"rows": 0, "min_id": 0, "max_id": 0}
    print(
        f"Source bounds ready: min_id={bounds['min_id']} max_id={bounds['max_id']}",
        file=sys.stderr,
    )

    while current <= final_id:
        batch_end = min(current + batch_size - 1, final_id)
        cursor = conn.execute(
            f"""
            INSERT IGNORE INTO {target} (
                id, tx_hash, log_index, market_id, maker, taker,
                price, size, side_code, outcome_code, token_id,
                block_number, block_time, order_hash, contract,
                maker_amount, taker_amount, fee, created_at
            )
            SELECT
                id, tx_hash, log_index, market_id, maker, taker,
                price, size, side_code, outcome_code, token_id,
                block_number, block_time, order_hash, contract,
                maker_amount, taker_amount, fee, created_at
            FROM {source}
            WHERE id >= %s AND id <= %s
            ORDER BY id ASC
            """,
            (current, batch_end),
        )
        inserted = int(cursor.rowcount or 0)
        conn.commit()
        total_inserted += max(inserted, 0)
        print(
            f"Rebuilt id range {current}-{batch_end}: inserted={inserted} total_inserted={total_inserted}",
            file=sys.stderr,
        )
        current = batch_end + 1

    return {
        "rows": total_inserted,
        "min_id": bounds["min_id"],
        "max_id": bounds["max_id"],
    }


def run_rebuild(
    *,
    db_path: str,
    source_table: str,
    target_table: str,
    backup_table: str,
    batch_size: int,
    drop_target_if_exists: bool,
    swap: bool,
    swap_only: bool,
    analyze: bool,
) -> None:
    init_schema(db_path=db_path)
    conn = get_connection(db_path)
    try:
        if not _table_exists(conn, source_table):
            raise RuntimeError(f"Source table not found: {source_table}")
        if swap_only:
            if not _table_exists(conn, target_table):
                raise RuntimeError(f"Target table not found for swap-only mode: {target_table}")
            copy_stats = {"rows": 0, "min_id": 0, "max_id": 0}
        else:
            if _table_exists(conn, target_table):
                if not drop_target_if_exists:
                    raise RuntimeError(
                        f"Target table already exists: {target_table}. "
                        "Use --drop-target-if-exists if you want to recreate it."
                    )
                conn.execute(f"DROP TABLE {sql_identifier(target_table)}")
                conn.commit()

            print(
                f"Rebuilding {source_table} -> {target_table} batch_size={batch_size} target={describe_db_target()}",
                file=sys.stderr,
            )
            create_trade_v2_core_table(conn, target_table)
            copy_stats = _copy_batches(
                conn,
                source_table=source_table,
                target_table=target_table,
                batch_size=batch_size,
            )
            if analyze:
                conn.execute(f"ANALYZE TABLE {sql_identifier(target_table)}")
                conn.commit()
                print(f"Analyze completed for {target_table}", file=sys.stderr)

        source_status = _show_table_status(conn, source_table)
        target_status = _show_table_status(conn, target_table)

        if swap:
            if _table_exists(conn, backup_table):
                raise RuntimeError(
                    f"Backup table already exists: {backup_table}. "
                    "Please drop or rename it before using --swap."
                )
            conn.execute(
                f"""
                RENAME TABLE
                    {sql_identifier(source_table)} TO {sql_identifier(backup_table)},
                    {sql_identifier(target_table)} TO {sql_identifier(source_table)}
                """
            )
            conn.commit()
            create_trade_v2_read_view(conn)
            print(
                f"Swap completed: {source_table} -> {backup_table}, {target_table} -> {source_table}",
                file=sys.stderr,
            )

        print(
            {
                "sourceTable": source_table,
                "targetTable": target_table,
                "backupTable": backup_table,
                "rowsCopied": copy_stats["rows"],
                "sourceStatus": source_status,
                "targetStatus": target_status,
                "swap": swap,
            }
        )
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild trades_v2 to reclaim Data_free and drop asset-id hot columns")
    add_db_cli_args(parser)
    parser.add_argument("--source-table", default=TRADE_V2_CORE_TABLE, help="Existing trades_v2-style source table")
    parser.add_argument("--target-table", default=DEFAULT_TARGET_TABLE, help="New compact table name")
    parser.add_argument("--backup-table", default=DEFAULT_BACKUP_TABLE, help="Backup name used when --swap is enabled")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Rows per INSERT...SELECT batch")
    parser.add_argument("--drop-target-if-exists", action="store_true", help="Drop target table before rebuild")
    parser.add_argument("--swap", action="store_true", help="Swap target into source table name after rebuild")
    parser.add_argument("--swap-only", action="store_true", help="Only swap an existing target table into place")
    parser.add_argument("--analyze", action="store_true", help="Run ANALYZE TABLE on the target after copy")
    args = parser.parse_args()
    configure_db_from_args(args)
    if args.swap_only and args.drop_target_if_exists:
        parser.error("--swap-only 不能和 --drop-target-if-exists 同时使用")
    run_rebuild(
        db_path=getattr(args, "sqlite_path", DEFAULT_DB_PATH),
        source_table=args.source_table,
        target_table=args.target_table,
        backup_table=args.backup_table,
        batch_size=max(1, int(args.batch_size)),
        drop_target_if_exists=bool(args.drop_target_if_exists),
        swap=bool(args.swap),
        swap_only=bool(args.swap_only),
        analyze=bool(args.analyze),
    )


if __name__ == "__main__":
    main()
