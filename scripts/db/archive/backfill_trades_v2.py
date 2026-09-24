#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backfill legacy trades into compact single-table trades_v2."""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

try:
    import pymysql
except ImportError:
    pymysql = None

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from db import (  # type: ignore
    DEFAULT_DB_PATH,
    add_db_cli_args,
    configure_db_from_args,
    describe_db_target,
    get_connection,
    init_schema,
)
from db.trade_v2 import (  # type: ignore
    LEGACY_TRADES_TABLE,
    convert_trade_row_to_v2,
    ensure_trade_v2_schema,
    insert_trades_v2_batch,
    sql_identifier,
    update_trade_v2_migration_state,
)

DEFAULT_BATCH_SIZE = 20_000
DEFAULT_MIGRATION_NAME = "legacy_trades_to_v2"
DEFAULT_MAX_RETRIES = 8
DEFAULT_MIN_BATCH_SIZE = 2_500
DEFAULT_RETRY_BASE_DELAY = 1.0
DEFAULT_RETRY_MAX_DELAY = 30.0
DEFAULT_GROWTH_SUCCESS_THRESHOLD = 3


def _is_retryable_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, BrokenPipeError, OSError)):
        return True
    if pymysql is None:
        return False
    retryable_types = (
        pymysql.err.OperationalError,
        pymysql.err.InterfaceError,
        pymysql.err.InternalError,
    )
    if isinstance(exc, retryable_types):
        return True
    return False


def _format_error(exc: BaseException) -> str:
    return f"{exc.__class__.__name__}: {exc}"


def _retry_delay(attempt: int, base_delay: float, max_delay: float) -> float:
    delay = min(max_delay, base_delay * (2 ** max(0, attempt - 1)))
    jitter = random.uniform(0.0, min(1.0, delay * 0.25))
    return delay + jitter


def _close_safely(conn) -> None:
    try:
        conn.close()
    except Exception:
        pass


def _reconnect(db_path: str):
    conn = get_connection(db_path)
    ensure_trade_v2_schema(conn)
    return conn


def _safe_update_state(
    conn,
    db_path: str,
    *,
    max_retries: int,
    base_delay: float,
    max_delay: float,
    **kwargs,
):
    last_error: BaseException | None = None
    working_conn = conn
    for attempt in range(1, max_retries + 1):
        try:
            if hasattr(working_conn, "ping"):
                working_conn.ping(reconnect=True)
            update_trade_v2_migration_state(working_conn, **kwargs)
            return working_conn
        except Exception as exc:
            last_error = exc
            _close_safely(working_conn)
            if attempt >= max_retries or not _is_retryable_error(exc):
                break
            time.sleep(_retry_delay(attempt, base_delay, max_delay))
            working_conn = _reconnect(db_path)
    if last_error is not None:
        raise last_error
    return working_conn


def _fetch_resume_start(conn, migration_name: str) -> int | None:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT MAX(end_block) AS max_completed_end
        FROM trade_v2_migration_state
        WHERE migration_name = %s
          AND status = 'completed'
        """,
        (migration_name,),
    )
    row = cursor.fetchone()
    max_completed_end = int((row["max_completed_end"] if row else 0) or 0)
    if max_completed_end <= 0:
        return None
    return max_completed_end + 1


def _fetch_id_bounds(conn) -> Dict[str, int]:
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT COALESCE(MIN(id), 0) AS min_id, COALESCE(MAX(id), 0) AS max_id
        FROM {sql_identifier(LEGACY_TRADES_TABLE)}
        """
    )
    row = cursor.fetchone()
    return {
        "min_id": int(row["min_id"] or 0),
        "max_id": int(row["max_id"] or 0),
    }


