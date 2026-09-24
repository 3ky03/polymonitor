import type {
  QuantBacktestEquityPoint,
  QuantBacktestLedgerRow,
  QuantBacktestMetric,
  QuantBacktestOrder,
  QuantBacktestRun,
  QuantBacktestTrade,
  QuantBlockClosePoint,
  QuantFrontendPricePoint,
  QuantMarketSeriesPayload,
  QuantMarketSeriesPoint,
} from '@/types';
import type { BacktestMetric, BacktestResult, EquityPoint, LedgerRow, OrderLifecycleRow, PerformanceRow, PricePoint, PriceSource, PropertyGroup, Trade } from '../types';
import { deriveEventOutcomeLabel, toNumber } from './formatters';

export function frontendToPrices(rows: QuantFrontendPricePoint[]): PricePoint[] {
  return rows.map((row) => ({
    timestamp: Number(row.timestamp),
    close: toNumber(row.price),
    volume: 0,
    source: 'frontend',
  })).filter((row) => row.timestamp && Number.isFinite(row.close));
}

export function blockToPrices(rows: QuantBlockClosePoint[]): PricePoint[] {
  return rows.map((row) => ({
    timestamp: Number(row.blockNumber),
    close: toNumber(row.closePrice ?? row.yesProbabilityClose),
    volume: toNumber(row.volume),
    tokenSide: row.tokenSide,
    source: 'orderfilled_block_close',
  })).filter((row) => row.timestamp && Number.isFinite(row.close));
}

export function marketSeriesToPrices(payload: QuantMarketSeriesPayload | null | undefined): PricePoint[] {
  if (!payload) return [];
  const source = payload.event?.source || payload.market?.source || 'quant_market_series';
  const eventTitle = payload.event?.eventTitle || payload.market?.marketTitle || '';
  const rawOutcomes = payload.outcomes || [];
  const canonicalKeys = new Set(
    rawOutcomes
      .filter((outcome) => String(outcome.buyYesTokenSide || outcome.tokenSide || '').toUpperCase() === 'YES')
      .map((outcome) => `${outcome.marketId || outcome.marketSlug || outcome.outcomeKey || outcome.outcomeLabel}`),
  );
  const canonicalOutcomes = rawOutcomes.filter((outcome) => {
    const side = String(outcome.buyYesTokenSide || outcome.tokenSide || '').toUpperCase();
    const key = `${outcome.marketId || outcome.marketSlug || outcome.outcomeKey || outcome.outcomeLabel}`;
    return side !== 'NO' || !canonicalKeys.has(key);
  });

  const optionalProbability = (value: unknown) => {
    if (value === null || value === undefined || value === '') return null;
    const numeric = Number(value);
    return Number.isFinite(numeric) && numeric >= 0 && numeric <= 1 ? numeric : null;
  };
  const canonicalYes = (point: QuantMarketSeriesPoint, fallbackSide?: string | null) => {
    const direct = optionalProbability(point.yesProbabilityClose);
    if (direct !== null) return direct;
    const raw = toNumber(point.price);
    const side = String(point.tokenSide || fallbackSide || '').toUpperCase();
    return side === 'NO' ? Math.max(0, Math.min(1, 1 - raw)) : raw;
  };
  const directTokenPrice = (point: QuantMarketSeriesPoint) => toNumber(point.price);

  return canonicalOutcomes.flatMap((outcome) => (
    (() => {
      const fullLabel = outcome.marketTitle || outcome.outcomeLabel || outcome.marketSlug || 'Outcome';
      const shortLabel = deriveEventOutcomeLabel(eventTitle, fullLabel, outcome.outcomeLabel);
      const outcomeKey = outcome.outcomeKey || `${outcome.marketSlug || outcome.marketId || shortLabel}`;
      const complementByX = new Map<string, number>();
      (outcome.complementPoints || []).forEach((point) => {
        const x = String(point.x ?? point.blockNumber ?? point.timestamp ?? '');
        if (x) complementByX.set(x, directTokenPrice(point));
      });
      const yesRows = (outcome.points || []).map((point) => {
        const x = String(point.x ?? point.blockNumber ?? point.timestamp ?? '');
        const yesPrice = canonicalYes(point, outcome.buyYesTokenSide || outcome.tokenSide);
        const directNo = complementByX.get(x);
        const hasDirectNo = typeof directNo === 'number' && Number.isFinite(directNo);
        const noPrice = hasDirectNo ? directNo : Math.max(0, Math.min(1, 1 - yesPrice));
        return {
          timestamp: Number(point.x ?? point.blockNumber ?? point.timestamp),
          close: yesPrice,
          volume: toNumber(point.volume),
          source,
          tokenId: outcome.buyYesTokenId || outcome.tokenId,
          tokenSide: outcome.buyYesTokenSide || 'YES',
          outcomeLabel: shortLabel,
          outcomeShortLabel: shortLabel,
          outcomeFullLabel: fullLabel,
          outcomeKey: `${outcomeKey}:YES`,
          yesPrice,
          noPrice,
          yesPriceKind: 'direct' as const,
          noPriceKind: hasDirectNo ? 'direct' as const : 'implied' as const,
        };
      });
      const noRows = (outcome.complementPoints || [])
        .filter((point) => !point.isImplied)
        .map((point) => {
          const noPrice = directTokenPrice(point);
          return {
            timestamp: Number(point.x ?? point.blockNumber ?? point.timestamp),
            close: noPrice,
            volume: toNumber(point.volume),
            source,
            tokenId: outcome.buyNoTokenId || point.tokenId || undefined,
            tokenSide: outcome.buyNoTokenSide || 'NO',
            outcomeLabel: deriveEventOutcomeLabel(eventTitle, outcome.buyNoLabel || '', outcome.buyNoLabel || `${shortLabel} No`),
            outcomeShortLabel: deriveEventOutcomeLabel(eventTitle, outcome.buyNoLabel || '', outcome.buyNoLabel || `${shortLabel} No`),
            outcomeFullLabel: outcome.buyNoLabel || fullLabel,
            outcomeKey: `${outcomeKey}:NO`,
            yesPrice: Math.max(0, Math.min(1, 1 - noPrice)),
            noPrice,
            yesPriceKind: 'implied' as const,
            noPriceKind: 'direct' as const,
          };
        });
      return [...yesRows, ...noRows];
    })()
  )).filter((row) => row.timestamp && Number.isFinite(row.close));
}

