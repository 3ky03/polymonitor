from decimal import Decimal

import pytest

from quant.backtest.backtest_engine import BacktestParameters, PricePoint, simulate_strategy


pytestmark = pytest.mark.backtest_validation


def test_unfilled_exit_holds_to_resolution_settlement_one():
    points = [
        PricePoint(x_value=1, price=Decimal("0.60"), volume=Decimal("30"), trade_count=1),
        PricePoint(x_value=2, price=Decimal("0.40"), volume=Decimal("30"), trade_count=1),
        PricePoint(x_value=3, price=Decimal("0.97"), volume=Decimal("30"), trade_count=1),
    ]
    params = BacktestParameters(
        initial_capital=Decimal("100"),
        position_size=Decimal("10"),
        buy_limit_price=Decimal("0.50"),
        sell_limit_price=Decimal("0.99"),
        settlement_value=Decimal("1"),
    )

    result = simulate_strategy(points, {"market_slug": "demo", "token_side": "YES", "price_source": "orderfilled_block_close"}, params)

    assert result["trades"][0]["exit_reason"] == "settlement"
    assert result["trades"][0]["exit_price"] == Decimal("1")
    assert [row["event_type"] for row in result["ledger"]] == ["BUY", "SETTLEMENT"]
    assert next(metric for metric in result["metrics"] if metric["metric_key"] == "trade_exit_pnl")["value"] == Decimal("0E-10")
    assert next(metric for metric in result["metrics"] if metric["metric_key"] == "settlement_pnl")["value"] == result["trades"][0]["pnl"]


def test_unfilled_exit_settlement_zero_does_not_force_close_at_last_price():
    points = [
        PricePoint(x_value=10, price=Decimal("0.60"), volume=Decimal("30"), trade_count=1),
        PricePoint(x_value=11, price=Decimal("0.40"), volume=Decimal("30"), trade_count=1),
        PricePoint(x_value=12, price=Decimal("0.97"), volume=Decimal("30"), trade_count=1),
    ]
    params = BacktestParameters(
        initial_capital=Decimal("100"),
        position_size=Decimal("10"),
        buy_limit_price=Decimal("0.50"),
        sell_limit_price=Decimal("0.99"),
        settlement_value=Decimal("0"),
    )

    result = simulate_strategy(points, {"market_slug": "demo", "token_side": "YES", "price_source": "orderfilled_block_close"}, params)

    trade = result["trades"][0]
    assert trade["exit_reason"] == "settlement"
    assert trade["exit_price"] == Decimal("0")
    assert trade["exit_price"] != Decimal("0.97")
    assert result["ledger"][-1]["event_type"] == "SETTLEMENT"
