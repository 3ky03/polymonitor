CREATE DATABASE IF NOT EXISTS poly_orderfilled;

USE poly_orderfilled;

CREATE TABLE IF NOT EXISTS orderfilled_fact
(
    tx_hash String CODEC(ZSTD(3)),
    log_index UInt32 CODEC(Delta, ZSTD(3)),
    market_id UInt64 CODEC(Delta, ZSTD(3)),
    condition_id String CODEC(ZSTD(3)),
    token_id String CODEC(ZSTD(3)),
    outcome_code UInt8 CODEC(ZSTD(3)),
    maker String CODEC(ZSTD(3)),
    taker String CODEC(ZSTD(3)),
    side_code UInt8 CODEC(ZSTD(3)),
    price Decimal(20, 10) CODEC(ZSTD(3)),
    size Decimal(30, 10) CODEC(ZSTD(3)),
    block_number UInt64 CODEC(Delta, ZSTD(3)),
    order_hash String CODEC(ZSTD(3)),
    contract LowCardinality(String) CODEC(ZSTD(3)),
    maker_amount Nullable(UInt64) CODEC(ZSTD(3)),
    taker_amount Nullable(UInt64) CODEC(ZSTD(3)),
    fee Nullable(UInt64) CODEC(ZSTD(3)),
    ingested_at DateTime DEFAULT now() CODEC(Delta, ZSTD(3)),
    INDEX idx_tx_hash tx_hash TYPE bloom_filter(0.01) GRANULARITY 64,
    INDEX idx_maker maker TYPE bloom_filter(0.01) GRANULARITY 64,
    INDEX idx_taker taker TYPE bloom_filter(0.01) GRANULARITY 64
)
ENGINE = MergeTree
PARTITION BY intDiv(block_number, 1000000)
ORDER BY (market_id, token_id, block_number, log_index, tx_hash)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS orderfilled_fact_buffer AS orderfilled_fact
ENGINE = Buffer(currentDatabase(), orderfilled_fact, 16, 2, 10, 10000, 200000, 1048576, 33554432);

CREATE TABLE IF NOT EXISTS address_trade_cashflows
(
    address String CODEC(ZSTD(3)),
    tx_hash String CODEC(ZSTD(3)),
    log_index UInt32 CODEC(Delta, ZSTD(3)),
    market_id UInt64 CODEC(Delta, ZSTD(3)),
    condition_id String CODEC(ZSTD(3)),
    token_id String CODEC(ZSTD(3)),
    outcome_code UInt8 CODEC(ZSTD(3)),
    side_code UInt8 CODEC(ZSTD(3)),
    usdc_amount Decimal(38, 18) CODEC(ZSTD(3)),
    size Decimal(30, 10) CODEC(ZSTD(3)),
    price Decimal(20, 10) CODEC(ZSTD(3)),
    role UInt8 CODEC(ZSTD(3)),
    counterparty String CODEC(ZSTD(3)),
    contract LowCardinality(String) CODEC(ZSTD(3)),
    block_number UInt64 CODEC(Delta, ZSTD(3)),
    source LowCardinality(String) DEFAULT 'orderfilled_fact' CODEC(ZSTD(3)),
    ingested_at DateTime DEFAULT now() CODEC(Delta, ZSTD(3)),
    INDEX idx_tx_hash tx_hash TYPE bloom_filter(0.01) GRANULARITY 64,
    INDEX idx_market_id market_id TYPE minmax GRANULARITY 1
)
ENGINE = MergeTree
PARTITION BY intDiv(block_number, 1000000)
ORDER BY (address, block_number, log_index, tx_hash, role)
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS address_trade_cashflows_maker_mv
TO address_trade_cashflows
AS
SELECT
    maker AS address,
    tx_hash,
    log_index,
    market_id,
    condition_id,
    token_id,
    outcome_code,
    side_code,
    CAST(
        if(side_code = 1, ifNull(maker_amount, 0), ifNull(taker_amount, 0)),
        'Decimal(38, 0)'
    ) / CAST(1000000, 'Decimal(38, 0)') AS usdc_amount,
    size,
    price,
    toUInt8(1) AS role,
    taker AS counterparty,
    contract,
    block_number,
    'orderfilled_fact' AS source,
    now() AS ingested_at
FROM orderfilled_fact
WHERE maker NOT IN
(
    '4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e',
    'c5d563a36ae78145c45a50134d48a1215220f80a',
    'e111180000d2663c0091e4f400237545b87b996b'
);