def _fetch_legacy_batch(conn, start_id: int, end_id: int) -> List[Dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT
            id,
            tx_hash,
            log_index,
            market_id,
            maker,
            taker,
            price,
            size,
            side,
            outcome,
            token_id,
            block_number,
            timestamp,
            order_hash,
            maker_asset_id,
            taker_asset_id,
            maker_amount,
            taker_amount,
            fee,
            contract,
            created_at
        FROM {sql_identifier(LEGACY_TRADES_TABLE)}
        WHERE id >= ? AND id <= ?
        ORDER BY id ASC
        """,
        (start_id, end_id),
    )
    rows = cursor.fetchall()
    return [row.as_dict() if hasattr(row, "as_dict") else dict(row) for row in rows]


def run_backfill(
    *,
    db_path: str,
    start_id: int | None,
    end_id: int | None,
    batch_size: int,
    migration_name: str,
    max_retries: int,
    min_batch_size: int,
    retry_base_delay: float,
    retry_max_delay: float,
    resume: bool,
) -> None:
    init_schema(db_path=db_path)
    conn = _reconnect(db_path)
    try:
        bounds = _fetch_id_bounds(conn)
        resume_start = _fetch_resume_start(conn, migration_name) if resume else None
        requested_start = start_id or bounds["min_id"]
        if resume_start is not None:
            requested_start = max(requested_start, resume_start)
        current = max(requested_start, bounds["min_id"])
        initial_current = current
        final_id = min(end_id or bounds["max_id"], bounds["max_id"])
        if current <= 0 or final_id <= 0 or current > final_id:
            print("No legacy trades found for backfill.", file=sys.stderr)
            return

        print(
            f"Backfilling trades_v2 ids {current}-{final_id} batch_size={batch_size} target={describe_db_target()}",
            file=sys.stderr,
        )
        total_read = 0
        total_written = 0
        configured_batch_size = max(1, int(batch_size))
        current_batch_size = configured_batch_size
        post_failure_successes = 0

        while current <= final_id:
            batch_end = min(current + current_batch_size - 1, final_id)
            conn = _safe_update_state(
                conn,
                db_path,
                max_retries=max_retries,
                base_delay=retry_base_delay,
                max_delay=retry_max_delay,
                migration_name=migration_name,
                start_block=current,
                end_block=batch_end,
                rows_read=0,
                rows_written=0,
                rows_validated=0,
                status="running",
                last_error=None,
            )

            batch_completed = False
            last_error: BaseException | None = None
            for attempt in range(1, max_retries + 1):
                try:
                    if hasattr(conn, "ping"):
                        conn.ping(reconnect=True)
                    rows = _fetch_legacy_batch(conn, current, batch_end)
                    rows_read = len(rows)
                    if not rows:
                        conn = _safe_update_state(
                            conn,
                            db_path,
                            max_retries=max_retries,
                            base_delay=retry_base_delay,
                            max_delay=retry_max_delay,
                            migration_name=migration_name,
                            start_block=current,
                            end_block=batch_end,
                            rows_read=0,
                            rows_written=0,
                            rows_validated=0,
                            status="completed",
                            last_error=None,
                        )
                        current = batch_end + 1
                        batch_completed = True
                        break

                    core_rows = [convert_trade_row_to_v2(row) for row in rows]
                    rows_written = insert_trades_v2_batch(conn, core_rows)
                    total_read += rows_read
                    total_written += rows_written
                    conn = _safe_update_state(
                        conn,
                        db_path,
                        max_retries=max_retries,
                        base_delay=retry_base_delay,
                        max_delay=retry_max_delay,
                        migration_name=migration_name,
                        start_block=current,
                        end_block=batch_end,
                        rows_read=rows_read,
                        rows_written=rows_written,
                        rows_validated=0,
                        status="completed",
                        last_error=None,
                    )
                    print(
                        f"Backfilled id range {current}-{batch_end}: read={rows_read} inserted={rows_written} "
                        f"batch_size={current_batch_size} total_inserted={total_written}",
                        file=sys.stderr,
                    )
                    current = batch_end + 1
                    batch_completed = True
                    if current_batch_size < configured_batch_size:
                        post_failure_successes += 1
                        if post_failure_successes >= DEFAULT_GROWTH_SUCCESS_THRESHOLD:
                            current_batch_size = min(configured_batch_size, current_batch_size * 2)
                            post_failure_successes = 0
                            print(
                                f"Recovered batch size to {current_batch_size} after stable progress.",
                                file=sys.stderr,
                            )
                    else:
                        post_failure_successes = 0
                    break
                except Exception as exc:
                    last_error = exc
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    if attempt >= max_retries or not _is_retryable_error(exc):
                        break
                    delay = _retry_delay(attempt, retry_base_delay, retry_max_delay)
                    print(
                        f"Transient batch failure on {current}-{batch_end} attempt={attempt}/{max_retries}: "
                        f"{_format_error(exc)}; retrying in {delay:.1f}s",
                        file=sys.stderr,
                    )
                    _close_safely(conn)
                    time.sleep(delay)
                    conn = _reconnect(db_path)

            if batch_completed:
                continue

            error_text = _format_error(last_error or RuntimeError("unknown batch failure"))
            try:
                conn = _safe_update_state(
                    conn,
                    db_path,
                    max_retries=max_retries,
                    base_delay=retry_base_delay,
                    max_delay=retry_max_delay,
                    migration_name=migration_name,
                    start_block=current,
                    end_block=batch_end,
                    rows_read=0,
                    rows_written=0,
                    rows_validated=0,
                    status="failed",
                    last_error=error_text,
                )
            except Exception:
                pass

            if current_batch_size > min_batch_size:
                next_batch_size = max(min_batch_size, current_batch_size // 2)
                if next_batch_size != current_batch_size:
                    print(
                        f"Batch {current}-{batch_end} failed after retries; reducing batch size "
                        f"from {current_batch_size} to {next_batch_size} and retrying. error={error_text}",
                        file=sys.stderr,
                    )
                    current_batch_size = next_batch_size
                    post_failure_successes = 0
                    _close_safely(conn)
                    conn = _reconnect(db_path)
                    continue

            raise RuntimeError(
                f"Backfill stopped at id range {current}-{batch_end} after {max_retries} retries: {error_text}"
            ) from last_error

        conn = _safe_update_state(
            conn,
            db_path,
            max_retries=max_retries,
            base_delay=retry_base_delay,
            max_delay=retry_max_delay,
            migration_name=migration_name,
            start_block=initial_current,
            end_block=final_id,
            rows_read=total_read,
            rows_written=total_written,
            rows_validated=0,
            status="completed",
            last_error=None,
        )
        print(
            f"Backfill completed: rows_read={total_read} rows_inserted={total_written}",
            file=sys.stderr,
        )
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill legacy trades into compact trades_v2")
    add_db_cli_args(parser)
    parser.add_argument("--start-id", type=int, default=None, help="Inclusive legacy trades.id lower bound")
    parser.add_argument("--end-id", type=int, default=None, help="Inclusive legacy trades.id upper bound")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Rows per batch")
    parser.add_argument("--migration-name", default=DEFAULT_MIGRATION_NAME, help="Migration state name")
    parser.add_argument("--resume", action="store_true", help="Resume from latest completed migration state")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help="Retry attempts per batch")
    parser.add_argument(
        "--min-batch-size",
        type=int,
        default=DEFAULT_MIN_BATCH_SIZE,
        help="Smallest batch size after automatic backoff",
    )
    parser.add_argument(
        "--retry-base-delay",
        type=float,
        default=DEFAULT_RETRY_BASE_DELAY,
        help="Base seconds for exponential retry backoff",
    )
    parser.add_argument(
        "--retry-max-delay",
        type=float,
        default=DEFAULT_RETRY_MAX_DELAY,
        help="Maximum seconds for retry backoff",
    )
    args = parser.parse_args()
    configure_db_from_args(args)
    run_backfill(
        db_path=getattr(args, "sqlite_path", DEFAULT_DB_PATH),
        start_id=args.start_id,
        end_id=args.end_id,
        batch_size=max(1, int(args.batch_size)),
        migration_name=args.migration_name,
        max_retries=max(1, int(args.max_retries)),
        min_batch_size=max(1, int(args.min_batch_size)),
        retry_base_delay=max(0.1, float(args.retry_base_delay)),
        retry_max_delay=max(0.1, float(args.retry_max_delay)),
        resume=bool(args.resume),
    )


if __name__ == "__main__":
    main()
