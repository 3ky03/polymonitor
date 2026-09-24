from decimal import Decimal
from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.orderbook import (
    LocalOrderBook,
    NormalizedBookDelta,
    OrderBookNotReady,
    OrderBookOutOfOrder,
    TokenBookIdentity,
    book_snapshot_from_local_book,
    normalize_polymarket_event,
    normalize_rest_book,
)
from quant.backtest.execution import ExecutionConfig, lookup_snapshot, simulate_depth_fill
from quant.orderbook.registry import OrderBookRegistry


def _identity(token_id: str = "token-yes") -> TokenBookIdentity:
    return TokenBookIdentity(
        token_id=token_id,
        market_id=123,
        condition_id="0xcondition",
        outcome="YES",
        outcome_index=0,
        market_slug="sample-market",
    )


def _book() -> LocalOrderBook:
    book = LocalOrderBook(_identity())
    book.apply_snapshot(
        bids=[("0.48", "30"), ("0.47", "20"), ("0", "999")],
        asks=[("0.52", "25"), ("0.53", "10")],
        event_ts_ms=1000,
        source_hash="snapshot-1",
    )
    return book


def test_snapshot_builds_ready_book_and_metrics():
    book = _book()
    metrics = book.metrics(depth_levels=2)

    assert book.ready
    assert book.generation == 1
    assert metrics.best_bid == Decimal("0.48")
    assert metrics.best_bid_size == Decimal("30")
    assert metrics.best_ask == Decimal("0.52")
    assert metrics.spread == Decimal("0.04")
    assert metrics.mid == Decimal("0.50")
    assert metrics.level_count_bid == 2
    assert metrics.depth_total == Decimal("42.10")


def test_price_change_updates_adds_and_deletes_levels():
    book = _book()

    book.apply_change(side="bid", price="0.49", size="15", event_ts_ms=1001)
    book.apply_change(side="ask", price="0.52", size="0", event_ts_ms=1002)
    metrics = book.metrics(depth_levels=2)

    assert metrics.best_bid == Decimal("0.49")
    assert metrics.best_bid_size == Decimal("15")
    assert metrics.best_ask == Decimal("0.53")
    assert Decimal("0.52") not in book.asks


def test_delta_before_snapshot_is_rejected_without_mutating_book():
    book = LocalOrderBook(_identity())

    with pytest.raises(OrderBookNotReady):
        book.apply_change(side="bid", price="0.50", size="10", event_ts_ms=1)

    assert not book.ready
    assert not book.bids
    assert not book.asks


def test_out_of_order_change_marks_book_stale():
    book = _book()

    with pytest.raises(OrderBookOutOfOrder):
        book.apply_change(side="bid", price="0.49", size="10", event_ts_ms=999)

    assert not book.ready
    assert book.status == "stale"
    assert book.stale_reason == "out_of_order"


def test_mark_stale_if_idle_and_resnapshot_restores_ready():
    book = _book()

    assert book.mark_stale_if_idle(now_ms=2000, stale_after_ms=500)
    assert book.status == "stale"

    book.apply_snapshot(bids=[("0.44", "5")], asks=[("0.56", "7")], event_ts_ms=2100)
    assert book.ready
    assert book.stale_reason is None
    assert book.best_bid() == (Decimal("0.44"), Decimal("5"))


def test_polymarket_book_event_normalizes_into_snapshot():
    events = normalize_polymarket_event(
        {
            "event_type": "book",
            "asset_id": "token-yes",
            "timestamp": "123456789000",
            "hash": "bookhash",
            "bids": [{"price": "0.48", "size": "30"}],
            "asks": [{"price": "0.52", "size": "25"}],
        }
    )

    assert len(events) == 1
    snapshot = events[0]
    assert snapshot.token_id == "token-yes"
    assert snapshot.event_ts_ms == 123456789000
    assert snapshot.bids == ((Decimal("0.48"), Decimal("30")),)


def test_polymarket_price_change_event_normalizes_into_deltas():
    events = normalize_polymarket_event(
        {
            "event_type": "price_change",
            "market": "0xcondition",
            "timestamp": "123456789001",
            "price_changes": [
                {"asset_id": "token-yes", "side": "BUY", "price": "0.49", "size": "10", "hash": "h1"},
                {"asset_id": "token-yes", "side": "SELL", "price": "0.52", "size": "0", "hash": "h2"},
            ],
        }
    )

    assert events == [
        NormalizedBookDelta("token-yes", "bid", Decimal("0.49"), Decimal("10"), 123456789001, "h1", events[0].raw),
        NormalizedBookDelta("token-yes", "ask", Decimal("0.52"), Decimal("0"), 123456789001, "h2", events[1].raw),
    ]


def test_registry_applies_snapshot_and_delta_for_one_token():
    registry = OrderBookRegistry()
    identity = _identity()
    snapshot = normalize_rest_book(
        "token-yes",
        {"bids": [{"price": "0.40", "size": "12"}], "asks": [{"price": "0.60", "size": "9"}]},
        event_ts_ms=10,
    )
    delta = NormalizedBookDelta("token-yes", "bid", Decimal("0.41"), Decimal("15"), 11)

    registry.apply(identity, snapshot)
    metrics = registry.apply(identity, delta)

    assert metrics.best_bid == Decimal("0.41")
    assert registry.get("token-yes") is not None


def test_registry_rejects_token_identity_mismatch():
    registry = OrderBookRegistry()
    event = normalize_rest_book("other-token", {"bids": [], "asks": []}, event_ts_ms=10)

    with pytest.raises(ValueError):
        registry.apply(_identity("token-yes"), event)


def test_local_book_converts_to_depth_execution_snapshot():
    book = _book()

    snapshot = book_snapshot_from_local_book(book, snapshot_id=77, depth_levels=2)
    lookup = lookup_snapshot(
        [snapshot],
        decision_block=None,
        decision_timestamp=snapshot.timestamp,
        config=ExecutionConfig(),
    )
    fill = simulate_depth_fill(
        side="BUY_YES",
        target_size=Decimal("30"),
        config=ExecutionConfig(),
        lookup=lookup,
    )

    assert snapshot.snapshot_id == 77
    assert snapshot.source == "local_orderbook"
    assert snapshot.book_status == "ok"
    assert snapshot.bids[0] == (Decimal("0.48"), Decimal("30"))
    assert snapshot.asks[0] == (Decimal("0.52"), Decimal("25"))
    assert fill.status == "FILLED"
    assert fill.snapshot_id == 77


def test_local_book_snapshot_payload_includes_market_slug_for_thin_storage():
    payload = _book().snapshot_payload(depth_levels=2)

    assert payload["market_id"] == 123
    assert payload["market_slug"] == "sample-market"
    assert payload["condition_id"] == "0xcondition"
