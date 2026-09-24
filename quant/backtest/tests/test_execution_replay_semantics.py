from decimal import Decimal

import pytest

from quant.backtest.runners.data_sources import FixtureReplayStore
from quant.backtest.runners.execution_replay import OrderIntent, replay_limit_order, sequence_key


pytestmark = pytest.mark.backtest_validation


def test_buy_sell_crossing_only_creates_candidates():
    events = FixtureReplayStore().load_trade_events("single_fill")

    buy = replay_limit_order(
        OrderIntent("BUY_YES", Decimal("0.50"), Decimal("4"), "GTC", sequence_key(100, 0, 2, "0xsubmit"), liquidity_cap_pct=Decimal("50")),
        events,
    )
    sell = replay_limit_order(
        OrderIntent("SELL_YES", Decimal("0.55"), Decimal("4"), "GTC", sequence_key(100, 0, 2, "0xsubmit"), liquidity_cap_pct=Decimal("100")),
        events,
    )

    assert buy.status == "FILLED"
    assert all(event.trade_price <= Decimal("0.50") for event in buy.candidate_events)
    assert all(event.event_sequence > sequence_key(100, 0, 2, "0xsubmit") for event in buy.candidate_events)
    assert sell.status == "NO_FILL"
    assert sell.candidate_events == []


def test_volume_cap_produces_partial_fill():
    events = FixtureReplayStore().load_trade_events("illiquid")

    result = replay_limit_order(
        OrderIntent("BUY_YES", Decimal("0.50"), Decimal("100"), "GTC", sequence_key(299, 0, 0, "0xsubmit"), liquidity_cap_pct=Decimal("10")),
        events,
    )

    assert result.status == "PARTIAL_FILLED"
    assert result.filled_size == Decimal("0.2000000000")
    assert result.filled_size <= sum((event.size for event in result.candidate_events), Decimal("0")) * Decimal("0.10")
    assert result.unfilled_size == Decimal("99.8000000000")


def test_same_block_sequence_prevents_future_function():
    events = FixtureReplayStore().load_trade_events("single_fill")

    after_log_2 = replay_limit_order(
        OrderIntent("BUY_YES", Decimal("0.50"), Decimal("100"), "GTC", sequence_key(100, 0, 2, "0xsubmit")),
        events,
    )
    after_log_4 = replay_limit_order(
        OrderIntent("BUY_YES", Decimal("0.50"), Decimal("100"), "GTC", sequence_key(100, 0, 4, "0xsubmit")),
        events,
    )

    assert [event.log_index for event in after_log_2.candidate_events] == [3, 1]
    assert [event.log_index for event in after_log_4.candidate_events] == [1]


def test_time_in_force_gtc_gtd_fok_fak():
    events = FixtureReplayStore().load_trade_events("lifecycle")

    gtc = replay_limit_order(OrderIntent("BUY_YES", Decimal("0.50"), Decimal("5"), "GTC", sequence_key(99, 0, 9, "0xsubmit")), events)
    gtd = replay_limit_order(OrderIntent("BUY_YES", Decimal("0.46"), Decimal("2"), "GTD", sequence_key(100, 0, 9, "0xsubmit"), expire_sequence=sequence_key(101, 0, 9, "0xexpire")), events)
    fok = replay_limit_order(OrderIntent("BUY_YES", Decimal("0.50"), Decimal("20"), "FOK", sequence_key(100, 0, 0, "0xsubmit"), liquidity_cap_pct=Decimal("10")), events)
    fak = replay_limit_order(OrderIntent("BUY_YES", Decimal("0.50"), Decimal("20"), "FAK", sequence_key(100, 0, 0, "0xsubmit"), liquidity_cap_pct=Decimal("10")), events)

    assert gtc.status == "FILLED"
    assert gtd.status == "EXPIRED"
    assert fok.status == "REJECTED"
    assert fok.filled_size == Decimal("0")
    assert fak.status == "PARTIAL_FILLED"
    assert fak.filled_size == Decimal("1.0000000000")
