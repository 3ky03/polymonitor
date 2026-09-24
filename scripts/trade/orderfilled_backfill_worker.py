#!/usr/bin/env python3
"""Recoverable OrderFilled backfill worker.

This is the long-running counterpart to `orderfilled_audit_repair.py`.
It audits chain log counts by block window, repairs incomplete windows into
`orderfilled_raw`, and optionally rebuilds the address-view PnL cashflow table
from the repaired raw layer.

Use this for database-wide completeness. The address-txhash repair used by PnL
validation is intentionally not enough for market history or arbitrary-address
PnL; this worker repairs complete block windows for all addresses/markets.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

from config import get_rpc_url  # noqa: E402
from db import add_db_cli_args, configure_db_from_args, describe_db_target, get_connection, init_schema  # noqa: E402
from trade.orderfilled_audit_repair import (  # noqa: E402
    audit_window,
    build_web3,
    iter_block_windows,
)
from trade.orderfilled_cashflows import build_cashflows  # noqa: E402
from trade.orderfilled_raw import (  # noqa: E402
    ORDERFILLED_SYNC_WINDOWS_TABLE,
    ensure_orderfilled_raw_schema,
    upsert_orderfilled_sync_window,
)


DEFAULT_BATCH_BLOCKS = 1000
DEFAULT_RETRY_ATTEMPTS = 3


def latest_safe_block(w3: Any, confirmations: int) -> int:
    return max(0, int(w3.eth.block_number) - max(0, int(confirmations)))


def get_last_complete_to_block(conn) -> Optional[int]:
    ensure_orderfilled_raw_schema(conn)
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT MAX(to_block) AS last_block
        FROM {ORDERFILLED_SYNC_WINDOWS_TABLE}
        WHERE exchange_set = 'known_orderfilled' AND status = 'complete'
        """
    )
    row = cursor.fetchone()
    if row is None:
        return None
    if hasattr(row, "get"):
        value = row.get("last_block")
    else:
        value = row[0]
    return None if value is None else int(value)


