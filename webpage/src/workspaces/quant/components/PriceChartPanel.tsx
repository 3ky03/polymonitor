import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  LineSeries,
  LineType,
  type AreaData,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type Time,
} from 'lightweight-charts';
import type { DataStatus, MarketInfo, PricePoint, Signal } from '../types';
import { movingAverage } from '../utils/backtest';
import { fmtPrice, fmtProbabilityPercent, formatTime } from '../utils/formatters';

type ScaleMode = 'full' | 'auto' | 'local';
type EventDisplayMode = 'top3' | 'top5' | 'top10' | 'all' | 'selected';
type EventSortMode = 'probability' | 'outcome' | 'volume' | 'change';
type EventSideMode = 'auto' | 'yes' | 'no' | 'both';
type EventLabelMode = 'selected' | 'top' | 'all';
type TooltipMode = 'compact' | 'full';
type DataWindowMode = 'compact' | 'expanded';
type DataWindowDock = 'floating' | 'left' | 'right';
type ChartViewMode = 'raw' | 'normalized' | 'direct' | 'implied';
type ChartType = 'Block-close line' | 'Line' | 'Step line' | 'Area' | 'Candles';
type PriceSeriesApi = ISeriesApi<'Line'> | ISeriesApi<'Area'> | ISeriesApi<'Candlestick'>;
type RangeSelection = { startX: number; currentX: number } | null;
type LogicalRangeState = { from: number; to: number } | null;
type LogicalRangeLike = LogicalRangeState | undefined;
type RangeZoomMeta = {
  fromIndex: number;
  toIndex: number;
  fromBlock: number;
  toBlock: number;
  pointCount: number;
  spanBlocks: number;
};

const DRAW_TOOLS = [
  ['cursor', 'Cursor', 'M5 4l10 8-5 1.5L8 18 5 4z'],
  ['crosshair', 'Crosshair', 'M12 4v16M4 12h16'],
  ['trend', 'Trend line', 'M4 17L17 4M6 17h-2v-2M17 6V4h-2'],
  ['hline', 'Horizontal line', 'M4 12h16'],
  ['vline', 'Vertical line', 'M12 4v16'],
  ['ray', 'Ray', 'M4 15l7-7 4 4 4-8M15 4h4v4'],
  ['text', 'Text', 'M5 5h14M12 5v14M9 19h6'],
  ['fib', 'Fib retracement', 'M5 5h14M5 9h14M5 13h14M5 17h14'],
  ['measure', 'Measure', 'M5 17L17 5M7 17H5v-2M17 7V5h-2M8 14l2 2M14 8l2 2'],
  ['brush', 'Brush', 'M7 17c1.5 1 4 0 4-2L17 7l-3-3-6 8c-1.5 0-3 1.5-1 5z'],
  ['magnet', 'Magnet', 'M7 4v7a5 5 0 0010 0V4M7 8h3M14 8h3'],
  ['lock', 'Lock', 'M7 10V8a5 5 0 0110 0v2M6 10h12v9H6z'],
  ['eye', 'Visibility', 'M3 12s3-5 9-5 9 5 9 5-3 5-9 5-9-5-9-5zM12 9a3 3 0 100 6 3 3 0 000-6z'],
  ['delete', 'Delete drawings', 'M7 7h10M10 7V5h4v2M8 7l1 12h6l1-12'],
] as const;

const SCALE_MODES: Array<[ScaleMode, string]> = [
  ['full', 'Full'],
  ['auto', 'Auto'],
  ['local', 'Local'],
];

type PriceChartPanelProps = {
  prices: PricePoint[];
  market: MarketInfo;
  selectedTradeId: string | null;
  signals?: Signal[];
  priceSource: string;
  dataStatus: DataStatus;
  loadingMessage?: string;
  marketCoverageRows?: number;
  loadedPriceRows?: number;
  backtestRows?: number;
  eventMode?: boolean;
  selectedTokenId?: string;
  selectedOutcomeLabel?: string;
  pinnedOutcomeKeys?: string[];
  hiddenOutcomeKeys?: string[];
  soloOutcomeKey?: string;
  onPinnedOutcomeKeysChange?: (keys: string[]) => void;
  onHiddenOutcomeKeysChange?: (keys: string[]) => void;
  onSoloOutcomeKeyChange?: (key: string) => void;
  onOutcomeSelect?: (tokenId: string, side: 'YES' | 'NO') => void;
  onOutcomeHover?: (key: string) => void;
  onRetry?: () => void;
  viewportResetKey?: string;
  onViewportModeChange?: (mode: 'preset' | 'custom') => void;
  onVisibleWindowChange?: (windowRange: { fromX: number; toX: number; pointCount: number; viewportWidth: number }) => void;
};

type SeriesRefs = {
  lines: Map<string, PriceSeriesApi>;
  ma: ISeriesApi<'Line'> | null;
  volume: ISeriesApi<'Histogram'> | null;
  kind: ChartType;
};

type Drawing = {
  id: string;
  kind: 'trend' | 'ray' | 'hline' | 'vline' | 'measure' | 'text';
  points: Array<{ timestamp: number; price: number }>;
  text?: string;
};

type DataWindowSettings = {
  visible: boolean;
  minimized: boolean;
  mode: DataWindowMode;
  dock: DataWindowDock;
  x?: number;
  y?: number;
};

type OutcomeGroup = {
  key: string;
  label: string;
  fullLabel: string;
  tokenId?: string;
  tokenSide: string;
  order: number;
  points: PricePoint[];
};

const SERIES_COLORS = ['#3b82f6', '#f59e0b', '#22c55e', '#ef4444', '#06b6d4', '#a855f7', '#f97316', '#84cc16', '#ec4899', '#14b8a6', '#eab308', '#94a3b8', '#60a5fa', '#fb7185'];

function chartTime(point: PricePoint): Time {
  return Math.floor(point.timestamp) as Time;
}

function blockLabel(value: number) {
  if (!Number.isFinite(value)) return '';
  return value.toLocaleString('en-US');
}

function compactIdentifier(value: string | undefined) {
  if (!value) return '--';
  if (value.length <= 16) return value;
  return `${value.slice(0, 8)}...${value.slice(-6)}`;
}

function pointLabel(point: PricePoint | undefined, source: string) {
  if (!point) return '--';
  if (source.includes('block')) return `block ${blockLabel(point.timestamp)}`;
  return formatTime(point.timestamp);
}

function sortUnique(points: PricePoint[]) {
  const seen = new Set<number>();
  return points
    .filter((point) => Number.isFinite(point.timestamp) && Number.isFinite(point.close))
    .sort((left, right) => left.timestamp - right.timestamp)
    .filter((point) => {
      const key = Math.floor(point.timestamp);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function outcomeKey(point: PricePoint) {
  return point.outcomeKey || point.outcomeShortLabel || point.outcomeLabel || point.tokenSide || point.tokenId || 'YES';
}

function outcomeGroups(points: PricePoint[]): OutcomeGroup[] {
  const groups = new Map<string, PricePoint[]>();
  points.forEach((point) => {
    const key = outcomeKey(point);
    const rows = groups.get(key) || [];
    rows.push(point);
    groups.set(key, rows);
  });
  return Array.from(groups.entries())
    .map(([key, rows], index) => {
      const sorted = sortUnique(rows);
      const sample = sorted[sorted.length - 1] || rows[0];
      return {
        key,
        label: sample?.outcomeShortLabel || sample?.outcomeLabel || key,
        fullLabel: sample?.outcomeFullLabel || sample?.outcomeLabel || key,
        tokenId: sample?.tokenId,
        tokenSide: (sample?.tokenSide || 'YES').toUpperCase(),
        order: index,
        points: sorted,
      };
    })
    .filter((group) => group.points.length)
    .sort((left, right) => {
      const leftLatest = left.points[left.points.length - 1]?.close ?? 0;
      const rightLatest = right.points[right.points.length - 1]?.close ?? 0;
      return rightLatest - leftLatest;
    });
}

function lineData(points: PricePoint[]): LineData<Time>[] {
  return points.map((point) => ({
    time: chartTime(point),
    value: point.close,
  }));
}

function areaData(points: PricePoint[]): AreaData<Time>[] {
  return points.map((point) => ({
    time: chartTime(point),
    value: point.close,
  }));
}

function candleData(points: PricePoint[]): CandlestickData<Time>[] {
  return points.map((point, index) => {
    const previous = index > 0 ? points[index - 1] : null;
    const open = clampProbability(previous?.close ?? point.close);
    const close = clampProbability(point.close);
    const movement = Math.abs(close - open);
    const wick = Math.max(0.0005, Math.min(0.015, movement * 0.35));
    return {
      time: chartTime(point),
      open,
      high: clampProbability(Math.max(open, close) + wick),
      low: clampProbability(Math.min(open, close) - wick),
      close,
    };
  });
}

function volumeData(points: PricePoint[]): HistogramData<Time>[] {
  const byTime = new Map<number, number>();
  points
    .filter((point) => Number.isFinite(point.timestamp))
    .forEach((point) => {
      const key = Math.floor(point.timestamp);
      byTime.set(key, (byTime.get(key) || 0) + Math.max(0, point.volume));
    });
  return Array.from(byTime.entries())
    .sort(([left], [right]) => left - right)
    .map(([timestamp, volume]) => ({
      time: timestamp as Time,
      value: volume,
      color: 'rgba(148,163,184,0.24)',
    }));
}

function chartTypeCaption(chartType: ChartType) {
  if (chartType === 'Candles') return 'close-derived candles';
  if (chartType === 'Step line') return 'step line';
  if (chartType === 'Area') return 'area';
  return 'line';
}

function formatSigned(value: number, digits = 3) {
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}`;
}

function latestClose(group: { points: PricePoint[] }) {
  return group.points[group.points.length - 1]?.close ?? 0;
}

function priceKindForPoint(point: PricePoint, tokenSide: string) {
  return tokenSide === 'NO' ? point.noPriceKind || 'direct' : point.yesPriceKind || 'direct';
}

function applyChartViewMode(groups: OutcomeGroup[], viewMode: ChartViewMode): OutcomeGroup[] {
  if (viewMode === 'raw') return groups;
  if (viewMode === 'direct' || viewMode === 'implied') {
    return groups
      .map((group) => ({
        ...group,
        points: group.points.filter((point) => priceKindForPoint(point, group.tokenSide) === viewMode),
      }))
      .filter((group) => group.points.length);
  }
  const sumsByBlock = new Map<number, number>();
  groups
    .filter((group) => group.tokenSide === 'YES')
    .forEach((group) => {
      group.points.forEach((point) => {
        const key = Math.floor(point.timestamp);
        sumsByBlock.set(key, (sumsByBlock.get(key) || 0) + Math.max(0, point.close));
      });
    });
  return groups
    .map((group) => ({
      ...group,
      points: group.points.map((point) => {
        const sum = sumsByBlock.get(Math.floor(point.timestamp)) || 0;
        if (group.tokenSide !== 'YES' || sum <= 0) return point;
        const close = Math.max(0, Math.min(1, point.close / sum));
        return {
          ...point,
          close,
          yesPrice: close,
          noPrice: Math.max(0, Math.min(1, 1 - close)),
        };
      }),
    }))
    .filter((group) => group.points.length);
}

function latestVolume(group: { points: PricePoint[] }) {
  return group.points[group.points.length - 1]?.volume ?? 0;
}

function latestChange(group: { points: PricePoint[] }) {
  const latest = group.points[group.points.length - 1]?.close ?? 0;
  const previous = group.points[Math.max(0, group.points.length - 2)]?.close ?? latest;
  return latest - previous;
}

function countRows(groups: OutcomeGroup[]) {
  return groups.reduce((sum, group) => sum + group.points.length, 0);
}

function sourceKindCounts(groups: OutcomeGroup[]) {
  return groups.reduce((counts, group) => {
    group.points.forEach((point) => {
      const kind = priceKindForPoint(point, group.tokenSide);
      if (kind === 'implied') counts.implied += 1;
      else counts.direct += 1;
      if (point.isCarriedForward) counts.carried += 1;
      if (point.isInterpolated) counts.interpolated += 1;
      if (point.qualityFlags?.length) counts.flagged += 1;
    });
    return counts;
  }, { direct: 0, implied: 0, carried: 0, interpolated: 0, flagged: 0 });
}

function colorWithOpacity(hex: string, opacity: number) {
  const clean = hex.replace('#', '');
  const value = Number.parseInt(clean.length === 3 ? clean.split('').map((ch) => ch + ch).join('') : clean, 16);
  if (!Number.isFinite(value)) return hex;
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${opacity})`;
}

function persistedState<T extends string>(key: string, fallback: T): T {
  try {
    return (window.localStorage.getItem(key) as T) || fallback;
  } catch {
    return fallback;
  }
}

function persistedStringArray(key: string) {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) || '[]');
    return Array.isArray(parsed) ? parsed.filter((value) => typeof value === 'string') : [];
  } catch {
    return [];
  }
}

function persistedDataWindowSettings(): DataWindowSettings {
  try {
    const parsed = JSON.parse(window.localStorage.getItem('polymonitor.quant.dataWindowSettings') || '{}') as Partial<DataWindowSettings>;
    return {
      visible: typeof parsed.visible === 'boolean' ? parsed.visible : false,
      minimized: typeof parsed.minimized === 'boolean' ? parsed.minimized : false,
      mode: parsed.mode === 'expanded' ? 'expanded' : 'compact',
      dock: parsed.dock === 'left' || parsed.dock === 'right' ? parsed.dock : 'floating',
      x: Number.isFinite(parsed.x) ? Number(parsed.x) : 12,
      y: Number.isFinite(parsed.y) ? Number(parsed.y) : 122,
    };
  } catch {
    return { visible: false, minimized: false, mode: 'compact', dock: 'floating', x: 12, y: 122 };
  }
}

function clampDataWindowPosition(x: number, y: number, bounds?: DOMRect | null) {
  if (!bounds) return { x: Math.max(8, x), y: Math.max(48, y) };
  const maxX = Math.max(8, bounds.width - 240);
  const maxY = Math.max(56, bounds.height - 130);
  return {
    x: Math.max(8, Math.min(maxX, x)),
    y: Math.max(56, Math.min(maxY, y)),
  };
}

function axisPercentFromIndex(index: number, points: PricePoint[], range?: LogicalRangeLike) {
  if (!points.length || index < 0) return null;
  if (range && Number.isFinite(range.from) && Number.isFinite(range.to) && range.to > range.from) {
    return ((index - range.from) / (range.to - range.from)) * 100;
  }
  return (index / Math.max(1, points.length - 1)) * 100;
}

function axisPercentStyle(percent: number | null) {
  if (percent === null || !Number.isFinite(percent) || percent < -6 || percent > 106) return null;
  return `${Math.max(-3, Math.min(103, percent))}%`;
}

function markerPosition(signal: Signal, points: PricePoint[], range?: LogicalRangeLike) {
  if (!points.length) return null;
  let bestIndex = 0;
  let bestDistance = Number.POSITIVE_INFINITY;
  points.forEach((point, index) => {
    const distance = Math.abs(point.timestamp - signal.timestamp);
    if (distance < bestDistance) {
      bestDistance = distance;
      bestIndex = index;
    }
  });
  const point = points[bestIndex];
  if (!point) return null;
  const left = axisPercentStyle(axisPercentFromIndex(bestIndex, points, range));
  if (!left) return null;
  return {
    signal,
    point,
    index: bestIndex,
    left,
    top: `${Math.max(7, Math.min(84, (1 - point.close) * 100))}%`,
  };
}

function nearestPointIndex(points: PricePoint[], timestamp: number) {
  if (!points.length) return -1;
  let bestIndex = 0;
  let bestDistance = Number.POSITIVE_INFINITY;
  points.forEach((point, index) => {
    const distance = Math.abs(point.timestamp - timestamp);
    if (distance < bestDistance) {
      bestDistance = distance;
      bestIndex = index;
    }
  });
  return bestIndex;
}

function previousPointFor(points: PricePoint[], point: PricePoint | null | undefined) {
  if (!point || points.length < 2) return null;
  const index = nearestPointIndex(points, point.timestamp);
  if (index <= 0) return null;
  return points[index - 1] || null;
}

