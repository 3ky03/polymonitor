#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Preflight checks and guarded legacy trades cutover/drop helper."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Optional

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from db import add_db_cli_args, configure_db_from_args, get_connection, get_table_columns  # type: ignore
from db.trade_v2 import (  # type: ignore
    LEGACY_TRADES_TABLE,
    TRADE_V2_CORE_TABLE,
    TRADE_V2_MIGRATION_STATE_TABLE,
    compat_maker_asset_id_sql,
    compat_taker_asset_id_sql,
    ensure_trade_v2_schema,
    sql_identifier,
    uint256_storage_to_text,
)

DEFAULT_MIGRATION_NAME = "legacy_trades_to_v2"
DEFAULT_BACKUP_TABLE = "trades_legacy_backup"
DEFAULT_SAMPLE_SIZE = 5_000


def _scalar(conn, sql: str, params=()) -> Any:
    cursor = conn.cursor()
    cursor.execute(sql, params)
    row = cursor.fetchone()
    if row is None:
        return None
    return row[0]


def _table_exists(conn, table_name: str) -> bool:
    return bool(get_table_columns(conn, table_name))


def _count_rows(conn, table_name: str) -> int:
    return int(_scalar(conn, f"SELECT COUNT(*) FROM {sql_identifier(table_name)}") or 0)


def _min_max_id(conn, table_name: str) -> Dict[str, int]:
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


