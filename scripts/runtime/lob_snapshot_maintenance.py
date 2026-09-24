#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_scripts_root = Path(__file__).resolve().parents[1]
_project_root = _scripts_root.parent
for _path in (str(_project_root), str(_scripts_root)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from quant.core.db import PostgresSettings, env_int, postgres_connection
from quant.core.schema import create_schema


DEFAULT_INTERVAL_SECONDS = 21600
DEFAULT_ROLLUP_BEFORE_DAYS = 7
DEFAULT_HOT_RETENTION_DAYS = 14
DEFAULT_WARM_RETENTION_DAYS = 14
DEFAULT_COLD_RETENTION_DAYS = 7
DEFAULT_BATCH_SIZE = 5000
DEFAULT_ROLLUP_WINDOW_MINUTES = 60
DEFAULT_ROLLUP_MAX_WINDOWS = 336
DEFAULT_DELETE_MAX_BATCHES = 20
DEFAULT_STATUS_PATH = Path("/tmp/polydata/lob-maintenance-status.json")


@dataclass(frozen=True)
class MaintenancePolicy:
    rollup_before_days: int = DEFAULT_ROLLUP_BEFORE_DAYS
    hot_retention_days: int = DEFAULT_HOT_RETENTION_DAYS
    warm_retention_days: int = DEFAULT_WARM_RETENTION_DAYS
    cold_retention_days: int = DEFAULT_COLD_RETENTION_DAYS
    batch_size: int = DEFAULT_BATCH_SIZE
    rollup_window_minutes: int = DEFAULT_ROLLUP_WINDOW_MINUTES
    rollup_max_windows: int = DEFAULT_ROLLUP_MAX_WINDOWS
    delete_max_batches: int = DEFAULT_DELETE_MAX_BATCHES


def cutoff_map(now: datetime, policy: MaintenancePolicy) -> dict[str, datetime]:
    now = now.astimezone(timezone.utc)
    return {
        "rollup": now - timedelta(days=max(1, int(policy.rollup_before_days))),
        "hot": now - timedelta(days=max(1, int(policy.hot_retention_days))),
        "warm": now - timedelta(days=max(1, int(policy.warm_retention_days))),
        "cold": now - timedelta(days=max(1, int(policy.cold_retention_days))),
    }


ROLLUP_SQL = """
WITH base AS (
    SELECT
        s.*,
        date_trunc('minute', s.fetched_at) AS bucket_minute,
        row_number() OVER (
            PARTITION BY s.token_id, s.side, date_trunc('minute', s.fetched_at)
            ORDER BY s.fetched_at DESC, s.snapshot_id DESC
        ) AS rn,
        count(*) OVER (
            PARTITION BY s.token_id, s.side, date_trunc('minute', s.fetched_at)
        ) AS sample_count,
        min(s.snapshot_id) OVER (
            PARTITION BY s.token_id, s.side, date_trunc('minute', s.fetched_at)
        ) AS first_snapshot_id,
        max(s.snapshot_id) OVER (
            PARTITION BY s.token_id, s.side, date_trunc('minute', s.fetched_at)
        ) AS last_snapshot_id,
        min(s.fetched_at) OVER (
            PARTITION BY s.token_id, s.side, date_trunc('minute', s.fetched_at)
        ) AS first_fetched_at,
        max(s.fetched_at) OVER (
            PARTITION BY s.token_id, s.side, date_trunc('minute', s.fetched_at)
        ) AS last_fetched_at
    FROM quant.clob_orderbook_snapshots s
    WHERE s.side = %s
      AND s.fetched_at >= %s
      AND s.fetched_at < %s
),
latest AS (
    SELECT * FROM base WHERE rn = 1
)
INSERT INTO quant.clob_orderbook_rollups_1m (
    token_id, side, bucket_minute,
    market_id, condition_id, market_slug, market_title,
    source, snapshot_source, storage_tier, book_status,
    first_snapshot_id, last_snapshot_id, sample_count,
    first_fetched_at, last_fetched_at,
    best_bid, best_ask, spread, mid,
    bid_depth, ask_depth, depth_total, imbalance,
    level_count_bid, level_count_ask, payload, updated_at
)
SELECT
    token_id, side, bucket_minute,
    market_id, condition_id, market_slug, market_title,
    source, snapshot_source, storage_tier, book_status,
    first_snapshot_id, last_snapshot_id, sample_count,
    first_fetched_at, last_fetched_at,
    best_bid, best_ask, spread, mid,
    bid_depth, ask_depth, depth_total, imbalance,
    level_count_bid, level_count_ask, payload, now()
FROM latest
ON CONFLICT (token_id, side, bucket_minute) DO UPDATE SET
    market_id = EXCLUDED.market_id,
    condition_id = EXCLUDED.condition_id,
    market_slug = EXCLUDED.market_slug,
    market_title = EXCLUDED.market_title,
    source = EXCLUDED.source,
    snapshot_source = EXCLUDED.snapshot_source,
    storage_tier = EXCLUDED.storage_tier,
    book_status = EXCLUDED.book_status,
    first_snapshot_id = LEAST(quant.clob_orderbook_rollups_1m.first_snapshot_id, EXCLUDED.first_snapshot_id),
    last_snapshot_id = GREATEST(quant.clob_orderbook_rollups_1m.last_snapshot_id, EXCLUDED.last_snapshot_id),
    sample_count = GREATEST(quant.clob_orderbook_rollups_1m.sample_count, EXCLUDED.sample_count),
    first_fetched_at = LEAST(quant.clob_orderbook_rollups_1m.first_fetched_at, EXCLUDED.first_fetched_at),
    last_fetched_at = GREATEST(quant.clob_orderbook_rollups_1m.last_fetched_at, EXCLUDED.last_fetched_at),
    best_bid = EXCLUDED.best_bid,
    best_ask = EXCLUDED.best_ask,
    spread = EXCLUDED.spread,
    mid = EXCLUDED.mid,
    bid_depth = EXCLUDED.bid_depth,
    ask_depth = EXCLUDED.ask_depth,
    depth_total = EXCLUDED.depth_total,
    imbalance = EXCLUDED.imbalance,
    level_count_bid = EXCLUDED.level_count_bid,
    level_count_ask = EXCLUDED.level_count_ask,
    payload = EXCLUDED.payload,
    updated_at = now()
RETURNING 1
"""


DELETE_ELIGIBLE_SQL = """
FROM quant.clob_orderbook_snapshots s
WHERE EXISTS (
    SELECT 1
    FROM quant.clob_orderbook_rollups_1m r
    WHERE r.token_id = s.token_id
      AND r.side = s.side
      AND r.bucket_minute = date_trunc('minute', s.fetched_at)
)
AND (
    (COALESCE(s.storage_tier, 'unknown') = 'cold' AND s.fetched_at < %s)
    OR (COALESCE(s.storage_tier, 'unknown') = 'warm' AND s.fetched_at < %s)
    OR (COALESCE(s.storage_tier, 'unknown') = 'hot' AND s.fetched_at < %s)
    OR (COALESCE(s.storage_tier, 'unknown') NOT IN ('hot', 'warm', 'cold') AND s.fetched_at < %s)
)
"""


def dry_run_counts(conn: Any, *, rollup_cutoff: datetime, cutoffs: dict[str, datetime]) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS groups
            FROM (
                SELECT DISTINCT token_id, side, date_trunc('minute', fetched_at) AS bucket_minute
                FROM quant.clob_orderbook_snapshots
                WHERE fetched_at < %s
            ) grouped
            """,
            (rollup_cutoff,),
        )
        rollup_groups = int((cur.fetchone() or {}).get("groups") or 0)
        cur.execute(
            "SELECT count(*) AS rows " + DELETE_ELIGIBLE_SQL,
            (cutoffs["cold"], cutoffs["warm"], cutoffs["hot"], cutoffs["warm"]),
        )
        delete_rows = int((cur.fetchone() or {}).get("rows") or 0)
    return {"rollupGroups": rollup_groups, "deleteRows": delete_rows}


def _rollup_start(conn: Any, *, rollup_cutoff: datetime) -> datetime | None:
    """Resume from the latest completed minute without rescanning all history."""

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT max(bucket_minute) AS watermark
            FROM quant.clob_orderbook_rollups_1m
            WHERE bucket_minute < %s
            """,
            (rollup_cutoff,),
        )
        watermark = (cur.fetchone() or {}).get("watermark")
        if watermark is not None:
            return watermark.astimezone(timezone.utc)
        cur.execute(
            """
            SELECT min(fetched_at) AS oldest
            FROM quant.clob_orderbook_snapshots
            WHERE fetched_at < %s
            """,
            (rollup_cutoff,),
        )
        oldest = (cur.fetchone() or {}).get("oldest")
    return oldest.astimezone(timezone.utc) if oldest is not None else None


