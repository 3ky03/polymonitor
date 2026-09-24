"""Backtest job runner for queued quant runs."""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from ..backtest.backtest_engine import (
    claim_backtest_run,
    execute_backtest_run,
    get_backtest_run_for_update_free,
    list_queued_backtest_run_ids,
    mark_run_failed,
)
from ..core.db import PostgresSettings, postgres_connection
from ..core.schema import create_schema


def run_backtest_job(run_id: int, *, settings: PostgresSettings | None = None) -> dict[str, Any] | None:
    """Claim and execute one queued run.

    Returns the final run row. If another worker already claimed or finished the
    run, the current row is returned without duplicate execution.
    """

    with postgres_connection(settings or PostgresSettings(), readonly=False) as conn:
        create_schema(conn)
        claimed = claim_backtest_run(conn, run_id)
        conn.commit()
        if not claimed:
            return get_backtest_run_for_update_free(conn, run_id)
        try:
            execute_backtest_run(conn, run_id)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            mark_run_failed(conn, run_id, str(exc))
            conn.commit()
        return get_backtest_run_for_update_free(conn, run_id)


def run_queued_backtests_once(*, settings: PostgresSettings | None = None, limit: int = 5) -> int:
    with postgres_connection(settings or PostgresSettings(), readonly=False) as conn:
        create_schema(conn)
        run_ids = list_queued_backtest_run_ids(conn, limit=limit)
    completed = 0
    for run_id in run_ids:
        row = run_backtest_job(run_id, settings=settings)
        if row and row.get("status") in {"succeeded", "failed"}:
            completed += 1
    return completed


def run_daemon(
    *,
    settings: PostgresSettings | None = None,
    limit: int = 5,
    sleep_seconds: float = 2.0,
    error_sleep_seconds: float = 60.0,
) -> None:
    while True:
        try:
            completed = run_queued_backtests_once(settings=settings, limit=limit)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - daemon must not restart storm on transient DB/tunnel failures.
            print(
                f"[quant-backtest-runner] loop error: {type(exc).__name__}: {exc}; "
                f"sleeping {max(error_sleep_seconds, 1.0):.1f}s",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(max(error_sleep_seconds, 1.0))
            continue
        if completed == 0:
            time.sleep(sleep_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run queued quant backtests.")
    parser.add_argument("--run-id", type=int, help="Execute one queued backtest run id.")
    parser.add_argument("--once", action="store_true", help="Process currently queued runs once and exit.")
    parser.add_argument("--daemon", action="store_true", help="Continuously poll queued runs.")
    parser.add_argument("--limit", type=int, default=5, help="Max queued runs to pick per pass.")
    parser.add_argument("--sleep-seconds", type=float, default=2.0, help="Daemon idle sleep interval.")
    parser.add_argument("--error-sleep-seconds", type=float, default=60.0, help="Daemon sleep interval after a failed loop.")
    args = parser.parse_args()

    if args.run_id:
        row = run_backtest_job(args.run_id)
        print(row)
        return
    if args.daemon:
        run_daemon(limit=args.limit, sleep_seconds=args.sleep_seconds, error_sleep_seconds=args.error_sleep_seconds)
        return
    completed = run_queued_backtests_once(limit=args.limit)
    print({"completed": completed})


if __name__ == "__main__":
    main()