function formatAxisValue(value: unknown, axis: string) {
  const numeric = Number(value);
  if (axis === 'block_number') return `block ${Number.isFinite(numeric) ? numeric.toLocaleString('en-US') : value}`;
  if (!Number.isFinite(numeric)) return String(value ?? '');
  return new Date(numeric * 1000).toISOString().slice(0, 16).replace('T', ' ');
}

function metricStatus(value: unknown): BacktestMetric['status'] {
  return value === 'positive' || value === 'negative' || value === 'neutral' ? value : 'neutral';
}

function metricToCard(row: QuantBacktestMetric): BacktestMetric {
  return {
    name: row.metricName,
    value: toNumber(row.value),
    formattedValue: row.formattedValue || String(row.value ?? ''),
    delta: row.delta || '',
    status: metricStatus(row.status),
    tooltip: row.tooltip || row.metricName,
  };
}

function runOutcomeLabel(run: QuantBacktestRun) {
  const meta = run.meta || {};
  const label = typeof meta.outcome_label === 'string' ? meta.outcome_label : typeof meta.outcomeLabel === 'string' ? meta.outcomeLabel : '';
  return label.trim();
}

function tradeToUi(row: QuantBacktestTrade, run: QuantBacktestRun): Trade {
  const xAxis = row.xAxis || 'timestamp';
  const outcomeLabel = runOutcomeLabel(run);
  return {
    id: row.tradeId,
    entryTime: formatAxisValue(row.entryX, xAxis),
    exitTime: formatAxisValue(row.exitX, xAxis),
    entryX: Number(row.entryX),
    exitX: Number(row.exitX),
    xAxis,
    marketId: row.marketSlug,
    market: row.marketSlug,
    outcome: outcomeLabel || (row.tokenSide === 'NO' ? 'NO' : 'YES'),
    side: row.side === 'SHORT' ? 'SHORT' : 'LONG',
    entryPrice: toNumber(row.entryPrice),
    exitPrice: toNumber(row.exitPrice),
    size: toNumber(row.size),
    notional: toNumber(row.notional),
    requestedSize: toNumber(row.requestedSize),
    filledSize: toNumber(row.filledSize),
    unfilledSize: toNumber(row.unfilledSize),
    fillStatus: row.fillStatus || undefined,
    bookSnapshotId: row.bookSnapshotId == null ? undefined : Number(row.bookSnapshotId),
    snapshotVersion: row.snapshotVersion || undefined,
    stalenessSeconds: toNumber(row.stalenessSeconds),
    stalenessBlocks: toNumber(row.stalenessBlocks),
    avgFillPrice: toNumber(row.avgFillPrice),
    fillProbability: toNumber(row.fillProbability),
    blockVolume: toNumber(row.blockVolume),
    tradeCount: toNumber(row.tradeCount),
    availableNotional: toNumber(row.availableNotional),
    executionSource: row.executionSource || undefined,
    pnl: toNumber(row.pnl),
    pnlPct: toNumber(row.pnlPct),
    holdingBars: toNumber(row.holdingBars),
    holdingTime: `${toNumber(row.holdingBars)} bars`,
    exitReason: row.exitReason,
  };
}

