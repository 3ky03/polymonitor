import { type ComponentChildren } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import { emptyState, orderfilledList } from '@/panels/shared/renderers';
import { formatCompact, formatCurrencyCompact, formatPercent, formatRelative, formatSignedPercent, signedClass } from '@/panels/shared/formatters';
import { fetchMarketLobByToken } from '@/services/api';
import type {
  ChartPayload,
  L2Level,
  LobPayload,
  MarketGroupChartPayload,
  MarketGroupChartRange,
  MarketGroupDetail,
  MarketGroupOutcome,
  MarketListItem,
  PanelRenderContext,
  PriceSummary,
  TradeRow,
} from '@/types';

type BookSide = 'yes' | 'no';
type FocusedPanelSlotRenderer = (panelId: string, className: string, panel: ComponentChildren) => ComponentChildren;
type FocusedMarketStripProps = PanelRenderContext & {
  renderPanelSlot?: FocusedPanelSlotRenderer;
};

const CHART_RANGE_TABS: Array<{ label: string; value: MarketGroupChartRange }> = [
  { label: '1h', value: '1h' },
  { label: '24h', value: '1d' },
  { label: '7d', value: '1w' },
  { label: '30d', value: '1m' },
];
const FOCUS_CHART = {
  width: 520,
  height: 276,
  top: 18,
  right: 58,
  bottom: 48,
  left: 10,
};
const BOOK_LEVEL_LIMIT = 4;
const LOB_REFRESH_INTERVAL_MS = 8_000;
const LIVE_LOB_MAX_AGE_MS = 60_000;

const POLYMARKET_SERIES_COLORS = ['#7cb6ff', '#4377ff', '#f5b800', '#ff7a1a', '#7f56d9', '#12b76a', '#f04438', '#06aed4'];
type RefreshDirection = 'up' | 'down' | 'flat';
type TokenLobState = {
  key: string;
  lob: LobPayload | null;
  loading: boolean;
  updatedAt: number | null;
  pulseId: number;
  direction: RefreshDirection;
};

function marketTimeSubtitle(endDate?: string | null, createdAt?: string | null) {
  if (endDate) return `Closes ${formatRelative(endDate)}`;
  if (createdAt) return `Opened ${formatRelative(createdAt)}`;
  return 'Rolling market';
}

function resolveMarketTitle(ctx: PanelRenderContext, trade: TradeRow) {
  if (trade.marketTitle) return trade.marketTitle;
  if (trade.marketId && ctx.selectedMarket?.id === trade.marketId) return ctx.selectedMarket.title;
  return ctx.selectedMarket?.title || null;
}

function formatBookPrice(value?: string | number | null) {
  if (value == null || value === '') return '--';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return `${Math.round(numeric * 100)}c`;
}

function formatBookShares(value?: string | number | null) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return numeric.toLocaleString('en-US', { maximumFractionDigits: numeric >= 100 ? 0 : 2 });
}

function formatBookTotal(value?: number | null) {
  if (value == null || !Number.isFinite(value)) return '--';
  return `$${value.toLocaleString('en-US', { maximumFractionDigits: value >= 100 ? 0 : 2 })}`;
}

function formatRefreshAge(value?: number | null) {
  if (!value) return 'waiting';
  const seconds = Math.max(0, Math.round((Date.now() - value) / 1000));
  if (seconds < 2) return 'now';
  if (seconds < 60) return `${seconds}s ago`;
  return `${Math.floor(seconds / 60)}m ago`;
}

function timestampMillis(value?: string | null) {
  const parsed = value ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

function bookDepthTotal(levels?: L2Level[]) {
  return (levels || []).slice(0, BOOK_LEVEL_LIMIT).reduce((total, level) => {
    const price = Number(level.price);
    const size = Number(level.size);
    if (!Number.isFinite(price) || !Number.isFinite(size)) return total;
    return total + price * size;
  }, 0);
}

function hasBookLevels(lob?: LobPayload | null) {
  return Boolean(
    lob
      && ((lob.yes?.asks || []).length
        || (lob.yes?.bids || []).length
        || (lob.no?.asks || []).length
        || (lob.no?.bids || []).length),
  );
}

function hasSideBookLevels(side?: { asks?: L2Level[]; bids?: L2Level[] } | null) {
  return Boolean(side && ((side.asks || []).length || (side.bids || []).length));
}

function isTerminalProbability(value?: string | number | null) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && (numeric <= 0.02 || numeric >= 0.98);
}

function safeLiveProbability(value: string | number | null | undefined, isLive: boolean) {
  return isLive ? value : null;
}

function firstProbability(...values: Array<string | number | null | undefined>) {
  for (const value of values) {
    if (value == null || value === '') continue;
    const numeric = Number(value);
    if (Number.isFinite(numeric) && numeric >= 0 && numeric <= 1) return numeric;
  }
  return null;
}

function firstPositiveCount(...values: Array<string | number | null | undefined>) {
  for (const value of values) {
    const numeric = Number(value);
    if (Number.isFinite(numeric) && numeric > 0) return value;
  }
  return null;
}

function accumulateNotional(levels: L2Level[]) {
  const working = levels.slice(0, BOOK_LEVEL_LIMIT).map((level) => ({
    price: Number(level.price) || 0,
    size: Number(level.size) || 0,
  }));
  let running = 0;
  return working.map((level) => {
    running += level.price * level.size;
    return { ...level, cumulative: running };
  });
}

