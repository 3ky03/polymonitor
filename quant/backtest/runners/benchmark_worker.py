"""DB-backed worker for queued multi-market benchmark runs."""

from __future__ import annotations

import argparse
from decimal import Decimal
import logging
import os
import signal
import socket
import time
from typing import Any

from ..benchmark_persistence import (
    claim_next_queued_benchmark_run,
    fail_benchmark_run,
    fail_stale_running_benchmark_runs,
    update_benchmark_worker_heartbeat,
)
from ...core.db import PostgresSettings, postgres_connection
from ...core.schema import create_schema
from .benchmark import run_orderfilled_fast_accurate_benchmark
from .selectors import UniverseSpec


LOGGER = logging.getLogger(__name__)
SHOULD_STOP = False


def _as_decimal(value: Any, default: str) -> Decimal:
    if value in (None, ""):
        return Decimal(default)
    return Decimal(str(value))


def _as_optional_decimal(value: Any) -> Decimal | None:
    if value in (None, "", "null"):
        return None
    return Decimal(str(value))


def _as_optional_int(value: Any) -> int | None:
    if value in (None, "", "null"):
        return None
    return int(value)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _universe_from_parameters(parameters: dict[str, Any], *, fallback_name: str, fallback_limit: int) -> UniverseSpec:
    universe = parameters.get("universe") if isinstance(parameters.get("universe"), dict) else {}
    return UniverseSpec(
        universe_name=str(universe.get("universe_name") or universe.get("universeName") or fallback_name or "nba_2024_25_moneyline"),
        universe_type=str(universe.get("universe_type") or universe.get("universeType") or "preset"),  # type: ignore[arg-type]
        limit=max(1, min(int(universe.get("limit") or parameters.get("limit") or fallback_limit or 50), 500)),
        market_ids=tuple(int(value) for value in universe.get("market_ids", universe.get("marketIds", [])) if str(value).strip()),
        market_slugs=tuple(str(value).strip() for value in universe.get("market_slugs", universe.get("marketSlugs", [])) if str(value).strip()),
        event_slug=str(universe.get("event_slug") or universe.get("eventSlug") or "") or None,
        category=str(universe.get("category") or "") or None,
        start_date=str(universe.get("start_date") or universe.get("startDate") or "") or None,
        end_date=str(universe.get("end_date") or universe.get("endDate") or "") or None,
        require_resolved=_as_bool(universe.get("require_resolved", universe.get("requireResolved")), True),
        require_orderfilled_rows=_as_bool(universe.get("require_orderfilled_rows", universe.get("requireOrderfilledRows")), True),
        meta={"source": "benchmark_worker", "raw": universe},
    )


def run_claimed_benchmark(row: dict[str, Any]) -> int:
    benchmark_id = int(row["benchmark_id"])
    parameters = row.get("parameters") if isinstance(row.get("parameters"), dict) else {}
    profiles = row.get("profiles") if isinstance(row.get("profiles"), dict) else {}
    universe_spec = _universe_from_parameters(
        parameters,
        fallback_name=str(row.get("universe_name") or "nba_2024_25_moneyline"),
        fallback_limit=int(row.get("market_count") or 50),
    )
    profile_keys = tuple(str(item) for item in (profiles.get("requested") or ("fast:optimistic", "fast:realistic", "fast:stress", "accurate:realistic")))
    with postgres_connection(PostgresSettings(), readonly=False) as conn:
        run_orderfilled_fast_accurate_benchmark(
            universe_spec=universe_spec,
            persist_conn=conn,
            benchmark_id=benchmark_id,
            force_block_replay_backfill=False,
            min_probability=_as_decimal(parameters.get("min_probability"), "0.60"),
            max_probability=_as_decimal(parameters.get("max_probability"), "0.80"),
            stake=_as_decimal(parameters.get("stake"), "10"),
            initial_capital=_as_decimal(parameters.get("initial_capital"), "1000"),
            max_daily_cost=_as_optional_decimal(parameters.get("max_daily_cost")),
            max_concurrent_positions=_as_optional_int(parameters.get("max_concurrent_positions")),
            max_daily_trades=_as_optional_int(parameters.get("max_daily_trades")),
            profile_keys=profile_keys,
        )
    return benchmark_id


def run_worker(*, once: bool = False, poll_seconds: float = 2.0, init_schema: bool = True, stale_running_seconds: int = 3600) -> int:
    processed = 0
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    if init_schema:
        with postgres_connection(PostgresSettings(), readonly=False) as conn:
            create_schema(conn)
    while not SHOULD_STOP:
        claimed: dict[str, Any] | None = None
        try:
            with postgres_connection(PostgresSettings(), readonly=False) as conn:
                stale_count = fail_stale_running_benchmark_runs(conn, stale_after_seconds=stale_running_seconds)
                if stale_count:
                    LOGGER.warning("marked stale running benchmarks failed count=%s threshold_seconds=%s", stale_count, stale_running_seconds)
                update_benchmark_worker_heartbeat(conn, worker_id=worker_id, status="idle", meta={"processed": processed})
                claimed = claim_next_queued_benchmark_run(conn)
                conn.commit()
            if not claimed:
                if once:
                    return processed
                time.sleep(max(0.1, poll_seconds))
                continue
            benchmark_id = int(claimed["benchmark_id"])
            LOGGER.info("claimed benchmark_id=%s universe=%s", benchmark_id, claimed.get("universe_name"))
            with postgres_connection(PostgresSettings(), readonly=False) as conn:
                update_benchmark_worker_heartbeat(conn, worker_id=worker_id, status="running", current_benchmark_id=benchmark_id, meta={"processed": processed})
                conn.commit()
            run_claimed_benchmark(claimed)
            LOGGER.info("completed benchmark_id=%s", benchmark_id)
            processed += 1
            with postgres_connection(PostgresSettings(), readonly=False) as conn:
                update_benchmark_worker_heartbeat(conn, worker_id=worker_id, status="idle", current_benchmark_id=None, meta={"processed": processed, "last_completed_benchmark_id": benchmark_id})
                conn.commit()
        except Exception as exc:
            LOGGER.exception("benchmark worker job failed")
            if claimed and claimed.get("benchmark_id"):
                try:
                    with postgres_connection(PostgresSettings(), readonly=False) as conn:
                        fail_benchmark_run(conn, benchmark_id=int(claimed["benchmark_id"]), error=str(exc))
                        update_benchmark_worker_heartbeat(
                            conn,
                            worker_id=worker_id,
                            status="failed",
                            current_benchmark_id=int(claimed["benchmark_id"]),
                            meta={"processed": processed, "error": str(exc)},
                        )
                        conn.commit()
                except Exception:
                    LOGGER.exception("benchmark worker failed-state write failed")
            if once:
                return processed
    return processed


def _handle_stop(signum, frame) -> None:  # pragma: no cover - signal glue
    del signum, frame
    global SHOULD_STOP
    SHOULD_STOP = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Claim and run at most one queued benchmark, then exit.")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--stale-running-seconds", type=int, default=3600)
    parser.add_argument("--skip-init-schema", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    run_worker(
        once=bool(args.once),
        poll_seconds=float(args.poll_seconds),
        init_schema=not bool(args.skip_init_schema),
        stale_running_seconds=int(args.stale_running_seconds),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
