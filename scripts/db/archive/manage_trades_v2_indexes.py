#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
显式管理 `trades_v2` 上的重索引。

当前重点目标：
- 删除 `idx_trades_v2_token_time`
- 删除 `idx_trades_v2_market_outcome_time`

说明：
- 这两个索引不属于当前主查询路径的刚需。
- 删除索引会降低逻辑索引占用，并减少后续写入维护成本。
- 物理 `.ibd` 文件通常不会立刻变小；若要立刻回收磁盘，需要后续重建表。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from db import add_db_cli_args, configure_db_from_args, describe_db_target, get_connection

TABLE_NAME = "trades_v2"

INDEX_SQL: Dict[str, str] = {
    "idx_trades_v2_token_time": (
        "ALTER TABLE trades_v2 "
        "ALGORITHM=INPLACE, LOCK=NONE, "
        "DROP INDEX idx_trades_v2_token_time"
    ),
    "idx_trades_v2_market_outcome_time": (
        "ALTER TABLE trades_v2 "
        "ALGORITHM=INPLACE, LOCK=NONE, "
        "DROP INDEX idx_trades_v2_market_outcome_time"
    ),
}

INDEX_NOTES: Dict[str, str] = {
    "idx_trades_v2_token_time": "按 token_id 的交易时间复合索引；当前主链路未依赖，预估可省约 80G-110G 逻辑索引空间。",
    "idx_trades_v2_market_outcome_time": "按 market_id+outcome 的交易时间复合索引；当前主链路未依赖，预估可省约 50G-75G 逻辑索引空间。",
}


def get_existing_indexes(conn) -> List[str]:
    cursor = conn.cursor()
    cursor.execute(f"SHOW INDEX FROM {TABLE_NAME}")
    return sorted({row[2] for row in cursor.fetchall()})


def show_table_status(conn) -> None:
    cursor = conn.cursor()
    cursor.execute(f"SHOW TABLE STATUS LIKE '{TABLE_NAME}'")
    row = cursor.fetchone()
    if not row:
        print(f"[warn] table {TABLE_NAME} not found")
        return
    data_mb = (int(row["Data_length"] or 0) / 1024 / 1024)
    index_mb = (int(row["Index_length"] or 0) / 1024 / 1024)
    free_mb = (int(row["Data_free"] or 0) / 1024 / 1024)
    print(
        f"[status] rows~{int(row['Rows'] or 0)} data={data_mb:.2f} MiB "
        f"index={index_mb:.2f} MiB free={free_mb:.2f} MiB"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage heavy secondary indexes on trades_v2")
    add_db_cli_args(parser)
    parser.add_argument("--drop-token-time", action="store_true", help="删除 idx_trades_v2_token_time")
    parser.add_argument(
        "--drop-market-outcome-time",
        action="store_true",
        help="删除 idx_trades_v2_market_outcome_time",
    )
    parser.add_argument("--drop-redundant", action="store_true", help="一次删除两个候选重索引")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不执行")
    parser.add_argument("--analyze", action="store_true", help="执行后运行 ANALYZE TABLE trades_v2")
    parser.add_argument("--show-status", action="store_true", help="打印 trades_v2 当前大小估算")
    args = parser.parse_args()
    configure_db_from_args(args)

    selected: List[str] = []
    if args.drop_redundant or args.drop_token_time:
        selected.append("idx_trades_v2_token_time")
    if args.drop_redundant or args.drop_market_outcome_time:
        selected.append("idx_trades_v2_market_outcome_time")

    if not selected and not args.show_status:
        parser.error("请至少指定一个动作，例如 --drop-redundant 或 --show-status")

    conn = get_connection()
    try:
        existing = set(get_existing_indexes(conn))
        print(f"Database target: {describe_db_target()}")
        if args.show_status:
            show_table_status(conn)

        for index_name in selected:
            note = INDEX_NOTES.get(index_name)
            if note:
                print(f"[note] {index_name}: {note}")
            if index_name not in existing:
                print(f"[skip] {index_name} does not exist")
                continue
            sql = INDEX_SQL[index_name]
            print(f"[plan] {sql}")
            if args.dry_run:
                continue
            conn.execute(sql)
            conn.commit()
            print(f"[done] {index_name}")

        if args.analyze and not args.dry_run:
            conn.execute(f"ANALYZE TABLE {TABLE_NAME}")
            conn.commit()
            print(f"[done] ANALYZE TABLE {TABLE_NAME}")
            show_table_status(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