function orderToUi(row: QuantBacktestOrder): OrderLifecycleRow {
  return {
    id: row.orderId,
    signalIndex: Number(row.signalIndex || 0),
    tradeId: row.tradeId || undefined,
    xAxis: row.xAxis || 'block_number',
    signalX: Number(row.signalX),
    submitX: Number(row.submitX ?? row.signalX),
    side: row.side || '-',
    role: row.role || '-',
    orderType: row.orderType || '-',
    status: row.status || '-',
    decisionPrice: toNumber(row.decisionPrice),
    requestedPrice: toNumber(row.requestedPrice),
    requestedSize: toNumber(row.requestedSize),
    requestedNotional: toNumber(row.requestedNotional),
    filledSize: toNumber(row.filledSize),
    filledNotional: toNumber(row.filledNotional),
    unfilledSize: toNumber(row.unfilledSize),
    avgFillPrice: toNumber(row.avgFillPrice),
    fillProbability: toNumber(row.fillProbability),
    fillPct: toNumber(row.fillPct),
    blockVolume: toNumber(row.blockVolume),
    tradeCount: toNumber(row.tradeCount),
    availableNotional: toNumber(row.availableNotional),
    feeCost: toNumber(row.feeCost),
    slippageCost: toNumber(row.slippageCost),
    executionCost: toNumber(row.executionCost),
    latencyBlocks: toNumber(row.latencyBlocks),
    latencySeconds: toNumber(row.latencySeconds),
    noFillReason: row.noFillReason || '',
    executionSource: row.executionSource || '-',
  };
}

function ledgerToUi(row: QuantBacktestLedgerRow): LedgerRow {
  return {
    id: row.ledgerId,
    orderId: row.orderId || undefined,
    tradeId: row.tradeId || undefined,
    eventType: row.eventType || '-',
    xAxis: row.xAxis || 'block_number',
    xValue: Number(row.xValue),
    sharesDelta: toNumber(row.sharesDelta),
    cashDelta: toNumber(row.cashDelta),
    fee: toNumber(row.fee),
    rebate: toNumber(row.rebate),
    slippageCost: toNumber(row.slippageCost),
    executionCost: toNumber(row.executionCost),
    realizedPnl: toNumber(row.realizedPnl),
    positionAfter: toNumber(row.positionAfter),
    cashAfter: toNumber(row.cashAfter),
    price: toNumber(row.price),
    source: row.source || '-',
  };
}

function equityToUi(row: QuantBacktestEquityPoint): EquityPoint {
  return {
    timestamp: Number(row.xValue),
    index: Number(row.pointIndex),
    equity: toNumber(row.equity),
    drawdown: toNumber(row.drawdown),
    drawdownPct: toNumber(row.drawdownPct),
    cumulativeReturn: toNumber(row.cumulativeReturn),
  };
}

