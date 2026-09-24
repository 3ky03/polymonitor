from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from db import init_schema
from db.sync_trade_analytics import _full_refresh_market_status_snapshot
from oracle.settlement_parser import (
    OUTCOME_CANCELLED,
    OUTCOME_NO,
    OUTCOME_YES,
    choose_best_settlement,
    parse_fast_settlement_code,
    parse_oracle_settlement_event,
    parse_payout,
    parse_settled_price,
)


class SettlementParserTestCase(unittest.TestCase):
    def test_settled_price_normalizes_uma_binary_prices(self) -> None:
        self.assertEqual(OUTCOME_YES, parse_settled_price("1").settlement_outcome)
        self.assertEqual(OUTCOME_YES, parse_settled_price("1000000000000000000").settlement_outcome)
        self.assertEqual(OUTCOME_NO, parse_settled_price("0").settlement_outcome)
        self.assertEqual(OUTCOME_CANCELLED, parse_settled_price("0.5").settlement_outcome)

    def test_payout_normalizes_ctf_resolution_arrays(self) -> None:
        self.assertEqual(OUTCOME_YES, parse_payout("[1, 0]").settlement_outcome)
        self.assertEqual(OUTCOME_NO, parse_payout("[0, 1]").settlement_outcome)
        self.assertEqual(OUTCOME_CANCELLED, parse_payout("[1, 1]").settlement_outcome)

    def test_oracle_parser_prefers_settled_price_then_payout(self) -> None:
        row = {"event_status": "settle", "settled_price": "", "payout": "[0, 1]", "id": 8}
        result = parse_oracle_settlement_event(row)
        self.assertEqual(OUTCOME_NO, result.settlement_outcome)
        self.assertEqual("oracle_payout", result.settlement_source)
        self.assertEqual(8, result.settlement_event_id)

    def test_fast_resolution_is_fallback_after_oracle(self) -> None:
        oracle_result = parse_oracle_settlement_event({"event_status": "settle", "settled_price": "1"})
        fast_result = parse_fast_settlement_code(2)
        self.assertEqual(OUTCOME_YES, choose_best_settlement(oracle_result, fast_result).settlement_outcome)
        self.assertEqual(OUTCOME_NO, choose_best_settlement(None, fast_result).settlement_outcome)


class MarketStatusSnapshotSettlementTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema(conn=self.conn)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_resolution_fast (
                market_id INTEGER PRIMARY KEY,
                condition_id TEXT,
                slug TEXT,
                settlement_code INTEGER NOT NULL,
                closed_time TEXT,
                updated_at TEXT
            )
            """
        )
        self.conn.executemany(
            """
            INSERT INTO markets (
                id, slug, condition_id, question_id, yes_token_id, no_token_id, title
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "market-1", "condition-1", "question-1", "yes-1", "no-1", "Market 1"),
                (2, "market-2", "condition-2", "question-2", "yes-2", "no-2", "Market 2"),
                (3, "market-3", "condition-3", "question-3", "yes-3", "no-3", "Market 3"),
                (4, "market-4", "condition-4", "question-4", "yes-4", "no-4", "Market 4"),
            ],
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_snapshot_merges_oracle_and_fast_resolution_sources(self) -> None:
        self.conn.executemany(
            """
            INSERT INTO oracle_events (
                tx_hash, log_index, block_number, event_status, market_id,
                settled_price, payout, settlement_transaction
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("0x1", 0, 10, "settle", 1, "1", "", "0xsettle1"),
                ("0x2", 0, 11, "settle", 2, "", "[0, 1]", "0xsettle2"),
                ("0x4", 0, 12, "propose", 4, "", "", ""),
            ],
        )
        self.conn.execute(
            """
            INSERT INTO market_resolution_fast (
                market_id, condition_id, slug, settlement_code, closed_time, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (3, "condition-3", "market-3", 3, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )

        _full_refresh_market_status_snapshot(self.conn)

        rows = {
            int(row["market_id"]): dict(row)
            for row in self.conn.execute("SELECT * FROM market_status_snapshot").fetchall()
        }
        self.assertEqual(OUTCOME_YES, rows[1]["settlement_outcome"])
        self.assertEqual("oracle_settled_price", rows[1]["settlement_source"])
        self.assertEqual(OUTCOME_NO, rows[2]["settlement_outcome"])
        self.assertEqual("oracle_payout", rows[2]["settlement_source"])
        self.assertEqual(OUTCOME_CANCELLED, rows[3]["settlement_outcome"])
        self.assertEqual("market_resolution_fast", rows[3]["settlement_source"])
        self.assertEqual(1, rows[4]["has_propose"])
        self.assertEqual("UNKNOWN", rows[4]["settlement_outcome"])


if __name__ == "__main__":
    unittest.main()
