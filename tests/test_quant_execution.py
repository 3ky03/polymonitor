from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.backtest.execution import (
    BookSnapshot,
    ExecutionConfig,
    lookup_snapshot,
    simulate_depth_fill,
)


def _snapshot(snapshot_id: int, ts: str, *, block: int | None = None) -> BookSnapshot:
    return BookSnapshot(
        snapshot_id=snapshot_id,
        token_id="token-1",
        side="YES",
        block_number=block,
        timestamp=datetime.fromisoformat(ts.replace("Z", "+00:00")),
        bids=((Decimal("0.58"), Decimal("100")), (Decimal("0.57"), Decimal("100"))),
        asks=((Decimal("0.62"), Decimal("50")), (Decimal("0.64"), Decimal("200"))),
        snapshot_version=f"v{snapshot_id}",
    )


def test_snapshot_lookup_uses_latest_snapshot_not_after_execution_time():
    config = ExecutionConfig(max_book_staleness_seconds=Decimal("3600"))
    snapshots = [
        _snapshot(1, "2026-06-10T00:00:00Z", block=100),
        _snapshot(2, "2026-06-10T00:10:00Z", block=110),
        _snapshot(3, "2026-06-10T00:20:00Z", block=120),
    ]

    result = lookup_snapshot(
        snapshots,
        decision_block=115,
        decision_timestamp=datetime(2026, 6, 10, 0, 15, tzinfo=timezone.utc),
        config=config,
    )

    assert result.status == "OK"
    assert result.snapshot is not None
    assert result.snapshot.snapshot_id == 2
    assert result.staleness_seconds == Decimal("300.0000000000")


def test_snapshot_lookup_rejects_future_only_books():
    result = lookup_snapshot(
        [_snapshot(3, "2026-06-10T00:20:00Z", block=120)],
        decision_block=115,
        decision_timestamp=datetime(2026, 6, 10, 0, 15, tzinfo=timezone.utc),
        config=ExecutionConfig(),
    )

    assert result.status == "NO_BOOK"
    assert result.snapshot is None


def test_snapshot_lookup_marks_stale_when_older_than_config():
    result = lookup_snapshot(
        [_snapshot(1, "2026-06-10T00:00:00Z", block=100)],
        decision_block=120,
        decision_timestamp=datetime(2026, 6, 10, 0, 20, tzinfo=timezone.utc),
        config=ExecutionConfig(max_book_staleness_seconds=Decimal("60")),
    )

    assert result.status == "STALE_BOOK"
    assert result.snapshot is not None
    assert result.staleness_seconds == Decimal("1200.0000000000")


def test_depth_fill_buy_consumes_asks_and_sell_consumes_bids():
    lookup = lookup_snapshot(
        [_snapshot(1, "2026-06-10T00:00:00Z", block=100)],
        decision_block=100,
        decision_timestamp=datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc),
        config=ExecutionConfig(),
    )

    buy = simulate_depth_fill(
        side="BUY_YES",
        target_size=Decimal("100"),
        config=ExecutionConfig(),
        lookup=lookup,
    )
    sell = simulate_depth_fill(
        side="SELL_YES",
        target_size=Decimal("150"),
        config=ExecutionConfig(),
        lookup=lookup,
    )

    assert buy.status == "FILLED"
    assert buy.avg_fill_price == Decimal("0.6300000000")
    assert buy.levels_consumed == 2
    assert sell.status == "FILLED"
    assert sell.avg_fill_price == Decimal("0.5766666667")
    assert sell.levels_consumed == 2
