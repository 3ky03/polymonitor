from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.backtest.backtest_engine import BacktestParameters, PricePoint, build_data_quality_report, data_quality_metrics, simulate_strategy
from quant.backtest.execution_profiles import effective_execution_profile
from quant.backtest.frameworks import normalize_backtest_engine, run_framework_backtest
from quant.backtest.frameworks import _nautilus_python_bin
from quant.core.db import ClickHouseSettings, PostgresSettings, database_settings_summary


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_normalize_backtest_engine_aliases():
    assert normalize_backtest_engine(None) == "builtin"
    assert normalize_backtest_engine("fixed-threshold-v1") == "builtin"
    assert normalize_backtest_engine("bt") == "backtrader"
    assert normalize_backtest_engine("nautilus") == "nautilus_trader"


def test_execution_profile_accepts_public_cent_units():
    params = BacktestParameters(adverse_slippage_cents=Decimal("1"))
    profile = effective_execution_profile(params)
    assert profile.adverse_slippage_cents == Decimal("0.0100000000")

    normalized_params = BacktestParameters(adverse_slippage_cents=Decimal("0.005"))
    normalized_profile = effective_execution_profile(normalized_params)
    assert normalized_profile.adverse_slippage_cents == Decimal("0.005")


def test_quant_db_settings_are_loaded_from_environment_at_instantiation(monkeypatch):
    monkeypatch.setenv("POLYDATA_POSTGRES_HOST", "pg.env.local")
    monkeypatch.setenv("POLYDATA_POSTGRES_PORT", "55432")
    monkeypatch.setenv("POLYDATA_POSTGRES_USER", "env_user")
    monkeypatch.setenv("POLYDATA_POSTGRES_PASSWORD", "env_password")
    monkeypatch.setenv("POLYDATA_POSTGRES_DATABASE", "env_db")
    monkeypatch.setenv("POLYDATA_POSTGRES_SEARCH_PATH", "quant,public")
    monkeypatch.setenv("POLYDATA_ORDERFILLED_CLICKHOUSE_PASSWORD", "ch_password")
    monkeypatch.delenv("CLICKHOUSE_PASSWORD", raising=False)

    pg = PostgresSettings()
    ch = ClickHouseSettings()
    summary = database_settings_summary(pg, ch)

    assert pg.host == "pg.env.local"
    assert pg.port == 55432
    assert pg.user == "env_user"
    assert pg.password == "env_password"
    assert pg.database == "env_db"
    assert pg.search_path == "quant,public"
    assert ch.password == "ch_password"
    assert summary["postgres"]["password_configured"] is True
    assert summary["clickhouse"]["password_configured"] is True
    assert "password" not in summary["postgres"]