function normalizeLogicalRange(range: { from: number; to: number } | null): LogicalRangeState {
  if (!range || !Number.isFinite(range.from) || !Number.isFinite(range.to)) return null;
  return { from: range.from, to: range.to };
}

function logicalRangesClose(left: LogicalRangeState, right: LogicalRangeState) {
  if (!left && !right) return true;
  if (!left || !right) return false;
  return Math.abs(left.from - right.from) < 0.02 && Math.abs(left.to - right.to) < 0.02;
}

function clampLogicalRange(range: { from: number; to: number }, totalPoints: number) {
  const fullSpan = Math.max(1, totalPoints - 1);
  const minSpan = Math.min(fullSpan, 8);
  const maxSpan = Math.max(minSpan, fullSpan + 4);
  const rawSpan = Math.max(0.01, range.to - range.from);
  const span = Math.max(minSpan, Math.min(maxSpan, rawSpan));
  const maxRight = fullSpan + 2;
  const minLeft = -2;
  let from = range.from;
  let to = from + span;
  if (from < minLeft) {
    from = minLeft;
    to = from + span;
  }
  if (to > maxRight) {
    to = maxRight;
    from = to - span;
  }
  return { from, to };
}

function clampProbability(value: number) {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(1, value));
}

function nearestPoint(points: PricePoint[], timestamp: number) {
  if (!points.length) return null;
  return points.reduce<PricePoint | null>((best, candidate) => {
    if (!best) return candidate;
    return Math.abs(candidate.timestamp - timestamp) < Math.abs(best.timestamp - timestamp) ? candidate : best;
  }, null);
}

function pointSnapshot(
  point: PricePoint | null | undefined,
  latestPoint: PricePoint | null | undefined,
  maPoint: PricePoint | null | undefined,
  previousPoint?: PricePoint | null,
) {
  if (!point) return null;
  const yes = clampProbability(point.yesPrice ?? point.close);
  const no = clampProbability(point.noPrice ?? (1 - yes));
  const latestYes = clampProbability(latestPoint?.yesPrice ?? latestPoint?.close ?? yes);
  const latestNo = clampProbability(latestPoint?.noPrice ?? (1 - latestYes));
  const previousYes = previousPoint ? clampProbability(previousPoint.yesPrice ?? previousPoint.close) : yes;
  const previousNo = previousPoint ? clampProbability(previousPoint.noPrice ?? (1 - previousYes)) : no;
  return {
    point,
    yes,
    no,
    yesKind: point.yesPriceKind || 'direct',
    noKind: point.noPriceKind || 'implied',
    ma: maPoint?.close,
    deltaYes: yes - latestYes,
    deltaNo: no - latestNo,
    deltaYesPct: latestYes ? ((yes - latestYes) / latestYes) * 100 : 0,
    deltaNoPct: latestNo ? ((no - latestNo) / latestNo) * 100 : 0,
    barDeltaYes: yes - previousYes,
    barDeltaNo: no - previousNo,
    barDeltaYesPct: previousYes ? ((yes - previousYes) / previousYes) * 100 : 0,
    barDeltaNoPct: previousNo ? ((no - previousNo) / previousNo) * 100 : 0,
  };
}

function pointToScreenSafe(point: PricePoint, points: PricePoint[], range?: LogicalRangeLike) {
  const index = points.findIndex((row) => Math.floor(row.timestamp) === Math.floor(point.timestamp));
  const left = axisPercentStyle(axisPercentFromIndex(index >= 0 ? index : 0, points, range)) || '0%';
  return {
    x: left,
    y: `${Math.max(8, Math.min(86, (1 - clampProbability(point.close)) * 100))}%`,
  };
}

function percentFromPrice(value: number) {
  return Math.max(5, Math.min(90, (1 - clampProbability(value)) * 100));
}

function robustPriceRange(points: PricePoint[], paddingRatio: number) {
  const values = points
    .map((point) => point.close)
    .filter((value) => Number.isFinite(value) && value >= 0 && value <= 1);
  if (!values.length) return { minValue: 0, maxValue: 1 };
  const min = Math.min(...values);
  const max = Math.max(...values);
  const spread = Math.max(0.015, max - min);
  const paddedMin = Math.max(0, min - spread * paddingRatio);
  const paddedMax = Math.min(1, max + spread * paddingRatio);
  if (paddedMax - paddedMin < 0.01) {
    const center = (paddedMin + paddedMax) / 2;
    return {
      minValue: Math.max(0, center - 0.005),
      maxValue: Math.min(1, center + 0.005),
    };
  }
  return {
    minValue: paddedMin,
    maxValue: paddedMax,
  };
}

function scaleProvider(mode: ScaleMode, points: PricePoint[], visiblePoints: PricePoint[] = points) {
  if (mode === 'auto') {
    return () => ({ priceRange: robustPriceRange(visiblePoints, 0.28) });
  }
  if (mode === 'full' || !points.length) {
    return () => ({ priceRange: { minValue: 0, maxValue: 1 } });
  }
  const min = points.reduce((value, point) => Math.min(value, point.close), Number.POSITIVE_INFINITY);
  const max = points.reduce((value, point) => Math.max(value, point.close), Number.NEGATIVE_INFINITY);
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return () => ({ priceRange: { minValue: 0, maxValue: 1 } });
  }
  const spread = Math.max(0.01, max - min);
  return () => ({
    priceRange: {
      minValue: Math.max(0, min - spread * 0.3),
      maxValue: Math.min(1, max + spread * 0.3),
    },
  });
}

function ToolIcon({ path }: { path: string }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d={path} />
    </svg>
  );
}

function drawingStorageKey(market: MarketInfo, priceSource: string) {
  return `polydata.quant.drawings.${market.slug}.${priceSource}`;
}

