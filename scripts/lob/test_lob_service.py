#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lob_service import (
    LOBNormalizer,
    LobRepository,
    MarketResolver,
    SnapshotThrottle,
    collect_asset_ids,
    derive_market_token_rows,
    init_lob_schema,
)

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_scripts_root))

from db import configure_runtime_db, get_connection, init_schema


class LobServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "lob_test.sqlite")
        configure_runtime_db(backend="sqlite", sqlite_path=self.db_path)
        conn = get_connection(self.db_path, backend="sqlite")
        init_schema(conn=conn, db_path=self.db_path)
        init_lob_schema(conn=conn, db_path=self.db_path)
        conn.execute(
            """
            INSERT INTO markets (
                gamma_market_id, slug, condition_id, question_id, oracle,
                yes_token_id, no_token_id, title, description, enable_neg_risk,
                end_date, created_at, category, tags, clob_token_ids
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "g-1",
                "sample-market",
                "0xcondition1",
                "0xquestion1",
                "0xoracle1",
                "yes_token_1",
                "no_token_1",
                "Sample market",
                "desc",
                0,
                "2099-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                "Politics",
                json.dumps(["sample"]),
                json.dumps(["yes_token_1", "no_token_1"]),
            ),
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_derive_market_token_rows(self) -> None:
        rows = derive_market_token_rows(
            {
                "id": 1,
                "condition_id": "0xcondition1",
                "yes_token_id": "yes_token_1",
                "no_token_id": "no_token_1",
                "clob_token_ids": json.dumps(["yes_token_1", "no_token_1"]),
                "end_date": "2099-01-01T00:00:00+00:00",
            }
        )
        self.assertEqual(2, len(rows))
        self.assertEqual("YES", rows[0]["outcome"])
        self.assertEqual("NO", rows[1]["outcome"])

    def test_sync_market_tokens_creates_subscriptions(self) -> None:
        repo = LobRepository(db_path=self.db_path)
        try:
            summary = repo.sync_market_tokens()
            self.assertEqual(1, summary.markets_seen)
            self.assertEqual(2, summary.tokens_upserted)
            conn = get_connection(self.db_path, backend="sqlite")
            cur = conn.execute("SELECT token_id, outcome, active FROM market_subscriptions ORDER BY token_id")
            rows = cur.fetchall()
            conn.close()
            self.assertEqual(2, len(rows))
            self.assertEqual("no_token_1", rows[0][0])
            self.assertEqual("NO", rows[0][1])
            self.assertEqual(1, rows[0][2])
        finally:
            repo.close()

    def test_sync_market_tokens_only_scans_active_markets(self) -> None:
        conn = get_connection(self.db_path, backend="sqlite")
        conn.execute(
            """
            INSERT INTO markets (
                gamma_market_id, slug, condition_id, question_id, oracle,
                yes_token_id, no_token_id, title, description, enable_neg_risk,
                end_date, created_at, category, tags, clob_token_ids
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "g-2",
                "expired-market",
                "0xcondition_expired",
                "0xquestion_expired",
                "0xoracle2",
                "yes_token_expired",
                "no_token_expired",
                "Expired market",
                "desc",
                0,
                "2020-01-01T00:00:00Z",
                "2019-01-01T00:00:00Z",
                "Politics",
                json.dumps(["expired"]),
                json.dumps(["yes_token_expired", "no_token_expired"]),
            ),
        )
        conn.commit()
        conn.close()

        repo = LobRepository(db_path=self.db_path)
        try:
            summary = repo.sync_market_tokens(active_only=True)
            self.assertEqual(1, summary.markets_seen)
            conn = get_connection(self.db_path, backend="sqlite")
            count = conn.execute("SELECT COUNT(*) FROM market_tokens").fetchone()[0]
            expired = conn.execute(
                "SELECT COUNT(*) FROM market_tokens WHERE token_id IN (?, ?)",
                ("yes_token_expired", "no_token_expired"),
            ).fetchone()[0]
            conn.close()
            self.assertEqual(2, count)
            self.assertEqual(0, expired)
        finally:
            repo.close()

    def test_resync_deactivates_no_longer_active_tokens(self) -> None:
        repo = LobRepository(db_path=self.db_path)
        try:
            first = repo.sync_market_tokens()
            self.assertEqual(2, first.tokens_upserted)
            conn = get_connection(self.db_path, backend="sqlite")
            conn.execute(
                "UPDATE markets SET end_date = ? WHERE condition_id = ?",
                ("2020-01-01T00:00:00Z", "0xcondition1"),
            )
            conn.commit()
            conn.close()
            second = repo.sync_market_tokens(active_only=True)
            self.assertEqual(0, second.markets_seen)
            self.assertEqual(2, second.subscriptions_deactivated)
            conn = get_connection(self.db_path, backend="sqlite")
            rows = conn.execute(
                "SELECT desired_active, active, subscribe_status FROM market_subscriptions ORDER BY token_id"
            ).fetchall()
            conn.close()
            self.assertTrue(all(row[0] == 0 and row[1] == 0 for row in rows))
        finally:
            repo.close()

    def test_sync_market_tokens_since_id_only_fetches_new_markets(self) -> None:
        repo = LobRepository(db_path=self.db_path)
        try:
            initial_latest = repo.get_latest_market_id()
            conn = get_connection(self.db_path, backend="sqlite")
            conn.execute(
                """
                INSERT INTO markets (
                    gamma_market_id, slug, condition_id, question_id, oracle,
                    yes_token_id, no_token_id, title, description, enable_neg_risk,
                    end_date, created_at, category, tags, clob_token_ids
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "g-3",
                    "new-active-market",
                    "0xcondition_new",
                    "0xquestion_new",
                    "0xoracle3",
                    "yes_token_new",
                    "no_token_new",
                    "New active market",
                    "desc",
                    0,
                    "2099-01-01T00:00:00Z",
                    "2026-01-03T00:00:00Z",
                    "Sports",
                    json.dumps(["new"]),
                    json.dumps(["yes_token_new", "no_token_new"]),
                ),
            )
            conn.commit()
            conn.close()

            summary, latest_seen = repo.sync_market_tokens_since_id(initial_latest)
            self.assertEqual(1, summary.markets_seen)
            self.assertGreater(latest_seen, initial_latest)
            conn = get_connection(self.db_path, backend="sqlite")
            count = conn.execute(
                "SELECT COUNT(*) FROM market_subscriptions WHERE token_id IN (?, ?)",
                ("yes_token_new", "no_token_new"),
            ).fetchone()[0]
            conn.close()
            self.assertEqual(2, count)
        finally:
            repo.close()

    def test_normalizer_book_and_price_change(self) -> None:
        repo = LobRepository(db_path=self.db_path)
        try:
            repo.sync_market_tokens()
            resolver = MarketResolver(repo, db_path=self.db_path)
            mapping = resolver.ensure("yes_token_1")
            self.assertIsNotNone(mapping)
            normalizer = LOBNormalizer(stream_name="test_stream", max_depth_levels=2)

            book_event = {
                "event_type": "book",
                "asset_id": "yes_token_1",
                "market": "0xcondition1",
                "bids": [{"price": "0.48", "size": "30"}, {"price": "0.47", "size": "20"}],
                "asks": [{"price": "0.52", "size": "25"}, {"price": "0.53", "size": "10"}],
                "timestamp": "123456789000",
                "hash": "bookhash",
            }
            snapshots = normalizer.normalize_event(book_event, mapping)
            self.assertEqual(1, len(snapshots))
            self.assertEqual("0.48", snapshots[0].best_bid)
            self.assertEqual("0.52", snapshots[0].best_ask)
            self.assertEqual(4, len(snapshots[0].levels))

            price_change = {
                "event_type": "price_change",
                "market": "0xcondition1",
                "timestamp": "1757908892351",
                "price_changes": [
                    {
                        "asset_id": "yes_token_1",
                        "price": "0.5",
                        "size": "200",
                        "side": "BUY",
                        "hash": "pricehash1",
                        "best_bid": "0.5",
                        "best_ask": "0.55",
                    }
                ],
            }
            pc_snapshots = normalizer.normalize_event(price_change, mapping)
            self.assertEqual(1, len(pc_snapshots))
            self.assertEqual("price_change", pc_snapshots[0].event_type)
            self.assertEqual("0.5", pc_snapshots[0].price)
            self.assertEqual("BUY", pc_snapshots[0].side)
        finally:
            repo.close()

    def test_snapshot_insert_is_idempotent(self) -> None:
        repo = LobRepository(db_path=self.db_path)
        try:
            repo.sync_market_tokens()
            resolver = MarketResolver(repo, db_path=self.db_path)
            mapping = resolver.ensure("yes_token_1")
            normalizer = LOBNormalizer(stream_name="test_stream", max_depth_levels=1)
            event = {
                "event_type": "best_bid_ask",
                "market": "0xcondition1",
                "asset_id": "yes_token_1",
                "best_bid": "0.73",
                "best_ask": "0.77",
                "spread": "0.04",
                "timestamp": "1766789469958",
            }
            snapshot = normalizer.normalize_event(event, mapping)[0]
            self.assertTrue(repo.insert_snapshot(snapshot))
            self.assertFalse(repo.insert_snapshot(snapshot))
            conn = get_connection(self.db_path, backend="sqlite")
            count = conn.execute("SELECT COUNT(*) FROM lob_snapshots").fetchone()[0]
            conn.close()
            self.assertEqual(1, count)
        finally:
            repo.close()

    def test_unknown_token_backfill_before_insert(self) -> None:
        repo = LobRepository(db_path=self.db_path)
        try:
            repo.sync_market_tokens()

            def fake_discovery(token_ids, db_path):
                conn = get_connection(db_path, backend="sqlite")
                conn.execute(
                    """
                    INSERT INTO markets (
                        gamma_market_id, slug, condition_id, question_id, oracle,
                        yes_token_id, no_token_id, title, description, enable_neg_risk,
                        end_date, created_at, category, tags, clob_token_ids
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "g-2",
                        "sample-market-2",
                        "0xcondition2",
                        "0xquestion2",
                        "0xoracle2",
                        "yes_token_2",
                        "no_token_2",
                        "Sample market 2",
                        "desc2",
                        0,
                        "2099-01-01T00:00:00+00:00",
                        "2026-01-02T00:00:00+00:00",
                        "Sports",
                        json.dumps(["sample2"]),
                        json.dumps(["yes_token_2", "no_token_2"]),
                    ),
                )
                conn.commit()
                conn.close()
                return len(token_ids)

            resolver = MarketResolver(repo, db_path=self.db_path, discovery_callback=fake_discovery)
            mapping = resolver.ensure("yes_token_2")
            self.assertIsNotNone(mapping)
            self.assertEqual("0xcondition2", mapping.condition_id)
        finally:
            repo.close()

    def test_collect_asset_ids(self) -> None:
        self.assertEqual(["abc"], collect_asset_ids({"event_type": "book", "asset_id": "abc"}))
        self.assertEqual(
            ["a", "b"],
            collect_asset_ids(
                {
                    "event_type": "price_change",
                    "price_changes": [{"asset_id": "a"}, {"asset_id": "b"}],
                }
            ),
        )

    def test_snapshot_throttle(self) -> None:
        repo = LobRepository(db_path=self.db_path)
        try:
            repo.sync_market_tokens()
            resolver = MarketResolver(repo, db_path=self.db_path)
            mapping = resolver.ensure("yes_token_1")
            normalizer = LOBNormalizer(stream_name="test_stream", max_depth_levels=1)
            event_a = {
                "event_type": "best_bid_ask",
                "market": "0xcondition1",
                "asset_id": "yes_token_1",
                "best_bid": "0.70",
                "best_ask": "0.74",
                "timestamp": "1000",
            }
            event_b = {
                "event_type": "best_bid_ask",
                "market": "0xcondition1",
                "asset_id": "yes_token_1",
                "best_bid": "0.71",
                "best_ask": "0.75",
                "timestamp": "1100",
            }
            snap_a = normalizer.normalize_event(event_a, mapping)[0]
            snap_b = normalizer.normalize_event(event_b, mapping)[0]
            throttle = SnapshotThrottle(bbo_ms=500, price_change_ms=0)
            self.assertTrue(throttle.should_write(snap_a))
            self.assertFalse(throttle.should_write(snap_b))
        finally:
            repo.close()


if __name__ == "__main__":
    unittest.main()
