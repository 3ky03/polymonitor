import type { QuantBlockClosePoint, QuantFrontendPricePoint } from '@/types';

export type TesterTab = 'overview' | 'benchmark' | 'fillQuality' | 'dataQuality' | 'regime' | 'predictionQuality' | 'parameters' | 'performance' | 'orders' | 'trades' | 'ledger' | 'equity' | 'drawdown' | 'runs' | 'logs' | 'properties';
export type PriceSource = 'frontend' | 'orderfilled' | 'orderbook' | 'conservative';
export type BacktestEngine = 'builtin' | 'backtrader' | 'nautilus_trader';
export type DataStatus = 'idle' | 'loading' | 'metadata_loading' | 'price_loading' | 'warming' | 'partial' | 'ready' | 'empty' | 'error';
export type MetricStatus = 'positive' | 'negative' | 'neutral';
export type TradeFilter = 'profitable' | 'losing' | 'yes' | 'no' | 'longHolding' | 'shortHolding';
export type PerformanceSortKey = 'metric' | 'all' | 'long' | 'short' | 'description';
export type SortDirection = 'asc' | 'desc';

export type StrategyParameters = {
  entryThreshold: number;
  exitThreshold: number;
  stopLoss: number;
  takeProfit: number;
  maxHoldingBars: number;
  initialCapital: number;
  positionSize: number;
  feeBps: number;
  slippageBps: number;
  liquidityCapPct: number;
  maxPositionNotional: number;
  minFillPct: number;
  executionProfile: 'optimistic' | 'realistic' | 'conservative' | 'stress';
  orderRole: 'maker' | 'taker';
  executionPriceMode: 'ORDERFILLED_LIMIT_REPLAY' | 'ORDERFILLED' | 'DEPTH' | 'LEGACY';
  finalValuationMode: 'SETTLEMENT' | 'FORCE_CLOSE' | string;
  buyLimitPrice?: number;
  sellLimitPrice?: number;
  settlementValue?: number;
  latencySeconds: number;
  latencyBlocks: number;
  maxBookStalenessSeconds: number;
  adverseSlippageCents: number;
  fillProbabilityHaircutPct: number;
  allowPartialFill: boolean;
  minFillSize: number;
  rejectOnStaleBook: boolean;
};

export type MarketInfo = {
  id: string;
  conditionId: string;
  title: string;
  category: string;
  slug: string;
  startTime: string;
  endTime: string;
  resolutionTime: string;
  resolvedOutcome: 'YES' | 'NO' | 'PENDING';
  yesTokenId: string;
  noTokenId: string;
  liquidity: string;
  volume: string;
};

export type PricePoint = {
  timestamp: number;
  close: number;
  volume: number;
  source: string;
  tokenId?: string;
  tokenSide?: string;
  outcomeLabel?: string;
  outcomeShortLabel?: string;
  outcomeFullLabel?: string;
  outcomeKey?: string;
  yesPrice?: number;
  noPrice?: number;
  yesPriceKind?: 'direct' | 'implied';
  noPriceKind?: 'direct' | 'implied';
  ma?: number;
  qualityFlags?: string[];
  isCarriedForward?: boolean;
  isInterpolated?: boolean;
};

export type CandlePoint = PricePoint & {
  open: number;
  high: number;
  low: number;
};

export type Signal = {
  id: string;
  timestamp: number;
  action: 'OPEN' | 'CLOSE' | 'BUY' | 'SELL';
  outcome: string;
  price: number;
  size: number;
  notional: number;
  reason: string;
  tradeId: string;
};

export type Trade = {
  id: string;
  entryTime: string;
  exitTime: string;
  entryX?: number;
  exitX?: number;
  xAxis?: string;
  marketId: string;
  market: string;
  outcome: string;
  side: 'LONG' | 'SHORT';
  entryPrice: number;
  exitPrice: number;
  size: number;
  notional: number;
  requestedSize?: number;
  filledSize?: number;
  unfilledSize?: number;
  fillStatus?: string;
  bookSnapshotId?: number;
  snapshotVersion?: string;
  stalenessSeconds?: number;
  stalenessBlocks?: number;
  avgFillPrice?: number;
  fillProbability?: number;
  blockVolume?: number;
  tradeCount?: number;
  availableNotional?: number;
  executionSource?: string;
  pnl: number;
  pnlPct: number;
  holdingTime: string;
  holdingBars: number;
  exitReason: string;
};

export type OrderLifecycleRow = {
  id: string;
  signalIndex: number;
  tradeId?: string;
  xAxis: string;
  signalX: number;
  submitX: number;
  side: string;
  role: string;
  orderType: string;
  status: string;
  decisionPrice: number;
  requestedPrice: number;
  requestedSize: number;
  requestedNotional: number;
  filledSize: number;
  filledNotional: number;
  unfilledSize: number;
  avgFillPrice: number;
  fillProbability: number;
  fillPct: number;
  blockVolume: number;
  tradeCount: number;
  availableNotional: number;
  feeCost: number;
  slippageCost: number;
  executionCost: number;
  latencyBlocks: number;
  latencySeconds: number;
  noFillReason: string;
  executionSource: string;
};

export type LedgerRow = {
  id: string;
  orderId?: string;
  tradeId?: string;
  eventType: string;
  xAxis: string;
  xValue: number;
  sharesDelta: number;
  cashDelta: number;
  fee: number;
  rebate: number;
  slippageCost: number;
  executionCost: number;
  realizedPnl: number;
  positionAfter: number;
  cashAfter: number;
  price: number;
  source: string;
};

export type BacktestMetric = {
  name: string;
  value: number;
  formattedValue: string;
  delta: string;
  status: MetricStatus;
  tooltip: string;
};

export type EquityPoint = {
  timestamp: number;
  index: number;
  equity: number;
  drawdown: number;
  drawdownPct: number;
  cumulativeReturn: number;
};

export type PerformanceRow = {
  metric: string;
  all: string;
  long: string;
  short: string;
  description: string;
};

export type PropertyGroup = {
  title: string;
  rows: Array<{ label: string; value: string }>;
};

export type BacktestResult = {
  runId: number;
  generatedAt: string;
  metrics: BacktestMetric[];
  equity: EquityPoint[];
  orders: OrderLifecycleRow[];
  trades: Trade[];
  ledger: LedgerRow[];
  performanceRows: PerformanceRow[];
  propertyGroups: PropertyGroup[];
  predictionMetrics: BacktestMetric[];
};

export type BatchBacktestRow = {
  key: string;
  runId?: number;
  outcome: string;
  marketSlug: string;
  tokenSide: 'YES' | 'NO';
  rows: number;
  trades: number;
  netProfit: string;
  totalReturn: string;
  maxDrawdown: string;
  status: string;
  error?: string;
};

export type QuantRows = {
  frontendRows: QuantFrontendPricePoint[];
  blockRows: QuantBlockClosePoint[];
};