export function PriceChartPanel({
  prices,
  market,
  selectedTradeId,
  signals = [],
  priceSource,
  dataStatus,
  loadingMessage = '',
  marketCoverageRows = 0,
  loadedPriceRows,
  backtestRows = 0,
  eventMode = false,
  selectedTokenId = '',
  selectedOutcomeLabel = '',
  pinnedOutcomeKeys,
  hiddenOutcomeKeys,
  soloOutcomeKey,
  onPinnedOutcomeKeysChange,
  onHiddenOutcomeKeysChange,
  onSoloOutcomeKeyChange,
  onOutcomeSelect,
  onOutcomeHover,
  onRetry,
  viewportResetKey = '',
  onViewportModeChange,
  onVisibleWindowChange,
}: PriceChartPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartSurfaceRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<SeriesRefs>({ lines: new Map(), ma: null, volume: null, kind: 'Block-close line' });
  const pointsRef = useRef<PricePoint[]>([]);
  const visibleOutcomeGroupsRef = useRef<OutcomeGroup[]>([]);
  const dataWindowDragRef = useRef<{ startX: number; startY: number; baseX: number; baseY: number } | null>(null);
  const navigatorDragRef = useRef(false);
  const suppressViewportModeRef = useRef(false);
  const onVisibleWindowChangeRef = useRef<typeof onVisibleWindowChange>(onVisibleWindowChange);
  const lastWindowNotifyRef = useRef('');
  const visibleLogicalRangeRef = useRef<LogicalRangeState>(null);
  const lastFitRequestKeyRef = useRef('');
  const lastTradeFocusKeyRef = useRef('');
  const onViewportModeChangeRef = useRef(onViewportModeChange);
  const onOutcomeHoverRef = useRef(onOutcomeHover);
  const [hover, setHover] = useState<PricePoint | null>(null);
  const [hoveredOutcomeKey, setHoveredOutcomeKey] = useState('');
  const [pinnedPoint, setPinnedPoint] = useState<PricePoint | null>(null);
  const [visibleLogicalRange, setVisibleLogicalRange] = useState<LogicalRangeState>(null);
  const [dataWindowSettings, setDataWindowSettings] = useState<DataWindowSettings>(persistedDataWindowSettings);
  const [dataWindowMenuOpen, setDataWindowMenuOpen] = useState(false);
  const [scaleMode, setScaleMode] = useState<ScaleMode>('auto');
  const [activeTool, setActiveTool] = useState('cursor');
  const [drawings, setDrawings] = useState<Drawing[]>([]);
  const [pendingDrawing, setPendingDrawing] = useState<Drawing | null>(null);
  const [drawingsLocked, setDrawingsLocked] = useState(false);
  const [drawingsHidden, setDrawingsHidden] = useState(false);
  const [chartType, setChartType] = useState<ChartType>('Block-close line');
  const [indicatorMode, setIndicatorMode] = useState({ ma: true, ema: false, bands: false, rsi: false, volume: true });
  const [displayMode, setDisplayMode] = useState<EventDisplayMode>(() => persistedState('polydata.quant.event.displayMode', 'top5'));
  const [eventSortMode, setEventSortMode] = useState<EventSortMode>(() => persistedState('polydata.quant.event.sortMode', 'probability'));
  const [compareMode, setCompareMode] = useState<EventSideMode>(() => persistedState('polydata.quant.event.sideMode', 'auto'));
  const [labelMode, setLabelMode] = useState<EventLabelMode>(() => persistedState('polydata.quant.event.labelMode', 'top'));
  const [tooltipMode, setTooltipMode] = useState<TooltipMode>(() => persistedState('polydata.quant.event.tooltipMode', 'compact'));
  const [chartViewMode, setChartViewMode] = useState<ChartViewMode>(() => persistedState('polydata.quant.chart.viewMode', 'raw'));
  const [showLowProbability, setShowLowProbability] = useState(false);
  const [outcomeManagerOpen, setOutcomeManagerOpen] = useState(false);
  const [outcomeManagerQuery, setOutcomeManagerQuery] = useState('');
  const [layoutMode, setLayoutMode] = useState('1');
  const [internalPinnedOutcomeKeys, setInternalPinnedOutcomeKeys] = useState<string[]>(() => persistedStringArray('polydata.quant.chart.pinnedOutcomes'));
  const [internalHiddenOutcomeKeys, setInternalHiddenOutcomeKeys] = useState<string[]>(() => persistedStringArray('polydata.quant.chart.hiddenOutcomes'));
  const [internalSoloOutcomeKey, setInternalSoloOutcomeKey] = useState<string>('');
  const [rangeZoomEnabled, setRangeZoomEnabled] = useState(false);
  const [rangeSelection, setRangeSelection] = useState<RangeSelection>(null);
  const [rangeZoomNotice, setRangeZoomNotice] = useState<RangeZoomMeta | null>(null);
  const [blockAxisTop, setBlockAxisTop] = useState<number | null>(null);
  const [replayEnabled, setReplayEnabled] = useState(false);
  const [replayPlaying, setReplayPlaying] = useState(false);
  const [replayIndex, setReplayIndex] = useState<number | null>(null);

  const rawAllPoints = useMemo(() => sortUnique(prices), [prices]);
  const fitRequestKey = `${market.slug}|${priceSource}|${scaleMode}|${viewportResetKey}`;
  const replayCutoff = replayEnabled && replayIndex !== null ? rawAllPoints[Math.min(replayIndex, rawAllPoints.length - 1)]?.timestamp : null;
  const replayPrices = useMemo(() => (
    replayCutoff ? prices.filter((point) => point.timestamp <= replayCutoff) : prices
  ), [prices, replayCutoff]);
  const rawGroupedOutcomes = useMemo(() => outcomeGroups(replayPrices), [replayPrices]);
  const yesOutcomeCount = useMemo(() => rawGroupedOutcomes.filter((group) => group.tokenSide === 'YES').length, [rawGroupedOutcomes]);
  const effectiveSideMode = compareMode === 'auto' ? 'yes' : compareMode;
  const sideFilteredOutcomes = useMemo(() => rawGroupedOutcomes.filter((group) => {
    if (effectiveSideMode === 'both') return true;
    if (effectiveSideMode === 'yes') return group.tokenSide === 'YES';
    if (effectiveSideMode === 'no') return group.tokenSide === 'NO';
    return true;
  }), [effectiveSideMode, rawGroupedOutcomes]);
  const viewFilteredOutcomes = useMemo(() => applyChartViewMode(sideFilteredOutcomes, chartViewMode), [chartViewMode, sideFilteredOutcomes]);
  const effectivePinnedOutcomeKeys = pinnedOutcomeKeys ?? internalPinnedOutcomeKeys;
  const effectiveHiddenOutcomeKeys = hiddenOutcomeKeys ?? internalHiddenOutcomeKeys;
  const effectiveSoloOutcomeKey = soloOutcomeKey ?? internalSoloOutcomeKey;
  const displayedOutcomeCount = eventMode ? sideFilteredOutcomes.length : (yesOutcomeCount || sideFilteredOutcomes.length);
  const selectedGroup = useMemo(() => viewFilteredOutcomes.find((group) => (
    (selectedTokenId && group.tokenId === selectedTokenId)
    || (selectedOutcomeLabel && group.label === selectedOutcomeLabel)
  )) || viewFilteredOutcomes[0] || null, [selectedOutcomeLabel, selectedTokenId, viewFilteredOutcomes]);
  const sortedOutcomes = useMemo(() => viewFilteredOutcomes.slice().sort((left, right) => {
    if (eventSortMode === 'outcome') return left.order - right.order;
    if (eventSortMode === 'volume') return latestVolume(right) - latestVolume(left);
    if (eventSortMode === 'change') return Math.abs(latestChange(right)) - Math.abs(latestChange(left));
    return latestClose(right) - latestClose(left);
  }), [eventSortMode, viewFilteredOutcomes]);
  const visibleOutcomeGroups = useMemo(() => {
    const hidden = new Set(effectiveHiddenOutcomeKeys);
    const pinned = new Set(effectivePinnedOutcomeKeys);
    const available = sortedOutcomes.filter((group) => !hidden.has(group.key));
    if (effectiveSoloOutcomeKey) {
      const solo = available.find((group) => group.key === effectiveSoloOutcomeKey);
      return solo ? [solo] : available.slice(0, 1);
    }
    if (!eventMode) return available;
    if (displayMode === 'selected') return selectedGroup ? [selectedGroup] : available.slice(0, 1);
    const topLimit = displayMode === 'top3' ? 3 : displayMode === 'top10' ? 10 : displayMode === 'all' ? sortedOutcomes.length : 5;
    const visible = new Map<string, (typeof sortedOutcomes)[number]>();
    available.slice(0, topLimit).forEach((group) => visible.set(group.key, group));
    available.filter((group) => pinned.has(group.key)).forEach((group) => visible.set(group.key, group));
    if (showLowProbability) {
      available.filter((group) => latestClose(group) >= 0.05).forEach((group) => visible.set(group.key, group));
    }
    if (selectedGroup) visible.set(selectedGroup.key, selectedGroup);
    return Array.from(visible.values()).sort((left, right) => {
      if (eventSortMode === 'outcome') return left.order - right.order;
      return latestClose(right) - latestClose(left);
    });
  }, [displayMode, effectiveHiddenOutcomeKeys, effectivePinnedOutcomeKeys, effectiveSoloOutcomeKey, eventMode, eventSortMode, selectedGroup, showLowProbability, sortedOutcomes]);
  const allPoints = useMemo(() => visibleOutcomeGroups.flatMap((group) => group.points).sort((left, right) => left.timestamp - right.timestamp), [visibleOutcomeGroups]);
  const primaryPoints = selectedGroup?.points || visibleOutcomeGroups[0]?.points || allPoints;
  const latestPoint = primaryPoints[primaryPoints.length - 1] || null;
  const latest = hover || latestPoint;
  const readoutCandidatePoint = hover || pinnedPoint || latestPoint;
  const readoutPreviousPoint = previousPointFor(primaryPoints, readoutCandidatePoint);
  const delta = readoutCandidatePoint && readoutPreviousPoint ? readoutCandidatePoint.close - readoutPreviousPoint.close : 0;
  const deltaPct = readoutCandidatePoint && readoutPreviousPoint?.close ? (delta / readoutPreviousPoint.close) * 100 : 0;
  const minPrice = primaryPoints.reduce((min, point) => Math.min(min, point.close), Number.POSITIVE_INFINITY);
  const maxPrice = primaryPoints.reduce((max, point) => Math.max(max, point.close), Number.NEGATIVE_INFINITY);
  const volumeTotal = allPoints.reduce((sum, point) => sum + (Number.isFinite(point.volume) ? point.volume : 0), 0);
  const selectedLatest = selectedGroup ? latestClose(selectedGroup) : latest?.close || 0;
  const allYesSum = rawGroupedOutcomes.filter((group) => group.tokenSide === 'YES').reduce((sum, group) => sum + latestClose(group), 0);
  const visibleYesSum = visibleOutcomeGroups.filter((group) => group.tokenSide === 'YES').reduce((sum, group) => sum + latestClose(group), 0);
  const sumWarning = eventMode && Math.abs(allYesSum - 1) > 0.08;
  const chartViewSummary = useMemo(() => {
    const sourceRows = countRows(sideFilteredOutcomes);
    const filteredRows = countRows(viewFilteredOutcomes);
    const visibleRows = countRows(visibleOutcomeGroups);
    const sourceKinds = sourceKindCounts(sideFilteredOutcomes);
    const visibleKinds = sourceKindCounts(visibleOutcomeGroups);
    const coverage = sourceRows > 0 ? filteredRows / sourceRows : 0;
    const visibleCoverage = filteredRows > 0 ? visibleRows / filteredRows : 0;
    const qualityRows = sourceKinds.carried + sourceKinds.interpolated + sourceKinds.flagged;
    const normalizedBlocks = new Set<number>();
    if (chartViewMode === 'normalized') {
      sideFilteredOutcomes
        .filter((group) => group.tokenSide === 'YES')
        .forEach((group) => group.points.forEach((point) => normalizedBlocks.add(Math.floor(point.timestamp))));
    }
    const labels: Record<ChartViewMode, { label: string; detail: string }> = {
      raw: {
        label: 'Raw',
        detail: 'Plot the stored block-close probability without reshaping.',
      },
      normalized: {
        label: 'Normalized',
        detail: 'YES outcomes are divided by the same-block YES total, so the full event sums to 1.000 when coverage aligns.',
      },
      direct: {
        label: 'Direct',
        detail: 'Only rows whose plotted side was directly observed from the selected source are shown.',
      },
      implied: {
        label: 'Implied',
        detail: 'Only complement-derived rows are shown, usually NO from YES or YES from NO.',
      },
    };
    let warning = '';
    if (sourceRows && chartViewMode === 'direct' && filteredRows === 0) {
      warning = 'No direct rows for the current side/source. Switch side or use Raw.';
    } else if (sourceRows && chartViewMode === 'implied' && filteredRows === 0) {
      warning = 'No implied rows for the current side/source. This source is already direct for the visible side.';
    } else if (chartViewMode === 'normalized' && Math.abs(allYesSum - 1) > 0.08) {
      warning = 'Latest raw YES sum is far from 1.000; normalized view is for relative event share, not executable price.';
    } else if (qualityRows > 0) {
      warning = `${qualityRows.toLocaleString('en-US')} rows have carried/interpolated/quality flags.`;
    }
    return {
      ...labels[chartViewMode],
      sourceRows,
      filteredRows,
      visibleRows,
      sourceKinds,
      visibleKinds,
      coverage,
      visibleCoverage,
      normalizedBlockCount: normalizedBlocks.size,
      warning,
    };
  }, [allYesSum, chartViewMode, sideFilteredOutcomes, viewFilteredOutcomes, visibleOutcomeGroups]);
  const maPoints = useMemo(() => {
    const closes = primaryPoints.map((point) => point.close);
    const ma = movingAverage(closes, Math.min(40, Math.max(3, Math.floor(primaryPoints.length / 20))));
    return primaryPoints.map((point, index) => ({ ...point, close: ma[index] ?? point.close }));
  }, [primaryPoints]);
  const latestMaPoint = latestPoint ? nearestPoint(maPoints, latestPoint.timestamp) : null;
  const dataWindowPoint = pinnedPoint || hover || latestPoint;
  const dataWindowMaPoint = dataWindowPoint ? nearestPoint(maPoints, dataWindowPoint.timestamp) : null;
  const dataWindowInspect = pointSnapshot(dataWindowPoint, latestPoint, dataWindowMaPoint, previousPointFor(primaryPoints, dataWindowPoint));
  const hoverMaPoint = hover ? nearestPoint(maPoints, hover.timestamp) : null;
  const hoverGroup = useMemo(
    () => visibleOutcomeGroups.find((group) => group.key === hoveredOutcomeKey) || null,
    [hoveredOutcomeKey, visibleOutcomeGroups],
  );
  const chartScreenForPoint = (point: PricePoint, group?: OutcomeGroup | null) => {
    const chart = chartRef.current;
    const matchGroup = group || visibleOutcomeGroups.find((candidate) => candidate.points.some((row) => Math.floor(row.timestamp) === Math.floor(point.timestamp))) || selectedGroup;
    const line = matchGroup ? seriesRef.current.lines.get(matchGroup.key) : null;
    const chartHeight = Math.max(260, containerRef.current?.clientHeight || 420);
    const chartWidth = Math.max(360, containerRef.current?.clientWidth || 1100);
    const xCoordinate = chart?.timeScale().timeToCoordinate(chartTime(point));
    const yCoordinate = line?.priceToCoordinate(point.close);
    if (typeof xCoordinate === 'number' && Number.isFinite(xCoordinate) && typeof yCoordinate === 'number' && Number.isFinite(yCoordinate)) {
      return {
        x: `${Math.max(0, Math.min(chartWidth - 70, xCoordinate))}px`,
        y: `${Math.max(12, Math.min(chartHeight - 58, yCoordinate))}px`,
      };
    }
    return pointToScreenSafe(point, matchGroup?.points || primaryPoints, visibleLogicalRange);
  };
  const hoverInspect = pointSnapshot(hover, latestPoint, hoverMaPoint, previousPointFor(hoverGroup?.points || primaryPoints, hover));
  const hoverScreen = hover ? chartScreenForPoint(hover, hoverGroup) : null;
  const hoverX = Number(String(hoverScreen?.x || '').replace(/[^\d.-]/g, ''));
  const hoverTooltipSide = Number.isFinite(hoverX) && hoverX > Math.max(520, (containerRef.current?.clientWidth || 1100) * 0.66) ? 'left-side' : '';
  const pinnedMaPoint = pinnedPoint ? nearestPoint(maPoints, pinnedPoint.timestamp) : null;
  const pinnedInspect = pointSnapshot(pinnedPoint, latestPoint, pinnedMaPoint, previousPointFor(primaryPoints, pinnedPoint));
  const pinnedScreen = pinnedPoint ? chartScreenForPoint(pinnedPoint, selectedGroup) : null;
  const latestInspect = pointSnapshot(latestPoint, latestPoint, latestMaPoint, previousPointFor(primaryPoints, latestPoint));
  const readoutInspect = hoverInspect || pinnedInspect || latestInspect;
  const readoutMode = hoverInspect ? 'Hover' : pinnedInspect ? 'Pinned' : 'Latest';
  const readoutPoint = readoutInspect?.point || latestPoint;
  const hoverOutcomeStack = useMemo(() => {
    if (!hoverInspect || !visibleOutcomeGroups.length) return [];
    const hoverBlock = hoverInspect.point.timestamp;
    const maxRows = tooltipMode === 'full' ? 12 : 8;
    const usedSlots: number[] = [];
    return visibleOutcomeGroups
      .map((group) => {
        const point = nearestPoint(group.points, hoverBlock);
        if (!point) return null;
        const price = clampProbability(point.close);
        const latestGroupPoint = group.points[group.points.length - 1];
        const latestPrice = clampProbability(latestGroupPoint?.close ?? price);
        const line = seriesRef.current.lines.get(group.key);
        const coordinate = line?.priceToCoordinate(price);
        const chartHeight = Math.max(260, containerRef.current?.clientHeight || 420);
        const fallbackY = (percentFromPrice(price) / 100) * chartHeight;
        const rawY = typeof coordinate === 'number' && Number.isFinite(coordinate) ? coordinate : fallbackY;
        const boundedY = Math.max(14, Math.min(chartHeight - 58, rawY));
        const slotY = usedSlots.reduce((candidate, used) => (
          Math.abs(candidate - used) < 22 ? used + 22 : candidate
        ), boundedY);
        usedSlots.push(slotY);
        return {
          key: group.key,
          label: group.label,
          fullLabel: group.fullLabel,
          price,
          delta: price - latestPrice,
          top: `${Math.max(14, Math.min(chartHeight - 58, slotY))}px`,
          color: SERIES_COLORS[group.order % SERIES_COLORS.length] || '#3b82f6',
          active: group.key === hoveredOutcomeKey || group.key === selectedGroup?.key,
        };
      })
      .filter((row): row is {
        key: string;
        label: string;
        fullLabel: string;
        price: number;
        delta: number;
        top: string;
        color: string;
        active: boolean;
      } => Boolean(row))
      .slice(0, maxRows);
  }, [hoverInspect, hoveredOutcomeKey, selectedGroup?.key, tooltipMode, visibleOutcomeGroups]);
  const managerOutcomes = useMemo(() => {
    const query = outcomeManagerQuery.trim().toLowerCase();
    return sortedOutcomes.filter((group) => {
      if (!query) return true;
      return `${group.fullLabel} ${group.label} ${group.tokenId || ''} ${group.tokenSide}`.toLowerCase().includes(query);
    });
  }, [outcomeManagerQuery, sortedOutcomes]);
  const hiddenOutcomeCount = effectiveHiddenOutcomeKeys.length;
  const pinnedOutcomeCount = effectivePinnedOutcomeKeys.length;
  const markers = useMemo(() => signals.map((signal) => markerPosition(signal, primaryPoints, visibleLogicalRange)).filter(Boolean), [primaryPoints, signals, visibleLogicalRange]);
  const focusedMarkers = markers.filter((marker) => marker?.signal.tradeId === selectedTradeId);
  const selectedTradeSignals = useMemo(() => (
    selectedTradeId ? signals.filter((signal) => signal.tradeId === selectedTradeId).sort((left, right) => left.timestamp - right.timestamp) : []
  ), [selectedTradeId, signals]);
  const selectedTradeEntrySignal = selectedTradeSignals.find((signal) => signal.action === 'OPEN' || signal.action === 'BUY') || selectedTradeSignals[0] || null;
  const selectedTradeExitSignal = selectedTradeSignals.find((signal) => signal.action === 'CLOSE' || signal.action === 'SELL') || selectedTradeSignals[selectedTradeSignals.length - 1] || null;
  const selectedTradeFocus = useMemo(() => {
    if (!selectedTradeId || !selectedTradeSignals.length || !primaryPoints.length) return null;
    const startSignal = selectedTradeEntrySignal || selectedTradeSignals[0];
    const endSignal = selectedTradeExitSignal || selectedTradeSignals[selectedTradeSignals.length - 1];
    if (!startSignal || !endSignal) return null;
    const startIndex = nearestPointIndex(primaryPoints, startSignal.timestamp);
    const endIndex = nearestPointIndex(primaryPoints, endSignal.timestamp);
    if (startIndex < 0 || endIndex < 0) return null;
    const leftIndex = Math.min(startIndex, endIndex);
    const rightIndex = Math.max(startIndex, endIndex);
    const entryPoint = primaryPoints[startIndex] || null;
    const exitPoint = primaryPoints[endIndex] || null;
    const entryLeftPercent = axisPercentFromIndex(startIndex, primaryPoints, visibleLogicalRange);
    const exitLeftPercent = axisPercentFromIndex(endIndex, primaryPoints, visibleLogicalRange);
    const left = axisPercentFromIndex(leftIndex, primaryPoints, visibleLogicalRange);
    const right = axisPercentFromIndex(rightIndex, primaryPoints, visibleLogicalRange);
    const entryLeft = axisPercentStyle(entryLeftPercent);
    const exitLeft = axisPercentStyle(exitLeftPercent);
    const bandLeft = axisPercentStyle(left);
    const bandRight = axisPercentStyle(right);
    const pnl = (endSignal.price - startSignal.price) * (startSignal.size || endSignal.size || 0);
    if (!entryLeft || !exitLeft || !bandLeft || !bandRight || left === null || right === null) return null;
    return {
      id: selectedTradeId,
      startSignal,
      endSignal,
      startIndex,
      endIndex,
      entryPoint,
      exitPoint,
      entryLeft,
      exitLeft,
      bandLeft,
      bandWidth: `${Math.max(0.15, Math.min(100, Math.abs(right - left)))}%`,
      pnl,
      bars: Math.abs(endIndex - startIndex),
    };
  }, [primaryPoints, selectedTradeEntrySignal, selectedTradeExitSignal, selectedTradeId, selectedTradeSignals, visibleLogicalRange]);
  const rangeSelectionStyle = rangeSelection && containerRef.current ? {
    left: `${Math.min(rangeSelection.startX, rangeSelection.currentX) - containerRef.current.getBoundingClientRect().left}px`,
    width: `${Math.abs(rangeSelection.currentX - rangeSelection.startX)}px`,
  } : undefined;
  const blockAxisStyle = blockAxisTop === null ? undefined : { top: `${blockAxisTop}px` };
  const rangeSelectionHudStyle = rangeSelection && containerRef.current ? {
    left: `${Math.min(
      Math.max(142, Math.min(rangeSelection.startX, rangeSelection.currentX) - containerRef.current.getBoundingClientRect().left + (Math.abs(rangeSelection.currentX - rangeSelection.startX) / 2)),
      Math.max(142, containerRef.current.getBoundingClientRect().width - 142),
    )}px`,
  } : undefined;
  const rangeSelectionMeta = useMemo(() => {
    if (!rangeSelection || !primaryPoints.length) return null;
    const from = logicalIndexFromClientX(Math.min(rangeSelection.startX, rangeSelection.currentX));
    const to = logicalIndexFromClientX(Math.max(rangeSelection.startX, rangeSelection.currentX));
    return rangeMetaFromLogical(from, to);
  }, [primaryPoints, rangeSelection, visibleLogicalRange]);
  const blockAxisTicks = useMemo(() => {
    if (!priceSource.includes('block') || !primaryPoints.length) return [];
    const axisWidth = Math.max(720, Math.floor((containerRef.current?.clientWidth || 1100) - 68));
    const logicalRangeLooksValid = Boolean(
      visibleLogicalRange
      && visibleLogicalRange.to > visibleLogicalRange.from
      && visibleLogicalRange.from >= -primaryPoints.length * 0.1
      && visibleLogicalRange.to <= primaryPoints.length * 1.15,
    );
    const range = logicalRangeLooksValid && visibleLogicalRange
      ? visibleLogicalRange
      : { from: 0, to: Math.max(0, primaryPoints.length - 1) };
    const fromIndex = Math.max(0, Math.min(primaryPoints.length - 1, Math.floor(range.from)));
    const toIndex = Math.max(fromIndex, Math.min(primaryPoints.length - 1, Math.ceil(range.to)));
    const span = Math.max(1, toIndex - fromIndex);
    const targetLabelCount = Math.floor(axisWidth / 168);
    const count = Math.min(11, Math.max(5, targetLabelCount));
    const seen = new Set<number>();
    return Array.from({ length: count }, (_, tickIndex) => {
      const index = Math.round(fromIndex + (span * tickIndex) / Math.max(1, count - 1));
      const point = primaryPoints[index];
      if (!point) return null;
      const block = Math.floor(point.timestamp);
      if (seen.has(block)) return null;
      seen.add(block);
      const fallbackLeft = `${(tickIndex / Math.max(1, count - 1)) * 100}%`;
      const coordinate = chartRef.current?.timeScale().timeToCoordinate(chartTime(point));
      const coordinateLeft = typeof coordinate === 'number' && Number.isFinite(coordinate)
        ? `${Math.max(0, Math.min(axisWidth, coordinate))}px`
        : null;
      return {
        key: `${block}-${tickIndex}`,
        block,
        index,
        left: coordinateLeft || (logicalRangeLooksValid
          ? axisPercentStyle(axisPercentFromIndex(index, primaryPoints, visibleLogicalRange)) || fallbackLeft
          : fallbackLeft),
        className: `${tickIndex === 0 ? 'start ' : ''}${tickIndex === count - 1 ? 'end ' : ''}${tickIndex % 2 ? 'level-1 ' : ''}major`,
      };
    }).filter((tick): tick is { key: string; block: number; index: number; left: string; className: string } => Boolean(tick?.left));
  }, [priceSource, primaryPoints, visibleLogicalRange]);
  const blockAxisMinorTicks = useMemo(() => {
    if (!blockAxisTicks.length || !priceSource.includes('block')) return [];
    const minorTicks: Array<{ key: string; left: string; level: string }> = [];
    blockAxisTicks.slice(0, -1).forEach((tick, tickIndex) => {
      const next = blockAxisTicks[tickIndex + 1];
      if (!next) return;
      const span = next.index - tick.index;
      if (span <= 1) return;
      const divisions = span > 260 ? 5 : span > 120 ? 4 : 3;
      for (let division = 1; division < divisions; division += 1) {
        const index = Math.round(tick.index + (span * division) / divisions);
        if (index <= tick.index || index >= next.index) continue;
        const point = primaryPoints[index];
        const coordinate = point ? chartRef.current?.timeScale().timeToCoordinate(chartTime(point)) : null;
        const left = typeof coordinate === 'number' && Number.isFinite(coordinate)
          ? `${Math.max(0, Math.min(Math.max(720, Math.floor((containerRef.current?.clientWidth || 1100) - 68)), coordinate))}px`
          : axisPercentStyle(axisPercentFromIndex(index, primaryPoints, visibleLogicalRange));
        if (!left) continue;
        minorTicks.push({
          key: `${tick.key}-minor-${division}`,
          left,
          level: division === Math.floor(divisions / 2) ? 'mid' : 'minor',
        });
      }
    });
    return minorTicks;
  }, [blockAxisTicks, priceSource, primaryPoints, visibleLogicalRange]);
  const blockScaleSummaryTicks = useMemo(() => {
    if (!priceSource.includes('block') || !primaryPoints.length) return [];
    const count = Math.min(8, Math.max(4, Math.floor(primaryPoints.length / 140) + 4));
    const seen = new Set<number>();
    return Array.from({ length: count }, (_, tickIndex) => {
      const index = Math.round((Math.max(0, primaryPoints.length - 1) * tickIndex) / Math.max(1, count - 1));
      const block = Math.floor(primaryPoints[index]?.timestamp || 0);
      if (!block || seen.has(block)) return null;
      seen.add(block);
      return block;
    }).filter((block): block is number => Boolean(block));
  }, [priceSource, primaryPoints]);
  const visibleWindowMeta = useMemo(() => {
    if (!primaryPoints.length) return null;
    const tickBlocks = blockAxisTicks.map((tick) => tick.block).filter((block) => Number.isFinite(block));
    const range = visibleLogicalRange && visibleLogicalRange.to > visibleLogicalRange.from
      ? visibleLogicalRange
      : { from: 0, to: Math.max(0, primaryPoints.length - 1) };
    const fromIndex = Math.max(0, Math.min(primaryPoints.length - 1, Math.floor(range.from)));
    const toIndex = Math.max(fromIndex, Math.min(primaryPoints.length - 1, Math.ceil(range.to)));
    const fromPoint = primaryPoints[fromIndex];
    const toPoint = primaryPoints[toIndex];
    const fromBlock = tickBlocks.length ? Math.min(...tickBlocks) : Math.floor(fromPoint?.timestamp || 0);
    const toBlock = tickBlocks.length ? Math.max(...tickBlocks) : Math.floor(toPoint?.timestamp || 0);
    const rowCount = primaryPoints.filter((point) => point.timestamp >= fromBlock && point.timestamp <= toBlock).length || Math.max(1, toIndex - fromIndex + 1);
    const coverage = primaryPoints.length ? rowCount / primaryPoints.length : 0;
    const latestBlock = Math.floor(primaryPoints[primaryPoints.length - 1]?.timestamp || 0);
    const firstBlock = Math.floor(primaryPoints[0]?.timestamp || 0);
    const latestVisible = toBlock >= latestBlock;
    const firstVisible = fromBlock <= firstBlock;
    return {
      fromIndex,
      toIndex,
      fromBlock,
      toBlock,
      rowCount,
      coverage,
      latestVisible,
      firstVisible,
      span: Math.max(1, range.to - range.from),
    };
  }, [blockAxisTicks, primaryPoints, visibleLogicalRange]);
  const navigatorWindowStyle = useMemo(() => {
    if (!primaryPoints.length) return null;
    const fullSpan = Math.max(1, primaryPoints.length - 1);
    const range = visibleLogicalRange && visibleLogicalRange.to > visibleLogicalRange.from
      ? visibleLogicalRange
      : { from: 0, to: fullSpan };
    const left = Math.max(0, Math.min(100, (Math.max(0, range.from) / fullSpan) * 100));
    const right = Math.max(left, Math.min(100, (Math.min(fullSpan, range.to) / fullSpan) * 100));
    return {
      left: `${left}%`,
      width: `${Math.max(2.5, right - left)}%`,
    };
  }, [primaryPoints.length, visibleLogicalRange]);
  const navigatorDomainLabels = useMemo(() => {
    if (!priceSource.includes('block') || !primaryPoints.length) return [];
    const count = Math.min(3, Math.max(2, primaryPoints.length));
    const seen = new Set<number>();
    return Array.from({ length: count }, (_, index) => {
      const pointIndex = Math.round((Math.max(0, primaryPoints.length - 1) * index) / Math.max(1, count - 1));
      const block = Math.floor(primaryPoints[pointIndex]?.timestamp || 0);
      if (!block || seen.has(block)) return null;
      seen.add(block);
      return {
        key: `domain-${block}-${index}`,
        block,
        left: `${(index / Math.max(1, count - 1)) * 100}%`,
        className: `${index === 0 ? 'start ' : ''}${index === count - 1 ? 'end ' : ''}`,
      };
    }).filter((row): row is { key: string; block: number; left: string; className: string } => Boolean(row));
  }, [priceSource, primaryPoints]);

  function setChartVisibleRange(range: { from: number; to: number }, viewportMode: 'preset' | 'custom' = 'custom') {
    const next = clampLogicalRange(range, primaryPoints.length);
    chartRef.current?.timeScale().setVisibleLogicalRange(next);
    visibleLogicalRangeRef.current = next;
    setVisibleLogicalRange((current) => (logicalRangesClose(current, next) ? current : next));
    onViewportModeChange?.(viewportMode);
  }

  function moveNavigatorToClientX(clientX: number) {
    const box = chartSurfaceRef.current?.getBoundingClientRect();
    if (!box || primaryPoints.length < 2) return;
    const axisWidth = Math.max(120, box.width - 68);
    const ratio = Math.max(0, Math.min(1, (clientX - box.left) / axisWidth));
    const fullSpan = Math.max(1, primaryPoints.length - 1);
    const current = normalizeLogicalRange(chartRef.current?.timeScale().getVisibleLogicalRange() || null)
      || visibleLogicalRangeRef.current
      || { from: 0, to: fullSpan };
    const span = Math.max(8, Math.min(fullSpan, current.to - current.from));
    const center = ratio * fullSpan;
    setChartVisibleRange({ from: center - span / 2, to: center + span / 2 });
  }

  function startNavigatorDrag(event: PointerEvent) {
    if (primaryPoints.length < 2) return;
    event.preventDefault();
    event.stopPropagation();
    navigatorDragRef.current = true;
    moveNavigatorToClientX(event.clientX);
    window.addEventListener('pointermove', handleNavigatorDrag);
    window.addEventListener('pointerup', stopNavigatorDrag, { once: true });
    window.addEventListener('pointercancel', stopNavigatorDrag, { once: true });
  }

  function handleNavigatorDrag(event: PointerEvent) {
    if (!navigatorDragRef.current) return;
    event.preventDefault();
    moveNavigatorToClientX(event.clientX);
  }

  function stopNavigatorDrag() {
    navigatorDragRef.current = false;
    window.removeEventListener('pointermove', handleNavigatorDrag);
  }

  function fitData() {
    suppressViewportModeRef.current = true;
    chartRef.current?.timeScale().fitContent();
    visibleLogicalRangeRef.current = null;
    setVisibleLogicalRange(null);
    onViewportModeChange?.('preset');
    window.setTimeout(() => {
      suppressViewportModeRef.current = false;
    }, 0);
  }

  function zoomLogicalRange(factor: number) {
    const scale = chartRef.current?.timeScale();
    if (!scale) return;
    const range = scale.getVisibleLogicalRange();
    if (!range) return;
    const center = (range.from + range.to) / 2;
    const half = ((range.to - range.from) * factor) / 2;
    setChartVisibleRange({ from: center - half, to: center + half });
  }

  function panLogicalRange(direction: -1 | 1, ratio = 0.5) {
    const range = normalizeLogicalRange(chartRef.current?.timeScale().getVisibleLogicalRange() || null)
      || visibleLogicalRangeRef.current
      || { from: 0, to: Math.max(1, primaryPoints.length - 1) };
    const span = Math.max(1, range.to - range.from);
    const shift = span * ratio * direction;
    setChartVisibleRange({ from: range.from + shift, to: range.to + shift });
  }

  function jumpToChartEdge(edge: 'start' | 'latest') {
    if (!primaryPoints.length) return;
    const range = normalizeLogicalRange(chartRef.current?.timeScale().getVisibleLogicalRange() || null)
      || visibleLogicalRangeRef.current
      || { from: 0, to: Math.max(1, primaryPoints.length - 1) };
    const span = Math.max(8, Math.min(Math.max(1, primaryPoints.length - 1), range.to - range.from));
    if (edge === 'start') {
      setChartVisibleRange({ from: 0, to: span });
      return;
    }
    const last = Math.max(1, primaryPoints.length - 1);
    setChartVisibleRange({ from: last - span, to: last });
  }

  function logicalIndexFromClientX(clientX: number) {
    const box = containerRef.current?.getBoundingClientRect();
    if (!box) return 0;
    const ratio = Math.max(0, Math.min(1, (clientX - box.left) / box.width));
    const range = normalizeLogicalRange(chartRef.current?.timeScale().getVisibleLogicalRange() || null)
      || visibleLogicalRangeRef.current
      || { from: 0, to: Math.max(1, primaryPoints.length - 1) };
    return range.from + ratio * Math.max(1, range.to - range.from);
  }

  function rangeMetaFromLogical(from: number, to: number): RangeZoomMeta | null {
    if (!primaryPoints.length) return null;
    const fromIndex = Math.max(0, Math.min(primaryPoints.length - 1, Math.floor(Math.min(from, to))));
    const toIndex = Math.max(fromIndex, Math.min(primaryPoints.length - 1, Math.ceil(Math.max(from, to))));
    const fromBlock = Math.floor(primaryPoints[fromIndex]?.timestamp || 0);
    const toBlock = Math.floor(primaryPoints[toIndex]?.timestamp || fromBlock);
    return {
      fromIndex,
      toIndex,
      fromBlock,
      toBlock,
      pointCount: Math.max(1, toIndex - fromIndex + 1),
      spanBlocks: Math.max(0, toBlock - fromBlock),
    };
  }

  function commitRangeZoom(selection: RangeSelection) {
    if (!selection) return null;
    const left = Math.min(selection.startX, selection.currentX);
    const right = Math.max(selection.startX, selection.currentX);
    if (Math.abs(right - left) < 18) return null;
    const from = logicalIndexFromClientX(left);
    const to = logicalIndexFromClientX(right);
    const meta = rangeMetaFromLogical(from, to);
    setChartVisibleRange({ from, to });
    setRangeZoomNotice(meta);
    return meta;
  }

  function handleChartWheel(event: WheelEvent) {
    if (rangeZoomEnabled || primaryPoints.length < 2) return;
    event.preventDefault();
    event.stopPropagation();

    const scale = chartRef.current?.timeScale();
    const box = containerRef.current?.getBoundingClientRect();
    if (!scale || !box) return;
    const current = normalizeLogicalRange(scale.getVisibleLogicalRange()) || { from: 0, to: Math.max(1, primaryPoints.length - 1) };
    const span = Math.max(1, current.to - current.from);
    const isTrackpadPan = Math.abs(event.deltaX) > Math.abs(event.deltaY) || event.shiftKey;

    if (isTrackpadPan) {
      const delta = Math.abs(event.deltaX) > 0 ? event.deltaX : event.deltaY;
      const shift = (delta / Math.max(260, box.width)) * span;
      setChartVisibleRange({ from: current.from + shift, to: current.to + shift });
      return;
    }

    const cursorRatio = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
    const anchor = current.from + span * cursorRatio;
    const wheelMagnitude = Math.min(3, Math.max(0.25, Math.abs(event.deltaY) / 120));
    const zoomFactor = (event.deltaY > 0 ? 1.18 : 0.84) ** wheelMagnitude;
    const nextSpan = span * zoomFactor;
    setChartVisibleRange({
      from: anchor - nextSpan * cursorRatio,
      to: anchor + nextSpan * (1 - cursorRatio),
    });
  }

  const updatePinnedOutcomeKeys = (next: string[] | ((current: string[]) => string[])) => {
    const value = typeof next === 'function' ? next(effectivePinnedOutcomeKeys) : next;
    setInternalPinnedOutcomeKeys(value);
    onPinnedOutcomeKeysChange?.(value);
  };

  const updateHiddenOutcomeKeys = (next: string[] | ((current: string[]) => string[])) => {
    const value = typeof next === 'function' ? next(effectiveHiddenOutcomeKeys) : next;
    setInternalHiddenOutcomeKeys(value);
    onHiddenOutcomeKeysChange?.(value);
  };

  const updateSoloOutcomeKey = (next: string | ((current: string) => string)) => {
    const value = typeof next === 'function' ? next(effectiveSoloOutcomeKey) : next;
    setInternalSoloOutcomeKey(value);
    onSoloOutcomeKeyChange?.(value);
  };

  const togglePinnedOutcome = (key: string) => {
    updatePinnedOutcomeKeys((current) => (current.includes(key) ? current.filter((item) => item !== key) : [...current, key]));
    updateHiddenOutcomeKeys((current) => current.filter((item) => item !== key));
  };

  const toggleHiddenOutcome = (key: string) => {
    updateHiddenOutcomeKeys((current) => (current.includes(key) ? current.filter((item) => item !== key) : [...current, key]));
    updatePinnedOutcomeKeys((current) => current.filter((item) => item !== key));
    if (effectiveSoloOutcomeKey === key) updateSoloOutcomeKey('');
  };

  const resetOutcomeVisibility = () => {
    updateHiddenOutcomeKeys([]);
    updatePinnedOutcomeKeys([]);
    updateSoloOutcomeKey('');
    setShowLowProbability(false);
  };

  useEffect(() => {
    window.localStorage.setItem('polydata.quant.event.displayMode', displayMode);
    window.localStorage.setItem('polydata.quant.event.sortMode', eventSortMode);
    window.localStorage.setItem('polydata.quant.event.sideMode', compareMode);
    window.localStorage.setItem('polydata.quant.event.labelMode', labelMode);
    window.localStorage.setItem('polydata.quant.event.tooltipMode', tooltipMode);
    window.localStorage.setItem('polydata.quant.chart.viewMode', chartViewMode);
  }, [chartViewMode, compareMode, displayMode, eventSortMode, labelMode, tooltipMode]);

  useEffect(() => {
    window.localStorage.setItem('polydata.quant.chart.pinnedOutcomes', JSON.stringify(effectivePinnedOutcomeKeys));
    window.localStorage.setItem('polydata.quant.chart.hiddenOutcomes', JSON.stringify(effectiveHiddenOutcomeKeys));
  }, [effectiveHiddenOutcomeKeys, effectivePinnedOutcomeKeys]);

  useEffect(() => {
    window.localStorage.setItem('polymonitor.quant.dataWindowSettings', JSON.stringify(dataWindowSettings));
  }, [dataWindowSettings]);

  useEffect(() => {
    const syncBlockAxisTop = () => {
      const surface = chartSurfaceRef.current;
      const region = surface?.closest('.qtv-chart-region');
      const surfaceBox = surface?.getBoundingClientRect();
      const regionBox = region?.getBoundingClientRect();
      if (!surfaceBox || !regionBox) return;
      const nextTop = Math.max(0, Math.round(regionBox.bottom - surfaceBox.top - 48));
      setBlockAxisTop((current) => (current === nextTop ? current : nextTop));
    };
    syncBlockAxisTop();
    const surface = chartSurfaceRef.current;
    const region = surface?.closest('.qtv-chart-region');
    const surfaceObserver = surface ? new ResizeObserver(syncBlockAxisTop) : null;
    const regionObserver = region ? new ResizeObserver(syncBlockAxisTop) : null;
    if (surface) surfaceObserver?.observe(surface);
    if (region) regionObserver?.observe(region);
    window.addEventListener('resize', syncBlockAxisTop);
    const timer = window.setTimeout(syncBlockAxisTop, 300);
    return () => {
      surfaceObserver?.disconnect();
      regionObserver?.disconnect();
      window.removeEventListener('resize', syncBlockAxisTop);
      window.clearTimeout(timer);
    };
  }, [allPoints.length, market.slug, priceSource]);

  useEffect(() => {
    const onPointerMove = (event: PointerEvent) => {
      const drag = dataWindowDragRef.current;
      if (!drag) return;
      const bounds = containerRef.current?.parentElement?.getBoundingClientRect();
      const next = clampDataWindowPosition(
        drag.baseX + event.clientX - drag.startX,
        drag.baseY + event.clientY - drag.startY,
        bounds,
      );
      setDataWindowSettings((current) => ({ ...current, dock: 'floating', x: next.x, y: next.y }));
    };
    const onPointerUp = () => {
      dataWindowDragRef.current = null;
    };
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
    window.addEventListener('pointercancel', onPointerUp);
    return () => {
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
      window.removeEventListener('pointercancel', onPointerUp);
    };
  }, []);

  useEffect(() => {
    pointsRef.current = primaryPoints;
  }, [primaryPoints]);

  useEffect(() => {
    visibleOutcomeGroupsRef.current = visibleOutcomeGroups;
  }, [visibleOutcomeGroups]);

  useEffect(() => {
    onViewportModeChangeRef.current = onViewportModeChange;
  }, [onViewportModeChange]);

  useEffect(() => {
    onOutcomeHoverRef.current = onOutcomeHover;
  }, [onOutcomeHover]);

  useEffect(() => {
    onVisibleWindowChangeRef.current = onVisibleWindowChange;
  }, [onVisibleWindowChange]);

  useEffect(() => {
    if (!selectedTradeFocus || !chartRef.current || !primaryPoints.length) return;
    const focusKey = `${selectedTradeFocus.id}|${selectedTradeFocus.startIndex}|${selectedTradeFocus.endIndex}|${primaryPoints.length}`;
    if (lastTradeFocusKeyRef.current === focusKey) return;
    lastTradeFocusKeyRef.current = focusKey;
    const span = Math.max(1, Math.abs(selectedTradeFocus.endIndex - selectedTradeFocus.startIndex));
    const padding = Math.max(8, Math.ceil(span * 0.45), Math.ceil(primaryPoints.length * 0.018));
    suppressViewportModeRef.current = true;
    chartRef.current.timeScale().setVisibleLogicalRange({
      from: Math.max(0, Math.min(selectedTradeFocus.startIndex, selectedTradeFocus.endIndex) - padding),
      to: Math.min(primaryPoints.length - 1, Math.max(selectedTradeFocus.startIndex, selectedTradeFocus.endIndex) + padding),
    });
    setPinnedPoint(selectedTradeFocus.entryPoint || nearestPoint(primaryPoints, selectedTradeFocus.startSignal.timestamp));
    onViewportModeChangeRef.current?.('custom');
    window.setTimeout(() => {
      suppressViewportModeRef.current = false;
    }, 0);
  }, [primaryPoints, selectedTradeFocus]);

  useEffect(() => {
    const raw = window.localStorage.getItem(drawingStorageKey(market, priceSource));
    if (!raw) {
      setDrawings([]);
      return;
    }
    try {
      const parsed = JSON.parse(raw) as Drawing[];
      setDrawings(Array.isArray(parsed) ? parsed : []);
    } catch {
      setDrawings([]);
    }
  }, [market.slug, priceSource]);

  useEffect(() => {
    window.localStorage.setItem(drawingStorageKey(market, priceSource), JSON.stringify(drawings));
  }, [drawings, market, priceSource]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (rangeSelection || rangeZoomEnabled || rangeZoomNotice) {
          setRangeSelection(null);
          setRangeZoomEnabled(false);
          setRangeZoomNotice(null);
          return;
        }
        if (document.activeElement instanceof HTMLElement && document.activeElement.closest('.qtv-data-window')) {
          setPinnedPoint(null);
          setDataWindowSettings((current) => ({ ...current, visible: false, minimized: false }));
        } else {
          setPinnedPoint(null);
        }
        setPendingDrawing(null);
      }
      if (event.key.toLowerCase() === 'd' && !(event.target instanceof HTMLInputElement)) {
        event.preventDefault();
        if (event.shiftKey) {
          if (hover) {
            setPinnedPoint(hover);
            setDataWindowSettings((current) => ({ ...current, visible: true, minimized: false }));
          } else {
            setPinnedPoint(null);
          }
          return;
        }
        setDataWindowSettings((current) => ({ ...current, visible: !current.visible, minimized: false }));
      }
      if (event.key.toLowerCase() === 'f' && !(event.target instanceof HTMLInputElement)) {
        event.preventDefault();
        fitData();
      }
      if (event.key.toLowerCase() === 'z' && !(event.target instanceof HTMLInputElement)) {
        event.preventDefault();
        setRangeSelection(null);
        setRangeZoomNotice(null);
        setRangeZoomEnabled((current) => !current);
      }
      if ((event.key === '+' || event.key === '=') && !(event.target instanceof HTMLInputElement)) {
        event.preventDefault();
        zoomLogicalRange(0.72);
      }
      if (event.key === '-' && !(event.target instanceof HTMLInputElement)) {
        event.preventDefault();
        zoomLogicalRange(1.35);
      }
      if (event.key === 'ArrowLeft' && !(event.target instanceof HTMLInputElement)) {
        event.preventDefault();
        panLogicalRange(-1, event.shiftKey ? 0.9 : 0.45);
      }
      if (event.key === 'ArrowRight' && !(event.target instanceof HTMLInputElement)) {
        event.preventDefault();
        panLogicalRange(1, event.shiftKey ? 0.9 : 0.45);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [rangeSelection, rangeZoomEnabled, rangeZoomNotice]);

  useEffect(() => {
    if (!replayPlaying || !replayEnabled || !rawAllPoints.length) return undefined;
    const timer = window.setInterval(() => {
      setReplayIndex((current) => Math.min(rawAllPoints.length - 1, (current ?? 0) + 1));
    }, 700);
    return () => window.clearInterval(timer);
  }, [rawAllPoints.length, replayEnabled, replayPlaying]);

  useEffect(() => {
    if (!replayEnabled) return;
    if (replayIndex === null && rawAllPoints.length) setReplayIndex(Math.floor(rawAllPoints.length * 0.25));
  }, [rawAllPoints.length, replayEnabled, replayIndex]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;

    const chart = createChart(container, {
      autoSize: false,
      height: Math.max(300, container.clientHeight || 420),
      width: Math.max(360, container.clientWidth || 760),
      layout: {
        attributionLogo: false,
        background: { type: ColorType.Solid, color: '#0b0d10' },
        textColor: '#9ca3af',
        fontFamily: 'var(--font-mono)',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.055)' },
        horzLines: { color: 'rgba(255,255,255,0.075)' },
      },
      rightPriceScale: {
        borderColor: 'rgba(148,163,184,0.18)',
        scaleMargins: { top: 0.08, bottom: 0.22 },
      },
      timeScale: {
        visible: true,
        borderColor: 'rgba(148,163,184,0.18)',
        fixLeftEdge: true,
        fixRightEdge: true,
        secondsVisible: false,
        timeVisible: false,
        rightOffset: 2,
        barSpacing: 5,
        tickMarkFormatter: (time: Time) => priceSource.includes('block') ? blockLabel(Number(time)) : formatTime(Number(time)),
      },
      localization: {
        priceFormatter: (value: number) => fmtProbabilityPercent(value),
        timeFormatter: (time: Time) => priceSource.includes('block') ? `block ${blockLabel(Number(time))}` : formatTime(Number(time)),
      },
      crosshair: {
        horzLine: { color: 'rgba(148,163,184,0.42)', labelBackgroundColor: '#1f2937' },
        vertLine: { color: 'rgba(148,163,184,0.28)', labelBackgroundColor: '#1f2937' },
      },
      handleScroll: {
        mouseWheel: false,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: false,
      },
      handleScale: {
        axisPressedMouseMove: true,
        mouseWheel: false,
        pinch: true,
      },
    });
    const ma = chart.addSeries(LineSeries, {
      color: '#f59e0b',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      title: 'MA',
    });
    const volume = chart.addSeries(HistogramSeries, {
      color: 'rgba(148,163,184,0.24)',
      priceFormat: { type: 'volume' },
      priceScaleId: '',
      priceLineVisible: false,
      lastValueVisible: false,
    });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    seriesRef.current = { lines: new Map(), ma, volume, kind: chartType };
    chartRef.current = chart;

    chart.subscribeCrosshairMove((param) => {
      const time = Number(param.time);
      if (!Number.isFinite(time)) {
        setHover(null);
        setHoveredOutcomeKey('');
        onOutcomeHoverRef.current?.('');
        return;
      }
      const groups = visibleOutcomeGroupsRef.current;
      const pointerY = Number(param.point?.y);
      const match = groups.reduce<{ group: OutcomeGroup; point: PricePoint; score: number } | null>((best, group) => {
        const line = seriesRef.current.lines.get(group.key);
        const exactPoint = group.points.find((candidate) => Math.floor(candidate.timestamp) === Math.floor(time));
        const point = exactPoint || nearestPoint(group.points, time);
        if (!point) return best;
        const lineWithCoordinate = line as unknown as { priceToCoordinate?: (price: number) => number | null };
        const y = lineWithCoordinate.priceToCoordinate?.(point.close);
        const timestampPenalty = Math.min(30, Math.abs(point.timestamp - time) / 250);
        const exactBonus = exactPoint ? -4 : 0;
        const yScore = Number.isFinite(pointerY) && typeof y === 'number' && Number.isFinite(y)
          ? Math.abs(y - pointerY)
          : Math.abs(point.timestamp - time) / 1000;
        const score = yScore + timestampPenalty + exactBonus;
        if (!best || score < best.score) return { group, point, score };
        return best;
      }, null);
      if (match) {
        setHover(match.point);
        setHoveredOutcomeKey(match.group.key);
        onOutcomeHoverRef.current?.(match.group.key);
        return;
      }
      const currentPoints = pointsRef.current;
      const point = nearestPoint(currentPoints, time);
      setHover(point);
      setHoveredOutcomeKey('');
      onOutcomeHoverRef.current?.('');
    });

    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return;
      chart.resize(Math.max(360, Math.floor(entry.contentRect.width)), Math.max(300, Math.floor(entry.contentRect.height)));
    });
    observer.observe(container);

    const handleVisibleLogicalRange = (range: { from: number; to: number } | null) => {
      const normalized = normalizeLogicalRange(range);
      visibleLogicalRangeRef.current = normalized;
      setVisibleLogicalRange((current) => (logicalRangesClose(current, normalized) ? current : normalized));
      if (suppressViewportModeRef.current || !normalized || pointsRef.current.length < 2) return;
      const span = Math.max(0, normalized.to - normalized.from);
      const fullSpan = Math.max(1, pointsRef.current.length - 1);
      const coversFullDataset = normalized.from <= 1.5 && normalized.to >= fullSpan - 1.5 && span >= fullSpan - 3;
      onViewportModeChange?.(coversFullDataset ? 'preset' : 'custom');
      if (!coversFullDataset && onVisibleWindowChangeRef.current) {
        const currentPoints = pointsRef.current;
        const fromIndex = Math.max(0, Math.min(currentPoints.length - 1, Math.floor(normalized.from)));
        const toIndex = Math.max(0, Math.min(currentPoints.length - 1, Math.ceil(normalized.to)));
        const fromPoint = currentPoints[fromIndex];
        const toPoint = currentPoints[toIndex];
        const fromX = Number(fromPoint?.timestamp);
        const toX = Number(toPoint?.timestamp);
        if (Number.isFinite(fromX) && Number.isFinite(toX)) {
          const nextKey = `${Math.floor(fromX)}:${Math.ceil(toX)}:${toIndex - fromIndex}`;
          if (nextKey !== lastWindowNotifyRef.current) {
            lastWindowNotifyRef.current = nextKey;
            const width = Math.max(360, Math.floor(containerRef.current?.clientWidth || 1200));
            onVisibleWindowChangeRef.current({
              fromX,
              toX,
              pointCount: Math.max(1, toIndex - fromIndex + 1),
              viewportWidth: width,
            });
          }
        }
      }
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(handleVisibleLogicalRange);

    return () => {
      observer.disconnect();
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(handleVisibleLogicalRange);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = { lines: new Map(), ma: null, volume: null, kind: chartType };
    };
  }, [chartType, priceSource, scaleMode]);

  useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    if (!chart || !series.ma || !series.volume) return;
    const candleGroup = selectedGroup || visibleOutcomeGroups[0] || null;
    const renderGroups = chartType === 'Candles' && candleGroup ? [candleGroup] : visibleOutcomeGroups;
    const activeKeys = new Set(renderGroups.map((group) => group.key));
    if (series.kind !== chartType) {
      Array.from(series.lines.values()).forEach((line) => chart.removeSeries(line));
      series.lines.clear();
      series.kind = chartType;
    }
    Array.from(series.lines.entries()).forEach(([key, line]) => {
      if (!activeKeys.has(key)) {
        chart.removeSeries(line);
        series.lines.delete(key);
      }
    });
    renderGroups.forEach((group, index) => {
      const autoscaleInfoProvider = scaleProvider(scaleMode, group.points, allPoints);
      const baseColor = SERIES_COLORS[group.order % SERIES_COLORS.length] || '#3b82f6';
      const isSelected = selectedGroup?.key === group.key;
      const isTopLabel = index < 3;
      const opacity = !eventMode || isSelected ? 1 : displayMode === 'all' && index >= 5 ? 0.32 : 0.82;
      const lineWidth = !eventMode ? 2 : isSelected ? 3 : displayMode === 'all' && index >= 5 ? 1 : 2;
      const showPriceLine = !eventMode
        || isSelected
        || labelMode === 'all'
        || (labelMode === 'top' && isTopLabel);
      let line = series.lines.get(group.key);
      if (!line) {
        if (chartType === 'Area') {
          line = chart.addSeries(AreaSeries, {
            lineColor: colorWithOpacity(baseColor, opacity),
            topColor: colorWithOpacity(baseColor, 0.18),
            bottomColor: colorWithOpacity(baseColor, 0.01),
            lineWidth,
            priceLineVisible: showPriceLine,
            priceLineColor: baseColor,
            priceLineWidth: 1,
            title: group.fullLabel,
            autoscaleInfoProvider,
          });
        } else if (chartType === 'Candles') {
          line = chart.addSeries(CandlestickSeries, {
            upColor: '#10b981',
            downColor: '#ef4444',
            borderUpColor: '#34d399',
            borderDownColor: '#fb7185',
            wickUpColor: '#34d399',
            wickDownColor: '#fb7185',
            priceLineVisible: showPriceLine,
            priceLineColor: baseColor,
            priceLineWidth: 1,
            title: group.fullLabel,
            autoscaleInfoProvider,
          });
        } else {
          line = chart.addSeries(LineSeries, {
            color: colorWithOpacity(baseColor, opacity),
            lineWidth,
            lineType: chartType === 'Step line' ? LineType.WithSteps : LineType.Simple,
            priceLineVisible: showPriceLine,
            priceLineColor: baseColor,
            priceLineWidth: 1,
            title: group.fullLabel,
            autoscaleInfoProvider,
          });
        }
        series.lines.set(group.key, line);
      }
      if (chartType === 'Area') {
        (line as ISeriesApi<'Area'>).applyOptions({
          lineColor: colorWithOpacity(baseColor, opacity),
          topColor: colorWithOpacity(baseColor, 0.18),
          bottomColor: colorWithOpacity(baseColor, 0.01),
          lineWidth,
          priceLineVisible: showPriceLine,
          priceLineColor: baseColor,
          title: `${group.fullLabel} ${fmtProbabilityPercent(latestClose(group))}`,
          autoscaleInfoProvider,
        });
        (line as ISeriesApi<'Area'>).setData(areaData(group.points));
      } else if (chartType === 'Candles') {
        (line as ISeriesApi<'Candlestick'>).applyOptions({
          priceLineVisible: showPriceLine,
          priceLineColor: baseColor,
          title: `${group.fullLabel} ${fmtProbabilityPercent(latestClose(group))}`,
          autoscaleInfoProvider,
        });
        (line as ISeriesApi<'Candlestick'>).setData(candleData(group.points));
      } else {
        (line as ISeriesApi<'Line'>).applyOptions({
          color: colorWithOpacity(baseColor, opacity),
          lineWidth,
          lineType: chartType === 'Step line' ? LineType.WithSteps : LineType.Simple,
          priceLineVisible: showPriceLine,
          priceLineColor: baseColor,
          title: `${group.fullLabel} ${fmtProbabilityPercent(latestClose(group))}`,
          autoscaleInfoProvider,
        });
        (line as ISeriesApi<'Line'>).setData(lineData(group.points));
      }
    });
    series.ma.setData(indicatorMode.ma ? lineData(maPoints) : []);
    series.volume.setData(indicatorMode.volume ? volumeData(allPoints) : []);
    if (allPoints.length && lastFitRequestKeyRef.current !== fitRequestKey) {
      suppressViewportModeRef.current = true;
      chart.timeScale().fitContent();
      visibleLogicalRangeRef.current = null;
      setVisibleLogicalRange(null);
      lastFitRequestKeyRef.current = fitRequestKey;
      onViewportModeChange?.('preset');
      window.setTimeout(() => {
        suppressViewportModeRef.current = false;
      }, 0);
    }
  }, [allPoints, chartType, displayMode, eventMode, fitRequestKey, indicatorMode.ma, indicatorMode.volume, labelMode, maPoints, scaleMode, selectedGroup, visibleOutcomeGroups]);

  const drawingPointFromEvent = (event: MouseEvent) => {
    const box = containerRef.current?.getBoundingClientRect();
    if (!box || !primaryPoints.length) return null;
    const ratioX = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
    const ratioY = Math.max(0, Math.min(1, (event.clientY - box.top) / box.height));
    const index = Math.min(primaryPoints.length - 1, Math.max(0, Math.round(ratioX * (primaryPoints.length - 1))));
    const snapped = primaryPoints[index];
    return {
      timestamp: snapped?.timestamp ?? 0,
      price: clampProbability(1 - ratioY),
    };
  };

  const pointToScreen = (point: { timestamp: number; price: number }) => {
    const index = primaryPoints.findIndex((row) => Math.floor(row.timestamp) === Math.floor(point.timestamp));
    const x = axisPercentStyle(axisPercentFromIndex(index >= 0 ? index : 0, primaryPoints, visibleLogicalRange)) || '0%';
    const y = `${(1 - clampProbability(point.price)) * 100}%`;
    return { x, y };
  };

  const handleChartClick = (event: MouseEvent) => {
    if (activeTool === 'lock') {
      setDrawingsLocked((current) => !current);
      return;
    }
    if (activeTool === 'eye') {
      setDrawingsHidden((current) => !current);
      return;
    }
    if (activeTool === 'delete') {
      setDrawings([]);
      setPendingDrawing(null);
      return;
    }
    if (drawingsLocked) return;
    const point = drawingPointFromEvent(event);
    if (!point) return;
    if (activeTool === 'cursor' || activeTool === 'crosshair') {
      const hoveredGroup = visibleOutcomeGroups.find((group) => group.key === hoveredOutcomeKey);
      if (hoveredGroup?.tokenId) {
        onOutcomeSelect?.(hoveredGroup.tokenId, hoveredGroup.tokenSide === 'NO' ? 'NO' : 'YES');
      }
      setPinnedPoint(hover || nearestPoint(primaryPoints, point.timestamp));
      setDataWindowSettings((current) => ({ ...current, visible: true, minimized: false, mode: current.mode || 'compact' }));
      return;
    }
    if (activeTool === 'hline' || activeTool === 'vline') {
      setDrawings((current) => [...current, { id: `${activeTool}-${Date.now()}`, kind: activeTool, points: [point] }]);
      return;
    }
    if (activeTool === 'text') {
      const text = window.prompt('Text note', 'Note') || 'Note';
      setDrawings((current) => [...current, { id: `text-${Date.now()}`, kind: 'text', points: [point], text }]);
      return;
    }
    if (['trend', 'ray', 'measure'].includes(activeTool)) {
      if (!pendingDrawing || pendingDrawing.kind !== activeTool) {
        setPendingDrawing({ id: `${activeTool}-${Date.now()}`, kind: activeTool as Drawing['kind'], points: [point] });
        return;
      }
      setDrawings((current) => [...current, { ...pendingDrawing, points: [...pendingDrawing.points, point] }]);
      setPendingDrawing(null);
    }
  };

  const exportLoadedCsv = () => {
    const rows = allPoints.map((point) => [
      point.timestamp,
      point.outcomeLabel || '',
      point.yesPrice ?? point.close,
      point.noPrice ?? '',
      point.noPriceKind || '',
      point.volume,
      point.source,
    ]);
    const csv = [
      ['block_or_time', 'outcome', 'yes', 'no', 'no_kind', 'volume', 'source'].join(','),
      ...rows.map((row) => row.map((value) => JSON.stringify(String(value ?? ''))).join(',')),
    ].join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `polydata-${market.slug || 'quant'}-prices.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const exportSnapshot = () => {
    const canvas = containerRef.current?.querySelector('canvas');
    if (!(canvas instanceof HTMLCanvasElement)) return;
    const link = document.createElement('a');
    link.href = canvas.toDataURL('image/png');
    link.download = `polydata-${market.slug || 'quant'}-chart.png`;
    link.click();
  };

  const hasLoadedPrices = allPoints.length > 0;
  const isLoadingPrices = ['loading', 'metadata_loading', 'price_loading', 'warming', 'partial'].includes(dataStatus) && !hasLoadedPrices;
  const rowsText = isLoadingPrices
    ? 'Loading...'
    : hasLoadedPrices
      ? allPoints.length.toLocaleString('en-US')
      : dataStatus === 'empty'
        ? 'No price rows'
        : '--';
  const selectedText = hasLoadedPrices && selectedGroup ? `${selectedGroup.fullLabel} ${fmtPrice(selectedLatest)}` : '--';
  const sumText = eventMode && hasLoadedPrices ? fmtPrice(chartViewMode === 'normalized' ? 1 : allYesSum) : '--';
  const visibleSumText = eventMode && hasLoadedPrices ? fmtPrice(visibleYesSum) : '--';
  const latestPriceText = latest && hasLoadedPrices ? fmtPrice(latest.close) : '--';
  const loadingTitle = dataStatus === 'metadata_loading'
    ? 'Loading market metadata'
    : dataStatus === 'price_loading'
      ? eventMode ? 'Loading event price series' : 'Loading market price series'
      : dataStatus === 'warming'
        ? 'Building historical price tile'
      : dataStatus === 'partial'
        ? 'Partial coverage loaded'
        : dataStatus === 'error'
          ? 'Price request failed'
          : dataStatus === 'empty'
            ? 'No price rows found'
            : 'Waiting for real price rows';
  const updateDataWindow = (patch: Partial<DataWindowSettings>) => {
    setDataWindowSettings((current) => ({ ...current, ...patch }));
  };
  const startDataWindowDrag = (event: PointerEvent) => {
    if ((event.target as HTMLElement | null)?.closest('button')) return;
    event.preventDefault();
    const base = clampDataWindowPosition(dataWindowSettings.x ?? 12, dataWindowSettings.y ?? 122, containerRef.current?.parentElement?.getBoundingClientRect());
    dataWindowDragRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      baseX: base.x,
      baseY: base.y,
    };
    setDataWindowSettings((current) => ({ ...current, dock: 'floating', x: base.x, y: base.y }));
  };
  const dataWindowStyle = dataWindowSettings.dock === 'floating'
    ? {
      left: `${dataWindowSettings.x ?? 12}px`,
      top: `${dataWindowSettings.y ?? 122}px`,
    }
    : undefined;
  const marketMetadataRows: Array<[string, string]> = [
    ['Condition', market.conditionId],
    ['YES token', market.yesTokenId],
    ['NO token', market.noTokenId],
    ['Resolution', market.resolutionTime || market.endTime],
    ['Outcome', market.resolvedOutcome],
    ['Liquidity', market.liquidity],
    ['Volume', market.volume],
  ].filter((row): row is [string, string] => Boolean(row[1] && row[1] !== '--'));

  return (
    <section className="qtv-chart-shell">
      <aside className="qtv-draw-rail" aria-label="Chart drawing tools">
        {DRAW_TOOLS.map(([id, label, path]) => (
          <button
            key={id}
            className={activeTool === id ? 'active' : ''}
            type="button"
            title={label}
            aria-label={label}
            onClick={() => setActiveTool(id)}
          >
            <ToolIcon path={path} />
          </button>
        ))}
      </aside>

      <div className="qtv-chart-stack">
        <div className="qtv-advanced-toolbar" aria-label="Advanced chart controls">
          <div className="qtv-toolbar-group">
            <span>Chart</span>
            <select value={chartType} onChange={(event) => setChartType(event.currentTarget.value as ChartType)}>
              <option>Block-close line</option>
              <option>Line</option>
              <option>Step line</option>
              <option>Area</option>
              <option>Candles</option>
            </select>
            <button type="button" title="Fit visible data (F)" onClick={fitData}>Fit</button>
            <button type="button" title="Zoom in (+)" onClick={() => zoomLogicalRange(0.72)}>+</button>
            <button type="button" title="Zoom out (-)" onClick={() => zoomLogicalRange(1.35)}>-</button>
            <button
              className={rangeZoomEnabled ? 'active' : ''}
              type="button"
              title="Box zoom (Z). Drag across the chart or hold Shift/Alt while dragging."
              onClick={() => {
                setRangeZoomEnabled((current) => !current);
                setRangeSelection(null);
                setRangeZoomNotice(null);
              }}
            >
              Box
            </button>
          </div>
          <div className="qtv-toolbar-group">
            <span>Indicators</span>
            <button className={indicatorMode.ma ? 'active' : ''} type="button" title="Moving average" onClick={() => setIndicatorMode((current) => ({ ...current, ma: !current.ma }))}>MA</button>
            <button className={indicatorMode.ema ? 'active' : ''} type="button" title="EMA is queued for the next indicator pass" disabled>EMA</button>
            <button className={indicatorMode.bands ? 'active' : ''} type="button" title="Bollinger bands are queued for the next indicator pass" disabled>BB</button>
            <button className={indicatorMode.volume ? 'active' : ''} type="button" title="Volume" onClick={() => setIndicatorMode((current) => ({ ...current, volume: !current.volume }))}>Vol</button>
          </div>
          {eventMode ? (
            <div className="qtv-toolbar-group">
              <span>Outcomes</span>
              <select value={displayMode} onChange={(event) => setDisplayMode(event.currentTarget.value as EventDisplayMode)} title="Visible outcomes">
                <option value="top3">Top 3</option>
                <option value="top5">Top 5</option>
                <option value="top10">Top 10</option>
                <option value="all">All</option>
                <option value="selected">Selected</option>
              </select>
              <select value={eventSortMode} onChange={(event) => setEventSortMode(event.currentTarget.value as EventSortMode)} title="Outcome sort">
                <option value="probability">Probability</option>
                <option value="outcome">Outcome order</option>
                <option value="volume">Volume</option>
                <option value="change">Change</option>
              </select>
              <button
                className={outcomeManagerOpen ? 'active' : ''}
                type="button"
                title="Manage outcome lines"
                onClick={() => setOutcomeManagerOpen((current) => !current)}
              >
                Manage
              </button>
            </div>
          ) : null}
          <details className="qtv-toolbar-group qtv-display-menu">
            <summary>Display</summary>
            <div>
              <label>Side<select value={compareMode} onChange={(event) => setCompareMode(event.currentTarget.value as EventSideMode)}>
                <option value="auto">Auto side</option>
                <option value="yes">YES</option>
                <option value="no">NO</option>
                <option value="both">YES + NO</option>
              </select></label>
              {eventMode ? (
                <>
                  <label>Labels<select value={labelMode} onChange={(event) => setLabelMode(event.currentTarget.value as EventLabelMode)} title="Right labels">
                    <option value="selected">Selected labels</option>
                    <option value="top">Top labels</option>
                    <option value="all">All labels</option>
                  </select></label>
                  <label>Tooltip<select value={tooltipMode} onChange={(event) => setTooltipMode(event.currentTarget.value as TooltipMode)} title="Tooltip mode">
                    <option value="compact">Compact</option>
                    <option value="full">Full</option>
                  </select></label>
                  <label>View<select value={chartViewMode} onChange={(event) => setChartViewMode(event.currentTarget.value as ChartViewMode)} title="Price view mode">
                    <option value="raw">Raw probability</option>
                    <option value="normalized">Normalized event share</option>
                    <option value="direct">Direct only</option>
                    <option value="implied">Implied only</option>
                  </select></label>
                  <button className={showLowProbability ? 'active' : ''} type="button" title="Show low-probability outcomes" onClick={() => setShowLowProbability((current) => !current)}>Low probability</button>
                  <button type="button" title="Reset pinned, hidden, and solo outcome lines" onClick={resetOutcomeVisibility}>Reset lines</button>
                </>
              ) : null}
              <label>Layout<select value={layoutMode} onChange={(event) => setLayoutMode(event.currentTarget.value)} title="Layout">
                <option value="1">Single chart</option>
                <option value="2v">2 vertical</option>
                <option value="2h">2 horizontal</option>
                <option value="4">4 grid</option>
              </select></label>
            </div>
          </details>
          <div className="qtv-toolbar-group">
            <span>Actions</span>
            <button className={replayEnabled ? 'active' : ''} type="button" title="Replay" onClick={() => setReplayEnabled((current) => !current)}>Replay</button>
            <button type="button" title="Alert scaffold">Alert</button>
            <button type="button" title="Snapshot PNG" onClick={exportSnapshot}>Snapshot</button>
            <button type="button" title="Export loaded CSV" onClick={exportLoadedCsv}>CSV</button>
            <span className="qtv-data-control">
              <button
                className={dataWindowSettings.visible ? 'active' : ''}
                type="button"
                title="Data Window"
                onClick={() => {
                  updateDataWindow({ visible: !dataWindowSettings.visible, minimized: false });
                  setDataWindowMenuOpen(false);
                }}
              >
                Data{pinnedPoint ? ' •' : ''}
              </button>
              <button className={dataWindowMenuOpen ? 'active' : ''} type="button" title="Data Window menu" onClick={() => setDataWindowMenuOpen((current) => !current)}>▾</button>
              {dataWindowMenuOpen ? (
                <div className="qtv-data-menu">
                  <button type="button" onClick={() => updateDataWindow({ visible: true, minimized: false })}>Show Data Window</button>
                  <button type="button" onClick={() => updateDataWindow({ visible: false, minimized: false })}>Hide Data Window</button>
                  <button type="button" onClick={() => updateDataWindow({ mode: 'compact', visible: true, minimized: false })}>Compact</button>
                  <button type="button" onClick={() => updateDataWindow({ mode: 'expanded', visible: true, minimized: false })}>Expanded</button>
                  <button type="button" onClick={() => updateDataWindow({ dock: 'floating', visible: true, minimized: false })}>Floating</button>
                  <button type="button" onClick={() => updateDataWindow({ dock: 'left', visible: true, minimized: false })}>Dock left</button>
                  <button type="button" onClick={() => updateDataWindow({ dock: 'right', visible: true, minimized: false })}>Dock right</button>
                  <button type="button" onClick={() => setPinnedPoint(null)}>Clear pinned point</button>
                </div>
              ) : null}
            </span>
          </div>
        </div>

        <div className="qtv-chart-info">
          <div className="qtv-chart-meta">
            <strong>{market.title}</strong>
            <span>{market.category} · {eventMode ? `${displayedOutcomeCount} outcomes` : 'outcome probability'} · {priceSource}</span>
            <div className="qtv-market-metadata-strip" aria-label="Polymarket metadata">
              <span>view <b>{chartTypeCaption(chartType)}</b></span>
              {marketMetadataRows.map(([label, value]) => (
                <span key={label} title={`${label}: ${value}`}>
                  <em>{label}</em>
                  <b>{label.includes('token') || label === 'Condition' ? compactIdentifier(value) : value}</b>
                </span>
              ))}
            </div>
            <div className="qtv-indicator-legend">
              <span>Rows <b>{rowsText}</b></span>
              <span>Range <i>{hasLoadedPrices ? pointLabel(primaryPoints[0], priceSource) : '--'}</i> <em>{hasLoadedPrices ? pointLabel(primaryPoints[primaryPoints.length - 1], priceSource) : '--'}</em></span>
              <span>Volume <b>{hasLoadedPrices ? volumeTotal.toLocaleString('en-US', { maximumFractionDigits: 2 }) : '--'}</b></span>
              <span>Selected <b title={selectedGroup?.fullLabel}>{selectedText}</b></span>
              {eventMode ? (
                <span title="Binary market prices may not sum exactly to 100% due to spread, stale data, independent markets, fees, or direct/implied price differences.">
                  Sum <b className={sumWarning && hasLoadedPrices ? 'negative' : ''}>{sumText}</b>
                  <em>visible {visibleSumText}</em>
                </span>
              ) : null}
            </div>
            {blockScaleSummaryTicks.length ? (
              <div className="qtv-block-scale-inline" aria-label="Visible block scale summary">
                {blockScaleSummaryTicks.map((block) => (
                  <span key={`inline-${block}`}>{blockLabel(block)}</span>
                ))}
              </div>
            ) : null}
            {visibleWindowMeta ? (
              <div className="qtv-visible-window-control" aria-label="Visible block window">
                <button type="button" disabled={visibleWindowMeta.firstVisible} onClick={() => jumpToChartEdge('start')}>Start</button>
                <button type="button" onClick={() => panLogicalRange(-1, 0.42)}>Prev</button>
                <span>
                  <em>Window</em>
                  <b>{blockLabel(visibleWindowMeta.fromBlock)} to {blockLabel(visibleWindowMeta.toBlock)}</b>
                </span>
                <span>
                  <em>Rows</em>
                  <b>{visibleWindowMeta.rowCount.toLocaleString('en-US')} / {primaryPoints.length.toLocaleString('en-US')}</b>
                </span>
                <span className="meter">
                  <i style={{ width: `${Math.max(3, Math.round(visibleWindowMeta.coverage * 100))}%` }} />
                  <b>{Math.round(visibleWindowMeta.coverage * 100)}%</b>
                </span>
                <button type="button" onClick={() => panLogicalRange(1, 0.42)}>Next</button>
                <button type="button" disabled={visibleWindowMeta.latestVisible} onClick={() => jumpToChartEdge('latest')}>Latest</button>
                <button type="button" onClick={fitData}>Fit</button>
              </div>
            ) : null}
            {eventMode ? (
              <div className={`qtv-chart-view-strip ${chartViewSummary.warning ? 'has-warning' : ''}`}>
                <div className="qtv-view-mode-buttons" aria-label="Price view mode">
                  {(['raw', 'normalized', 'direct', 'implied'] as ChartViewMode[]).map((mode) => (
                    <button
                      key={mode}
                      className={chartViewMode === mode ? 'active' : ''}
                      type="button"
                      title={mode === 'raw' ? 'Raw stored probability' : mode === 'normalized' ? 'Normalized event share' : mode === 'direct' ? 'Direct observed rows only' : 'Implied complement rows only'}
                      onClick={() => setChartViewMode(mode)}
                    >
                      {mode}
                    </button>
                  ))}
                </div>
                <span title={chartViewSummary.detail}><b>{chartViewSummary.label}</b> view</span>
                <span>source <b>{chartViewSummary.sourceRows.toLocaleString('en-US')}</b></span>
                <span>mode rows <b>{chartViewSummary.filteredRows.toLocaleString('en-US')}</b> <em>{fmtProbabilityPercent(chartViewSummary.coverage, 0)}</em></span>
                <span>visible <b>{chartViewSummary.visibleRows.toLocaleString('en-US')}</b> <em>{fmtProbabilityPercent(chartViewSummary.visibleCoverage, 0)}</em></span>
                <span>direct <b>{chartViewSummary.sourceKinds.direct.toLocaleString('en-US')}</b></span>
                <span>implied <b>{chartViewSummary.sourceKinds.implied.toLocaleString('en-US')}</b></span>
                {chartViewMode === 'normalized' ? (
                  <>
                    <span>blocks <b>{chartViewSummary.normalizedBlockCount.toLocaleString('en-US')}</b></span>
                    <span>raw sum <b className={sumWarning && hasLoadedPrices ? 'negative' : ''}>{hasLoadedPrices ? fmtPrice(allYesSum) : '--'}</b></span>
                    <span>visible share <b>{hasLoadedPrices ? fmtPrice(visibleYesSum) : '--'}</b></span>
                  </>
                ) : null}
                {chartViewSummary.warning ? <strong>{chartViewSummary.warning}</strong> : null}
              </div>
            ) : null}
            <div className="qtv-outcome-legend">
              {visibleOutcomeGroups.slice(0, 8).map((group) => {
                const point = group.points[group.points.length - 1];
                const isSelected = selectedGroup?.key === group.key;
                const isPinned = effectivePinnedOutcomeKeys.includes(group.key);
                const isSolo = effectiveSoloOutcomeKey === group.key;
                const isHovered = hoveredOutcomeKey === group.key;
                return (
                  <span
                    key={group.key}
                    className={`qtv-legend-item ${isSelected ? 'active' : ''} ${isHovered ? 'hovered' : ''} ${isPinned ? 'pinned' : ''} ${isSolo ? 'solo' : ''}`}
                    title={group.fullLabel}
                  >
                    <button
                      type="button"
                      onClick={() => {
                        if (group.tokenId) onOutcomeSelect?.(group.tokenId, group.tokenSide === 'NO' ? 'NO' : 'YES');
                      }}
                    >
                      <i style={{ backgroundColor: SERIES_COLORS[group.order % SERIES_COLORS.length] }} />
                      <span>{group.fullLabel}</span> <b>{fmtPrice(point?.close || 0)}</b>
                    </button>
                    <button className={isPinned ? 'active micro' : 'micro'} type="button" title={isPinned ? 'Unpin outcome line' : 'Pin outcome line'} onClick={() => togglePinnedOutcome(group.key)}>P</button>
                    <button className={isSolo ? 'active micro' : 'micro'} type="button" title={isSolo ? 'Clear solo outcome' : 'Solo outcome'} onClick={() => updateSoloOutcomeKey(isSolo ? '' : group.key)}>S</button>
                    <button className="micro danger" type="button" title="Hide outcome line" onClick={() => toggleHiddenOutcome(group.key)}>H</button>
                  </span>
                );
              })}
              {eventMode && sortedOutcomes.length > visibleOutcomeGroups.length ? (
                <button type="button" onClick={() => setDisplayMode('all')}>+{sortedOutcomes.length - visibleOutcomeGroups.length} more</button>
              ) : null}
            </div>
          </div>
          <div className="qtv-ohlc">
            <span className={`mode ${readoutMode.toLowerCase()}`}>{readoutMode}</span>
            <span>Block {readoutPoint ? blockLabel(readoutPoint.timestamp) : '--'}</span>
            <span>YES {readoutInspect ? fmtPrice(readoutInspect.yes) : latestPriceText}<em>{readoutInspect?.yesKind || 'direct'}</em></span>
            <span>NO {readoutInspect ? fmtPrice(readoutInspect.no) : '--'}<em>{readoutInspect?.noKind || 'implied'}</em></span>
            <span>Min {Number.isFinite(minPrice) ? fmtPrice(minPrice) : '--'}</span>
            <span>Max {Number.isFinite(maxPrice) ? fmtPrice(maxPrice) : '--'}</span>
            <span>{readoutMode === 'Latest' ? 'Latest Δ' : 'Bar Δ'}</span>
            <b className={delta >= 0 ? 'positive' : 'negative'}>{hasLoadedPrices ? `${formatSigned(delta)} (${formatSigned(deltaPct, 2)}%)` : '--'}</b>
          </div>
          <div className="qtv-scale-switch" aria-label="Chart scale mode">
            {SCALE_MODES.map(([mode, label]) => (
              <button key={mode} className={scaleMode === mode ? 'active' : ''} type="button" onClick={() => setScaleMode(mode)}>{label}</button>
            ))}
          </div>
        </div>

        {eventMode && outcomeManagerOpen ? (
          <div className="qtv-outcome-manager">
            <header>
              <div>
                <strong>Outcome Manager</strong>
                <span>{visibleOutcomeGroups.length.toLocaleString('en-US')} visible · {pinnedOutcomeCount.toLocaleString('en-US')} pinned · {hiddenOutcomeCount.toLocaleString('en-US')} hidden</span>
              </div>
              <button type="button" title="Close outcome manager" onClick={() => setOutcomeManagerOpen(false)}>Close</button>
            </header>
            <div className="qtv-outcome-manager-search">
              <input
                value={outcomeManagerQuery}
                placeholder="Search outcomes, token IDs, sides"
                onInput={(event) => setOutcomeManagerQuery(event.currentTarget.value)}
              />
              <button type="button" onClick={() => setDisplayMode('all')}>Show all</button>
              <button type="button" onClick={resetOutcomeVisibility}>Reset</button>
            </div>
            <div className="qtv-outcome-manager-list">
              {managerOutcomes.map((group) => {
                const latestGroupPoint = group.points[group.points.length - 1];
                const isSelected = selectedGroup?.key === group.key;
                const isVisible = visibleOutcomeGroups.some((visibleGroup) => visibleGroup.key === group.key);
                const isPinned = effectivePinnedOutcomeKeys.includes(group.key);
                const isHidden = effectiveHiddenOutcomeKeys.includes(group.key);
                const isSolo = effectiveSoloOutcomeKey === group.key;
                const isHovered = hoveredOutcomeKey === group.key;
                return (
                  <div
                    key={`manager-${group.key}`}
                    className={`${isSelected ? 'selected' : ''} ${isHovered ? 'hovered' : ''} ${isVisible ? 'visible' : ''} ${isHidden ? 'hidden' : ''}`}
                  >
                    <button
                      type="button"
                      title={group.fullLabel}
                      onClick={() => {
                        if (group.tokenId) onOutcomeSelect?.(group.tokenId, group.tokenSide === 'NO' ? 'NO' : 'YES');
                      }}
                    >
                      <i style={{ backgroundColor: SERIES_COLORS[group.order % SERIES_COLORS.length] }} />
                      <span>{group.fullLabel}</span>
                      <b>{fmtPrice(latestGroupPoint?.close || 0)}</b>
                    </button>
                    <em>{group.tokenSide}</em>
                    <button className={isPinned ? 'active' : ''} type="button" onClick={() => togglePinnedOutcome(group.key)}>{isPinned ? 'Pinned' : 'Pin'}</button>
                    <button className={isSolo ? 'active' : ''} type="button" onClick={() => updateSoloOutcomeKey(isSolo ? '' : group.key)}>{isSolo ? 'Solo on' : 'Solo'}</button>
                    <button className={isHidden ? 'danger active' : 'danger'} type="button" onClick={() => toggleHiddenOutcome(group.key)}>{isHidden ? 'Hidden' : 'Hide'}</button>
                  </div>
                );
              })}
              {!managerOutcomes.length ? (
                <div className="empty">
                  <strong>No outcomes matched</strong>
                  <span>Clear the search query or switch side/view filters.</span>
                </div>
              ) : null}
            </div>
          </div>
        ) : null}

        {!hasLoadedPrices && (dataStatus === 'price_loading' || dataStatus === 'metadata_loading' || dataStatus === 'warming' || dataStatus === 'partial' || dataStatus === 'loading') ? (
          <div className="qtv-chart-loading-ribbon">
            <b>{loadingMessage || loadingTitle}</b>
            <span>Outcomes {eventMode ? displayedOutcomeCount.toLocaleString('en-US') : '--'} · Source {priceSource}</span>
            <span>Coverage {marketCoverageRows ? marketCoverageRows.toLocaleString('en-US') : '--'} · Loaded {hasLoadedPrices ? (loadedPriceRows ?? allPoints.length).toLocaleString('en-US') : 'Loading...'} · Backtest {backtestRows ? backtestRows.toLocaleString('en-US') : '--'}</span>
          </div>
        ) : null}

        {dataWindowSettings.visible && dataWindowSettings.minimized ? (
          <button
            className={`qtv-data-window-chip dock-${dataWindowSettings.dock}`}
            type="button"
            onClick={() => updateDataWindow({ minimized: false, visible: true })}
          >
            Data Window {pinnedPoint ? 'Pinned' : 'Latest'}
          </button>
        ) : null}

        {dataWindowSettings.visible && !dataWindowSettings.minimized && dataWindowInspect ? (
          <div
            className={`qtv-data-window ${pinnedPoint ? 'pinned' : ''} ${dataWindowSettings.mode} dock-${dataWindowSettings.dock}`}
            style={dataWindowStyle}
            tabIndex={0}
          >
            <header onPointerDown={(event) => startDataWindowDrag(event as unknown as PointerEvent)}>
              <strong>{dataWindowSettings.mode === 'compact' ? 'Data Window' : 'Expanded Data Window'}</strong>
              <i>{pinnedPoint ? 'Pinned' : hover ? 'Hover' : 'Latest'}</i>
              <button type="button" title="Minimize" onClick={() => updateDataWindow({ minimized: true })}>_</button>
              {pinnedPoint ? <button type="button" title="Clear pinned point" onClick={() => setPinnedPoint(null)}>Clear</button> : null}
              <button type="button" title="Close" onClick={() => updateDataWindow({ visible: false, minimized: false })}>×</button>
            </header>
            <div><span>Block</span><b>{blockLabel(dataWindowInspect.point.timestamp)}</b></div>
            <div>
              <span>Outcome</span>
              <b title={dataWindowInspect.point.outcomeFullLabel || dataWindowInspect.point.outcomeLabel || selectedGroup?.fullLabel}>
                {dataWindowInspect.point.outcomeFullLabel || dataWindowInspect.point.outcomeLabel || selectedGroup?.fullLabel || 'Outcome'}
              </b>
            </div>
            <div><span>YES</span><b>{fmtPrice(dataWindowInspect.yes)} <em>{dataWindowInspect.yesKind}</em></b></div>
            <div><span>NO</span><b>{fmtPrice(dataWindowInspect.no)} <em>{dataWindowInspect.noKind}</em></b></div>
            <div><span>Source</span><b>{priceSource}</b></div>
            {visibleOutcomeGroups.slice(0, 3).map((group) => {
              const point = nearestPoint(group.points, dataWindowInspect.point.timestamp);
              return (
                <div key={group.key} title={group.fullLabel}>
                  <span>{group.fullLabel}</span>
                  <b>{fmtPrice(point?.close ?? latestClose(group))}</b>
                </div>
              );
            })}
            {dataWindowSettings.mode === 'expanded' ? (
              <>
                <div><span>Market</span><b>{market.title}</b></div>
                <div><span>MA</span><b>{dataWindowInspect.ma === undefined ? '--' : fmtPrice(dataWindowInspect.ma)}</b></div>
                <div><span>Volume</span><b>{dataWindowInspect.point.volume.toLocaleString('en-US', { maximumFractionDigits: 2 })}</b></div>
                <div><span>Window</span><b>{allPoints.length.toLocaleString('en-US')} rows · {scaleMode}</b></div>
                <footer>
                  <span className={dataWindowInspect.barDeltaYes >= 0 ? 'positive' : 'negative'}>Bar Δ YES {formatSigned(dataWindowInspect.barDeltaYes)} ({formatSigned(dataWindowInspect.barDeltaYesPct, 2)}%)</span>
                  <span className={dataWindowInspect.deltaYes >= 0 ? 'positive' : 'negative'}>vs latest {formatSigned(dataWindowInspect.deltaYes)} ({formatSigned(dataWindowInspect.deltaYesPct, 2)}%)</span>
                </footer>
              </>
            ) : null}
          </div>
        ) : null}

        {replayEnabled ? (
          <div className="qtv-replay-controls">
            <button type="button" onClick={() => setReplayPlaying((current) => !current)}>{replayPlaying ? 'Pause' : 'Play'}</button>
            <button type="button" onClick={() => setReplayIndex((current) => Math.max(0, (current ?? 0) - 1))}>Step -</button>
            <button type="button" onClick={() => setReplayIndex((current) => Math.min(rawAllPoints.length - 1, (current ?? 0) + 1))}>Step +</button>
            <span>{replayCutoff ? `block ${blockLabel(replayCutoff)}` : 'choose start'}</span>
            <button type="button" onClick={() => { setReplayEnabled(false); setReplayPlaying(false); setReplayIndex(null); }}>Exit</button>
          </div>
        ) : null}

        <div
          className={`qtv-chart-surface ${rangeZoomEnabled ? 'range-enabled' : ''}`}
          ref={chartSurfaceRef}
          onWheel={(event) => handleChartWheel(event as unknown as WheelEvent)}
          onPointerDown={(event) => {
            const shouldRangeZoom = rangeZoomEnabled || event.shiftKey || event.altKey;
            if (!shouldRangeZoom) return;
            event.preventDefault();
            setRangeZoomNotice(null);
            setRangeSelection({ startX: event.clientX, currentX: event.clientX });
          }}
          onPointerMove={(event) => {
            if (!rangeSelection) return;
            setRangeSelection((current) => (current ? { ...current, currentX: event.clientX } : current));
          }}
          onPointerUp={() => {
            if (!rangeSelection) return;
            commitRangeZoom(rangeSelection);
            setRangeSelection(null);
            setRangeZoomEnabled(false);
          }}
          onPointerCancel={() => setRangeSelection(null)}
          onClick={(event) => {
            if (rangeZoomEnabled || rangeSelection) return;
            handleChartClick(event as unknown as MouseEvent);
          }}
        >
          <div className="qtv-tv-chart" ref={containerRef} />
          {!allPoints.length ? (
            <div className="qtv-chart-empty">
              <strong>{loadingTitle}</strong>
              <span>{loadingMessage || 'Preparing event metadata and block-close coverage.'}</span>
              <dl>
                <div><dt>Outcomes</dt><dd>{eventMode ? displayedOutcomeCount.toLocaleString('en-US') : '--'}</dd></div>
                <div><dt>Source</dt><dd>{priceSource}</dd></div>
                <div><dt>Coverage</dt><dd>{marketCoverageRows ? marketCoverageRows.toLocaleString('en-US') : '--'}</dd></div>
                <div><dt>Loaded rows</dt><dd>{isLoadingPrices ? 'Loading...' : rowsText}</dd></div>
              </dl>
              <div className="qtv-chart-empty-actions">
                {onRetry ? <button type="button" onClick={onRetry}>Retry</button> : null}
                <button type="button" onClick={() => setDisplayMode('all')}>Show members</button>
                <button type="button" onClick={() => setScaleMode('full')}>Full scale</button>
              </div>
            </div>
          ) : null}
          {!drawingsHidden ? (
            <svg className="qtv-drawing-layer" aria-hidden="true">
              {[...drawings, ...(pendingDrawing ? [pendingDrawing] : [])].map((drawing) => {
                const first = drawing.points[0];
                const second = drawing.points[1] || drawing.points[0];
                if (!first || !second) return null;
                const a = pointToScreen(first);
                const b = pointToScreen(second);
                if (drawing.kind === 'hline') return <line key={drawing.id} x1="0%" x2="100%" y1={a.y} y2={a.y} />;
                if (drawing.kind === 'vline') return <line key={drawing.id} x1={a.x} x2={a.x} y1="0%" y2="100%" />;
                if (drawing.kind === 'text') return <text key={drawing.id} x={a.x} y={a.y}>{drawing.text || 'Note'}</text>;
                return <line key={drawing.id} className={drawing.kind} x1={a.x} y1={a.y} x2={b.x} y2={b.y} />;
              })}
            </svg>
          ) : null}
          {selectedTradeFocus ? (
            <>
              <div className="qtv-trade-focus-band" style={{ left: selectedTradeFocus.bandLeft, width: selectedTradeFocus.bandWidth }} />
              <div className="qtv-trade-focus-marker-line entry" style={{ left: selectedTradeFocus.entryLeft }} />
              <div className="qtv-trade-focus-marker-line exit" style={{ left: selectedTradeFocus.exitLeft }} />
              <div className="qtv-trade-focus-card">
                <header>
                  <strong>{selectedTradeFocus.id}</strong>
                  <button type="button" onClick={fitData}>Reset view</button>
                </header>
                <dl>
                  <div><dt>Entry</dt><dd>{blockLabel(selectedTradeFocus.startSignal.timestamp)} · {fmtPrice(selectedTradeFocus.startSignal.price)}</dd></div>
                  <div><dt>Exit</dt><dd>{blockLabel(selectedTradeFocus.endSignal.timestamp)} · {fmtPrice(selectedTradeFocus.endSignal.price)}</dd></div>
                  <div><dt>Bars</dt><dd>{selectedTradeFocus.bars.toLocaleString('en-US')}</dd></div>
                  <div><dt>Move</dt><dd className={selectedTradeFocus.endSignal.price >= selectedTradeFocus.startSignal.price ? 'positive' : 'negative'}>{formatSigned(selectedTradeFocus.endSignal.price - selectedTradeFocus.startSignal.price)}</dd></div>
                </dl>
              </div>
            </>
          ) : null}
          {markers.map((marker) => marker ? (
            <div
              key={marker.signal.id}
              className={`qtv-html-signal ${marker.signal.action === 'OPEN' || marker.signal.action === 'BUY' ? 'open' : 'close'} ${marker.signal.tradeId === selectedTradeId ? 'selected' : ''}`}
              style={{ left: marker.left, top: marker.top }}
              title={`${marker.signal.action} ${marker.signal.outcome} @ ${fmtPrice(marker.signal.price)}\n${marker.signal.reason}`}
            >
              {marker.signal.action === 'SELL' ? 'SELL' : marker.signal.action}
            </div>
          ) : null)}
          {focusedMarkers.length && !selectedTradeFocus ? <div className="qtv-trade-focus-pill">{selectedTradeId} entry / exit located</div> : null}
          {rangeZoomEnabled && !rangeSelection ? (
            <div className="qtv-range-armed">
              <strong>Box zoom armed</strong>
              <span>Drag horizontally across blocks · Esc cancels · Z toggles</span>
            </div>
          ) : null}
          {rangeZoomNotice ? (
            <div className="qtv-range-notice">
              <span>Zoomed</span>
              <b>{blockLabel(rangeZoomNotice.fromBlock)} → {blockLabel(rangeZoomNotice.toBlock)}</b>
              <em>{rangeZoomNotice.pointCount.toLocaleString('en-US')} rows · {rangeZoomNotice.spanBlocks.toLocaleString('en-US')} blocks</em>
              <button type="button" onClick={() => { setRangeZoomNotice(null); setRangeZoomEnabled(true); }}>Box again</button>
              <button type="button" onClick={() => { setRangeZoomNotice(null); fitData(); }}>Fit</button>
            </div>
          ) : null}
          {rangeSelectionStyle ? <div className="qtv-range-selection" style={rangeSelectionStyle} /> : null}
          {rangeSelectionMeta && rangeSelectionHudStyle ? (
            <div className="qtv-range-hud" style={rangeSelectionHudStyle}>
              <strong>{blockLabel(rangeSelectionMeta.fromBlock)} → {blockLabel(rangeSelectionMeta.toBlock)}</strong>
              <span>{rangeSelectionMeta.pointCount.toLocaleString('en-US')} rows · {rangeSelectionMeta.spanBlocks.toLocaleString('en-US')} blocks</span>
              <em>release to zoom</em>
            </div>
          ) : null}
          {replayEnabled && replayCutoff ? (
            <div className="qtv-replay-cursor" style={{ left: `${(Math.max(0, primaryPoints.length - 1) / Math.max(1, rawAllPoints.length - 1)) * 100}%` }} />
          ) : null}
          {pinnedPoint ? (
            <div className="qtv-pinned-actions">
              <button type="button">Run backtest from here</button>
              <button type="button" onClick={() => { setReplayEnabled(true); setReplayIndex(Math.max(0, rawAllPoints.findIndex((point) => point.timestamp >= pinnedPoint.timestamp))); }}>Set replay start</button>
              <button type="button" onClick={exportLoadedCsv}>Export window</button>
            </div>
          ) : null}
          {hoverInspect && hoverScreen ? (
            <>
              <div className="qtv-hover-crosshair-x" style={{ left: hoverScreen.x }} />
              <div className="qtv-hover-crosshair-y" style={{ top: hoverScreen.y }} />
              <div className="qtv-hover-price-tag" style={{ top: hoverScreen.y }}>
                <em title={hoverGroup?.fullLabel}>{hoverGroup?.label || 'Hover'}</em>
                <span><i>YES</i>{fmtPrice(hoverInspect.yes)}</span>
                <span><i>NO</i>{fmtPrice(hoverInspect.no)}</span>
                <b>{blockLabel(hoverInspect.point.timestamp)}</b>
                <small className={hoverInspect.barDeltaYes >= 0 ? 'positive' : 'negative'}>
                  bar {formatSigned(hoverInspect.barDeltaYes)} ({formatSigned(hoverInspect.barDeltaYesPct, 2)}%)
                </small>
                <small className={hoverInspect.deltaYes >= 0 ? 'positive' : 'negative'}>
                  latest {formatSigned(hoverInspect.deltaYes)}
                </small>
              </div>
              {hoverOutcomeStack.length ? (
                <div className="qtv-hover-axis-stack" aria-label="Hovered outcome prices">
                  {hoverOutcomeStack.map((row) => (
                    <div
                      key={`hover-axis-${row.key}`}
                      className={row.active ? 'active' : ''}
                      style={`top: ${row.top}; --qtv-axis-color: ${row.color};`}
                      title={`${row.fullLabel}: ${fmtPrice(row.price)} at block ${blockLabel(hoverInspect.point.timestamp)}`}
                    >
                      <span>{row.label}</span>
                      <b>{fmtProbabilityPercent(row.price)}</b>
                      <small className={row.delta >= 0 ? 'positive' : 'negative'}>{formatSigned(row.delta)}</small>
                    </div>
                  ))}
                </div>
              ) : null}
            </>
          ) : null}
          {pinnedInspect && pinnedScreen ? (
            <>
              <div className="qtv-pinned-crosshair-x" style={{ left: pinnedScreen.x }} />
              <div className="qtv-pinned-crosshair-y" style={{ top: pinnedScreen.y }} />
              <div className="qtv-pinned-point-dot" style={{ left: pinnedScreen.x, top: pinnedScreen.y }} />
              <div className="qtv-pinned-price-tag" style={{ top: pinnedScreen.y }}>
                <em>Pinned</em>
                <span><i>YES</i>{fmtPrice(pinnedInspect.yes)}</span>
                <span><i>NO</i>{fmtPrice(pinnedInspect.no)}</span>
                <b>{blockLabel(pinnedInspect.point.timestamp)}</b>
                <small className={pinnedInspect.barDeltaYes >= 0 ? 'positive' : 'negative'}>
                  bar {formatSigned(pinnedInspect.barDeltaYes)} ({formatSigned(pinnedInspect.barDeltaYesPct, 2)}%)
                </small>
                <small className={pinnedInspect.deltaYes >= 0 ? 'positive' : 'negative'}>
                  latest {formatSigned(pinnedInspect.deltaYes)}
                </small>
              </div>
            </>
          ) : null}
          {hoverInspect && hoverScreen && (!pinnedPoint || Math.floor(pinnedPoint.timestamp) !== Math.floor(hoverInspect.point.timestamp)) ? (
            <div className={`qtv-hover-tooltip ${tooltipMode} ${hoverTooltipSide}`} style={{ left: hoverScreen.x, top: hoverScreen.y }}>
              <strong>{hoverInspect.point.outcomeFullLabel || hoverInspect.point.outcomeLabel || selectedGroup?.fullLabel || 'Outcome'}</strong>
              <span>Block {blockLabel(hoverInspect.point.timestamp)}</span>
              <b>YES {fmtPrice(hoverInspect.yes)} · NO {fmtPrice(hoverInspect.no)}</b>
              {visibleOutcomeGroups.slice(0, tooltipMode === 'full' ? 8 : 5).map((group) => {
                const point = nearestPoint(group.points, hoverInspect.point.timestamp);
                return (
                  <em key={group.key} title={group.fullLabel}>
                    <i style={{ backgroundColor: SERIES_COLORS[group.order % SERIES_COLORS.length] }} />
                    <span>{group.fullLabel}</span>
                    <b>{fmtPrice(point?.close ?? latestClose(group))}</b>
                  </em>
                );
              })}
            </div>
          ) : null}
          <div
            className="qtv-block-tick-axis"
            style={blockAxisStyle}
            aria-label="Block navigator"
            onPointerDown={(event) => startNavigatorDrag(event as unknown as PointerEvent)}
          >
            <div className="qtv-block-navigator-track" aria-hidden="true">
              {navigatorWindowStyle ? <i className="qtv-block-navigator-window" style={navigatorWindowStyle} /> : null}
            </div>
            <div className="qtv-block-navigator-labels" aria-hidden="true">
              {navigatorDomainLabels.map((tick) => (
                <span key={tick.key} className={tick.className} style={{ left: tick.left }}>
                  <b>{blockLabel(tick.block)}</b>
                </span>
              ))}
            </div>
            {visibleWindowMeta ? (
              <div className="qtv-block-navigator-window-readout" aria-hidden="true">
                <span>visible</span>
                <b>{blockLabel(visibleWindowMeta.fromBlock)} - {blockLabel(visibleWindowMeta.toBlock)}</b>
              </div>
            ) : null}
            {blockAxisMinorTicks.map((tick) => (
              <span key={tick.key} className={`minor ${tick.level}`} style={{ left: tick.left }}>
                <i />
              </span>
            ))}
            {blockAxisTicks.map((tick) => (
              <span key={tick.key} className={tick.className} style={{ left: tick.left }}>
                <i />
                <b>{blockLabel(tick.block)}</b>
              </span>
            ))}
            {!blockAxisTicks.length ? (
              <em>{priceSource.includes('block') ? 'Loading block scale' : 'Time scale'}</em>
            ) : null}
            {priceSource.includes('block') && hoverInspect && hoverScreen ? (
              <strong className="qtv-block-hover-label" style={{ left: hoverScreen.x }}>
                <span>block</span>
                <b>{blockLabel(hoverInspect.point.timestamp)}</b>
                <em>YES {fmtPrice(hoverInspect.yes)} · NO {fmtPrice(hoverInspect.no)}</em>
              </strong>
            ) : null}
          </div>
        </div>
        {layoutMode !== '1' ? (
          <div className={`qtv-layout-scaffold layout-${layoutMode}`}>
            {Array.from({ length: layoutMode === '4' ? 3 : 1 }).map((_, index) => (
              <div key={index}>
                <strong>Synced pane {index + 2}</strong>
                <span>{market.title}</span>
                <em>Crosshair and market synced · configure source/outcome next</em>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}
