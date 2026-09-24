import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import {
  createQuantBacktestBenchmark,
  createQuantBacktestRun,
  fetchQuantBacktestBenchmark,
  fetchQuantBacktestBenchmarkRows,
  fetchQuantBacktestBenchmarks,
  fetchQuantBacktestUniverses,
  fetchQuantBacktestRuns,
  fetchMarketLobByToken,
  fetchMarketLobSnapshotsByToken,
  fetchQuantBacktestEquity,
  fetchQuantBacktestLedger,
  fetchQuantBacktestMetrics,
  fetchQuantBacktestOrders,
  fetchQuantBacktestRun,
  fetchQuantBacktestTrades,
  fetchQuantBuildStatus,
  fetchQuantEntitySnapshot,
  fetchQuantEventMembers,
  fetchQuantEventPriceSeries,
  fetchQuantPriceWindow,
  fetchQuantPriceEvents,
  fetchQuantPriceMarkets,
  isAbortLikeError,
  quantPriceStreamUrl,
  type QuantPriceQuery,
} from '@/services/api';
import type { LobPayload, LobSide, LobSnapshot, QuantBacktestBenchmarkArtifact, QuantBacktestBenchmarkRow, QuantBacktestBenchmarkRun, QuantBacktestCreatePayload, QuantBacktestRun, QuantBacktestUniverse, QuantBlockClosePoint, QuantBuildRun, QuantFrontendPricePoint, QuantMarketSeriesOutcome, QuantMarketSeriesPayload, QuantMarketSeriesPoint, QuantPriceMarket } from '@/types';
import { PriceChartPanel } from './components/PriceChartPanel';
import { StrategyTesterPanel } from './components/StrategyTesterPanel';
import { WorkspaceHeader } from './components/WorkspaceHeader';
import type { BacktestEngine, BacktestResult, BatchBacktestRow, DataStatus, MarketInfo, PerformanceSortKey, PricePoint, PriceSource, Signal, SortDirection, StrategyParameters, TesterTab, TradeFilter } from './types';
import { backtestApiToResult, blockToPrices, emptyBacktestResult, frontendToPrices, marketSeriesToPrices } from './utils/apiAdapters';
import { deriveEventOutcomeLabel, downloadText, fmtPrice } from './utils/formatters';

function rowSortValue(value: string) {
  const numeric = Number(value.replace(/[^0-9.-]/g, ''));
  return Number.isFinite(numeric) && value.match(/[0-9]/) ? numeric : value.toLowerCase();
}

function metricText(metrics: Array<{ metricKey?: string; metricName?: string; formattedValue?: string | null; value?: string | number | null }>, keys: string[], fallback = '-') {
  const normalized = new Set(keys.map((key) => key.toLowerCase()));
  const metric = metrics.find((row) => (
    normalized.has(String(row.metricKey || '').toLowerCase())
    || normalized.has(String(row.metricName || '').toLowerCase().replace(/\s+/g, '_'))
  ));
  if (!metric) return fallback;
  return metric.formattedValue || String(metric.value ?? fallback);
}

function tradesToCsv(trades: BacktestResult['trades']) {
  const headers = ['id', 'entryTime', 'exitTime', 'market', 'outcome', 'side', 'entryPrice', 'exitPrice', 'size', 'notional', 'pnl', 'pnlPct', 'holdingTime', 'exitReason'];
  const rows = trades.map((trade) => headers.map((key) => JSON.stringify(String(trade[key as keyof typeof trade] ?? ''))).join(','));
  return [headers.join(','), ...rows].join('\n');
}

function backendPriceSource(priceSource: PriceSource) {
  return priceSource === 'orderfilled' ? 'orderfilled_block_close' : 'frontend';
}

function uiPriceSource(value: string | null | undefined): PriceSource {
  return String(value || '').toLowerCase().includes('frontend') ? 'frontend' : 'orderfilled';
}

const DEFAULT_QUANT_EVENT_SLUG = '2026-fifa-world-cup-winner-595';
const DEFAULT_QUANT_EVENT_TITLE = '2026 FIFA World Cup Winner';
const EVENT_TILE_OUTCOME_LIMIT = 12;
const EVENT_TILE_MAX_POINTS = 240;
const EVENT_TILE_FULL_MAX_POINTS = 900;
const EVENT_TILE_SEEDED_MAX_OUTCOMES = 100;
const EVENT_TILE_SEEDED_LATEST_LIMIT = 2500;
const EVENT_TILE_SEEDED_LATEST_MAX_POINTS = 600;
const EVENT_TILE_SEEDED_FULL_LIMIT = 250000;
const MIN_WINDOW_TILE_POINTS = 240;
const MAX_WINDOW_TILE_POINTS = 1800;

function chartViewportWidth() {
  if (typeof window === 'undefined') return 1200;
  return Math.max(360, Math.floor(window.innerWidth - 120));
}

function tilePointBudget(range: string, width = chartViewportWidth()) {
  const full = chartRangeFromTimeframe(range) === 'full' || range === 'full' || range === 'all';
  const multiplier = full ? 0.85 : 1.25;
  return Math.max(MIN_WINDOW_TILE_POINTS, Math.min(MAX_WINDOW_TILE_POINTS, Math.floor(width * multiplier)));
}
const DEFAULT_STRATEGY_PARAMETERS: StrategyParameters = {
  entryThreshold: 0.58,
  exitThreshold: 0.44,
  stopLoss: 0.075,
  takeProfit: 0.16,
  maxHoldingBars: 96,
  initialCapital: 100000,
  positionSize: 100,
  feeBps: 0,
  slippageBps: 0,
  liquidityCapPct: 100,
  maxPositionNotional: 0,
  minFillPct: 0,
  executionProfile: 'realistic',
  orderRole: 'taker',
  executionPriceMode: 'ORDERFILLED_LIMIT_REPLAY',
  finalValuationMode: 'SETTLEMENT',
  buyLimitPrice: 0.5,
  sellLimitPrice: 0.99,
  latencySeconds: 0,
  latencyBlocks: 0,
  maxBookStalenessSeconds: 900,
  adverseSlippageCents: 0.005,
  fillProbabilityHaircutPct: 20,
  allowPartialFill: true,
  minFillSize: 0,
  rejectOnStaleBook: true,
};

function defaultMarketSlug() {
  const params = new URLSearchParams(window.location.search);
  return params.get('event_slug') || params.get('event') || params.get('market') || params.get('market_slug') || params.get('slug') || DEFAULT_QUANT_EVENT_SLUG;
}

function defaultEntityKind() {
  const params = new URLSearchParams(window.location.search);
  if (params.get('event_slug') || params.get('event')) return 'event';
  if (params.get('market') || params.get('market_slug') || params.get('slug')) return params.get('kind') === 'event' ? 'event' : 'market';
  return 'event';
}

function defaultPriceSource(): PriceSource {
  const params = new URLSearchParams(window.location.search);
  const source = `${params.get('source') || params.get('price_source') || ''}`.toLowerCase();
  if (source.includes('frontend')) return 'frontend';
  return 'orderfilled';
}

function defaultMarketSearchQuery() {
  const params = new URLSearchParams(window.location.search);
  return params.get('q') || params.get('search') || params.get('query') || '';
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function chartRangeFromTimeframe(timeframe: string) {
  return timeframe === 'all' || timeframe === 'full' || timeframe === '25000' ? 'full' : 'latest';
}

function eventTileRequestShape(chartRange: string, timeframe: string, chartLimit: number) {
  if (chartRange === 'full') {
    return {
      limit: EVENT_TILE_SEEDED_FULL_LIMIT,
      maxOutcomes: EVENT_TILE_SEEDED_MAX_OUTCOMES,
      topN: EVENT_TILE_OUTCOME_LIMIT,
      maxPoints: EVENT_TILE_FULL_MAX_POINTS,
    };
  }
  const prefersSeededLatest = chartLimit <= EVENT_TILE_SEEDED_LATEST_LIMIT;
  return {
    limit: prefersSeededLatest ? EVENT_TILE_SEEDED_LATEST_LIMIT : chartLimit,
    maxOutcomes: prefersSeededLatest ? EVENT_TILE_SEEDED_MAX_OUTCOMES : EVENT_TILE_OUTCOME_LIMIT,
    topN: EVENT_TILE_OUTCOME_LIMIT,
    maxPoints: prefersSeededLatest
      ? EVENT_TILE_SEEDED_LATEST_MAX_POINTS
      : Math.max(EVENT_TILE_MAX_POINTS, Math.min(900, tilePointBudget(timeframe))),
  };
}

function isSeriesWarming(payload: QuantMarketSeriesPayload | null | undefined) {
  const status = String(payload?.status || payload?.cacheStatus || '').toLowerCase();
  return Boolean(payload?.warming) || ['warming', 'partial'].includes(status);
}

function retryDelayFromSeries(payload: QuantMarketSeriesPayload | null | undefined) {
  const value = Number(payload?.retryAfterMs);
  return Number.isFinite(value) && value > 0 ? Math.max(750, Math.min(8000, value)) : 1800;
}

async function waitForRun(runId: number, onStatus: (run: QuantBacktestRun) => void) {
  for (let attempt = 0; attempt < 90; attempt += 1) {
    const response = await fetchQuantBacktestRun(runId);
    onStatus(response.item);
    if (!['queued', 'running'].includes(response.item.status)) return response.item;
    await sleep(1000);
  }
  const response = await fetchQuantBacktestRun(runId);
  onStatus(response.item);
  return response.item;
}

function signalsFromTrades(result: BacktestResult): Signal[] {
  return result.trades.flatMap((trade) => {
    if (!trade.entryX || !trade.exitX) return [];
    return [
      {
        id: `${trade.id}-entry`,
        timestamp: trade.entryX,
        action: 'OPEN' as const,
        outcome: trade.outcome,
        price: trade.entryPrice,
        size: trade.size,
        notional: trade.notional,
        reason: 'entry threshold reached',
        tradeId: trade.id,
      },
      {
        id: `${trade.id}-exit`,
        timestamp: trade.exitX,
        action: 'CLOSE' as const,
        outcome: trade.outcome,
        price: trade.exitPrice,
        size: trade.size,
        notional: trade.notional,
        reason: trade.exitReason,
        tradeId: trade.id,
      },
    ];
  });
}

function toNumber(value: unknown) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

function finiteNumber(value: unknown) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function formatBookValue(value: unknown, digits = 3) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return numeric.toLocaleString('en-US', { maximumFractionDigits: digits });
}

function bookSideHasLevels(side: LobSide | null | undefined) {
  return Boolean(side?.bids?.length || side?.asks?.length);
}

function sumBookDepth(levels: LobSide['bids'], limit = 5) {
  return (levels || []).slice(0, limit).reduce((total, level) => total + toNumber(level.size), 0);
}

function sumBookNotional(levels: LobSide['bids'], limit = 12) {
  return (levels || []).slice(0, limit).reduce((total, level) => total + toNumber(level.price) * toNumber(level.size), 0);
}

function bookMidpoint(side: LobSide | null | undefined) {
  const bid = finiteNumber(side?.bestBid);
  const ask = finiteNumber(side?.bestAsk);
  if (bid !== null && ask !== null) return (bid + ask) / 2;
  return bid ?? ask ?? null;
}

function formatBookPercent(value: unknown, digits = 1) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return `${(numeric * 100).toLocaleString('en-US', { maximumFractionDigits: digits })}%`;
}

function orderBookLadderRows(levels: LobSide['bids'], side: 'bid' | 'ask', maxNotional: number) {
  const sorted = (levels || [])
    .map((level) => ({
      price: toNumber(level.price),
      size: toNumber(level.size),
    }))
    .filter((level) => level.price > 0 && level.size > 0)
    .sort((left, right) => (side === 'bid' ? right.price - left.price : left.price - right.price))
    .slice(0, 8);
  let cumulative = 0;
  return sorted.map((level) => {
    cumulative += level.price * level.size;
    return {
      ...level,
      cumulative,
      fillPct: maxNotional > 0 ? Math.max(4, Math.min(100, (cumulative / maxNotional) * 100)) : 0,
    };
  });
}

function estimateMarketBuyFill(levels: LobSide['asks'], targetNotional: number, referencePrice: number | null) {
  const target = Number.isFinite(targetNotional) && targetNotional > 0 ? targetNotional : 0;
  const sorted = (levels || [])
    .map((level) => ({ price: toNumber(level.price), size: toNumber(level.size) }))
    .filter((level) => level.price > 0 && level.size > 0)
    .sort((left, right) => left.price - right.price);
  let remaining = target;
  let spent = 0;
  let shares = 0;
  let levelsUsed = 0;
  for (const level of sorted) {
    if (remaining <= 0) break;
    const maxNotional = level.price * level.size;
    const takeNotional = Math.min(remaining, maxNotional);
    if (takeNotional <= 0) continue;
    spent += takeNotional;
    shares += takeNotional / level.price;
    remaining -= takeNotional;
    levelsUsed += 1;
  }
  const vwap = shares > 0 ? spent / shares : null;
  const fillPct = target > 0 ? spent / target : 0;
  const slippage = vwap !== null && referencePrice !== null ? vwap - referencePrice : null;
  return {
    fillPct,
    levelsUsed,
    remaining,
    shares,
    spent,
    target,
    vwap,
    slippage,
  };
}

function persistedBoolean(key: string, fallback: boolean) {
  try {
    const value = window.localStorage.getItem(key);
    if (value === 'true') return true;
    if (value === 'false') return false;
  } catch {
    // Keep the workspace usable when browser storage is blocked.
  }
  return fallback;
}

function persistedStringArray(key: string) {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) || '[]');
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === 'string') : [];
  } catch {
    return [];
  }
}

function clampNumber(value: unknown, fallback: number, min: number, max: number) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(min, Math.min(max, numeric));
}

function strategyNumber(value: unknown, fallback: number) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function normalizeStrategyParameters(value: Partial<StrategyParameters> | null | undefined): StrategyParameters {
  return {
    entryThreshold: clampNumber(value?.entryThreshold, DEFAULT_STRATEGY_PARAMETERS.entryThreshold, 0.001, 0.999),
    exitThreshold: clampNumber(value?.exitThreshold, DEFAULT_STRATEGY_PARAMETERS.exitThreshold, 0.001, 0.999),
    stopLoss: clampNumber(value?.stopLoss, DEFAULT_STRATEGY_PARAMETERS.stopLoss, 0.001, 0.95),
    takeProfit: clampNumber(value?.takeProfit, DEFAULT_STRATEGY_PARAMETERS.takeProfit, 0.001, 5),
    maxHoldingBars: Math.round(clampNumber(value?.maxHoldingBars, DEFAULT_STRATEGY_PARAMETERS.maxHoldingBars, 1, 10000)),
    initialCapital: clampNumber(value?.initialCapital, DEFAULT_STRATEGY_PARAMETERS.initialCapital, 1, 1000000000),
    positionSize: clampNumber(value?.positionSize, DEFAULT_STRATEGY_PARAMETERS.positionSize, 1, 1000000000),
    feeBps: clampNumber(value?.feeBps, DEFAULT_STRATEGY_PARAMETERS.feeBps, 0, 1000),
    slippageBps: clampNumber(value?.slippageBps, DEFAULT_STRATEGY_PARAMETERS.slippageBps, 0, 1000),
    liquidityCapPct: clampNumber(value?.liquidityCapPct, DEFAULT_STRATEGY_PARAMETERS.liquidityCapPct, 0, 100),
    maxPositionNotional: clampNumber(value?.maxPositionNotional, DEFAULT_STRATEGY_PARAMETERS.maxPositionNotional, 0, 1000000000),
    minFillPct: clampNumber(value?.minFillPct, DEFAULT_STRATEGY_PARAMETERS.minFillPct, 0, 100),
    executionProfile: value?.executionProfile === 'optimistic' || value?.executionProfile === 'conservative' || value?.executionProfile === 'stress' ? value.executionProfile : DEFAULT_STRATEGY_PARAMETERS.executionProfile,
    orderRole: value?.orderRole === 'maker' ? 'maker' : DEFAULT_STRATEGY_PARAMETERS.orderRole,
    executionPriceMode: value?.executionPriceMode === 'ORDERFILLED_LIMIT_REPLAY' || value?.executionPriceMode === 'ORDERFILLED' || value?.executionPriceMode === 'DEPTH' || value?.executionPriceMode === 'LEGACY' ? value.executionPriceMode : DEFAULT_STRATEGY_PARAMETERS.executionPriceMode,
    finalValuationMode: value?.finalValuationMode === 'FORCE_CLOSE' ? 'FORCE_CLOSE' : 'SETTLEMENT',
    buyLimitPrice: clampNumber(value?.buyLimitPrice, DEFAULT_STRATEGY_PARAMETERS.buyLimitPrice ?? DEFAULT_STRATEGY_PARAMETERS.entryThreshold, 0.001, 0.999),
    sellLimitPrice: clampNumber(value?.sellLimitPrice, DEFAULT_STRATEGY_PARAMETERS.sellLimitPrice ?? 0.999, 0.001, 0.999),
    settlementValue: value?.settlementValue === undefined || value?.settlementValue === null ? undefined : clampNumber(value.settlementValue, 0, 0, 1),
    latencySeconds: clampNumber(value?.latencySeconds, DEFAULT_STRATEGY_PARAMETERS.latencySeconds, 0, 3600),
    latencyBlocks: Math.round(clampNumber(value?.latencyBlocks, DEFAULT_STRATEGY_PARAMETERS.latencyBlocks, 0, 100000)),
    maxBookStalenessSeconds: clampNumber(value?.maxBookStalenessSeconds, DEFAULT_STRATEGY_PARAMETERS.maxBookStalenessSeconds, 0, 86400),
    adverseSlippageCents: clampNumber(value?.adverseSlippageCents, DEFAULT_STRATEGY_PARAMETERS.adverseSlippageCents, 0, 1),
    fillProbabilityHaircutPct: clampNumber(value?.fillProbabilityHaircutPct, DEFAULT_STRATEGY_PARAMETERS.fillProbabilityHaircutPct, 0, 100),
    allowPartialFill: value?.allowPartialFill ?? DEFAULT_STRATEGY_PARAMETERS.allowPartialFill,
    minFillSize: clampNumber(value?.minFillSize, DEFAULT_STRATEGY_PARAMETERS.minFillSize, 0, 1000000000),
    rejectOnStaleBook: value?.rejectOnStaleBook ?? DEFAULT_STRATEGY_PARAMETERS.rejectOnStaleBook,
  };
}

function persistedStrategyParameters() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem('polydata.quant.strategyParameters') || '{}');
    return normalizeStrategyParameters(parsed);
  } catch {
    return DEFAULT_STRATEGY_PARAMETERS;
  }
}

function backtestParameterPayload(strategyParameters: StrategyParameters): Pick<
  QuantBacktestCreatePayload,
  | 'entryThreshold'
  | 'exitThreshold'
  | 'stopLoss'
  | 'takeProfit'
  | 'maxHoldingBars'
  | 'initialCapital'
  | 'positionSize'
  | 'feeBps'
  | 'slippageBps'
  | 'liquidityCapPct'
  | 'maxPositionNotional'
  | 'minFillPct'
  | 'executionProfile'
  | 'orderRole'
  | 'executionPriceMode'
  | 'finalValuationMode'
  | 'buyLimitPrice'
  | 'sellLimitPrice'
  | 'settlementValue'
  | 'latencySeconds'
  | 'latencyBlocks'
  | 'maxBookStalenessSeconds'
  | 'adverseSlippageCents'
  | 'fillProbabilityHaircutPct'
  | 'allowPartialFill'
  | 'minFillSize'
  | 'rejectOnStaleBook'
> {
  return {
    entryThreshold: strategyParameters.entryThreshold,
    exitThreshold: strategyParameters.exitThreshold,
    stopLoss: strategyParameters.stopLoss,
    takeProfit: strategyParameters.takeProfit,
    maxHoldingBars: strategyParameters.maxHoldingBars,
    initialCapital: strategyParameters.initialCapital,
    positionSize: strategyParameters.positionSize,
    feeBps: strategyParameters.feeBps,
    slippageBps: strategyParameters.slippageBps,
    liquidityCapPct: strategyParameters.liquidityCapPct,
    maxPositionNotional: strategyParameters.maxPositionNotional,
    minFillPct: strategyParameters.minFillPct,
    executionProfile: strategyParameters.executionProfile,
    orderRole: strategyParameters.orderRole,
    executionPriceMode: strategyParameters.executionPriceMode,
    finalValuationMode: strategyParameters.finalValuationMode,
    buyLimitPrice: strategyParameters.buyLimitPrice,
    sellLimitPrice: strategyParameters.sellLimitPrice,
    settlementValue: strategyParameters.settlementValue,
    latencySeconds: strategyParameters.latencySeconds,
    latencyBlocks: strategyParameters.latencyBlocks,
    maxBookStalenessSeconds: strategyParameters.maxBookStalenessSeconds,
    adverseSlippageCents: strategyParameters.adverseSlippageCents,
    fillProbabilityHaircutPct: strategyParameters.fillProbabilityHaircutPct,
    allowPartialFill: strategyParameters.allowPartialFill,
    minFillSize: strategyParameters.minFillSize,
    rejectOnStaleBook: strategyParameters.rejectOnStaleBook,
  };
}

function chartOutcomeKey(outcome: QuantMarketSeriesOutcome | null | undefined, label: string, side: BacktestAction = 'YES') {
  const base = outcome?.outcomeKey || outcome?.marketSlug || outcome?.marketId || outcome?.outcomeLabel || label || outcome?.tokenId || 'outcome';
  return `${base}:${side}`;
}

function persistedInspectorTab() {
  try {
    const value = window.localStorage.getItem('polydata.quant.inspectorTab') as InspectorTab | null;
    if (value && ['watchlist', 'market', 'outcomes', 'book', 'trades', 'dataQuality'].includes(value)) return value;
  } catch {
    // Keep the default tab.
  }
  return 'market';
}

function blockRangeLabel(prices: Array<{ timestamp: number }>) {
  const first = prices[0]?.timestamp;
  const last = prices[prices.length - 1]?.timestamp;
  if (typeof first !== 'number' || typeof last !== 'number' || !Number.isFinite(first) || !Number.isFinite(last)) return '--';
  return `${Math.floor(first).toLocaleString('en-US')} -> ${Math.floor(last).toLocaleString('en-US')}`;
}

function formatBuildTime(value: string | null | undefined) {
  if (!value) return '--';
  const time = Date.parse(value);
  if (!Number.isFinite(time)) return value;
  return new Date(time).toLocaleString();
}

function formatMarketTime(value: string | number | null | undefined) {
  if (!value || value === '-') return '--';
  const time = typeof value === 'number' ? value : Date.parse(String(value));
  if (!Number.isFinite(time)) return String(value);
  return new Date(time).toLocaleString();
}

function metadataScalar(value: unknown): string | number | null {
  return typeof value === 'string' || typeof value === 'number' ? value : null;
}

function buildRunStatusClass(run: QuantBuildRun) {
  const status = String(run.status || '').toLowerCase();
  if (status.includes('success') || status.includes('complete') || status === 'ready') return 'ready';
  if (status.includes('run') || status.includes('queue') || status.includes('warm')) return 'running';
  if (status.includes('fail') || status.includes('error') || toNumber(run.errorCount) > 0) return 'error';
  return 'review';
}

function dataQualitySummary(prices: PricePoint[], source: string, outcomes: QuantMarketSeriesOutcome[], dataStatus: DataStatus) {
  const sorted = prices
    .filter((point) => Number.isFinite(point.timestamp) && Number.isFinite(point.close))
    .slice()
    .sort((left, right) => left.timestamp - right.timestamp);
  const deltas = sorted.slice(1).map((point, index) => {
    const previous = sorted[index];
    return previous ? point.timestamp - previous.timestamp : 0;
  }).filter((delta) => delta > 0);
  const medianDelta = quantile(deltas, 0.5);
  const gapCount = medianDelta > 0 ? deltas.filter((delta) => delta > medianDelta * 4).length : 0;
  const spikeCount = sorted.slice(1).filter((point, index) => {
    const previous = sorted[index];
    return previous ? Math.abs(point.close - previous.close) > 0.18 : false;
  }).length;
  const directYesRows = sorted.filter((point) => point.yesPriceKind === 'direct' || !point.yesPriceKind).length;
  const impliedNoRows = sorted.filter((point) => point.noPriceKind === 'implied').length;
  const directNoRows = sorted.filter((point) => point.noPriceKind === 'direct').length;
  const outcomeRows = outcomes.reduce((sum, outcome) => sum + toNumber(outcome.rows) + toNumber(outcome.complementRows), 0);
  const firstBlock = sorted[0]?.timestamp || 0;
  const latestBlock = sorted[sorted.length - 1]?.timestamp || 0;
  const warnings = [
    !sorted.length ? 'No price rows loaded for the selected source/window.' : '',
    gapCount ? `${gapCount} block gaps larger than the median spacing.` : '',
    spikeCount ? `${spikeCount} large adjacent price jumps detected.` : '',
    impliedNoRows > directNoRows ? 'NO side is mostly implied from YES prices.' : '',
    dataStatus === 'partial' ? 'Showing cached or partial coverage while refresh continues.' : '',
  ].filter(Boolean);
  return {
    rows: sorted.length,
    outcomeRows,
    firstBlock,
    latestBlock,
    medianDelta,
    gapCount,
    spikeCount,
    directYesRows,
    directNoRows,
    impliedNoRows,
    source,
    health: !sorted.length ? 'empty' : warnings.length ? 'review' : 'ready',
    warnings,
  };
}

function pointBlock(point: QuantMarketSeriesPoint | null | undefined) {
  return toNumber(point?.x ?? point?.blockNumber ?? point?.timestamp);
}

function outcomePointStats(points: QuantMarketSeriesPoint[] | undefined) {
  const sorted = (points || [])
    .map((point) => ({
      block: pointBlock(point),
      price: toNumber(point.price ?? point.yesProbabilityClose),
      implied: Boolean(point.isImplied),
    }))
    .filter((point) => Number.isFinite(point.block) && point.block > 0)
    .sort((left, right) => left.block - right.block);
  const deltas = sorted.slice(1).map((point, index) => {
    const previous = sorted[index];
    return previous ? point.block - previous.block : 0;
  }).filter((delta) => delta > 0);
  const medianDelta = quantile(deltas, 0.5);
  const gaps = medianDelta > 0 ? deltas.filter((delta) => delta > medianDelta * 4).length : 0;
  const spikes = sorted.slice(1).filter((point, index) => {
    const previous = sorted[index];
    return previous ? Math.abs(point.price - previous.price) > 0.18 : false;
  }).length;
  return {
    rows: sorted.length,
    firstBlock: sorted[0]?.block || 0,
    lastBlock: sorted[sorted.length - 1]?.block || 0,
    medianDelta,
    gaps,
    spikes,
    impliedRows: sorted.filter((point) => point.implied).length,
  };
}