CREATE MATERIALIZED VIEW IF NOT EXISTS address_trade_cashflows_taker_mv
TO address_trade_cashflows
AS
SELECT
    taker AS address,
    tx_hash,
    log_index,
    market_id,
    condition_id,
    token_id,
    outcome_code,
    multiIf(side_code = 1, toUInt8(2), side_code = 2, toUInt8(1), toUInt8(0)) AS side_code,
    CAST(
        if(side_code = 1, ifNull(maker_amount, 0), ifNull(taker_amount, 0)),
        'Decimal(38, 0)'
    ) / CAST(1000000, 'Decimal(38, 0)') AS usdc_amount,
    size,
    price,
    toUInt8(2) AS role,
    maker AS counterparty,
    contract,
    block_number,
    'orderfilled_fact' AS source,
    now() AS ingested_at
FROM orderfilled_fact
WHERE taker NOT IN
(
    '4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e',
    'c5d563a36ae78145c45a50134d48a1215220f80a',
    'e111180000d2663c0091e4f400237545b87b996b'
);

CREATE TABLE IF NOT EXISTS pnl_trade_cashflows_erc20_compact
(
    tx_hash String CODEC(ZSTD(3)),
    address String CODEC(ZSTD(3)),
    collateral_token String CODEC(ZSTD(3)),
    signed_amount Int64 CODEC(ZSTD(3)),
    block_number UInt64 CODEC(Delta, ZSTD(3)),
    first_log_index UInt32 CODEC(Delta, ZSTD(3)),
    last_log_index UInt32 CODEC(Delta, ZSTD(3)),
    transfer_count UInt16 CODEC(ZSTD(3)),
    orderfilled_count UInt16 CODEC(ZSTD(3)),
    ingested_at DateTime DEFAULT now() CODEC(Delta, ZSTD(3)),
    INDEX idx_tx_hash tx_hash TYPE bloom_filter(0.01) GRANULARITY 64,
    INDEX idx_collateral_token collateral_token TYPE bloom_filter(0.01) GRANULARITY 64
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY intDiv(block_number, 1000000)
ORDER BY (address, block_number, tx_hash, collateral_token)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS non_trade_cashflows
(
    event_key String CODEC(ZSTD(3)),
    address String CODEC(ZSTD(3)),
    cashflow_type LowCardinality(String) CODEC(ZSTD(3)),
    usdc_amount Decimal(38, 18) CODEC(ZSTD(3)),
    collateral_token String CODEC(ZSTD(3)),
    condition_id String CODEC(ZSTD(3)),
    parent_collection_id String CODEC(ZSTD(3)),
    partition_json String CODEC(ZSTD(3)),
    tx_hash String CODEC(ZSTD(3)),
    log_index UInt32 CODEC(Delta, ZSTD(3)),
    block_number UInt64 CODEC(Delta, ZSTD(3)),
    source_contract String CODEC(ZSTD(3)),
    source_event LowCardinality(String) CODEC(ZSTD(3)),
    source LowCardinality(String) CODEC(ZSTD(3)),
    ingested_at DateTime DEFAULT now() CODEC(Delta, ZSTD(3)),
    INDEX idx_tx_hash tx_hash TYPE bloom_filter(0.01) GRANULARITY 64
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY intDiv(block_number, 1000000)
ORDER BY (address, cashflow_type, block_number, tx_hash, log_index);

CREATE TABLE IF NOT EXISTS position_snapshots
(
    address String CODEC(ZSTD(3)),
    snapshot_block UInt64 CODEC(Delta, ZSTD(3)),
    unrealized_position_value Decimal(38, 18) CODEC(ZSTD(3)),
    open_positions UInt32 CODEC(ZSTD(3)),
    source LowCardinality(String) CODEC(ZSTD(3)),
    ingested_at DateTime DEFAULT now() CODEC(Delta, ZSTD(3)),
    INDEX idx_address address TYPE bloom_filter(0.01) GRANULARITY 64
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY intDiv(snapshot_block, 1000000)
ORDER BY (address, snapshot_block);

DROP VIEW IF EXISTS address_pnl_with_latest_position;
DROP VIEW IF EXISTS address_pnl_cashflow_summary;
DROP VIEW IF EXISTS address_pnl_cashflow_events;

CREATE VIEW address_pnl_cashflow_events AS
SELECT
    address,
    block_number,
    tx_hash,
    first_log_index AS log_index,
    toUInt64(0) AS market_id,
    '' AS condition_id,
    collateral_token AS token_id,
    if(signed_amount > 0, 'SELL', 'BUY') AS cashflow_type,
    toDecimal128(signed_amount, 6) / toDecimal128(1000000, 6) AS signed_usdc_amount,
    abs(toDecimal128(signed_amount, 6)) / toDecimal128(1000000, 6) AS usdc_amount,
    'pnl_trade_cashflows_erc20_compact' AS source
FROM pnl_trade_cashflows_erc20_compact
WHERE signed_amount != 0
UNION ALL
SELECT
    address,
    block_number,
    tx_hash,
    log_index,
    toUInt64(0) AS market_id,
    condition_id,
    '' AS token_id,
    cashflow_type,
    multiIf(
        cashflow_type IN ('REDEEM', 'MERGE', 'MAKER_REBATE'), usdc_amount,
        cashflow_type = 'SPLIT', -usdc_amount,
        CAST(0, 'Decimal(38, 18)')
    ) AS signed_usdc_amount,
    usdc_amount,
    source
FROM non_trade_cashflows
WHERE cashflow_type IN ('REDEEM', 'MERGE', 'SPLIT', 'MAKER_REBATE');

CREATE VIEW address_pnl_cashflow_summary AS
SELECT
    address,
    sum(signed_usdc_amount) AS realized_cashflow_pnl,
    sumIf(usdc_amount, cashflow_type = 'SELL') AS sell_usdc,
    sumIf(usdc_amount, cashflow_type = 'BUY') AS buy_usdc,
    sumIf(usdc_amount, cashflow_type = 'REDEEM') AS redeem_usdc,
    sumIf(usdc_amount, cashflow_type = 'MERGE') AS merge_usdc,
    sumIf(usdc_amount, cashflow_type = 'MAKER_REBATE') AS maker_rebate_usdc,
    sumIf(usdc_amount, cashflow_type = 'SPLIT') AS split_usdc,
    count() AS cashflow_rows
FROM address_pnl_cashflow_events
GROUP BY address;

CREATE VIEW address_pnl_with_latest_position AS
SELECT
    c.address,
    c.realized_cashflow_pnl,
    ifNull(p.unrealized_position_value, CAST(0, 'Decimal(38, 18)')) AS unrealized_position_value,
    c.realized_cashflow_pnl + ifNull(p.unrealized_position_value, CAST(0, 'Decimal(38, 18)')) AS trading_pnl,
    c.sell_usdc,
    c.buy_usdc,
    c.redeem_usdc,
    c.merge_usdc,
    c.maker_rebate_usdc,
    c.split_usdc,
    c.cashflow_rows,
    ifNull(p.latest_snapshot_block, toUInt64(0)) AS position_snapshot_block
FROM address_pnl_cashflow_summary c
LEFT JOIN
(
    SELECT
        address,
        argMax(unrealized_position_value, snapshot_block) AS unrealized_position_value,
        max(snapshot_block) AS latest_snapshot_block
    FROM position_snapshots
    GROUP BY address
) p ON p.address = c.address;

CREATE TABLE IF NOT EXISTS orderfilled_migration_windows
(
    table_name LowCardinality(String),
    from_block UInt64,
    to_block UInt64,
    source_count UInt64,
    inserted_count UInt64,
    target_count UInt64,
    status LowCardinality(String),
    started_at DateTime,
    finished_at DateTime,
    last_error String CODEC(ZSTD(3))
)
ENGINE = ReplacingMergeTree(finished_at)
ORDER BY (table_name, from_block, to_block);

CREATE TABLE IF NOT EXISTS block_timestamps
(
    block_number UInt64 CODEC(Delta, ZSTD(3)),
    block_time DateTime64(0, 'UTC') CODEC(Delta, ZSTD(3)),
    block_hash String CODEC(ZSTD(3)),
    source LowCardinality(String) DEFAULT 'rpc' CODEC(ZSTD(3)),
    ingested_at DateTime DEFAULT now() CODEC(Delta, ZSTD(3)),
    INDEX idx_block_time block_time TYPE minmax GRANULARITY 1
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY block_number
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS query_benchmark_results
(
    benchmark_name LowCardinality(String),
    query_label LowCardinality(String),
    elapsed_ms Float64,
    rows_read UInt64,
    bytes_read UInt64,
    result_rows UInt64,
    success UInt8,
    error String CODEC(ZSTD(3)),
    query_text String CODEC(ZSTD(3)),
    measured_at DateTime DEFAULT now() CODEC(Delta, ZSTD(3))
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(measured_at)
ORDER BY (benchmark_name, measured_at, query_label);