def _snapshot_sides(
    conn: Any,
    *,
    start: datetime,
    end: datetime,
) -> list[str]:
    """Return only outcome sides that have snapshots in the current window.

    Sports and multi-outcome markets make ``side`` an open vocabulary rather
    than a small YES/NO enum. Iterating every historical side for every hour
    creates thousands of empty index probes and prevents maintenance from
    advancing its watermark. Limit the probe set to the bounded window.
    """

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT side
            FROM quant.clob_orderbook_snapshots
            WHERE side IS NOT NULL AND side <> ''
              AND fetched_at >= %s
              AND fetched_at < %s
            ORDER BY side
            """,
            (start, end),
        )
        return [str(row["side"]) for row in cur.fetchall() if row.get("side")]


def execute_rollup(
    conn: Any,
    *,
    rollup_cutoff: datetime,
    window_minutes: int = DEFAULT_ROLLUP_WINDOW_MINUTES,
    max_windows: int = DEFAULT_ROLLUP_MAX_WINDOWS,
) -> int:
    """Roll up bounded time windows using the existing ``(side, fetched_at)`` index.

    The former query applied window functions to every historical snapshot on
    every pass. Production has millions of rows, so PostgreSQL repeatedly
    exceeded ``temp_file_limit`` before retention could run. Reprocessing the
    latest completed minute keeps late samples correct while bounding each
    sort to one side and one small time window.
    """

    start = _rollup_start(conn, rollup_cutoff=rollup_cutoff)
    if start is None or start >= rollup_cutoff:
        return 0
    start = start.replace(second=0, microsecond=0)
    window = timedelta(minutes=max(1, int(window_minutes)))
    total = 0
    windows = 0
    while start < rollup_cutoff and windows < max(1, int(max_windows)):
        end = min(rollup_cutoff, start + window)
        sides = _snapshot_sides(conn, start=start, end=end)
        with conn.cursor() as cur:
            for side in sides:
                cur.execute(ROLLUP_SQL, (side, start, end))
                total += len(cur.fetchall())
        conn.commit()
        windows += 1
        start = end
    return total


def execute_delete(conn: Any, *, cutoffs: dict[str, datetime], batch_size: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH doomed AS (
                SELECT s.ctid
                """ + DELETE_ELIGIBLE_SQL + """
                ORDER BY s.fetched_at ASC
                LIMIT %s
            )
            DELETE FROM quant.clob_orderbook_snapshots s
            USING doomed
            WHERE s.ctid = doomed.ctid
            RETURNING 1
            """,
            (cutoffs["cold"], cutoffs["warm"], cutoffs["hot"], cutoffs["warm"], max(1, int(batch_size))),
        )
        return len(cur.fetchall())


