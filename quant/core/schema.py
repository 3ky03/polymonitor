"""Postgres schema for quant price production tables."""

from __future__ import annotations

import re
from typing import Any


CREATE_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS quant"

OPTIONAL_EXTENSION_SQL: tuple[str, ...] = (
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
)


CREATE_TABLE_SQL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS quant.market_token_metadata (
        market_id BIGINT NOT NULL,
        gamma_market_id TEXT,
        market_slug TEXT,
        condition_id TEXT,
        question_id TEXT,
        market_title TEXT,
        token_id TEXT NOT NULL PRIMARY KEY,
        token_id_hex TEXT,
        token_side TEXT NOT NULL,
        outcome_index INTEGER,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        closed BOOLEAN NOT NULL DEFAULT FALSE,
        archived BOOLEAN NOT NULL DEFAULT FALSE,
        deprecated BOOLEAN NOT NULL DEFAULT FALSE,
        duplicate_group_key TEXT,
        end_date TIMESTAMPTZ,
        created_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.market_event_metadata (
        event_id TEXT NOT NULL,
        event_slug TEXT NOT NULL PRIMARY KEY,
        event_title TEXT NOT NULL,
        event_category TEXT,
        event_subcategory TEXT,
        event_image_url TEXT,
        event_icon_url TEXT,
        description TEXT,
        start_date TIMESTAMPTZ,
        end_date TIMESTAMPTZ,
        resolution_date TIMESTAMPTZ,
        status TEXT NOT NULL DEFAULT 'unknown',
        volume NUMERIC(38, 10),
        liquidity NUMERIC(38, 10),
        grouping_confidence TEXT NOT NULL DEFAULT 'official',
        source TEXT NOT NULL DEFAULT 'core.markets',
        created_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.market_event_members (
        event_slug TEXT NOT NULL REFERENCES quant.market_event_metadata(event_slug) ON DELETE CASCADE,
        event_id TEXT NOT NULL,
        market_id BIGINT NOT NULL,
        market_slug TEXT NOT NULL,
        condition_id TEXT,
        question TEXT,
        outcome_label TEXT NOT NULL,
        outcome_key TEXT NOT NULL,
        outcome_order INTEGER NOT NULL DEFAULT 0,
        token_yes_id TEXT,
        token_no_id TEXT,
        clob_token_ids JSONB,
        status TEXT NOT NULL DEFAULT 'unknown',
        active BOOLEAN NOT NULL DEFAULT FALSE,
        closed BOOLEAN NOT NULL DEFAULT FALSE,
        resolved BOOLEAN NOT NULL DEFAULT FALSE,
        volume NUMERIC(38, 10),
        liquidity NUMERIC(38, 10),
        block_rows BIGINT NOT NULL DEFAULT 0,
        frontend_rows BIGINT NOT NULL DEFAULT 0,
        orderfilled_rows BIGINT NOT NULL DEFAULT 0,
        latest_yes NUMERIC(20, 10),
        latest_no NUMERIC(20, 10),
        latest_block BIGINT,
        latest_timestamp TIMESTAMPTZ,
        coverage_status TEXT NOT NULL DEFAULT 'none',
        grouping_confidence TEXT NOT NULL DEFAULT 'official',
        source TEXT NOT NULL DEFAULT 'core.markets',
        created_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (event_slug, market_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.market_price_eligibility (
        token_id TEXT PRIMARY KEY REFERENCES quant.market_token_metadata(token_id) ON DELETE CASCADE,
        market_id BIGINT NOT NULL,
        market_slug TEXT,
        token_side TEXT NOT NULL,
        eligible BOOLEAN NOT NULL DEFAULT FALSE,
        has_orderfilled_trades BOOLEAN NOT NULL DEFAULT FALSE,
        is_archived BOOLEAN NOT NULL DEFAULT FALSE,
        is_deprecated BOOLEAN NOT NULL DEFAULT FALSE,
        is_duplicate_market BOOLEAN NOT NULL DEFAULT FALSE,
        skip_reason TEXT,
        orderfilled_trade_count BIGINT NOT NULL DEFAULT 0,
        first_orderfilled_block BIGINT,
        last_orderfilled_block BIGINT,
        frontend_points BIGINT NOT NULL DEFAULT 0,
        checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.market_token_frontend_price_1m (
        token_id TEXT NOT NULL REFERENCES quant.market_token_metadata(token_id) ON DELETE CASCADE,
        market_id BIGINT NOT NULL,
        market_slug TEXT,
        token_side TEXT NOT NULL,
        ts_minute TIMESTAMPTZ NOT NULL,
        timestamp BIGINT NOT NULL,
        price NUMERIC(20, 10) NOT NULL,
        source TEXT NOT NULL DEFAULT 'prices-history',
        fidelity_minutes INTEGER NOT NULL DEFAULT 1,
        fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (token_id, ts_minute)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.market_token_block_close (
        token_id TEXT NOT NULL REFERENCES quant.market_token_metadata(token_id) ON DELETE CASCADE,
        market_id BIGINT NOT NULL,
        market_slug TEXT,
        token_side TEXT NOT NULL,
        block_number BIGINT NOT NULL,
        block_timestamp TIMESTAMPTZ,
        close_price NUMERIC(20, 10) NOT NULL,
        yes_probability_close NUMERIC(20, 10),
        vwap_price NUMERIC(20, 10),
        yes_probability_vwap NUMERIC(20, 10),
        close_raw_price NUMERIC(20, 10),
        close_price_source TEXT NOT NULL DEFAULT 'unknown',
        close_tx_hash TEXT,
        close_log_index INTEGER,
        close_maker_amount NUMERIC(38, 0),
        close_taker_amount NUMERIC(38, 0),
        trade_count BIGINT NOT NULL DEFAULT 0,
        raw_trade_count BIGINT NOT NULL DEFAULT 0,
        internal_filtered_count BIGINT NOT NULL DEFAULT 0,
        invalid_size_count BIGINT NOT NULL DEFAULT 0,
        invalid_price_count BIGINT NOT NULL DEFAULT 0,
        amount_ratio_count BIGINT NOT NULL DEFAULT 0,
        raw_price_fallback_count BIGINT NOT NULL DEFAULT 0,
        extreme_trade_count BIGINT NOT NULL DEFAULT 0,
        anomaly_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
        volume NUMERIC(38, 10) NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT 'clean_orderfilled_fact',
        built_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (token_id, block_number)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.quant_price_series_tiles (
        tile_key TEXT PRIMARY KEY,
        key_version INTEGER NOT NULL DEFAULT 1,
        entity_type TEXT NOT NULL DEFAULT 'event',
        tile_kind TEXT NOT NULL DEFAULT 'series',
        scope TEXT NOT NULL,
        entity_slug TEXT NOT NULL,
        price_source TEXT NOT NULL,
        range_name TEXT NOT NULL,
        resolution TEXT NOT NULL,
        point_format TEXT NOT NULL DEFAULT 'lite',
        top_n INTEGER NOT NULL,
        max_points INTEGER NOT NULL,
        window_from_x BIGINT,
        window_to_x BIGINT,
        payload JSONB NOT NULL,
        payload_bytes BIGINT NOT NULL DEFAULT 0,
        row_count BIGINT NOT NULL DEFAULT 0,
        data_min_x BIGINT,
        data_max_x BIGINT,
        cache_ttl_seconds INTEGER,
        updated_reason TEXT,
        expires_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.market_price_build_runs (
        run_id BIGSERIAL PRIMARY KEY,
        source TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'once',
        status TEXT NOT NULL,
        started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at TIMESTAMPTZ,
        requested_from_ts TIMESTAMPTZ,
        requested_to_ts TIMESTAMPTZ,
        requested_from_block BIGINT,
        requested_to_block BIGINT,
        markets_total BIGINT NOT NULL DEFAULT 0,
        markets_complete BIGINT NOT NULL DEFAULT 0,
        rows_written BIGINT NOT NULL DEFAULT 0,
        error_count BIGINT NOT NULL DEFAULT 0,
        last_error TEXT,
        meta JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.market_price_build_market_state (
        source TEXT NOT NULL,
        token_id TEXT NOT NULL REFERENCES quant.market_token_metadata(token_id) ON DELETE CASCADE,
        market_id BIGINT NOT NULL,
        market_slug TEXT,
        token_side TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        next_from_ts TIMESTAMPTZ,
        next_to_ts TIMESTAMPTZ,
        next_from_block BIGINT,
        next_to_block BIGINT,
        last_complete_ts TIMESTAMPTZ,
        last_complete_block BIGINT,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (source, token_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.market_price_build_market_progress (
        market_id BIGINT PRIMARY KEY,
        market_slug TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        token_count INTEGER NOT NULL DEFAULT 0,
        eligible_token_count INTEGER NOT NULL DEFAULT 0,
        first_orderfilled_block BIGINT,
        last_orderfilled_block BIGINT,
        min_block_complete BIGINT,
        max_block_complete BIGINT,
        min_frontend_complete_ts TIMESTAMPTZ,
        max_frontend_complete_ts TIMESTAMPTZ,
        block_rows_written BIGINT NOT NULL DEFAULT 0,
        frontend_rows_written BIGINT NOT NULL DEFAULT 0,
        error_count BIGINT NOT NULL DEFAULT 0,
        last_error TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.market_orderfilled_market_stats (
        market_id BIGINT PRIMARY KEY,
        trade_count BIGINT NOT NULL DEFAULT 0,
        first_orderfilled_block BIGINT,
        last_orderfilled_block BIGINT,
        refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.market_orderfilled_token_stats (
        market_id BIGINT NOT NULL,
        token_id_hex TEXT NOT NULL,
        trade_count BIGINT NOT NULL DEFAULT 0,
        first_orderfilled_block BIGINT,
        last_orderfilled_block BIGINT,
        refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (market_id, token_id_hex)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.market_price_build_targets (
        source TEXT NOT NULL,
        token_id TEXT NOT NULL REFERENCES quant.market_token_metadata(token_id) ON DELETE CASCADE,
        market_id BIGINT NOT NULL,
        market_slug TEXT,
        token_side TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 100,
        reason TEXT,
        requested_from_ts TIMESTAMPTZ,
        requested_to_ts TIMESTAMPTZ,
        requested_from_block BIGINT,
        requested_to_block BIGINT,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (source, token_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.clob_orderbook_snapshots (
        snapshot_id BIGSERIAL PRIMARY KEY,
        token_id TEXT NOT NULL,
        side TEXT NOT NULL DEFAULT 'YES',
        paired_token_id TEXT,
        market_id BIGINT,
        condition_id TEXT,
        market_slug TEXT,
        market_title TEXT,
        source TEXT NOT NULL DEFAULT 'clob-book',
        snapshot_source TEXT,
        storage_tier TEXT NOT NULL DEFAULT 'unknown',
        book_generation BIGINT,
        last_event_ts_ms BIGINT,
        book_status TEXT NOT NULL DEFAULT 'unknown',
        block_number BIGINT,
        snapshot_timestamp TIMESTAMPTZ,
        best_bid NUMERIC(20, 10),
        best_ask NUMERIC(20, 10),
        spread NUMERIC(20, 10),
        mid NUMERIC(20, 10),
        bid_depth NUMERIC(38, 10) NOT NULL DEFAULT 0,
        ask_depth NUMERIC(38, 10) NOT NULL DEFAULT 0,
        depth_total NUMERIC(38, 10) NOT NULL DEFAULT 0,
        imbalance NUMERIC(20, 10),
        level_count_bid INTEGER NOT NULL DEFAULT 0,
        level_count_ask INTEGER NOT NULL DEFAULT 0,
        payload JSONB NOT NULL,
        snapshot_version TEXT,
        fetched_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.clob_orderbook_rollups_1m (
        token_id TEXT NOT NULL,
        side TEXT NOT NULL DEFAULT 'YES',
        bucket_minute TIMESTAMPTZ NOT NULL,
        market_id BIGINT,
        condition_id TEXT,
        market_slug TEXT,
        market_title TEXT,
        source TEXT NOT NULL DEFAULT 'local-orderbook',
        snapshot_source TEXT,
        storage_tier TEXT NOT NULL DEFAULT 'unknown',
        book_status TEXT NOT NULL DEFAULT 'unknown',
        first_snapshot_id BIGINT,
        last_snapshot_id BIGINT,
        sample_count BIGINT NOT NULL DEFAULT 0,
        first_fetched_at TIMESTAMPTZ,
        last_fetched_at TIMESTAMPTZ,
        best_bid NUMERIC(20, 10),
        best_ask NUMERIC(20, 10),
        spread NUMERIC(20, 10),
        mid NUMERIC(20, 10),
        bid_depth NUMERIC(38, 10) NOT NULL DEFAULT 0,
        ask_depth NUMERIC(38, 10) NOT NULL DEFAULT 0,
        depth_total NUMERIC(38, 10) NOT NULL DEFAULT 0,
        imbalance NUMERIC(20, 10),
        level_count_bid INTEGER NOT NULL DEFAULT 0,
        level_count_ask INTEGER NOT NULL DEFAULT 0,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (token_id, side, bucket_minute)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.clob_orderbook_dead_letters (
        dead_letter_id BIGSERIAL PRIMARY KEY,
        reason TEXT NOT NULL,
        token_id TEXT,
        market_id BIGINT,
        condition_id TEXT,
        event_type TEXT,
        source TEXT NOT NULL DEFAULT 'local-orderbook',
        raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        detail TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.quant_backtest_runs (
        run_id BIGSERIAL PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'queued',
        market_slug TEXT NOT NULL,
        token_side TEXT NOT NULL,
        price_source TEXT NOT NULL,
        backtest_engine TEXT NOT NULL DEFAULT 'builtin',
        from_ts BIGINT,
        to_ts BIGINT,
        from_block BIGINT,
        to_block BIGINT,
        rows_processed BIGINT NOT NULL DEFAULT 0,
        error TEXT,
        meta JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        started_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.quant_backtest_parameters (
        run_id BIGINT PRIMARY KEY REFERENCES quant.quant_backtest_runs(run_id) ON DELETE CASCADE,
        entry_threshold NUMERIC(20, 10) NOT NULL,
        exit_threshold NUMERIC(20, 10) NOT NULL,
        stop_loss NUMERIC(20, 10) NOT NULL,
        take_profit NUMERIC(20, 10) NOT NULL,
        max_holding_bars INTEGER NOT NULL,
        initial_capital NUMERIC(38, 10) NOT NULL DEFAULT 100000,
        position_size NUMERIC(38, 10) NOT NULL DEFAULT 100,
        fee_bps NUMERIC(20, 10) NOT NULL DEFAULT 0,
        slippage_bps NUMERIC(20, 10) NOT NULL DEFAULT 0,
        liquidity_cap_pct NUMERIC(20, 10) NOT NULL DEFAULT 100,
        max_position_notional NUMERIC(38, 10) NOT NULL DEFAULT 0,
        min_fill_pct NUMERIC(20, 10) NOT NULL DEFAULT 0,
        execution_price_mode TEXT NOT NULL DEFAULT 'ORDERFILLED_LIMIT_REPLAY',
        execution_profile TEXT NOT NULL DEFAULT 'realistic',
        order_role TEXT NOT NULL DEFAULT 'taker',
        latency_blocks BIGINT NOT NULL DEFAULT 0,
        adverse_slippage_cents NUMERIC(20, 10) NOT NULL DEFAULT 0.005,
        fill_probability_haircut_pct NUMERIC(20, 10) NOT NULL DEFAULT 20,
        latency_seconds NUMERIC(20, 10) NOT NULL DEFAULT 0,
        max_book_staleness_seconds NUMERIC(20, 10) NOT NULL DEFAULT 900,
        allow_partial_fill BOOLEAN NOT NULL DEFAULT TRUE,
        min_fill_size NUMERIC(38, 10) NOT NULL DEFAULT 0,
        reject_on_stale_book BOOLEAN NOT NULL DEFAULT TRUE,
        final_valuation_mode TEXT NOT NULL DEFAULT 'SETTLEMENT',
        max_entry_price NUMERIC(20, 10) NOT NULL DEFAULT 1,
        min_exit_price NUMERIC(20, 10) NOT NULL DEFAULT 0,
        buy_limit_price NUMERIC(20, 10),
        sell_limit_price NUMERIC(20, 10),
        settlement_value NUMERIC(20, 10),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.quant_backtest_metrics (
        run_id BIGINT NOT NULL REFERENCES quant.quant_backtest_runs(run_id) ON DELETE CASCADE,
        metric_key TEXT NOT NULL,
        metric_name TEXT NOT NULL,
        metric_group TEXT NOT NULL DEFAULT 'overview',
        value NUMERIC(38, 10),
        formatted_value TEXT,
        delta TEXT,
        status TEXT NOT NULL DEFAULT 'neutral',
        tooltip TEXT,
        sort_order INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (run_id, metric_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.quant_backtest_equity (
        run_id BIGINT NOT NULL REFERENCES quant.quant_backtest_runs(run_id) ON DELETE CASCADE,
        point_index INTEGER NOT NULL,
        x_axis TEXT NOT NULL,
        x_value BIGINT NOT NULL,
        equity NUMERIC(38, 10) NOT NULL,
        drawdown NUMERIC(38, 10) NOT NULL,
        drawdown_pct NUMERIC(20, 10) NOT NULL,
        cumulative_return NUMERIC(20, 10) NOT NULL,
        PRIMARY KEY (run_id, point_index)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.quant_backtest_trades (
        run_id BIGINT NOT NULL REFERENCES quant.quant_backtest_runs(run_id) ON DELETE CASCADE,
        trade_id TEXT NOT NULL,
        market_slug TEXT NOT NULL,
        token_side TEXT NOT NULL,
        side TEXT NOT NULL DEFAULT 'LONG',
        x_axis TEXT NOT NULL,
        entry_x BIGINT NOT NULL,
        exit_x BIGINT NOT NULL,
        entry_price NUMERIC(20, 10) NOT NULL,
        exit_price NUMERIC(20, 10) NOT NULL,
        size NUMERIC(38, 10) NOT NULL,
        notional NUMERIC(38, 10) NOT NULL,
        requested_notional NUMERIC(38, 10) NOT NULL DEFAULT 0,
        filled_notional NUMERIC(38, 10) NOT NULL DEFAULT 0,
        requested_size NUMERIC(38, 10) NOT NULL DEFAULT 0,
        filled_size NUMERIC(38, 10) NOT NULL DEFAULT 0,
        unfilled_size NUMERIC(38, 10) NOT NULL DEFAULT 0,
        fill_pct NUMERIC(20, 10) NOT NULL DEFAULT 100,
        fill_status TEXT NOT NULL DEFAULT 'FILLED',
        book_snapshot_id BIGINT,
        snapshot_version TEXT,
        staleness_seconds NUMERIC(20, 10),
        staleness_blocks BIGINT,
        avg_fill_price NUMERIC(20, 10),
        fill_probability NUMERIC(20, 10) NOT NULL DEFAULT 0,
        block_volume NUMERIC(38, 10) NOT NULL DEFAULT 0,
        trade_count BIGINT NOT NULL DEFAULT 0,
        available_notional NUMERIC(38, 10) NOT NULL DEFAULT 0,
        execution_source TEXT NOT NULL DEFAULT 'unknown',
        fee_cost NUMERIC(38, 10) NOT NULL DEFAULT 0,
        slippage_cost NUMERIC(38, 10) NOT NULL DEFAULT 0,
        execution_cost NUMERIC(38, 10) NOT NULL DEFAULT 0,
        entry_order_id TEXT,
        exit_order_id TEXT,
        pnl NUMERIC(38, 10) NOT NULL,
        pnl_pct NUMERIC(20, 10) NOT NULL,
        holding_bars INTEGER NOT NULL,
        exit_reason TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (run_id, trade_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.quant_backtest_orders (
        run_id BIGINT NOT NULL REFERENCES quant.quant_backtest_runs(run_id) ON DELETE CASCADE,
        order_id TEXT NOT NULL,
        signal_index INTEGER NOT NULL DEFAULT 0,
        trade_id TEXT,
        x_axis TEXT NOT NULL,
        signal_x BIGINT NOT NULL,
        submit_x BIGINT NOT NULL,
        decision_price NUMERIC(20, 10) NOT NULL DEFAULT 0,
        requested_price NUMERIC(20, 10),
        side TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'taker',
        order_type TEXT NOT NULL DEFAULT 'market_like_limit',
        status TEXT NOT NULL,
        requested_size NUMERIC(38, 10) NOT NULL DEFAULT 0,
        requested_notional NUMERIC(38, 10) NOT NULL DEFAULT 0,
        filled_size NUMERIC(38, 10) NOT NULL DEFAULT 0,
        filled_notional NUMERIC(38, 10) NOT NULL DEFAULT 0,
        unfilled_size NUMERIC(38, 10) NOT NULL DEFAULT 0,
        avg_fill_price NUMERIC(20, 10),
        fill_probability NUMERIC(20, 10) NOT NULL DEFAULT 0,
        fill_pct NUMERIC(20, 10) NOT NULL DEFAULT 0,
        block_volume NUMERIC(38, 10) NOT NULL DEFAULT 0,
        trade_count BIGINT NOT NULL DEFAULT 0,
        available_notional NUMERIC(38, 10) NOT NULL DEFAULT 0,
        fee_cost NUMERIC(38, 10) NOT NULL DEFAULT 0,
        slippage_cost NUMERIC(38, 10) NOT NULL DEFAULT 0,
        execution_cost NUMERIC(38, 10) NOT NULL DEFAULT 0,
        latency_blocks BIGINT NOT NULL DEFAULT 0,
        latency_seconds NUMERIC(20, 10) NOT NULL DEFAULT 0,
        no_fill_reason TEXT,
        execution_source TEXT NOT NULL DEFAULT 'unknown',
        meta JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (run_id, order_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.quant_backtest_ledger (
        run_id BIGINT NOT NULL REFERENCES quant.quant_backtest_runs(run_id) ON DELETE CASCADE,
        ledger_id TEXT NOT NULL,
        order_id TEXT,
        trade_id TEXT,
        event_type TEXT NOT NULL,
        x_axis TEXT NOT NULL,
        x_value BIGINT NOT NULL,
        market_slug TEXT NOT NULL,
        token_side TEXT NOT NULL,
        shares_delta NUMERIC(38, 10) NOT NULL DEFAULT 0,
        cash_delta NUMERIC(38, 10) NOT NULL DEFAULT 0,
        fee NUMERIC(38, 10) NOT NULL DEFAULT 0,
        rebate NUMERIC(38, 10) NOT NULL DEFAULT 0,
        slippage_cost NUMERIC(38, 10) NOT NULL DEFAULT 0,
        execution_cost NUMERIC(38, 10) NOT NULL DEFAULT 0,
        realized_pnl NUMERIC(38, 10) NOT NULL DEFAULT 0,
        position_after NUMERIC(38, 10) NOT NULL DEFAULT 0,
        cash_after NUMERIC(38, 10) NOT NULL DEFAULT 0,
        price NUMERIC(20, 10),
        source TEXT NOT NULL DEFAULT 'backtest',
        meta JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (run_id, ledger_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.quant_backtest_events (
        run_id BIGINT NOT NULL REFERENCES quant.quant_backtest_runs(run_id) ON DELETE CASCADE,
        event_index INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        x_axis TEXT NOT NULL,
        x_value BIGINT NOT NULL,
        trade_id TEXT,
        price NUMERIC(20, 10),
        message TEXT,
        meta JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (run_id, event_index)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.quant_backtest_benchmark_runs (
        benchmark_id BIGSERIAL PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'queued',
        universe_type TEXT NOT NULL DEFAULT 'preset',
        universe_name TEXT NOT NULL,
        market_count INTEGER NOT NULL DEFAULT 0,
        strategy_name TEXT NOT NULL,
        parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
        profiles JSONB NOT NULL DEFAULT '{}'::jsonb,
        summary JSONB NOT NULL DEFAULT '{}'::jsonb,
        data_version TEXT,
        error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        started_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.quant_backtest_benchmark_rows (
        benchmark_id BIGINT NOT NULL REFERENCES quant.quant_backtest_benchmark_runs(benchmark_id) ON DELETE CASCADE,
        row_index INTEGER NOT NULL,
        market_id BIGINT,
        market_slug TEXT NOT NULL,
        title TEXT,
        event_time TIMESTAMPTZ,
        outcome TEXT,
        signal_time TIMESTAMPTZ,
        fast_status TEXT,
        accurate_status TEXT,
        fast_pnl NUMERIC(38, 10) NOT NULL DEFAULT 0,
        accurate_pnl NUMERIC(38, 10) NOT NULL DEFAULT 0,
        pnl_diff NUMERIC(38, 10) NOT NULL DEFAULT 0,
        fast_fill_block BIGINT NOT NULL DEFAULT 0,
        accurate_fill_block BIGINT NOT NULL DEFAULT 0,
        data_quality TEXT NOT NULL DEFAULT 'unknown',
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (benchmark_id, row_index)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.quant_backtest_benchmark_artifacts (
        benchmark_id BIGINT NOT NULL REFERENCES quant.quant_backtest_benchmark_runs(benchmark_id) ON DELETE CASCADE,
        artifact_key TEXT NOT NULL,
        artifact_kind TEXT NOT NULL,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (benchmark_id, artifact_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quant.quant_backtest_benchmark_worker_heartbeats (
        worker_id TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'idle',
        current_benchmark_id BIGINT,
        heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        meta JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
)


ALTER_TABLE_SQL: tuple[str, ...] = (
    "ALTER TABLE quant.market_token_metadata ADD COLUMN IF NOT EXISTS token_id_hex TEXT",
    "ALTER TABLE quant.market_token_block_close ADD COLUMN IF NOT EXISTS yes_probability_close NUMERIC(20, 10)",
    "ALTER TABLE quant.market_token_block_close ADD COLUMN IF NOT EXISTS block_timestamp TIMESTAMPTZ",
    "ALTER TABLE quant.market_token_block_close ADD COLUMN IF NOT EXISTS vwap_price NUMERIC(20, 10)",
    "ALTER TABLE quant.market_token_block_close ADD COLUMN IF NOT EXISTS yes_probability_vwap NUMERIC(20, 10)",
    "ALTER TABLE quant.market_token_block_close ADD COLUMN IF NOT EXISTS close_raw_price NUMERIC(20, 10)",
    "ALTER TABLE quant.market_token_block_close ADD COLUMN IF NOT EXISTS close_price_source TEXT NOT NULL DEFAULT 'unknown'",
    "ALTER TABLE quant.market_token_block_close ADD COLUMN IF NOT EXISTS close_maker_amount NUMERIC(38, 0)",
    "ALTER TABLE quant.market_token_block_close ADD COLUMN IF NOT EXISTS close_taker_amount NUMERIC(38, 0)",
    "ALTER TABLE quant.market_token_block_close ADD COLUMN IF NOT EXISTS raw_trade_count BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE quant.market_token_block_close ADD COLUMN IF NOT EXISTS internal_filtered_count BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE quant.market_token_block_close ADD COLUMN IF NOT EXISTS invalid_size_count BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE quant.market_token_block_close ADD COLUMN IF NOT EXISTS invalid_price_count BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE quant.market_token_block_close ADD COLUMN IF NOT EXISTS amount_ratio_count BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE quant.market_token_block_close ADD COLUMN IF NOT EXISTS raw_price_fallback_count BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE quant.market_token_block_close ADD COLUMN IF NOT EXISTS extreme_trade_count BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE quant.market_token_block_close ADD COLUMN IF NOT EXISTS anomaly_flags JSONB NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE quant.market_token_block_close ALTER COLUMN source SET DEFAULT 'clean_orderfilled_fact'",
    "ALTER TABLE quant.quant_price_series_tiles ADD COLUMN IF NOT EXISTS key_version INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE quant.quant_price_series_tiles ADD COLUMN IF NOT EXISTS entity_type TEXT NOT NULL DEFAULT 'event'",
    "ALTER TABLE quant.quant_price_series_tiles ADD COLUMN IF NOT EXISTS tile_kind TEXT NOT NULL DEFAULT 'series'",
    "ALTER TABLE quant.quant_price_series_tiles ADD COLUMN IF NOT EXISTS point_format TEXT NOT NULL DEFAULT 'lite'",
    "ALTER TABLE quant.quant_price_series_tiles ADD COLUMN IF NOT EXISTS window_from_x BIGINT",
    "ALTER TABLE quant.quant_price_series_tiles ADD COLUMN IF NOT EXISTS window_to_x BIGINT",
    "ALTER TABLE quant.quant_price_series_tiles ADD COLUMN IF NOT EXISTS cache_ttl_seconds INTEGER",
    "ALTER TABLE quant.quant_price_series_tiles ADD COLUMN IF NOT EXISTS updated_reason TEXT",
    "ALTER TABLE quant.quant_backtest_runs ADD COLUMN IF NOT EXISTS backtest_engine TEXT NOT NULL DEFAULT 'builtin'",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS fee_bps NUMERIC(20, 10) NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS slippage_bps NUMERIC(20, 10) NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS liquidity_cap_pct NUMERIC(20, 10) NOT NULL DEFAULT 100",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS max_position_notional NUMERIC(38, 10) NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS min_fill_pct NUMERIC(20, 10) NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS execution_price_mode TEXT NOT NULL DEFAULT 'ORDERFILLED_LIMIT_REPLAY'",
    "ALTER TABLE quant.quant_backtest_parameters ALTER COLUMN execution_price_mode SET DEFAULT 'ORDERFILLED_LIMIT_REPLAY'",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS execution_profile TEXT NOT NULL DEFAULT 'realistic'",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS order_role TEXT NOT NULL DEFAULT 'taker'",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS latency_blocks BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS adverse_slippage_cents NUMERIC(20, 10) NOT NULL DEFAULT 0.005",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS fill_probability_haircut_pct NUMERIC(20, 10) NOT NULL DEFAULT 20",
    "ALTER TABLE quant.quant_backtest_parameters ALTER COLUMN adverse_slippage_cents SET DEFAULT 0.005",
    "ALTER TABLE quant.quant_backtest_parameters ALTER COLUMN fill_probability_haircut_pct SET DEFAULT 20",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS latency_seconds NUMERIC(20, 10) NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS max_book_staleness_seconds NUMERIC(20, 10) NOT NULL DEFAULT 900",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS allow_partial_fill BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS min_fill_size NUMERIC(38, 10) NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS reject_on_stale_book BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS final_valuation_mode TEXT NOT NULL DEFAULT 'SETTLEMENT'",
    "ALTER TABLE quant.quant_backtest_parameters ALTER COLUMN final_valuation_mode SET DEFAULT 'SETTLEMENT'",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS max_entry_price NUMERIC(20, 10) NOT NULL DEFAULT 1",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS min_exit_price NUMERIC(20, 10) NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS buy_limit_price NUMERIC(20, 10)",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS sell_limit_price NUMERIC(20, 10)",
    "ALTER TABLE quant.quant_backtest_parameters ADD COLUMN IF NOT EXISTS settlement_value NUMERIC(20, 10)",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS requested_notional NUMERIC(38, 10) NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS filled_notional NUMERIC(38, 10) NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS requested_size NUMERIC(38, 10) NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS filled_size NUMERIC(38, 10) NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS unfilled_size NUMERIC(38, 10) NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS fill_pct NUMERIC(20, 10) NOT NULL DEFAULT 100",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS fill_status TEXT NOT NULL DEFAULT 'FILLED'",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS book_snapshot_id BIGINT",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS snapshot_version TEXT",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS staleness_seconds NUMERIC(20, 10)",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS staleness_blocks BIGINT",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS avg_fill_price NUMERIC(20, 10)",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS fill_probability NUMERIC(20, 10) NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS block_volume NUMERIC(38, 10) NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS trade_count BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS available_notional NUMERIC(38, 10) NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS execution_source TEXT NOT NULL DEFAULT 'unknown'",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS fee_cost NUMERIC(38, 10) NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS slippage_cost NUMERIC(38, 10) NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS execution_cost NUMERIC(38, 10) NOT NULL DEFAULT 0",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS entry_order_id TEXT",
    "ALTER TABLE quant.quant_backtest_trades ADD COLUMN IF NOT EXISTS exit_order_id TEXT",
    "ALTER TABLE quant.market_event_members ADD COLUMN IF NOT EXISTS grouping_confidence TEXT NOT NULL DEFAULT 'official'",
    "ALTER TABLE quant.market_event_metadata ADD COLUMN IF NOT EXISTS grouping_confidence TEXT NOT NULL DEFAULT 'official'",
    "ALTER TABLE quant.clob_orderbook_snapshots ADD COLUMN IF NOT EXISTS block_number BIGINT",
    "ALTER TABLE quant.clob_orderbook_snapshots ADD COLUMN IF NOT EXISTS snapshot_timestamp TIMESTAMPTZ",
    "ALTER TABLE quant.clob_orderbook_snapshots ADD COLUMN IF NOT EXISTS snapshot_version TEXT",
    "ALTER TABLE quant.clob_orderbook_snapshots ADD COLUMN IF NOT EXISTS market_id BIGINT",
    "ALTER TABLE quant.clob_orderbook_snapshots ADD COLUMN IF NOT EXISTS condition_id TEXT",
    "ALTER TABLE quant.clob_orderbook_snapshots ADD COLUMN IF NOT EXISTS market_slug TEXT",
    "ALTER TABLE quant.clob_orderbook_snapshots ADD COLUMN IF NOT EXISTS snapshot_source TEXT",
    "ALTER TABLE quant.clob_orderbook_snapshots ADD COLUMN IF NOT EXISTS storage_tier TEXT NOT NULL DEFAULT 'unknown'",
    "ALTER TABLE quant.clob_orderbook_snapshots ADD COLUMN IF NOT EXISTS book_generation BIGINT",
    "ALTER TABLE quant.clob_orderbook_snapshots ADD COLUMN IF NOT EXISTS last_event_ts_ms BIGINT",
)


DATA_MIGRATION_SQL: tuple[str, ...] = (
    """
    UPDATE quant.clob_orderbook_snapshots
    SET snapshot_timestamp = fetched_at
    WHERE snapshot_timestamp IS NULL
      AND fetched_at IS NOT NULL
    """,
    """
    UPDATE quant.clob_orderbook_snapshots
    SET
        market_id = COALESCE(
            market_id,
            CASE
                WHEN (payload->>'market_id') ~ '^[0-9]+$' THEN (payload->>'market_id')::bigint
                WHEN (payload->>'marketId') ~ '^[0-9]+$' THEN (payload->>'marketId')::bigint
                ELSE NULL
            END
        ),
        condition_id = COALESCE(condition_id, NULLIF(payload->>'condition_id', ''), NULLIF(payload->>'conditionId', '')),
        market_slug = COALESCE(market_slug, NULLIF(payload->>'market_slug', ''), NULLIF(payload->>'marketSlug', '')),
        snapshot_source = COALESCE(snapshot_source, NULLIF(payload->>'snapshot_source', ''), NULLIF(payload->>'snapshotSource', '')),
        storage_tier = COALESCE(NULLIF(storage_tier, 'unknown'), NULLIF(payload->'coverage'->>'tier', ''), 'unknown'),
        book_generation = COALESCE(
            book_generation,
            CASE WHEN (payload->>'generation') ~ '^[0-9]+$' THEN (payload->>'generation')::bigint ELSE NULL END
        ),
        last_event_ts_ms = COALESCE(
            last_event_ts_ms,
            CASE
                WHEN (payload->>'last_event_ts_ms') ~ '^[0-9]+$' THEN (payload->>'last_event_ts_ms')::bigint
                WHEN (payload->>'lastEventTsMs') ~ '^[0-9]+$' THEN (payload->>'lastEventTsMs')::bigint
                ELSE NULL
            END
        )
    WHERE (market_id IS NULL AND ((payload->>'market_id') ~ '^[0-9]+$' OR (payload->>'marketId') ~ '^[0-9]+$'))
       OR (condition_id IS NULL AND (NULLIF(payload->>'condition_id', '') IS NOT NULL OR NULLIF(payload->>'conditionId', '') IS NOT NULL))
       OR (market_slug IS NULL AND (NULLIF(payload->>'market_slug', '') IS NOT NULL OR NULLIF(payload->>'marketSlug', '') IS NOT NULL))
       OR (snapshot_source IS NULL AND (NULLIF(payload->>'snapshot_source', '') IS NOT NULL OR NULLIF(payload->>'snapshotSource', '') IS NOT NULL))
       OR (storage_tier = 'unknown' AND NULLIF(payload->'coverage'->>'tier', '') IS NOT NULL)
       OR (book_generation IS NULL AND (payload->>'generation') ~ '^[0-9]+$')
       OR (last_event_ts_ms IS NULL AND ((payload->>'last_event_ts_ms') ~ '^[0-9]+$' OR (payload->>'lastEventTsMs') ~ '^[0-9]+$'))
    """,
)


CREATE_INDEX_SQL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_quant_metadata_slug_side ON quant.market_token_metadata (market_slug, token_side)",
    "CREATE INDEX IF NOT EXISTS idx_quant_metadata_market_side ON quant.market_token_metadata (market_id, token_side)",
    "CREATE INDEX IF NOT EXISTS idx_quant_metadata_token_hex ON quant.market_token_metadata (token_id_hex)",
    "CREATE INDEX IF NOT EXISTS idx_quant_metadata_dates ON quant.market_token_metadata (created_at, end_date)",
    "CREATE INDEX IF NOT EXISTS idx_quant_metadata_frontend_shard6_created ON quant.market_token_metadata ((mod(abs(hashtext(token_id)), 6)), created_at, market_id, token_id)",
    "CREATE INDEX IF NOT EXISTS idx_quant_metadata_frontend_shard6_active_updated ON quant.market_token_metadata ((mod(abs(hashtext(token_id)), 6)), active, closed, updated_at DESC, created_at DESC, market_id, token_id)",
    "CREATE INDEX IF NOT EXISTS idx_quant_metadata_live_dates ON quant.market_token_metadata (active, closed, end_date, created_at, market_id) WHERE token_id_hex IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_quant_metadata_end_live ON quant.market_token_metadata (end_date, created_at, market_id) WHERE token_id_hex IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_quant_metadata_duplicate_rank ON quant.market_token_metadata (duplicate_group_key, token_side, created_at, market_id) WHERE duplicate_group_key IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_quant_eligibility_eligible ON quant.market_price_eligibility (eligible, market_id)",
    "CREATE INDEX IF NOT EXISTS idx_quant_eligibility_block_watermark ON quant.market_price_eligibility (eligible, last_orderfilled_block, market_id) WHERE eligible = TRUE AND last_orderfilled_block IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_quant_eligibility_trade_count ON quant.market_price_eligibility (eligible, orderfilled_trade_count DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_frontend_slug_side_time ON quant.market_token_frontend_price_1m (market_slug, token_side, ts_minute)",
    "CREATE INDEX IF NOT EXISTS idx_quant_frontend_market_side_time ON quant.market_token_frontend_price_1m (market_id, token_side, ts_minute)",
    "CREATE INDEX IF NOT EXISTS idx_quant_block_close_slug_side_block ON quant.market_token_block_close (market_slug, token_side, block_number)",
    "CREATE INDEX IF NOT EXISTS idx_quant_block_close_market_side_block ON quant.market_token_block_close (market_id, token_side, block_number)",
    "CREATE INDEX IF NOT EXISTS idx_quant_block_close_token_time ON quant.market_token_block_close (token_id, block_timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_quant_build_state_status ON quant.market_price_build_market_state (source, status, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_quant_build_state_frontend_watermark ON quant.market_price_build_market_state (last_complete_ts, token_id) WHERE source = 'frontend' AND status NOT IN ('skipped', 'deferred')",
    "CREATE INDEX IF NOT EXISTS idx_quant_build_state_frontend_shard6_watermark ON quant.market_price_build_market_state ((mod(abs(hashtext(token_id)), 6)), last_complete_ts, token_id) WHERE source = 'frontend' AND status NOT IN ('skipped', 'deferred')",
    "CREATE INDEX IF NOT EXISTS idx_quant_build_state_frontend_retry_shard6 ON quant.market_price_build_market_state (status, (mod(abs(hashtext(token_id)), 6)), last_complete_ts, token_id) WHERE source = 'frontend' AND status IN ('skipped', 'deferred')",
    "CREATE INDEX IF NOT EXISTS idx_quant_build_state_block_watermark ON quant.market_price_build_market_state (source, last_complete_block, token_id) WHERE source = 'orderfilled_block_close'",
    "CREATE INDEX IF NOT EXISTS idx_quant_market_progress_status ON quant.market_price_build_market_progress (status, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_quant_orderfilled_market_stats_blocks ON quant.market_orderfilled_market_stats (first_orderfilled_block, last_orderfilled_block)",
    "CREATE INDEX IF NOT EXISTS idx_quant_orderfilled_token_stats_token ON quant.market_orderfilled_token_stats (token_id_hex)",
    "CREATE INDEX IF NOT EXISTS idx_quant_build_targets_active ON quant.market_price_build_targets (source, status, priority DESC, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_build_targets_slug_side ON quant.market_price_build_targets (market_slug, token_side, source)",
    "CREATE INDEX IF NOT EXISTS idx_quant_clob_snapshots_token_time ON quant.clob_orderbook_snapshots (token_id, fetched_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_clob_snapshots_token_snapshot_time ON quant.clob_orderbook_snapshots (token_id, snapshot_timestamp DESC, snapshot_id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_clob_snapshots_token_block ON quant.clob_orderbook_snapshots (token_id, block_number DESC, snapshot_id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_clob_snapshots_side_time ON quant.clob_orderbook_snapshots (side, fetched_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_clob_snapshots_status_time ON quant.clob_orderbook_snapshots (book_status, fetched_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_clob_snapshots_market_side_time ON quant.clob_orderbook_snapshots (market_id, side, fetched_at DESC) WHERE market_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_quant_clob_snapshots_storage_time ON quant.clob_orderbook_snapshots (storage_tier, fetched_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_clob_snapshots_source_time ON quant.clob_orderbook_snapshots (snapshot_source, fetched_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_lob_rollups_market_time ON quant.clob_orderbook_rollups_1m (market_id, side, bucket_minute DESC) WHERE market_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_quant_lob_rollups_storage_time ON quant.clob_orderbook_rollups_1m (storage_tier, bucket_minute DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_lob_dead_letters_created ON quant.clob_orderbook_dead_letters (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_lob_dead_letters_reason ON quant.clob_orderbook_dead_letters (reason, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_backtest_runs_status ON quant.quant_backtest_runs (status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_backtest_runs_market ON quant.quant_backtest_runs (market_slug, token_side, price_source, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_backtest_runs_engine ON quant.quant_backtest_runs (backtest_engine, status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_backtest_trades_run_pnl ON quant.quant_backtest_trades (run_id, pnl)",
    "CREATE INDEX IF NOT EXISTS idx_quant_backtest_orders_run_status ON quant.quant_backtest_orders (run_id, status, submit_x)",
    "CREATE INDEX IF NOT EXISTS idx_quant_backtest_orders_trade ON quant.quant_backtest_orders (run_id, trade_id)",
    "CREATE INDEX IF NOT EXISTS idx_quant_backtest_ledger_run_event ON quant.quant_backtest_ledger (run_id, event_type, x_value)",
    "CREATE INDEX IF NOT EXISTS idx_quant_backtest_ledger_trade ON quant.quant_backtest_ledger (run_id, trade_id)",
    "CREATE INDEX IF NOT EXISTS idx_quant_backtest_equity_run_x ON quant.quant_backtest_equity (run_id, point_index)",
    "CREATE INDEX IF NOT EXISTS idx_quant_backtest_benchmark_runs_created ON quant.quant_backtest_benchmark_runs (created_at DESC, benchmark_id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_backtest_benchmark_runs_universe ON quant.quant_backtest_benchmark_runs (universe_name, status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_backtest_benchmark_runs_queue ON quant.quant_backtest_benchmark_runs (status, created_at ASC, benchmark_id ASC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_backtest_benchmark_workers_heartbeat ON quant.quant_backtest_benchmark_worker_heartbeats (heartbeat_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_backtest_benchmark_rows_quality ON quant.quant_backtest_benchmark_rows (benchmark_id, data_quality, row_index)",
    "CREATE INDEX IF NOT EXISTS idx_quant_backtest_benchmark_rows_market ON quant.quant_backtest_benchmark_rows (market_slug, benchmark_id)",
    "CREATE INDEX IF NOT EXISTS idx_quant_event_metadata_search ON quant.market_event_metadata (event_slug, event_title)",
    "CREATE INDEX IF NOT EXISTS idx_quant_event_metadata_status ON quant.market_event_metadata (status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_event_members_market ON quant.market_event_members (market_id)",
    "CREATE INDEX IF NOT EXISTS idx_quant_event_members_coverage ON quant.market_event_members (event_slug, coverage_status, outcome_order)",
    "CREATE INDEX IF NOT EXISTS idx_quant_event_members_active ON quant.market_event_members (active, closed, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_price_series_tiles_lookup ON quant.quant_price_series_tiles (scope, entity_slug, price_source, range_name, resolution, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_price_series_tiles_entity ON quant.quant_price_series_tiles (entity_type, entity_slug, price_source, tile_kind, range_name, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_price_series_tiles_window ON quant.quant_price_series_tiles (entity_type, entity_slug, price_source, tile_kind, window_from_x, window_to_x, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quant_price_series_tiles_expiry ON quant.quant_price_series_tiles (expires_at)",
)


OPTIONAL_SEARCH_INDEX_SQL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_quant_metadata_slug_trgm ON quant.market_token_metadata USING gin (lower(market_slug) gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_quant_metadata_title_trgm ON quant.market_token_metadata USING gin (lower(market_title) gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_quant_market_progress_slug_trgm ON quant.market_price_build_market_progress USING gin (lower(market_slug) gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_quant_event_metadata_slug_trgm ON quant.market_event_metadata USING gin (lower(event_slug) gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_quant_event_metadata_title_trgm ON quant.market_event_metadata USING gin (lower(event_title) gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_quant_event_members_slug_trgm ON quant.market_event_members USING gin (lower(market_slug) gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_quant_event_members_question_trgm ON quant.market_event_members USING gin (lower(question) gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_quant_event_members_outcome_trgm ON quant.market_event_members USING gin (lower(outcome_label) gin_trgm_ops)",
)


ADD_COLUMN_RE = re.compile(r"^ALTER TABLE (?P<table>[a-z_]+\.[a-z_]+) ADD COLUMN IF NOT EXISTS (?P<column>[a-z_]+)\b", re.IGNORECASE)
INDEX_RE = re.compile(r"^CREATE INDEX IF NOT EXISTS (?P<index>[a-z_][a-z0-9_]*)\b", re.IGNORECASE)


def _column_exists(conn: Any, table: str, column: str) -> bool:
    schema_name, table_name = table.split(".", 1)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s AND column_name = %s
            LIMIT 1
            """,
            (schema_name, table_name, column),
        )
        return cur.fetchone() is not None


def _index_exists(conn: Any, index_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS exists", (f"quant.{index_name}",))
        row = cur.fetchone()
        return bool(row and row["exists"])


def _should_skip_statement(conn: Any, statement: str) -> bool:
    text = " ".join(statement.strip().split())
    add_match = ADD_COLUMN_RE.match(text)
    if add_match and _column_exists(conn, add_match.group("table"), add_match.group("column")):
        return True
    index_match = INDEX_RE.match(text)
    if index_match and _index_exists(conn, index_match.group("index")):
        return True
    if text.upper().startswith("ALTER TABLE QUANT.MARKET_TOKEN_BLOCK_CLOSE ALTER COLUMN SOURCE SET DEFAULT"):
        return True
    return False


def _execute_optional_ddl(conn: Any, statement: str, *, lock_timeout_ms: int = 1500) -> bool:
    """Run optional search DDL without blocking core API/backtest schema setup."""

    with conn.cursor() as cur:
        cur.execute("SAVEPOINT optional_search_ddl")
        try:
            cur.execute(f"SET LOCAL lock_timeout = '{int(lock_timeout_ms)}ms'")
            cur.execute(statement)
            cur.execute("RELEASE SAVEPOINT optional_search_ddl")
            return True
        except Exception as exc:
            cur.execute("ROLLBACK TO SAVEPOINT optional_search_ddl")
            cur.execute("RELEASE SAVEPOINT optional_search_ddl")
            sqlstate = str(getattr(exc, "sqlstate", "") or "")
            if sqlstate in {"55P03", "42501", "42704"}:
                return False
            raise


def create_schema(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(914020250607)")
        cur.execute(CREATE_SCHEMA_SQL)
        for statement in CREATE_TABLE_SQL:
            cur.execute(statement)
    for statement in ALTER_TABLE_SQL:
        if _should_skip_statement(conn, statement):
            continue
        with conn.cursor() as cur:
            cur.execute(statement)
    for statement in DATA_MIGRATION_SQL:
        with conn.cursor() as cur:
            cur.execute(statement)
    for statement in CREATE_INDEX_SQL:
        if _should_skip_statement(conn, statement):
            continue
        with conn.cursor() as cur:
            cur.execute(statement)
    for statement in OPTIONAL_EXTENSION_SQL:
        _execute_optional_ddl(conn, statement)
    for statement in OPTIONAL_SEARCH_INDEX_SQL:
        if _should_skip_statement(conn, statement):
            continue
        _execute_optional_ddl(conn, statement)