def repair_window_with_retry(
    *,
    conn: Any,
    w3: Any,
    from_block: int,
    to_block: int,
    retry_attempts: int,
    min_batch_blocks: int,
    quiet: bool,
    include_block_time: bool,
    include_raw_json: bool,
    fast_decode: bool,
) -> list[dict[str, Any]]:
    """Repair a window, splitting it if the RPC cannot serve it reliably."""

    results: list[dict[str, Any]] = []
    last_error: Optional[BaseException] = None
    for attempt in range(1, max(1, retry_attempts) + 1):
        try:
            results.append(
                audit_window(
                    conn=conn,
                    w3=w3,
                    from_block=from_block,
                    to_block=to_block,
                    repair=True,
                    quiet=quiet,
                    include_block_time=include_block_time,
                    include_raw_json=include_raw_json,
                    fast_decode=fast_decode,
                )
            )
            return results
        except Exception as exc:
            last_error = exc
            if not quiet:
                print(
                    f"[orderfilled-worker] window {from_block}-{to_block} "
                    f"attempt {attempt}/{retry_attempts} failed: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
            time.sleep(min(30, 2**attempt))

    if to_block > from_block and (to_block - from_block + 1) > max(1, min_batch_blocks):
        mid = (from_block + to_block) // 2
        if not quiet:
            print(
                f"[orderfilled-worker] splitting hot window {from_block}-{to_block} -> "
                f"{from_block}-{mid}, {mid + 1}-{to_block}",
                file=sys.stderr,
                flush=True,
            )
        results.extend(
            repair_window_with_retry(
                conn=conn,
                w3=w3,
                from_block=from_block,
                to_block=mid,
                retry_attempts=retry_attempts,
                min_batch_blocks=min_batch_blocks,
                quiet=quiet,
                include_block_time=include_block_time,
                include_raw_json=include_raw_json,
                fast_decode=fast_decode,
            )
        )
        results.extend(
            repair_window_with_retry(
                conn=conn,
                w3=w3,
                from_block=mid + 1,
                to_block=to_block,
                retry_attempts=retry_attempts,
                min_batch_blocks=min_batch_blocks,
                quiet=quiet,
                include_block_time=include_block_time,
                include_raw_json=include_raw_json,
                fast_decode=fast_decode,
            )
        )
        return results

    upsert_orderfilled_sync_window(
        conn,
        from_block=from_block,
        to_block=to_block,
        chain_log_count=0,
        db_log_count=0,
        repaired_count=0,
        status="error",
        last_error=str(last_error),
    )
    return [
        {
            "from_block": from_block,
            "to_block": to_block,
            "chain_log_count": 0,
            "db_log_count_after": 0,
            "repaired_count": 0,
            "missing_after": 0,
            "status": "error",
            "last_error": str(last_error),
        }
    ]


def repair_window_task(
    *,
    rpc_url: str,
    from_block: int,
    to_block: int,
    retry_attempts: int,
    min_batch_blocks: int,
    quiet: bool,
    include_block_time: bool,
    include_raw_json: bool,
    fast_decode: bool,
) -> list[dict[str, Any]]:
    """Repair one top-level window with an isolated RPC and DB connection.

    This is used by the parallel raw-only worker path. Connections are not
    shared across threads because both Web3 providers and DB cursors are
    stateful under retry/timeout pressure.
    """

    w3 = build_web3(rpc_url)
    conn = get_connection()
    ensure_orderfilled_raw_schema(conn)
    try:
        return repair_window_with_retry(
            conn=conn,
            w3=w3,
            from_block=from_block,
            to_block=to_block,
            retry_attempts=retry_attempts,
            min_batch_blocks=min_batch_blocks,
            quiet=quiet,
            include_block_time=include_block_time,
            include_raw_json=include_raw_json,
            fast_decode=fast_decode,
        )
    finally:
        conn.close()


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    rpc_url = args.rpc or get_rpc_url()
    w3 = build_web3(rpc_url)
    init_schema()
    conn = get_connection()
    ensure_orderfilled_raw_schema(conn)
    try:
        if args.continue_sync:
            last_complete = get_last_complete_to_block(conn)
            from_block = int(args.from_block) if args.from_block is not None else (
                max(0, int(last_complete) + 1 - max(0, int(args.overlap_blocks)))
                if last_complete is not None
                else max(0, latest_safe_block(w3, args.confirmations) - int(args.bootstrap_lookback_blocks))
            )
        elif args.from_block is not None:
            from_block = int(args.from_block)
        else:
            raise SystemExit("Pass --from-block, or use --continue-sync with --bootstrap-lookback-blocks.")

        to_block = int(args.to_block) if args.to_block is not None else latest_safe_block(w3, args.confirmations)
        if from_block > to_block:
            return {"status": "noop", "from_block": from_block, "to_block": to_block, "reason": "already_synced"}

        if not args.quiet:
            print(f"[orderfilled-worker] DB={describe_db_target()}", file=sys.stderr)
            print(
                f"[orderfilled-worker] repair blocks={from_block}-{to_block} "
                f"batch={args.batch} rebuild_cashflows={args.rebuild_cashflows} "
                f"include_block_time={not args.skip_block_time} "
                f"include_raw_json={not args.skip_raw_json} "
                f"fast_decode={not args.web3_abi_decode}",
                file=sys.stderr,
            )

        windows: list[dict[str, Any]] = []
        cashflow_summaries: list[dict[str, Any]] = []
        block_windows = list(iter_block_windows(from_block, to_block, max(1, int(args.batch))))
        parallel_workers = max(1, int(args.parallel_workers))

        if parallel_workers > 1 and args.rebuild_cashflows:
            print(
                "[orderfilled-worker] --parallel-workers is ignored when --rebuild-cashflows is enabled; "
                "cashflow rebuild is intentionally sequential.",
                file=sys.stderr,
                flush=True,
            )
            parallel_workers = 1

        if parallel_workers > 1:
            if not args.quiet:
                print(
                    f"[orderfilled-worker] parallel raw repair windows={len(block_windows)} "
                    f"workers={parallel_workers}",
                    file=sys.stderr,
                    flush=True,
                )
            completed = 0
            with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
                future_to_window = {
                    executor.submit(
                        repair_window_task,
                        rpc_url=rpc_url,
                        from_block=start,
                        to_block=end,
                        retry_attempts=int(args.retry_attempts),
                        min_batch_blocks=int(args.min_batch),
                        quiet=bool(args.quiet),
                        include_block_time=not args.skip_block_time,
                        include_raw_json=not args.skip_raw_json,
                        fast_decode=not args.web3_abi_decode,
                    ): (start, end)
                    for start, end in block_windows
                }
                for future in as_completed(future_to_window):
                    start, end = future_to_window[future]
                    try:
                        repaired_windows = future.result()
                    except Exception as exc:
                        repaired_windows = [
                            {
                                "from_block": start,
                                "to_block": end,
                                "chain_log_count": 0,
                                "db_log_count_after": 0,
                                "repaired_count": 0,
                                "missing_after": 0,
                                "status": "error",
                                "last_error": str(exc),
                            }
                        ]
                    windows.extend(repaired_windows)
                    completed += 1
                    if not args.quiet:
                        print(
                            {
                                "parallel_completed_windows": completed,
                                "parallel_total_windows": len(block_windows),
                                "last_window": f"{start}-{end}",
                                "last_statuses": [row.get("status") for row in repaired_windows],
                                "last_repaired": sum(int(row.get("repaired_count") or 0) for row in repaired_windows),
                                "last_missing_after": sum(int(row.get("missing_after") or 0) for row in repaired_windows),
                            },
                            file=sys.stderr,
                            flush=True,
                        )
        else:
            for start, end in block_windows:
                repaired_windows = (
                    repair_window_with_retry(
                        conn=conn,
                        w3=w3,
                        from_block=start,
                        to_block=end,
                    retry_attempts=int(args.retry_attempts),
                    min_batch_blocks=int(args.min_batch),
                    quiet=bool(args.quiet),
                    include_block_time=not args.skip_block_time,
                    include_raw_json=not args.skip_raw_json,
                    fast_decode=not args.web3_abi_decode,
                )
            )
                windows.extend(repaired_windows)
                if args.rebuild_cashflows:
                    cashflow_summaries.append(
                        build_cashflows(
                            from_block=start,
                            to_block=end,
                            batch_blocks=int(args.cashflow_batch or args.batch),
                            address=None,
                            delete_existing=True,
                            quiet=bool(args.quiet),
                        )
                    )
                time.sleep(float(args.window_delay))
    finally:
        conn.close()

    return {
        "status": "done",
        "from_block": from_block,
        "to_block": to_block,
        "windows": len(windows),
        "complete_windows": sum(1 for row in windows if row.get("status") == "complete"),
        "error_windows": sum(1 for row in windows if row.get("status") == "error"),
        "total_chain_logs": sum(int(row.get("chain_log_count") or 0) for row in windows),
        "total_repaired": sum(int(row.get("repaired_count") or 0) for row in windows),
        "total_missing_after": sum(int(row.get("missing_after") or 0) for row in windows),
        "cashflow_rebuild": {
            "mode": "per_window" if args.rebuild_cashflows else "disabled",
            "windows": len(cashflow_summaries),
            "raw_orderfilled_rows": sum(int(row.get("raw_orderfilled_rows") or 0) for row in cashflow_summaries),
            "cashflow_rows_after_filter": sum(
                int(row.get("cashflow_rows_after_filter") or 0) for row in cashflow_summaries
            ),
            "inserted_or_updated_rows": sum(
                int(row.get("inserted_or_updated_rows") or 0) for row in cashflow_summaries
            ),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recoverable OrderFilled range backfill worker.")
    add_db_cli_args(parser)
    parser.add_argument("--rpc", default=None)
    parser.add_argument("--from-block", type=int)
    parser.add_argument("--to-block", type=int)
    parser.add_argument("--continue-sync", action="store_true")
    parser.add_argument("--bootstrap-lookback-blocks", type=int, default=20000)
    parser.add_argument("--overlap-blocks", type=int, default=20)
    parser.add_argument("--confirmations", type=int, default=20)
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH_BLOCKS)
    parser.add_argument("--min-batch", type=int, default=50)
    parser.add_argument("--retry-attempts", type=int, default=DEFAULT_RETRY_ATTEMPTS)
    parser.add_argument(
        "--parallel-workers",
        type=int,
        default=1,
        help="Run raw OrderFilled repair windows concurrently. Ignored with --rebuild-cashflows.",
    )
    parser.add_argument(
        "--skip-block-time",
        action="store_true",
        help="Do not fetch/store block_time during raw repair; hydrate it later from block_number.",
    )
    parser.add_argument(
        "--skip-raw-json",
        action="store_true",
        help="Do not store decoded raw_json during raw repair; all normalized OrderFilled columns are still stored.",
    )
    parser.add_argument("--web3-abi-decode", action="store_true", help="Use Web3 ABI event decoder instead of fast static decoder.")
    parser.add_argument("--window-delay", type=float, default=0.05)
    parser.add_argument("--rebuild-cashflows", action="store_true")
    parser.add_argument("--cashflow-batch", type=int)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_db_from_args(args)
    while True:
        summary = run_once(args)
        print(summary)
        if not args.watch:
            break
        time.sleep(max(1, int(args.interval_seconds)))


if __name__ == "__main__":
    main()
