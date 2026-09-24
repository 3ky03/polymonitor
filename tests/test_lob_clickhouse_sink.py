from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.orderbook.clickhouse_sink import (  # noqa: E402
    ClickHouseLobSink,
    LobClickHouseSettings,
    clickhouse_lob_storage_report,
    create_lob_clickhouse_schema,
    delta_event_to_tsv,
    snapshot_event_to_level_tsv,
)
from quant.orderbook.local_book import TokenBookIdentity  # noqa: E402
from quant.orderbook.polymarket_adapter import NormalizedBookDelta, NormalizedBookSnapshot  # noqa: E402


class FakeClickHouseClient:
    def __init__(self):
        self.executed = []
        self.scalars = []
        self.json_rows = []

    def execute(self, query, *, stdin=None, timeout_seconds=None):
        self.executed.append((query, stdin, timeout_seconds))

    def query_json_rows(self, query, *, timeout_seconds=None):
        self.json_rows.append((query, timeout_seconds))
        return [
            {"table": "quant_lob_delta_fact", "rows": 1000, "bytes_on_disk": 80000},
            {"table": "quant_lob_level_fact", "rows": 200, "bytes_on_disk": 24000},
        ]

    def query_scalar(self, query, *, timeout_seconds=None):
        self.scalars.append((query, timeout_seconds))
        return "50" if "quant_lob_delta_fact" in query else "5"


def _identity() -> TokenBookIdentity:
    return TokenBookIdentity("token-yes", 42, "0xcondition", "YES", 0, "sample-market")


def test_clickhouse_lob_schema_uses_compressed_numeric_columns_and_ttl():
    client = FakeClickHouseClient()
    settings = LobClickHouseSettings(enabled=True, ttl_days=14, schema_timeout_seconds=3)

    create_lob_clickhouse_schema(client=client, settings=settings)

    ddl = "\n".join(query for query, _stdin, _timeout in client.executed)
    assert "quant_lob_delta_fact" in ddl
    assert "price_ppm UInt32" in ddl
    assert "size_micros UInt64" in ddl
    assert "LowCardinality(String)" in ddl
    assert "TTL event_date + INTERVAL 14 DAY DELETE" in ddl
    assert "ORDER BY (market_id, token_id, event_ts, book_side, price_ppm)" in ddl
    assert {timeout for _query, _stdin, timeout in client.executed} == {3}


def test_delta_event_to_tsv_compresses_price_size_and_codes():
    row = delta_event_to_tsv(
        identity=_identity(),
        event=NormalizedBookDelta("token-yes", "bid", Decimal("0.1234567"), Decimal("10.25"), 1234567890123, "abc123"),
        tier="hot",
        generation=7,
        received_ts_ms=1234567890456,
    )

    assert row is not None
    fields = row.split("\t")
    assert fields[2] == "42"
    assert fields[3] == "sample-market"
    assert fields[6] == "1"  # YES
    assert fields[7] == "1"  # bid
    assert fields[8] == "1"  # upsert
    assert fields[9] == "123456"
    assert fields[10] == "10250000"
    assert fields[11] == "7"
    assert fields[14] == "hot"


def test_snapshot_event_to_level_tsv_writes_top_n_only():
    rows = snapshot_event_to_level_tsv(
        identity=_identity(),
        event=NormalizedBookSnapshot(
            "token-yes",
            bids=((Decimal("0.40"), Decimal("10")), (Decimal("0.39"), Decimal("9"))),
            asks=((Decimal("0.42"), Decimal("11")), (Decimal("0.43"), Decimal("12"))),
            event_ts_ms=1234567890123,
        ),
        tier="warm",
        generation=3,
        depth_limit=1,
    )

    assert len(rows) == 2
    assert rows[0].split("\t")[8] == "1"  # bid
    assert rows[1].split("\t")[8] == "2"  # ask
    assert rows[0].split("\t")[10] == "400000"
    assert rows[1].split("\t")[10] == "420000"


def test_clickhouse_lob_sink_respects_tier_filter_and_batches():
    client = FakeClickHouseClient()
    settings = LobClickHouseSettings(enabled=True, tiers=frozenset({"hot"}), batch_size=1, insert_timeout_seconds=4)
    sink = ClickHouseLobSink(settings=settings, client=client)

    skipped = sink.enqueue_delta(
        identity=_identity(),
        event=NormalizedBookDelta("token-yes", "bid", Decimal("0.1"), Decimal("1"), 1),
        tier="cold",
    )
    written = sink.enqueue_delta(
        identity=_identity(),
        event=NormalizedBookDelta("token-yes", "bid", Decimal("0.2"), Decimal("2"), 2),
        tier="hot",
    )

    assert skipped == 0
    assert written == 1
    assert len(client.executed) == 1
    assert "INSERT INTO quant_lob_delta_fact" in client.executed[0][0]
    assert client.executed[0][2] == 4
    status = sink.status_snapshot()
    assert status["deltaEventsSeen"] == 2
    assert status["deltaRowsSkippedTier"] == 1
    assert status["deltaRowsEnqueued"] == 1
    assert status["deltaRowsInserted"] == 1
    assert status["bufferedRows"] == 0


def test_clickhouse_lob_sink_status_reports_level_rows_and_buffers():
    client = FakeClickHouseClient()
    settings = LobClickHouseSettings(enabled=True, tiers=frozenset({"warm"}), batch_size=100)
    sink = ClickHouseLobSink(settings=settings, client=client)

    written = sink.enqueue_snapshot_levels(
        identity=_identity(),
        event=NormalizedBookSnapshot(
            "token-yes",
            bids=((Decimal("0.40"), Decimal("10")),),
            asks=((Decimal("0.42"), Decimal("11")),),
            event_ts_ms=1234567890123,
        ),
        tier="warm",
        depth_limit=2,
    )

    assert written == 2
    status = sink.status_snapshot()
    assert status["snapshotEventsSeen"] == 1
    assert status["levelRowsEnqueued"] == 2
    assert status["levelRowsInserted"] == 0
    assert status["bufferedLevelRows"] == 2


def test_clickhouse_lob_storage_report_projects_retention_bytes():
    client = FakeClickHouseClient()
    settings = LobClickHouseSettings(enabled=True, ttl_days=7, report_timeout_seconds=6)

    report = clickhouse_lob_storage_report(client=client, settings=settings)

    assert report["totalBytesOnDisk"] == 104000
    assert report["projectedBytesPerDay"] > 0
    assert report["projectedRetentionBytes"] == report["projectedBytesPerDay"] * 7
    assert [timeout for _query, timeout in client.json_rows] == [6]
    assert [timeout for _query, timeout in client.scalars] == [6, 6]