def test_quant_db_settings_do_not_use_hardcoded_secret_defaults(monkeypatch):
    for key in (
        "POLYDATA_POSTGRES_PASSWORD",
        "POLYMARKET_POSTGRES_PASSWORD",
        "POLYMARKET_POSTGRESQL_PASSWORD",
        "POLYMARKET_PostgreSQL_PASSWORD",
        "POLYDATA_ORDERFILLED_CLICKHOUSE_PASSWORD",
        "CLICKHOUSE_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)

    assert PostgresSettings().password == ""
    assert ClickHouseSettings().password == ""


def test_quant_db_settings_support_legacy_postgresql_aliases(monkeypatch):
    for key in (
        "POLYDATA_POSTGRES_HOST",
        "POLYDATA_POSTGRES_PORT",
        "POLYDATA_POSTGRES_USER",
        "POLYDATA_POSTGRES_PASSWORD",
        "POLYDATA_POSTGRES_DATABASE",
        "POLYMARKET_POSTGRES_HOST",
        "POLYMARKET_POSTGRES_PORT",
        "POLYMARKET_POSTGRES_USER",
        "POLYMARKET_POSTGRES_PASSWORD",
        "POLYMARKET_POSTGRES_DATABASE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("POLYMARKET_PostgreSQL_HOST", "legacy.pg")
    monkeypatch.setenv("POLYMARKET_PostgreSQL_PORT", "65432")
    monkeypatch.setenv("POLYMARKET_PostgreSQL_USER", "legacy_user")
    monkeypatch.setenv("POLYMARKET_PostgreSQL_PASSWORD", "legacy_password")
    monkeypatch.setenv("POLYMARKET_PostgreSQL_DATABASE", "legacy_db")

    pg = PostgresSettings()
    assert pg.host == "legacy.pg"
    assert pg.port == 65432
    assert pg.user == "legacy_user"
    assert pg.password == "legacy_password"
    assert pg.database == "legacy_db"


def test_normalize_backtest_engine_rejects_unknown():
    with pytest.raises(ValueError):
        normalize_backtest_engine("zipline")


def test_builtin_framework_runs_and_annotates_result():
    points = [
        PricePoint(x_value=1, price=Decimal("0.60"), volume=Decimal("10")),
        PricePoint(x_value=2, price=Decimal("0.70"), volume=Decimal("10")),
        PricePoint(x_value=3, price=Decimal("0.50"), volume=Decimal("10")),
    ]
    run = {
        "market_slug": "demo-market",
        "token_side": "YES",
        "price_source": "orderfilled_block_close",
    }
    params = BacktestParameters(
        entry_threshold=Decimal("0.58"),
        exit_threshold=Decimal("0.55"),
        stop_loss=Decimal("0.50"),
        take_profit=Decimal("0.50"),
        max_holding_bars=10,
        initial_capital=Decimal("1000"),
        position_size=Decimal("100"),
        execution_price_mode="LEGACY",
    )

    result = run_framework_backtest(
        "builtin",
        points,
        run,
        params,
        builtin_simulator=simulate_strategy,
        metrics_builder=lambda trades, equity, price_points, parameters: [],
    )

    assert result["trades"][0]["exit_reason"] == "exit_threshold"
    assert result["events"][0]["event_type"] == "framework"
    engine_metric = next(row for row in result["metrics"] if row["metric_key"] == "backtest_engine")
    assert engine_metric["formatted_value"] == "builtin"


def test_builtin_framework_applies_execution_costs_and_liquidity_cap():
    points = [
        PricePoint(x_value=1, price=Decimal("0.60"), volume=Decimal("40")),
        PricePoint(x_value=2, price=Decimal("0.70"), volume=Decimal("40")),
        PricePoint(x_value=3, price=Decimal("0.50"), volume=Decimal("40")),
    ]
    run = {
        "market_slug": "demo-market",
        "token_side": "YES",
        "price_source": "orderfilled_block_close",
    }
    params = BacktestParameters(
        entry_threshold=Decimal("0.58"),
        exit_threshold=Decimal("0.55"),
        stop_loss=Decimal("0.50"),
        take_profit=Decimal("0.50"),
        max_holding_bars=10,
        initial_capital=Decimal("1000"),
        position_size=Decimal("100"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("20"),
        liquidity_cap_pct=Decimal("50"),
        execution_price_mode="LEGACY",
    )

    result = run_framework_backtest(
        "builtin",
        points,
        run,
        params,
        builtin_simulator=simulate_strategy,
        metrics_builder=lambda trades, equity, price_points, parameters: [],
    )

    trade = result["trades"][0]
    assert trade["size"] == Decimal("33.3333333333")
    assert trade["entry_price"] > Decimal("0.60")
    assert trade["exit_price"] < Decimal("0.50")
    assert trade["execution_cost"] > Decimal("0")
    assert trade["requested_notional"] == Decimal("100")
    assert trade["filled_notional"] == Decimal("20.0000000000")
    assert trade["fill_pct"] == Decimal("20.0000000000")
    assert trade["pnl"] < Decimal("-2")


def test_builtin_framework_rejects_entry_when_min_fill_not_met():
    points = [
        PricePoint(x_value=1, price=Decimal("0.60"), volume=Decimal("20")),
        PricePoint(x_value=2, price=Decimal("0.70"), volume=Decimal("20")),
        PricePoint(x_value=3, price=Decimal("0.50"), volume=Decimal("20")),
    ]
    run = {
        "market_slug": "demo-market",
        "token_side": "YES",
        "price_source": "orderfilled_block_close",
    }
    params = BacktestParameters(
        entry_threshold=Decimal("0.58"),
        exit_threshold=Decimal("0.55"),
        stop_loss=Decimal("0.50"),
        take_profit=Decimal("0.50"),
        max_holding_bars=10,
        initial_capital=Decimal("1000"),
        position_size=Decimal("100"),
        liquidity_cap_pct=Decimal("50"),
        max_position_notional=Decimal("80"),
        min_fill_pct=Decimal("20"),
        execution_price_mode="LEGACY",
    )

    result = run_framework_backtest(
        "builtin",
        points,
        run,
        params,
        builtin_simulator=simulate_strategy,
        metrics_builder=lambda trades, equity, price_points, parameters: [],
    )

    assert result["trades"] == []
    rejected = next(event for event in result["events"] if event["event_type"] == "fill_rejected")
    assert rejected["meta"]["requested_notional"] == Decimal("80")
    assert rejected["meta"]["fill_pct"] == Decimal("12.5000000000")


def test_builtin_framework_supports_legacy_orderfilled_probability_fill():
    points = [
        PricePoint(x_value=1, price=Decimal("0.60"), volume=Decimal("40"), trade_count=4),
        PricePoint(x_value=2, price=Decimal("0.50"), volume=Decimal("20"), trade_count=2),
    ]
    run = {
        "market_slug": "demo-market",
        "token_side": "YES",
        "price_source": "orderfilled_block_close",
    }
    params = BacktestParameters(
        entry_threshold=Decimal("0.58"),
        exit_threshold=Decimal("0.55"),
        stop_loss=Decimal("0.50"),
        take_profit=Decimal("0.50"),
        max_holding_bars=10,
        initial_capital=Decimal("1000"),
        position_size=Decimal("100"),
        liquidity_cap_pct=Decimal("50"),
        min_fill_pct=Decimal("0"),
        execution_price_mode="ORDERFILLED",
        execution_profile="optimistic",
        adverse_slippage_cents=Decimal("0"),
        fill_probability_haircut_pct=Decimal("0"),
    )

    result = run_framework_backtest(
        "builtin",
        points,
        run,
        params,
        builtin_simulator=simulate_strategy,
        metrics_builder=lambda trades, equity, price_points, parameters: [],
    )

    open_event = next(event for event in result["events"] if event["event_type"] == "open")
    trade = result["trades"][0]
    assert open_event["meta"]["execution_source"] == "orderfilled_volume"
    assert open_event["meta"]["fill_probability"] == Decimal("20.0000000000")
    assert open_event["meta"]["block_volume"] == Decimal("40")
    assert open_event["meta"]["trade_count"] == 4
    assert trade["execution_source"] == "orderfilled_volume"
    assert Decimal("59.9") < trade["fill_probability"] < Decimal("60.1")
    assert trade["fill_status"] == "PARTIAL"
    assert len({row["trade_id"] for row in result["trades"]}) == len(result["trades"])


def test_orderfilled_first_phase1_tracks_orders_ledger_and_account_pnl():
    points = [
        PricePoint(x_value=10, price=Decimal("0.600"), volume=Decimal("200"), trade_count=8),
        PricePoint(x_value=11, price=Decimal("0.650"), volume=Decimal("120"), trade_count=5),
        PricePoint(x_value=12, price=Decimal("0.520"), volume=Decimal("150"), trade_count=7),
    ]
    run = {
        "market_slug": "demo-market",
        "token_side": "YES",
        "price_source": "orderfilled_block_close",
    }
    params = BacktestParameters(
        entry_threshold=Decimal("0.58"),
        exit_threshold=Decimal("0.55"),
        stop_loss=Decimal("0.50"),
        take_profit=Decimal("0.50"),
        max_holding_bars=10,
        initial_capital=Decimal("100"),
        position_size=Decimal("100"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("0"),
        liquidity_cap_pct=Decimal("100"),
        execution_price_mode="ORDERFILLED",
        execution_profile="realistic",
        adverse_slippage_cents=Decimal("0.005"),
        fill_probability_haircut_pct=Decimal("20"),
    )

    result = run_framework_backtest(
        "builtin",
        points,
        run,
        params,
        builtin_simulator=simulate_strategy,
        metrics_builder=lambda trades, equity, price_points, parameters: [],
    )

    orders = result["orders"]
    ledger = result["ledger"]
    trade = result["trades"][0]
    assert orders[0]["status"] == "PARTIAL_FILLED"
    assert orders[0]["fill_probability"] == Decimal("80.0000000000")
    assert orders[0]["filled_notional"] == Decimal("80.0000000000")
    assert orders[0]["avg_fill_price"] == Decimal("0.6050000000")
    assert orders[0]["fee_cost"] > Decimal("0")
    assert orders[0]["slippage_cost"] > Decimal("0")
    assert orders[0]["block_volume"] == Decimal("200")
    assert orders[0]["trade_count"] == 8
    assert trade["entry_order_id"] == orders[0]["order_id"]
    assert len(ledger) == 3
    assert ledger[0]["event_type"] == "BUY"
    assert ledger[0]["cash_after"] < Decimal("100")
    assert ledger[0]["position_after"] == sum((row["size"] for row in result["trades"]), Decimal("0"))
    assert ledger[0]["position_after"] <= orders[0]["filled_size"]
    assert ledger[1]["event_type"] == "SELL"
    assert ledger[1]["position_after"] > Decimal("0")
    assert ledger[-1]["event_type"] == "SELL"
    assert ledger[-1]["position_after"] == Decimal("0E-10")
    net_pnl = sum((row["pnl"] for row in result["trades"]), Decimal("0"))
    assert (ledger[-1]["cash_after"] - (params.initial_capital + net_pnl)).copy_abs() <= Decimal("0.0000000001")
    assert next(metric for metric in result["metrics"] if metric["metric_key"] == "ledger_realized_pnl")["value"] == net_pnl


def test_default_limit_replay_waits_for_buy_cross_then_sell_cross():
    points = [
        PricePoint(x_value=1, price=Decimal("0.60"), volume=Decimal("10"), trade_count=1),
        PricePoint(x_value=2, price=Decimal("0.56"), volume=Decimal("10"), trade_count=1),
        PricePoint(x_value=3, price=Decimal("0.40"), volume=Decimal("50"), trade_count=1),
        PricePoint(x_value=4, price=Decimal("0.70"), volume=Decimal("50"), trade_count=1),
    ]
    run = {
        "market_slug": "demo-market",
        "token_side": "YES",
        "price_source": "orderfilled_block_close",
    }
    params = BacktestParameters(
        initial_capital=Decimal("100"),
        position_size=Decimal("10"),
        buy_limit_price=Decimal("0.50"),
        sell_limit_price=Decimal("0.65"),
        fee_bps=Decimal("0"),
    )

    result = run_framework_backtest(
        "builtin",
        points,
        run,
        params,
        builtin_simulator=simulate_strategy,
        metrics_builder=lambda trades, equity, price_points, parameters: [],
    )

    assert [order["status"] for order in result["orders"]] == ["FILLED", "FILLED"]
    trade = result["trades"][0]
    assert trade["entry_x"] == 3
    assert trade["entry_price"] == Decimal("0.5000000000")
    assert trade["exit_x"] == 4
    assert trade["exit_price"] == Decimal("0.6500000000")
    assert trade["exit_reason"] == "limit_exit"
    assert trade["pnl"] == Decimal("3.0000000000")
    assert result["ledger"][0]["event_type"] == "BUY"
    assert result["ledger"][1]["event_type"] == "SELL"
    assert next(metric for metric in result["metrics"] if metric["metric_key"] == "trade_exit_pnl")["value"] == Decimal("3.0000000000")
    assert next(metric for metric in result["metrics"] if metric["metric_key"] == "settlement_pnl")["value"] == Decimal("0E-10")


def test_limit_replay_does_not_force_close_when_sell_does_not_cross_and_settles():
    points = [
        PricePoint(x_value=1, price=Decimal("0.60"), volume=Decimal("10"), trade_count=1),
        PricePoint(x_value=2, price=Decimal("0.40"), volume=Decimal("50"), trade_count=1),
        PricePoint(x_value=3, price=Decimal("0.97"), volume=Decimal("10"), trade_count=1),
    ]
    run = {
        "market_slug": "demo-market",
        "token_side": "YES",
        "price_source": "orderfilled_block_close",
    }
    params = BacktestParameters(
        initial_capital=Decimal("100"),
        position_size=Decimal("10"),
        buy_limit_price=Decimal("0.50"),
        sell_limit_price=Decimal("0.99"),
        settlement_value=Decimal("1"),
        fee_bps=Decimal("0"),
    )

    result = run_framework_backtest(
        "builtin",
        points,
        run,
        params,
        builtin_simulator=simulate_strategy,
        metrics_builder=lambda trades, equity, price_points, parameters: [],
    )

    assert [order["order_type"] for order in result["orders"]] == ["resting_limit", "resting_limit", "settlement"]
    assert [order["status"] for order in result["orders"]] == ["FILLED", "NO_FILL", "FILLED"]
    trade = result["trades"][0]
    assert trade["exit_reason"] == "settlement"
    assert trade["exit_price"] == Decimal("1")
    assert trade["pnl"] == Decimal("10.0000000000")
    assert result["ledger"][-1]["event_type"] == "SETTLEMENT"
    assert next(metric for metric in result["metrics"] if metric["metric_key"] == "trade_exit_pnl")["value"] == Decimal("0E-10")
    assert next(metric for metric in result["metrics"] if metric["metric_key"] == "settlement_pnl")["value"] == Decimal("10.0000000000")


def test_builtin_framework_uses_clob_snapshot_depth_for_entry_fill():
    points = [
        PricePoint(x_value=100, price=Decimal("0.60"), volume=Decimal("10000"), timestamp=_dt("2026-06-10T00:00:30Z")),
        PricePoint(x_value=110, price=Decimal("0.50"), volume=Decimal("10000"), timestamp=_dt("2026-06-10T00:01:30Z")),
    ]
    run = {
        "market_slug": "demo-market",
        "token_side": "YES",
        "price_source": "orderfilled_block_close",
        "_clob_snapshots": [{
            "snapshot_id": 123,
            "token_id": "token-1",
            "side": "YES",
            "block_number": 100,
            "timestamp": "2026-06-10T00:00:00Z",
            "snapshot_version": "v-entry",
            "best_bid": "0.58",
            "best_ask": "0.62",
            "spread": "0.04",
            "asks": [
                {"price": "0.62", "size": "50"},
                {"price": "0.64", "size": "200"},
            ],
            "bids": [
                {"price": "0.58", "size": "200"},
            ],
        }],
    }
    params = BacktestParameters(
        entry_threshold=Decimal("0.58"),
        exit_threshold=Decimal("0.55"),
        stop_loss=Decimal("0.50"),
        take_profit=Decimal("0.50"),
        max_holding_bars=10,
        initial_capital=Decimal("1000"),
        position_size=Decimal("100"),
        liquidity_cap_pct=Decimal("100"),
        max_book_staleness_seconds=Decimal("900"),
        execution_price_mode="DEPTH",
    )

    result = run_framework_backtest(
        "builtin",
        points,
        run,
        params,
        builtin_simulator=simulate_strategy,
        metrics_builder=lambda trades, equity, price_points, parameters: [],
    )

    trade = result["trades"][0]
    open_event = next(event for event in result["events"] if event["event_type"] == "open")
    assert trade["requested_notional"] == Decimal("100")
    assert trade["filled_notional"] == Decimal("100.0000000000")
    assert trade["entry_price"] == Decimal("0.6336633663")
    assert open_event["meta"]["execution_source"] == "clob_depth"
    assert open_event["meta"]["book_snapshot_id"] == 123
    assert open_event["meta"]["levels_consumed"] == 2


def test_data_quality_report_flags_gaps_and_jumps():
    points = [
        PricePoint(x_value=100, price=Decimal("0.40"), volume=Decimal("10")),
        PricePoint(x_value=101, price=Decimal("0.42"), volume=Decimal("10")),
        PricePoint(x_value=180, price=Decimal("0.82"), volume=Decimal("10")),
        PricePoint(x_value=181, price=Decimal("0.81"), volume=Decimal("10")),
    ]
    run = {
        "market_slug": "demo-market",
        "token_side": "YES",
        "price_source": "orderfilled_block_close",
        "from_block": 100,
        "to_block": 181,
    }

    report = build_data_quality_report(points, run)
    metrics = data_quality_metrics(report)

    assert report["status"] == "review"
    assert report["gap_count"] == 1
    assert report["jump_count"] == 1
    assert len(report["data_version"]) == 20
    assert report["version_basis"] == "x_value:price:volume:trade_count"
    assert report["source_table"] == "quant.market_token_block_close"
    assert report["access_path"] == "market_slug+token_side+block_number_range"
    assert report["index_hint"] == "idx_quant_block_close_slug_side_block"
    assert report["query_guard_version"] == "phase1_keyed_price_access_v1"
    assert metrics[0]["metric_key"] == "data_quality_status"
    assert metrics[0]["status"] == "negative"
    version_metric = next(metric for metric in metrics if metric["metric_key"] == "data_version")
    assert version_metric["formatted_value"] == report["data_version"]
    access_metric = next(metric for metric in metrics if metric["metric_key"] == "data_access_path")
    assert access_metric["formatted_value"] == "market_slug+token_side+block_number_range"


def test_nautilus_framework_runs_through_python312_worker_when_available():
    if sys.version_info >= (3, 12):
        pytest.importorskip("nautilus_trader")
    else:
        try:
            _nautilus_python_bin()
        except RuntimeError as exc:
            pytest.skip(str(exc))

    points = [
        PricePoint(x_value=1, price=Decimal("0.60"), volume=Decimal("1")),
        PricePoint(x_value=2, price=Decimal("0.70"), volume=Decimal("1")),
        PricePoint(x_value=3, price=Decimal("0.50"), volume=Decimal("1")),
    ]
    run = {
        "market_slug": "demo-market",
        "token_side": "YES",
        "price_source": "orderfilled_block_close",
    }
    params = BacktestParameters(
        entry_threshold=Decimal("0.58"),
        exit_threshold=Decimal("0.55"),
        stop_loss=Decimal("0.50"),
        take_profit=Decimal("0.50"),
        max_holding_bars=10,
        initial_capital=Decimal("1000"),
        position_size=Decimal("100"),
        execution_price_mode="LEGACY",
    )

    result = run_framework_backtest(
        "nautilus_trader",
        points,
        run,
        params,
        builtin_simulator=simulate_strategy,
        metrics_builder=lambda trades, equity, price_points, parameters: [],
    )

    engine_metric = next(row for row in result["metrics"] if row["metric_key"] == "backtest_engine")
    assert result["trades"][0]["exit_reason"] == "exit_threshold"
    assert engine_metric["formatted_value"] == "nautilus_trader"