def execute_delete_batches(
    conn: Any,
    *,
    cutoffs: dict[str, datetime],
    batch_size: int,
    max_batches: int = DEFAULT_DELETE_MAX_BATCHES,
) -> int:
    total = 0
    for _ in range(max(1, int(max_batches))):
        deleted = execute_delete(conn, cutoffs=cutoffs, batch_size=batch_size)
        conn.commit()
        total += deleted
        if deleted < max(1, int(batch_size)):
            break
    return total


def rollup_watermark(conn: Any) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT max(bucket_minute) AS watermark FROM quant.clob_orderbook_rollups_1m")
        value = (cur.fetchone() or {}).get("watermark")
    return value.isoformat().replace("+00:00", "Z") if value else None


def write_status(path: Path, payload: dict[str, Any]) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def run_once(*, settings: PostgresSettings | None = None, policy: MaintenancePolicy | None = None, dry_run: bool = False) -> dict[str, Any]:
    policy = policy or MaintenancePolicy()
    now = datetime.now(timezone.utc)
    cutoffs = cutoff_map(now, policy)
    with postgres_connection(settings or PostgresSettings(), readonly=dry_run) as conn:
        if not dry_run:
            create_schema(conn)
            rolled_up = execute_rollup(
                conn,
                rollup_cutoff=cutoffs["rollup"],
                window_minutes=policy.rollup_window_minutes,
                max_windows=policy.rollup_max_windows,
            )
            deleted = execute_delete_batches(
                conn,
                cutoffs=cutoffs,
                batch_size=policy.batch_size,
                max_batches=policy.delete_max_batches,
            )
            counts = {"rollupGroups": rolled_up, "deleteRows": deleted}
        else:
            counts = dry_run_counts(conn, rollup_cutoff=cutoffs["rollup"], cutoffs=cutoffs)
        watermark = rollup_watermark(conn)
    return {
        "dryRun": bool(dry_run),
        "policy": policy.__dict__,
        "rollupCutoff": cutoffs["rollup"].isoformat().replace("+00:00", "Z"),
        "retentionCutoffs": {
            "hot": cutoffs["hot"].isoformat().replace("+00:00", "Z"),
            "warm": cutoffs["warm"].isoformat().replace("+00:00", "Z"),
            "cold": cutoffs["cold"].isoformat().replace("+00:00", "Z"),
        },
        "rolledUpRows": int(counts["rollupGroups"]),
        "deletedRows": int(counts["deleteRows"]),
        "rollupWatermark": watermark,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Roll up and retain sampled LocalOrderBook snapshots")
    parser.add_argument("--once", action="store_true", help="Run one maintenance pass and exit")
    parser.add_argument("--watch", action="store_true", help="Run maintenance forever on an interval")
    parser.add_argument("--dry-run", action="store_true", help="Report rows that would be rolled/deleted without mutating the DB")
    parser.add_argument("--interval", type=int, default=env_int("POLYDATA_LOB_MAINTENANCE_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS))
    parser.add_argument("--rollup-before-days", type=int, default=env_int("POLYDATA_LOB_ROLLUP_BEFORE_DAYS", DEFAULT_ROLLUP_BEFORE_DAYS))
    parser.add_argument("--hot-retention-days", type=int, default=env_int("POLYDATA_LOB_HOT_RETENTION_DAYS", DEFAULT_HOT_RETENTION_DAYS))
    parser.add_argument("--warm-retention-days", type=int, default=env_int("POLYDATA_LOB_WARM_RETENTION_DAYS", DEFAULT_WARM_RETENTION_DAYS))
    parser.add_argument("--cold-retention-days", type=int, default=env_int("POLYDATA_LOB_COLD_RETENTION_DAYS", DEFAULT_COLD_RETENTION_DAYS))
    parser.add_argument("--batch-size", type=int, default=env_int("POLYDATA_LOB_MAINTENANCE_BATCH_SIZE", DEFAULT_BATCH_SIZE))
    parser.add_argument("--rollup-window-minutes", type=int, default=env_int("POLYDATA_LOB_ROLLUP_WINDOW_MINUTES", DEFAULT_ROLLUP_WINDOW_MINUTES))
    parser.add_argument("--rollup-max-windows", type=int, default=env_int("POLYDATA_LOB_ROLLUP_MAX_WINDOWS", DEFAULT_ROLLUP_MAX_WINDOWS))
    parser.add_argument("--delete-max-batches", type=int, default=env_int("POLYDATA_LOB_DELETE_MAX_BATCHES", DEFAULT_DELETE_MAX_BATCHES))
    parser.add_argument(
        "--status-path",
        type=Path,
        default=Path(
            os.environ.get(
                "POLYDATA_LOB_MAINTENANCE_STATUS_PATH",
                str(DEFAULT_STATUS_PATH),
            )
        ),
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    policy = MaintenancePolicy(
        rollup_before_days=args.rollup_before_days,
        hot_retention_days=args.hot_retention_days,
        warm_retention_days=args.warm_retention_days,
        cold_retention_days=args.cold_retention_days,
        batch_size=args.batch_size,
        rollup_window_minutes=args.rollup_window_minutes,
        rollup_max_windows=args.rollup_max_windows,
        delete_max_batches=args.delete_max_batches,
    )
    watch = bool(args.watch or not args.once)
    while True:
        try:
            summary = run_once(policy=policy, dry_run=args.dry_run)
        except Exception as exc:
            write_status(
                args.status_path,
                {
                    "status": "error",
                    "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "errorClass": exc.__class__.__name__,
                },
            )
            raise
        summary = {
            **summary,
            "status": "ok",
            "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        write_status(args.status_path, summary)
        print(json.dumps(summary, ensure_ascii=True, sort_keys=True), flush=True)
        if not watch:
            return 0
        time.sleep(max(60, int(args.interval or DEFAULT_INTERVAL_SECONDS)))


if __name__ == "__main__":
    raise SystemExit(main())