function performanceRows(metrics: QuantBacktestMetric[], trades: Trade[]): PerformanceRow[] {
  const yesTrades = trades.filter((trade) => trade.outcome === 'YES');
  const noTrades = trades.filter((trade) => trade.outcome === 'NO');
  return metrics.map((metric) => ({
    metric: metric.metricName,
    all: metric.formattedValue || String(metric.value ?? ''),
    long: metric.metricKey === 'total_trades' ? String(yesTrades.length) : '',
    short: metric.metricKey === 'total_trades' ? String(noTrades.length) : '',
    description: metric.tooltip || metric.metricName,
  }));
}

function propertyGroups(run: QuantBacktestRun, priceSource: PriceSource): PropertyGroup[] {
  const meta = run.meta || {};
  const parameterFingerprint = run.parameterFingerprint || (typeof meta.parameter_fingerprint === 'string' ? meta.parameter_fingerprint : typeof meta.parameterFingerprint === 'string' ? meta.parameterFingerprint : '-');
  const executionContext = (meta.execution_context || meta.executionContext || run.parameterSnapshot?.execution_context || run.parameterSnapshot?.executionContext || {}) as Record<string, unknown>;
  const actualDataQuality = (meta.actual_data_quality || meta.actualDataQuality || {}) as Record<string, unknown>;
  const liveClob = (executionContext.live_clob || executionContext.liveClob || {}) as Record<string, unknown>;
  const executionQuality = (executionContext.execution_quality || executionContext.executionQuality || {}) as Record<string, unknown>;
  const fillEstimate = (executionContext.fill_estimate || executionContext.fillEstimate || {}) as Record<string, unknown>;
  const dataQuality = (executionContext.data_quality || executionContext.dataQuality || {}) as Record<string, unknown>;
  const dataAccess = (actualDataQuality.data_access || actualDataQuality.dataAccess || dataQuality.data_access || dataQuality.dataAccess || {}) as Record<string, unknown>;
  const stringifyValue = (value: unknown, fallback = '-') => (
    value === null || value === undefined || value === '' ? fallback : String(value)
  );
  return [
    {
      title: 'Market Info',
      rows: [
        { label: 'market slug', value: run.marketSlug },
        { label: 'token side', value: run.tokenSide },
        { label: 'outcome', value: runOutcomeLabel(run) || run.tokenSide },
        { label: 'token id', value: typeof meta.token_id === 'string' ? meta.token_id : '-' },
        { label: 'source', value: run.priceSource },
        { label: 'engine', value: run.backtestEngine || 'builtin' },
        { label: 'from block', value: String(run.fromBlock ?? '-') },
        { label: 'to block', value: String(run.toBlock ?? '-') },
      ],
    },
    {
      title: 'Strategy Parameters',
      rows: [
        { label: 'entry threshold', value: String(run.entryThreshold ?? '0.58') },
        { label: 'exit threshold', value: String(run.exitThreshold ?? '0.44') },
        { label: 'stop loss', value: String(run.stopLoss ?? '0.075') },
        { label: 'take profit', value: String(run.takeProfit ?? '0.16') },
        { label: 'max hold', value: `${run.maxHoldingBars ?? 96} bars` },
        { label: 'position size', value: `${run.positionSize ?? 100} shares` },
        { label: 'fee bps', value: `${run.feeBps ?? 0}` },
        { label: 'slippage bps', value: `${run.slippageBps ?? 0}` },
        { label: 'liquidity cap', value: `${run.liquidityCapPct ?? 100}%` },
        { label: 'max position', value: run.maxPositionNotional ? `${run.maxPositionNotional} USDC` : 'off' },
        { label: 'min fill', value: `${run.minFillPct ?? 0}%` },
        { label: 'execution mode', value: run.executionPriceMode || 'ORDERFILLED_LIMIT_REPLAY' },
        { label: 'final valuation', value: stringifyValue(run.finalValuationMode ?? run.parameterSnapshot?.final_valuation_mode ?? run.parameterSnapshot?.finalValuationMode, 'SETTLEMENT') },
        { label: 'buy limit', value: stringifyValue(run.buyLimitPrice ?? run.parameterSnapshot?.buy_limit_price ?? run.parameterSnapshot?.buyLimitPrice ?? run.entryThreshold, '-') },
        { label: 'sell limit', value: stringifyValue(run.sellLimitPrice ?? run.parameterSnapshot?.sell_limit_price ?? run.parameterSnapshot?.sellLimitPrice, '-') },
        { label: 'settlement value', value: stringifyValue(run.settlementValue ?? run.parameterSnapshot?.settlement_value ?? run.parameterSnapshot?.settlementValue, 'unresolved') },
        { label: 'latency', value: `${run.latencySeconds ?? 0}s` },
        { label: 'max stale book', value: `${run.maxBookStalenessSeconds ?? 900}s` },
        { label: 'min fill size', value: `${run.minFillSize ?? 0}` },
        { label: 'partial fill', value: run.allowPartialFill === false ? 'disabled' : 'enabled' },
        { label: 'stale book gate', value: run.rejectOnStaleBook === false ? 'warn only' : 'reject' },
        { label: 'execution profile', value: stringifyValue(run.executionProfile ?? run.parameterSnapshot?.execution_profile ?? run.parameterSnapshot?.executionProfile, 'realistic') },
        { label: 'order role', value: stringifyValue(run.orderRole ?? run.parameterSnapshot?.order_role ?? run.parameterSnapshot?.orderRole, 'taker') },
        { label: 'latency blocks', value: stringifyValue(run.latencyBlocks ?? run.parameterSnapshot?.latency_blocks ?? run.parameterSnapshot?.latencyBlocks, '0') },
        { label: 'adverse slippage cents', value: stringifyValue(run.adverseSlippageCents ?? run.parameterSnapshot?.adverse_slippage_cents ?? run.parameterSnapshot?.adverseSlippageCents, '0') },
        { label: 'fill haircut', value: `${stringifyValue(run.fillProbabilityHaircutPct ?? run.parameterSnapshot?.fill_probability_haircut_pct ?? run.parameterSnapshot?.fillProbabilityHaircutPct, '0')}%` },
      ],
    },
    {
      title: 'Backtest Assumptions',
      rows: [
        { label: 'initial capital', value: `${run.initialCapital ?? 100000} USDC` },
        { label: 'price source', value: priceSource },
        { label: 'fingerprint', value: parameterFingerprint },
        { label: 'run status', value: run.status },
        { label: 'rows processed', value: String(run.rowsProcessed ?? 0) },
      ],
    },
    {
      title: 'Execution Context',
      rows: [
        { label: 'fill model', value: stringifyValue(executionContext.fill_model ?? executionContext.fillModel ?? executionContext.model, 'close + bps') },
        { label: 'execution status', value: stringifyValue(executionQuality.status ?? liveClob.status) },
        { label: 'confidence', value: executionQuality.confidence === undefined ? '-' : `${executionQuality.confidence}%` },
        { label: 'book source', value: stringifyValue(liveClob.source) },
        { label: 'book snapshot', value: stringifyValue(liveClob.fetchedAt ?? liveClob.fetched_at) },
        { label: 'best bid / ask', value: `${stringifyValue(liveClob.bestBid ?? liveClob.best_bid)} / ${stringifyValue(liveClob.bestAsk ?? liveClob.best_ask)}` },
        { label: 'spread', value: stringifyValue(liveClob.spread) },
        { label: 'top depth', value: stringifyValue(liveClob.topDepth ?? liveClob.top_depth) },
        { label: 'fill estimate', value: `${stringifyValue(fillEstimate.fillPct ?? fillEstimate.fill_pct, '0')} filled · VWAP ${stringifyValue(fillEstimate.vwap)}` },
      ],
    },
    {
      title: 'Data Reproducibility',
      rows: [
        { label: 'actual quality', value: stringifyValue(actualDataQuality.status ?? dataQuality.status) },
        { label: 'data version', value: stringifyValue(actualDataQuality.data_version ?? actualDataQuality.dataVersion ?? actualDataQuality.checksum ?? dataQuality.dataVersion) },
        { label: 'actual rows', value: stringifyValue(actualDataQuality.rows ?? dataQuality.rows) },
        { label: 'actual range', value: `${stringifyValue(actualDataQuality.first_x ?? actualDataQuality.firstX)} -> ${stringifyValue(actualDataQuality.last_x ?? actualDataQuality.lastX)}` },
        { label: 'gap count', value: stringifyValue(actualDataQuality.gap_count ?? actualDataQuality.gapCount ?? dataQuality.gapCount) },
        { label: 'jump count', value: stringifyValue(actualDataQuality.jump_count ?? actualDataQuality.jumpCount ?? dataQuality.jumpCount) },
        { label: 'span coverage', value: `${stringifyValue(actualDataQuality.span_coverage_pct ?? actualDataQuality.spanCoveragePct)}%` },
        { label: 'segment', value: stringifyValue(executionContext.segment ?? executionContext.scope) },
      ],
    },
    {
      title: 'Data Access Guard',
      rows: [
        { label: 'source table', value: stringifyValue(actualDataQuality.source_table ?? actualDataQuality.sourceTable ?? dataAccess.source_table ?? dataAccess.sourceTable) },
        { label: 'access path', value: stringifyValue(actualDataQuality.access_path ?? actualDataQuality.accessPath ?? dataAccess.access_path ?? dataAccess.accessPath) },
        { label: 'index hint', value: stringifyValue(actualDataQuality.index_hint ?? actualDataQuality.indexHint ?? dataAccess.index_hint ?? dataAccess.indexHint) },
        { label: 'query guard', value: stringifyValue(actualDataQuality.query_guard_version ?? actualDataQuality.queryGuardVersion ?? dataAccess.query_guard_version ?? dataAccess.queryGuardVersion) },
        { label: 'token id', value: stringifyValue(dataAccess.token_id ?? dataAccess.tokenId ?? meta.token_id) },
        { label: 'requested range', value: `${stringifyValue(dataAccess.requested_from ?? dataAccess.requestedFrom)} -> ${stringifyValue(dataAccess.requested_to ?? dataAccess.requestedTo)}` },
        { label: 'actual range', value: `${stringifyValue(dataAccess.actual_first_x ?? dataAccess.actualFirstX ?? actualDataQuality.first_x ?? actualDataQuality.firstX)} -> ${stringifyValue(dataAccess.actual_last_x ?? dataAccess.actualLastX ?? actualDataQuality.last_x ?? actualDataQuality.lastX)}` },
        { label: 'policy', value: stringifyValue((dataAccess.policy as Record<string, unknown> | undefined)?.backtest_price_read ?? (dataAccess.policy as Record<string, unknown> | undefined)?.backtestPriceRead, 'keyed block-close reads only') },
      ],
    },
  ];
}