function nullableBookNumber(value?: string | number | null) {
  if (value == null || value === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

export function bookMidValue(lob?: LobPayload | null, side: BookSide = 'yes') {
  const book = side === 'no' ? lob?.no : lob?.yes;
  if (!hasSideBookLevels(book)) return null;
  const bid = nullableBookNumber(book?.bestBid);
  const ask = nullableBookNumber(book?.bestAsk);
  if (bid != null && ask != null) return (bid + ask) / 2;
  if (bid != null) return bid;
  if (ask != null) return ask;
  return null;
}

function directionFromValues(next?: number | null, previous?: number | null): RefreshDirection {
  if (next == null || previous == null || !Number.isFinite(next) || !Number.isFinite(previous)) return 'flat';
  if (next > previous + 0.0001) return 'up';
  if (next < previous - 0.0001) return 'down';
  return 'flat';
}

function orderBookRows(levels: L2Level[], tone: 'bid' | 'ask', pulseId = 0) {
  const sorted = levels
    .slice()
    .sort((left, right) => {
      const leftPrice = Number(left.price) || 0;
      const rightPrice = Number(right.price) || 0;
      return tone === 'ask' ? leftPrice - rightPrice : rightPrice - leftPrice;
    });
  const rows = accumulateNotional(sorted);
  const max = Math.max(...rows.map((row) => row.cumulative), 1);
  return rows.map((level, index) => {
    const total = level.cumulative;
    const depth = Math.min(100, (total / max) * 100);
    return (
      <div
        className={`wm-focus-book-row ${tone} ${pulseId ? 'live-updated' : ''}`}
        key={`${tone}-${index}-${level.price}-${level.size}-${pulseId}`}
        style={{ '--wm-book-row-delay': `${index * 28}ms` } as Record<string, string>}
      >
        <div className="wm-focus-book-depth-track" aria-hidden="true">
          <div className="wm-focus-book-side-fill" style={{ width: `${depth}%` }} />
        </div>
        <strong>{formatBookPrice(level.price)}</strong>
        <span>{formatBookShares(level.size)}</span>
        <em>{formatBookTotal(total)}</em>
      </div>
    );
  });
}

function bookImbalancePercent(bidTotal: number, askTotal: number) {
  const total = bidTotal + askTotal;
  if (!Number.isFinite(total) || total <= 0) return 50;
  return Math.max(0, Math.min(100, (bidTotal / total) * 100));
}

function marketLookup(markets: MarketListItem[], marketId: number | null) {
  return markets.find((market) => market.id === marketId) || null;
}

function orderbookOutcomeLabel(ctx: PanelRenderContext, side: BookSide, selectedOutcome: MarketGroupOutcome | null) {
  const title = selectedOutcome?.label || ctx.selectedMarket?.title || 'selected market';
  return `${side.toUpperCase()} · ${title}`;
}

function formatUnderlyingValue(value?: string | number | null) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return `$${numeric.toLocaleString('en-US', { maximumFractionDigits: numeric >= 1000 ? 0 : 2 })}`;
}

function compactDateLabel(value: number) {
  if (!Number.isFinite(value)) return '';
  return new Date(value).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export function compactTimeLabel(value: number, index: number, total: number, latestTickIsNow = true) {
  if (!Number.isFinite(value)) return '';
  if (latestTickIsNow && index === total - 1) return 'Now';
  return new Date(value).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

export function marketLifecycleIsClosed(...signals: Array<string | boolean | null | undefined>) {
  return signals.some((signal) => {
    if (signal === true) return true;
    if (typeof signal !== 'string') return false;
    const normalized = signal.trim().toLowerCase().replace(/[_-]+/g, ' ');
    return /\b(closed|settled|resolved|final|cancelled|expired)\b/.test(normalized)
      || normalized.includes('awaiting oracle');
  });
}

function marketLifecycleIsFinal(...signals: Array<string | boolean | null | undefined>) {
  return signals.some((signal) => {
    if (signal === true) return true;
    if (typeof signal !== 'string') return false;
    const normalized = signal.trim().toLowerCase().replace(/[_-]+/g, ' ');
    return /\b(settled|resolved|final|cancelled)\b/.test(normalized);
  });
}

function compactBlockLabel(value: number) {
  if (!Number.isFinite(value)) return '';
  return `#${Math.round(value).toLocaleString('en-US')}`;
}

function chartTimeTicks(points: Array<{ timestamp: string }>, count = 4) {
  const stamps = points
    .map((point) => new Date(point.timestamp).getTime())
    .filter((value) => Number.isFinite(value))
    .sort((a, b) => a - b);
  if (!stamps.length) return [];
  const minTs = stamps[0] ?? 0;
  const maxTs = stamps[stamps.length - 1] ?? minTs;
  if (maxTs <= minTs) return [{ ts: minTs, ratio: 0, label: compactDateLabel(minTs) }];
  return Array.from({ length: count }, (_, index) => {
    const ratio = index / Math.max(count - 1, 1);
    const ts = minTs + (maxTs - minTs) * ratio;
    return { ts, ratio, label: compactDateLabel(ts) };
  });
}

function chartBlockTicks(points: Array<{ blockNumber?: number | string | null; x?: number | string | null }>, count = 4) {
  const blocks = points
    .map((point) => Number(point.blockNumber ?? point.x))
    .filter((value) => Number.isFinite(value))
    .sort((a, b) => a - b);
  if (!blocks.length) return [];
  const minBlock = blocks[0] ?? 0;
  const maxBlock = blocks[blocks.length - 1] ?? minBlock;
  if (maxBlock <= minBlock) return [{ block: minBlock, ratio: 0, label: compactBlockLabel(minBlock) }];
  return Array.from({ length: count }, (_, index) => {
    const sourceIndex = Math.round((index / Math.max(count - 1, 1)) * Math.max(blocks.length - 1, 0));
    const block = blocks[sourceIndex] ?? minBlock;
    const ratio = (block - minBlock) / Math.max(maxBlock - minBlock, 1);
    return { block, ratio, label: compactBlockLabel(block) };
  });
}

function blockRangeLabel(points: Array<{ blockNumber?: number | string | null; x?: number | string | null }>) {
  const blocks = points
    .map((point) => Number(point.blockNumber ?? point.x))
    .filter((value) => Number.isFinite(value))
    .sort((a, b) => a - b);
  if (!blocks.length) return '';
  const first = blocks[0] ?? 0;
  const last = blocks[blocks.length - 1] ?? first;
  if (first === last) return compactBlockLabel(first);
  return `${compactBlockLabel(first)} - ${compactBlockLabel(last)}`;
}

function blockClosePlaceholder(message: string, loading = false) {
  return (
    <div className={`wm-focus-block-placeholder${loading ? ' loading' : ''}`}>
      <div>
        <span>{loading ? 'Index query' : 'Block close'}</span>
        <strong>{message}</strong>
        <em>{loading ? 'Using token-level sampled quant series.' : 'No sampled rows returned for the selected outcome.'}</em>
      </div>
      <i />
      <i />
      <i />
    </div>
  );
}

function buildLinePath(
  points: number[],
  {
    width,
    height,
    left,
    right,
    top,
    bottom,
    min,
    max,
  }: {
    width: number;
    height: number;
    left: number;
    right: number;
    top: number;
    bottom: number;
    min: number;
    max: number;
  },
) {
  const span = max - min || 1;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  return points
    .map((value, index) => {
      const x = left + (index / Math.max(points.length - 1, 1)) * plotWidth;
      const y = top + (1 - (value - min) / span) * plotHeight;
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(' ');
}

function buildBlockLinePath(
  points: Array<{ blockNumber: number; price: number }>,
  {
    width,
    height,
    left,
    right,
    top,
    bottom,
    min,
    max,
    minBlock: domainMinBlock,
    maxBlock: domainMaxBlock,
  }: {
    width: number;
    height: number;
    left: number;
    right: number;
    top: number;
    bottom: number;
    min: number;
    max: number;
    minBlock?: number;
    maxBlock?: number;
  },
) {
  const clean = points
    .filter((point) => Number.isFinite(point.blockNumber) && Number.isFinite(point.price))
    .sort((leftPoint, rightPoint) => leftPoint.blockNumber - rightPoint.blockNumber);
  if (clean.length < 2) return '';
  const minBlock = Number.isFinite(domainMinBlock) ? Number(domainMinBlock) : (clean[0]?.blockNumber ?? 0);
  const maxBlock = Number.isFinite(domainMaxBlock) ? Number(domainMaxBlock) : (clean[clean.length - 1]?.blockNumber ?? minBlock);
  const blockSpan = Math.max(maxBlock - minBlock, 1);
  const valueSpan = max - min || 1;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  return clean
    .map((point, index) => {
      const x = left + ((point.blockNumber - minBlock) / blockSpan) * plotWidth;
      const y = top + (1 - (point.price - min) / valueSpan) * plotHeight;
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(' ');
}

function buildTimedLinePath(
  points: Array<{ timestamp: string; price: number }>,
  {
    width,
    height,
    left,
    right,
    top,
    bottom,
    min,
    max,
    minTs: domainMinTs,
    maxTs: domainMaxTs,
  }: {
    width: number;
    height: number;
    left: number;
    right: number;
    top: number;
    bottom: number;
    min: number;
    max: number;
    minTs?: number;
    maxTs?: number;
  },
) {
  if (points.length < 2) return '';
  const stamped = points
    .map((point) => ({ ...point, ts: new Date(point.timestamp).getTime() }))
    .filter((point) => Number.isFinite(point.ts) && Number.isFinite(point.price))
    .sort((leftPoint, rightPoint) => leftPoint.ts - rightPoint.ts);
  if (stamped.length < 2) return '';
  const minTs = Number.isFinite(domainMinTs) ? Number(domainMinTs) : (stamped[0]?.ts ?? 0);
  const maxTs = Number.isFinite(domainMaxTs) ? Number(domainMaxTs) : (stamped[stamped.length - 1]?.ts ?? minTs);
  const tsSpan = Math.max(maxTs - minTs, 1);
  const valueSpan = max - min || 1;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  return stamped
    .map((point, index) => {
      const x = left + ((point.ts - minTs) / tsSpan) * plotWidth;
      const y = top + (1 - (point.price - min) / valueSpan) * plotHeight;
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(' ');
}

function rangeWindowMs(range?: string | null) {
  switch (String(range || '').toLowerCase()) {
    case '1h':
      return 60 * 60 * 1000;
    case '6h':
      return 6 * 60 * 60 * 1000;
    case '1d':
      return 24 * 60 * 60 * 1000;
    case '1w':
      return 7 * 24 * 60 * 60 * 1000;
    case '1m':
      return 30 * 24 * 60 * 60 * 1000;
    default:
      return null;
  }
}

function normalizeTimedPointsForRange(
  points: Array<{ timestamp: string; price: number }>,
  range?: string | null,
) {
  const clean = points
    .map((point) => ({ ...point, ts: new Date(point.timestamp).getTime() }))
    .filter((point) => Number.isFinite(point.ts) && Number.isFinite(point.price))
    .sort((leftPoint, rightPoint) => leftPoint.ts - rightPoint.ts);
  if (!clean.length) return [];
  const windowMs = rangeWindowMs(range);
  if (!windowMs) return clean.map(({ ts: _ts, ...point }) => point);
  const lastTs = clean[clean.length - 1]?.ts ?? Date.now();
  const startTs = lastTs - windowMs;
  const inWindow = clean.filter((point) => point.ts >= startTs);
  const base = inWindow.length ? inWindow : [clean[clean.length - 1]!];
  const first = base[0]!;
  const last = base[base.length - 1]!;
  const padded = [...base];
  if (first.ts > startTs) {
    padded.unshift({ ...first, timestamp: new Date(startTs).toISOString(), ts: startTs });
  }
  if (last.ts < lastTs) {
    padded.push({ ...last, timestamp: new Date(lastTs).toISOString(), ts: lastTs });
  }
  return padded.map(({ ts: _ts, ...point }) => point);
}

function buildAreaPath(
  path: string,
  { width, height, left, right, bottom }: { width: number; height: number; left: number; right: number; bottom: number },
) {
  return `${path} L ${width - right} ${height - bottom} L ${left} ${height - bottom} Z`;
}

function buildHorizontalTicks(min: number, max: number, count = 4) {
  return Array.from({ length: count }, (_, index) => {
    const ratio = index / Math.max(count - 1, 1);
    return max - (max - min) * ratio;
  });
}

function polymarketProbabilityScale(values: number[]) {
  const finite = values.filter((value) => Number.isFinite(value));
  const rawMax = finite.length ? Math.max(...finite) : 1;
  let max = 1;
  if (rawMax <= 0.15) max = 0.15;
  else if (rawMax <= 0.3) max = 0.3;
  else if (rawMax <= 0.45) max = 0.45;
  else if (rawMax <= 0.6) max = 0.6;
  else if (rawMax <= 0.75) max = 0.75;
  return { min: 0, max };
}

function focusedProbabilityScale(values: number[]) {
  const finite = values.filter((value) => Number.isFinite(value));
  if (!finite.length) return { min: 0, max: 1 };
  const rawMin = Math.min(...finite);
  const rawMax = Math.max(...finite);
  const rawSpan = rawMax - rawMin;
  const span = Math.max(rawSpan, 0.08);
  const padding = Math.max(span * 0.14, 0.0125);
  let min = Math.max(0, rawMin - padding - Math.max(0, (span - rawSpan) / 2));
  let max = Math.min(1, rawMax + padding + Math.max(0, (span - rawSpan) / 2));
  if (max - min < 0.08) {
    const center = (min + max) / 2;
    min = Math.max(0, center - 0.04);
    max = Math.min(1, center + 0.04);
  }
  return { min, max };
}

function outcomeCards(price: PriceSummary | null) {
  const yesPrice = Number(price?.latestYesPrice);
  const noPrice = Number(price?.latestNoPrice);
  const yesChange = Number(price?.change24h);
  const noChange = Number.isFinite(yesChange) ? -yesChange : NaN;

  return [
    {
      label: 'YES',
      price: Number.isFinite(yesPrice) ? yesPrice : null,
      change: Number.isFinite(yesChange) ? yesChange : null,
      tone: 'yes',
      cta: Number.isFinite(yesPrice) ? `Buy ${Math.round(yesPrice * 100)}%` : 'Buy Yes',
    },
    {
      label: 'NO',
      price: Number.isFinite(noPrice) ? noPrice : null,
      change: Number.isFinite(noChange) ? noChange : null,
      tone: 'no',
      cta: Number.isFinite(noPrice) ? `Buy ${Math.round(noPrice * 100)}%` : 'Buy No',
    },
  ];
}

function eventOutcomeCards(detail: MarketGroupDetail | null) {
  return (detail?.outcomes || []).map((outcome) => ({
    ...outcome,
    price: Number(outcome.yesPrice),
    change: Number(outcome.change24h),
  }));
}

type LegacyOutcomeCard = {
  label: string;
  price: number | null;
  change: number | null;
  tone: string;
  cta: string;
};

type EventOutcomeCard = MarketGroupOutcome & {
  price: number;
  change: number;
};

function renderEventDetailChart(
  chart: MarketGroupChartPayload | null,
  selectedOutcomeKey: string | null,
  activeRange?: string | null,
  latestTickIsNow = true,
) {
  const series = (chart?.series || []).filter((entry) => (entry.points || []).length > 1);
  if (!series.length) return emptyState('Fresh market: waiting for event price history to print.');
  const { width, height, left, right, top, bottom } = FOCUS_CHART;
  const allValues = series.flatMap((entry) => (entry.points || []).map((point) => Number(point.price)).filter((value) => Number.isFinite(value)));
  if (!allValues.length) return emptyState('No event probability history loaded yet.');
  const { min, max } = polymarketProbabilityScale(allValues);
  const span = max - min || 1;
  const plotHeight = height - top - bottom;
  const projectY = (value: number) => top + (1 - (value - min) / span) * plotHeight;
  const ticks = buildHorizontalTicks(min, max, 5);
  const selectedSeries = series.find((entry) => entry.outcomeKey === selectedOutcomeKey) || series[0];
  const cleanSeries = series
    .map((entry, index) => ({
      entry,
      color: entry.color || POLYMARKET_SERIES_COLORS[index % POLYMARKET_SERIES_COLORS.length],
      points: normalizeTimedPointsForRange(
        (entry.points || [])
          .map((point) => ({ timestamp: point.timestamp, price: Number(point.price) }))
          .filter((point) => Number.isFinite(point.price) && Boolean(point.timestamp)),
        activeRange,
      ),
    }))
    .filter((entry) => entry.points.length > 1);
  const allStamped = cleanSeries
    .flatMap((entry) => entry.points.map((point) => new Date(point.timestamp).getTime()))
    .filter((value) => Number.isFinite(value));
  const domainMinTs = allStamped.length ? Math.min(...allStamped) : undefined;
  const domainMaxTs = allStamped.length ? Math.max(...allStamped) : undefined;
  const selectedClean = cleanSeries.find((entry) => entry.entry.outcomeKey === selectedSeries?.outcomeKey) || cleanSeries[0] || null;
  const selectedPath = selectedClean
    ? buildTimedLinePath(selectedClean.points, { width, height, left, right, top, bottom, min, max, minTs: domainMinTs, maxTs: domainMaxTs })
    : '';
  const selectedColor = selectedClean?.color || selectedSeries?.color || '#4377ff';
  const lastPoint = selectedClean?.points?.[selectedClean.points.length - 1] || null;
  const lastPointX = lastPoint && selectedClean
    ? (() => {
        const stamped = selectedClean.points.map((point) => new Date(point.timestamp).getTime()).filter(Number.isFinite);
        const minTs = domainMinTs ?? stamped[0] ?? 0;
        const maxTs = domainMaxTs ?? stamped[stamped.length - 1] ?? minTs;
        return left + ((new Date(lastPoint.timestamp).getTime() - minTs) / Math.max(maxTs - minTs, 1)) * (width - left - right);
      })()
    : null;
  const timeTicks = chartTimeTicks(cleanSeries.flatMap((entry) => entry.points), 5);
  const plotWidth = width - left - right;

  return (
    <div className="wm-focus-chart-shell wm-focus-event-chart-shell wm-polymarket-prob-chart">
      <div className="wm-focus-chart-watermark" aria-hidden="true">Polymarket</div>
      <svg viewBox={`0 0 ${width} ${height}`} className="wm-focus-chart-svg" preserveAspectRatio="none">
        {ticks.map((tick) => {
          const y = projectY(tick);
          return (
            <g key={tick}>
              <line x1={left} y1={y} x2={width - right} y2={y} className="wm-focus-chart-grid h" />
              <text x={width - right + 8} y={y + 4} className="wm-focus-chart-axis-text">{`${Math.round(tick * 100)}%`}</text>
            </g>
          );
        })}
        {timeTicks.map((tick) => {
          const x = left + tick.ratio * plotWidth;
          return (
            <g key={`${tick.ts}-${tick.label}`}>
              <rect x={x - 5} y={height - 30} width="10" height="7" rx="2.5" className="wm-focus-chart-timeline-handle" />
              <text x={x} y={height - 8} className="wm-focus-chart-time-text">{compactTimeLabel(tick.ts, timeTicks.indexOf(tick), timeTicks.length, latestTickIsNow)}</text>
            </g>
          );
        })}
        <line x1={left} y1={height - 25} x2={width - right} y2={height - 25} className="wm-focus-chart-timeline-line" />
        {cleanSeries.filter(({ entry }) => entry.outcomeKey !== selectedSeries?.outcomeKey).map(({ entry, points, color }) => {
          const path = buildTimedLinePath(points, { width, height, left, right, top, bottom, min, max, minTs: domainMinTs, maxTs: domainMaxTs });
          if (!path) return null;
          const endpoint = points[points.length - 1];
          const endpointX = endpoint
            ? (() => {
                const stamped = points.map((point) => new Date(point.timestamp).getTime()).filter(Number.isFinite);
                const minTs = domainMinTs ?? stamped[0] ?? 0;
                const maxTs = domainMaxTs ?? stamped[stamped.length - 1] ?? minTs;
                return left + ((new Date(endpoint.timestamp).getTime() - minTs) / Math.max(maxTs - minTs, 1)) * plotWidth;
              })()
            : null;
          return (
            <g key={entry.outcomeKey || entry.label || path}>
              <path d={path} className="wm-focus-chart-line event-series muted" style={{ stroke: color }} />
              {endpoint && endpointX != null ? (
                <circle cx={endpointX} cy={projectY(endpoint.price)} r="3.7" className="wm-focus-chart-endpoint" style={{ fill: color }} />
              ) : null}
            </g>
          );
        })}
        {selectedPath ? (
          <path
            d={selectedPath}
            className="wm-focus-chart-line event-series selected"
            style={{ stroke: selectedColor }}
          />
        ) : null}
        {lastPoint && lastPointX != null ? (
          <circle cx={lastPointX} cy={projectY(lastPoint.price)} r="4.6" className="wm-focus-chart-endpoint selected" style={{ fill: selectedColor }} />
        ) : null}
      </svg>
    </div>
  );
}

function eventChartLegend(
  detail: MarketGroupDetail | null,
  chart: MarketGroupChartPayload | null,
  selectedOutcomeKey: string | null,
  onSelect: (outcome: MarketGroupOutcome) => void,
) {
  const outcomes = detail?.outcomes || [];
  const seriesColorMap = new Map((chart?.series || []).map((entry) => [entry.outcomeKey || '', entry.color || '#7cb6ff']));
  const visible = outcomes
    .slice()
    .sort((left, right) => Number(right.yesPrice || 0) - Number(left.yesPrice || 0))
    .slice(0, 6);
  if (!visible.length) return null;
  return (
    <div className="wm-focus-event-legend" aria-label="event outcomes legend">
      {visible.map((outcome, index) => {
        const key = outcome.outcomeKey || '';
        const active = key === selectedOutcomeKey;
        return (
          <button
            key={key || outcome.label || outcome.marketId || outcome.gammaMarketId}
            type="button"
            className={`wm-focus-event-legend-item${active ? ' active' : ''}`}
            onClick={() => onSelect(outcome)}
          >
            <span
              className="wm-focus-event-legend-dot"
              style={{ background: seriesColorMap.get(key) || POLYMARKET_SERIES_COLORS[index % POLYMARKET_SERIES_COLORS.length] }}
              aria-hidden="true"
            />
            <span className="wm-focus-event-legend-label">{outcome.label || 'Outcome'}</span>
            <strong>{formatPercent(outcome.yesPrice)}</strong>
            <em className={signedClass(outcome.change24h)}>{formatSignedPercent(outcome.change24h)}</em>
          </button>
        );
      })}
    </div>
  );
}

type DetailChartOptions = {
  title?: string | null;
  category?: string | null;
  outcomeCount?: number | string | null;
  selectedLabel?: string | null;
  latestTickIsNow?: boolean;
};

function renderDetailChart(chart: ChartPayload | null, activeRange?: string | null, _options: DetailChartOptions = {}) {
  const points = chart?.points || [];
  if (!points.length) return emptyState('No market history loaded yet.');
  const { width, height, left, right, top, bottom } = FOCUS_CHART;
  const tickLabel = (value: number) => (chart?.kind === 'underlying-price' ? formatUnderlyingValue(value) : `${Math.round(value * 100)}.0%`);

  if (chart?.kind !== 'underlying-price') {
    const isBlockAxis = String(chart?.priceSource || '').toLowerCase().includes('block_close')
      || String(chart?.interval || '').toLowerCase() === 'block'
      || points.some((point) => point.blockNumber != null || point.x != null);
    if (isBlockAxis) {
      const plotTop = 18;
      const yesBlock = points
        .map((point) => ({ blockNumber: Number(point.blockNumber ?? point.x), price: Number(point.yesPrice) }))
        .filter((point) => Number.isFinite(point.blockNumber) && Number.isFinite(point.price));
      if (yesBlock.length < 2) return emptyState('No block-close probability history loaded yet.');
      const yes = yesBlock.map((point) => point.price);
      const { min, max } = focusedProbabilityScale(yes);
      const allBlocks = yesBlock.map((point) => point.blockNumber).filter((value) => Number.isFinite(value));
      const domainMinBlock = allBlocks.length ? Math.min(...allBlocks) : undefined;
      const domainMaxBlock = allBlocks.length ? Math.max(...allBlocks) : undefined;
      const yesPath = buildBlockLinePath(yesBlock, { width, height, left, right, top: plotTop, bottom, min, max, minBlock: domainMinBlock, maxBlock: domainMaxBlock });
      const yesAreaPath = buildAreaPath(yesPath, { width, height, left, right, bottom });
      const ticks = buildHorizontalTicks(min, max, 4);
      const lastYes = yes[yes.length - 1] ?? 0;
      const span = max - min || 1;
      const plotHeight = height - plotTop - bottom;
      const projectY = (value: number) => plotTop + (1 - (value - min) / span) * plotHeight;
      const blockTicks = chartBlockTicks(points, 4);
      const axisTicks = blockTicks.map((tick, index) => ({
        ...tick,
        axisRatio: blockTicks.length > 1 ? index / (blockTicks.length - 1) : 0,
      }));
      const plotWidth = width - left - right;
      const latestY = projectY(lastYes);
      const decimalPlaces = span <= 0.12 ? 1 : 0;

      return (
        <div className="wm-focus-chart-shell wm-polymarket-prob-chart wm-block-close-chart">
          <svg
            viewBox={`0 0 ${width} ${height}`}
            className="wm-focus-chart-svg"
            preserveAspectRatio="none"
            role="img"
            aria-label={`${_options.title || 'Selected market'} YES probability by OrderFilled block close`}
          >
            <defs>
              <linearGradient id="wm-block-close-area" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#38bdf8" stopOpacity="0.24" />
                <stop offset="72%" stopColor="#38bdf8" stopOpacity="0.055" />
                <stop offset="100%" stopColor="#38bdf8" stopOpacity="0" />
              </linearGradient>
            </defs>
            {ticks.map((tick) => {
              const y = projectY(tick);
              return (
                <g key={tick}>
                  <line x1={left} y1={y} x2={width - right} y2={y} className="wm-focus-chart-grid h" />
                  <text x={width - right + 8} y={y + 4} className="wm-focus-chart-axis-text">{`${(tick * 100).toFixed(decimalPlaces)}%`}</text>
                </g>
              );
            })}
            {axisTicks.map((tick) => {
              const x = left + tick.axisRatio * plotWidth;
              return (
                <g key={`${tick.block}-${tick.label}`}>
                  <line x1={x} y1={plotTop} x2={x} y2={height - bottom} className="wm-focus-chart-grid v" />
                  <rect x={x - 5} y={height - 30} width="10" height="7" rx="2.5" className="wm-focus-chart-timeline-handle" />
                </g>
              );
            })}
            <line x1={left} y1={height - 25} x2={width - right} y2={height - 25} className="wm-focus-chart-timeline-line" />
            <line x1={left} y1={latestY} x2={width - right} y2={latestY} className="wm-focus-chart-current-guide" />
            <path d={yesAreaPath} className="wm-focus-chart-area block-close" />
            <path d={yesPath} className="wm-focus-chart-line yes" />
            <circle cx={width - right} cy={latestY} r="4.4" className="wm-focus-chart-endpoint selected" style={{ fill: '#38bdf8' }} />
            <text x={width - right + 8} y={latestY - 7} className="wm-focus-chart-latest-value">{formatPercent(lastYes)}</text>
          </svg>
          <div className="wm-focus-block-tick-axis" aria-hidden="true">
            {axisTicks.map((tick, index) => (
              <span
                key={`axis-${tick.block}-${index}`}
                className={index === 0 ? 'start' : index === blockTicks.length - 1 ? 'end' : ''}
                style={{ left: `${Math.max(0, Math.min(100, tick.axisRatio * 100))}%` }}
              >
                <i />
                <b>{tick.label}</b>
              </span>
            ))}
          </div>
        </div>
      );
    }
    const yesTimed = normalizeTimedPointsForRange(
      points
        .map((point) => ({ timestamp: String(point.timestamp || ''), price: Number(point.yesPrice) }))
        .filter((point) => Number.isFinite(point.price) && Boolean(point.timestamp)),
      activeRange || chart?.range,
    );
    const yes = yesTimed.map((point) => point.price);
    if (yes.length < 2) return emptyState('No probability history loaded yet.');
    const { min, max } = focusedProbabilityScale(yes);
    const allStamped = yesTimed
      .map((point) => new Date(point.timestamp).getTime())
      .filter((value) => Number.isFinite(value));
    const domainMinTs = allStamped.length ? Math.min(...allStamped) : undefined;
    const domainMaxTs = allStamped.length ? Math.max(...allStamped) : undefined;
    const yesPath = buildTimedLinePath(yesTimed, { width, height, left, right, top, bottom, min, max, minTs: domainMinTs, maxTs: domainMaxTs });
    const yesAreaPath = buildAreaPath(yesPath, { width, height, left, right, bottom });
    const ticks = buildHorizontalTicks(min, max, 4);
    const lastYes = yes[yes.length - 1] ?? 0;
    const span = max - min || 1;
    const plotHeight = height - top - bottom;
    const projectY = (value: number) => top + (1 - (value - min) / span) * plotHeight;
    const timeTicks = chartTimeTicks(yesTimed.map((point) => ({ timestamp: String(point.timestamp || '') })), 5);
    const plotWidth = width - left - right;
    const latestY = projectY(lastYes);
    const decimalPlaces = span <= 0.12 ? 1 : 0;

    return (
      <div className="wm-focus-chart-shell wm-polymarket-prob-chart">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="wm-focus-chart-svg"
          preserveAspectRatio="none"
          role="img"
          aria-label={`${_options.title || 'Selected market'} YES probability history`}
        >
          <defs>
            <linearGradient id="wm-prob-history-area" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#38bdf8" stopOpacity="0.24" />
              <stop offset="72%" stopColor="#38bdf8" stopOpacity="0.055" />
              <stop offset="100%" stopColor="#38bdf8" stopOpacity="0" />
            </linearGradient>
          </defs>
          {ticks.map((tick) => {
            const y = projectY(tick);
            return (
              <g key={tick}>
                <line x1={left} y1={y} x2={width - right} y2={y} className="wm-focus-chart-grid h" />
                <text x={width - right + 8} y={y + 4} className="wm-focus-chart-axis-text">{`${(tick * 100).toFixed(decimalPlaces)}%`}</text>
              </g>
            );
          })}
          {timeTicks.map((tick) => {
            const x = left + tick.ratio * plotWidth;
            return (
              <g key={`${tick.ts}-${tick.label}`}>
                <rect x={x - 5} y={height - 30} width="10" height="7" rx="2.5" className="wm-focus-chart-timeline-handle" />
                <text x={x} y={height - 8} className="wm-focus-chart-time-text">{compactTimeLabel(tick.ts, timeTicks.indexOf(tick), timeTicks.length, _options.latestTickIsNow !== false)}</text>
              </g>
            );
          })}
          <line x1={left} y1={height - 25} x2={width - right} y2={height - 25} className="wm-focus-chart-timeline-line" />
          <line x1={left} y1={latestY} x2={width - right} y2={latestY} className="wm-focus-chart-current-guide" />
          <path d={yesAreaPath} className="wm-focus-chart-area probability-history" />
          <path d={yesPath} className="wm-focus-chart-line yes" />
          <circle cx={width - right} cy={latestY} r="4.4" className="wm-focus-chart-endpoint selected" style={{ fill: '#38bdf8' }} />
          <text x={width - right + 8} y={latestY - 7} className="wm-focus-chart-latest-value">{formatPercent(lastYes)}</text>
        </svg>
      </div>
    );
  }

  const clean = points
    .map((point, index) => ({ index, value: Number(point.value), timestamp: point.timestamp }))
    .filter((point) => Number.isFinite(point.value));

  if (clean.length < 2) return emptyState('No underlying price history loaded yet.');

  const target = Number(chart.targetPrice);
  const rawMin = Math.min(...clean.map((point) => point.value));
  const rawMax = Math.max(...clean.map((point) => point.value));
  const rawSpan = rawMax - rawMin || Math.max(rawMax * 0.01, 1);
  const padding = rawSpan * 0.18;
  const min = rawMin - padding;
  const max = rawMax + padding;
  const span = max - min || 1;
  const plotHeight = height - top - bottom;
  const plotWidth = width - left - right;
  const projectY = (value: number) => top + (1 - (value - min) / span) * plotHeight;
  const path = buildLinePath(clean.map((point) => point.value), { width, height, left, right, top, bottom, min, max });
  const areaPath = buildAreaPath(path, { width, height, left, right, bottom });
  const last = clean[clean.length - 1];
  if (!last) return emptyState('No underlying price history loaded yet.');
  const targetInRange = Number.isFinite(target) && target >= min && target <= max;
  const targetY = targetInRange ? projectY(target) : null;
  const ticks = buildHorizontalTicks(min, max, 4);

  return (
    <div className="wm-focus-chart-shell wm-line-chart-underlying">
      <svg viewBox={`0 0 ${width} ${height}`} className="wm-focus-chart-svg" preserveAspectRatio="none">
        <defs>
          <linearGradient id="wmUnderlyingArea" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor="rgba(255, 152, 0, 0.26)" />
            <stop offset="100%" stopColor="rgba(255, 152, 0, 0.02)" />
          </linearGradient>
        </defs>
        {ticks.map((tick) => {
          const y = projectY(tick);
          return (
            <g key={tick}>
              <line x1={left} y1={y} x2={width - right} y2={y} className="wm-focus-chart-grid h" />
              <text x={width - right + 8} y={y + 4} className="wm-focus-chart-axis-text">{tickLabel(tick)}</text>
            </g>
          );
        })}
        {Array.from({ length: 4 }, (_, index) => {
          const x = left + (plotWidth / 3) * index;
          return <line key={index} x1={x} y1={top} x2={x} y2={height - bottom} className="wm-focus-chart-grid v" />;
        })}
        {targetY !== null ? <line x1="0" y1={targetY} x2={width} y2={targetY} className="wm-focus-target-line" /> : null}
        <path d={areaPath} fill="url(#wmUnderlyingArea)" />
        <path d={path} className="wm-focus-chart-line underlying" />
        <circle cx={left + (last.index / Math.max(clean.length - 1, 1)) * plotWidth} cy={projectY(last.value)} r="4.2" fill="#ff9a1f" />
      </svg>
    </div>
  );
}

function chartSourceLabel(source?: string | null, status?: string | null) {
  const normalizedSource = String(source || '').toLowerCase();
  const normalizedStatus = String(status || '').toLowerCase();
  if (normalizedStatus === 'snapshot') return 'Snapshot only';
  if (normalizedStatus === 'flat') return 'Flat quote';
  if (normalizedSource.includes('block_close')) return 'OrderFilled block close';
  if (normalizedSource.includes('orderfilled')) return 'OrderFilled history';
  if (normalizedSource.includes('trade-history')) return 'Trade history';
  if (normalizedSource.includes('clob')) return 'CLOB quote history';
  if (normalizedSource.includes('serving')) return 'DB price history';
  return normalizedStatus ? `History ${normalizedStatus}` : 'History';
}

export function FocusedMarketStrip(props: FocusedMarketStripProps) {
  const { renderPanelSlot, ...ctx } = props;
  const focusedMarket = (
    ctx.bundle?.market?.id != null && ctx.selectedMarketId != null && Number(ctx.bundle.market.id) === Number(ctx.selectedMarketId)
      ? ctx.bundle.market
      : ctx.selectedMarket
  );
  const selectedGroup = ctx.bundle?.group || ctx.selectedMarketGroup;
  const detail = ctx.selectedMarketGroupDetail || ctx.bundle?.group || null;
  const eventChart = ctx.selectedMarketGroupChart;
  const selectedOutcome = (
    (ctx.bundle?.selectedOutcome && ctx.selectedMarketId != null && Number(ctx.bundle.selectedOutcome.marketId) === ctx.selectedMarketId ? ctx.bundle.selectedOutcome : null)
    || (detail?.outcomes || []).find((outcome) => ctx.selectedMarketId != null && Number(outcome.marketId) === ctx.selectedMarketId)
    || (selectedGroup?.outcomes || []).find((outcome) => ctx.selectedMarketId != null && Number(outcome.marketId) === ctx.selectedMarketId)
    || (detail?.outcomes || []).find((outcome) => outcome.outcomeKey === ctx.selectedMarketGroupOutcomeKey)
    || (selectedGroup?.outcomes || []).find((outcome) => outcome.outcomeKey === ctx.selectedMarketGroupOutcomeKey)
    || null
  );
  const bundleMarketId = ctx.bundle?.market?.id ?? ctx.bundle?.identity?.localMarketId ?? ctx.bundle?.identity?.marketId ?? ctx.bundle?.price?.marketId ?? ctx.bundle?.chart?.marketId ?? ctx.bundle?.lob?.marketId ?? null;
  const bundleMatchesSelected = ctx.selectedMarketId != null && bundleMarketId != null && Number(bundleMarketId) === Number(ctx.selectedMarketId);
  const marketStats = marketLookup(ctx.markets, ctx.selectedMarketId);
  const marketIsClosed = marketLifecycleIsClosed(
    bundleMatchesSelected ? ctx.bundle?.oracle?.isTradingClosed : false,
    bundleMatchesSelected ? ctx.bundle?.oracle?.currentStatus : null,
    bundleMatchesSelected ? ctx.bundle?.oracle?.completionStatus : null,
    focusedMarket?.status,
    detail?.status,
    marketStats?.status,
  );
  const marketIsFinal = marketLifecycleIsFinal(
    bundleMatchesSelected ? ctx.bundle?.oracle?.isFinal : false,
    bundleMatchesSelected ? ctx.bundle?.oracle?.isResolved : false,
    bundleMatchesSelected ? ctx.bundle?.oracle?.completionStatus : null,
    focusedMarket?.status,
    detail?.status,
    marketStats?.status,
  );
  const marketIsAwaitingOracle = marketIsClosed && !marketIsFinal;
  const selectedOutcomeMatches = selectedOutcome?.marketId != null && Number(selectedOutcome.marketId) === ctx.selectedMarketId;
  const activeOutcomeKey = selectedOutcome?.outcomeKey || ctx.selectedMarketGroupOutcomeKey || null;
  const selectedTokenId = String(
    selectedOutcome?.yesTokenId
      || focusedMarket?.yesTokenId
      || ctx.bundle?.market?.yesTokenId
      || '',
  ).trim();
  const selectedNoTokenId = String(
    selectedOutcome?.noTokenId
      || focusedMarket?.noTokenId
      || ctx.bundle?.market?.noTokenId
      || '',
  ).trim();
  const selectedTokenKey = selectedTokenId ? `${selectedTokenId}:${selectedNoTokenId}` : '';
  const [refreshClock, setRefreshClock] = useState(0);
  const tokenLobRequestRef = useRef(0);
  const [tokenLobState, setTokenLobState] = useState<TokenLobState>({
    key: '',
    lob: null,
    loading: false,
    updatedAt: null,
    pulseId: 0,
    direction: 'flat',
  });
  void refreshClock;
  const tokenLob = !marketIsClosed && tokenLobState.key === selectedTokenKey ? tokenLobState.lob : null;
  const tokenLobLoading = !marketIsClosed && tokenLobState.key === selectedTokenKey && tokenLobState.loading;
  const executionAvailable = bundleMatchesSelected || selectedOutcomeMatches || Boolean(selectedTokenId) || Boolean(ctx.selectedMarketId && !detail);
  const price = executionAvailable && bundleMatchesSelected ? (ctx.bundle?.price ?? null) : null;
  const lob = executionAvailable && !marketIsClosed ? (tokenLob || (bundleMatchesSelected ? ctx.bundle?.lob : null)) : null;
  const trades = executionAvailable && bundleMatchesSelected ? (ctx.bundle?.trades || []) : [];
  const chart = bundleMatchesSelected ? (ctx.bundle?.chart || null) : null;
  const [bookSide, setBookSide] = useState<BookSide>('yes');
  const activeBook = bookSide === 'no' ? lob?.no : lob?.yes;
  const activePrice = bookSide === 'no'
    ? (price?.latestNoPrice ?? price?.latestPrice)
    : (price?.latestYesPrice ?? price?.latestPrice);
  const spreadValue = activeBook?.spread;
  const askLevels = activeBook?.asks || [];
  const bidLevels = activeBook?.bids || [];
  const askDepthTotal = bookDepthTotal(askLevels);
  const bidDepthTotal = bookDepthTotal(bidLevels);
  const bidImbalance = bookImbalancePercent(bidDepthTotal, askDepthTotal);
  const hasAnyBookLevels = hasBookLevels(lob);
  const hasBundledBookLevels = bundleMatchesSelected && hasBookLevels(ctx.bundle?.lob);
  const hasActiveBookLevels = hasSideBookLevels(activeBook);
  const eventOutcomes = detail ? eventOutcomeCards(detail) : [];
  const legacyOutcomes = detail ? [] : (outcomeCards(price) as LegacyOutcomeCard[]);
  const shouldShowOutcomeRail = detail
    ? (detail.outcomes || []).length > 1
    : ((selectedGroup?.outcomes || []).length > 1 || Number(marketStats?.outcomeCount || 2) > 2);
  const lobObservedAt = timestampMillis(lob?.fetchedAt) ?? (tokenLob ? tokenLobState.updatedAt : null);
  const lobAgeMs = lobObservedAt == null ? null : Date.now() - lobObservedAt;
  const lobIsFresh = Boolean(
    lobAgeMs != null
      && lobAgeMs >= -30_000
      && lobAgeMs <= LIVE_LOB_MAX_AGE_MS,
  );
  const lobYesPrice = lobIsFresh ? bookMidValue(lob, 'yes') : null;
  const lobNoPrice = lobIsFresh ? bookMidValue(lob, 'no') : null;
  const servingYesPrice = firstProbability(price?.latestYesPrice, price?.latestPrice);
  const catalogYesPrice = firstProbability(
    selectedOutcome?.yesPrice,
    focusedMarket?.latestYesPrice,
    focusedMarket?.latestPrice,
  );
  const displayedYesPrice = firstProbability(lobYesPrice, servingYesPrice, catalogYesPrice);
  const displayedNoPrice = firstProbability(
    lobNoPrice,
    price?.latestNoPrice,
    selectedOutcome?.noPrice,
    displayedYesPrice == null ? null : 1 - displayedYesPrice,
  );
  const displayedChange = selectedOutcome?.change24h ?? price?.change24h;
  const displayedVolume = selectedOutcome?.volume24h ?? price?.volume24h ?? marketStats?.volume24h;
  const displayedTrades = firstPositiveCount(
    selectedOutcome?.tradeCount24h,
    price?.tradeCount24h,
    marketStats?.tradeCount24h,
  );
  const chartStatus = String(
    chart?.historyStatus
      || (chart?.range === 'snapshot' || chart?.interval === 'snapshot' ? 'snapshot' : ''),
  ).toLowerCase();
  const chartSource = String(chart?.priceSource || '').toLowerCase();
  const isBlockCloseChart = Boolean(chartSource.includes('block_close') || String(chart?.interval || '').toLowerCase() === 'block');
  const isBlockCloseView = isBlockCloseChart;
  const eventChartStatus = String(eventChart?.historyStatus || '').toLowerCase();
  const eventChartSource = String(eventChart?.priceSource || '').toLowerCase();
  const chartPointCount = chart?.points?.length || 0;
  const chartRenderable = Boolean(
    chart
      && chartPointCount >= 2
      && chartStatus !== 'missing',
  );
  const displayChart = chartRenderable ? chart : null;
  const chartPoints = displayChart?.points || [];
  const chartBlockRangeText = isBlockCloseChart ? blockRangeLabel(chartPoints) : '';
  const focusedMarketDistinctPrices = new Set(
    chartPoints
      .map((point) => Number(point.yesPrice))
      .filter((value) => Number.isFinite(value))
      .map((value) => value.toFixed(6)),
  );
  const focusedMarketDistinctTimes = new Set(
    chartPoints
      .map((point) => String(point.timestamp || ''))
      .filter(Boolean),
  );
  const hasEventHistory = Boolean((eventChart?.series || []).some((entry) => (entry.points || []).length > 1));
  const hasSelectedEventHistory = Boolean((eventChart?.series || []).some((entry) => (
    (!activeOutcomeKey || entry.outcomeKey === activeOutcomeKey)
      && (entry.points || []).length > 1
  )));
  const hasFocusedMarketHistory = Boolean(
    chartPoints.length > 1
      && chartStatus !== 'missing'
      && chartStatus !== 'snapshot'
  );
  const hasFocusedMarketCurve = Boolean(
    chartPoints.length > 2
      && focusedMarketDistinctTimes.size > 1
      && focusedMarketDistinctPrices.size > 1
      && chartStatus !== 'missing'
      && chartStatus !== 'snapshot',
  );
  const focusedMarketSourceIsHistorical = Boolean(
    chartSource.includes('orderfilled')
      || chartSource.includes('trade-history')
      || chartSource.includes('serving-history')
      || chartSource.includes('clob'),
  );
  const canRenderFocusedHistory = hasFocusedMarketCurve || (hasFocusedMarketHistory && focusedMarketSourceIsHistorical);
  const preferEventChart = Boolean(
    detail
      && shouldShowOutcomeRail
      && hasSelectedEventHistory
      && !canRenderFocusedHistory
      && !isBlockCloseView,
  );
  const showFocusedEventLegend = preferEventChart;
  const showFocusedOutcomeRail = preferEventChart && shouldShowOutcomeRail;
  const chartSourceText = preferEventChart || (!canRenderFocusedHistory && hasEventHistory)
    ? chartSourceLabel(eventChartSource || 'clob-history', eventChartStatus)
    : canRenderFocusedHistory || hasFocusedMarketHistory
      ? chartSourceLabel(chartSource, chartStatus)
      : chartSourceLabel(chartSource, chartStatus);
  const hasServingTradeActivity = Number(displayedTrades || 0) > 0 || Number(displayedVolume || 0) > 0;
  const focusedHistoryEmptyText = isBlockCloseView
    ? 'No local OrderFilled block-close history is available for this market yet.'
    : chartStatus === 'snapshot'
      ? 'Only a latest-price snapshot is available for this market.'
      : hasServingTradeActivity
        ? 'Price history is still loading for this market.'
        : 'No local price history is available for this market.';
  const focusedHistoryFallback = isBlockCloseView
    ? blockClosePlaceholder(focusedHistoryEmptyText)
    : emptyState(focusedHistoryEmptyText);
  const hasLiveExecutionQuote = !marketIsClosed && (lobYesPrice != null || servingYesPrice != null);
  const suppressTerminalSnapshot = Boolean(
    (marketIsAwaitingOracle && isTerminalProbability(displayedYesPrice))
      || (
        isTerminalProbability(displayedYesPrice)
        && !hasLiveExecutionQuote
        && !hasServingTradeActivity
      )
  );
  const liveDisplayedYesPrice = safeLiveProbability(displayedYesPrice, !suppressTerminalSnapshot);
  const liveDisplayedNoPrice = safeLiveProbability(displayedNoPrice, !suppressTerminalSnapshot);
  const quoteDirection = lobYesPrice != null ? tokenLobState.direction : 'flat';
  const quotePulseId = lobYesPrice != null ? tokenLobState.pulseId : 0;
  const quoteSourceLabel = marketIsClosed
    ? suppressTerminalSnapshot
      ? 'Closed · awaiting settlement evidence'
      : displayedYesPrice != null
        ? 'Closed · last recorded quote'
        : 'Closed · no live quote'
    : lobYesPrice != null
      ? `CLOB midpoint · ${formatRefreshAge(lobObservedAt)}`
      : servingYesPrice != null
        ? 'Latest serving quote'
        : catalogYesPrice != null
          ? 'Catalog quote'
          : 'No live quote';
  const activePriceForBook = hasActiveBookLevels ? activePrice : null;
  const noTradesYet = executionAvailable && trades.length === 0 && !hasServingTradeActivity;
  const eventCategory = detail?.category || selectedGroup?.category || focusedMarket?.category || marketStats?.category || 'market';
  const outcomeCount = detail?.outcomeCount ?? selectedGroup?.outcomeCount ?? detail?.outcomes?.length ?? marketStats?.outcomeCount ?? null;
  const marketTitle = selectedOutcome?.title
    || selectedOutcome?.label
    || focusedMarket?.title
    || detail?.title
    || selectedGroup?.title
    || 'No market selected.';
  const selectedOutcomeLabel = selectedOutcome?.label || focusedMarket?.title || 'Selected outcome';
  const detailChartOptions: DetailChartOptions = {
    title: marketTitle,
    category: eventCategory,
    outcomeCount,
    selectedLabel: selectedOutcome?.label || 'YES',
    latestTickIsNow: !marketIsClosed,
  };
  const lobSyncLabel = tokenLobLoading
    ? 'SYNC'
    : hasAnyBookLevels && lobIsFresh
      ? 'LIVE'
      : hasAnyBookLevels
        ? 'STALE'
      : 'WAIT';
  const lobAgeLabel = lobObservedAt ? formatRefreshAge(lobObservedAt) : 'waiting';
  const wrapPanel = (panelId: string, className: string, panel: ComponentChildren) => (
    renderPanelSlot ? renderPanelSlot(panelId, className, panel) : panel
  );

  useEffect(() => {
    if (marketIsClosed || !selectedTokenId) {
      setTokenLobState((current) => (
        current.loading || current.lob
          ? { key: '', lob: null, loading: false, updatedAt: null, pulseId: 0, direction: 'flat' }
          : current
      ));
      return;
    }
    let cancelled = false;
    let timer: number | undefined;
    const controller = new AbortController();
    const key = selectedTokenKey;
    const title = selectedOutcome?.label || detail?.title || '';
    const bundledLob = bundleMatchesSelected && hasBookLevels(ctx.bundle?.lob) ? ctx.bundle?.lob || null : null;

    if (bundledLob) {
      setTokenLobState((current) => ({
        key,
        lob: bundledLob,
        loading: false,
        updatedAt: timestampMillis(bundledLob.fetchedAt) ?? Date.now(),
        pulseId: current.key === key ? current.pulseId : current.pulseId + 1,
        direction: current.key === key ? current.direction : 'flat',
      }));
    }

    const loadBook = () => {
      const requestSeq = ++tokenLobRequestRef.current;
      setTokenLobState((current) => ({
        key,
        lob: current.key === key ? current.lob : null,
        loading: true,
        updatedAt: current.key === key ? current.updatedAt : null,
        pulseId: current.key === key ? current.pulseId : 0,
        direction: current.key === key ? current.direction : 'flat',
      }));
      fetchMarketLobByToken(selectedTokenId, title, selectedNoTokenId, 6500, controller.signal, ctx.selectedMarketId)
        .then((lobPayload) => {
          if (cancelled || requestSeq !== tokenLobRequestRef.current) return;
          setTokenLobState((current) => {
            const previousMid = current.key === key ? bookMidValue(current.lob, bookSide) : null;
            const nextMid = bookMidValue(lobPayload, bookSide);
            return {
              key,
              lob: lobPayload,
              loading: false,
              updatedAt: timestampMillis(lobPayload.fetchedAt) ?? Date.now(),
              pulseId: (current.key === key ? current.pulseId : 0) + 1,
              direction: directionFromValues(nextMid, previousMid),
            };
          });
        })
        .catch(() => {
          if (!cancelled) {
            setTokenLobState((current) => ({
              key,
              lob: current.key === key ? current.lob : null,
              loading: false,
              updatedAt: current.key === key ? current.updatedAt : null,
              pulseId: current.key === key ? current.pulseId : 0,
              direction: current.key === key ? current.direction : 'flat',
            }));
          }
        })
        .finally(() => {
          if (!cancelled) {
            timer = window.setTimeout(() => {
              if (document.visibilityState === 'hidden') {
                timer = window.setTimeout(loadBook, LOB_REFRESH_INTERVAL_MS);
                return;
              }
              loadBook();
            }, LOB_REFRESH_INTERVAL_MS);
          }
        });
    };

    timer = window.setTimeout(loadBook, hasBundledBookLevels ? 4_000 : 350);
    return () => {
      cancelled = true;
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [bookSide, detail?.title, hasBundledBookLevels, marketIsClosed, selectedNoTokenId, selectedOutcome?.label, selectedTokenId, selectedTokenKey]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      setRefreshClock((value) => value + 1);
    }, 2000);
    return () => {
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    setBookSide('yes');
  }, [ctx.selectedMarketId, activeOutcomeKey]);

  useEffect(() => {
    if (!lob) return;
    const yesHasLevels = hasSideBookLevels(lob.yes);
    const noHasLevels = hasSideBookLevels(lob.no);
    if (bookSide === 'yes' && !yesHasLevels && noHasLevels) setBookSide('no');
    if (bookSide === 'no' && !noHasLevels && yesHasLevels) setBookSide('yes');
  }, [bookSide, lob]);

  return (
    <section className="wm-focus-strip">
      {wrapPanel('price-chart', 'wm-focus-panel-slot wm-focus-detail-slot', (
        <Panel
          title="Market Detail"
          badge={marketIsAwaitingOracle ? 'Awaiting Oracle' : marketIsClosed ? 'Closed' : 'Selected'}
          status={marketIsClosed ? 'muted' : 'live'}
          className="wm-focus-panel wm-focus-detail-panel"
        >
          <div className={`wm-focus-detail tick-${quoteDirection}`}>
            <div className="wm-focus-event-head">
              <div className="wm-focus-detail-copy">
                <strong className="wm-focus-title">{marketTitle}</strong>
                <div className="wm-focus-kicker wm-focus-market-meta-line">
                  <span>{eventCategory}</span>
                  <i>{outcomeCount ? `${outcomeCount} outcomes` : 'event'}</i>
                  <i>{isBlockCloseView ? 'Vol' : '24h Vol'} {formatCurrencyCompact(displayedVolume)}</i>
                  {displayedTrades != null
                    ? <i>{formatCompact(displayedTrades)} trades</i>
                    : Number(displayedVolume) > 0
                      ? <i>24h activity</i>
                      : null}
                </div>
              </div>
              <div className="wm-focus-price-hero">
                <strong
                  key={`price-${quotePulseId}`}
                  className={`wm-live-price-tick ${quoteDirection}`}
                  aria-live="polite"
                  aria-label={`YES probability ${formatPercent(liveDisplayedYesPrice)}`}
                >
                  {formatPercent(liveDisplayedYesPrice)}
                </strong>
                <span className={suppressTerminalSnapshot ? 'flat' : signedClass(displayedChange)}>
                  {suppressTerminalSnapshot ? 'No live quote' : formatSignedPercent(displayedChange)}
                </span>
                <small className="wm-focus-quote-source">{quoteSourceLabel}</small>
                {ctx.selectedMarketId ? (
                  <a className="wm-focus-dossier-link" href={`/markets/${ctx.selectedMarketId}`}>
                    Open dossier ↗
                  </a>
                ) : null}
              </div>
            </div>

            <div className="wm-focus-chart-card">
              <div className="wm-focus-price-strip" aria-label="selected market quote">
                <span><em>YES</em> <strong>{formatBookPrice(liveDisplayedYesPrice)}</strong></span>
                <span><em>NO</em> <strong>{formatBookPrice(liveDisplayedNoPrice)}</strong></span>
                <span><em>Spread</em> <strong>{formatBookPrice(spreadValue)}</strong></span>
              </div>
              <div className="wm-focus-chart-topline">
                {isBlockCloseView ? (
                  <div className="wm-focus-chart-block-axis" aria-label="block close range">
                    <span>OrderFilled close · selected YES</span>
                    {chartBlockRangeText ? <strong>{chartBlockRangeText}</strong> : null}
                  </div>
                ) : (
                  <div className="wm-focus-chart-tabs" aria-label="chart range">
                    <button type="button" className="ghost">Past</button>
                    {CHART_RANGE_TABS.map((tab) => (
                      <button
                        type="button"
                        key={tab.value}
                        className={ctx.selectedMarketGroupChartRange === tab.value ? 'active' : ''}
                        onClick={() => ctx.setSelectedMarketGroupChartRange(tab.value)}
                      >
                        {tab.label}
                      </button>
                    ))}
                    <i>UTC</i>
                  </div>
                )}
                <div className="wm-focus-chart-summary">
                  {!isBlockCloseView ? <span>{chartSourceText}</span> : null}
                  <span>{marketTimeSubtitle(
                    detail?.endDate || selectedGroup?.endDate || focusedMarket?.endDate || null,
                    detail?.createdAt || selectedGroup?.createdAt || focusedMarket?.createdAt || marketStats?.createdAt || null,
                  )}</span>
                </div>
              </div>
              <div className={`wm-focus-detail-grid${showFocusedOutcomeRail ? '' : ' compact'}`}>
                <div className="wm-focus-chart-wrap">
                  {detail
                    ? (
                        preferEventChart
                            ? renderEventDetailChart(eventChart, activeOutcomeKey, ctx.selectedMarketGroupChartRange, !marketIsClosed)
                            : canRenderFocusedHistory
                              ? renderDetailChart(displayChart, ctx.selectedMarketGroupChartRange, detailChartOptions)
                            : hasEventHistory && !isBlockCloseView
                              ? renderEventDetailChart(eventChart, activeOutcomeKey, ctx.selectedMarketGroupChartRange, !marketIsClosed)
                              : focusedHistoryFallback
                      )
                    : (canRenderFocusedHistory ? renderDetailChart(displayChart, ctx.selectedMarketGroupChartRange, detailChartOptions) : focusedHistoryFallback)}
                </div>
                {showFocusedEventLegend ? eventChartLegend(detail, eventChart, activeOutcomeKey, (outcome) => {
                  ctx.setSelectedMarketGroupOutcomeKey(outcome.outcomeKey || null);
                  if (outcome.marketId != null) {
                    ctx.setSelectedMarketId(Number(outcome.marketId));
                  } else {
                    ctx.setSelectedMarketId(null);
                  }
                }) : null}
                {showFocusedOutcomeRail ? (
                  <aside className="wm-focus-outcome-rail" aria-label="outcomes">
                    {detail
                      ? eventOutcomes.map((outcome: EventOutcomeCard) => (
                          <button
                            type="button"
                            className={`wm-focus-outcome-card event ${outcome.outcomeKey === activeOutcomeKey ? 'active' : ''} ${outcome.marketId == null ? 'pending' : ''}`}
                            key={outcome.outcomeKey || outcome.label || outcome.gammaMarketId || outcome.marketId}
                            onClick={() => {
                              ctx.setSelectedMarketGroupOutcomeKey(outcome.outcomeKey || null);
                              if (outcome.marketId != null) {
                                ctx.setSelectedMarketId(Number(outcome.marketId));
                              } else {
                                ctx.setSelectedMarketId(null);
                              }
                            }}
                          >
                            <div className="wm-focus-outcome-top">
                              <span>{outcome.label}</span>
                              <strong>{Number.isFinite(outcome.price) ? formatPercent(outcome.price) : '--'}</strong>
                            </div>
                            <div className={`wm-focus-outcome-change ${signedClass(outcome.change)}`}>
                              {Number.isFinite(outcome.change) ? formatSignedPercent(outcome.change) : '--'}
                            </div>
                            <div className="wm-focus-outcome-cta">
                              {outcome.marketId != null ? `Focus ${outcome.label}` : 'Pending local sync'}
                            </div>
                          </button>
                        ))
                      : legacyOutcomes.map((outcome) => (
                          <button type="button" className={`wm-focus-outcome-card ${outcome.tone}`} key={outcome.label}>
                            <div className="wm-focus-outcome-top">
                              <span>{outcome.label}</span>
                              <strong>{outcome.price == null ? '--' : formatPercent(outcome.price)}</strong>
                            </div>
                            <div className={`wm-focus-outcome-change ${signedClass(outcome.change)}`}>
                              {outcome.change == null ? '--' : formatSignedPercent(outcome.change)}
                            </div>
                            <div className="wm-focus-outcome-cta">{outcome.cta}</div>
                          </button>
                        ))}
                  </aside>
                ) : null}
              </div>
            </div>

            <div className="wm-focus-inline-stats">
              <span><em>Vol</em> {formatCurrencyCompact(displayedVolume)}</span>
              <span>
                <em>{isBlockCloseView ? 'Trades' : '24h trades'}</em>
                {' '}{displayedTrades != null ? `${formatCompact(displayedTrades)} trades` : '--'}
              </span>
              <span><em>Yes</em> {formatPercent(liveDisplayedYesPrice)}</span>
              <span><em>No</em> {formatPercent(liveDisplayedNoPrice)}</span>
              <span><em>Quote</em> {quoteSourceLabel}</span>
            </div>
          </div>
        </Panel>
      ))}

      {wrapPanel('lob-depth', 'wm-focus-panel-slot wm-focus-book-slot', (
        <Panel
          title="Order Book"
          badge={marketIsClosed ? 'Closed' : hasAnyBookLevels ? (tokenLobLoading ? 'Refreshing' : 'Live') : tokenLobLoading ? 'Loading' : 'Stale'}
          status={marketIsClosed ? 'muted' : 'live'}
          className="wm-focus-panel wm-focus-book-panel"
          controls={(selectedOutcome || focusedMarket) ? <span className="wm-focus-header-note">{orderbookOutcomeLabel(ctx, bookSide, selectedOutcome)}</span> : undefined}
        >
          {marketIsClosed ? (
            emptyState('Trading is closed. Live CLOB levels are cleared; historical price and OrderFilled evidence remain available.')
          ) : !executionAvailable && detail ? (
            emptyState('Select an outcome with CLOB token data to inspect the order book.')
          ) : tokenLobLoading && !lob ? (
            emptyState('Loading live CLOB order book.')
          ) : !lob || !activeBook ? (
            emptyState('No CLOB order book snapshot is available for this market.')
          ) : !hasAnyBookLevels ? (
            emptyState('No live CLOB order book is available. This market may be newly listed, paused, closed, or not yet indexed locally.')
          ) : (
            <div className={`wm-focus-book ${tokenLobLoading ? 'is-refreshing' : ''} tick-${tokenLobState.direction}`} key={`book-${bookSide}`}>
              <div className="wm-focus-book-topbar">
                <div className="wm-focus-book-tabs" aria-label="order book outcome">
                  <button type="button" className={bookSide === 'yes' ? 'active' : ''} onClick={() => setBookSide('yes')}>YES</button>
                  <button type="button" className={bookSide === 'no' ? 'active' : ''} onClick={() => setBookSide('no')}>NO</button>
                </div>
                <div className="wm-focus-book-market">
                  <span>{selectedOutcomeLabel}</span>
                  <strong key={`lob-sync-${tokenLobState.pulseId}`} className={tokenLobLoading ? 'syncing' : 'live'}>{lobSyncLabel}</strong>
                  <em>{lobAgeLabel}</em>
                </div>
              </div>
              <div className="wm-focus-book-quote-strip">
                <span>Best Bid <strong className="bid">{formatBookPrice(activeBook?.bestBid)}</strong></span>
                <span>Mid <strong>{formatBookPrice(activePriceForBook)}</strong></span>
                <span>Best Ask <strong className="ask">{formatBookPrice(activeBook?.bestAsk)}</strong></span>
              </div>
              <div className="wm-focus-book-depth-strip">
                <span>Spread <strong>{formatBookPrice(spreadValue)}</strong></span>
                <span>Depth <strong>{formatBookTotal(askDepthTotal + bidDepthTotal)}</strong></span>
              </div>
              <div className="wm-focus-book-imbalance" aria-label="book depth imbalance">
                <span>Bid {Math.round(bidImbalance)}%</span>
                <div>
                  <i className="bid" style={{ width: `${bidImbalance}%` }} />
                  <i className="ask" style={{ width: `${100 - bidImbalance}%` }} />
                </div>
                <span>Ask {Math.round(100 - bidImbalance)}%</span>
              </div>
              <div className="wm-focus-book-ladder">
                <div className="wm-focus-book-side ask">
                  <div className="wm-focus-book-side-head">
                    <strong>Asks</strong>
                    <span>top {Math.min(askLevels.length, BOOK_LEVEL_LIMIT)} · {formatBookTotal(askDepthTotal)}</span>
                  </div>
                  <div className="wm-focus-book-header">
                    <span>Price</span>
                    <span>Shares</span>
                    <span>Total</span>
                  </div>
                  <div className="wm-focus-book-rows">
                    {askLevels.length ? orderBookRows(askLevels, 'ask', tokenLobState.pulseId) : <div className="wm-focus-book-empty">No asks</div>}
                  </div>
                </div>
                <div className="wm-focus-book-mid" aria-label="order book spread">
                  <span />
                  <strong>MID {formatBookPrice(activePriceForBook)}</strong>
                  <em />
                </div>
                <div className="wm-focus-book-side bid">
                  <div className="wm-focus-book-side-head">
                    <strong>Bids</strong>
                    <span>top {Math.min(bidLevels.length, BOOK_LEVEL_LIMIT)} · {formatBookTotal(bidDepthTotal)}</span>
                  </div>
                  <div className="wm-focus-book-header">
                    <span>Price</span>
                    <span>Shares</span>
                    <span>Total</span>
                  </div>
                  <div className="wm-focus-book-rows">
                    {bidLevels.length ? orderBookRows(bidLevels, 'bid', tokenLobState.pulseId) : <div className="wm-focus-book-empty">No bids</div>}
                  </div>
                </div>
              </div>
            </div>
          )}
        </Panel>
      ))}

      {wrapPanel('global-orderfilled', 'wm-focus-panel-slot wm-focus-trades-slot', (
        <Panel
          title="OrderFilled Flow"
          badge={marketIsClosed ? 'Historical' : trades.length ? 'Live' : 'Focused'}
          status={marketIsClosed ? 'muted' : 'live'}
          count={trades.length}
          className="wm-focus-panel wm-focus-trades-panel wm-orderfilled-panel"
        >
          <div className="wm-focus-trades">
            {!executionAvailable && detail
              ? emptyState('Select an outcome with local sync to inspect orderfilled flow.')
              : trades.length
              ? orderfilledList(trades, 12, (trade) => resolveMarketTitle(ctx, trade), { compact: true })
              : emptyState(hasServingTradeActivity ? 'Volume is available, but raw OrderFilled rows are not indexed locally yet.' : noTradesYet ? 'No orderfilled rows exist for this outcome yet.' : 'No local orderfilled rows are available for this market.')}
          </div>
        </Panel>
      ))}
    </section>
  );
}
