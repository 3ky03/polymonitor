from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from runtime import lob_snapshot_maintenance as maintenance


def test_lob_maintenance_cutoffs_follow_hot_warm_cold_policy():
    now = datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc)
    policy = maintenance.MaintenancePolicy(
        rollup_before_days=7,
        hot_retention_days=14,
        warm_retention_days=14,
        cold_retention_days=7,
        batch_size=5000,
    )

    cutoffs = maintenance.cutoff_map(now, policy)

    assert cutoffs["rollup"].isoformat() == "2026-06-11T12:00:00+00:00"
    assert cutoffs["hot"].isoformat() == "2026-06-04T12:00:00+00:00"
    assert cutoffs["warm"].isoformat() == "2026-06-04T12:00:00+00:00"
    assert cutoffs["cold"].isoformat() == "2026-06-11T12:00:00+00:00"


def test_lob_maintenance_rollup_sql_keeps_latest_snapshot_per_minute():
    sql = maintenance.ROLLUP_SQL

    assert "row_number() OVER" in sql
    assert "PARTITION BY s.token_id, s.side, date_trunc('minute', s.fetched_at)" in sql
    assert "ORDER BY s.fetched_at DESC, s.snapshot_id DESC" in sql
    assert "SELECT * FROM base WHERE rn = 1" in sql
    assert "ON CONFLICT (token_id, side, bucket_minute) DO UPDATE" in sql


def test_lob_maintenance_delete_requires_existing_rollup_and_batch_limit():
    delete_sql = maintenance.DELETE_ELIGIBLE_SQL
    execute_sql = maintenance.execute_delete.__code__.co_consts
    joined_consts = "\n".join(str(value) for value in execute_sql)

    assert "quant.clob_orderbook_rollups_1m r" in delete_sql
    assert "r.bucket_minute = date_trunc('minute', s.fetched_at)" in delete_sql
    assert "COALESCE(s.storage_tier, 'unknown') = 'cold'" in delete_sql
    assert "LIMIT %s" in joined_consts


def test_lob_maintenance_dry_run_uses_counts_not_mutating_sql():
    consts = "\n".join(str(value) for value in maintenance.dry_run_counts.__code__.co_consts)

    assert "SELECT count(*) AS groups" in consts
    assert "SELECT count(*) AS rows" in consts
    assert "DELETE FROM" not in consts
    assert "INSERT INTO" not in consts
