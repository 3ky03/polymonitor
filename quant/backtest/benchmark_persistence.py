"""Persistence helpers for multi-market backtest benchmarks."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
import json
from typing import Any


def create_benchmark_run(
    conn: Any,
    *,
    universe_type: str,
    universe_name: str,
    market_count: int,
    strategy_name: str,
    parameters: dict[str, Any],
    profiles: dict[str, Any],
    status: str = "running",
) -> int:
    normalized_status = str(status or "running").strip().lower()
    if normalized_status not in {"queued", "running"}:
        raise ValueError(f"unsupported benchmark status: {status!r}")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO quant.quant_backtest_benchmark_runs (
                status, universe_type, universe_name, market_count,
                strategy_name, parameters, profiles, started_at
            )
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, CASE WHEN %s = 'running' THEN now() ELSE NULL END)
            RETURNING benchmark_id
            """,
            (
                normalized_status,
                universe_type,
                universe_name,
                int(market_count),
                strategy_name,
                _json_dumps(parameters),
                _json_dumps(profiles),
                normalized_status,
            ),
        )
        row = cur.fetchone()
        return int(row["benchmark_id"] if isinstance(row, dict) else row[0])


def mark_benchmark_run_started(conn: Any, *, benchmark_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE quant.quant_backtest_benchmark_runs
            SET status = 'running',
                started_at = COALESCE(started_at, now()),
                error = NULL
            WHERE benchmark_id = %s
              AND status IN ('queued', 'running')
            """,
            (int(benchmark_id),),
        )


def claim_next_queued_benchmark_run(conn: Any) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH next_run AS (
                SELECT benchmark_id
                FROM quant.quant_backtest_benchmark_runs
                WHERE status = 'queued'
                ORDER BY created_at ASC, benchmark_id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE quant.quant_backtest_benchmark_runs b
            SET status = 'running',
                started_at = COALESCE(b.started_at, now()),
                error = NULL
            FROM next_run
            WHERE b.benchmark_id = next_run.benchmark_id
            RETURNING b.*
            """
        )
        row = cur.fetchone()
        return dict(row) if row else None


def complete_benchmark_run(
    conn: Any,
    *,
    benchmark_id: int,
    summary: dict[str, Any],
    rows: list[Any],
    artifacts: dict[str, Any] | None = None,
    data_version: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM quant.quant_backtest_benchmark_rows WHERE benchmark_id = %s", (int(benchmark_id),))
        cur.execute("DELETE FROM quant.quant_backtest_benchmark_artifacts WHERE benchmark_id = %s", (int(benchmark_id),))
        cur.executemany(
            """
            INSERT INTO quant.quant_backtest_benchmark_rows (
                benchmark_id, row_index, market_id, market_slug, title, event_time,
                outcome, signal_time, fast_status, accurate_status, fast_pnl,
                accurate_pnl, pnl_diff, fast_fill_block, accurate_fill_block,
                data_quality, payload
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            [
                (
                    int(benchmark_id),
                    index,
                    _get(row, "market_id"),
                    _get(row, "market_slug"),
                    _get(row, "title"),
                    _parse_timestamp(_get(row, "event_time")),
                    _get(row, "buy_outcome_label") or _get(row, "outcome"),
                    _parse_timestamp(_get(row, "signal_time")),
                    _get(row, "fast_status"),
                    _get(row, "accurate_status"),
                    Decimal(str(_get(row, "fast_pnl") or "0")),
                    Decimal(str(_get(row, "accurate_pnl") or "0")),
                    Decimal(str(_get(row, "pnl_diff") or "0")),
                    int(_get(row, "fast_fill_block") or 0),
                    int(_get(row, "accurate_fill_block") or 0),
                    _get(row, "data_quality") or "unknown",
                    _json_dumps(_as_plain(row)),
                )
                for index, row in enumerate(rows, start=1)
            ],
        )
        artifact_items = artifacts or {}
        cur.executemany(
            """
            INSERT INTO quant.quant_backtest_benchmark_artifacts (
                benchmark_id, artifact_key, artifact_kind, payload
            )
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            [
                (int(benchmark_id), key, _artifact_kind(value), _json_dumps(value))
                for key, value in artifact_items.items()
            ],
        )
        cur.execute(
            """
            UPDATE quant.quant_backtest_benchmark_runs
            SET status = 'completed',
                summary = %s::jsonb,
                data_version = %s,
                finished_at = now(),
                error = NULL
            WHERE benchmark_id = %s
            """,
            (_json_dumps(summary), data_version, int(benchmark_id)),
        )


def fail_benchmark_run(conn: Any, *, benchmark_id: int, error: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE quant.quant_backtest_benchmark_runs
            SET status = 'failed', error = %s, finished_at = now()
            WHERE benchmark_id = %s
            """,
            (str(error), int(benchmark_id)),
        )


def fail_stale_running_benchmark_runs(conn: Any, *, stale_after_seconds: int = 3600) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE quant.quant_backtest_benchmark_runs
            SET status = 'failed',
                error = %s,
                finished_at = now()
            WHERE status = 'running'
              AND started_at IS NOT NULL
              AND started_at < now() - (%s::text || ' seconds')::interval
            """,
            (f"stale running benchmark exceeded {int(stale_after_seconds)} seconds", int(stale_after_seconds)),
        )
        return int(cur.rowcount or 0)


def cancel_queued_benchmark_run(conn: Any, *, benchmark_id: int) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE quant.quant_backtest_benchmark_runs
            SET status = 'canceled',
                error = 'canceled by user',
                finished_at = now()
            WHERE benchmark_id = %s
              AND status = 'queued'
            RETURNING *
            """,
            (int(benchmark_id),),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def retry_benchmark_run(conn: Any, *, benchmark_id: int) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE quant.quant_backtest_benchmark_runs
            SET status = 'queued',
                summary = '{}'::jsonb,
                data_version = NULL,
                error = NULL,
                started_at = NULL,
                finished_at = NULL
            WHERE benchmark_id = %s
              AND status IN ('failed', 'canceled')
            RETURNING *
            """,
            (int(benchmark_id),),
        )
        row = cur.fetchone()
        if row:
            cur.execute("DELETE FROM quant.quant_backtest_benchmark_rows WHERE benchmark_id = %s", (int(benchmark_id),))
            cur.execute("DELETE FROM quant.quant_backtest_benchmark_artifacts WHERE benchmark_id = %s", (int(benchmark_id),))
        return dict(row) if row else None


