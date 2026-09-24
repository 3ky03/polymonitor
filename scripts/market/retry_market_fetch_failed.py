#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重试主回补脚本已经扫描过、但仍缺 canonical 字段的 market。

用途：
1. 主回补脚本曾因 Gamma/CLOB 短暂失败导致 fetch_failed
2. 不重新扫全表，只重试“已经扫过但仍未补齐”的残留 market
3. 适合在 market_canonical_backfill 跑完后做第二轮收尾
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from db import add_db_cli_args, configure_db_from_args, describe_db_target, get_connection, init_schema
from market.backfill_market_canonical_fields import (
    DEFAULT_SYNC_STATE_KEY,
    _load_resume_id,
    _resolve_candidate,
)
from market.market_discovery import batch_upsert_markets


DEFAULT_BATCH_SIZE = 200
DEFAULT_WORKERS = 8


def _select_retry_candidates(
    conn,
    *,
    start_id: int,
    max_id: int,
    batch_size: int,
) -> List[Tuple[Any, ...]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, slug, condition_id, title, gamma_market_id, question_id, oracle, created_at, end_date
        FROM markets
        WHERE id > ?
          AND id <= ?
          AND (
                gamma_market_id IS NULL OR TRIM(gamma_market_id) = ''
             OR question_id IS NULL OR TRIM(question_id) = ''
             OR oracle IS NULL OR TRIM(oracle) = ''
             OR created_at IS NULL
             OR end_date IS NULL
          )
        ORDER BY id ASC
        LIMIT ?
        """,
        (int(start_id), int(max_id), int(batch_size)),
    )
    return [tuple(row) for row in cur.fetchall()]


def run_retry(
    db_path: Optional[str],
    *,
    batch_size: int,
    workers: int,
    limit: Optional[int],
    start_id: int,
    max_id: int,
    dry_run: bool,
) -> Dict[str, int]:
    init_schema(db_path=db_path)
    conn = get_connection(db_path)
    try:
        stats = {
            "scanned": 0,
            "resolved": 0,
            "updated": 0,
            "noop": 0,
            "unresolved": 0,
        }
        unresolved_samples: List[str] = []
        last_seen_id = int(start_id)

        while True:
            remaining = None if limit is None else max(0, int(limit) - stats["scanned"])
            if remaining == 0:
                break

            fetch_size = batch_size if remaining is None else min(batch_size, remaining)
            rows = _select_retry_candidates(
                conn,
                start_id=last_seen_id,
                max_id=max_id,
                batch_size=fetch_size,
            )
            if not rows:
                break

            norms: List[Dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=max(1, int(workers))) as executor:
                future_to_row = {executor.submit(_resolve_candidate, row): row for row in rows}
                for future in as_completed(future_to_row):
                    result = future.result()
                    stats["scanned"] += 1
                    status = result["status"]
                    if status == "resolved":
                        stats["resolved"] += 1
                        norms.append(result["market"])
                    elif status == "noop":
                        stats["noop"] += 1
                    else:
                        stats["unresolved"] += 1
                        if len(unresolved_samples) < 20:
                            unresolved_samples.append(
                                f"id={result['market_row_id']} slug={result['slug']} reason={result['reason']}"
                            )

            if norms and not dry_run:
                stats["updated"] += batch_upsert_markets(conn, norms)

            last_seen_id = max(int(row[0]) for row in rows)
            print(
                f"[retry-fetch-failed] scanned={stats['scanned']} resolved={stats['resolved']} "
                f"updated={stats['updated']} noop={stats['noop']} unresolved={stats['unresolved']} "
                f"last_id={last_seen_id}/{max_id}",
                file=sys.stderr,
            )

            if len(rows) < fetch_size:
                break

        if unresolved_samples:
            print("[retry-fetch-failed] unresolved samples:", file=sys.stderr)
            for sample in unresolved_samples:
                print(f"  - {sample}", file=sys.stderr)

        return stats
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Retry markets already scanned by canonical backfill but still missing canonical fields"
    )
    add_db_cli_args(parser)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=f"每轮扫描多少条（default: {DEFAULT_BATCH_SIZE}）")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"并发请求数（default: {DEFAULT_WORKERS}）")
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少条候选记录")
    parser.add_argument("--start-id", type=int, default=0, help="只处理 markets.id 大于该值的记录")
    parser.add_argument(
        "--max-id",
        type=int,
        default=None,
        help="只重试到该 id 为止；默认读取 sync_state.market_canonical_backfill.last_block",
    )
    parser.add_argument(
        "--sync-state-key",
        default=DEFAULT_SYNC_STATE_KEY,
        help=f"读取主回补进度的 sync_state key（default: {DEFAULT_SYNC_STATE_KEY}）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只扫描和请求，不写回数据库")
    args = parser.parse_args()
    configure_db_from_args(args)

    db_path = getattr(args, "sqlite_path", None)
    init_schema(db_path=db_path)
    conn = get_connection(db_path)
    try:
        default_max_id = _load_resume_id(conn, args.sync_state_key)
    finally:
        conn.close()

    effective_max_id = int(args.max_id) if args.max_id is not None else int(default_max_id)
    if effective_max_id <= 0:
        raise SystemExit(
            f"无法确定有效的 --max-id；请先运行主回补脚本或显式传入 --max-id。当前 sync_state key={args.sync_state_key}"
        )

    print(f"Database target: {describe_db_target()}", file=sys.stderr)
    print(
        f"Retrying scanned-but-still-incomplete markets in id range ({int(args.start_id)}, {effective_max_id}]",
        file=sys.stderr,
    )
    stats = run_retry(
        db_path,
        batch_size=args.batch_size,
        workers=args.workers,
        limit=args.limit,
        start_id=int(args.start_id),
        max_id=effective_max_id,
        dry_run=bool(args.dry_run),
    )
    print(stats)


if __name__ == "__main__":
    main()
