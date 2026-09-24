# Polymarket Backtesting Accuracy

This backtester is designed to be deterministic and no-lookahead. A strategy
signal is evaluated on historical price rows, then execution is simulated from
historical Polymarket-specific fill evidence. The default model is OrderFilled
participation, not all-market CLOB replay.

## Execution Model

- `execution_price_mode=ORDERFILLED` uses `quant.market_token_block_close`
  close price, volume, and trade count. It estimates deterministic fill
  probability from the configured participation cap:
  `available_notional = block_volume * liquidity_cap_pct`.
- OrderFilled execution rejects zero-volume blocks, supports partial fills, and
  records `fill_probability`, `block_volume`, `trade_count`, and
  `available_notional` on fill events and closed trades.
- `execution_price_mode=DEPTH` is optional. It uses
  `quant.clob_orderbook_snapshots` when a specific market has useful historical
  depth. BUY YES consumes ask levels; SELL YES consumes bid levels.
- CLOB snapshot lookup never uses a future book.
- `latency_seconds` shifts the execution timestamp forward before CLOB lookup.
- `max_book_staleness_seconds` marks old CLOB books as stale.
- `reject_on_stale_book=true` rejects fills from stale CLOB books.
- `allow_partial_fill=false` rejects orders that cannot fully fill.
- `min_fill_size` and `min_fill_pct` reject dust or insufficient fills.
- Buy fills are capped by the configured position cash budget.
- Exit fills are capped by the open position size.
- Fees and slippage are applied after the selected fill model.

`execution_price_mode=LEGACY` is retained for comparison with older runs. It
uses close price plus bps costs and a looser volume cap, and should not be
treated as the primary historical Polymarket model.

## Reproducibility

Each run stores:

- run id and selected framework
- full strategy parameter snapshot
- price row checksum/data version
- execution model summary/version
- gap and jump report
- trade-level fill status, filled size, fill probability, source volume, and
  average fill

The run can be reproduced as long as the exact price rows remain in the
database. DEPTH-mode runs additionally require the referenced CLOB snapshots.

## Current Accuracy Boundary

This is suitable for internal research and liquidity-aware backtests using
OrderFilled history. It is intentionally more scalable than full-market CLOB
collection, but it is still not equivalent to a complete live exchange simulator.

Known remaining limits:

- queue position is not simulated
- maker/taker order lifecycle and cancellation are not simulated
- network latency is modeled as a fixed parameter
- OrderFilled volume measures observed fills in a block, not your exact queue
  priority inside that block
- DEPTH accuracy depends on snapshot sampling coverage when that optional mode
  is selected
- market resolution/settlement is not a full accounting engine yet

For broad Polymarket historical research, prefer `ORDERFILLED`, use conservative
`liquidity_cap_pct`, inspect gap reports, and reject runs with poor row coverage.
Use `DEPTH` only for markets where you intentionally maintain depth snapshots.