def list_benchmark_runs(conn: Any, *, limit: int = 25) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM quant.quant_backtest_benchmark_runs
            ORDER BY created_at DESC, benchmark_id DESC
            LIMIT %s
            """,
            (int(limit),),
        )
        return [dict(row) for row in cur.fetchall()]


def get_benchmark_queue_status(conn: Any) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, count(*) AS count
            FROM quant.quant_backtest_benchmark_runs
            WHERE created_at >= now() - interval '7 days'
            GROUP BY status
            """
        )
        counts = {str(row["status"]): int(row["count"]) for row in cur.fetchall()}
        cur.execute(
            """
            SELECT
                min(created_at) FILTER (WHERE status = 'queued') AS oldest_queued_at,
                max(finished_at) FILTER (WHERE status = 'completed') AS last_completed_at,
                max(finished_at) FILTER (WHERE status = 'failed') AS last_failed_at
            FROM quant.quant_backtest_benchmark_runs
            WHERE created_at >= now() - interval '7 days'
            """
        )
        timing = dict(cur.fetchone() or {})
        try:
            cur.execute(
                """
                SELECT *
                FROM quant.quant_backtest_benchmark_worker_heartbeats
                ORDER BY heartbeat_at DESC
                LIMIT 8
                """
            )
            workers = [dict(row) for row in cur.fetchall()]
        except Exception:
            conn.rollback()
            workers = []
    oldest = timing.get("oldest_queued_at")
    return {
        "counts": counts,
        "queued_count": counts.get("queued", 0),
        "running_count": counts.get("running", 0),
        "completed_count": counts.get("completed", 0),
        "failed_count": counts.get("failed", 0),
        "canceled_count": counts.get("canceled", 0),
        "oldest_queued_at": oldest,
        "oldest_queued_age_seconds": _age_seconds(oldest),
        "last_completed_at": timing.get("last_completed_at"),
        "last_failed_at": timing.get("last_failed_at"),
        "workers": workers,
        "worker_online": any(_age_seconds(row.get("heartbeat_at")) is not None and _age_seconds(row.get("heartbeat_at")) <= 30 for row in workers),
    }


def update_benchmark_worker_heartbeat(
    conn: Any,
    *,
    worker_id: str,
    status: str,
    current_benchmark_id: int | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO quant.quant_backtest_benchmark_worker_heartbeats (
                worker_id, status, current_benchmark_id, heartbeat_at, started_at, meta
            )
            VALUES (%s, %s, %s, now(), now(), %s::jsonb)
            ON CONFLICT (worker_id) DO UPDATE
            SET status = EXCLUDED.status,
                current_benchmark_id = EXCLUDED.current_benchmark_id,
                heartbeat_at = now(),
                meta = EXCLUDED.meta
            """,
            (str(worker_id), str(status), current_benchmark_id, _json_dumps(meta or {})),
        )


def get_benchmark_run(conn: Any, *, benchmark_id: int) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM quant.quant_backtest_benchmark_runs
            WHERE benchmark_id = %s
            """,
            (int(benchmark_id),),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_benchmark_rows(conn: Any, *, benchmark_id: int, limit: int = 10000) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM quant.quant_backtest_benchmark_rows
            WHERE benchmark_id = %s
            ORDER BY row_index ASC
            LIMIT %s
            """,
            (int(benchmark_id), int(limit)),
        )
        return [dict(row) for row in cur.fetchall()]


def get_benchmark_artifacts(conn: Any, *, benchmark_id: int) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM quant.quant_backtest_benchmark_artifacts
            WHERE benchmark_id = %s
            ORDER BY artifact_key ASC
            """,
            (int(benchmark_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def _get(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _as_plain(value: Any) -> Any:
    if is_dataclass(value):
        return _as_plain(asdict(value))
    if isinstance(value, dict):
        return {str(key): _as_plain(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_as_plain(item) for item in value]
    if isinstance(value, tuple):
        return [_as_plain(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(_as_plain(value), ensure_ascii=True, sort_keys=True, default=str)


def _artifact_kind(value: Any) -> str:
    if isinstance(value, list):
        return "rows"
    if isinstance(value, dict):
        return "json"
    return "value"


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_seconds(value: Any) -> float | None:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return None
    return max(0.0, (datetime.now(tz=parsed.tzinfo) - parsed).total_seconds())