def _safe_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _sample_report(conn, legacy_table: str, *, start_id: int, end_id: int) -> Dict[str, Any]:
    legacy = sql_identifier(legacy_table)
    core = sql_identifier(TRADE_V2_CORE_TABLE)
    params = (start_id, end_id)
    missing_core = int(
        _scalar(
            conn,
            f"""
            SELECT COUNT(*)
            FROM {legacy} l
            LEFT JOIN {core} c
              ON c.tx_hash = UNHEX(l.tx_hash)
             AND c.log_index = l.log_index
            WHERE l.id >= ? AND l.id <= ?
              AND c.tx_hash IS NULL
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
        WHERE l.id >= ? AND l.id <= ?
        ORDER BY l.id ASC
        LIMIT 1000
        """,
        params,
    )
    sample_mismatch = 0
    for row in cursor.fetchall():
        checks = [
            _safe_decimal(row["legacy_price"]) == row["core_price"],
            _safe_decimal(row["legacy_size"]) == row["core_size"],
            (uint256_storage_to_text(row["core_token_id"]) or "") == str(row["legacy_token_id"] or ""),
            (uint256_storage_to_text(row["core_maker_asset_id"]) or "") == str(row["legacy_maker_asset_id"] or ""),
            (uint256_storage_to_text(row["core_taker_asset_id"]) or "") == str(row["legacy_taker_asset_id"] or ""),
            str(row["core_order_hash"] or "").lower() == str(row["legacy_order_hash"] or "").lower(),
            str(row["core_contract"] or "").lower() == str(row["legacy_contract"] or "").lower(),
        ]
        if not all(checks):
            sample_mismatch += 1

    return {
        "startId": start_id,
        "endId": end_id,
        "missingCoreTrades": missing_core,
        "sampleMismatchCount": sample_mismatch,
        "ok": missing_core == 0 and sample_mismatch == 0,
    }


def collect_cutover_report(
    conn,
    *,
    migration_name: str,
    backup_table: str,
    sample_size: int,
) -> Dict[str, Any]:
    ensure_trade_v2_schema(conn)

    legacy_exists = _table_exists(conn, LEGACY_TRADES_TABLE)
    backup_exists = _table_exists(conn, backup_table)
    v2_exists = _table_exists(conn, TRADE_V2_CORE_TABLE)

    source_table = None
    if legacy_exists:
        source_table = LEGACY_TRADES_TABLE
    elif backup_exists:
        source_table = backup_table

    report: Dict[str, Any] = {
        "migrationName": migration_name,
        "legacyTableExists": legacy_exists,
        "backupTableExists": backup_exists,
        "tradesV2Exists": v2_exists,
        "sourceTable": source_table,
        "checks": [],
    }

    def add_check(name: str, ok: bool, details: Any) -> None:
        report["checks"].append({"name": name, "ok": bool(ok), "details": details})

    add_check("trades_v2_exists", v2_exists, {"table": TRADE_V2_CORE_TABLE})
    add_check(
        "legacy_or_backup_exists",
        source_table is not None,
        {"legacyTable": LEGACY_TRADES_TABLE, "backupTable": backup_table},
    )
    if source_table is None or not v2_exists:
        report["ok"] = False
        return report

    completed_runs = int(
        _scalar(
            conn,
            f"""
            SELECT COUNT(*)
            FROM {TRADE_V2_MIGRATION_STATE_TABLE}
            WHERE migration_name = ?
              AND status = 'completed'
            """,
            (migration_name,),
        )
        or 0
    )
    running_runs = int(
        _scalar(
            conn,
            f"""
            SELECT COUNT(*)
            FROM {TRADE_V2_MIGRATION_STATE_TABLE}
            WHERE migration_name = ?
              AND status = 'running'
            """,
            (migration_name,),
        )
        or 0
    )
    add_check("migration_has_completed_batches", completed_runs > 0, {"completedBatches": completed_runs})
    add_check("migration_not_currently_running", running_runs == 0, {"runningBatches": running_runs})
    if running_runs > 0 or completed_runs <= 0:
        report["ok"] = all(item["ok"] for item in report["checks"])
        return report

    legacy_count = _count_rows(conn, source_table)
    v2_count = _count_rows(conn, TRADE_V2_CORE_TABLE)
    legacy_bounds = _min_max_id(conn, source_table)
    v2_bounds = _min_max_id(conn, TRADE_V2_CORE_TABLE)
    add_check(
        "row_count_matches",
        legacy_count == v2_count and legacy_count > 0,
        {"legacyCount": legacy_count, "tradesV2Count": v2_count},
    )
    add_check(
        "id_bounds_match",
        legacy_bounds == v2_bounds and legacy_bounds["min_id"] > 0,
        {"legacyBounds": legacy_bounds, "tradesV2Bounds": v2_bounds},
    )

    head_end = min(legacy_bounds["max_id"], legacy_bounds["min_id"] + max(1, sample_size) - 1)
    tail_start = max(legacy_bounds["min_id"], legacy_bounds["max_id"] - max(1, sample_size) + 1)
    head_report = _sample_report(conn, source_table, start_id=legacy_bounds["min_id"], end_id=head_end)
    tail_report = _sample_report(conn, source_table, start_id=tail_start, end_id=legacy_bounds["max_id"])
    report["headSample"] = head_report
    report["tailSample"] = tail_report
    add_check("head_sample_matches", head_report["ok"], head_report)
    add_check("tail_sample_matches", tail_report["ok"], tail_report)

    report["ok"] = all(item["ok"] for item in report["checks"])
    return report


def perform_cutover(
    conn,
    *,
    backup_table: str,
    drop_legacy: bool,
) -> Dict[str, Any]:
    legacy_exists = _table_exists(conn, LEGACY_TRADES_TABLE)
    backup_exists = _table_exists(conn, backup_table)

    if drop_legacy:
        target = LEGACY_TRADES_TABLE if legacy_exists else backup_table
        if not _table_exists(conn, target):
            raise RuntimeError(f"No legacy table available to drop: {target}")
        conn.execute(f"DROP TABLE {sql_identifier(target)}")
        conn.commit()
        return {"action": "drop", "droppedTable": target}

    if not legacy_exists:
        raise RuntimeError(
            f"Legacy table {LEGACY_TRADES_TABLE} not found; cannot rename. "
            f"If it is already renamed, rerun with --drop-legacy to drop {backup_table}."
        )
    if backup_exists:
        raise RuntimeError(
            f"Backup table {backup_table} already exists. "
            "Please drop or rename it first, or rerun with --drop-legacy if you intend to remove it."
        )

    conn.execute(
        f"RENAME TABLE {sql_identifier(LEGACY_TRADES_TABLE)} TO {sql_identifier(backup_table)}"
    )
    conn.commit()
    return {"action": "rename", "renamedTo": backup_table}


def main() -> None:
    parser = argparse.ArgumentParser(description="Check trades_v2 cutover safety and rename/drop legacy trades")
    add_db_cli_args(parser)
    parser.add_argument("--migration-name", default=DEFAULT_MIGRATION_NAME, help="Migration state name to inspect")
    parser.add_argument("--backup-table", default=DEFAULT_BACKUP_TABLE, help="Backup table name for legacy trades")
    parser.add_argument(
        "--sample-size",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help="Rows to validate from both the head and tail of the legacy id range",
    )
    parser.add_argument("--execute", action="store_true", help="Perform the rename/drop action after checks pass")
    parser.add_argument(
        "--drop-legacy",
        action="store_true",
        help="Drop the legacy table immediately after checks pass; otherwise rename it to the backup table",
    )
    args = parser.parse_args()
    configure_db_from_args(args)

    conn = get_connection(getattr(args, "sqlite_path", None))
    try:
        report = collect_cutover_report(
            conn,
            migration_name=args.migration_name,
            backup_table=args.backup_table,
            sample_size=max(1, int(args.sample_size)),
        )
        if args.execute:
            if not report.get("ok"):
                print(json.dumps(report, ensure_ascii=False, indent=2))
                raise SystemExit(1)
            report["execution"] = perform_cutover(
                conn,
                backup_table=args.backup_table,
                drop_legacy=bool(args.drop_legacy),
            )
    finally:
        conn.close()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
