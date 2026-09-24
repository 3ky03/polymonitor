from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from db import dict_from_row
from db import backfill_market_list_change24h as backfill_job


def _connect_sqlite(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


class MarketListChange24hBackfillTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "backfill.sqlite3")
        conn = _connect_sqlite(self.db_path)
        conn.execute(
            """
            CREATE TABLE sync_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                last_block INTEGER,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE market_list_serving (
                market_id INTEGER PRIMARY KEY,
                price_24h_ago REAL
            )
            """
        )
        conn.executemany(
            "INSERT INTO market_list_serving (market_id, price_24h_ago) VALUES (?, NULL)",
            [(1,), (2,), (3,), (4,), (5,)],
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_run_backfill_advances_in_batches_and_persists_progress(self):
        threshold = datetime(2026, 4, 22, 0, 0, tzinfo=timezone.utc)

        def fake_refresh(conn, market_ids, threshold_dt):
            self.assertEqual(threshold_dt, threshold)
            conn.executemany(
                "UPDATE market_list_serving SET price_24h_ago = ? WHERE market_id = ?",
                [(1.23, market_id) for market_id in market_ids],
            )
            return len(market_ids)

        with patch.object(backfill_job, "init_schema", return_value=None), \
             patch.object(backfill_job, "get_connection", side_effect=lambda db_path: _connect_sqlite(db_path)), \
             patch.object(backfill_job, "_refresh_market_list_price_24h_ago", side_effect=fake_refresh), \
             patch.object(backfill_job, "_threshold_datetime_24h", return_value=threshold):
            first = backfill_job.run_backfill(
                self.db_path,
                batch_size=2,
                max_batches=1,
                force_new_cycle=True,
                verbose=False,
            )
            second = backfill_job.run_backfill(
                self.db_path,
                batch_size=2,
                max_batches=1,
                verbose=False,
            )

        self.assertFalse(first["completed"])
        self.assertEqual(first["last_market_id"], 2)
        self.assertEqual(first["updated_markets"], 2)
        self.assertFalse(second["completed"])
        self.assertEqual(second["last_market_id"], 4)
        self.assertEqual(second["updated_markets"], 2)

        conn = _connect_sqlite(self.db_path)
        state_row = conn.execute(
            "SELECT value, last_block FROM sync_state WHERE key = ?",
            (backfill_job.MARKET_LIST_CHANGE24H_BACKFILL_SYNC_KEY,),
        ).fetchone()
        self.assertIsNotNone(state_row)
        state_payload = json.loads(dict_from_row(state_row)["value"])
        self.assertEqual(int(dict_from_row(state_row)["last_block"]), 4)
        self.assertEqual(state_payload["last_market_id"], 4)
        priced_rows = conn.execute(
            "SELECT COUNT(*) AS c FROM market_list_serving WHERE price_24h_ago IS NOT NULL"
        ).fetchone()
        self.assertEqual(int(dict_from_row(priced_rows)["c"]), 4)
        conn.close()

    def test_completed_cycle_restarts_with_new_threshold(self):
        threshold_one = datetime(2026, 4, 22, 0, 0, tzinfo=timezone.utc)
        threshold_two = datetime(2026, 4, 22, 1, 0, tzinfo=timezone.utc)

        def fake_refresh(conn, market_ids, threshold_dt):
            marker = 1.0 if threshold_dt == threshold_one else 2.0
            conn.executemany(
                "UPDATE market_list_serving SET price_24h_ago = ? WHERE market_id = ?",
                [(marker, market_id) for market_id in market_ids],
            )
            return len(market_ids)

        with patch.object(backfill_job, "init_schema", return_value=None), \
             patch.object(backfill_job, "get_connection", side_effect=lambda db_path: _connect_sqlite(db_path)), \
             patch.object(backfill_job, "_refresh_market_list_price_24h_ago", side_effect=fake_refresh), \
             patch.object(backfill_job, "_threshold_datetime_24h", side_effect=[threshold_one, threshold_two]):
            first = backfill_job.run_backfill(
                self.db_path,
                batch_size=10,
                force_new_cycle=True,
                verbose=False,
            )
            second = backfill_job.run_backfill(
                self.db_path,
                batch_size=10,
                verbose=False,
            )

        self.assertTrue(first["completed"])
        self.assertEqual(first["updated_markets"], 5)
        self.assertTrue(second["completed"])
        self.assertEqual(second["updated_markets"], 5)
        self.assertEqual(second["threshold"], threshold_two.isoformat().replace("+00:00", "Z"))


if __name__ == "__main__":
    unittest.main()