export function backtestApiToResult(
  run: QuantBacktestRun,
  metrics: QuantBacktestMetric[],
  equity: QuantBacktestEquityPoint[],
  trades: QuantBacktestTrade[],
  priceSource: PriceSource,
  orders: QuantBacktestOrder[] = [],
  ledger: QuantBacktestLedgerRow[] = [],
): BacktestResult {
  const overviewMetrics = metrics.filter((metric) => metric.metricGroup !== 'prediction').map(metricToCard);
  const predictionMetrics = metrics.filter((metric) => metric.metricGroup === 'prediction').map(metricToCard);
  const uiTrades = trades.map((trade) => tradeToUi(trade, run));
  const uiEquity = equity.map(equityToUi);
  const uiOrders = orders.map(orderToUi);
  const uiLedger = ledger.map(ledgerToUi);
  return {
    runId: run.runId,
    generatedAt: run.finishedAt || run.startedAt || new Date().toISOString(),
    metrics: overviewMetrics,
    equity: uiEquity,
    orders: uiOrders,
    trades: uiTrades,
    ledger: uiLedger,
    performanceRows: performanceRows(metrics, uiTrades),
    propertyGroups: propertyGroups(run, priceSource),
    predictionMetrics,
  };
}

export function emptyBacktestResult(): BacktestResult {
  return {
    runId: 0,
    generatedAt: '',
    metrics: [],
    equity: [],
    orders: [],
    trades: [],
    ledger: [],
    performanceRows: [],
    propertyGroups: [
      {
        title: 'Backtest',
        rows: [
          { label: 'status', value: 'No completed run' },
          { label: 'data', value: 'Select a market and run a backtest' },
        ],
      },
    ],
    predictionMetrics: [],
  };
}