function statsCoverageRatio(stats: ReturnType<typeof outcomePointStats>) {
  if (!stats.rows) return 0;
  if (!stats.firstBlock || !stats.lastBlock || !stats.medianDelta) return 1;
  const expectedRows = Math.max(1, Math.floor((stats.lastBlock - stats.firstBlock) / stats.medianDelta) + 1);
  return Math.max(0, Math.min(1, stats.rows / expectedRows));
}

function formatCoverageRatio(value: number) {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

function quantile(values: number[], ratio: number) {
  if (!values.length) return 0;
  const sorted = values.slice().sort((left, right) => left - right);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.floor((sorted.length - 1) * ratio)));
  return sorted[index] ?? 0;
}

function strategyDefaults(prices: Array<{ close: number }>) {
  const closes = prices.map((price) => price.close).filter((value) => Number.isFinite(value) && value > 0);
  const entry = Math.max(0.01, Math.min(0.95, quantile(closes, 0.68) || 0.58));
  const rawExit = Math.max(0.001, Math.min(0.94, quantile(closes, 0.42) || entry * 0.82));
  const exit = rawExit >= entry ? Math.max(0.001, entry * 0.82) : rawExit;
  return {
    entryThreshold: Number(entry.toFixed(4)),
    exitThreshold: Number(exit.toFixed(4)),
  };
}

type BacktestAction = 'YES' | 'NO';
type OutcomeSortKey = 'order' | 'probability' | 'rows' | 'volume';
type OutcomeVisibilityFilter = 'all' | 'visible' | 'pinned' | 'hidden' | 'watched' | 'issues';
type InspectorTab = 'watchlist' | 'market' | 'outcomes' | 'book' | 'trades' | 'dataQuality';
type RefreshQuantRowsOptions = {
  silent?: boolean;
};
type EventPriceStreamOutcome = {
  tokenId?: string | null;
  outcomeLabel?: string | null;
  buyYesPrice?: string | number | null;
  buyNoPrice?: string | number | null;
  latestPrice?: string | number | null;
  lastX?: string | number | null;
  rows?: string | number | null;
};
type EventPriceStreamPayload = {
  eventSlug?: string;
  priceSource?: string;
  updatedAt?: string;
  outcomes?: EventPriceStreamOutcome[];
};

function outcomePricePoints(outcome: QuantMarketSeriesOutcome | null | undefined, action: BacktestAction = 'YES') {
  const points = action === 'NO' ? outcome?.complementPoints || [] : outcome?.points || [];
  return points.map((point) => ({
    timestamp: Number(point.x ?? point.blockNumber ?? point.timestamp),
    close: toNumber(point.price),
    volume: toNumber(point.volume),
    source: action === 'NO' ? 'selected_outcome_no' : 'selected_outcome_yes',
    tokenId: action === 'NO' ? outcome?.buyNoTokenId || undefined : outcome?.buyYesTokenId || outcome?.tokenId,
    tokenSide: action === 'NO' ? outcome?.buyNoTokenSide || undefined : outcome?.buyYesTokenSide || outcome?.tokenSide,
    outcomeLabel: action === 'NO' ? outcome?.buyNoLabel || `${outcome?.outcomeLabel || 'Outcome'} No` : outcome?.buyYesLabel || outcome?.outcomeLabel,
  })).filter((point) => point.timestamp && Number.isFinite(point.close));
}

function defaultOutcomeTokenId(outcomes: QuantMarketSeriesOutcome[]) {
  const sorted = outcomes.slice().sort((left, right) => toNumber(right.latestPrice) - toNumber(left.latestPrice));
  return sorted[0]?.tokenId || outcomes[0]?.tokenId || '';
}

function appendStreamPoint(points: QuantMarketSeriesPoint[] | undefined, x: number, price: string | number | null | undefined, tokenId: string | null | undefined, tokenSide: string | null | undefined) {
  if (!Number.isFinite(x) || price === null || price === undefined) return points || [];
  const current = points || [];
  const last = current[current.length - 1];
  const lastX = Number(last?.x ?? last?.blockNumber ?? last?.timestamp);
  if (Number.isFinite(lastX) && x < lastX) return current;
  if (Number.isFinite(lastX) && x === lastX) {
    return current.map((point, index) => (index === current.length - 1 ? {
      ...point,
      price,
      volume: toNumber(point.volume),
      tokenId: tokenId || point.tokenId,
      tokenSide: tokenSide || point.tokenSide,
    } : point));
  }
  return [
    ...current,
    {
      x,
      blockNumber: x,
      tokenId,
      tokenSide,
      price,
      volume: 0,
      isImplied: false,
    },
  ];
}

function pointX(point: QuantMarketSeriesPoint | null | undefined) {
  return Number(point?.x ?? point?.blockNumber ?? point?.timestamp);
}

function mergeSeriesPoints(base: QuantMarketSeriesPoint[] | undefined, patch: QuantMarketSeriesPoint[] | undefined) {
  const byX = new Map<number, QuantMarketSeriesPoint>();
  [...(base || []), ...(patch || [])].forEach((point) => {
    const x = pointX(point);
    if (!Number.isFinite(x)) return;
    byX.set(Math.floor(x), { ...byX.get(Math.floor(x)), ...point, x: point.x ?? x });
  });
  return Array.from(byX.entries())
    .sort(([left], [right]) => left - right)
    .map(([, point]) => point);
}

function outcomeMergeKey(outcome: QuantMarketSeriesOutcome) {
  return String(outcome.tokenId || outcome.buyYesTokenId || outcome.marketId || outcome.marketSlug || outcome.outcomeKey || outcome.outcomeLabel || '');
}

function mergeMarketSeries(base: QuantMarketSeriesPayload | null, patch: QuantMarketSeriesPayload | null | undefined): QuantMarketSeriesPayload | null {
  if (!patch?.outcomes?.length) return base;
  if (!base?.outcomes?.length) return patch;
  const patchByKey = new Map(patch.outcomes.map((outcome) => [outcomeMergeKey(outcome), outcome]));
  const seen = new Set<string>();
  const outcomes = base.outcomes.map((outcome) => {
    const key = outcomeMergeKey(outcome);
    const next = patchByKey.get(key);
    seen.add(key);
    if (!next) return outcome;
    const points = mergeSeriesPoints(outcome.points, next.points);
    const complementPoints = mergeSeriesPoints(outcome.complementPoints, next.complementPoints);
    const latest = points[points.length - 1];
    const latestNo = complementPoints[complementPoints.length - 1];
    return {
      ...outcome,
      ...next,
      rows: Math.max(toNumber(outcome.rows), toNumber(next.rows), points.length),
      firstX: pointX(points[0]) || outcome.firstX || next.firstX,
      lastX: pointX(latest) || next.lastX || outcome.lastX,
      latestPrice: latest?.price ?? next.latestPrice ?? outcome.latestPrice,
      buyYesPrice: latest?.price ?? next.buyYesPrice ?? outcome.buyYesPrice,
      points,
      complementRows: Math.max(toNumber(outcome.complementRows), toNumber(next.complementRows), complementPoints.length),
      complementFirstX: pointX(complementPoints[0]) || outcome.complementFirstX || next.complementFirstX,
      complementLastX: pointX(latestNo) || next.complementLastX || outcome.complementLastX,
      complementLatestPrice: latestNo?.price ?? next.complementLatestPrice ?? outcome.complementLatestPrice,
      buyNoPrice: latestNo?.price ?? next.buyNoPrice ?? outcome.buyNoPrice,
      complementPoints,
    };
  });
  patch.outcomes.forEach((outcome) => {
    const key = outcomeMergeKey(outcome);
    if (!seen.has(key)) outcomes.push(outcome);
  });
  return {
    ...base,
    ...patch,
    event: patch.event || base.event,
    market: patch.market || base.market,
    members: patch.members?.length ? patch.members : base.members,
    outcomes,
    count: Math.max(base.count || 0, patch.count || 0, outcomes.length),
  };
}

function mergeEventPriceStream(current: QuantMarketSeriesPayload | null, update: EventPriceStreamPayload): QuantMarketSeriesPayload | null {
  if (!current?.outcomes?.length || !update.outcomes?.length) return current;
  const latestByToken = new Map(update.outcomes.map((outcome) => [String(outcome.tokenId || ''), outcome]));
  return {
    ...current,
    outcomes: current.outcomes.map((outcome) => {
      const latest = latestByToken.get(String(outcome.tokenId || ''));
      if (!latest) return outcome;
      const lastX = Number(latest.lastX);
      const nextPoints = appendStreamPoint(outcome.points, lastX, latest.latestPrice ?? latest.buyYesPrice, outcome.buyYesTokenId || outcome.tokenId, outcome.buyYesTokenSide || outcome.tokenSide);
      const nextComplementPoints = appendStreamPoint(outcome.complementPoints, lastX, latest.buyNoPrice, outcome.buyNoTokenId, outcome.buyNoTokenSide || 'NO');
      return {
        ...outcome,
        buyYesPrice: latest.buyYesPrice ?? outcome.buyYesPrice,
        buyNoPrice: latest.buyNoPrice ?? outcome.buyNoPrice,
        latestPrice: latest.latestPrice ?? latest.buyYesPrice ?? outcome.latestPrice,
        lastX: latest.lastX ?? outcome.lastX,
        rows: toNumber(latest.rows ?? outcome.rows),
        points: nextPoints,
        complementPoints: nextComplementPoints,
        complementRows: nextComplementPoints.length || outcome.complementRows,
      };
    }),
  };
}

function isClosedStatus(status: unknown) {
  const text = String(status || '').toLowerCase();
  return Boolean(text) && ['closed', 'resolved', 'settled', 'finalized', 'ended', 'archived'].some((word) => text.includes(word));
}

function isSelectionLive(market: QuantPriceMarket | null | undefined, series: QuantMarketSeriesPayload | null | undefined) {
  const status = (market as (QuantPriceMarket & { status?: string | null }) | null | undefined)?.status || series?.event?.status;
  if (isClosedStatus(status)) return false;
  const endDate = market?.endDate || series?.market?.endDate;
  if (!endDate) return true;
  const endTs = Date.parse(String(endDate));
  if (!Number.isFinite(endTs)) return true;
  return endTs > Date.now() - 12 * 60 * 60 * 1000;
}

function defaultEventMeta(): QuantPriceMarket {
  return {
    itemKind: 'event',
    eventSlug: DEFAULT_QUANT_EVENT_SLUG,
    eventTitle: DEFAULT_QUANT_EVENT_TITLE,
    groupingConfidence: 'official',
    source: 'default',
    outcomeCount: 60,
    totalMembers: 60,
    readyMembers: 0,
    marketSlug: DEFAULT_QUANT_EVENT_SLUG,
    marketTitle: DEFAULT_QUANT_EVENT_TITLE,
    tokenSide: 'EVENT',
    blockRows: 0,
    frontendRows: 0,
  };
}

function marketInfoFromSelection(slug: string, market?: QuantPriceMarket): MarketInfo {
  const isEvent = market?.itemKind === 'event';
  return {
    id: String(market?.marketId || slug || 'quant-market'),
    conditionId: market?.conditionId || '-',
    title: market?.marketTitle || slug || 'Select a Polymarket market',
    category: isEvent ? 'Polymarket Event' : 'Polymarket',
    slug: market?.marketSlug || slug,
    startTime: market?.firstTs ? new Date(toNumber(market.firstTs) * 1000).toISOString() : '-',
    endTime: market?.endDate || '-',
    resolutionTime: market?.endDate || '-',
    resolvedOutcome: 'PENDING',
    yesTokenId: 'YES',
    noTokenId: 'NO',
    liquidity: '-',
    volume: isEvent
      ? `${toNumber(market?.readyMembers).toLocaleString('en-US')} / ${toNumber(market?.totalMembers || market?.outcomeCount).toLocaleString('en-US')} members ready`
      : `${toNumber(market?.blockRows).toLocaleString('en-US')} block rows`,
  };
}

