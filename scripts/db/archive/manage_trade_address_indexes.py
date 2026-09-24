#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
显式管理 `trades` 上的地址查询索引。

说明：
- 不在应用启动时自动加索引，避免对超大表做隐式重 DDL。
- 通过这个脚本按需、分步骤地创建 maker/taker 地址索引。
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

INDEX_SQL: Dict[str, str] = {
    "idx_trades_maker_time_block_log": "ALTER TABLE trades ALGORITHM=INPLACE, LOCK=NONE, ADD INDEX idx_trades_maker_time_block_log (maker, timestamp, block_number, log_index)",
    "idx_trades_taker_time_block_log": "ALTER TABLE trades ALGORITHM=INPLACE, LOCK=NONE, ADD INDEX idx_trades_taker_time_block_log (taker, timestamp, block_number, log_index)",
    "idx_trades_maker_market_time_block_log": "ALTER TABLE trades ALGORITHM=INPLACE, LOCK=NONE, ADD INDEX idx_trades_maker_market_time_block_log (maker, market_id, timestamp, block_number, log_index)",
    "idx_trades_taker_market_time_block_log": "ALTER TABLE trades ALGORITHM=INPLACE, LOCK=NONE, ADD INDEX idx_trades_taker_market_time_block_log (taker, market_id, timestamp, block_number, log_index)",
}


def get_existing_indexes(conn) -> List[str]:
    cursor = conn.cursor()
    cursor.execute("SHOW INDEX FROM trades")
    return sorted({row[2] for row in cursor.fetchall()})


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage maker/taker address indexes on trades")
    add_db_cli_args(parser)
    parser.add_argument("--maker-time", action="store_true", help="创建 (maker, timestamp, block_number, log_index)")
    parser.add_argument("--taker-time", action="store_true", help="创建 (taker, timestamp, block_number, log_index)")
    parser.add_argument("--maker-market-time", action="store_true", help="创建 (maker, market_id, timestamp, block_number, log_index)")
    parser.add_argument("--taker-market-time", action="store_true", help="创建 (taker, market_id, timestamp, block_number, log_index)")
    parser.add_argument("--all", action="store_true", help="创建四个地址索引")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的 SQL")
    args = parser.parse_args()
    configure_db_from_args(args)

    selected: List[str] = []
    if args.all or args.maker_time:
        selected.append("idx_trades_maker_time_block_log")
    if args.all or args.taker_time:
        selected.append("idx_trades_taker_time_block_log")
    if args.all or args.maker_market_time:
        selected.append("idx_trades_maker_market_time_block_log")
    if args.all or args.taker_market_time:
        selected.append("idx_trades_taker_market_time_block_log")

    if not selected:
        parser.error("请至少指定一个索引，例如 --maker-time")

    conn = get_connection()
    try:
        existing = set(get_existing_indexes(conn))
        print(f"Database target: {describe_db_target()}")
        for index_name in selected:
            if index_name in existing:
                print(f"[skip] {index_name} already exists")
                continue
            sql = INDEX_SQL[index_name]
            print(f"[plan] {sql}")
            if args.dry_run:
                continue
            conn.execute(sql)
            conn.commit()
            print(f"[done] {index_name}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
