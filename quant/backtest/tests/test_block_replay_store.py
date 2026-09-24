from __future__ import annotations

from quant.backtest.runners import block_replay_store


class FakeClickHouse:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""

    def query_json_rows(self, sql, timeout_seconds=None):
        self.sql = sql
        return list(self.rows)


def test_large_block_replay_range_load_uses_primary_key_scan_and_filters_rows(monkeypatch):
    monkeypatch.setattr(block_replay_store, "GLOBAL_RANGE_SCAN_MARKET_THRESHOLD", 2)
    client = FakeClickHouse(
        [
            {"market_id": 1, "block_number": 100, "price": "0.60"},
            {"market_id": 1, "block_number": 500, "price": "0.61"},
            {"f.market_id": 2, "f.block_number": 210, "price": "0.70"},
            {"market_id": 3, "block_number": 300, "price": "0.80"},
        ]
    )

    rows = block_replay_store.load_orderfilled_block_replay_rows_for_ranges(
        {
            1: (90, 120),
            2: (200, 220),
            3: (1000, 1100),
        },
        client=client,
    )

    assert [(row["market_id"], row["block_number"]) for row in rows] == [(1, 100), (2, 210)]
    assert "PREWHERE market_id IN (1,2,3)" in client.sql
    assert "block_number BETWEEN 90 AND 1100" in client.sql
    assert "arrayJoin" in client.sql
    assert "INNER JOIN" in client.sql
    assert " OR " not in client.sql
