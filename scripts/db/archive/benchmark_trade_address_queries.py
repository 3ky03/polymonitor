#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
评估 maker/taker 地址查询的索引方案。

默认思路：
1. 对目标地址分别测 maker 单路、taker 单路。
2. 再测 UNION ALL 合并。
3. 对比 OR 写法。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Sequence

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from db import add_db_cli_args, configure_db_from_args, get_connection


def _print_explain(rows: Iterable[Sequence[Any]]) -> None:
    for row in rows:
        if hasattr(row, "as_dict"):
            print("  EXPLAIN", row.as_dict())
        else:
            print("  EXPLAIN", row)


def _run_query(cursor, sql: str, params: Sequence[Any], runs: int) -> tuple[int, float, float]:
    timings = []
    rowcount = 0
    for _ in range(runs):
        start = time.perf_counter()
        cursor.execute(sql, params)
        rowcount = len(cursor.fetchall())
        timings.append((time.perf_counter() - start) * 1000)
    return rowcount, mean(timings), max(timings)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark address detail queries on trades")
    add_db_cli_args(parser)
    parser.add_argument("--table", default="trades", help="目标表，默认 trades")
    parser.add_argument("--address", required=True, help="目标地址")
    parser.add_argument("--market-id", type=int, default=None)
    parser.add_argument("--start-ts", default=None)
    parser.add_argument("--end-ts", default=None)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    configure_db_from_args(args)

    table = args.table
    address = args.address
    limit = args.limit

    conn = get_connection()
    cur = conn.cursor()
    try:
        tests = [
            (
                "A_maker_single",
                f"SELECT id, market_id, maker, taker, timestamp, block_number, log_index FROM {table} WHERE maker = ? ORDER BY timestamp DESC, block_number DESC, log_index DESC LIMIT {limit}",
                (address,),
            ),
            (
                "A_taker_single",
                f"SELECT id, market_id, maker, taker, timestamp, block_number, log_index FROM {table} WHERE taker = ? ORDER BY timestamp DESC, block_number DESC, log_index DESC LIMIT {limit}",
                (address,),
            ),
            (
                "A_or",
                f"SELECT id, market_id, maker, taker, timestamp, block_number, log_index FROM {table} WHERE maker = ? OR taker = ? ORDER BY timestamp DESC, block_number DESC, log_index DESC LIMIT {limit}",
                (address, address),
            ),
            (
                "A_union",
                f"""
                SELECT *
                FROM (
                    (
                        SELECT id, market_id, maker, taker, timestamp, block_number, log_index
                        FROM {table}
                        WHERE maker = ?
                        ORDER BY timestamp DESC, block_number DESC, log_index DESC
                        LIMIT {limit}
                    )
                    UNION ALL
                    (
                        SELECT id, market_id, maker, taker, timestamp, block_number, log_index
                        FROM {table}
                        WHERE taker = ?
                        ORDER BY timestamp DESC, block_number DESC, log_index DESC
                        LIMIT {limit}
                    )
                ) t
                ORDER BY timestamp DESC, block_number DESC, log_index DESC
                LIMIT {limit}
                """,
                (address, address),
            ),
        ]

        if args.start_ts and args.end_ts:
            tests.append(
                (
                    "B_union_time_range",
                    f"""
                    SELECT *
                    FROM (
                        (
                            SELECT id, market_id, maker, taker, timestamp, block_number, log_index
                            FROM {table}
                            WHERE maker = ? AND timestamp >= ? AND timestamp < ?
                            ORDER BY timestamp DESC, block_number DESC, log_index DESC
                            LIMIT {limit}
                        )
                        UNION ALL
                        (
                            SELECT id, market_id, maker, taker, timestamp, block_number, log_index
                            FROM {table}
                            WHERE taker = ? AND timestamp >= ? AND timestamp < ?
                            ORDER BY timestamp DESC, block_number DESC, log_index DESC
                            LIMIT {limit}
                        )
                    ) t
                    ORDER BY timestamp DESC, block_number DESC, log_index DESC
                    LIMIT {limit}
                    """,
                    (address, args.start_ts, args.end_ts, address, args.start_ts, args.end_ts),
                )
            )

        if args.market_id is not None:
            tests.append(
                (
                    "C_union_market",
                    f"""
                    SELECT *
                    FROM (
                        (
                            SELECT id, market_id, maker, taker, timestamp, block_number, log_index
                            FROM {table}
                            WHERE maker = ? AND market_id = ?
                            ORDER BY timestamp DESC, block_number DESC, log_index DESC
                            LIMIT {limit}
                        )
                        UNION ALL
                        (
                            SELECT id, market_id, maker, taker, timestamp, block_number, log_index
                            FROM {table}
                            WHERE taker = ? AND market_id = ?
                            ORDER BY timestamp DESC, block_number DESC, log_index DESC
                            LIMIT {limit}
                        )
                    ) t
                    ORDER BY timestamp DESC, block_number DESC, log_index DESC
                    LIMIT {limit}
                    """,
                    (address, args.market_id, address, args.market_id),
                )
            )

        for name, sql, params in tests:
            print(f"===== {name} =====")
            cur.execute("EXPLAIN " + sql, params)
            _print_explain(cur.fetchall())
            rowcount, avg_ms, max_ms = _run_query(cur, sql, params, args.runs)
            print(f"  rows={rowcount} avg_ms={avg_ms:.2f} max_ms={max_ms:.2f}")
            print()
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
