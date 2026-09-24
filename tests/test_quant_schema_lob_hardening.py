from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.core import schema


def _joined(values: tuple[str, ...]) -> str:
    return "\n".join(values)


def test_lob_schema_adds_thin_snapshot_columns_rollup_and_dead_letter_tables():
    create_sql = _joined(schema.CREATE_TABLE_SQL)
    alter_sql = _joined(schema.ALTER_TABLE_SQL)

    for column in (
        "market_id",
        "condition_id",
        "market_slug",
        "snapshot_source",
        "storage_tier",
        "book_generation",
        "last_event_ts_ms",
    ):
        assert column in create_sql
        assert f"ADD COLUMN IF NOT EXISTS {column}" in alter_sql

    assert "quant.clob_orderbook_rollups_1m" in create_sql
    assert "PRIMARY KEY (token_id, side, bucket_minute)" in create_sql
    assert "quant.clob_orderbook_dead_letters" in create_sql
    assert "reason TEXT NOT NULL" in create_sql


def test_lob_schema_backfills_new_columns_with_safe_casts_and_indexes():
    migration_sql = _joined(schema.DATA_MIGRATION_SQL)
    index_sql = _joined(schema.CREATE_INDEX_SQL)

    assert "payload->>'market_id'" in migration_sql
    assert "~ '^[0-9]+$'" in migration_sql
    assert "payload->'coverage'->>'tier'" in migration_sql
    assert "payload->>'snapshotSource'" in migration_sql
    assert "idx_quant_clob_snapshots_market_side_time" in index_sql
    assert "idx_quant_clob_snapshots_storage_time" in index_sql
    assert "idx_quant_lob_rollups_market_time" in index_sql
    assert "idx_quant_lob_dead_letters_reason" in index_sql