export function QuantWorkspace() {
  const [frontendRows, setFrontendRows] = useState<QuantFrontendPricePoint[]>([]);
  const [blockRows, setBlockRows] = useState<QuantBlockClosePoint[]>([]);
  const [marketSeries, setMarketSeries] = useState<QuantMarketSeriesPayload | null>(null);
  const [selectedOutcomeTokenId, setSelectedOutcomeTokenId] = useState('');
  const [selectedBacktestAction, setSelectedBacktestAction] = useState<BacktestAction>('YES');
  const [liveLob, setLiveLob] = useState<LobPayload | null>(null);
  const [liveLobStatus, setLiveLobStatus] = useState<DataStatus>('idle');
  const [liveLobError, setLiveLobError] = useState('');
  const [liveLobRefreshSeq, setLiveLobRefreshSeq] = useState(0);
  const [lobSnapshots, setLobSnapshots] = useState<LobSnapshot[]>([]);
  const [lobSnapshotStatus, setLobSnapshotStatus] = useState<DataStatus>('idle');
  const [lobSnapshotError, setLobSnapshotError] = useState('');
  const [batchBacktestRows, setBatchBacktestRows] = useState<BatchBacktestRow[]>([]);
  const [batchBacktestStatus, setBatchBacktestStatus] = useState('idle');
  const [splitBacktestRows, setSplitBacktestRows] = useState<BatchBacktestRow[]>([]);
  const [splitBacktestStatus, setSplitBacktestStatus] = useState('idle');
  const [walkForwardRows, setWalkForwardRows] = useState<BatchBacktestRow[]>([]);
  const [walkForwardStatus, setWalkForwardStatus] = useState('idle');
  const [runs, setRuns] = useState<QuantBuildRun[]>([]);
  const [recentBacktestRuns, setRecentBacktestRuns] = useState<QuantBacktestRun[]>([]);
  const [backtestRunsStatus, setBacktestRunsStatus] = useState<DataStatus>('idle');
  const [recentBenchmarkRuns, setRecentBenchmarkRuns] = useState<QuantBacktestBenchmarkRun[]>([]);
  const [benchmarkRun, setBenchmarkRun] = useState<QuantBacktestBenchmarkRun | null>(null);
  const [benchmarkRows, setBenchmarkRows] = useState<QuantBacktestBenchmarkRow[]>([]);
  const [benchmarkArtifacts, setBenchmarkArtifacts] = useState<QuantBacktestBenchmarkArtifact[]>([]);
  const [benchmarkUniverses, setBenchmarkUniverses] = useState<QuantBacktestUniverse[]>([]);
  const [benchmarkUniverse, setBenchmarkUniverse] = useState('nba_2024_25_moneyline');
  const [benchmarkLimit, setBenchmarkLimit] = useState(50);
  const [benchmarkStatus, setBenchmarkStatus] = useState<DataStatus>('idle');
  const [quantMarkets, setQuantMarkets] = useState<QuantPriceMarket[]>([]);
  const [marketSearchStatus, setMarketSearchStatus] = useState<DataStatus>('idle');
  const [selectedMarketMeta, setSelectedMarketMeta] = useState<QuantPriceMarket | null>(() => (
    defaultMarketSlug() === DEFAULT_QUANT_EVENT_SLUG ? defaultEventMeta() : null
  ));
  const [dataStatus, setDataStatus] = useState<DataStatus>('idle');
  const [loadingMessage, setLoadingMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [timeframe, setTimeframe] = useState('2500');
  const [viewportMode, setViewportMode] = useState<'preset' | 'custom'>('preset');
  const [viewportResetSeq, setViewportResetSeq] = useState(0);
  const [priceSource, setPriceSource] = useState<PriceSource>(defaultPriceSource);
  const [backtestEngine, setBacktestEngine] = useState<BacktestEngine>('backtrader');
  const [testerTab, setTesterTab] = useState<TesterTab>('overview');
  const [deepBacktest, setDeepBacktest] = useState(false);
  const [selectedTradeId, setSelectedTradeId] = useState<string | null>(null);
  const [marketSlug, setMarketSlug] = useState(defaultMarketSlug);
  const [selectedEntityKindHint, setSelectedEntityKindHint] = useState<'market' | 'event'>(defaultEntityKind);
  const [marketSearchQuery, setMarketSearchQuery] = useState(defaultMarketSearchQuery);
  const [marketReloadKey, setMarketReloadKey] = useState(0);
  const [backtestStatus, setBacktestStatus] = useState('idle');
  const [performanceSearch, setPerformanceSearch] = useState('');
  const [performanceSortKey, setPerformanceSortKey] = useState<PerformanceSortKey>('metric');
  const [performanceSortDirection, setPerformanceSortDirection] = useState<SortDirection>('asc');
  const [tradeFilters, setTradeFilters] = useState<Set<TradeFilter>>(new Set());
  const [workspaceNotice, setWorkspaceNotice] = useState('');
  const [outcomeSortKey, setOutcomeSortKey] = useState<OutcomeSortKey>('probability');
  const [outcomeSearch, setOutcomeSearch] = useState('');
  const [outcomeVisibilityFilter, setOutcomeVisibilityFilter] = useState<OutcomeVisibilityFilter>('all');
  const [lastPriceRefreshAt, setLastPriceRefreshAt] = useState('');
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>(persistedInspectorTab);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(() => persistedBoolean('polydata.quant.inspectorCollapsed', false));
  const [strategyDrawerCollapsed, setStrategyDrawerCollapsed] = useState(() => persistedBoolean('polydata.quant.strategyDrawerCollapsed', false));
  const [watchlistKeys, setWatchlistKeys] = useState<string[]>(() => persistedStringArray('polydata.quant.watchlistKeys'));
  const [chartPinnedOutcomeKeys, setChartPinnedOutcomeKeys] = useState<string[]>(() => persistedStringArray('polydata.quant.chart.pinnedOutcomes'));
  const [chartHiddenOutcomeKeys, setChartHiddenOutcomeKeys] = useState<string[]>(() => persistedStringArray('polydata.quant.chart.hiddenOutcomes'));
  const [chartSoloOutcomeKey, setChartSoloOutcomeKey] = useState('');
  const [hoveredChartOutcomeKey, setHoveredChartOutcomeKey] = useState('');
  const [strategyParameters, setStrategyParameters] = useState<StrategyParameters>(persistedStrategyParameters);
  const marketSearchSeq = useRef(0);
  const priceLoadSeq = useRef(0);
  const marketSlugRef = useRef(marketSlug);
  const autoSelectedDefaultRef = useRef(Boolean(defaultMarketSlug()));
  const marketSearchCacheRef = useRef(new Map<string, QuantPriceMarket[]>());
  const priceSeriesCacheRef = useRef(new Map<string, QuantMarketSeriesPayload>());
  const pricePrefetchingRef = useRef(new Set<string>());
  const viewportFetchTimerRef = useRef<number | null>(null);
  const viewportFetchSeq = useRef(0);
  const eventMembersPrefetchRef = useRef(new Set<string>());
  const liveRefreshInFlightRef = useRef(false);
  const priceRetryTimerRef = useRef<number | null>(null);

  const activePrices = useMemo(() => {
    const semanticPrices = marketSeriesToPrices(marketSeries);
    if (semanticPrices.length) return semanticPrices;
    if (priceSource === 'orderfilled') return blockToPrices(blockRows);
    if (priceSource === 'frontend') return frontendToPrices(frontendRows);
    return [];
  }, [blockRows, frontendRows, marketSeries, priceSource]);
  const [backtestResult, setBacktestResult] = useState<BacktestResult>(() => emptyBacktestResult());
  const strategySignals = useMemo(() => signalsFromTrades(backtestResult), [backtestResult]);
  const latestPrice = activePrices[activePrices.length - 1]?.close || 0;
  const displayedPriceRows = activePrices.length;
  const selectedOutcome = useMemo(() => {
    const outcomes = marketSeries?.outcomes || [];
    if (!outcomes.length) return null;
    return outcomes.find((outcome) => outcome.tokenId === selectedOutcomeTokenId) || outcomes[0] || null;
  }, [marketSeries, selectedOutcomeTokenId]);
  const selectedMarket = useMemo(
    () => (
      (selectedMarketMeta?.marketSlug === marketSlug ? selectedMarketMeta : undefined)
      || quantMarkets.find((market) => market.marketSlug === marketSlug && market.itemKind === 'event')
      || quantMarkets.find((market) => market.marketSlug === marketSlug && market.tokenSide === 'YES')
      || quantMarkets.find((market) => market.marketSlug === marketSlug)
    ),
    [marketSlug, quantMarkets, selectedMarketMeta],
  );
  const selectedEntityKind = selectedMarket?.itemKind === 'event' || selectedEntityKindHint === 'event' ? 'event' : 'market';
  const marketInfo = useMemo(() => marketInfoFromSelection(marketSlug, selectedMarket), [marketSlug, selectedMarket]);
  const livePriceRefreshEnabled = useMemo(
    () => Boolean(marketSlug.trim()) && isSelectionLive(selectedMarket, marketSeries),
    [marketSeries, marketSlug, selectedMarket],
  );
  const marketCoverageRows = toNumber(selectedMarket?.blockRows || selectedMarket?.frontendRows || marketSeries?.outcomes?.reduce((sum, outcome) => sum + toNumber(outcome.rows), 0));
  const marketReadyMembers = toNumber(selectedMarket?.readyMembers || marketSeries?.members?.filter((member) => toNumber(member.blockRows || member.frontendRows || member.orderfilledRows) > 0).length);
  const marketTotalMembers = toNumber(selectedMarket?.totalMembers || selectedMarket?.outcomeCount || marketSeries?.members?.length || marketSeries?.outcomes?.length);
  const marketCoveragePct = marketTotalMembers ? Math.min(100, Math.max(0, (marketReadyMembers / marketTotalMembers) * 100)) : 0;
  const marketDataSourceLabel = selectedMarket?.source || marketSeries?.event?.source || marketSeries?.market?.source || backendPriceSource(priceSource);
  const marketSourceDisplay = marketDataSourceLabel === 'default' ? backendPriceSource(priceSource) : marketDataSourceLabel;
  const marketStatusLabel = selectedMarket?.status || marketSeries?.event?.status || marketSeries?.market?.status || '--';
  const eventMeta = (marketSeries?.event || {}) as Record<string, unknown>;
  const marketMeta = (marketSeries?.market || {}) as Record<string, unknown>;
  const marketEndValue = selectedMarket?.endDate || metadataScalar(eventMeta.endDate) || marketSeries?.market?.endDate || marketInfo.endTime;
  const marketUpdatedValue = selectedMarket?.latestBlockAt || selectedMarket?.latestFrontendAt || metadataScalar(eventMeta.updated_at) || metadataScalar(eventMeta.updatedAt) || metadataScalar(marketMeta.updated_at) || metadataScalar(marketMeta.updatedAt);
  const marketEndLabel = formatMarketTime(marketEndValue);
  const marketUpdatedLabel = formatMarketTime(marketUpdatedValue);
  const chartLimit = useMemo(() => {
    if (chartRangeFromTimeframe(timeframe) === 'full') return 25000;
    const parsed = Number(timeframe);
    return Number.isFinite(parsed) ? Math.max(100, Math.min(25000, parsed)) : 2500;
  }, [timeframe]);
  const chartRange = chartRangeFromTimeframe(timeframe);
  const eventTileShape = selectedEntityKind === 'event' ? eventTileRequestShape(chartRange, timeframe, chartLimit) : null;
  const chartRequestLimit = eventTileShape?.limit || chartLimit;
  const semanticChartQuery: QuantPriceQuery & { priceSource: string; scope: string; maxOutcomes: number; topN?: number; maxPoints?: number } = {
    marketSlug,
    priceSource: backendPriceSource(priceSource),
    scope: 'auto',
    limit: chartRequestLimit,
    maxOutcomes: eventTileShape?.maxOutcomes || 24,
    topN: eventTileShape?.topN,
    maxPoints: eventTileShape?.maxPoints || tilePointBudget(timeframe),
    range: selectedEntityKind === 'event' ? chartRange : undefined,
    resolution: selectedEntityKind === 'event' ? 'auto' : undefined,
    live: false,
  };
  const priceRequestKey = [
    selectedEntityKind,
    marketSlug.trim(),
    backendPriceSource(priceSource),
    timeframe,
  ].join('|');

  const seriesKeyForSlug = (slug: string, itemKind = selectedEntityKind) => [
    itemKind,
    slug.trim(),
    backendPriceSource(priceSource),
    timeframe,
  ].join('|');

  const refreshQuantRows = async (requestSeq = priceLoadSeq.current, options: RefreshQuantRowsOptions = {}) => {
    const silent = Boolean(options.silent);
    const hasMarketSlug = Boolean(marketSlug.trim());
    const cacheKey = priceRequestKey;
    if (hasMarketSlug) {
      const cached = priceSeriesCacheRef.current.get(cacheKey);
      if (cached) {
        setMarketSeries(cached);
        if (!silent) {
          setDataStatus('partial');
          setLoadingMessage('Refreshing cached price series...');
        }
      } else {
        if (!silent) {
          setDataStatus(selectedMarket ? 'price_loading' : 'metadata_loading');
          setLoadingMessage(selectedMarket ? 'Loading new price window; keeping the previous chart visible...' : 'Loading market metadata...');
        }
      }
      if (import.meta.env.DEV) {
        console.debug('[quant] price load start', { cacheKey, cached: Boolean(cached), marketSlug, priceSource, timeframe, silent });
      }
    }
    if (hasMarketSlug && !silent && !priceSeriesCacheRef.current.has(cacheKey)) {
      const snapshotRequest = selectedEntityKind === 'event'
        ? fetchQuantEventPriceSeries({
          eventSlug: marketSlug,
          priceSource: backendPriceSource(priceSource),
          limit: 2500,
          maxOutcomes: 100,
          topN: EVENT_TILE_OUTCOME_LIMIT,
          maxPoints: EVENT_TILE_MAX_POINTS,
          range: 'latest',
          resolution: 'auto',
          pointFormat: 'lite',
          timeoutMs: 5000,
        })
        : fetchQuantEntitySnapshot({
          entityType: selectedEntityKind,
          marketSlug,
          priceSource: backendPriceSource(priceSource),
          maxOutcomes: 24,
          pointFormat: 'lite',
          timeoutMs: 3500,
        });
      void snapshotRequest
        .then((snapshot) => {
          if (requestSeq !== priceLoadSeq.current) return;
          if (!snapshot?.outcomes?.length) return;
          priceSeriesCacheRef.current.set(cacheKey, snapshot);
          setMarketSeries(snapshot);
          setDataStatus('partial');
          setLoadingMessage(selectedEntityKind === 'event'
            ? 'Latest outcome prices loaded; historical tile is warming...'
            : 'Latest snapshot loaded; historical tile is still warming...');
        })
        .catch((snapshotError) => {
          if (import.meta.env.DEV && !isAbortLikeError(snapshotError)) console.debug('[quant] snapshot load failed', snapshotError);
        });
    }
    const priceQuery = {
      ...semanticChartQuery,
      live: livePriceRefreshEnabled && silent && selectedEntityKind !== 'event',
    };
    void fetchQuantBuildStatus('', 12)
      .then((status) => {
        if (requestSeq === priceLoadSeq.current) setRuns(status.items || []);
      })
      .catch((statusError) => {
        if (import.meta.env.DEV && !isAbortLikeError(statusError)) console.debug('[quant] build status refresh failed', statusError);
      });
    const seriesResult = await (
      hasMarketSlug
        ? (selectedEntityKind === 'event'
          ? fetchQuantEventPriceSeries({
            ...priceQuery,
            eventSlug: marketSlug,
            viewportWidth: chartViewportWidth(),
            timeoutMs: chartRange === 'full' ? 12000 : 8000,
          })
          : fetchQuantPriceWindow({
            ...priceQuery,
            entityType: selectedEntityKind,
            marketSlug,
            viewportWidth: chartViewportWidth(),
            timeoutMs: chartRange === 'full' ? 16000 : 10000,
          }))
        : Promise.resolve(null)
    )
      .then((value) => ({ status: 'fulfilled' as const, value }))
      .catch((reason) => ({ status: 'rejected' as const, reason }));
    if (requestSeq !== priceLoadSeq.current) {
      if (import.meta.env.DEV) console.debug('[quant] stale price response ignored', { cacheKey });
      return {
        frontendRows,
        blockRows,
        marketSeries,
      };
    }
    const fulfilledSeries = seriesResult.status === 'fulfilled' ? seriesResult.value : null;
    const warmingSeries = isSeriesWarming(fulfilledSeries);
    const fulfilledPointCount = marketSeriesToPrices(fulfilledSeries).length;
    const currentPointCount = marketSeriesToPrices(marketSeries).length;
    const keepCurrentWhileWarming = Boolean(warmingSeries && marketSeries && currentPointCount > fulfilledPointCount);
    const nextMarketSeries = fulfilledSeries && !keepCurrentWhileWarming ? fulfilledSeries : marketSeries;
    const nextFrontendRows = priceSource === 'frontend' ? frontendRows : [];
    const nextBlockRows = priceSource === 'orderfilled' ? blockRows : [];
    if (seriesResult.status === 'fulfilled') {
      if (fulfilledSeries && (!warmingSeries || !keepCurrentWhileWarming)) priceSeriesCacheRef.current.set(cacheKey, fulfilledSeries);
      if (nextMarketSeries && nextMarketSeries !== marketSeries) priceSeriesCacheRef.current.set(cacheKey, nextMarketSeries);
      setMarketSeries(nextMarketSeries);
      setLastPriceRefreshAt(new Date().toLocaleTimeString());
      if (warmingSeries && !silent && selectedEntityKind === 'event') {
        if (priceRetryTimerRef.current) window.clearTimeout(priceRetryTimerRef.current);
        const retryMs = retryDelayFromSeries(fulfilledSeries);
        priceRetryTimerRef.current = window.setTimeout(() => {
          if (requestSeq !== priceLoadSeq.current) return;
          setMarketReloadKey((current) => current + 1);
        }, retryMs);
      }
    }
    if (priceSource !== 'frontend') setFrontendRows([]);
    if (priceSource !== 'orderfilled') setBlockRows([]);
    const activeRowCount = marketSeriesToPrices(nextMarketSeries).length || (priceSource === 'orderfilled' ? nextBlockRows.length : nextFrontendRows.length);
    if (hasMarketSlug) {
      if (!silent) {
        setDataStatus(warmingSeries ? 'warming' : activeRowCount ? 'ready' : 'empty');
        setLoadingMessage(warmingSeries
          ? (keepCurrentWhileWarming
            ? 'Historical price tile is warming; keeping the previous chart until the full series is ready.'
            : (fulfilledSeries?.message || 'Historical price tile is warming; showing latest outcome snapshot.'))
          : activeRowCount ? '' : 'No price rows found for this source/window');
      } else if (activeRowCount) {
        setDataStatus('ready');
      }
      if (import.meta.env.DEV) console.debug('[quant] price load complete', { cacheKey, activeRowCount });
    }
    if (hasMarketSlug && seriesResult.status === 'rejected') {
      const hasCached = priceSeriesCacheRef.current.has(cacheKey);
      if (!silent) {
        setDataStatus(hasCached ? 'partial' : 'error');
        setLoadingMessage(hasCached ? 'Showing cached data; refresh failed.' : 'Price request failed.');
      }
      throw seriesResult.reason instanceof Error ? seriesResult.reason : new Error('Quant API unavailable');
    }
    return {
      frontendRows: nextFrontendRows,
      blockRows: nextBlockRows,
      marketSeries: nextMarketSeries,
    };
  };

  const refreshBacktestRuns = async () => {
    setBacktestRunsStatus('loading');
    try {
      const payload = await fetchQuantBacktestRuns('', 25);
      setRecentBacktestRuns(payload.items || []);
      setBacktestRunsStatus((payload.items || []).length ? 'ready' : 'empty');
    } catch (runsError) {
      if (import.meta.env.DEV && !isAbortLikeError(runsError)) console.debug('[quant] backtest run history failed', runsError);
      setBacktestRunsStatus('error');
    }
  };

  const refreshBenchmarkRuns = async () => {
    try {
      const payload = await fetchQuantBacktestBenchmarks(12);
      const items = payload.items || [];
      setRecentBenchmarkRuns(items);
      if (!benchmarkRun && items.length) {
        const [latestBenchmark] = items;
        if (!latestBenchmark) return;
        const detail = await fetchQuantBacktestBenchmark(latestBenchmark.benchmarkId);
        setBenchmarkRun(detail.item || latestBenchmark);
        setBenchmarkArtifacts(detail.artifacts || []);
        const rows = await fetchQuantBacktestBenchmarkRows(latestBenchmark.benchmarkId, 1000);
        setBenchmarkRows(rows.items || []);
        setBenchmarkStatus(latestBenchmark.status === 'completed' ? 'ready' : 'idle');
      }
    } catch (benchmarkError) {
      if (import.meta.env.DEV && !isAbortLikeError(benchmarkError)) console.debug('[quant] benchmark history failed', benchmarkError);
    }
  };

  const refreshBenchmarkUniverses = async () => {
    try {
      const payload = await fetchQuantBacktestUniverses();
      setBenchmarkUniverses(payload.items || []);
    } catch (universeError) {
      if (import.meta.env.DEV && !isAbortLikeError(universeError)) console.debug('[quant] benchmark universes failed', universeError);
    }
  };

  const loadBacktestRun = async (runId: number) => {
    if (!runId) return;
    setLoading(true);
    setError('');
    setBacktestStatus(`loading #${runId}`);
    try {
      const runResponse = await fetchQuantBacktestRun(runId);
      const run = runResponse.item;
      const [metricsResult, equityResult, tradesResult, ordersResult, ledgerResult] = await Promise.all([
        fetchQuantBacktestMetrics(run.runId),
        fetchQuantBacktestEquity(run.runId),
        fetchQuantBacktestTrades(run.runId),
        fetchQuantBacktestOrders(run.runId),
        fetchQuantBacktestLedger(run.runId),
      ]);
      const runPriceSource = uiPriceSource(run.priceSource);
      const result = backtestApiToResult(run, metricsResult.items || [], equityResult.items || [], tradesResult.items || [], runPriceSource, ordersResult.items || [], ledgerResult.items || []);
      setBacktestResult(result);
      setSelectedTradeId(result.trades[0]?.id ?? null);
      setBacktestStatus(run.status || 'loaded');
      setTesterTab('runs');
      if (run.backtestEngine && ['builtin', 'backtrader', 'nautilus_trader'].includes(run.backtestEngine)) {
        setBacktestEngine(run.backtestEngine as BacktestEngine);
      }
      setPriceSource(runPriceSource);
      if (run.marketSlug) {
        setMarketSlug(run.marketSlug);
        setSelectedEntityKindHint('market');
        setSelectedMarketMeta(null);
        setMarketSearchQuery('');
        setViewportMode('preset');
        setViewportResetSeq((current) => current + 1);
        setMarketReloadKey((current) => current + 1);
      }
      setSelectedBacktestAction(String(run.tokenSide || 'YES').toUpperCase() === 'NO' ? 'NO' : 'YES');
      setStrategyParameters((current) => normalizeStrategyParameters({
        ...current,
        entryThreshold: strategyNumber(run.entryThreshold, current.entryThreshold),
        exitThreshold: strategyNumber(run.exitThreshold, current.exitThreshold),
        stopLoss: strategyNumber(run.stopLoss, current.stopLoss),
        takeProfit: strategyNumber(run.takeProfit, current.takeProfit),
        maxHoldingBars: strategyNumber(run.maxHoldingBars, current.maxHoldingBars),
        initialCapital: strategyNumber(run.initialCapital, current.initialCapital),
        positionSize: strategyNumber(run.positionSize, current.positionSize),
        feeBps: strategyNumber(run.feeBps, current.feeBps),
        slippageBps: strategyNumber(run.slippageBps, current.slippageBps),
        liquidityCapPct: strategyNumber(run.liquidityCapPct, current.liquidityCapPct),
        maxPositionNotional: strategyNumber(run.maxPositionNotional, current.maxPositionNotional),
        minFillPct: strategyNumber(run.minFillPct, current.minFillPct),
        executionProfile: current.executionProfile,
        orderRole: current.orderRole,
        executionPriceMode: run.executionPriceMode === 'ORDERFILLED_LIMIT_REPLAY' || run.executionPriceMode === 'DEPTH' || run.executionPriceMode === 'LEGACY' || run.executionPriceMode === 'ORDERFILLED' ? run.executionPriceMode : current.executionPriceMode,
        finalValuationMode: run.finalValuationMode === 'FORCE_CLOSE' ? 'FORCE_CLOSE' : current.finalValuationMode,
        buyLimitPrice: strategyNumber(run.buyLimitPrice, current.buyLimitPrice ?? current.entryThreshold),
        sellLimitPrice: strategyNumber(run.sellLimitPrice, current.sellLimitPrice ?? 0.99),
        settlementValue: run.settlementValue === null || run.settlementValue === undefined ? current.settlementValue : strategyNumber(run.settlementValue, current.settlementValue ?? 0),
        latencySeconds: strategyNumber(run.latencySeconds, current.latencySeconds),
        latencyBlocks: current.latencyBlocks,
        maxBookStalenessSeconds: strategyNumber(run.maxBookStalenessSeconds, current.maxBookStalenessSeconds),
        adverseSlippageCents: current.adverseSlippageCents,
        fillProbabilityHaircutPct: current.fillProbabilityHaircutPct,
        allowPartialFill: typeof run.allowPartialFill === 'boolean' ? run.allowPartialFill : current.allowPartialFill,
        minFillSize: strategyNumber(run.minFillSize, current.minFillSize),
        rejectOnStaleBook: typeof run.rejectOnStaleBook === 'boolean' ? run.rejectOnStaleBook : current.rejectOnStaleBook,
      }));
      void refreshBacktestRuns();
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : `Backtest run #${runId} unavailable`);
      setBacktestStatus('failed');
    } finally {
      setLoading(false);
    }
  };

  function buildBacktestExecutionContext(scope: string, points: PricePoint[], segment?: string) {
    const firstPoint = points[0] || null;
    const lastPoint = points[points.length - 1] || null;
    const bookSide = selectedBacktestAction === 'NO' ? liveLob?.no : liveLob?.yes;
    const bidDepth = sumBookDepth(bookSide?.bids, 5);
    const askDepth = sumBookDepth(bookSide?.asks, 5);
    return {
      model: 'fixed_threshold_v1',
      scope,
      segment: segment || scope,
      fill_model: strategyParameters.executionPriceMode === 'ORDERFILLED_LIMIT_REPLAY'
        ? 'orderfilled_limit_cross_then_settlement'
        : 'close_price_with_bps_slippage_volume_cap_and_live_clob_snapshot',
      selected_outcome: selectedOutcomeRow?.fullLabel || selectedOutcome?.outcomeLabel || '',
      selected_side: selectedBacktestAction,
      selected_token_id: selectedBacktestAction === 'NO'
        ? selectedOutcome?.buyNoTokenId
        : selectedOutcome?.buyYesTokenId || selectedOutcome?.tokenId,
      requested_range: {
        first_x: firstPoint?.timestamp ?? null,
        last_x: lastPoint?.timestamp ?? null,
        rows: points.length,
        source: backendPriceSource(priceSource),
      },
      execution_parameters: {
        position_size: strategyParameters.positionSize,
        fee_bps: strategyParameters.feeBps,
        slippage_bps: strategyParameters.slippageBps,
        liquidity_cap_pct: strategyParameters.liquidityCapPct,
        max_position_notional: strategyParameters.maxPositionNotional,
        min_fill_pct: strategyParameters.minFillPct,
        execution_profile: strategyParameters.executionProfile,
        order_role: strategyParameters.orderRole,
        execution_price_mode: strategyParameters.executionPriceMode,
        final_valuation_mode: strategyParameters.finalValuationMode,
        buy_limit_price: strategyParameters.buyLimitPrice ?? null,
        sell_limit_price: strategyParameters.sellLimitPrice ?? null,
        settlement_value: strategyParameters.settlementValue ?? null,
        latency_seconds: strategyParameters.latencySeconds,
        latency_blocks: strategyParameters.latencyBlocks,
        max_book_staleness_seconds: strategyParameters.maxBookStalenessSeconds,
        adverse_slippage_cents: strategyParameters.adverseSlippageCents,
        fill_probability_haircut_pct: strategyParameters.fillProbabilityHaircutPct,
        allow_partial_fill: strategyParameters.allowPartialFill,
        min_fill_size: strategyParameters.minFillSize,
        reject_on_stale_book: strategyParameters.rejectOnStaleBook,
      },
      live_clob: {
        status: liveLobStatus,
        source: liveLob?.source || '',
        book_status: liveLob?.bookStatus || '',
        fetched_at: liveLob?.fetchedAt || '',
        side: selectedBacktestAction,
        best_bid: finiteNumber(bookSide?.bestBid),
        best_ask: finiteNumber(bookSide?.bestAsk),
        spread: finiteNumber(bookSide?.spread),
        bid_depth_5: bidDepth,
        ask_depth_5: askDepth,
        top_depth: bidDepth + askDepth,
      },
      fill_estimate: {
        target: selectedBookFillEstimate.target,
        spent: Number(selectedBookFillEstimate.spent.toFixed(6)),
        remaining: Number(selectedBookFillEstimate.remaining.toFixed(6)),
        fill_pct: Number((selectedBookFillEstimate.fillPct * 100).toFixed(4)),
        vwap: selectedBookFillEstimate.vwap === null ? null : Number(selectedBookFillEstimate.vwap.toFixed(6)),
        slippage: selectedBookFillEstimate.slippage === null ? null : Number(selectedBookFillEstimate.slippage.toFixed(6)),
        levels_used: selectedBookFillEstimate.levelsUsed,
      },
      execution_quality: {
        status: bookExecutionQuality.status,
        confidence: bookExecutionQuality.confidence,
        spread: bookExecutionQuality.spread,
        top_depth: bookExecutionQuality.topDepth,
        drift: bookExecutionQuality.drift,
        age_seconds: bookExecutionQuality.ageSeconds,
        caveats: bookExecutionQuality.caveats.slice(0, 8),
      },
      data_quality: {
        status: dataQuality.health,
        rows: dataQuality.outcomeRows,
        gap_count: dataQuality.gapCount,
        spike_count: dataQuality.spikeCount,
        latest_block: dataQuality.latestBlock,
        source: dataQuality.source,
        caveats: dataQuality.warnings.slice(0, 8),
      },
    };
  }

  const runBacktest = async () => {
    setLoading(true);
    setError('');
    setBacktestStatus('submitting');
    try {
      if (!marketSlug.trim()) {
        throw new Error('market_slug is required for real backtest');
      }
      const loadedSeriesPrices = outcomePricePoints(selectedOutcome, selectedBacktestAction);
      const loadedBlockRows = blockRows.filter((row) => String(row.tokenSide || '').toUpperCase() === 'YES');
      const hasLoadedRows = loadedSeriesPrices.length > 0 || (priceSource === 'orderfilled' ? loadedBlockRows.length > 0 : frontendRows.length > 0);
      const nextRows = hasLoadedRows
        ? { frontendRows, blockRows, marketSeries }
        : await refreshQuantRows();
      const nextSelectedOutcome = (
        nextRows.marketSeries?.outcomes?.find((outcome) => outcome.tokenId === selectedOutcomeTokenId)
        || nextRows.marketSeries?.outcomes?.[0]
        || selectedOutcome
      );
      const seriesPrices = outcomePricePoints(nextSelectedOutcome, selectedBacktestAction);
      const backtestBlockRows = nextRows.blockRows.filter((row) => String(row.tokenSide || '').toUpperCase() === 'YES');
      const sourceRows = seriesPrices.length ? seriesPrices : (priceSource === 'orderfilled' ? blockToPrices(backtestBlockRows) : frontendToPrices(nextRows.frontendRows));
      if (!sourceRows.length) {
        throw new Error(`No ${backendPriceSource(priceSource)} rows for ${marketSlug.trim()}`);
      }
      const firstX = sourceRows[0]?.timestamp;
      const lastX = sourceRows[sourceRows.length - 1]?.timestamp;
      const firstBlock = priceSource === 'orderfilled' ? firstX : backtestBlockRows[0]?.blockNumber;
      const lastBlock = priceSource === 'orderfilled' ? lastX : backtestBlockRows[backtestBlockRows.length - 1]?.blockNumber;
      const firstTs = priceSource === 'frontend' ? firstX : nextRows.frontendRows[0]?.timestamp;
      const lastTs = priceSource === 'frontend' ? lastX : nextRows.frontendRows[nextRows.frontendRows.length - 1]?.timestamp;
      const selectedTokenId = selectedBacktestAction === 'NO'
        ? nextSelectedOutcome?.buyNoTokenId
        : nextSelectedOutcome?.buyYesTokenId || nextSelectedOutcome?.tokenId;
      const selectedTokenSide = selectedBacktestAction === 'NO'
        ? nextSelectedOutcome?.buyNoTokenSide
        : nextSelectedOutcome?.buyYesTokenSide || nextSelectedOutcome?.tokenSide;
      const selectedOutcomeLabel = selectedBacktestAction === 'NO'
        ? nextSelectedOutcome?.buyNoLabel || `${nextSelectedOutcome?.outcomeLabel || 'Outcome'} No`
        : nextSelectedOutcome?.buyYesLabel || nextSelectedOutcome?.outcomeLabel;
      const created = await createQuantBacktestRun({
        marketSlug: (nextSelectedOutcome?.marketSlug || marketSlug).trim(),
        tokenSide: selectedTokenSide || 'YES',
        tokenId: selectedTokenId || undefined,
        outcomeLabel: selectedOutcomeLabel,
        priceSource: backendPriceSource(priceSource),
        backtestEngine,
        ...(priceSource === 'orderfilled' && firstBlock && lastBlock ? { fromBlock: String(firstBlock), toBlock: String(lastBlock) } : {}),
        ...(priceSource === 'frontend' && firstTs && lastTs ? { from: String(firstTs), to: String(lastTs) } : {}),
        ...backtestParameterPayload(strategyParameters),
        executionContext: buildBacktestExecutionContext('single', sourceRows),
      });
      setBacktestStatus(created.item.status);
      const completedRun = ['queued', 'running'].includes(created.item.status)
        ? await waitForRun(created.runId, (run) => setBacktestStatus(run.status))
        : created.item;
      if (completedRun.status === 'failed') {
        throw new Error(completedRun.error || 'Backtest failed');
      }
      if (['queued', 'running'].includes(completedRun.status)) {
        throw new Error(`Backtest ${completedRun.status}; try refresh in a moment`);
      }
      const [metricsResult, equityResult, tradesResult, ordersResult, ledgerResult] = await Promise.all([
        fetchQuantBacktestMetrics(completedRun.runId),
        fetchQuantBacktestEquity(completedRun.runId),
        fetchQuantBacktestTrades(completedRun.runId),
        fetchQuantBacktestOrders(completedRun.runId),
        fetchQuantBacktestLedger(completedRun.runId),
      ]);
      const result = backtestApiToResult(completedRun, metricsResult.items || [], equityResult.items || [], tradesResult.items || [], priceSource, ordersResult.items || [], ledgerResult.items || []);
      setBacktestResult(result);
      setSelectedTradeId(result.trades[0]?.id ?? null);
      setBacktestStatus(completedRun.status);
      void refreshBacktestRuns();
      if (!nextRows.frontendRows.length && !nextRows.blockRows.length) setError('');
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : 'Quant API unavailable');
      setBacktestStatus('failed');
    } finally {
      setLoading(false);
    }
  };

  const runBenchmark = async () => {
    setError('');
    setBenchmarkStatus('loading');
    setTesterTab('benchmark');
    setStrategyDrawerCollapsed(false);
    try {
      const created = await createQuantBacktestBenchmark({
        universe: benchmarkUniverse,
        limit: benchmarkLimit,
        strategy: 'favorite_hold_v1',
        strategySpec: {
          minProbability: 0.6,
          maxProbability: 0.8,
          stake: 10,
          initialCapital: strategyParameters.initialCapital || 1000,
          maxDailyCost: 20,
          maxConcurrentPositions: 2,
        },
        profileBundle: 'fast-vs-accurate',
        stake: 10,
        initialCapital: strategyParameters.initialCapital || 1000,
        maxDailyCost: 20,
        maxConcurrentPositions: 2,
        forceBlockReplayBackfill: false,
      });
      setBenchmarkRun(created.item);
      setBenchmarkArtifacts(created.artifacts || []);
      setBenchmarkRows([]);
      let currentRun = created.item;
      setBenchmarkStatus(currentRun.status === 'completed' ? 'ready' : 'loading');
      for (let attempt = 0; attempt < 180 && currentRun.status !== 'completed' && currentRun.status !== 'failed'; attempt += 1) {
        await sleep(2000);
        const detail = await fetchQuantBacktestBenchmark(created.benchmarkId, false);
        currentRun = detail.item;
        setBenchmarkRun(detail.item);
        setBenchmarkStatus(currentRun.status === 'completed' ? 'ready' : 'loading');
      }
      if (currentRun.status === 'failed') {
        throw new Error(currentRun.error || 'Benchmark failed');
      }
      if (currentRun.status !== 'completed') {
        throw new Error(`Benchmark #${created.benchmarkId} is still ${currentRun.status || 'running'}`);
      }
      const detail = await fetchQuantBacktestBenchmark(created.benchmarkId, true);
      setBenchmarkRun(detail.item);
      setBenchmarkArtifacts(detail.artifacts || []);
      const rows = await fetchQuantBacktestBenchmarkRows(created.benchmarkId, 1000);
      setBenchmarkRows(rows.items || []);
      void refreshBenchmarkRuns();
    } catch (benchmarkError) {
      setBenchmarkStatus('error');
      setError(benchmarkError instanceof Error ? benchmarkError.message : 'Benchmark failed');
    }
  };

  const runBatchBacktest = async () => {
    setError('');
    setBatchBacktestStatus('running');
    setBacktestStatus('batch running');
    setTesterTab('runs');
    setStrategyDrawerCollapsed(false);
    try {
      if (!marketSlug.trim()) throw new Error('market_slug is required for batch backtest');
      const nextRows = marketSeries?.outcomes?.length
        ? { frontendRows, blockRows, marketSeries }
        : await refreshQuantRows();
      const eventTitle = nextRows.marketSeries?.event?.eventTitle || nextRows.marketSeries?.market?.marketTitle || '';
      const candidates = (nextRows.marketSeries?.outcomes || [])
        .map((outcome, index) => ({
          outcome,
          index,
          label: deriveEventOutcomeLabel(eventTitle, outcome.marketTitle, outcome.outcomeLabel),
          rows: toNumber(outcome.rows),
          latest: toNumber(outcome.buyYesPrice ?? outcome.latestPrice),
        }))
        .filter((row) => row.rows > 0)
        .sort((left, right) => right.latest - left.latest || right.rows - left.rows || left.index - right.index)
        .slice(0, 5);
      if (!candidates.length) throw new Error('No loaded event outcomes are eligible for batch backtest');

      const initialRows: BatchBacktestRow[] = candidates.map(({ outcome, label, rows }) => ({
        key: outcome.tokenId || outcome.marketSlug || label,
        outcome: label,
        marketSlug: outcome.marketSlug || marketSlug,
        tokenSide: 'YES',
        rows,
        trades: 0,
        netProfit: '-',
        totalReturn: '-',
        maxDrawdown: '-',
        status: 'queued',
      }));
      setBatchBacktestRows(initialRows);

      const updateBatchRow = (key: string, patch: Partial<BatchBacktestRow>) => {
        setBatchBacktestRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)));
      };

      for (const candidate of candidates) {
        const { outcome, label } = candidate;
        const key = outcome.tokenId || outcome.marketSlug || label;
        try {
          updateBatchRow(key, { status: 'submitting' });
          const seriesPrices = outcomePricePoints(outcome, 'YES');
          if (!seriesPrices.length) throw new Error('No YES points for this outcome');
          const firstX = seriesPrices[0]?.timestamp;
          const lastX = seriesPrices[seriesPrices.length - 1]?.timestamp;
          const selectedTokenId = outcome.buyYesTokenId || outcome.tokenId;
          const created = await createQuantBacktestRun({
            marketSlug: (outcome.marketSlug || marketSlug).trim(),
            tokenSide: outcome.buyYesTokenSide || outcome.tokenSide || 'YES',
            tokenId: selectedTokenId || undefined,
            outcomeLabel: outcome.buyYesLabel || outcome.outcomeLabel || label,
            priceSource: backendPriceSource(priceSource),
            backtestEngine,
            ...(priceSource === 'orderfilled' && firstX && lastX ? { fromBlock: String(firstX), toBlock: String(lastX) } : {}),
            ...(priceSource === 'frontend' && firstX && lastX ? { from: String(firstX), to: String(lastX) } : {}),
            ...backtestParameterPayload(strategyParameters),
            executionContext: buildBacktestExecutionContext('batch_top5', seriesPrices, label),
          });
          updateBatchRow(key, { runId: created.runId, status: created.item.status });
          const completedRun = ['queued', 'running'].includes(created.item.status)
            ? await waitForRun(created.runId, (run) => updateBatchRow(key, { status: run.status }))
            : created.item;
          if (completedRun.status === 'failed') throw new Error(completedRun.error || 'Backtest failed');
          const [metricsResult, tradesResult] = await Promise.all([
            fetchQuantBacktestMetrics(completedRun.runId),
            fetchQuantBacktestTrades(completedRun.runId, 1000),
          ]);
          updateBatchRow(key, {
            runId: completedRun.runId,
            rows: toNumber(completedRun.rowsProcessed) || seriesPrices.length,
            trades: tradesResult.items?.length || 0,
            netProfit: metricText(metricsResult.items || [], ['net_profit'], '-'),
            totalReturn: metricText(metricsResult.items || [], ['total_return'], '-'),
            maxDrawdown: metricText(metricsResult.items || [], ['max_drawdown'], '-'),
            status: completedRun.status,
          });
        } catch (batchError) {
          updateBatchRow(key, {
            status: 'failed',
            error: batchError instanceof Error ? batchError.message : 'Batch backtest failed',
          });
        }
      }
      setBatchBacktestStatus('complete');
      setBacktestStatus('batch complete');
      void refreshBacktestRuns();
    } catch (batchError) {
      setBatchBacktestStatus('failed');
      setBacktestStatus('failed');
      setError(batchError instanceof Error ? batchError.message : 'Batch backtest failed');
    }
  };

  const runSplitBacktest = async () => {
    setError('');
    setSplitBacktestStatus('running');
    setBacktestStatus('split running');
    setTesterTab('runs');
    setStrategyDrawerCollapsed(false);
    try {
      if (!marketSlug.trim()) throw new Error('market_slug is required for train/test split');
      const nextRows = marketSeries?.outcomes?.length
        ? { frontendRows, blockRows, marketSeries }
        : await refreshQuantRows();
      const nextSelectedOutcome = (
        nextRows.marketSeries?.outcomes?.find((outcome) => outcome.tokenId === selectedOutcomeTokenId)
        || nextRows.marketSeries?.outcomes?.[0]
        || selectedOutcome
      );
      const seriesPrices = outcomePricePoints(nextSelectedOutcome, selectedBacktestAction);
      if (seriesPrices.length < 120) throw new Error('Need at least 120 loaded points for a train/test split');
      const splitIndex = Math.max(20, Math.min(seriesPrices.length - 20, Math.floor(seriesPrices.length * 0.7)));
      const trainPoints = seriesPrices.slice(0, splitIndex);
      const testPoints = seriesPrices.slice(splitIndex);
      const selectedTokenId = selectedBacktestAction === 'NO'
        ? nextSelectedOutcome?.buyNoTokenId
        : nextSelectedOutcome?.buyYesTokenId || nextSelectedOutcome?.tokenId;
      const selectedTokenSide = selectedBacktestAction === 'NO'
        ? nextSelectedOutcome?.buyNoTokenSide
        : nextSelectedOutcome?.buyYesTokenSide || nextSelectedOutcome?.tokenSide;
      const splitTokenSide: BacktestAction = selectedTokenSide === 'NO' ? 'NO' : 'YES';
      const selectedOutcomeLabel = selectedBacktestAction === 'NO'
        ? nextSelectedOutcome?.buyNoLabel || `${nextSelectedOutcome?.outcomeLabel || 'Outcome'} No`
        : nextSelectedOutcome?.buyYesLabel || nextSelectedOutcome?.outcomeLabel;
      const baseMarketSlug = (nextSelectedOutcome?.marketSlug || marketSlug).trim();
      const segments = [
        { key: 'train', label: 'Train 70%', points: trainPoints },
        { key: 'test', label: 'Test 30%', points: testPoints },
      ];
      setSplitBacktestRows(segments.map((segment) => ({
        key: segment.key,
        outcome: segment.label,
        marketSlug: baseMarketSlug,
        tokenSide: splitTokenSide,
        rows: segment.points.length,
        trades: 0,
        netProfit: '-',
        totalReturn: '-',
        maxDrawdown: '-',
        status: 'queued',
      })));
      const updateSplitRow = (key: string, patch: Partial<BatchBacktestRow>) => {
        setSplitBacktestRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)));
      };

      let lastResult: BacktestResult | null = null;
      for (const segment of segments) {
        const firstX = segment.points[0]?.timestamp;
        const lastX = segment.points[segment.points.length - 1]?.timestamp;
        try {
          updateSplitRow(segment.key, { status: 'submitting' });
          const created = await createQuantBacktestRun({
            marketSlug: baseMarketSlug,
            tokenSide: splitTokenSide,
            tokenId: selectedTokenId || undefined,
            outcomeLabel: `${selectedOutcomeLabel || 'Outcome'} · ${segment.label}`,
            priceSource: backendPriceSource(priceSource),
            backtestEngine,
            ...(priceSource === 'orderfilled' && firstX && lastX ? { fromBlock: String(firstX), toBlock: String(lastX) } : {}),
            ...(priceSource === 'frontend' && firstX && lastX ? { from: String(firstX), to: String(lastX) } : {}),
            ...backtestParameterPayload(strategyParameters),
            executionContext: buildBacktestExecutionContext('split_70_30', segment.points, segment.label),
          });
          updateSplitRow(segment.key, { runId: created.runId, status: created.item.status });
          const completedRun = ['queued', 'running'].includes(created.item.status)
            ? await waitForRun(created.runId, (run) => updateSplitRow(segment.key, { status: run.status }))
            : created.item;
          if (completedRun.status === 'failed') throw new Error(completedRun.error || 'Backtest failed');
          const [metricsResult, equityResult, tradesResult] = await Promise.all([
            fetchQuantBacktestMetrics(completedRun.runId),
            fetchQuantBacktestEquity(completedRun.runId),
            fetchQuantBacktestTrades(completedRun.runId, 1000),
          ]);
          const segmentResult = backtestApiToResult(completedRun, metricsResult.items || [], equityResult.items || [], tradesResult.items || [], priceSource);
          lastResult = segmentResult;
          updateSplitRow(segment.key, {
            runId: completedRun.runId,
            rows: toNumber(completedRun.rowsProcessed) || segment.points.length,
            trades: tradesResult.items?.length || 0,
            netProfit: metricText(metricsResult.items || [], ['net_profit'], '-'),
            totalReturn: metricText(metricsResult.items || [], ['total_return'], '-'),
            maxDrawdown: metricText(metricsResult.items || [], ['max_drawdown'], '-'),
            status: completedRun.status,
          });
        } catch (splitError) {
          updateSplitRow(segment.key, {
            status: 'failed',
            error: splitError instanceof Error ? splitError.message : 'Split backtest failed',
          });
        }
      }
      if (lastResult) {
        setBacktestResult(lastResult);
        setSelectedTradeId(lastResult.trades[0]?.id ?? null);
        setBacktestStatus('split complete');
      }
      setSplitBacktestStatus('complete');
      void refreshBacktestRuns();
    } catch (splitError) {
      setSplitBacktestStatus('failed');
      setBacktestStatus('failed');
      setError(splitError instanceof Error ? splitError.message : 'Train/test split failed');
    }
  };

  const runWalkForwardBacktest = async () => {
    setError('');
    setWalkForwardStatus('running');
    setBacktestStatus('walk-forward running');
    setTesterTab('runs');
    setStrategyDrawerCollapsed(false);
    try {
      if (!marketSlug.trim()) throw new Error('market_slug is required for walk-forward backtest');
      const nextRows = marketSeries?.outcomes?.length
        ? { frontendRows, blockRows, marketSeries }
        : await refreshQuantRows();
      const nextSelectedOutcome = (
        nextRows.marketSeries?.outcomes?.find((outcome) => outcome.tokenId === selectedOutcomeTokenId)
        || nextRows.marketSeries?.outcomes?.[0]
        || selectedOutcome
      );
      const seriesPrices = outcomePricePoints(nextSelectedOutcome, selectedBacktestAction);
      if (seriesPrices.length < 180) throw new Error('Need at least 180 loaded points for walk-forward backtests');

      const selectedTokenId = selectedBacktestAction === 'NO'
        ? nextSelectedOutcome?.buyNoTokenId
        : nextSelectedOutcome?.buyYesTokenId || nextSelectedOutcome?.tokenId;
      const selectedTokenSide = selectedBacktestAction === 'NO'
        ? nextSelectedOutcome?.buyNoTokenSide
        : nextSelectedOutcome?.buyYesTokenSide || nextSelectedOutcome?.tokenSide;
      const walkTokenSide: BacktestAction = selectedTokenSide === 'NO' ? 'NO' : 'YES';
      const selectedOutcomeLabel = selectedBacktestAction === 'NO'
        ? nextSelectedOutcome?.buyNoLabel || `${nextSelectedOutcome?.outcomeLabel || 'Outcome'} No`
        : nextSelectedOutcome?.buyYesLabel || nextSelectedOutcome?.outcomeLabel;
      const baseMarketSlug = (nextSelectedOutcome?.marketSlug || marketSlug).trim();
      const trainSize = Math.max(80, Math.floor(seriesPrices.length * 0.45));
      const testSize = Math.max(40, Math.floor(seriesPrices.length * 0.18));
      const stepSize = Math.max(testSize, Math.floor(seriesPrices.length * 0.16));
      const windows: Array<{ key: string; label: string; points: PricePoint[] }> = [];

      for (
        let startIndex = 0, windowIndex = 1;
        startIndex + trainSize + testSize <= seriesPrices.length && windowIndex <= 3;
        startIndex += stepSize, windowIndex += 1
      ) {
        const trainPoints = seriesPrices.slice(startIndex, startIndex + trainSize);
        const testPoints = seriesPrices.slice(startIndex + trainSize, startIndex + trainSize + testSize);
        windows.push({ key: `wf-${windowIndex}-train`, label: `WF ${windowIndex} Train`, points: trainPoints });
        windows.push({ key: `wf-${windowIndex}-test`, label: `WF ${windowIndex} Test`, points: testPoints });
      }

      if (!windows.length) throw new Error('Loaded rows cannot form a valid walk-forward window');

      setWalkForwardRows(windows.map((segment) => ({
        key: segment.key,
        outcome: segment.label,
        marketSlug: baseMarketSlug,
        tokenSide: walkTokenSide,
        rows: segment.points.length,
        trades: 0,
        netProfit: '-',
        totalReturn: '-',
        maxDrawdown: '-',
        status: 'queued',
      })));
      const updateWalkForwardRow = (key: string, patch: Partial<BatchBacktestRow>) => {
        setWalkForwardRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)));
      };

      let latestTestResult: BacktestResult | null = null;
      let completedCount = 0;
      for (const segment of windows) {
        const firstX = segment.points[0]?.timestamp;
        const lastX = segment.points[segment.points.length - 1]?.timestamp;
        try {
          updateWalkForwardRow(segment.key, { status: 'submitting' });
          const created = await createQuantBacktestRun({
            marketSlug: baseMarketSlug,
            tokenSide: walkTokenSide,
            tokenId: selectedTokenId || undefined,
            outcomeLabel: `${selectedOutcomeLabel || 'Outcome'} · ${segment.label}`,
            priceSource: backendPriceSource(priceSource),
            backtestEngine,
            ...(priceSource === 'orderfilled' && firstX && lastX ? { fromBlock: String(firstX), toBlock: String(lastX) } : {}),
            ...(priceSource === 'frontend' && firstX && lastX ? { from: String(firstX), to: String(lastX) } : {}),
            ...backtestParameterPayload(strategyParameters),
            executionContext: buildBacktestExecutionContext('walk_forward', segment.points, segment.label),
          });
          updateWalkForwardRow(segment.key, { runId: created.runId, status: created.item.status });
          const completedRun = ['queued', 'running'].includes(created.item.status)
            ? await waitForRun(created.runId, (run) => updateWalkForwardRow(segment.key, { status: run.status }))
            : created.item;
          if (completedRun.status === 'failed') throw new Error(completedRun.error || 'Backtest failed');
          const [metricsResult, equityResult, tradesResult] = await Promise.all([
            fetchQuantBacktestMetrics(completedRun.runId),
            fetchQuantBacktestEquity(completedRun.runId),
            fetchQuantBacktestTrades(completedRun.runId, 1000),
          ]);
          const segmentResult = backtestApiToResult(completedRun, metricsResult.items || [], equityResult.items || [], tradesResult.items || [], priceSource);
          if (segment.key.endsWith('-test')) latestTestResult = segmentResult;
          completedCount += 1;
          updateWalkForwardRow(segment.key, {
            runId: completedRun.runId,
            rows: toNumber(completedRun.rowsProcessed) || segment.points.length,
            trades: tradesResult.items?.length || 0,
            netProfit: metricText(metricsResult.items || [], ['net_profit'], '-'),
            totalReturn: metricText(metricsResult.items || [], ['total_return'], '-'),
            maxDrawdown: metricText(metricsResult.items || [], ['max_drawdown'], '-'),
            status: completedRun.status,
          });
        } catch (walkError) {
          updateWalkForwardRow(segment.key, {
            status: 'failed',
            error: walkError instanceof Error ? walkError.message : 'Walk-forward backtest failed',
          });
        }
      }

      if (latestTestResult) {
        setBacktestResult(latestTestResult);
        setSelectedTradeId(latestTestResult.trades[0]?.id ?? null);
      }
      if (!completedCount) throw new Error('All walk-forward segments failed');
      setWalkForwardStatus('complete');
      setBacktestStatus('walk-forward complete');
      void refreshBacktestRuns();
    } catch (walkError) {
      setWalkForwardStatus('failed');
      setBacktestStatus('failed');
      setError(walkError instanceof Error ? walkError.message : 'Walk-forward backtest failed');
    }
  };

  const selectMarketSlug = (slug: string) => {
    const nextSlug = slug.trim();
    if (priceRetryTimerRef.current) {
      window.clearTimeout(priceRetryTimerRef.current);
      priceRetryTimerRef.current = null;
    }
    const nextMarket = quantMarkets.find((market) => market.marketSlug === nextSlug && market.itemKind === 'event')
      || quantMarkets.find((market) => market.marketSlug === nextSlug && market.tokenSide === 'YES')
      || quantMarkets.find((market) => market.marketSlug === nextSlug)
      || null;
    setMarketSlug(nextSlug);
    setMarketSearchQuery('');
    setMarketReloadKey((current) => current + 1);
    setViewportMode('preset');
    setViewportResetSeq((current) => current + 1);
    setSelectedMarketMeta(nextMarket);
    setSelectedEntityKindHint(nextMarket?.itemKind === 'event' ? 'event' : 'market');
    const cached = nextSlug ? priceSeriesCacheRef.current.get(seriesKeyForSlug(nextSlug, nextMarket?.itemKind === 'event' ? 'event' : 'market')) : null;
    setFrontendRows([]);
    setBlockRows([]);
    if (cached) setMarketSeries(cached);
    setSelectedOutcomeTokenId('');
    setSelectedBacktestAction('YES');
    setBacktestResult(emptyBacktestResult());
    setSelectedTradeId(null);
    setError('');
    autoSelectedDefaultRef.current = true;
    if (nextSlug) {
      setDataStatus(cached ? 'partial' : 'metadata_loading');
      setLoadingMessage(cached ? 'Rendering cached data...' : 'Switching market; keeping stale chart while snapshot loads...');
    }
  };

  const changeTimeframePreset = (value: string) => {
    if (viewportFetchTimerRef.current) {
      window.clearTimeout(viewportFetchTimerRef.current);
      viewportFetchTimerRef.current = null;
    }
    viewportFetchSeq.current += 1;
    setTimeframe(value);
    setViewportMode('preset');
    setViewportResetSeq((current) => current + 1);
  };

  const changePriceSource = (value: PriceSource) => {
    setPriceSource(value);
    setViewportMode('preset');
    setViewportResetSeq((current) => current + 1);
  };

  const updateViewportMode = (mode: 'preset' | 'custom') => {
    setViewportMode((current) => (current === mode ? current : mode));
  };

  const requestViewportWindow = (windowRange: { fromX: number; toX: number; pointCount: number; viewportWidth?: number }) => {
    if (!marketSlug.trim()) return;
    if (!Number.isFinite(windowRange.fromX) || !Number.isFinite(windowRange.toX)) return;
    const fromX = Math.floor(Math.min(windowRange.fromX, windowRange.toX));
    const toX = Math.ceil(Math.max(windowRange.fromX, windowRange.toX));
    if (toX <= fromX) return;
    if (viewportFetchTimerRef.current) window.clearTimeout(viewportFetchTimerRef.current);
    const nextSeq = viewportFetchSeq.current + 1;
    viewportFetchSeq.current = nextSeq;
    const entityType = selectedEntityKind;
    const slug = marketSlug.trim();
    const source = backendPriceSource(priceSource);
    const maxPoints = tilePointBudget('window', windowRange.viewportWidth || chartViewportWidth());
    const cacheKey = [
      'window',
      entityType,
      slug,
      source,
      fromX,
      toX,
      maxPoints,
      EVENT_TILE_OUTCOME_LIMIT,
    ].join('|');
    const cached = priceSeriesCacheRef.current.get(cacheKey);
    if (cached) {
      setMarketSeries(cached);
      setDataStatus('ready');
      setViewportMode('custom');
      return;
    }
    viewportFetchTimerRef.current = window.setTimeout(() => {
      setViewportMode('custom');
      setDataStatus((current) => (current === 'ready' ? 'partial' : current));
      setLoadingMessage('Loading viewport tile...');
      void fetchQuantPriceWindow({
        entityType,
        marketSlug: entityType === 'market' ? slug : undefined,
        eventSlug: entityType === 'event' ? slug : undefined,
        priceSource: source,
        ...(source === 'orderfilled_block_close' ? { fromBlock: String(fromX), toBlock: String(toX) } : { from: String(fromX), to: String(toX) }),
        limit: Math.max(2500, maxPoints * 8),
        maxOutcomes: entityType === 'event' ? EVENT_TILE_OUTCOME_LIMIT : 24,
        topN: entityType === 'event' ? EVENT_TILE_OUTCOME_LIMIT : undefined,
        maxPoints,
        range: 'window',
        resolution: 'auto',
        pointFormat: 'lite',
        viewportWidth: windowRange.viewportWidth || chartViewportWidth(),
        timeoutMs: 10000,
      })
        .then((payload) => {
          if (nextSeq !== viewportFetchSeq.current) return;
          if (!payload?.outcomes?.length) return;
          setMarketSeries((current) => {
            const merged = mergeMarketSeries(current, payload) || payload;
            priceSeriesCacheRef.current.set(cacheKey, merged);
            priceSeriesCacheRef.current.set(priceRequestKey, merged);
            return merged;
          });
          setDataStatus('ready');
          setLoadingMessage('');
          setLastPriceRefreshAt(new Date().toLocaleTimeString());
        })
        .catch((viewportError) => {
          if (nextSeq !== viewportFetchSeq.current) return;
          if (import.meta.env.DEV && !isAbortLikeError(viewportError)) console.debug('[quant] viewport tile failed', viewportError);
          setDataStatus((current) => (current === 'partial' ? 'ready' : current));
          setLoadingMessage('');
        });
    }, 220);
  };

  useEffect(() => {
    marketSlugRef.current = marketSlug;
  }, [marketSlug]);

  useEffect(() => {
    void refreshBacktestRuns();
    void refreshBenchmarkRuns();
    void refreshBenchmarkUniverses();
  }, []);

  const prefetchMarketSlug = (slug: string) => {
    const nextSlug = slug.trim();
    const nextMarket = quantMarkets.find((market) => market.marketSlug === nextSlug && market.itemKind === 'event')
      || quantMarkets.find((market) => market.marketSlug === nextSlug);
    const nextKind = nextMarket?.itemKind === 'event' ? 'event' : 'market';
    const cacheKey = seriesKeyForSlug(nextSlug, nextKind);
    if (!nextSlug || priceSeriesCacheRef.current.has(cacheKey) || pricePrefetchingRef.current.has(cacheKey)) return;
    pricePrefetchingRef.current.add(cacheKey);
    if (nextKind === 'event' && !eventMembersPrefetchRef.current.has(nextSlug)) {
      eventMembersPrefetchRef.current.add(nextSlug);
      void fetchQuantEventMembers(nextSlug, 200).catch(() => undefined);
    }
    void fetchQuantEntitySnapshot({
      entityType: nextKind,
      marketSlug: nextKind === 'market' ? nextSlug : undefined,
      eventSlug: nextKind === 'event' ? nextSlug : undefined,
      priceSource: backendPriceSource(priceSource),
      maxOutcomes: nextKind === 'event' ? EVENT_TILE_OUTCOME_LIMIT : 24,
      topN: nextKind === 'event' ? EVENT_TILE_OUTCOME_LIMIT : undefined,
      pointFormat: 'lite',
      timeoutMs: 3500,
    })
      .then((payload) => {
        priceSeriesCacheRef.current.set(cacheKey, payload);
        if (import.meta.env.DEV) console.debug('[quant] prefetched price series', { cacheKey });
      })
      .catch((prefetchError) => {
        if (import.meta.env.DEV && !isAbortLikeError(prefetchError)) console.debug('[quant] prefetch failed', { cacheKey, prefetchError });
      })
      .finally(() => {
        pricePrefetchingRef.current.delete(cacheKey);
      });
    const prefetchSeries = nextKind === 'event'
      ? fetchQuantEventPriceSeries({
        eventSlug: nextSlug,
        priceSource: backendPriceSource(priceSource),
        ...eventTileRequestShape('latest', timeframe, chartLimit),
        range: 'latest',
        resolution: 'auto',
        pointFormat: 'lite',
        viewportWidth: chartViewportWidth(),
        timeoutMs: 6000,
      })
      : fetchQuantPriceWindow({
        entityType: nextKind,
        marketSlug: nextSlug,
        priceSource: backendPriceSource(priceSource),
        limit: Math.max(2500, EVENT_TILE_MAX_POINTS * 8),
        maxOutcomes: 24,
        maxPoints: EVENT_TILE_MAX_POINTS,
        range: 'latest',
        resolution: 'auto',
        pointFormat: 'lite',
        viewportWidth: chartViewportWidth(),
        timeoutMs: 6000,
      });
    void prefetchSeries.catch(() => undefined);
  };

  useEffect(() => {
    const text = marketSearchQuery.trim();
    const cacheKey = text.toLowerCase();
    const cached = marketSearchCacheRef.current.get(cacheKey);
    const seq = marketSearchSeq.current + 1;
    marketSearchSeq.current = seq;
    if (cached) {
      setQuantMarkets(cached);
      setMarketSearchStatus(cached.length ? 'ready' : 'empty');
    } else {
      setMarketSearchStatus('loading');
    }
    const timer = window.setTimeout(() => {
      let events: QuantPriceMarket[] | null = null;
      let markets: QuantPriceMarket[] | null = null;
      let failures = 0;
      const publish = () => {
        if (seq !== marketSearchSeq.current) return;
        const eventItems = events || [];
        const marketItems = markets || [];
        const bothSearchesComplete = events !== null && markets !== null;
        const seen = new Set<string>();
        const items = [...eventItems, ...marketItems].filter((item) => {
          const key = `${item.itemKind || 'market'}:${item.marketSlug}`;
          if (seen.has(key)) return false;
          seen.add(key);
          if (item.itemKind !== 'event' && eventItems.some((event) => event.marketSlug === item.marketSlug)) return false;
          return true;
        });
        if (items.length) {
          setQuantMarkets(items);
          setMarketSearchStatus(bothSearchesComplete ? 'ready' : 'partial');
          marketSearchCacheRef.current.set(cacheKey, items);
          if (
            bothSearchesComplete
            && !autoSelectedDefaultRef.current
            && !marketSlugRef.current.trim()
            && !text
            && items[0]?.marketSlug
          ) {
            autoSelectedDefaultRef.current = true;
            setMarketSlug(items[0].marketSlug);
            setSelectedMarketMeta(items[0]);
            setSelectedEntityKindHint(items[0].itemKind === 'event' ? 'event' : 'market');
          }
          return;
        }
        if (events !== null && markets !== null) {
          setQuantMarkets([]);
          setMarketSearchStatus(failures >= 2 ? 'error' : 'empty');
        }
      };
      void fetchQuantPriceMarkets(text, text ? 120 : 80)
        .then((payload) => {
          markets = payload.items || [];
          publish();
        })
        .catch((searchError) => {
          markets = [];
          failures += 1;
          publish();
          if (!isAbortLikeError(searchError)) console.warn('quant market search failed', searchError);
        });
      void fetchQuantPriceEvents(text, text ? 48 : 32)
        .then((payload) => {
          events = payload.items || [];
          publish();
        })
        .catch((searchError) => {
          events = [];
          failures += 1;
          publish();
          if (!isAbortLikeError(searchError)) console.warn('quant event search failed', searchError);
        });
    }, text ? 180 : 0);
    return () => window.clearTimeout(timer);
  }, [marketSearchQuery]);

  useEffect(() => {
    if (!quantMarkets.length) return undefined;
    const timer = window.setTimeout(() => {
      quantMarkets.slice(0, 6).forEach((market) => {
        if (market.marketSlug && market.marketSlug !== marketSlugRef.current) prefetchMarketSlug(market.marketSlug);
      });
    }, 240);
    return () => window.clearTimeout(timer);
  }, [quantMarkets, priceSource, timeframe]);

  useEffect(() => {
    if (!marketSlug.trim()) return;
    const seq = priceLoadSeq.current + 1;
    priceLoadSeq.current = seq;
    const timer = window.setTimeout(() => {
      void refreshQuantRows(seq).catch((loadError) => {
        if (seq !== priceLoadSeq.current) return;
        setError(loadError instanceof Error ? loadError.message : 'Quant API unavailable');
      });
    }, 60);
    return () => window.clearTimeout(timer);
  }, [marketReloadKey, marketSlug, priceSource, timeframe, selectedEntityKind]);

  useEffect(() => {
    return () => {
      if (priceRetryTimerRef.current) window.clearTimeout(priceRetryTimerRef.current);
      if (viewportFetchTimerRef.current) window.clearTimeout(viewportFetchTimerRef.current);
    };
  }, []);

  useEffect(() => {
    if (!livePriceRefreshEnabled) return undefined;
    const intervalMs = priceSource === 'frontend' ? 30000 : 8000;
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible' || liveRefreshInFlightRef.current) return;
      const seq = priceLoadSeq.current + 1;
      priceLoadSeq.current = seq;
      liveRefreshInFlightRef.current = true;
      void refreshQuantRows(seq, { silent: true })
        .catch((loadError) => {
          if (import.meta.env.DEV && !isAbortLikeError(loadError)) console.debug('[quant] live price refresh failed', loadError);
        })
        .finally(() => {
          liveRefreshInFlightRef.current = false;
        });
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [livePriceRefreshEnabled, marketSlug, priceSource, timeframe, selectedEntityKind]);

  useEffect(() => {
    if (!livePriceRefreshEnabled || !marketSlug.trim() || typeof EventSource === 'undefined') return undefined;
    const stream = new EventSource(quantPriceStreamUrl({
      entityType: selectedEntityKind,
      eventSlug: selectedEntityKind === 'event' ? marketSlug : undefined,
      marketSlug: selectedEntityKind === 'market' ? marketSlug : undefined,
      priceSource: backendPriceSource(priceSource),
      maxOutcomes: selectedEntityKind === 'event' ? EVENT_TILE_OUTCOME_LIMIT : 24,
      interval: priceSource === 'frontend' ? 10 : 2,
    }));
    const onPrice = (event: MessageEvent) => {
      try {
        const update = JSON.parse(String(event.data || '{}')) as EventPriceStreamPayload;
        setMarketSeries((current) => {
          const merged = mergeEventPriceStream(current, update);
          if (merged) priceSeriesCacheRef.current.set(priceRequestKey, merged);
          return merged;
        });
        setLastPriceRefreshAt(new Date().toLocaleTimeString());
      } catch (streamError) {
        if (import.meta.env.DEV) console.debug('[quant] event price stream parse failed', streamError);
      }
    };
    stream.addEventListener('price', onPrice as EventListener);
    stream.onerror = () => {
      if (import.meta.env.DEV) console.debug('[quant] event price stream disconnected');
    };
    return () => {
      stream.removeEventListener('price', onPrice as EventListener);
      stream.close();
    };
  }, [livePriceRefreshEnabled, marketSlug, priceRequestKey, priceSource, selectedEntityKind]);

  useEffect(() => {
    const outcomes = marketSeries?.outcomes || [];
    if (!outcomes.length) {
      setSelectedOutcomeTokenId('');
      return;
    }
    if (!outcomes.some((outcome) => outcome.tokenId === selectedOutcomeTokenId)) {
      setSelectedOutcomeTokenId(defaultOutcomeTokenId(outcomes));
    }
  }, [marketSeries, selectedOutcomeTokenId]);

  const filteredPerformanceRows = useMemo(() => {
    const queryText = performanceSearch.trim().toLowerCase();
    const filtered = backtestResult.performanceRows.filter((row) => (
      !queryText || row.metric.toLowerCase().includes(queryText) || row.description.toLowerCase().includes(queryText)
    ));
    return filtered.slice().sort((a, b) => {
      const left = rowSortValue(a[performanceSortKey]);
      const right = rowSortValue(b[performanceSortKey]);
      const order = left > right ? 1 : left < right ? -1 : 0;
      return performanceSortDirection === 'asc' ? order : -order;
    });
  }, [backtestResult.performanceRows, performanceSearch, performanceSortDirection, performanceSortKey]);

  const filteredTrades = useMemo(() => {
    return backtestResult.trades.filter((trade) => {
      if (tradeFilters.has('profitable') && trade.pnl <= 0) return false;
      if (tradeFilters.has('losing') && trade.pnl >= 0) return false;
      if (tradeFilters.has('yes') && trade.outcome !== 'YES') return false;
      if (tradeFilters.has('no') && trade.outcome !== 'NO') return false;
      if (tradeFilters.has('longHolding') && trade.holdingBars < 12) return false;
      if (tradeFilters.has('shortHolding') && trade.holdingBars >= 12) return false;
      return true;
    });
  }, [backtestResult.trades, tradeFilters]);

  const watchKeyForOutcome = (outcome: QuantMarketSeriesOutcome) => (
    outcome.buyYesTokenId || outcome.tokenId || outcome.marketSlug || outcome.conditionId || ''
  );

  const sortedOutcomeRows = useMemo(() => {
    const eventTitle = marketSeries?.event?.eventTitle || marketSeries?.market?.marketTitle || '';
    const baseRows = (marketSeries?.outcomes || []).map((outcome, index) => {
      const label = deriveEventOutcomeLabel(eventTitle, outcome.marketTitle, outcome.outcomeLabel);
      const yesStats = outcomePointStats(outcome.points);
      const noStats = outcomePointStats(outcome.complementPoints);
      const firstBlocks = [yesStats.firstBlock, noStats.firstBlock].filter((value) => value > 0);
      const rows = yesStats.rows + noStats.rows;
      const gaps = yesStats.gaps + noStats.gaps;
      const spikes = yesStats.spikes + noStats.spikes;
      const directNoRows = noStats.rows - noStats.impliedRows;
      const coverageRatio = Math.min(1, Math.max(statsCoverageRatio(yesStats), statsCoverageRatio(noStats)));
      return {
        outcome,
        index,
        label,
        fullLabel: outcome.marketTitle || outcome.outcomeLabel,
        yes: toNumber(outcome.buyYesPrice ?? outcome.latestPrice),
        no: toNumber(outcome.buyNoPrice ?? outcome.complementLatestPrice),
        yesRows: yesStats.rows,
        noRows: noStats.rows,
        medianDelta: Math.max(yesStats.medianDelta, noStats.medianDelta),
        rows,
        volume: [...(outcome.points || []), ...(outcome.complementPoints || [])].reduce((sum, point) => sum + toNumber(point.volume), 0),
        firstBlock: firstBlocks.length ? Math.min(...firstBlocks) : 0,
        lastBlock: Math.max(yesStats.lastBlock, noStats.lastBlock),
        gaps,
        spikes,
        directNoRows,
        impliedNoRows: noStats.impliedRows,
        coverageRatio,
      };
    });
    const latestBlock = baseRows.reduce((max, row) => Math.max(max, row.lastBlock), 0);
    return baseRows.map((row) => {
      const reasons: string[] = [];
      if (!row.rows) reasons.push('no rows');
      if (row.gaps) reasons.push(`${row.gaps} gaps`);
      if (row.spikes) reasons.push(`${row.spikes} jumps`);
      if (row.noRows && row.impliedNoRows / row.noRows > 0.8) reasons.push('NO mostly implied');
      if (row.coverageRatio > 0 && row.coverageRatio < 0.72) reasons.push('sparse coverage');
      if (latestBlock && row.lastBlock && row.medianDelta && row.lastBlock < latestBlock - row.medianDelta * 8) reasons.push('stale');
      const qualityStatus = !row.rows ? 'empty' : reasons.length ? 'review' : 'ready';
      return {
        ...row,
        qualityStatus,
        qualityReason: reasons.slice(0, 2).join(' · ') || 'continuous block-close coverage',
        yesKey: chartOutcomeKey(row.outcome, row.label, 'YES'),
        noKey: chartOutcomeKey(row.outcome, row.label, 'NO'),
      };
    }).sort((left, right) => {
      if (outcomeSortKey === 'order') return left.index - right.index;
      if (outcomeSortKey === 'rows') return right.rows - left.rows;
      if (outcomeSortKey === 'volume') return right.volume - left.volume;
      return right.yes - left.yes;
    });
  }, [marketSeries, outcomeSortKey]);
  const filteredOutcomeRows = useMemo(() => {
    const query = outcomeSearch.trim().toLowerCase();
    const tokens = query.split(/\s+/).filter(Boolean);
    return sortedOutcomeRows.filter((row) => {
      const activeKey = selectedBacktestAction === 'NO' ? row.noKey : row.yesKey;
      const isPinned = chartPinnedOutcomeKeys.includes(row.yesKey) || chartPinnedOutcomeKeys.includes(row.noKey);
      const isHidden = chartHiddenOutcomeKeys.includes(row.yesKey) || chartHiddenOutcomeKeys.includes(row.noKey);
      const isSolo = chartSoloOutcomeKey === row.yesKey || chartSoloOutcomeKey === row.noKey;
      const watchKey = watchKeyForOutcome(row.outcome);
      const isWatched = watchKey ? watchlistKeys.includes(watchKey) : false;
      const visible = chartSoloOutcomeKey ? isSolo : !chartHiddenOutcomeKeys.includes(activeKey);
      if (outcomeVisibilityFilter === 'visible' && !visible) return false;
      if (outcomeVisibilityFilter === 'pinned' && !isPinned) return false;
      if (outcomeVisibilityFilter === 'hidden' && !isHidden) return false;
      if (outcomeVisibilityFilter === 'watched' && !isWatched) return false;
      if (outcomeVisibilityFilter === 'issues' && row.qualityStatus === 'ready') return false;
      if (!tokens.length) return true;
      const haystack = [
        row.label,
        row.fullLabel,
        row.outcome.marketSlug,
        row.outcome.outcomeLabel,
        row.outcome.tokenId,
        row.outcome.buyYesTokenId,
        row.outcome.buyNoTokenId,
      ].join(' ').toLowerCase();
      return tokens.every((token) => haystack.includes(token));
    });
  }, [
    chartHiddenOutcomeKeys,
    chartPinnedOutcomeKeys,
    chartSoloOutcomeKey,
    outcomeSearch,
    outcomeVisibilityFilter,
    selectedBacktestAction,
    sortedOutcomeRows,
    watchlistKeys,
  ]);
  const displayedOutcomeCount = useMemo(() => {
    const outcomes = marketSeries?.outcomes || [];
    if (selectedEntityKind === 'event' && outcomes.length === 1 && outcomes[0]?.buyNoTokenId) return 2;
    return outcomes.length;
  }, [marketSeries?.outcomes, selectedEntityKind]);
  const selectedOutcomeRow = useMemo(
    () => sortedOutcomeRows.find(({ outcome }) => outcome.tokenId === selectedOutcome?.tokenId) || sortedOutcomeRows[0] || null,
    [selectedOutcome, sortedOutcomeRows],
  );
  const hoveredOutcomeRow = useMemo(
    () => sortedOutcomeRows.find((row) => row.yesKey === hoveredChartOutcomeKey || row.noKey === hoveredChartOutcomeKey) || null,
    [hoveredChartOutcomeKey, sortedOutcomeRows],
  );
  const outcomeVisibilitySummary = useMemo(() => {
    const activeKeys = sortedOutcomeRows
      .map((row) => (selectedBacktestAction === 'NO' ? row.noKey : row.yesKey))
      .filter(Boolean);
    const visibleCount = chartSoloOutcomeKey
      ? activeKeys.filter((key) => key === chartSoloOutcomeKey).length
      : activeKeys.filter((key) => !chartHiddenOutcomeKeys.includes(key)).length;
    const soloRow = chartSoloOutcomeKey
      ? sortedOutcomeRows.find((row) => row.yesKey === chartSoloOutcomeKey || row.noKey === chartSoloOutcomeKey)
      : null;
    return {
      activeTotal: activeKeys.length,
      visibleCount,
      pinnedCount: activeKeys.filter((key) => chartPinnedOutcomeKeys.includes(key)).length,
      hiddenCount: activeKeys.filter((key) => chartHiddenOutcomeKeys.includes(key)).length,
      soloLabel: soloRow?.label || '',
    };
  }, [chartHiddenOutcomeKeys, chartPinnedOutcomeKeys, chartSoloOutcomeKey, selectedBacktestAction, sortedOutcomeRows]);
  const managedOutcomeLineRows = useMemo(() => {
    const selectedToken = selectedOutcome?.tokenId || '';
    const rows = sortedOutcomeRows.flatMap((row) => ([
      {
        key: row.yesKey,
        side: 'YES' as BacktestAction,
        row,
        price: row.yes,
        rows: row.yesRows,
      },
      {
        key: row.noKey,
        side: 'NO' as BacktestAction,
        row,
        price: row.no,
        rows: row.noRows,
      },
    ])).filter((line) => line.key).map((line) => {
      const watchKey = watchKeyForOutcome(line.row.outcome);
      const pinned = chartPinnedOutcomeKeys.includes(line.key);
      const hidden = chartHiddenOutcomeKeys.includes(line.key);
      const solo = chartSoloOutcomeKey === line.key;
      const selected = line.row.outcome.tokenId === selectedToken && selectedBacktestAction === line.side;
      const watched = watchKey ? watchlistKeys.includes(watchKey) : false;
      const score = (solo ? 64 : 0) + (selected ? 32 : 0) + (pinned ? 16 : 0) + (hidden ? 8 : 0) + (watched ? 4 : 0);
      return {
        ...line,
        pinned,
        hidden,
        selected,
        solo,
        watched,
        score,
      };
    }).filter((line) => line.score > 0);
    return rows.sort((left, right) => right.score - left.score || right.price - left.price).slice(0, 12);
  }, [
    chartHiddenOutcomeKeys,
    chartPinnedOutcomeKeys,
    chartSoloOutcomeKey,
    selectedBacktestAction,
    selectedOutcome?.tokenId,
    sortedOutcomeRows,
    watchlistKeys,
  ]);
  const selectedBookQuality = useMemo(() => {
    const yesPoints = selectedOutcome?.points || [];
    const noPoints = selectedOutcome?.complementPoints || [];
    const yes = outcomePointStats(yesPoints);
    const no = outcomePointStats(noPoints);
    const directNoRows = noPoints.filter((point) => !point.isImplied).length;
    const impliedNoRows = noPoints.filter((point) => point.isImplied).length;
    const lastBlock = Math.max(yes.lastBlock, no.lastBlock);
    const firstBlocks = [yes.firstBlock, no.firstBlock].filter((value) => value > 0);
    return {
      yes,
      no,
      firstBlock: firstBlocks.length ? Math.min(...firstBlocks) : 0,
      lastBlock,
      directNoRows,
      impliedNoRows,
      totalRows: yes.rows + no.rows,
      gaps: yes.gaps + no.gaps,
      spikes: yes.spikes + no.spikes,
      status: !yes.rows && !no.rows ? 'empty' : yes.gaps + no.gaps || yes.spikes + no.spikes ? 'review' : 'ready',
    };
  }, [selectedOutcome]);
  const selectedBookYesTokenId = selectedOutcome?.buyYesTokenId || selectedOutcome?.tokenId || '';
  const selectedBookNoTokenId = selectedOutcome?.buyNoTokenId || '';
  const selectedBookTitle = selectedOutcomeRow?.fullLabel || selectedOutcome?.outcomeLabel || marketInfo.title || '';
  const selectedLiveBookTokenId = selectedBacktestAction === 'NO'
    ? (selectedBookNoTokenId || selectedBookYesTokenId)
    : selectedBookYesTokenId;
  const liveBookSide = selectedBacktestAction === 'NO' ? liveLob?.no : liveLob?.yes;
  const liveBookHasLevels = bookSideHasLevels(liveLob?.yes) || bookSideHasLevels(liveLob?.no);
  const liveSelectedBookHasLevels = bookSideHasLevels(liveBookSide);
  const liveBookBidDepth = useMemo(() => sumBookNotional(liveBookSide?.bids, 12), [liveBookSide?.bids]);
  const liveBookAskDepth = useMemo(() => sumBookNotional(liveBookSide?.asks, 12), [liveBookSide?.asks]);
  const liveBookDepthTotal = liveBookBidDepth + liveBookAskDepth;
  const liveBookBidImbalance = liveBookDepthTotal > 0 ? liveBookBidDepth / liveBookDepthTotal : 0.5;
  const liveBookMid = bookMidpoint(liveBookSide);
  const liveBookLadderMax = Math.max(liveBookBidDepth, liveBookAskDepth, 1);
  const liveBookBidRows = useMemo(() => orderBookLadderRows(liveBookSide?.bids, 'bid', liveBookLadderMax), [liveBookLadderMax, liveBookSide?.bids]);
  const liveBookAskRows = useMemo(() => orderBookLadderRows(liveBookSide?.asks, 'ask', liveBookLadderMax), [liveBookLadderMax, liveBookSide?.asks]);
  const selectedBookFillEstimate = useMemo(() => {
    const reference = finiteNumber(selectedBacktestAction === 'NO' ? selectedOutcomeRow?.no : selectedOutcomeRow?.yes)
      ?? finiteNumber(liveBookSide?.bestAsk)
      ?? finiteNumber(liveBookSide?.bestBid);
    return estimateMarketBuyFill(liveBookSide?.asks, strategyParameters.positionSize, reference);
  }, [liveBookSide?.asks, liveBookSide?.bestAsk, liveBookSide?.bestBid, selectedBacktestAction, selectedOutcomeRow?.no, selectedOutcomeRow?.yes, strategyParameters.positionSize]);
  const bookExecutionQuality = useMemo(() => {
    const bid = finiteNumber(liveBookSide?.bestBid);
    const ask = finiteNumber(liveBookSide?.bestAsk);
    const spread = finiteNumber(liveBookSide?.spread) ?? (bid !== null && ask !== null ? Math.max(0, ask - bid) : null);
    const mid = bid !== null && ask !== null ? (bid + ask) / 2 : null;
    const blockClose = finiteNumber(selectedBacktestAction === 'NO' ? selectedOutcomeRow?.no : selectedOutcomeRow?.yes);
    const bidDepth = sumBookDepth(liveBookSide?.bids, 5);
    const askDepth = sumBookDepth(liveBookSide?.asks, 5);
    const topDepth = bidDepth + askDepth;
    const drift = mid !== null && blockClose !== null ? Math.abs(mid - blockClose) : null;
    const fetchedAtMs = liveLob?.fetchedAt ? Date.parse(liveLob.fetchedAt) : NaN;
    const ageSeconds = Number.isFinite(fetchedAtMs) ? Math.max(0, Math.round((Date.now() - fetchedAtMs) / 1000)) : null;
    const caveats: string[] = [];

    if (liveLobStatus === 'loading') caveats.push('Live CLOB is still loading.');
    if (liveLobStatus === 'error') caveats.push(liveLobError || 'Live CLOB request failed.');
    if (!liveSelectedBookHasLevels) caveats.push(`No live ${selectedBacktestAction} bid/ask levels are available for this token.`);
    if (spread !== null && spread > 0.08) caveats.push('Spread is wide; fills may differ from block-close rows.');
    if (drift !== null && drift > 0.08) caveats.push('Live midpoint is far from the selected block-close price.');
    if (topDepth > 0 && topDepth < 100) caveats.push('Top-of-book depth is thin for production-sized backtests.');
    if (selectedBookFillEstimate.target > 0 && selectedBookFillEstimate.fillPct < 0.9) caveats.push('Current position size cannot be filled from visible ask depth.');
    if (selectedBookFillEstimate.slippage !== null && selectedBookFillEstimate.slippage > 0.05) caveats.push('Estimated VWAP is materially above the selected block-close price.');
    if (selectedBookQuality.status === 'review') caveats.push('Historical block-close series has gaps or jumps.');
    if (ageSeconds !== null && ageSeconds > 60) caveats.push('Live book snapshot is stale.');

    const status = liveLobStatus === 'loading'
      ? 'loading'
      : !liveSelectedBookHasLevels || liveLobStatus === 'empty'
        ? 'empty'
        : liveLobStatus === 'error' || caveats.some((item) => /wide|far|thin|gaps|jumps|stale|failed/i.test(item))
          ? 'review'
          : 'ready';
    const confidence = status === 'ready'
      ? 92
      : status === 'review'
        ? Math.max(45, 82 - caveats.length * 8)
        : status === 'loading'
          ? 50
          : 24;
    const title = status === 'ready'
      ? 'Execution assumptions look usable'
      : status === 'review'
        ? 'Review before trusting simulated fills'
        : status === 'loading'
          ? 'Checking live CLOB execution context'
          : 'No executable live book context';
    const nextAction = status === 'ready'
      ? 'Run a liquidity-aware backtest or compare target size against top depth.'
      : status === 'review'
        ? 'Refresh CLOB, lower assumed size, or inspect Data Quality before running.'
        : status === 'loading'
          ? 'Wait for the book request to finish.'
          : 'Use block-close results as historical signal only until CLOB depth returns.';

    return {
      ageSeconds,
      ask,
      askDepth,
      bid,
      bidDepth,
      blockClose,
      caveats,
      confidence,
      drift,
      mid,
      nextAction,
      spread,
      status,
      title,
      topDepth,
    };
  }, [
    liveBookSide,
    liveSelectedBookHasLevels,
    liveLob?.fetchedAt,
    liveLobError,
    liveLobStatus,
    selectedBacktestAction,
    selectedBookFillEstimate.fillPct,
    selectedBookFillEstimate.slippage,
    selectedBookFillEstimate.target,
    selectedBookQuality.status,
    selectedOutcomeRow?.no,
    selectedOutcomeRow?.yes,
  ]);

  useEffect(() => {
    if (inspectorTab !== 'book' && inspectorTab !== 'dataQuality') return undefined;
    if (!selectedBookYesTokenId) {
      setLiveLob(null);
      setLiveLobStatus('empty');
      setLiveLobError('Missing selected YES token id');
      setLobSnapshots([]);
      setLobSnapshotStatus('empty');
      setLobSnapshotError('');
      return undefined;
    }
    let cancelled = false;
    setLiveLobStatus('loading');
    setLiveLobError('');
    setLobSnapshotStatus('loading');
    setLobSnapshotError('');
    void (async () => {
      try {
        const payload = await fetchMarketLobByToken(selectedBookYesTokenId, selectedBookTitle, selectedBookNoTokenId, 3500);
        if (cancelled) return;
        setLiveLob(payload);
        setLiveLobStatus(bookSideHasLevels(payload.yes) || bookSideHasLevels(payload.no) ? 'ready' : 'empty');
      } catch (lobError) {
        if (cancelled) return;
        setLiveLob(null);
        setLiveLobStatus('error');
        setLiveLobError(lobError instanceof Error ? lobError.message : 'Live CLOB book unavailable');
      }
      try {
        const snapshotPayload = await fetchMarketLobSnapshotsByToken(selectedLiveBookTokenId || selectedBookYesTokenId, selectedBacktestAction, 48, 4500);
        if (cancelled) return;
        setLobSnapshots(snapshotPayload.items || []);
        setLobSnapshotStatus((snapshotPayload.items || []).length ? 'ready' : 'empty');
      } catch (snapshotError) {
        if (cancelled) return;
        setLobSnapshots([]);
        setLobSnapshotStatus('error');
        setLobSnapshotError(snapshotError instanceof Error ? snapshotError.message : 'CLOB snapshot history unavailable');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [inspectorTab, liveLobRefreshSeq, selectedBacktestAction, selectedBookNoTokenId, selectedBookTitle, selectedBookYesTokenId, selectedLiveBookTokenId]);

  const currentWatchKeySet = useMemo(() => new Set(
    sortedOutcomeRows.map(({ outcome }) => watchKeyForOutcome(outcome)).filter(Boolean),
  ), [sortedOutcomeRows]);
  const watchedOutcomeRows = useMemo(() => {
    const savedRows = watchlistKeys
      .map((key) => sortedOutcomeRows.find(({ outcome }) => outcome.tokenId === key || outcome.buyYesTokenId === key || outcome.buyNoTokenId === key))
      .filter((row): row is (typeof sortedOutcomeRows)[number] => Boolean(row));
    return savedRows;
  }, [sortedOutcomeRows, watchlistKeys]);
  const watchlistRows = useMemo(() => (
    watchedOutcomeRows.length ? watchedOutcomeRows : sortedOutcomeRows.slice(0, 12)
  ), [sortedOutcomeRows, watchedOutcomeRows]);
  const externalWatchlistCount = useMemo(
    () => watchlistKeys.filter((key) => !currentWatchKeySet.has(key)).length,
    [currentWatchKeySet, watchlistKeys],
  );
  const recentTradeRows = useMemo(() => filteredTrades.slice(-10).reverse(), [filteredTrades]);
  const selectedTradeRow = useMemo(() => (
    selectedTradeId ? backtestResult.trades.find((trade) => trade.id === selectedTradeId) || null : null
  ), [backtestResult.trades, selectedTradeId]);
  const inspectorTradeStats = useMemo(() => {
    const netPnl = filteredTrades.reduce((sum, trade) => sum + trade.pnl, 0);
    const winners = filteredTrades.filter((trade) => trade.pnl > 0).length;
    const selectedIndex = selectedTradeRow ? filteredTrades.findIndex((trade) => trade.id === selectedTradeRow.id) : -1;
    return {
      count: filteredTrades.length,
      netPnl,
      winners,
      winRate: filteredTrades.length ? winners / filteredTrades.length : 0,
      selectedIndex,
    };
  }, [filteredTrades, selectedTradeRow]);
  const selectedTradeBlockSpan = useMemo(() => {
    if (!selectedTradeRow?.entryX || !selectedTradeRow.exitX) return null;
    return Math.abs(Math.floor(selectedTradeRow.exitX) - Math.floor(selectedTradeRow.entryX));
  }, [selectedTradeRow]);
  const selectInspectorTradeOffset = (offset: number) => {
    if (!filteredTrades.length) return;
    const base = inspectorTradeStats.selectedIndex >= 0 ? inspectorTradeStats.selectedIndex : 0;
    const nextIndex = Math.min(filteredTrades.length - 1, Math.max(0, base + offset));
    const nextTrade = filteredTrades[nextIndex];
    if (!nextTrade) return;
    setSelectedTradeId(nextTrade.id);
    setTesterTab('trades');
    setStrategyDrawerCollapsed(false);
  };
  const priceBlockRange = useMemo(() => blockRangeLabel(activePrices), [activePrices]);
  const selectedWatchKey = selectedOutcome ? watchKeyForOutcome(selectedOutcome) : selectedOutcomeRow ? watchKeyForOutcome(selectedOutcomeRow.outcome) : '';
  const selectedIsWatched = selectedWatchKey ? watchlistKeys.includes(selectedWatchKey) : false;
  const dataQuality = useMemo(
    () => dataQualitySummary(activePrices, backendPriceSource(priceSource), marketSeries?.outcomes || [], dataStatus),
    [activePrices, dataStatus, marketSeries, priceSource],
  );
  const selectedTargetTrust = useMemo(() => {
    const activeStats = selectedBacktestAction === 'NO' ? selectedBookQuality.no : selectedBookQuality.yes;
    const activeRows = activeStats.rows;
    const sidePrice = finiteNumber(selectedBacktestAction === 'NO' ? selectedOutcomeRow?.no : selectedOutcomeRow?.yes);
    const firstBlock = activeStats.firstBlock || selectedBookQuality.firstBlock;
    const lastBlock = activeStats.lastBlock || selectedBookQuality.lastBlock;
    const staleBlocks = dataQuality.latestBlock && lastBlock ? Math.max(0, dataQuality.latestBlock - lastBlock) : 0;
    const staleLimit = Math.max(1000, (dataQuality.medianDelta || activeStats.medianDelta || 0) * 8);
    const stale = staleBlocks > staleLimit;
    const coverageRatio = statsCoverageRatio(activeStats);
    const directRows = selectedBacktestAction === 'NO'
      ? selectedBookQuality.directNoRows
      : Math.max(0, activeRows - activeStats.impliedRows);
    const impliedRows = selectedBacktestAction === 'NO' ? selectedBookQuality.impliedNoRows : activeStats.impliedRows;
    const directRatio = activeRows ? directRows / activeRows : 0;
    const caveats = [
      !selectedOutcomeRow ? 'No selected outcome row is available.' : '',
      !activeRows ? `No ${selectedBacktestAction} block-close rows for the selected target.` : '',
      activeStats.gaps ? `${activeStats.gaps.toLocaleString('en-US')} spacing gaps on the selected ${selectedBacktestAction} side.` : '',
      activeStats.spikes ? `${activeStats.spikes.toLocaleString('en-US')} large jumps on the selected ${selectedBacktestAction} side.` : '',
      stale ? `Selected target is ${staleBlocks.toLocaleString('en-US')} blocks behind the event latest block.` : '',
      selectedBacktestAction === 'NO' && activeRows && directRatio < 0.25 ? 'NO history is mostly implied from YES rather than direct NO closes.' : '',
      liveLobStatus === 'error' ? liveLobError || 'Live CLOB context failed to load.' : '',
      liveLobStatus === 'empty' ? 'No live CLOB levels are available for the selected target.' : '',
      bookExecutionQuality.status === 'review' ? 'Live execution context needs review before trusting fills.' : '',
    ].filter(Boolean).slice(0, 6);
    const status = !activeRows || !selectedOutcomeRow
      ? 'blocked'
      : stale || activeStats.gaps > 0 || activeStats.spikes > 0 || directRatio < 0.25 || bookExecutionQuality.status === 'review'
        ? 'review'
        : 'ready';
    const confidence = status === 'blocked'
      ? 0
      : Math.max(5, Math.min(99, Math.round(
        94
        - Math.min(24, activeStats.gaps * 5)
        - Math.min(22, activeStats.spikes * 4)
        - (stale ? Math.min(18, staleBlocks / Math.max(1, staleLimit) * 8) : 0)
        - (selectedBacktestAction === 'NO' ? Math.max(0, 1 - directRatio) * 16 : 0)
        - Math.max(0, 1 - coverageRatio) * 18
        - (bookExecutionQuality.status === 'review' ? 8 : 0)
      )));
    const title = status === 'ready'
      ? 'Selected target is backtest-ready'
      : status === 'review'
        ? 'Selected target needs review'
        : 'Selected target is blocked';
    const nextAction = status === 'ready'
      ? 'Run target backtest'
      : status === 'review'
        ? 'Inspect outcome, CLOB, and gaps first'
        : 'Choose a covered outcome';
    const evidence = [
      activeRows ? `${activeRows.toLocaleString('en-US')} ${selectedBacktestAction} rows` : `0 ${selectedBacktestAction} rows`,
      firstBlock && lastBlock ? `block ${Math.floor(firstBlock).toLocaleString('en-US')} to ${Math.floor(lastBlock).toLocaleString('en-US')}` : 'no block range',
      sidePrice !== null ? `latest ${selectedBacktestAction} ${fmtPrice(sidePrice)}` : '',
      `${formatCoverageRatio(coverageRatio)} coverage`,
      selectedBacktestAction === 'NO' ? `${directRows.toLocaleString('en-US')} direct / ${impliedRows.toLocaleString('en-US')} implied NO` : `${directRows.toLocaleString('en-US')} direct YES rows`,
      liveLobStatus === 'ready' ? `live CLOB ${bookExecutionQuality.status}` : `live CLOB ${liveLobStatus}`,
    ].filter(Boolean);
    return {
      activeRows,
      caveats,
      confidence,
      coverageRatio,
      directRows,
      directRatio,
      evidence,
      firstBlock,
      impliedRows,
      label: selectedOutcomeRow?.fullLabel || selectedOutcome?.outcomeLabel || 'No target selected',
      lastBlock,
      nextAction,
      price: sidePrice,
      staleBlocks,
      status,
      title,
    };
  }, [
    bookExecutionQuality.status,
    dataQuality.latestBlock,
    dataQuality.medianDelta,
    liveLobError,
    liveLobStatus,
    selectedBacktestAction,
    selectedBookQuality.directNoRows,
    selectedBookQuality.firstBlock,
    selectedBookQuality.impliedNoRows,
    selectedBookQuality.lastBlock,
    selectedBookQuality.no,
    selectedBookQuality.yes,
    selectedOutcome,
    selectedOutcomeRow,
  ]);
  const recentBuildRuns = useMemo(() => runs.slice(0, 6), [runs]);
  const buildRunSummary = useMemo(() => {
    const latest = recentBuildRuns[0];
    const errors = recentBuildRuns.reduce((sum, run) => sum + toNumber(run.errorCount), 0);
    const rowsWritten = recentBuildRuns.reduce((sum, run) => sum + toNumber(run.rowsWritten), 0);
    return {
      latest,
      errors,
      rowsWritten,
      health: !recentBuildRuns.length ? 'empty' : errors > 0 ? 'review' : 'ready',
    };
  }, [recentBuildRuns]);
  const outcomeQualityAllRows = useMemo(() => {
    const eventTitle = marketSeries?.event?.eventTitle || marketSeries?.market?.marketTitle || '';
    const latestGlobalBlock = dataQuality.latestBlock || 0;
    return (marketSeries?.outcomes || []).map((outcome) => {
      const label = deriveEventOutcomeLabel(eventTitle, outcome.marketTitle, outcome.outcomeLabel);
      const yes = outcomePointStats(outcome.points);
      const no = outcomePointStats(outcome.complementPoints);
      const lastBlock = Math.max(yes.lastBlock, no.lastBlock);
      const rows = yes.rows + no.rows;
      const gaps = yes.gaps + no.gaps;
      const spikes = yes.spikes + no.spikes;
      const staleBlocks = latestGlobalBlock && lastBlock ? Math.max(0, latestGlobalBlock - lastBlock) : 0;
      const stale = staleBlocks > Math.max(1000, (dataQuality.medianDelta || 0) * 8);
      const directNoRows = Math.max(0, no.rows - no.impliedRows);
      const impliedNoRatio = no.rows ? no.impliedRows / no.rows : 0;
      const coverageRatio = Math.min(1, Math.max(statsCoverageRatio(yes), statsCoverageRatio(no)));
      const reasons = [
        !rows ? 'no loaded rows' : '',
        stale ? `${staleBlocks.toLocaleString('en-US')} blocks behind latest` : '',
        gaps ? `${gaps.toLocaleString('en-US')} large gaps` : '',
        spikes ? `${spikes.toLocaleString('en-US')} price jumps` : '',
        no.rows && impliedNoRatio > 0.8 ? 'NO mostly implied' : '',
        coverageRatio > 0 && coverageRatio < 0.72 ? 'sparse coverage' : '',
      ].filter(Boolean);
      const status = !rows ? 'empty' : stale ? 'stale' : gaps || spikes ? 'review' : 'ready';
      const firstBlocks = [yes.firstBlock, no.firstBlock].filter((value) => value > 0);
      return {
        key: outcome.tokenId || outcome.marketSlug || label,
        label,
        yesRows: yes.rows,
        noRows: no.rows,
        rows,
        firstBlock: firstBlocks.length ? Math.min(...firstBlocks) : 0,
        lastBlock,
        gaps,
        spikes,
        staleBlocks,
        directNoRows,
        impliedNoRows: no.impliedRows,
        impliedNoRatio,
        coverageRatio,
        medianDelta: Math.max(yes.medianDelta, no.medianDelta),
        reason: reasons.slice(0, 3).join(' · ') || 'continuous direct block-close coverage',
        status,
      };
    }).sort((left, right) => {
      const severity = (row: { status: string; gaps: number; spikes: number; staleBlocks: number }) => (
        row.status === 'empty' ? 4 : row.status === 'stale' ? 3 : row.status === 'review' ? 2 : 1
      ) * 100000 + row.gaps * 1000 + row.spikes * 100 + Math.min(row.staleBlocks, 99);
      return severity(right) - severity(left) || right.rows - left.rows;
    });
  }, [dataQuality.latestBlock, dataQuality.medianDelta, marketSeries]);
  const outcomeQualityRows = useMemo(() => outcomeQualityAllRows.slice(0, 18), [outcomeQualityAllRows]);
  const outcomeQualitySummary = useMemo(() => {
    const counts = outcomeQualityAllRows.reduce((acc, row) => {
      acc[row.status as keyof typeof acc] = (acc[row.status as keyof typeof acc] || 0) + 1;
      return acc;
    }, { ready: 0, review: 0, stale: 0, empty: 0 });
    const total = outcomeQualityAllRows.length;
    const issueCount = counts.review + counts.stale + counts.empty;
    const worst = outcomeQualityAllRows[0] || null;
    return {
      ...counts,
      total,
      issueCount,
      readyPct: total ? (counts.ready / total) * 100 : 0,
      worst,
    };
  }, [outcomeQualityAllRows]);
  const dataTrustDecision = useMemo(() => {
    const issueCount = outcomeQualitySummary.issueCount;
    const total = Math.max(0, outcomeQualitySummary.total);
    const readyPct = total ? outcomeQualitySummary.ready / total : 0;
    const buildHasErrors = buildRunSummary.errors > 0;
    const noRows = dataQuality.rows <= 0;
    const status = noRows
      ? 'blocked'
      : buildHasErrors || outcomeQualitySummary.empty > 0 || outcomeQualitySummary.stale > 0 || outcomeQualitySummary.review > Math.max(2, total * 0.18) || dataQuality.gapCount > 0 || dataQuality.spikeCount > 0
        ? 'review'
        : 'ready';
    const title = status === 'ready'
      ? 'Backtest-ready data'
      : status === 'review'
        ? 'Usable with caveats'
        : 'Not enough data';
    const confidence = noRows
      ? 0
      : Math.max(0, Math.min(100, Math.round(
        100
        - (issueCount / Math.max(1, total)) * 42
        - Math.min(25, dataQuality.gapCount * 2.8)
        - Math.min(22, dataQuality.spikeCount * 2.4)
        - (buildHasErrors ? 12 : 0),
      )));
    const evidence = [
      noRows ? 'No loaded block-close rows for the selected source/window.' : `${dataQuality.rows.toLocaleString('en-US')} plotted rows loaded.`,
      total ? `${outcomeQualitySummary.ready.toLocaleString('en-US')} / ${total.toLocaleString('en-US')} outcomes are ready.` : 'No event outcomes loaded.',
      dataQuality.medianDelta ? `Median spacing is ${Math.floor(dataQuality.medianDelta).toLocaleString('en-US')} blocks.` : '',
      dataQuality.directNoRows ? `${dataQuality.directNoRows.toLocaleString('en-US')} direct NO rows available.` : dataQuality.impliedNoRows ? 'NO prices are currently implied from YES.' : '',
      buildRunSummary.latest?.runId ? `Latest build #${buildRunSummary.latest.runId} is ${buildRunSummary.latest.status}.` : 'No recent build status rows loaded.',
      lastPriceRefreshAt ? `Last live refresh ${lastPriceRefreshAt}.` : livePriceRefreshEnabled ? 'Live refresh enabled; waiting for next tick.' : 'Live refresh is not active for this selection.',
    ].filter(Boolean);
    const caveats = [
      ...dataQuality.warnings,
      outcomeQualitySummary.worst && outcomeQualitySummary.worst.status !== 'ready' ? `${outcomeQualitySummary.worst.label}: ${outcomeQualitySummary.worst.reason}` : '',
      buildHasErrors ? `${buildRunSummary.errors.toLocaleString('en-US')} build errors in recent runs.` : '',
      outcomeQualitySummary.stale ? `${outcomeQualitySummary.stale.toLocaleString('en-US')} stale outcomes.` : '',
      outcomeQualitySummary.empty ? `${outcomeQualitySummary.empty.toLocaleString('en-US')} outcomes have no rows.` : '',
    ].filter(Boolean).slice(0, 5);
    const nextAction = noRows
      ? 'Refresh market data or choose a covered source before running a backtest.'
      : status === 'review'
        ? 'Review affected outcomes, then run split/walk-forward before trusting aggregate metrics.'
        : 'Run or replay the strategy; data quality does not show major blockers.';
    return {
      status,
      title,
      confidence,
      readyPct,
      evidence,
      caveats,
      nextAction,
    };
  }, [buildRunSummary.errors, buildRunSummary.latest, dataQuality, lastPriceRefreshAt, livePriceRefreshEnabled, outcomeQualitySummary]);

  useEffect(() => {
    window.localStorage.setItem('polydata.quant.inspectorTab', inspectorTab);
    window.localStorage.setItem('polydata.quant.inspectorCollapsed', String(inspectorCollapsed));
    window.localStorage.setItem('polydata.quant.strategyDrawerCollapsed', String(strategyDrawerCollapsed));
  }, [inspectorCollapsed, inspectorTab, strategyDrawerCollapsed]);

  useEffect(() => {
    window.localStorage.setItem('polydata.quant.watchlistKeys', JSON.stringify(watchlistKeys));
  }, [watchlistKeys]);

  useEffect(() => {
    window.localStorage.setItem('polydata.quant.chart.pinnedOutcomes', JSON.stringify(chartPinnedOutcomeKeys));
    window.localStorage.setItem('polydata.quant.chart.hiddenOutcomes', JSON.stringify(chartHiddenOutcomeKeys));
  }, [chartHiddenOutcomeKeys, chartPinnedOutcomeKeys]);

  useEffect(() => {
    window.localStorage.setItem('polydata.quant.strategyParameters', JSON.stringify(strategyParameters));
  }, [strategyParameters]);

  const updateStrategyParameters = (next: StrategyParameters) => {
    setStrategyParameters(normalizeStrategyParameters(next));
  };

  const autoTuneStrategyParameters = () => {
    const selectedRows = outcomePricePoints(selectedOutcome, selectedBacktestAction);
    const sourceRows = selectedRows.length ? selectedRows : activePrices;
    const thresholds = strategyDefaults(sourceRows);
    setStrategyParameters((current) => normalizeStrategyParameters({
      ...current,
      ...thresholds,
      maxHoldingBars: Math.max(20, Math.min(240, Math.floor(chartLimit / 30))),
    }));
    setWorkspaceNotice(`strategy tuned from ${sourceRows.length.toLocaleString('en-US')} rows`);
    window.setTimeout(() => setWorkspaceNotice(''), 2400);
  };

  const toggleSelectedWatchlist = () => {
    if (!selectedWatchKey) return;
    setWatchlistKeys((current) => (
      current.includes(selectedWatchKey)
        ? current.filter((key) => key !== selectedWatchKey)
        : [selectedWatchKey, ...current].slice(0, 48)
    ));
  };

  const toggleOutcomeWatchlist = (outcome: QuantMarketSeriesOutcome) => {
    const key = watchKeyForOutcome(outcome);
    if (!key) return;
    setWatchlistKeys((current) => (
      current.includes(key)
        ? current.filter((item) => item !== key)
        : [key, ...current].slice(0, 48)
    ));
  };

  const clearCurrentWatchlist = () => {
    setWatchlistKeys((current) => current.filter((key) => !currentWatchKeySet.has(key)));
  };

  const pinWatchedOutcomes = () => {
    const watchedKeys = watchedOutcomeRows.map((row) => row.yesKey).filter(Boolean);
    if (!watchedKeys.length) return;
    setChartPinnedOutcomeKeys((current) => Array.from(new Set([...watchedKeys, ...current])).slice(0, 24));
    setChartHiddenOutcomeKeys((current) => current.filter((key) => !watchedKeys.includes(key)));
    setChartSoloOutcomeKey('');
  };

  const openWatchedOutcomes = () => {
    setInspectorTab('outcomes');
    setOutcomeVisibilityFilter('watched');
  };

  const selectOutcomeSide = (outcome: QuantMarketSeriesOutcome, side: BacktestAction) => {
    setSelectedOutcomeTokenId(outcome.tokenId);
    setSelectedBacktestAction(side);
  };

  const pinTopOutcomes = () => {
    const topKeys = sortedOutcomeRows.slice(0, 5).map((row) => row.yesKey).filter(Boolean);
    setChartPinnedOutcomeKeys((current) => Array.from(new Set([...topKeys, ...current])).slice(0, 24));
    setChartHiddenOutcomeKeys((current) => current.filter((key) => !topKeys.includes(key)));
    setChartSoloOutcomeKey('');
  };

  const toggleChartPinnedOutcome = (key: string) => {
    setChartPinnedOutcomeKeys((current) => (current.includes(key) ? current.filter((item) => item !== key) : [key, ...current].slice(0, 24)));
    setChartHiddenOutcomeKeys((current) => current.filter((item) => item !== key));
  };

  const toggleChartHiddenOutcome = (key: string) => {
    setChartHiddenOutcomeKeys((current) => (current.includes(key) ? current.filter((item) => item !== key) : [key, ...current].slice(0, 80)));
    setChartPinnedOutcomeKeys((current) => current.filter((item) => item !== key));
    if (chartSoloOutcomeKey === key) setChartSoloOutcomeKey('');
  };

  const resetChartOutcomeVisibility = () => {
    setChartPinnedOutcomeKeys([]);
    setChartHiddenOutcomeKeys([]);
    setChartSoloOutcomeKey('');
  };

  const filteredActiveOutcomeKeys = (limit = 80) => (
    filteredOutcomeRows
      .slice(0, limit)
      .map((row) => (selectedBacktestAction === 'NO' ? row.noKey : row.yesKey))
      .filter(Boolean)
  );

  const pinFilteredOutcomes = () => {
    const keys = filteredActiveOutcomeKeys(24);
    if (!keys.length) return;
    setChartPinnedOutcomeKeys((current) => Array.from(new Set([...keys, ...current])).slice(0, 24));
    setChartHiddenOutcomeKeys((current) => current.filter((key) => !keys.includes(key)));
    setChartSoloOutcomeKey('');
  };

  const hideFilteredOutcomes = () => {
    const keys = filteredActiveOutcomeKeys();
    if (!keys.length) return;
    setChartHiddenOutcomeKeys((current) => Array.from(new Set([...keys, ...current])).slice(0, 80));
    setChartPinnedOutcomeKeys((current) => current.filter((key) => !keys.includes(key)));
    if (keys.includes(chartSoloOutcomeKey)) setChartSoloOutcomeKey('');
  };

  const showFilteredOutcomes = () => {
    const keys = filteredActiveOutcomeKeys();
    if (!keys.length) return;
    setChartHiddenOutcomeKeys((current) => current.filter((key) => !keys.includes(key)));
  };

  const focusFilteredOutcomes = () => {
    const focusKeys = filteredActiveOutcomeKeys(24);
    if (!focusKeys.length) return;
    const focusSet = new Set(focusKeys);
    const activeKeys = sortedOutcomeRows
      .map((row) => (selectedBacktestAction === 'NO' ? row.noKey : row.yesKey))
      .filter(Boolean);
    setChartHiddenOutcomeKeys(Array.from(new Set(activeKeys.filter((key) => !focusSet.has(key)))).slice(0, 120));
    setChartPinnedOutcomeKeys(focusKeys.slice(0, 24));
    setChartSoloOutcomeKey('');
    setOutcomeVisibilityFilter('visible');
  };

  const unpinChartOutcomes = () => {
    setChartPinnedOutcomeKeys([]);
  };

  const soloFirstFilteredOutcome = () => {
    const key = filteredActiveOutcomeKeys(1)[0] || '';
    setChartSoloOutcomeKey((current) => (current === key ? '' : key));
  };

  const togglePerformanceSort = (key: PerformanceSortKey) => {
    if (performanceSortKey === key) {
      setPerformanceSortDirection((current) => (current === 'asc' ? 'desc' : 'asc'));
      return;
    }
    setPerformanceSortKey(key);
    setPerformanceSortDirection('asc');
  };

  const toggleTradeFilter = (filter: TradeFilter) => {
    setTradeFilters((current) => {
      const next = new Set(current);
      if (next.has(filter)) next.delete(filter);
      else next.add(filter);
      return next;
    });
  };

  const exportBacktest = (format: 'csv' | 'json') => {
    if (format === 'csv') {
      downloadText(`quant-backtest-${backtestResult.runId}.csv`, tradesToCsv(backtestResult.trades), 'text/csv;charset=utf-8');
      return;
    }
    downloadText(
      `quant-backtest-${backtestResult.runId}.json`,
      JSON.stringify({ market: marketInfo, priceSource, backtestEngine, timeframe, strategyParameters, result: backtestResult }, null, 2),
      'application/json;charset=utf-8',
    );
  };

  const saveWorkspace = () => {
    const payload = {
      marketSlug,
      marketTitle: marketInfo.title,
      marketSearchQuery,
      timeframe,
      priceSource,
      backtestEngine,
      strategyParameters,
      testerTab,
      deepBacktest,
      savedAt: new Date().toISOString(),
    };
    window.localStorage.setItem('polydata.quant.workspace', JSON.stringify(payload));
    setWorkspaceNotice(`workspace saved ${new Date().toLocaleTimeString()}`);
    window.setTimeout(() => setWorkspaceNotice(''), 2400);
  };

  return (
    <div className="qtv-shell">
      <WorkspaceHeader
        marketSlug={marketSlug}
        marketQuery={marketSearchQuery}
        timeframe={timeframe}
        viewportMode={viewportMode}
        priceSource={priceSource}
        backtestEngine={backtestEngine}
        loading={loading}
        marketOptions={quantMarkets}
        selectedMarket={selectedMarket}
        marketSearchStatus={marketSearchStatus}
        onMarketSlugChange={selectMarketSlug}
        onMarketQueryChange={setMarketSearchQuery}
        onTimeframeChange={changeTimeframePreset}
        onPriceSourceChange={changePriceSource}
        onBacktestEngineChange={setBacktestEngine}
        onRunBacktest={() => void runBacktest()}
        onSave={saveWorkspace}
        onExport={exportBacktest}
        onMarketPreview={prefetchMarketSlug}
      />

      {error ? <div className="qtv-error">{error}</div> : null}

      <main className={`qtv-workspace ${inspectorCollapsed ? 'inspector-collapsed' : ''} ${strategyDrawerCollapsed ? 'drawer-collapsed' : ''}`}>
        <section className="qtv-chart-region" aria-label="Quant chart workspace">
          <PriceChartPanel
            prices={activePrices}
            market={marketInfo}
            selectedTradeId={selectedTradeId}
            signals={strategySignals}
            priceSource={backendPriceSource(priceSource)}
            dataStatus={dataStatus}
            loadingMessage={loadingMessage}
            marketCoverageRows={marketCoverageRows}
            loadedPriceRows={displayedPriceRows}
            backtestRows={displayedPriceRows}
            eventMode={selectedEntityKind === 'event'}
            selectedTokenId={selectedBacktestAction === 'NO' ? selectedOutcome?.buyNoTokenId || '' : selectedOutcome?.buyYesTokenId || selectedOutcome?.tokenId || ''}
            selectedOutcomeLabel={selectedBacktestAction === 'NO' ? selectedOutcome?.buyNoLabel || '' : selectedOutcome?.buyYesLabel || selectedOutcome?.outcomeLabel || ''}
            pinnedOutcomeKeys={chartPinnedOutcomeKeys}
            hiddenOutcomeKeys={chartHiddenOutcomeKeys}
            soloOutcomeKey={chartSoloOutcomeKey}
            onPinnedOutcomeKeysChange={setChartPinnedOutcomeKeys}
            onHiddenOutcomeKeysChange={setChartHiddenOutcomeKeys}
            onSoloOutcomeKeyChange={setChartSoloOutcomeKey}
            onOutcomeHover={setHoveredChartOutcomeKey}
            onOutcomeSelect={(tokenId, side) => {
              const next = marketSeries?.outcomes?.find((outcome) => (
                side === 'NO' ? outcome.buyNoTokenId === tokenId : (outcome.buyYesTokenId === tokenId || outcome.tokenId === tokenId)
              ));
              if (next) {
                setSelectedOutcomeTokenId(next.tokenId);
                setSelectedBacktestAction(side);
              }
            }}
            onRetry={() => {
              setMarketReloadKey((current) => current + 1);
            }}
            viewportResetKey={`${marketSlug}|${priceSource}|${timeframe}|${viewportResetSeq}`}
            onViewportModeChange={updateViewportMode}
            onVisibleWindowChange={requestViewportWindow}
          />
        </section>

        <aside className="qtv-inspector" aria-label="Market inspector">
          <header className="qtv-inspector-head">
            <strong>Inspector</strong>
            <button type="button" onClick={() => setInspectorCollapsed((current) => !current)}>{inspectorCollapsed ? 'Open' : 'Hide'}</button>
          </header>
          {!inspectorCollapsed ? (
            <>
              <nav className="qtv-inspector-tabs" aria-label="Inspector tabs">
                {[
                  ['watchlist', 'Watchlist'],
                  ['market', 'Market'],
                  ['outcomes', 'Outcomes'],
                  ['book', 'Book'],
                  ['trades', 'Trades'],
                  ['dataQuality', 'Data'],
                ].map(([id, label]) => (
                  <button key={id} className={inspectorTab === id ? 'active' : ''} type="button" onClick={() => setInspectorTab(id as InspectorTab)}>{label}</button>
                ))}
              </nav>
              <div className="qtv-inspector-body">
                {inspectorTab === 'watchlist' ? (
                  <div className="qtv-watchlist">
                    <div className="qtv-watchlist-head">
                      <span>
                        {watchedOutcomeRows.length ? `${watchedOutcomeRows.length} watched here` : 'Top outcomes'}
                        {externalWatchlistCount ? <em>{externalWatchlistCount} saved elsewhere</em> : null}
                      </span>
                      <button type="button" disabled={!selectedWatchKey} onClick={toggleSelectedWatchlist}>
                        {selectedIsWatched ? 'Remove selected' : 'Add selected'}
                      </button>
                    </div>
                    <div className="qtv-watchlist-toolbar">
                      <button type="button" disabled={!watchedOutcomeRows.length} onClick={pinWatchedOutcomes}>Pin watched</button>
                      <button type="button" disabled={!watchedOutcomeRows.length} onClick={openWatchedOutcomes}>Open filter</button>
                      <button type="button" disabled={!watchedOutcomeRows.length} onClick={clearCurrentWatchlist}>Clear here</button>
                    </div>
                    {watchlistRows.map(({ outcome, label, fullLabel, yes, no, rows, yesKey }) => {
                      const isSelected = outcome.tokenId === selectedOutcome?.tokenId;
                      const watchKey = watchKeyForOutcome(outcome);
                      const isSaved = watchKey ? watchlistKeys.includes(watchKey) : false;
                      const isPinned = chartPinnedOutcomeKeys.includes(yesKey);
                      const isSolo = chartSoloOutcomeKey === yesKey;
                      return (
                        <div
                          key={`watch-${outcome.tokenId}`}
                          className={`qtv-watchlist-row ${isSelected ? 'active' : ''} ${isSaved ? 'saved' : ''} ${isPinned ? 'pinned' : ''} ${isSolo ? 'solo' : ''}`}
                          title={fullLabel}
                        >
                          <button
                            className="qtv-watchlist-main"
                            type="button"
                            onClick={() => selectOutcomeSide(outcome, 'YES')}
                          >
                            <span><strong>{label}</strong><em>{rows.toLocaleString('en-US')} rows</em></span>
                            <b>{fmtPrice(yes)}</b>
                            <i>{fmtPrice(no)}</i>
                          </button>
                          <div className="qtv-watchlist-actions">
                            <button type="button" onClick={() => toggleOutcomeWatchlist(outcome)}>{isSaved ? 'Remove' : 'Save'}</button>
                            <button className={isPinned ? 'active' : ''} type="button" onClick={() => toggleChartPinnedOutcome(yesKey)}>{isPinned ? 'Pinned' : 'Pin'}</button>
                            <button className={isSolo ? 'active' : ''} type="button" onClick={() => setChartSoloOutcomeKey(isSolo ? '' : yesKey)}>{isSolo ? 'Solo on' : 'Solo'}</button>
                          </div>
                        </div>
                      );
                    })}
                    {!watchlistRows.length ? <p>No outcomes loaded for the selected market.</p> : null}
                  </div>
                ) : null}

                {inspectorTab === 'market' ? (
                  <div className="qtv-market-card">
                    <h3>{marketInfo.title}</h3>
                    <div className="qtv-market-readiness">
                      <span className={marketStatusLabel === 'active' ? 'ready' : 'review'}>
                        <b>{marketStatusLabel}</b>
                        <em>Status</em>
                      </span>
                      <span className={marketCoveragePct >= 80 ? 'ready' : marketCoveragePct > 0 ? 'review' : 'empty'}>
                        <b>{marketTotalMembers ? `${marketCoveragePct.toFixed(0)}%` : '--'}</b>
                        <em>{marketReadyMembers.toLocaleString('en-US')} / {marketTotalMembers.toLocaleString('en-US')} ready</em>
                      </span>
                      <span className={dataQuality.health}>
                        <b>{dataQuality.health}</b>
                        <em>{dataQuality.gapCount.toLocaleString('en-US')} gaps · {dataQuality.spikeCount.toLocaleString('en-US')} jumps</em>
                      </span>
                    </div>
                    <div className="qtv-market-source-card">
                      <strong>{marketSourceDisplay}</strong>
                      <span>Backtest source {backendPriceSource(priceSource)} · visible rows {displayedPriceRows.toLocaleString('en-US')} · coverage rows {marketCoverageRows.toLocaleString('en-US')}</span>
                      <small>End {marketEndLabel} · Updated {marketUpdatedLabel}</small>
                    </div>
                    <dl>
                      <div><dt>Type</dt><dd>{selectedEntityKind === 'event' ? 'Event' : 'Market'}</dd></div>
                      <div><dt>Source</dt><dd>{backendPriceSource(priceSource)}</dd></div>
                      <div><dt>Outcomes</dt><dd>{displayedOutcomeCount.toLocaleString('en-US')}</dd></div>
                      <div><dt>Rows Loaded</dt><dd>{displayedPriceRows.toLocaleString('en-US')}</dd></div>
                      <div><dt>Coverage</dt><dd>{marketCoverageRows.toLocaleString('en-US')}</dd></div>
                      <div><dt>Blocks</dt><dd>{priceBlockRange}</dd></div>
                      <div><dt>Latest</dt><dd>{fmtPrice(latestPrice)}</dd></div>
                      <div><dt>Freshness</dt><dd>{lastPriceRefreshAt || '--'}</dd></div>
                      <div><dt>Status</dt><dd>{marketStatusLabel}</dd></div>
                      <div><dt>End</dt><dd>{marketEndLabel}</dd></div>
                      <div><dt>Updated</dt><dd>{marketUpdatedLabel}</dd></div>
                      <div><dt>Selected</dt><dd>{selectedOutcomeRow?.fullLabel || selectedOutcome?.outcomeLabel || '--'}</dd></div>
                      <div><dt>YES Token</dt><dd>{selectedOutcome?.buyYesTokenId || selectedOutcome?.tokenId || '--'}</dd></div>
                      <div><dt>NO Token</dt><dd>{selectedOutcome?.buyNoTokenId || '--'}</dd></div>
                      <div><dt>Slug</dt><dd>{marketSlug}</dd></div>
                    </dl>
                  </div>
                ) : null}

                {inspectorTab === 'outcomes' ? (
                  <div className="qtv-inspector-outcomes">
                    <div className="qtv-inspector-sort">
                      <span>
                        {filteredOutcomeRows.length.toLocaleString('en-US')} / {displayedOutcomeCount.toLocaleString('en-US')} outcomes
                        {chartPinnedOutcomeKeys.length || chartHiddenOutcomeKeys.length || chartSoloOutcomeKey ? (
                          <em>{chartPinnedOutcomeKeys.length} pinned · {chartHiddenOutcomeKeys.length} hidden</em>
                        ) : null}
                      </span>
                      <select value={outcomeSortKey} onChange={(event) => setOutcomeSortKey(event.currentTarget.value as OutcomeSortKey)}>
                        <option value="probability">Probability</option>
                        <option value="order">Outcome order</option>
                        <option value="rows">Rows</option>
                        <option value="volume">Volume</option>
                      </select>
                      <button type="button" disabled={!sortedOutcomeRows.length} onClick={pinTopOutcomes}>Pin Top 5</button>
                      <button type="button" disabled={!selectedOutcomeRow} onClick={() => selectedOutcomeRow && setChartSoloOutcomeKey(selectedBacktestAction === 'NO' ? selectedOutcomeRow.noKey : selectedOutcomeRow.yesKey)}>Solo selected</button>
                      <button type="button" disabled={!selectedOutcome} onClick={() => selectedOutcome && toggleOutcomeWatchlist(selectedOutcome)}>{selectedIsWatched ? 'Unwatch' : 'Watch'}</button>
                      <button type="button" disabled={!chartPinnedOutcomeKeys.length} onClick={unpinChartOutcomes}>Unpin</button>
                      <button type="button" disabled={!chartPinnedOutcomeKeys.length && !chartHiddenOutcomeKeys.length && !chartSoloOutcomeKey} onClick={resetChartOutcomeVisibility}>Reset lines</button>
                    </div>
                    <div className="qtv-inspector-outcome-manager">
                      <section className="qtv-outcome-visibility-strip">
                        <div>
                          <span>Chart</span>
                          <strong>{outcomeVisibilitySummary.visibleCount.toLocaleString('en-US')} / {outcomeVisibilitySummary.activeTotal.toLocaleString('en-US')} visible</strong>
                        </div>
                        <div>
                          <span>Pinned</span>
                          <strong>{outcomeVisibilitySummary.pinnedCount.toLocaleString('en-US')}</strong>
                        </div>
                        <div>
                          <span>Hidden</span>
                          <strong>{outcomeVisibilitySummary.hiddenCount.toLocaleString('en-US')}</strong>
                        </div>
                        <div>
                          <span>Mode</span>
                          <strong>{chartSoloOutcomeKey ? `Solo · ${outcomeVisibilitySummary.soloLabel || 'outcome'}` : selectedBacktestAction}</strong>
                        </div>
                        <div>
                          <span>Hover</span>
                          <strong>{hoveredOutcomeRow?.label || '--'}</strong>
                        </div>
                      </section>
                      <section className="qtv-managed-lines">
                        <header>
                          <div>
                            <span>Managed chart lines</span>
                            <strong>{managedOutcomeLineRows.length ? `${managedOutcomeLineRows.length} active controls` : 'No pinned, solo, hidden, or watched lines'}</strong>
                          </div>
                          <button type="button" disabled={!chartPinnedOutcomeKeys.length && !chartHiddenOutcomeKeys.length && !chartSoloOutcomeKey} onClick={resetChartOutcomeVisibility}>Reset</button>
                        </header>
                        {managedOutcomeLineRows.length ? (
                          <div className="qtv-managed-line-list">
                            {managedOutcomeLineRows.map((line) => (
                              <article
                                key={`managed-${line.key}`}
                                className={`${line.selected ? 'selected' : ''} ${line.solo ? 'solo' : ''} ${line.pinned ? 'pinned' : ''} ${line.hidden ? 'hidden' : ''}`}
                              >
                                <button type="button" onClick={() => selectOutcomeSide(line.row.outcome, line.side)}>
                                  <span>{line.side}</span>
                                  <strong title={line.row.fullLabel}>{line.row.label}</strong>
                                  <b>{fmtPrice(line.price)}</b>
                                </button>
                                <div>
                                  {line.selected ? <em>target</em> : null}
                                  {line.solo ? <em>solo</em> : null}
                                  {line.pinned ? <em>pinned</em> : null}
                                  {line.hidden ? <em>hidden</em> : null}
                                  {line.watched ? <em>watch</em> : null}
                                  <small>{line.rows.toLocaleString('en-US')} rows</small>
                                </div>
                                <nav aria-label={`${line.side} managed line controls`}>
                                  <button className={line.pinned ? 'active' : ''} type="button" onClick={() => toggleChartPinnedOutcome(line.key)}>{line.pinned ? 'Unpin' : 'Pin'}</button>
                                  <button className={line.solo ? 'active' : ''} type="button" onClick={() => setChartSoloOutcomeKey(line.solo ? '' : line.key)}>{line.solo ? 'Clear solo' : 'Solo'}</button>
                                  <button className={line.hidden ? 'active danger' : 'danger'} type="button" onClick={() => toggleChartHiddenOutcome(line.key)}>{line.hidden ? 'Show' : 'Hide'}</button>
                                  <button type="button" onClick={() => { selectOutcomeSide(line.row.outcome, line.side); setInspectorTab('book'); }}>Book</button>
                                </nav>
                              </article>
                            ))}
                          </div>
                        ) : (
                          <p>Pin, watch, hide, or solo an outcome to keep it here while scanning the event.</p>
                        )}
                      </section>
                      {selectedOutcomeRow ? (
                        <section className="qtv-selected-outcome-linebar">
                          <header>
                            <span>Selected outcome</span>
                            <strong title={selectedOutcomeRow.fullLabel}>{selectedOutcomeRow.fullLabel}</strong>
                          </header>
                          <div>
                            {([
                              ['YES', selectedOutcomeRow.yesKey, selectedOutcomeRow.yes, selectedOutcomeRow.yesRows],
                              ['NO', selectedOutcomeRow.noKey, selectedOutcomeRow.no, selectedOutcomeRow.noRows],
                            ] as Array<['YES' | 'NO', string, number, number]>).map(([side, key, price, rows]) => {
                              const isTarget = selectedBacktestAction === side;
                              const isPinned = chartPinnedOutcomeKeys.includes(key);
                              const isHidden = chartHiddenOutcomeKeys.includes(key);
                              const isSolo = chartSoloOutcomeKey === key;
                              return (
                                <article key={`selected-line-${side}`} className={`${isTarget ? 'target' : ''} ${isPinned ? 'pinned' : ''} ${isHidden ? 'hidden' : ''} ${isSolo ? 'solo' : ''}`}>
                                  <div>
                                    <b>{side}</b>
                                    <strong>{fmtPrice(price)}</strong>
                                    <span>{rows.toLocaleString('en-US')} rows</span>
                                  </div>
                                  <nav aria-label={`${side} line controls`}>
                                    <button className={isTarget ? 'active' : ''} type="button" onClick={() => selectOutcomeSide(selectedOutcomeRow.outcome, side)}>Target</button>
                                    <button className={isPinned ? 'active' : ''} type="button" onClick={() => toggleChartPinnedOutcome(key)}>{isPinned ? 'Pinned' : 'Pin'}</button>
                                    <button className={isSolo ? 'active' : ''} type="button" onClick={() => setChartSoloOutcomeKey(isSolo ? '' : key)}>{isSolo ? 'Solo on' : 'Solo'}</button>
                                    <button className={isHidden ? 'active danger' : 'danger'} type="button" onClick={() => toggleChartHiddenOutcome(key)}>{isHidden ? 'Show' : 'Hide'}</button>
                                  </nav>
                                </article>
                              );
                            })}
                          </div>
                        </section>
                      ) : null}
                      <label>
                        <span>Find</span>
                        <input
                          value={outcomeSearch}
                          onInput={(event) => setOutcomeSearch(event.currentTarget.value)}
                          placeholder="Outcome, slug, token"
                        />
                      </label>
                      <div className="qtv-outcome-filter-row" role="group" aria-label="Outcome visibility filters">
                        {([
                          ['all', 'All'],
                          ['visible', 'Visible'],
                          ['pinned', 'Pinned'],
                          ['hidden', 'Hidden'],
                          ['watched', 'Watchlist'],
                          ['issues', 'Issues'],
                        ] as Array<[OutcomeVisibilityFilter, string]>).map(([id, label]) => (
                          <button
                            key={id}
                            className={outcomeVisibilityFilter === id ? 'active' : ''}
                            type="button"
                            onClick={() => setOutcomeVisibilityFilter(id)}
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                      <div className="qtv-outcome-bulk-actions">
                        <button type="button" disabled={!filteredOutcomeRows.length} onClick={focusFilteredOutcomes}>Focus filtered</button>
                        <button type="button" disabled={!filteredOutcomeRows.length} onClick={pinFilteredOutcomes}>Pin filtered</button>
                        <button type="button" disabled={!filteredOutcomeRows.length} onClick={soloFirstFilteredOutcome}>Solo first</button>
                        <button type="button" disabled={!filteredOutcomeRows.length} onClick={hideFilteredOutcomes}>Hide filtered</button>
                        <button type="button" disabled={!filteredOutcomeRows.length} onClick={showFilteredOutcomes}>Show filtered</button>
                      </div>
                    </div>
                    {filteredOutcomeRows.map(({ outcome, label, fullLabel, yes, no, yesRows, noRows, rows, volume, yesKey, noKey, firstBlock, lastBlock, gaps, spikes, directNoRows, impliedNoRows, coverageRatio, qualityStatus, qualityReason }) => {
                      const isSelected = outcome.tokenId === selectedOutcome?.tokenId;
                      const activeKey = selectedBacktestAction === 'NO' ? noKey : yesKey;
                      const isPinned = chartPinnedOutcomeKeys.includes(yesKey) || chartPinnedOutcomeKeys.includes(noKey);
                      const isHidden = chartHiddenOutcomeKeys.includes(yesKey) || chartHiddenOutcomeKeys.includes(noKey);
                      const isSolo = chartSoloOutcomeKey === yesKey || chartSoloOutcomeKey === noKey;
                      const isHovered = hoveredChartOutcomeKey === yesKey || hoveredChartOutcomeKey === noKey;
                      const watchKey = watchKeyForOutcome(outcome);
                      const isWatched = watchKey ? watchlistKeys.includes(watchKey) : false;
                      return (
                        <div key={`side-${outcome.tokenId}`} className={`qtv-inspector-outcome ${isSelected ? 'active' : ''} ${isHovered ? 'hovered' : ''} ${isPinned ? 'pinned' : ''} ${isHidden ? 'hidden' : ''} ${isSolo ? 'solo' : ''} ${isWatched ? 'watched' : ''}`} title={fullLabel}>
                          <button type="button" onClick={() => selectOutcomeSide(outcome, 'YES')}>
                            <strong>{label}</strong>
                            <span>{rows.toLocaleString('en-US')} rows · {volume.toLocaleString('en-US', { maximumFractionDigits: 0 })} vol</span>
                            <em>
                              {firstBlock ? Math.floor(firstBlock).toLocaleString('en-US') : '--'} → {lastBlock ? Math.floor(lastBlock).toLocaleString('en-US') : '--'}
                            </em>
                          </button>
                          <div>
                            <button className={isSelected && selectedBacktestAction === 'YES' ? 'active' : ''} type="button" onClick={() => selectOutcomeSide(outcome, 'YES')}>YES {fmtPrice(yes)}</button>
                            <button className={isSelected && selectedBacktestAction === 'NO' ? 'active no' : 'no'} type="button" disabled={!outcome.buyNoTokenId} onClick={() => selectOutcomeSide(outcome, 'NO')}>NO {fmtPrice(no)}</button>
                          </div>
                          <div className="qtv-outcome-quality-tags">
                            <span className={qualityStatus}>{qualityStatus}</span>
                            <span className="reason" title={qualityReason}>{qualityReason}</span>
                            <span className="coverage">
                              <i style={{ width: `${Math.max(4, Math.round(coverageRatio * 100))}%` }} />
                              {formatCoverageRatio(coverageRatio)} coverage
                            </span>
                            <span>YES {yesRows.toLocaleString('en-US')}</span>
                            <span>NO {noRows.toLocaleString('en-US')}</span>
                            <span>{gaps} gaps</span>
                            <span>{spikes} jumps</span>
                            <span>{directNoRows.toLocaleString('en-US')} direct NO</span>
                            <span>{impliedNoRows.toLocaleString('en-US')} implied NO</span>
                          </div>
                          <div className="qtv-outcome-line-actions">
                            <button className={chartPinnedOutcomeKeys.includes(activeKey) ? 'active' : ''} type="button" onClick={() => toggleChartPinnedOutcome(activeKey)}>Pin</button>
                            <button className={chartSoloOutcomeKey === activeKey ? 'active' : ''} type="button" onClick={() => setChartSoloOutcomeKey(chartSoloOutcomeKey === activeKey ? '' : activeKey)}>Solo</button>
                            <button className={chartHiddenOutcomeKeys.includes(activeKey) ? 'active danger' : 'danger'} type="button" onClick={() => toggleChartHiddenOutcome(activeKey)}>{chartHiddenOutcomeKeys.includes(activeKey) ? 'Show' : 'Hide'}</button>
                            <button className={isWatched ? 'active' : ''} type="button" onClick={() => toggleOutcomeWatchlist(outcome)}>{isWatched ? 'Watching' : 'Watch'}</button>
                          </div>
                        </div>
                      );
                    })}
                    {!filteredOutcomeRows.length ? <p>No outcomes match the current filter.</p> : null}
                  </div>
                ) : null}

                {inspectorTab === 'book' ? (
                  <div className="qtv-book-card">
                    <h3>{selectedOutcomeRow?.fullLabel || selectedOutcome?.outcomeLabel || 'Selected outcome'}</h3>
                    <div className="qtv-book-ladder">
                      <span>YES</span><b>{fmtPrice(selectedOutcomeRow?.yes || 0)}</b>
                      <span>NO</span><b>{fmtPrice(selectedOutcomeRow?.no || 0)}</b>
                      <span>Rows</span><b>{(selectedOutcomeRow?.rows || 0).toLocaleString('en-US')}</b>
                      <span>Volume</span><b>{(selectedOutcomeRow?.volume || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}</b>
                    </div>
                    <div className="qtv-book-actions">
                      <button className={selectedBacktestAction === 'YES' ? 'active' : ''} type="button" onClick={() => setSelectedBacktestAction('YES')}>Target YES</button>
                      <button className={selectedBacktestAction === 'NO' ? 'active no' : 'no'} type="button" disabled={!selectedOutcome?.buyNoTokenId} onClick={() => setSelectedBacktestAction('NO')}>Target NO</button>
                      <button type="button" onClick={() => setLiveLobRefreshSeq((current) => current + 1)}>{liveLobStatus === 'loading' ? 'Loading book' : 'Refresh CLOB'}</button>
                      <button type="button" onClick={() => setInspectorTab('dataQuality')}>Data quality</button>
                    </div>
                    <section className={`qtv-live-book ${liveLobStatus}`}>
                      <header>
                        <div>
                          <strong>Order Book</strong>
                          <span>{liveLobStatus === 'ready' ? `${selectedBacktestAction} · ${liveLob?.source || 'clob-book'} · ${liveLob?.bookStatus || 'ok'}` : liveLobStatus === 'loading' ? 'loading live /book...' : liveLobStatus === 'error' ? liveLobError || 'CLOB unavailable' : 'no live levels returned'}</span>
                        </div>
                        <em>{liveLob?.fetchedAt ? new Date(liveLob.fetchedAt).toLocaleTimeString() : '--'}</em>
                      </header>
                      <div className="qtv-live-book-summary">
                        <span>Target</span><b>{selectedBacktestAction}</b>
                        <span>Bid</span><b>{formatBookValue(liveBookSide?.bestBid)}</b>
                        <span>Ask</span><b>{formatBookValue(liveBookSide?.bestAsk)}</b>
                        <span>Spread</span><b>{formatBookValue(liveBookSide?.spread)}</b>
                      </div>
                      <div className="qtv-live-book-trade-strip">
                        <article>
                          <span>Best Bid</span>
                          <strong className="bid">{formatBookValue(liveBookSide?.bestBid)}</strong>
                        </article>
                        <article>
                          <span>Mid</span>
                          <strong>{formatBookValue(liveBookMid)}</strong>
                        </article>
                        <article>
                          <span>Best Ask</span>
                          <strong className="ask">{formatBookValue(liveBookSide?.bestAsk)}</strong>
                        </article>
                        <article>
                          <span>Depth</span>
                          <strong>{formatBookValue(liveBookDepthTotal, 0)}</strong>
                        </article>
                      </div>
                      <div className="qtv-live-book-imbalance" aria-label="live book imbalance">
                        <span>Bid {Math.round(liveBookBidImbalance * 100)}%</span>
                        <div>
                          <i className="bid" style={{ width: `${Math.max(2, Math.round(liveBookBidImbalance * 100))}%` }} />
                          <i className="ask" style={{ width: `${Math.max(2, Math.round((1 - liveBookBidImbalance) * 100))}%` }} />
                        </div>
                        <span>Ask {Math.round((1 - liveBookBidImbalance) * 100)}%</span>
                      </div>
                      <div className="qtv-live-book-ladder">
                        <section className="ask">
                          <header>
                            <strong>Asks</strong>
                            <span>top {liveBookAskRows.length} · {formatBookValue(liveBookAskDepth, 0)} USDC</span>
                          </header>
                          <div className="qtv-live-book-ladder-head"><span>Price</span><span>Shares</span><span>Total</span></div>
                          {liveBookAskRows.length ? liveBookAskRows.map((level) => (
                            <div key={`ask-${level.price}-${level.size}`} className="qtv-live-book-ladder-row ask">
                              <i style={{ width: `${level.fillPct}%` }} />
                              <b>{formatBookValue(level.price)}</b>
                              <span>{formatBookValue(level.size, 2)}</span>
                              <em>{formatBookValue(level.cumulative, 0)}</em>
                            </div>
                          )) : <p>No live asks for {selectedBacktestAction}.</p>}
                        </section>
                        <div className="qtv-live-book-midline">
                          <span />
                          <strong>MID {formatBookValue(liveBookMid)}</strong>
                          <em>spread {formatBookValue(liveBookSide?.spread)}</em>
                        </div>
                        <section className="bid">
                          <header>
                            <strong>Bids</strong>
                            <span>top {liveBookBidRows.length} · {formatBookValue(liveBookBidDepth, 0)} USDC</span>
                          </header>
                          <div className="qtv-live-book-ladder-head"><span>Price</span><span>Shares</span><span>Total</span></div>
                          {liveBookBidRows.length ? liveBookBidRows.map((level) => (
                            <div key={`bid-${level.price}-${level.size}`} className="qtv-live-book-ladder-row bid">
                              <i style={{ width: `${level.fillPct}%` }} />
                              <b>{formatBookValue(level.price)}</b>
                              <span>{formatBookValue(level.size, 2)}</span>
                              <em>{formatBookValue(level.cumulative, 0)}</em>
                            </div>
                          )) : <p>No live bids for {selectedBacktestAction}.</p>}
                        </section>
                      </div>
                      <div className="qtv-lob-snapshot-tape">
                        <header>
                          <strong>Depth Snapshots</strong>
                          <span>{lobSnapshotStatus === 'ready' ? `${lobSnapshots.length} persisted` : lobSnapshotStatus === 'loading' ? 'syncing history' : lobSnapshotStatus === 'error' ? lobSnapshotError : 'no snapshots yet'}</span>
                        </header>
                        <div>
                          {lobSnapshots.slice(0, 8).map((snapshot) => (
                            <article key={snapshot.snapshotId}>
                              <span>{snapshot.fetchedAt ? new Date(snapshot.fetchedAt).toLocaleTimeString() : '--'}</span>
                              <b>{formatBookValue(snapshot.mid)}</b>
                              <em>{formatBookValue(snapshot.depthTotal, 0)}</em>
                              <i>{formatBookPercent(snapshot.imbalance, 0)}</i>
                            </article>
                          ))}
                          {!lobSnapshots.length ? <p>{lobSnapshotStatus === 'loading' ? 'Persisting the current CLOB book, then loading recent snapshots.' : 'Open or refresh this book to start building CLOB depth history.'}</p> : null}
                        </div>
                      </div>
                      <div className={`qtv-book-execution ${bookExecutionQuality.status}`}>
                        <header>
                          <div>
                            <strong>{bookExecutionQuality.title}</strong>
                            <span>{bookExecutionQuality.nextAction}</span>
                          </div>
                          <b>{bookExecutionQuality.confidence}%</b>
                        </header>
                        <div className="qtv-book-execution-meter">
                          <i style={{ width: `${bookExecutionQuality.confidence}%` }} />
                        </div>
                        <dl>
                          <div><dt>Block close</dt><dd>{formatBookValue(bookExecutionQuality.blockClose)}</dd></div>
                          <div><dt>Live mid</dt><dd>{formatBookValue(bookExecutionQuality.mid)}</dd></div>
                          <div><dt>Spread</dt><dd>{formatBookValue(bookExecutionQuality.spread)}</dd></div>
                          <div><dt>Top depth</dt><dd>{formatBookValue(bookExecutionQuality.topDepth, 0)}</dd></div>
                          <div><dt>Mid drift</dt><dd>{formatBookValue(bookExecutionQuality.drift)}</dd></div>
                          <div><dt>Snapshot</dt><dd>{bookExecutionQuality.ageSeconds === null ? '--' : `${bookExecutionQuality.ageSeconds}s ago`}</dd></div>
                        </dl>
                        <div className={`qtv-book-fill-estimate ${selectedBookFillEstimate.fillPct >= 0.9 ? 'ready' : selectedBookFillEstimate.fillPct > 0 ? 'review' : 'empty'}`}>
                          <header>
                            <div>
                              <strong>Market buy estimate</strong>
                              <span>{formatBookValue(selectedBookFillEstimate.target, 0)} USDC target · {selectedBacktestAction} asks</span>
                            </div>
                            <b>{Math.round(Math.min(1, selectedBookFillEstimate.fillPct) * 100)}%</b>
                          </header>
                          <div className="qtv-book-fill-meter">
                            <i style={{ width: `${Math.max(2, Math.min(100, Math.round(selectedBookFillEstimate.fillPct * 100)))}%` }} />
                          </div>
                          <dl>
                            <div><dt>VWAP</dt><dd>{selectedBookFillEstimate.vwap === null ? '--' : formatBookValue(selectedBookFillEstimate.vwap)}</dd></div>
                            <div><dt>Slippage</dt><dd>{selectedBookFillEstimate.slippage === null ? '--' : formatBookValue(selectedBookFillEstimate.slippage)}</dd></div>
                            <div><dt>Filled</dt><dd>{formatBookValue(selectedBookFillEstimate.spent, 0)} USDC</dd></div>
                            <div><dt>Remainder</dt><dd>{formatBookValue(selectedBookFillEstimate.remaining, 0)} USDC</dd></div>
                            <div><dt>Shares</dt><dd>{formatBookValue(selectedBookFillEstimate.shares, 2)}</dd></div>
                            <div><dt>Levels</dt><dd>{selectedBookFillEstimate.levelsUsed.toLocaleString('en-US')}</dd></div>
                          </dl>
                        </div>
                        {bookExecutionQuality.caveats.length ? (
                          <ul>
                            {bookExecutionQuality.caveats.slice(0, 3).map((caveat) => <li key={caveat}>{caveat}</li>)}
                          </ul>
                        ) : (
                          <p>Live spread, depth, and historical block-close quality are aligned for the selected side.</p>
                        )}
                      </div>
                      {!liveBookHasLevels ? <p>{liveLobStatus === 'loading' ? 'Fetching Polymarket CLOB /book through the API server.' : 'Live CLOB depth is empty for this token pair right now.'}</p> : null}
                    </section>
                    <dl className="qtv-book-metadata">
                      <div><dt>YES token</dt><dd>{selectedOutcome?.buyYesTokenId || selectedOutcome?.tokenId || '--'}</dd></div>
                      <div><dt>NO token</dt><dd>{selectedOutcome?.buyNoTokenId || '--'}</dd></div>
                      <div><dt>Condition</dt><dd>{selectedOutcome?.conditionId || '--'}</dd></div>
                      <div><dt>Block range</dt><dd>{selectedBookQuality.firstBlock ? Math.floor(selectedBookQuality.firstBlock).toLocaleString('en-US') : '--'} → {selectedBookQuality.lastBlock ? Math.floor(selectedBookQuality.lastBlock).toLocaleString('en-US') : '--'}</dd></div>
                      <div><dt>YES rows</dt><dd>{selectedBookQuality.yes.rows.toLocaleString('en-US')}</dd></div>
                      <div><dt>NO rows</dt><dd>{selectedBookQuality.no.rows.toLocaleString('en-US')}</dd></div>
                      <div><dt>Direct NO</dt><dd>{selectedBookQuality.directNoRows.toLocaleString('en-US')}</dd></div>
                      <div><dt>Implied NO</dt><dd>{selectedBookQuality.impliedNoRows.toLocaleString('en-US')}</dd></div>
                      <div><dt>Gaps</dt><dd>{selectedBookQuality.gaps.toLocaleString('en-US')}</dd></div>
                      <div><dt>Jumps</dt><dd>{selectedBookQuality.spikes.toLocaleString('en-US')}</dd></div>
                    </dl>
                    <div className={`qtv-book-status ${selectedBookQuality.status}`}>
                      <strong>{selectedBookQuality.status === 'ready' ? 'Block-close book proxy ready' : selectedBookQuality.status === 'review' ? 'Review gaps or jumps' : 'No selected outcome rows'}</strong>
                      <span>Backtest execution still uses block-close rows. Live CLOB depth above is the current order book context for the selected YES/NO tokens.</span>
                    </div>
                  </div>
                ) : null}

                {inspectorTab === 'trades' ? (
                  <div
                    className="qtv-inspector-trades"
                    tabIndex={0}
                    onKeyDown={(event) => {
                      if (event.key === 'ArrowUp') {
                        event.preventDefault();
                        selectInspectorTradeOffset(-1);
                      }
                      if (event.key === 'ArrowDown') {
                        event.preventDefault();
                        selectInspectorTradeOffset(1);
                      }
                      if (event.key === 'Enter' && selectedTradeRow) {
                        event.preventDefault();
                        setSelectedTradeId(selectedTradeRow.id);
                        setTesterTab('trades');
                        setStrategyDrawerCollapsed(false);
                      }
                      if (event.key === 'Escape' && selectedTradeRow) {
                        event.preventDefault();
                        setSelectedTradeId(null);
                      }
                    }}
                  >
                    <section className="qtv-inspector-trade-head">
                      <div>
                        <span>Trades</span>
                        <strong>{inspectorTradeStats.count.toLocaleString('en-US')}</strong>
                      </div>
                      <div>
                        <span>Net PnL</span>
                        <strong className={inspectorTradeStats.netPnl >= 0 ? 'positive' : 'negative'}>
                          {inspectorTradeStats.netPnl >= 0 ? '+' : ''}{inspectorTradeStats.netPnl.toFixed(2)}
                        </strong>
                      </div>
                      <div>
                        <span>Win rate</span>
                        <strong>{(inspectorTradeStats.winRate * 100).toFixed(1)}%</strong>
                      </div>
                    </section>
                    <section className="qtv-inspector-trade-focus">
                      <div className="qtv-inspector-trade-focus-copy">
                        <span>Focused trade</span>
                        <strong>{selectedTradeRow ? `${selectedTradeRow.id} · ${selectedTradeRow.side} ${selectedTradeRow.outcome}` : 'None selected'}</strong>
                        <em>{selectedTradeRow && inspectorTradeStats.selectedIndex >= 0 ? `${inspectorTradeStats.selectedIndex + 1} of ${filteredTrades.length} · chart zooms to entry / exit` : 'Click a trade, then use ↑↓ / Enter / Esc.'}</em>
                      </div>
                      <div className="qtv-inspector-trade-toolbar">
                        <button
                          type="button"
                          disabled={inspectorTradeStats.selectedIndex <= 0}
                          onClick={() => selectInspectorTradeOffset(-1)}
                        >
                          Prev
                        </button>
                        <button
                          type="button"
                          disabled={!selectedTradeRow}
                          onClick={() => {
                            if (!selectedTradeRow) return;
                            setSelectedTradeId(selectedTradeRow.id);
                            setTesterTab('trades');
                            setStrategyDrawerCollapsed(false);
                          }}
                        >
                          Focus
                        </button>
                        <button
                          type="button"
                          disabled={inspectorTradeStats.selectedIndex < 0 || inspectorTradeStats.selectedIndex >= filteredTrades.length - 1}
                          onClick={() => selectInspectorTradeOffset(1)}
                        >
                          Next
                        </button>
                        <button type="button" disabled={!selectedTradeRow} onClick={() => setSelectedTradeId(null)}>Clear</button>
                      </div>
                      {selectedTradeRow ? (
                        <div className="qtv-inspector-trade-focus-grid">
                          <div><span>Entry</span><b>{selectedTradeRow.entryX ? Math.floor(selectedTradeRow.entryX).toLocaleString('en-US') : selectedTradeRow.entryTime}</b><small>{fmtPrice(selectedTradeRow.entryPrice)}</small></div>
                          <div><span>Exit</span><b>{selectedTradeRow.exitX ? Math.floor(selectedTradeRow.exitX).toLocaleString('en-US') : selectedTradeRow.exitTime}</b><small>{fmtPrice(selectedTradeRow.exitPrice)}</small></div>
                          <div><span>Span</span><b>{selectedTradeBlockSpan !== null ? `${selectedTradeBlockSpan.toLocaleString('en-US')} blocks` : selectedTradeRow.holdingTime}</b><small>{selectedTradeRow.exitReason}</small></div>
                          <div><span>PnL</span><b className={selectedTradeRow.pnl >= 0 ? 'positive' : 'negative'}>{selectedTradeRow.pnl >= 0 ? '+' : ''}{selectedTradeRow.pnl.toFixed(2)} USDC</b><small>{selectedTradeRow.pnlPct.toFixed(2)}%</small></div>
                        </div>
                      ) : null}
                    </section>
                    {recentTradeRows.map((trade) => (
                      <button
                        key={`inspector-trade-${trade.id}`}
                        className={`qtv-inspector-trade ${trade.id === selectedTradeId ? 'active' : ''} ${trade.pnl >= 0 ? 'win' : 'loss'}`}
                        type="button"
                        title="Open this trade in Strategy Tester and focus the chart"
                        onClick={() => { setSelectedTradeId(trade.id); setTesterTab('trades'); setStrategyDrawerCollapsed(false); }}
                      >
                        <header>
                          <strong>{trade.id}</strong>
                          <i>{trade.side} {trade.outcome}</i>
                        </header>
                        <div className="qtv-inspector-trade-blocks">
                          <span>
                            <em>Entry</em>
                            <b>{trade.entryX ? Math.floor(trade.entryX).toLocaleString('en-US') : trade.entryTime}</b>
                            <small>{fmtPrice(trade.entryPrice)}</small>
                          </span>
                          <span>
                            <em>Exit</em>
                            <b>{trade.exitX ? Math.floor(trade.exitX).toLocaleString('en-US') : trade.exitTime}</b>
                            <small>{fmtPrice(trade.exitPrice)}</small>
                          </span>
                        </div>
                        <footer>
                          <span>{trade.holdingBars.toLocaleString('en-US')} bars · {trade.exitReason}</span>
                          <b className={trade.pnl >= 0 ? 'positive' : 'negative'}>{trade.pnl >= 0 ? '+' : ''}{trade.pnl.toFixed(2)} USDC</b>
                        </footer>
                      </button>
                    ))}
                    {!recentTradeRows.length ? <p>No completed backtest trades yet. Run a backtest to populate this tab.</p> : null}
                  </div>
                ) : null}

                {inspectorTab === 'dataQuality' ? (
                  <div className="qtv-data-quality">
                    <header>
                      <strong>{dataQuality.health === 'ready' ? 'Ready' : dataQuality.health === 'review' ? 'Review suggested' : 'No rows'}</strong>
                      <span>{dataQuality.source}</span>
                    </header>
                    <section className={`qtv-data-trust-card ${dataTrustDecision.status}`}>
                      <div>
                        <span>Trust verdict</span>
                        <strong>{dataTrustDecision.title}</strong>
                        <em>{dataTrustDecision.nextAction}</em>
                      </div>
                      <b>{dataTrustDecision.confidence}%</b>
                      <div className="qtv-data-trust-meter" aria-label="Data confidence">
                        <i style={{ width: `${Math.max(3, dataTrustDecision.confidence)}%` }} />
                      </div>
                      <ul>
                        {dataTrustDecision.evidence.slice(0, 4).map((item) => <li key={item}>{item}</li>)}
                      </ul>
                      {dataTrustDecision.caveats.length ? (
                        <div className="qtv-data-trust-caveats">
                          {dataTrustDecision.caveats.map((item) => <span key={item}>{item}</span>)}
                        </div>
                      ) : null}
                      <footer>
                        <button type="button" onClick={() => setMarketReloadKey((current) => current + 1)}>Refresh data</button>
                        <button
                          type="button"
                          onClick={() => {
                            setOutcomeVisibilityFilter('issues');
                            setInspectorTab('outcomes');
                          }}
                        >
                          Review issues
                        </button>
                        <button type="button" disabled={dataTrustDecision.status === 'blocked'} onClick={() => void runBacktest()}>
                          Run backtest
                        </button>
                      </footer>
                    </section>
                    <section className={`qtv-selected-target-trust ${selectedTargetTrust.status}`}>
                      <div>
                        <span>Selected target</span>
                        <strong title={selectedTargetTrust.label}>{selectedTargetTrust.label}</strong>
                        <em>{selectedBacktestAction} · {selectedTargetTrust.nextAction}</em>
                      </div>
                      <b>{selectedTargetTrust.confidence}%</b>
                      <div className="qtv-selected-target-meter" aria-label="Selected target confidence">
                        <i style={{ width: `${Math.max(3, selectedTargetTrust.confidence)}%` }} />
                      </div>
                      <dl>
                        <div><dt>Side rows</dt><dd>{selectedTargetTrust.activeRows.toLocaleString('en-US')}</dd></div>
                        <div><dt>Price</dt><dd>{selectedTargetTrust.price !== null ? fmtPrice(selectedTargetTrust.price) : '--'}</dd></div>
                        <div><dt>First block</dt><dd>{selectedTargetTrust.firstBlock ? Math.floor(selectedTargetTrust.firstBlock).toLocaleString('en-US') : '--'}</dd></div>
                        <div><dt>Latest block</dt><dd>{selectedTargetTrust.lastBlock ? Math.floor(selectedTargetTrust.lastBlock).toLocaleString('en-US') : '--'}</dd></div>
                        <div><dt>Coverage</dt><dd>{formatCoverageRatio(selectedTargetTrust.coverageRatio)}</dd></div>
                        <div><dt>Stale</dt><dd>{selectedTargetTrust.staleBlocks ? `${Math.floor(selectedTargetTrust.staleBlocks).toLocaleString('en-US')} blocks` : '0 blocks'}</dd></div>
                        <div><dt>Direct rows</dt><dd>{selectedTargetTrust.directRows.toLocaleString('en-US')}</dd></div>
                        <div><dt>Implied rows</dt><dd>{selectedTargetTrust.impliedRows.toLocaleString('en-US')}</dd></div>
                      </dl>
                      <ul>
                        {selectedTargetTrust.evidence.slice(0, 4).map((item) => <li key={item}>{item}</li>)}
                      </ul>
                      {selectedTargetTrust.caveats.length ? (
                        <div className="qtv-selected-target-caveats">
                          {selectedTargetTrust.caveats.map((item) => <span key={item}>{item}</span>)}
                        </div>
                      ) : null}
                      <footer>
                        <button type="button" onClick={() => setInspectorTab('book')}>Open book</button>
                        <button
                          type="button"
                          onClick={() => {
                            setOutcomeVisibilityFilter('all');
                            setInspectorTab('outcomes');
                          }}
                        >
                          Open outcome
                        </button>
                        <button type="button" disabled={selectedTargetTrust.status === 'blocked'} onClick={() => void runBacktest()}>
                          Run target
                        </button>
                      </footer>
                    </section>
                    <section className="qtv-quality-summary-strip">
                      <div className="ready">
                        <span>Ready</span>
                        <strong>{outcomeQualitySummary.ready.toLocaleString('en-US')}</strong>
                        <em>{outcomeQualitySummary.readyPct.toFixed(0)}% outcomes</em>
                      </div>
                      <div className={outcomeQualitySummary.review ? 'review' : 'ready'}>
                        <span>Review</span>
                        <strong>{outcomeQualitySummary.review.toLocaleString('en-US')}</strong>
                        <em>gaps or jumps</em>
                      </div>
                      <div className={outcomeQualitySummary.stale ? 'stale' : 'ready'}>
                        <span>Stale</span>
                        <strong>{outcomeQualitySummary.stale.toLocaleString('en-US')}</strong>
                        <em>behind latest block</em>
                      </div>
                      <div className={outcomeQualitySummary.empty ? 'empty' : 'ready'}>
                        <span>Empty</span>
                        <strong>{outcomeQualitySummary.empty.toLocaleString('en-US')}</strong>
                        <em>no rows loaded</em>
                      </div>
                    </section>
                    {outcomeQualitySummary.worst ? (
                      <section className={`qtv-quality-worst-card ${outcomeQualitySummary.worst.status}`}>
                        <span>Highest risk outcome</span>
                        <strong>{outcomeQualitySummary.worst.label}</strong>
                        <em>
                          {outcomeQualitySummary.worst.status} · {outcomeQualitySummary.worst.rows.toLocaleString('en-US')} rows · {outcomeQualitySummary.worst.gaps.toLocaleString('en-US')} gaps · {outcomeQualitySummary.worst.spikes.toLocaleString('en-US')} jumps
                          {outcomeQualitySummary.worst.staleBlocks ? ` · ${outcomeQualitySummary.worst.staleBlocks.toLocaleString('en-US')} stale blocks` : ''}
                        </em>
                      </section>
                    ) : null}
                    <dl>
                      <div><dt>Loaded rows</dt><dd>{dataQuality.rows.toLocaleString('en-US')}</dd></div>
                      <div><dt>Outcome rows</dt><dd>{dataQuality.outcomeRows.toLocaleString('en-US')}</dd></div>
                      <div><dt>First block</dt><dd>{dataQuality.firstBlock ? Math.floor(dataQuality.firstBlock).toLocaleString('en-US') : '--'}</dd></div>
                      <div><dt>Latest block</dt><dd>{dataQuality.latestBlock ? Math.floor(dataQuality.latestBlock).toLocaleString('en-US') : '--'}</dd></div>
                      <div><dt>Median spacing</dt><dd>{dataQuality.medianDelta ? `${Math.floor(dataQuality.medianDelta).toLocaleString('en-US')} blocks` : '--'}</dd></div>
                      <div><dt>Large gaps</dt><dd>{dataQuality.gapCount.toLocaleString('en-US')}</dd></div>
                      <div><dt>Large jumps</dt><dd>{dataQuality.spikeCount.toLocaleString('en-US')}</dd></div>
                      <div><dt>Direct YES</dt><dd>{dataQuality.directYesRows.toLocaleString('en-US')}</dd></div>
                      <div><dt>Direct NO</dt><dd>{dataQuality.directNoRows.toLocaleString('en-US')}</dd></div>
                      <div><dt>Implied NO</dt><dd>{dataQuality.impliedNoRows.toLocaleString('en-US')}</dd></div>
                    </dl>
                    <div className="qtv-quality-notes">
                      {dataQuality.warnings.length ? dataQuality.warnings.map((warning) => <span key={warning}>{warning}</span>) : <span>Block-close coverage looks usable for this visible window.</span>}
                    </div>
                    <section className="qtv-build-quality">
                      <header>
                        <strong>Build and worker status</strong>
                        <span>{buildRunSummary.health === 'ready' ? 'recent runs clean' : buildRunSummary.health === 'review' ? `${buildRunSummary.errors.toLocaleString('en-US')} errors` : 'no runs loaded'}</span>
                      </header>
                      <dl>
                        <div><dt>Latest run</dt><dd>{buildRunSummary.latest?.runId ? `#${buildRunSummary.latest.runId}` : '--'}</dd></div>
                        <div><dt>Latest status</dt><dd>{buildRunSummary.latest?.status || '--'}</dd></div>
                        <div><dt>Rows written</dt><dd>{buildRunSummary.rowsWritten.toLocaleString('en-US')}</dd></div>
                        <div><dt>Errors</dt><dd>{buildRunSummary.errors.toLocaleString('en-US')}</dd></div>
                      </dl>
                      <div className="qtv-build-run-list">
                        {recentBuildRuns.map((run) => {
                          const statusClass = buildRunStatusClass(run);
                          const complete = toNumber(run.marketsComplete);
                          const total = toNumber(run.marketsTotal);
                          const progress = total ? `${complete.toLocaleString('en-US')} / ${total.toLocaleString('en-US')}` : '--';
                          return (
                            <button
                              key={`build-run-${run.runId}`}
                              className={`qtv-build-run-row ${statusClass}`}
                              type="button"
                              title={run.lastError || `${run.source} ${run.mode || ''}`}
                            >
                              <span>
                                <strong>#{run.runId} {run.source}</strong>
                                <em>{run.mode || 'default'} · {formatBuildTime(run.finishedAt || run.startedAt)}</em>
                              </span>
                              <b>{run.status}</b>
                              <small>{progress}</small>
                              <small>{toNumber(run.rowsWritten).toLocaleString('en-US')} rows</small>
                            </button>
                          );
                        })}
                        {!recentBuildRuns.length ? <p>No build status rows returned yet.</p> : null}
                      </div>
                    </section>
                    <section className="qtv-outcome-quality">
                      <header>
                        <strong>Outcome coverage</strong>
                        <span>{outcomeQualityRows.length.toLocaleString('en-US')} inspected</span>
                      </header>
                      {outcomeQualityRows.map((row) => (
                        <button
                          key={row.key}
                          className={`qtv-outcome-quality-row ${row.status}`}
                          type="button"
                          title={`${row.label}\n${row.firstBlock ? Math.floor(row.firstBlock).toLocaleString('en-US') : '--'} -> ${row.lastBlock ? Math.floor(row.lastBlock).toLocaleString('en-US') : '--'}`}
                          onClick={() => {
                            const next = marketSeries?.outcomes?.find((outcome) => (outcome.tokenId || outcome.marketSlug || outcome.outcomeLabel) === row.key);
                            if (next?.tokenId) {
                              setSelectedOutcomeTokenId(next.tokenId);
                              setInspectorTab('outcomes');
                            }
                          }}
                        >
                          <span>
                            <strong>{row.label}</strong>
                            <em>{row.firstBlock ? Math.floor(row.firstBlock).toLocaleString('en-US') : '--'} {'->'} {row.lastBlock ? Math.floor(row.lastBlock).toLocaleString('en-US') : '--'}</em>
                          </span>
                          <b>{row.status}</b>
                          <small>{row.yesRows.toLocaleString('en-US')}Y / {row.noRows.toLocaleString('en-US')}N</small>
                          <small>{row.gaps} gaps · {row.spikes} jumps</small>
                          <div className="qtv-outcome-quality-detail">
                            <span className="coverage">
                              <i style={{ width: `${Math.max(3, Math.round(row.coverageRatio * 100))}%` }} />
                              {formatCoverageRatio(row.coverageRatio)}
                            </span>
                            <span title={row.reason}>{row.reason}</span>
                            <span>{row.staleBlocks ? `${row.staleBlocks.toLocaleString('en-US')} stale blocks` : 'fresh'}</span>
                            <span>{row.directNoRows.toLocaleString('en-US')} direct NO / {row.impliedNoRows.toLocaleString('en-US')} implied</span>
                          </div>
                        </button>
                      ))}
                    </section>
                  </div>
                ) : null}
              </div>
            </>
          ) : null}
        </aside>

        <section className="qtv-strategy-drawer" aria-label="Strategy tester drawer">
          <header className="qtv-drawer-head">
            <div>
              <strong>Strategy Tester</strong>
              <span>{backtestStatus} · {backtestEngine} · {displayedPriceRows.toLocaleString('en-US')} rows</span>
            </div>
            <button type="button" onClick={() => setStrategyDrawerCollapsed((current) => !current)}>{strategyDrawerCollapsed ? 'Expand' : 'Collapse'}</button>
          </header>
          {!strategyDrawerCollapsed ? (
            <StrategyTesterPanel
              result={backtestResult}
              testerTab={testerTab}
              deepBacktest={deepBacktest}
              selectedTradeId={selectedTradeId}
              performanceSearch={performanceSearch}
              performanceSortKey={performanceSortKey}
              performanceSortDirection={performanceSortDirection}
              tradeFilters={tradeFilters}
              filteredPerformanceRows={filteredPerformanceRows}
              filteredTrades={filteredTrades}
              onTesterTabChange={setTesterTab}
              onDeepBacktestChange={() => setDeepBacktest((current) => !current)}
              onRefresh={() => void runBacktest()}
              onBatchBacktest={() => void runBatchBacktest()}
              onSplitBacktest={() => void runSplitBacktest()}
              onWalkForwardBacktest={() => void runWalkForwardBacktest()}
              onExport={exportBacktest}
              onPerformanceSearchChange={setPerformanceSearch}
              onPerformanceSortChange={togglePerformanceSort}
              onTradeFilterToggle={toggleTradeFilter}
              onTradeSelect={(tradeId) => {
                setSelectedTradeId(tradeId);
                setTesterTab('trades');
              }}
              strategyParameters={strategyParameters}
              onStrategyParametersChange={updateStrategyParameters}
              onStrategyAutoTune={autoTuneStrategyParameters}
              marketTitle={marketInfo.title}
              dataSource={backendPriceSource(priceSource)}
              engine={backtestEngine}
              rowCount={displayedPriceRows}
              backtestStatus={backtestStatus}
              batchRows={batchBacktestRows}
              batchStatus={batchBacktestStatus}
              splitRows={splitBacktestRows}
              splitStatus={splitBacktestStatus}
              walkForwardRows={walkForwardRows}
              walkForwardStatus={walkForwardStatus}
              recentBacktestRuns={recentBacktestRuns}
              backtestRunsStatus={backtestRunsStatus}
              onRunLoad={(runId) => void loadBacktestRun(runId)}
              benchmarkRun={benchmarkRun}
              benchmarkRows={benchmarkRows}
              benchmarkArtifacts={benchmarkArtifacts}
              benchmarkUniverses={benchmarkUniverses}
              selectedBenchmarkUniverse={benchmarkUniverse}
              selectedBenchmarkLimit={benchmarkLimit}
              benchmarkStatus={benchmarkStatus === 'idle' && recentBenchmarkRuns.length ? 'ready' : benchmarkStatus}
              onRunBenchmark={() => void runBenchmark()}
              onBenchmarkUniverseChange={setBenchmarkUniverse}
              onBenchmarkLimitChange={setBenchmarkLimit}
            />
          ) : null}
        </section>
      </main>

      <div className="qtv-statusbar">
        <span><i>Source</i><b>{priceSource === 'orderfilled' ? 'OrderFilled' : 'Frontend'}</b></span>
        <span><i>Target</i><b>{selectedOutcome?.outcomeLabel || 'Outcome'} {selectedBacktestAction}</b></span>
        <span><i>Latest YES</i><b>{fmtPrice(toNumber(selectedBacktestAction === 'NO' ? selectedOutcome?.buyNoPrice : selectedOutcome?.buyYesPrice) || latestPrice)}</b></span>
        <span><i>Rows</i><b>{displayedPriceRows.toLocaleString('en-US')}</b></span>
        <span><i>Outcomes</i><b>{displayedOutcomeCount}</b></span>
        <span><i>Engine</i><b>{backtestEngine}</b></span>
        <span><i>Build Runs</i><b>{runs.length}</b></span>
        <span><i>Live</i><b>{livePriceRefreshEnabled ? `ON ${lastPriceRefreshAt || 'waiting'}` : 'OFF'}</b></span>
        <span><i>Backtest</i><b>{backtestStatus}</b></span>
        {workspaceNotice ? <span className="notice"><b>{workspaceNotice}</b></span> : null}
        <span><b>UTC+0</b></span>
        <span><b>Auto</b></span>
      </div>
    </div>
  );
}
