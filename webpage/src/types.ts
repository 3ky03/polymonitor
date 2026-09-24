export type MarketSummary = {
  id: number;
  slug: string;
  title: string;
  conditionId?: string | null;
  questionId?: string | null;
  oracle?: string | null;
  yesTokenId?: string | null;
  noTokenId?: string | null;
  description?: string;
  status?: string;
  latestPrice?: string | number | null;
  latestYesPrice?: string | number | null;
  latestNoPrice?: string | number | null;
  enableNegRisk?: boolean;
  endDate?: string | null;
  createdAt?: string | null;
  category?: string;
  tags?: string[];
  gammaMarketId?: string | number | null;
};

export type BootstrapPayload = {
  generatedAt: string;
  defaultWorkspace: {
    name: string;
    panels: string[];
  };
  featuredMarket: MarketSummary | null;
  activeMarketsPreview: MarketListItem[];
  activeMarketGroupsPreview?: MarketGroupItem[];
  globalTradesPreview: TradeRow[];
  globalOraclePreview: OracleEvent[];
  latestContentPreview: ContentItem[];
  commoditiesPreview?: RuntimeMarketGroup | null;
  recentTradesPreview: TradeRow[];
  oraclePreview: OracleEvent[];
  contentPreview: ContentItem[];
  pricePreview: PriceSummary | null;
  systemHealth: SystemHealth;
};

export type MarketsPayload = {
  items: MarketListItem[];
  pagination: {
    page: number;
    pageSize: number;
    total: number;
    totalPages: number;
    hasMore: boolean;
  };
};

export type MarketGroupOutcome = {
  outcomeKey?: string | null;
  marketId?: number | null;
  gammaMarketId?: string | number | null;
  label?: string | null;
  title?: string | null;
  yesPrice?: string | number | null;
  noPrice?: string | number | null;
  blockCloseYesPrice?: string | number | null;
  blockCloseBlockNumber?: string | number | null;
  change24h?: string | number | null;
  volume24h?: string | number | null;
  tradeCount24h?: number | string | null;
  volumeScope?: 'market' | 'event' | null;
  volumeSource?: string | null;
  lastTradeAt?: string | null;
  conditionId?: string | null;
  slug?: string | null;
  yesTokenId?: string | null;
  noTokenId?: string | null;
};

export type MarketGroupItem = {
  groupId: string;
  eventId?: string | number | null;
  title: string;
  slug?: string | null;
  category?: string | null;
  tags?: string[];
  createdAt?: string | null;
  lastActivityAt?: string | null;
  generatedAt?: string | null;
  endDate?: string | null;
  volume24h?: string | number | null;
  tradeCount24h?: number | string | null;
  volumeScope?: 'market' | 'event' | null;
  volumeSource?: string | null;
  outcomeCount?: number | null;
  defaultOutcomeKey?: string | null;
  defaultMarketId?: number | null;
  latestBlockClosePrice?: string | number | null;
  outcomes: MarketGroupOutcome[];
  topOutcomes: MarketGroupOutcome[];
};

export type MarketGroupSort = 'active' | 'new' | 'volume' | 'close' | 'move' | 'trades';
export type MarketGroupChartRange = '1h' | '6h' | '1d' | '1w' | '1m' | 'all';

export type MarketGroupsPayload = {
  items: MarketGroupItem[];
  pagination: {
    page: number;
    pageSize: number;
    total?: number;
    totalPages?: number;
    hasMore: boolean;
  };
  generatedAt?: string;
};

export type MarketGroupDetail = MarketGroupItem & {
  defaultOutcomeKey?: string | null;
  generatedAt?: string;
  status?: string;
};

export type MarketGroupChartSeriesPoint = {
  timestamp: string;
  price: number | string;
};

export type MarketGroupChartSeries = {
  outcomeKey?: string | null;
  label?: string | null;
  marketId?: number | null;
  color?: string | null;
  points: MarketGroupChartSeriesPoint[];
};

export type MarketGroupChartPayload = {
  eventId?: string | number | null;
  groupId?: string | null;
  title?: string | null;
  defaultOutcomeKey?: string | null;
  range: string;
  interval?: string | null;
  historyStatus?: string | null;
  priceSource?: string | null;
  generatedAt?: string;
  series: MarketGroupChartSeries[];
};

export type MarketListItem = {
  id: number;
  slug: string;
  title: string;
  conditionId?: string | null;
  questionId?: string | null;
  gammaMarketId?: string | number | null;
  endDate?: string | null;
  createdAt?: string | null;
  latestPrice?: string | number | null;
  status?: string;
  category?: string;
  tags?: string[];
  outcomeCount?: number | null;
  volume24h?: string | number | null;
  tradeCount24h?: number | null;
  change24h?: string | number | null;
  lastTradeAt?: string | null;
  yesTokenId?: string | null;
  noTokenId?: string | null;
};

export type TradeRow = {
  txHash?: string | null;
  logIndex?: number | null;
  blockNumber?: number | null;
  timestamp?: string | null;
  maker?: string | null;
  taker?: string | null;
  price?: string | null;
  size?: string | null;
  side?: string | null;
  outcome?: string | null;
  tokenId?: string | null;
  marketId?: number | null;
  marketTitle?: string | null;
  orderHash?: string | null;
  makerAssetId?: string | null;
  takerAssetId?: string | null;
  makerAmount?: string | number | null;
  takerAmount?: string | number | null;
  fee?: string | number | null;
  contract?: string | null;
};

export type OracleEvent = {
  id?: number;
  txHash?: string | null;
  logIndex?: number | null;
  blockNumber?: number | null;
  eventTime?: string | null;
  eventStatus?: string | null;
  externalMarketId?: string | null;
  marketId?: number | null;
  localMarketId?: number | null;
  gammaMarketId?: string | number | null;
  marketTitle?: string | null;
  marketSlug?: string | null;
  marketCategory?: string | null;
  isBound?: boolean | null;
  matchedBy?: string | null;
  questionId?: string | null;
  conditionId?: string | null;
  proposedPrice?: string | number | null;
  settledPrice?: string | number | null;
  payout?: string | number | null;
  settlementCode?: number | string | null;
  settlementOutcome?: string | null;
  settlementSource?: string | null;
  settlementRaw?: string | null;
  effectiveSettlementCode?: number | string | null;
  effectiveSettlementOutcome?: string | null;
  effectiveSettlementSource?: string | null;
  completionStatus?: string | null;
  isTradingClosed?: boolean | null;
  isResolved?: boolean | null;
  isFinal?: boolean | null;
  requester?: string | null;
  proposer?: string | null;
  disputer?: string | null;
  proposalTransaction?: string | null;
  settlementTransaction?: string | null;
  sourceAdapter?: string | null;
  sourceOracle?: string | null;
};

export type OraclePayload = {
  marketId: number;
  localMarketId?: number | null;
  gammaMarketId?: string | number | null;
  questionId?: string | null;
  conditionId?: string | null;
  oracle?: string | null;
  currentStatus?: string | null;
  completionStatus?: string | null;
  isTradingClosed?: boolean | null;
  isResolved?: boolean | null;
  isFinal?: boolean | null;
  settlementOutcome?: string | null;
  settlementSource?: string | null;
  summary?: {
    completionStatus?: string | null;
    isTradingClosed?: boolean | null;
    isResolved?: boolean | null;
    isFinal?: boolean | null;
    settlementCode?: number | string | null;
    settlementOutcome?: string | null;
    settlementSource?: string | null;
    settlementEventId?: number | string | null;
    settlementEventTime?: string | null;
    settlementTransaction?: string | null;
  } | null;
  timeline: OracleEvent[];
};

export type PriceSummary = {
  marketId: number;
  latestPrice?: string | null;
  latestYesPrice?: string | null;
  latestNoPrice?: string | null;
  change1h?: string | null;
  change24h?: string | null;
  volume24h?: string | null;
  tradeCount24h?: number;
  updatedAt?: string | null;
  priceSource?: string | null;
};

export type ChartPoint = {
  timestamp: string;
  blockNumber?: number | string | null;
  x?: number | string | null;
  yesPrice?: string | number | null;
  noPrice?: string | number | null;
  value?: string | number | null;
  volume?: string | number | null;
  tradeCount?: number | string | null;
};

export type ChartPayload = {
  marketId: number;
  localMarketId?: number | null;
  range: string;
  interval: string;
  kind?: 'probability' | 'underlying-price' | string;
  historyStatus?: 'ok' | 'short' | 'flat' | 'snapshot' | 'missing' | string | null;
  sourceSymbol?: string | null;
  sourceLabel?: string | null;
  pairLabel?: string | null;
  currentUnderlyingPrice?: string | number | null;
  underlyingChangePercent?: number | null;
  targetPrice?: string | number | null;
  targetLabel?: string | null;
  referenceRule?: string | null;
  priceSource?: string | null;
  servingSource?: string | null;
  servingUpdatedAt?: string | null;
  points: ChartPoint[];
};

export type QuantFrontendPricePoint = {
  tokenId: string;
  marketId: number;
  marketSlug?: string | null;
  tokenSide: string;
  tsMinute?: string | null;
  timestamp: number;
  price: string | number;
};

export type QuantBlockClosePoint = {
  tokenId: string;
  marketId: number;
  marketSlug?: string | null;
  tokenSide: string;
  blockNumber: number;
  closePrice: string | number;
  yesProbabilityClose?: string | number | null;
  vwapPrice?: string | number | null;
  yesProbabilityVwap?: string | number | null;
  closeRawPrice?: string | number | null;
  closePriceSource?: string | null;
  closeTxHash?: string | null;
  closeLogIndex?: number | null;
  tradeCount?: number | string | null;
  rawTradeCount?: number | string | null;
  volume?: string | number | null;
};

export type QuantMarketSeriesPoint = {
  x: number | string;
  timestamp?: number | string | null;
  blockNumber?: number | string | null;
  tokenId?: string | null;
  tokenSide?: string | null;
  price: string | number;
  yesProbabilityClose?: string | number | null;
  vwapPrice?: string | number | null;
  yesProbabilityVwap?: string | number | null;
  volume?: string | number | null;
  tradeCount?: number | string | null;
  isImplied?: boolean | null;
};

export type QuantMarketSeriesOutcome = {
  marketId?: number | string | null;
  marketSlug?: string | null;
  marketTitle?: string | null;
  eventId?: string | null;
  eventSlug?: string | null;
  conditionId?: string | null;
  endDate?: string | null;
  tokenId: string;
  tokenSide: string;
  outcomeIndex?: number | string | null;
  outcomeLabel: string;
  outcomeKey?: string | null;
  coverageStatus?: string | null;
  buyYesTokenId?: string | null;
  buyYesTokenSide?: string | null;
  buyYesLabel?: string | null;
  buyYesPrice?: string | number | null;
  buyNoTokenId?: string | null;
  buyNoTokenSide?: string | null;
  buyNoLabel?: string | null;
  buyNoPrice?: string | number | null;
  rows: number;
  firstX?: number | string | null;
  lastX?: number | string | null;
  latestPrice?: string | number | null;
  points: QuantMarketSeriesPoint[];
  complementRows?: number | string | null;
  complementFirstX?: number | string | null;
  complementLastX?: number | string | null;
  complementLatestPrice?: string | number | null;
  complementPoints?: QuantMarketSeriesPoint[];
};

export type QuantMarketSeriesPayload = {
  market?: {
    marketId?: number | string | null;
    marketSlug?: string | null;
    marketTitle?: string | null;
    conditionId?: string | null;
    endDate?: string | null;
    status?: string | null;
    source: string;
    scope: string;
    xAxis: 'timestamp' | 'block_number' | string;
  };
  event?: {
    eventId?: string | null;
    eventSlug?: string | null;
    eventTitle?: string | null;
    status?: string | null;
    groupingConfidence?: string | null;
    source: string;
    scope: string;
    xAxis: 'timestamp' | 'block_number' | string;
  };
  members?: Array<Record<string, unknown>>;
  outcomes: QuantMarketSeriesOutcome[];
  count: number;
  status?: string | null;
  cacheHit?: boolean | null;
  cacheStatus?: string | null;
  warming?: boolean | null;
  retryAfterMs?: number | string | null;
  requestId?: string | null;
  message?: string | null;
  tile?: Record<string, unknown> | null;
};

export type QuantPriceMarket = {
  itemKind?: 'market' | 'event' | string;
  eventId?: string | null;
  eventSlug?: string | null;
  eventTitle?: string | null;
  groupingConfidence?: string | null;
  source?: string | null;
  outcomeCount?: number | string | null;
  totalMembers?: number | string | null;
  readyMembers?: number | string | null;
  orderfilledRows?: number | string | null;
  marketId?: number | string | null;
  marketSlug: string;
  marketTitle?: string | null;
  tokenSide: string;
  conditionId?: string | null;
  status?: string | null;
  endDate?: string | null;
  blockRows?: number | string | null;
  frontendRows?: number | string | null;
  firstBlock?: number | string | null;
  lastBlock?: number | string | null;
  latestBlockPrice?: string | number | null;
  latestBlockAt?: string | null;
  firstTs?: number | string | null;
  lastTs?: number | string | null;
  latestFrontendPrice?: string | number | null;
  latestFrontendAt?: string | null;
};

export type QuantBuildRun = {
  runId: number;
  source: string;
  mode?: string | null;
  status: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  marketsTotal?: number | string | null;
  marketsComplete?: number | string | null;
  rowsWritten?: number | string | null;
  errorCount?: number | string | null;
  lastError?: string | null;
};

export type QuantBacktestRun = {
  runId: number;
  status: string;
  marketSlug: string;
  tokenSide: string;
  priceSource: string;
  backtestEngine?: string | null;
  fromTs?: number | string | null;
  toTs?: number | string | null;
  fromBlock?: number | string | null;
  toBlock?: number | string | null;
  rowsProcessed?: number | string | null;
  error?: string | null;
  createdAt?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  entryThreshold?: string | number | null;
  exitThreshold?: string | number | null;
  stopLoss?: string | number | null;
  takeProfit?: string | number | null;
  maxHoldingBars?: number | string | null;
  initialCapital?: string | number | null;
  positionSize?: string | number | null;
  feeBps?: string | number | null;
  slippageBps?: string | number | null;
  liquidityCapPct?: string | number | null;
  maxPositionNotional?: string | number | null;
  minFillPct?: string | number | null;
  executionPriceMode?: string | null;
  finalValuationMode?: string | null;
  buyLimitPrice?: string | number | null;
  sellLimitPrice?: string | number | null;
  settlementValue?: string | number | null;
  latencySeconds?: string | number | null;
  maxBookStalenessSeconds?: string | number | null;
  allowPartialFill?: boolean | string | number | null;
  minFillSize?: string | number | null;
  rejectOnStaleBook?: boolean | string | number | null;
  executionProfile?: string | null;
  orderRole?: string | null;
  latencyBlocks?: string | number | null;
  adverseSlippageCents?: string | number | null;
  fillProbabilityHaircutPct?: string | number | null;
  parameterFingerprint?: string | null;
  parameterSnapshot?: Record<string, unknown> | null;
  meta?: Record<string, unknown> | null;
};

export type QuantBacktestBenchmarkRun = {
  benchmarkId: number;
  status: string;
  universeType?: string | null;
  universeName?: string | null;
  marketCount?: number | string | null;
  strategyName?: string | null;
  parameters?: Record<string, unknown> | null;
  profiles?: Record<string, unknown> | null;
  summary?: Record<string, unknown> | null;
  dataVersion?: string | null;
  error?: string | null;
  createdAt?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
};

export type QuantBacktestBenchmarkRow = {
  benchmarkId: number;
  rowIndex: number;
  marketId?: string | number | null;
  marketSlug?: string | null;
  title?: string | null;
  eventTime?: string | null;
  outcome?: string | null;
  signalTime?: string | null;
  fastStatus?: string | null;
  accurateStatus?: string | null;
  fastPnl?: string | number | null;
  accuratePnl?: string | number | null;
  pnlDiff?: string | number | null;
  fastFillBlock?: string | number | null;
  accurateFillBlock?: string | number | null;
  dataQuality?: string | null;
  payload?: Record<string, unknown> | null;
};

export type QuantBacktestBenchmarkArtifact = {
  benchmarkId: number;
  artifactKey: string;
  artifactKind: string;
  payload?: unknown;
  createdAt?: string | null;
};

export type QuantBacktestBenchmarkQueue = {
  counts?: Record<string, number>;
  queuedCount?: number;
  runningCount?: number;
  completedCount?: number;
  failedCount?: number;
  canceledCount?: number;
  oldestQueuedAt?: string | null;
  oldestQueuedAgeSeconds?: number | null;
  lastCompletedAt?: string | null;
  lastFailedAt?: string | null;
  workerOnline?: boolean;
  workers?: Array<Record<string, unknown>>;
};

export type QuantBacktestUniverse = {
  universeName: string;
  universeType: string;
  label?: string | null;
  category?: string | null;
  eventSlug?: string | null;
  requireResolved?: boolean;
  requireOrderfilledRows?: boolean;
};

export type QuantBacktestBenchmarkCreatePayload = {
  universe?: string;
  universeSpec?: Record<string, unknown>;
  limit?: number;
  strategy?: string;
  strategySpec?: Record<string, unknown>;
  replayProfiles?: string[];
  executionProfiles?: string[];
  profileBundle?: string | string[];
  stake?: number;
  initialCapital?: number;
  maxDailyCost?: number;
  maxConcurrentPositions?: number;
  forceBlockReplayBackfill?: boolean;
};

export type QuantBacktestMetric = {
  runId: number;
  metricKey: string;
  metricName: string;
  metricGroup: 'overview' | 'prediction' | string;
  value?: string | number | null;
  formattedValue?: string | null;
  delta?: string | null;
  status?: 'positive' | 'negative' | 'neutral' | string | null;
  tooltip?: string | null;
  sortOrder?: number | string | null;
};

export type QuantBacktestEquityPoint = {
  runId: number;
  pointIndex: number;
  xAxis: 'timestamp' | 'block_number' | string;
  xValue: number | string;
  equity: string | number;
  drawdown: string | number;
  drawdownPct: string | number;
  cumulativeReturn: string | number;
};

export type QuantBacktestTrade = {
  runId: number;
  tradeId: string;
  marketSlug: string;
  tokenSide: 'YES' | 'NO' | string;
  side: 'LONG' | 'SHORT' | string;
  xAxis: 'timestamp' | 'block_number' | string;
  entryX: number | string;
  exitX: number | string;
  entryPrice: string | number;
  exitPrice: string | number;
  size: string | number;
  notional: string | number;
  requestedNotional?: string | number | null;
  filledNotional?: string | number | null;
  requestedSize?: string | number | null;
  filledSize?: string | number | null;
  unfilledSize?: string | number | null;
  fillPct?: string | number | null;
  fillStatus?: string | null;
  bookSnapshotId?: string | number | null;
  snapshotVersion?: string | null;
  stalenessSeconds?: string | number | null;
  stalenessBlocks?: string | number | null;
  avgFillPrice?: string | number | null;
  fillProbability?: string | number | null;
  blockVolume?: string | number | null;
  tradeCount?: string | number | null;
  availableNotional?: string | number | null;
  executionSource?: string | null;
  feeCost?: string | number | null;
  slippageCost?: string | number | null;
  executionCost?: string | number | null;
  pnl: string | number;
  pnlPct: string | number;
  holdingBars: number | string;
  exitReason: string;
};

export type QuantBacktestOrder = {
  runId: number;
  orderId: string;
  signalIndex?: string | number | null;
  tradeId?: string | null;
  xAxis: 'timestamp' | 'block_number' | string;
  signalX: number | string;
  submitX?: number | string | null;
  decisionPrice?: string | number | null;
  requestedPrice?: string | number | null;
  side: 'BUY' | 'SELL' | string;
  role?: string | null;
  orderType?: string | null;
  status: string;
  requestedSize?: string | number | null;
  requestedNotional?: string | number | null;
  filledSize?: string | number | null;
  filledNotional?: string | number | null;
  unfilledSize?: string | number | null;
  avgFillPrice?: string | number | null;
  fillProbability?: string | number | null;
  fillPct?: string | number | null;
  blockVolume?: string | number | null;
  tradeCount?: string | number | null;
  availableNotional?: string | number | null;
  feeCost?: string | number | null;
  slippageCost?: string | number | null;
  executionCost?: string | number | null;
  latencyBlocks?: string | number | null;
  latencySeconds?: string | number | null;
  noFillReason?: string | null;
  executionSource?: string | null;
  meta?: Record<string, unknown> | null;
};

export type QuantBacktestLedgerRow = {
  runId: number;
  ledgerId: string;
  orderId?: string | null;
  tradeId?: string | null;
  eventType: string;
  xAxis: 'timestamp' | 'block_number' | string;
  xValue: number | string;
  marketSlug?: string | null;
  tokenSide?: string | null;
  sharesDelta?: string | number | null;
  cashDelta?: string | number | null;
  fee?: string | number | null;
  rebate?: string | number | null;
  slippageCost?: string | number | null;
  executionCost?: string | number | null;
  realizedPnl?: string | number | null;
  positionAfter?: string | number | null;
  cashAfter?: string | number | null;
  price?: string | number | null;
  source?: string | null;
  meta?: Record<string, unknown> | null;
};

export type QuantBacktestCreatePayload = {
  marketSlug: string;
  tokenSide: string;
  tokenId?: string;
  outcomeLabel?: string;
  priceSource: string;
  backtestEngine?: string;
  from?: string;
  to?: string;
  fromBlock?: string;
  toBlock?: string;
  entryThreshold?: number;
  exitThreshold?: number;
  stopLoss?: number;
  takeProfit?: number;
  maxHoldingBars?: number;
  initialCapital?: number;
  positionSize?: number;
  feeBps?: number;
  slippageBps?: number;
  liquidityCapPct?: number;
  maxPositionNotional?: number;
  minFillPct?: number;
  executionProfile?: string;
  orderRole?: string;
  executionPriceMode?: string;
  finalValuationMode?: string;
  buyLimitPrice?: number;
  sellLimitPrice?: number;
  settlementValue?: number;
  latencySeconds?: number;
  latencyBlocks?: number;
  maxBookStalenessSeconds?: number;
  adverseSlippageCents?: number;
  fillProbabilityHaircutPct?: number;
  allowPartialFill?: boolean;
  minFillSize?: number;
  rejectOnStaleBook?: boolean;
  executionContext?: Record<string, unknown>;
};

export type QuantListPayload<T> = {
  items: T[];
  count: number;
  source?: string;
};

export type ContentItem = {
  id?: string | number;
  contentType?: string | null;
  source?: string | null;
  category?: string | null;
  topicId?: string | null;
  title?: string | null;
  url?: string | null;
  publishedAt?: string | null;
  summary?: string | null;
  provider?: string | null;
  relevanceScore?: number | null;
  sourceCount?: number | null;
};

export type ContentPayload = {
  marketId: number;
  items: ContentItem[];
  sourceMode?: string;
  topicIds?: string[];
};

export type SyncCheckpoint = {
  value?: string | number | null;
  lastBlock?: string | number | null;
  updatedAt?: string | null;
};

export type SystemHealth = {
  database?: string;
  redis?: boolean;
  apiStatus?: string;
  lobRuntime?: {
    status?: string;
    mode?: string;
    rollupWatermark?: string | number | null;
    deadLetters1h?: string | number | null;
    detail?: string | null;
  };
  contentSync?: { status?: string };
  syncState?: Record<string, SyncCheckpoint>;
  marketSync?: SyncCheckpoint | null;
  tradeSync?: SyncCheckpoint | null;
  oracleSync?: SyncCheckpoint | null;
  priceSync?: { status?: string; updatedAt?: string | null };
};

export type SeedHealthItem = {
  panelId: string;
  serviceName: string;
  status: string;
  freshness: string;
  expectedIntervalSeconds: number;
  lastAttemptAt?: string | null;
  lastSuccessAt?: string | null;
  attemptAgeSeconds?: number | null;
  successAgeSeconds?: number | null;
  recordCount?: number;
  sourceStates?: Record<string, string>;
  errorSummary?: string | null;
  cacheMode?: string | null;
  payloadStatus?: string | null;
  metadata?: Record<string, unknown>;
};

export type SeedHealthPayload = {
  generatedAt: string;
  status: string;
  summary: {
    watcherCount: number;
    okCount: number;
    degradedCount: number;
    errorCount: number;
  };
  items: SeedHealthItem[];
};

export type MarketDataQualityDimension = {
  id: string;
  label: string;
  status: string;
  numerator?: number | null;
  denominator?: number | null;
  coveragePct?: number | null;
  source: string;
  observedAt?: string | null;
  ageSeconds?: number | null;
  detail?: string | null;
};

export type MarketDataQualityLifecycleStage = {
  id: string;
  label: string;
  count?: number | null;
  source: string;
  detail?: string | null;
  status?: string | null;
};

export type MarketDataQualityGap = {
  id: string;
  severity: string;
  label: string;
  count: number;
  detail?: string | null;
  observedAt?: string | null;
  source: string;
};

export type MarketDataQualityGapMarket = {
  marketId: number;
  title: string;
  slug?: string | null;
  category?: string | null;
  endDate?: string | null;
  completionStatus?: string | null;
  observedAt?: string | null;
};

export type MarketDataQualityWatermark = {
  id: string;
  key: string;
  lastBlock?: number | string | null;
  updatedAt?: string | null;
  state?: unknown;
};

export type MarketDataQualityPayload = {
  contractVersion: 'prediction-market-data-quality.v1' | string;
  generatedAt: string;
  status: string;
  score: number;
  summary: {
    marketCount: number;
    servingMarketCount: number;
    recentlyTradedMarketCount: number;
    oracleEventCount: number;
    oracleBoundMarketCount: number;
    activeGapCount: number;
    criticalDimensionCount: number;
    warningDimensionCount: number;
    latestTradeAt?: string | null;
    latestOracleAt?: string | null;
  };
  dimensions: MarketDataQualityDimension[];
  lifecycle: MarketDataQualityLifecycleStage[];
  oracleLifecycle: {
    source: string;
    latestEventAt?: string | null;
    latestBlock?: number | string | null;
    stages: Array<{ id: string; label: string; count: number }>;
    recentEvents: OracleEvent[];
  };
  gaps: MarketDataQualityGap[];
  gapMarkets: MarketDataQualityGapMarket[];
  watermarks: MarketDataQualityWatermark[];
  semantics?: {
    eventIdentity?: string;
    canonicalOrder?: string;
    marketBridge?: string;
    score?: string;
  };
};

export type L2Level = {
  price?: string | number | null;
  size?: string | number | null;
};

export type LobSide = {
  bestBid?: string | number | null;
  bestAsk?: string | number | null;
  spread?: string | number | null;
  bids?: L2Level[];
  asks?: L2Level[];
};

export type LobPayload = {
  marketId: number;
  localMarketId?: number | null;
  marketTitle?: string;
  fetchedAt?: string;
  tokenMode?: boolean;
  source?: string | null;
  bookStatus?: 'ok' | 'no-book' | string | null;
  fallbackReason?: string | null;
  yes?: LobSide;
  no?: LobSide;
};

export type LobSnapshot = {
  snapshotId: number | string;
  tokenId: string;
  side: 'YES' | 'NO' | string;
  pairedTokenId?: string | null;
  marketTitle?: string | null;
  source?: string | null;
  bookStatus?: string | null;
  bestBid?: string | number | null;
  bestAsk?: string | number | null;
  spread?: string | number | null;
  mid?: string | number | null;
  bidDepth?: string | number | null;
  askDepth?: string | number | null;
  depthTotal?: string | number | null;
  imbalance?: string | number | null;
  levelCountBid?: number | string | null;
  levelCountAsk?: number | string | null;
  payload?: Record<string, unknown> | null;
  fetchedAt?: string | null;
  createdAt?: string | null;
};

export type LobSnapshotPayload = {
  tokenId: string;
  side?: string | null;
  items: LobSnapshot[];
  count: number;
};

export type WorkspaceIdentity = {
  localMarketId?: number | null;
  marketId?: number | null;
  gammaMarketId?: string | number | null;
  eventId?: string | number | null;
  eventSlug?: string | null;
  selectedOutcomeKey?: string | null;
  slug?: string | null;
  conditionId?: string | null;
  questionId?: string | null;
  oracle?: string | null;
  yesTokenId?: string | null;
  noTokenId?: string | null;
};

export type WorkspaceDiagnostics = {
  marketId?: number | null;
  identityStatus?: 'ok' | 'partial' | string;
  chartStatus?: 'ok' | 'short' | 'flat' | 'snapshot' | 'missing' | string;
  oracleStatus?: string | null;
  oracleEventCount?: number;
  tradeCount?: number;
  hasPrice?: boolean;
  hasLobTokens?: boolean;
  issues?: string[];
  level?: 'ok' | 'warn' | 'critical' | string;
};

export type MarketWorkspaceHealth = {
  marketId?: number | null;
  priceStatus?: 'ok' | 'missing' | 'stale' | string;
  chartStatus?: 'ok' | 'short' | 'flat' | 'snapshot' | 'missing' | 'missing-local-history' | string;
  oracleStatus?: 'bound' | 'open-no-events' | 'unbound' | 'mismatch' | string;
  lobStatus?: 'ok' | 'no-book' | 'missing' | 'not-loaded' | string;
  servingStatus?: 'ok' | 'partial' | 'missing' | 'fallback' | string;
  groupStatus?: 'ok' | 'single-market' | 'outcome-missing' | string;
  issues?: string[];
  level?: 'ok' | 'warn' | 'critical' | string;
};

export type MarketEvidenceClaim = {
  id: 'identity' | 'price' | 'history' | 'trades' | 'oracle' | 'group' | string;
  label: string;
  status: string;
  source: string;
  observedAt?: string | null;
  recordCount?: number | null;
  detail?: string | null;
  identifiers?: Record<string, string | number | null>;
};

export type MarketWorkspaceEvidence = {
  contractVersion: 'market-workspace-evidence.v1' | string;
  generatedAt?: string | null;
  claims: MarketEvidenceClaim[];
  issues?: string[];
};

export type WorkspaceBundle = {
  market: MarketSummary | null;
  identity?: WorkspaceIdentity | null;
  diagnostics?: WorkspaceDiagnostics | null;
  health?: MarketWorkspaceHealth | null;
  evidence?: MarketWorkspaceEvidence | null;
  group?: MarketGroupDetail | null;
  selectedOutcome?: MarketGroupOutcome | null;
  trades: TradeRow[];
  oracle: OraclePayload | null;
  price: PriceSummary | null;
  chart: ChartPayload | null;
  content: ContentPayload | null;
  lob: LobPayload | null;
  servingSource?: string | null;
  servingUpdatedAt?: string | null;
  generatedAt?: string | null;
  focusStatus?: 'ready' | 'warming' | string | null;
  cacheLayers?: Record<string, unknown> | null;
};

export type MarketAiInsightFocus = {
  label: string;
  title: string;
  summary: string;
  severity: 'positive' | 'warning' | 'critical' | 'neutral' | string;
  evidence?: string | null;
};

export type MarketAiInsightPayload = {
  market?: MarketSummary | MarketListItem | null;
  selectedGroup?: MarketGroupItem | MarketGroupDetail | null;
  selectedOutcome?: MarketGroupOutcome | null;
  price?: PriceSummary | null;
  lob?: LobPayload | null;
  trades?: TradeRow[];
  oracle?: OraclePayload | null;
  content?: ContentItem[];
};

export type MarketAiInsightResponse = {
  status: 'live' | 'fallback' | 'missing-api-key' | 'agent-error' | 'invalid-payload' | string;
  generatedAt?: string;
  model?: string;
  cacheStatus?: 'hit' | 'warming' | 'warming-in-progress' | string;
  cacheKey?: string;
  brief: string;
  focus: MarketAiInsightFocus[];
  evidence?: string[];
  searchResults?: Array<{
    title?: string;
    url?: string;
    content?: string;
  }>;
  error?: string;
};

export type MarketWideAiInsightLens = 'overview' | 'special' | 'trend';

export type MarketWideSpecialMarket = {
  title: string;
  why: string;
  trend?: string | null;
  severity?: string | null;
  evidence?: string | null;
};

export type MarketWideTheme = {
  label: string;
  title: string;
  summary: string;
  severity?: string | null;
  evidence?: string | null;
};

export type MarketWideWatchItem = {
  title: string;
  reason: string;
  horizon?: string | null;
  severity?: string | null;
};

export type MarketWideAiInsightPayload = {
  lens: MarketWideAiInsightLens;
  markets: MarketListItem[];
  marketGroups: MarketGroupItem[];
  trades: TradeRow[];
  oracle: OracleEvent[];
  content: ContentItem[];
  alphaSignals?: RuntimeTradeSignal[];
  whaleSignals?: RuntimeTradeSignal[];
  suspiciousSignals?: RuntimeTradeSignal[];
};

export type MarketWideAiInsightResponse = MarketAiInsightResponse & {
  lens?: MarketWideAiInsightLens | string;
  specialMarkets?: MarketWideSpecialMarket[];
  themes?: MarketWideTheme[];
  watchlist?: MarketWideWatchItem[];
  metrics?: Record<string, string | number | boolean | string[] | null>;
  viaGateway?: boolean;
  servedBy?: string;
  gatewayFallback?: boolean;
  source?: string;
  snapshotGeneratedAt?: string;
  snapshotExpiresAt?: string;
  snapshotLiveAttempted?: boolean;
  dailyBudget?: {
    enabled?: boolean;
    limit?: number | null;
    used?: number | null;
    remaining?: number | null;
    kind?: string;
  };
};

export type SparkPoint = {
  timestamp: string;
  value: number;
};

export type RuntimeMarketTicker = {
  id: string;
  label: string;
  symbol: string;
  price?: number | null;
  changePercent?: number | null;
  currency?: string | null;
  marketCap?: number | null;
  volume24h?: number | null;
  points: SparkPoint[];
};

export type RuntimeMarketGroup = {
  kind: string;
  items: RuntimeMarketTicker[];
  generatedAt?: string;
};

export type RuntimeCommodityTapeItem = {
  id: string;
  label: string;
  symbol: string;
  price?: number | null;
  changePct?: number | null;
  changeLabel?: string | null;
  tone?: 'up' | 'down' | 'neutral' | string;
};

export type RuntimeEquityExposure = {
  ticker: string;
  name: string;
  market?: string | null;
  role?: 'producer' | 'processor' | 'consumer' | 'spread' | string;
  direction?: 'positive' | 'negative' | 'spread' | 'weak' | string;
  score?: number | null;
  impactLabel?: string | null;
  confidence?: 'high' | 'medium' | 'low' | string;
  basis?: string | null;
};

export type RuntimeLinkedMarket = {
  id?: number | string | null;
  title?: string | null;
  slug?: string | null;
  query?: string | null;
  source?: string | null;
};

export type RuntimeTransmissionChain = {
  id: string;
  commodityId: string;
  chainLabel: string;
  shockLabel?: string | null;
  shockPct?: number | null;
  tone?: 'up' | 'down' | 'watch' | 'neutral' | string;
  demandRegime?: string | null;
  lagLabel?: string | null;
  confidence?: 'high' | 'medium' | 'low' | string;
  formula?: string | null;
  winners?: RuntimeEquityExposure[];
  losers?: RuntimeEquityExposure[];
  spreadWatch?: RuntimeEquityExposure[];
  linkedMarkets?: RuntimeLinkedMarket[];
};

export type RuntimeCommodityTransmissionPayload = {
  generatedAt?: string;
  panelId?: string | null;
  source?: string | null;
  cacheMode?: string | null;
  status?: 'ok' | 'partial' | 'model' | string | null;
  sources?: Record<string, string>;
  summary?: {
    signal?: string | null;
    signalLabel?: string | null;
    bias?: 'beneficiary' | 'pressure' | 'mixed' | 'model' | string | null;
    chainCount?: number | string | null;
    liveCommodityCount?: number | string | null;
    topShockLabel?: string | null;
    topShockChangeLabel?: string | null;
    positiveCount?: number | string | null;
    negativeCount?: number | string | null;
    spreadCount?: number | string | null;
  } | null;
  commodities?: RuntimeCommodityTapeItem[];
  transmissions?: RuntimeTransmissionChain[];
};

export type RuntimeF1PanelCard = {
  id?: string;
  kind?: 'meeting' | 'session' | 'result' | 'news' | string;
  status?: 'live' | 'upcoming' | 'completed' | string;
  topic?: string | null;
  phase?: string | null;
  detail?: string | null;
  title?: string | null;
  summary?: string | null;
  primaryMetric?: string | null;
  secondaryMetric?: string | null;
  tertiaryMetric?: string | null;
  quaternaryMetric?: string | null;
  accentColor?: string | null;
  url?: string | null;
  source?: string | null;
  publishedAt?: string | null;
};

export type RuntimeF1Meeting = {
  meetingKey?: number | null;
  meetingName?: string | null;
  officialName?: string | null;
  location?: string | null;
  countryName?: string | null;
  circuitName?: string | null;
  startAt?: string | null;
  endAt?: string | null;
  status?: string | null;
};

export type RuntimeF1Payload = {
  generatedAt?: string;
  season?: number;
  source?: string | null;
  sourceUrl?: string | null;
  status?: string | null;
  focusMeeting?: RuntimeF1Meeting | null;
  cards: RuntimeF1PanelCard[];
};

export type RuntimeJin10Item = {
  id: string;
  timestamp?: string | null;
  headline?: string | null;
  summary?: string | null;
  source?: string | null;
  url?: string | null;
  important?: boolean;
  locked?: boolean;
  vipLevel?: number | string | null;
  assetHints?: string[];
  channelIds?: number[];
};

export type RuntimeJin10Payload = {
  generatedAt?: string;
  source?: string | null;
  sourceUrl?: string | null;
  status?: string | null;
  items: RuntimeJin10Item[];
};

export type RuntimeCryptoFundingItem = {
  id: string;
  exchange?: string | null;
  symbol?: string | null;
  asset?: string | null;
  pair?: string | null;
  fundingRate?: number | null;
  fundingRatePercent?: number | null;
  annualizedPercent?: number | null;
  severity?: string | null;
  tone?: 'critical' | 'warning' | 'normal' | 'neutral' | string | null;
  abnormalScore?: number | null;
  direction?: 'positive' | 'negative' | 'flat' | string | null;
  marketState?: 'longs-pay-shorts' | 'shorts-pay-longs' | 'flat' | string | null;
  heatBand?: 'extreme' | 'strong' | 'medium' | 'light' | 'flat' | string | null;
  markPrice?: number | null;
  indexPrice?: number | null;
  nextFundingTime?: string | null;
  updatedAt?: string | null;
};

export type RuntimeCryptoFundingAsset = {
  id: string;
  asset?: string | null;
  symbol?: string | null;
  venues?: number | null;
  bias?: 'longs-pay' | 'shorts-pay' | 'mixed' | 'flat' | string | null;
  consensusFundingPercent?: number | null;
  spreadPercent?: number | null;
  maxAbsFundingPercent?: number | null;
  tone?: 'critical' | 'warning' | 'normal' | 'neutral' | string | null;
  nextFundingTime?: string | null;
  quotes: RuntimeCryptoFundingItem[];
};

export type RuntimeCryptoFundingPayload = {
  generatedAt?: string;
  source?: string | null;
  sourceUrl?: string | null;
  status?: string | null;
  sources?: Record<string, string>;
  venues?: string[];
  legend?: Record<string, string>;
  assets?: RuntimeCryptoFundingAsset[];
  items: RuntimeCryptoFundingItem[];
};

export type RuntimeNbaGame = {
  id?: string;
  name?: string;
  status?: string;
  state?: string;
  tipoff?: string;
  homeTeam?: string;
  awayTeam?: string;
  homeScore?: string | number | null;
  awayScore?: string | number | null;
  broadcast?: string | null;
};

export type RuntimeNbaPayload = {
  items: RuntimeNbaGame[];
  generatedAt?: string;
};

export type RuntimeNbaIntelItem = {
  headline?: string;
  description?: string | null;
  publishedAt?: string | null;
  url?: string | null;
  source?: string | null;
  type?: string | null;
};

export type RuntimeNbaLineupPlayer = {
  side?: string;
  playerName?: string;
  position?: string;
  lineupStatus?: string;
  timestamp?: string | null;
};

export type RuntimeNbaLineupGame = {
  gameId?: string | number | null;
  label?: string;
  status?: string | null;
  starters: RuntimeNbaLineupPlayer[];
};

export type RuntimeNbaIntelPayload = {
  items: RuntimeNbaIntelItem[];
  lineups: RuntimeNbaLineupGame[];
  generatedAt?: string;
};

export type RuntimeNbaMatchupPredictorItem = {
  eventId?: string;
  name?: string | null;
  shortName?: string | null;
  tipoff?: string | null;
  state?: string | null;
  status?: string | null;
  awayTeam?: string | null;
  homeTeam?: string | null;
  awayWinProbability?: number | null;
  homeWinProbability?: number | null;
  matchupQuality?: number | null;
  projectedMargin?: number | null;
  awayExpectedPoints?: number | null;
  homeExpectedPoints?: number | null;
  lastModified?: string | null;
};

export type RuntimeNbaMatchupPredictorPayload = {
  items: RuntimeNbaMatchupPredictorItem[];
  generatedAt?: string;
  source?: string;
};

export type RuntimeGridEsportsTeamMetric = {
  won?: boolean;
  score?: number | string | null;
  kills?: number | string | null;
  deaths?: number | string | null;
};

export type RuntimeGridEsportsPmContext = {
  status?: string | null;
  marketId?: string | number | null;
  title?: string | null;
  probability?: number | string | null;
  delta?: number | string | null;
  signal?: string | null;
  matchQuality?: string | null;
};

export type RuntimeGridEsportsItem = {
  id: string;
  gameTitle?: string | null;
  tournament?: string | null;
  series?: string | null;
  teamA?: string | null;
  teamB?: string | null;
  format?: string | null;
  startTime?: string | null;
  startedAt?: string | null;
  state?: 'live' | 'upcoming' | 'finished' | 'pending-state' | 'scheduled' | string | null;
  score?: string | null;
  currentMap?: string | null;
  liveContext?: string | null;
  momentum?: number | string | null;
  contextTags?: string[];
  teamMetrics?: RuntimeGridEsportsTeamMetric[];
  pm?: RuntimeGridEsportsPmContext | null;
};

export type RuntimeGridEsportsPayload = {
  generatedAt?: string;
  source?: string | null;
  sourceUrl?: string | null;
  status?: string | null;
  cacheMode?: string | null;
  sources?: Record<string, string>;
  window?: {
    gte?: string | null;
    lte?: string | null;
  } | null;
  summary?: {
    totalSeries?: number | string | null;
    visibleSeries?: number | string | null;
    liveSeries?: number | string | null;
    officialSnapshots?: number | string | null;
    pmLinked?: number | string | null;
  } | null;
  items: RuntimeGridEsportsItem[];
};

export type RuntimeSportsOddsQuote = {
  name?: string | null;
  bestPrice?: number | string | null;
  consensusProbability?: number | string | null;
  dispersion?: number | string | null;
  bookCount?: number | string | null;
};

export type RuntimeSportsOddsItem = {
  id: string;
  sportKey?: string | null;
  sportTitle?: string | null;
  commenceTime?: string | null;
  homeTeam?: string | null;
  awayTeam?: string | null;
  event?: string | null;
  marketType?: string | null;
  bookmakerCount?: number | string | null;
  bestPrice?: number | string | null;
  consensusProbability?: number | string | null;
  dispersion?: number | string | null;
  signal?: string | null;
  lastUpdate?: string | null;
  quotes?: RuntimeSportsOddsQuote[];
  pm?: RuntimeGridEsportsPmContext | null;
};

export type RuntimeSportsOddsPayload = {
  generatedAt?: string;
  source?: string | null;
  sourceUrl?: string | null;
  status?: string | null;
  cacheMode?: string | null;
  sources?: Record<string, string>;
  summary?: {
    eventCount?: number | string | null;
    bookmakerCount?: number | string | null;
    pmLinked?: number | string | null;
    wideCount?: number | string | null;
  } | null;
  items: RuntimeSportsOddsItem[];
};

export type RuntimeInflationNowcastRow = {
  [key: string]: string | undefined;
};

export type RuntimeInflationNowcastPayload = {
  monthOverMonth?: RuntimeInflationNowcastRow | null;
  yearOverYear?: RuntimeInflationNowcastRow | null;
  quarterly?: RuntimeInflationNowcastRow[];
  generatedAt?: string;
  source?: string;
  url?: string;
  status?: string | null;
  cacheMode?: string | null;
};

export type RuntimeGeoSanctionsShockSummary = {
  hotspotCount?: number;
  newSanctionsCount?: number;
  targetLabels?: string[];
  targetSummary?: string;
  nuclearRisk?: string;
  militaryFeed?: string;
};

export type RuntimeGeoSanctionsShockItem = {
  id?: string | null;
  kind?: string | null;
  headline?: string | null;
  summary?: string | null;
  source?: string | null;
  sourceUrl?: string | null;
  occurredAt?: string | null;
  severity?: string | null;
  targetLabels?: string[];
  country?: string | null;
  tags?: string[];
  sideA?: string | null;
  sideB?: string | null;
  locationLabel?: string | null;
  latitude?: number | string | null;
  longitude?: number | string | null;
  violenceType?: string | number | null;
  deathsBest?: number | null;
  deathsLow?: number | null;
  deathsHigh?: number | null;
};

export type RuntimeGeoSanctionsShockTargetBreakdown = {
  label?: string | null;
  count?: number | null;
  latestHeadline?: string | null;
  latestOccurredAt?: string | null;
  latestSource?: string | null;
};

export type RuntimeGeoSanctionsShockLinkedMarket = {
  marketId?: number | string | null;
  slug?: string | null;
  title?: string | null;
  matchedBy?: string | null;
  score?: number | null;
  gammaActive?: boolean;
};

export type RuntimeGeoSanctionsShockPayload = {
  generatedAt?: string;
  source?: string;
  sourceUrl?: string;
  cacheMode?: string | null;
  status?: string | null;
  sources?: Record<string, string>;
  conflictProvider?: string | null;
  conflictState?: string | null;
  summary?: RuntimeGeoSanctionsShockSummary | null;
  items?: RuntimeGeoSanctionsShockItem[];
  targetBreakdown?: RuntimeGeoSanctionsShockTargetBreakdown[];
  sanctionsTargetBreakdown?: RuntimeGeoSanctionsShockTargetBreakdown[];
  countryRiskBreakdown?: RuntimeGeoSanctionsShockTargetBreakdown[];
  linkedMarkets?: RuntimeGeoSanctionsShockLinkedMarket[];
  ofacRecordCountTotal?: number;
  publishDates?: string[];
};

export type RuntimePolymarketMacroMapOutcome = {
  outcomeKey?: string | null;
  gammaMarketId?: string | number | null;
  label?: string | null;
  title?: string | null;
  yesPrice?: number | string | null;
  noPrice?: number | string | null;
  volume24h?: number | string | null;
  conditionId?: string | null;
  slug?: string | null;
};

export type RuntimePolymarketMacroMapItem = {
  eventId?: string | number | null;
  slug?: string | null;
  title?: string | null;
  categoryIds?: string[];
  categoryLabels?: string[];
  marketTypes?: string[];
  endDate?: string | null;
  createdAt?: string | null;
  volume24h?: number | string | null;
  liquidity?: number | string | null;
  outcomeCount?: number | null;
  topOutcomes?: RuntimePolymarketMacroMapOutcome[];
};

export type RuntimePolymarketMacroMapCategory = {
  id?: string | null;
  label?: string | null;
  marketType?: string | null;
  activeCount?: number | null;
  topTitle?: string | null;
  volume24h?: number | string | null;
};

export type RuntimePolymarketMacroMapSummary = {
  activeCount?: number | null;
  categoryCount?: number | null;
  topCategory?: string | null;
  signal?: string | null;
  topCatalyst?: {
    title?: string | null;
    eventId?: string | number | null;
    slug?: string | null;
    endDate?: string | null;
    categoryLabels?: string[];
  } | null;
};

export type RuntimePolymarketMacroMapPayload = {
  generatedAt?: string;
  source?: string | null;
  sourceUrl?: string | null;
  cacheMode?: string | null;
  status?: string | null;
  sources?: Record<string, string>;
  summary?: RuntimePolymarketMacroMapSummary | null;
  categories?: RuntimePolymarketMacroMapCategory[];
  items?: RuntimePolymarketMacroMapItem[];
};

export type RuntimeCpiCalendarItem = {
  id?: string | null;
  kind?: 'cpi' | 'pce' | 'nfp' | 'fomc' | string;
  title?: string | null;
  referencePeriod?: string | null;
  releaseAt?: string | null;
  releaseTimeEt?: string | null;
  source?: string | null;
  sourceUrl?: string | null;
  marketRelevance?: string | null;
};

export type RuntimeCpiCalendarBaseline = {
  status?: string | null;
  label?: string | null;
  probability?: number | string | null;
  marketTitle?: string | null;
  marketSlug?: string | null;
  source?: string | null;
};

export type RuntimeCpiCalendarSummary = {
  nextEvent?: RuntimeCpiCalendarItem | null;
  nextCpi?: RuntimeCpiCalendarItem | null;
  nextPce?: RuntimeCpiCalendarItem | null;
  nextNfp?: RuntimeCpiCalendarItem | null;
  nextFomc?: RuntimeCpiCalendarItem | null;
  signal?: string | null;
  risk?: string | null;
  hoursToEvent?: number | string | null;
  baselineLabel?: string | null;
  baselineProbability?: number | string | null;
  consensusStatus?: string | null;
};

export type RuntimeCpiReleaseCalendarPayload = {
  generatedAt?: string;
  source?: string | null;
  sourceUrl?: string | null;
  cacheMode?: string | null;
  status?: string | null;
  sources?: Record<string, string>;
  summary?: RuntimeCpiCalendarSummary | null;
  baseline?: RuntimeCpiCalendarBaseline | null;
  consensus?: RuntimeCpiCalendarBaseline | null;
  items?: RuntimeCpiCalendarItem[];
};

export type RuntimeEnergyShockItem = {
  key?: string | null;
  label?: string | null;
  unit?: string | null;
  cadence?: string | null;
  date?: string | null;
  value?: number | string | null;
  change1?: number | string | null;
  changeWeek?: number | string | null;
  source?: string | null;
  sourceUrl?: string | null;
};

export type RuntimeEnergyShockSummary = {
  signal?: string | null;
  bias?: string | null;
  headlineImpulsePp?: number | string | null;
  linkedMarkets?: string[];
};

export type RuntimeEnergyGasolineShockPayload = {
  generatedAt?: string;
  source?: string | null;
  sourceUrl?: string | null;
  cacheMode?: string | null;
  status?: string | null;
  sources?: Record<string, string>;
  summary?: RuntimeEnergyShockSummary | null;
  items?: RuntimeEnergyShockItem[];
};

export type RuntimeWeatherQuoteBin = {
  label?: string | null;
  bucketType?: string | null;
  minTemp?: number | string | null;
  maxTemp?: number | string | null;
  minValue?: number | string | null;
  maxValue?: number | string | null;
  unit?: string | null;
  metricType?: string | null;
  marketFamily?: string | null;
  bestBidYes?: number | string | null;
  bestAskYes?: number | string | null;
  midPriceYes?: number | string | null;
  marketId?: number | string | null;
  marketSlug?: string | null;
  marketStatus?: string | null;
  priceSource?: string | null;
  bookStatus?: string | null;
  yesTokenId?: string | null;
};

export type RuntimeGlobalWeatherCity = {
  cityId?: string | null;
  city?: string | null;
  country?: string | null;
  region?: string | null;
  lat?: number | string | null;
  lon?: number | string | null;
  timezone?: string | null;
  unit?: string | null;
  icao?: string | null;
  labelDx?: number | string | null;
  labelDy?: number | string | null;
  condition?: string | null;
  weatherCode?: number | string | null;
  currentTemp?: number | string | null;
  currentWindSpeed?: number | string | null;
  currentWindGust?: number | string | null;
  currentPrecipitation?: number | string | null;
  todayHigh?: number | string | null;
  todayLow?: number | string | null;
  todayWindSpeed?: number | string | null;
  todayWindGust?: number | string | null;
  todayPrecipitationSum?: number | string | null;
  todayPrecipitationProbability?: number | string | null;
  forecastHigh?: number | string | null;
  forecastWindSpeedMax?: number | string | null;
  forecastWindGustMax?: number | string | null;
  forecastPrecipitationSum?: number | string | null;
  forecastPrecipitationProbabilityMax?: number | string | null;
  windSpeedUnit?: string | null;
  precipitationUnit?: string | null;
  metarTemp?: number | string | null;
  hourly?: Array<{
    time?: string | null;
    temp?: number | string | null;
    precipitation?: number | string | null;
    precipitationProbability?: number | string | null;
    windSpeed?: number | string | null;
    windGust?: number | string | null;
    weatherCode?: number | string | null;
  }>;
  daily?: Array<{
    date?: string | null;
    high?: number | string | null;
    low?: number | string | null;
    precipitationSum?: number | string | null;
    precipitationProbabilityMax?: number | string | null;
    windSpeedMax?: number | string | null;
    windGustMax?: number | string | null;
    weatherCode?: number | string | null;
  }>;
  eventSlug?: string | null;
  eventTitle?: string | null;
  marketSource?: string | null;
  eventStatus?: string | null;
  marketFamily?: string | null;
  marketFamilyLabel?: string | null;
  metricType?: string | null;
  marketUrl?: string | null;
  quoteCoverage?: string | null;
  topBin?: RuntimeWeatherQuoteBin | null;
  bins?: RuntimeWeatherQuoteBin[];
  markets?: Array<{
    eventSlug?: string | null;
    eventTitle?: string | null;
    marketSource?: string | null;
    eventStatus?: string | null;
    marketFamily?: string | null;
    marketFamilyLabel?: string | null;
    metricType?: string | null;
    marketUrl?: string | null;
    quoteCoverage?: string | null;
    topBin?: RuntimeWeatherQuoteBin | null;
    bins?: RuntimeWeatherQuoteBin[];
    updatedAt?: string | null;
  }>;
  marketFamilies?: string[];
  sourceStates?: Record<string, string>;
  weatherCarryForward?: boolean;
  weatherCarryForwardFields?: string[];
  weatherUpdatedAt?: string | null;
  updatedAt?: string | null;
};

export type RuntimeGlobalWeatherMapPayload = {
  generatedAt?: string;
  source?: string | null;
  sourceUrl?: string | null;
  cacheMode?: string | null;
  status?: string | null;
  sources?: Record<string, string>;
  summary?: {
    cityCount?: number | string | null;
    mappedCount?: number | string | null;
    liveMarketCount?: number | string | null;
    staleCount?: number | string | null;
    hottestCity?: RuntimeGlobalWeatherCity | null;
    marketFamilyCounts?: Record<string, number | string>;
    unmappedMarketCount?: number | string | null;
  } | null;
  items?: RuntimeGlobalWeatherCity[];
  unmappedMarkets?: Array<Record<string, unknown>>;
};

export type RuntimeWeatherNewsItem = {
  id?: string | null;
  cityId?: string | null;
  city?: string | null;
  source?: string | null;
  title?: string | null;
  summary?: string | null;
  publishedAt?: string | null;
  url?: string | null;
  severity?: string | null;
  tags?: string[];
  marketFamily?: string | null;
};

export type RuntimeWeatherNewsPayload = {
  generatedAt?: string;
  source?: string | null;
  sourceUrl?: string | null;
  cacheMode?: string | null;
  status?: string | null;
  sources?: Record<string, string>;
  summary?: {
    articleCount?: number | string | null;
    cityCount?: number | string | null;
    warningCount?: number | string | null;
    topCity?: string | null;
  } | null;
  items?: RuntimeWeatherNewsItem[];
};

export type RuntimeFoodBasketItem = {
  key?: string | null;
  seriesId?: string | null;
  label?: string | null;
  date?: string | null;
  value?: number | string | null;
  momPct?: number | string | null;
  yoyPct?: number | string | null;
  threeMonthPct?: number | string | null;
  source?: string | null;
  sourceUrl?: string | null;
};

export type RuntimeFoodBasketSummary = {
  signal?: string | null;
  bias?: string | null;
  pressureScore?: number | string | null;
  topMover?: RuntimeFoodBasketItem | null;
  coverage?: number | string | null;
};

export type RuntimeFoodRetailBasketPayload = {
  generatedAt?: string;
  source?: string | null;
  sourceUrl?: string | null;
  cacheMode?: string | null;
  status?: string | null;
  sources?: Record<string, string>;
  summary?: RuntimeFoodBasketSummary | null;
  items?: RuntimeFoodBasketItem[];
};

export type RuntimeMacroDriverItem = {
  key?: string | null;
  seriesId?: string | null;
  label?: string | null;
  group?: string | null;
  icon?: string | null;
  metric?: string | null;
  unit?: string | null;
  date?: string | null;
  value?: number | string | null;
  change?: number | string | null;
  changePct?: number | string | null;
  yoyPct?: number | string | null;
  tone?: string | null;
  source?: string | null;
  sourceUrl?: string | null;
};

export type RuntimeMacroDriverSummary = {
  signal?: string | null;
  bias?: string | null;
  hotCount?: number | string | null;
  coolCount?: number | string | null;
  watchCount?: number | string | null;
  coverage?: number | string | null;
  sourceCount?: number | string | null;
  topMover?: RuntimeMacroDriverItem | null;
  linkedMarketCategories?: string[];
  panelId?: string | null;
};

export type RuntimeMacroDriverPayload = {
  generatedAt?: string;
  source?: string | null;
  sourceUrl?: string | null;
  cacheMode?: string | null;
  status?: string | null;
  sources?: Record<string, string>;
  summary?: RuntimeMacroDriverSummary | null;
  items?: RuntimeMacroDriverItem[];
};

export type RuntimeMacroRegistryItem = {
  key?: string | null;
  type?: string | null;
  group?: string | null;
  label?: string | null;
  value?: number | string | null;
  unit?: string | null;
  valueLabel?: string | null;
  change?: number | string | null;
  changeLabel?: string | null;
  date?: string | null;
  tone?: string | null;
  source?: string | null;
  sourceUrl?: string | null;
  sourceLabel?: string | null;
  domainTag?: string | null;
  severityLabel?: string | null;
  ageLabel?: string | null;
  rank?: number | string | null;
  implication?: string | null;
};

export type RuntimeMacroRegistrySummary = {
  panelId?: string | null;
  signal?: string | null;
  signalLabel?: string | null;
  bias?: string | null;
  hotCount?: number | string | null;
  coolCount?: number | string | null;
  watchCount?: number | string | null;
  rowCount?: number | string | null;
  coverage?: number | string | null;
  sourceCount?: number | string | null;
  topMover?: RuntimeMacroRegistryItem | null;
  topLabel?: string | null;
  topValueLabel?: string | null;
  topChangeLabel?: string | null;
  sourceLabel?: string | null;
};

export type RuntimeMacroRegistryPayload = {
  generatedAt?: string;
  panelId?: string | null;
  source?: string | null;
  sourceUrl?: string | null;
  cacheMode?: string | null;
  status?: string | null;
  sources?: Record<string, string>;
  summary?: RuntimeMacroRegistrySummary | null;
  items?: RuntimeMacroRegistryItem[];
};

export type RuntimeCpiReleaseCommandEvent = {
  key?: string | null;
  title?: string | null;
  period?: string | null;
  releaseAt?: string | null;
  status?: string | null;
  unit?: string | null;
  actual?: number | string | null;
  actualLabel?: string | null;
  forecast?: number | string | null;
  forecastLabel?: string | null;
  forecastKind?: string | null;
  previous?: number | string | null;
  previousLabel?: string | null;
  surprise?: number | string | null;
  surpriseLabel?: string | null;
  seriesId?: string | null;
  source?: string | null;
  sourceUrl?: string | null;
  forecastSource?: string | null;
  forecastSourceUrl?: string | null;
  asOf?: string | null;
};

export type RuntimeCpiReleaseCommandPayload = RuntimeMacroRegistryPayload & {
  release?: RuntimeCpiCalendarItem & {
    hoursToEvent?: number | string | null;
  } | null;
  events?: RuntimeCpiReleaseCommandEvent[];
  actualSeries?: Record<string, unknown>;
  summary?: (RuntimeMacroRegistrySummary & {
    period?: string | null;
    eventCount?: number | string | null;
    actualCount?: number | string | null;
    forecastCount?: number | string | null;
    previousCount?: number | string | null;
    hoursToEvent?: number | string | null;
  }) | null;
};

export type RuntimeTradeSignal = {
  marketId?: number | null;
  marketTitle?: string | null;
  timestamp?: string | null;
  txHash?: string | null;
  eventTime?: string | null;
  eventStatus?: string | null;
  side?: string | null;
  outcome?: string | null;
  price?: string | null;
  size?: string | null;
  notional?: string | null;
  severity?: string | null;
  title?: string | null;
  summary?: string | null;
  kind?: string | null;
  bias?: string | null;
  sourceLabel?: string | null;
  sourceTag?: string | null;
  headline?: string | null;
  action?: RuntimeSignalAction | null;
  contributors?: string[];
  addresses?: RuntimeSignalAddress[];
  relatedContent?: RuntimeSignalContent[];
  metrics?: RuntimeSignalMetrics | null;
};

export type RuntimeSignalAction = {
  label?: string | null;
  outcome?: string | null;
};

export type RuntimeSignalAddress = {
  address?: string | null;
  shortAddress?: string | null;
  labels?: string[];
  tradeCount?: number | string | null;
  volumeNotional?: string | null;
  marketTradeCount?: number | string | null;
  marketVolumeNotional?: string | null;
  firstTradeAt?: string | null;
  firstMarketTradeAt?: string | null;
  isNewAddress?: boolean;
  isNewToMarket?: boolean;
};

export type RuntimeSignalContent = {
  source?: string | null;
  title?: string | null;
  url?: string | null;
  publishedAt?: string | null;
  summary?: string | null;
};

export type RuntimeSignalMetrics = {
  totalNotional?: string | null;
  avgPrice?: string | null;
  currentProbability?: string | null;
  accountCount?: number | string | null;
  newAccountCount?: number | string | null;
  newToMarketCount?: number | string | null;
  tradeCount?: number | string | null;
  score?: string | null;
};

export type RuntimeSignalPayload = {
  items: RuntimeTradeSignal[];
  generatedAt?: string;
};

export type RuntimePolybeatsWallet = RuntimeSignalAddress & {
  netCashPnlProxy?: string | null;
  tradeCashPnl?: string | null;
  redeemCashflow?: string | null;
  pnlSource?: string | null;
  smartScore?: string | null;
  activeMarkets?: number | string | null;
  officialLikePnl?: string | null;
  qualityTier?: string | null;
  qualityErrorPct?: string | null;
  qualityReason?: string | null;
  categoryEdge?: RuntimePolybeatsCategoryEdge | null;
  marketExposure?: RuntimePolybeatsMarketExposure[];
  riskFlags?: string[];
  recentTrades?: RuntimePolybeatsTradeEvidence[];
};

export type RuntimePolybeatsPnlSummary = {
  address?: string | null;
  safeBlock?: number | string | null;
  realizedTradePnl?: string | null;
  nonTradePnl?: string | null;
  conversionCollateralDelta?: string | null;
  unrealizedPositionValue?: string | null;
  officialLikePnl?: string | null;
  buyUsdc?: string | null;
  sellUsdc?: string | null;
  redeemUsdc?: string | null;
  mergeUsdc?: string | null;
  splitUsdc?: string | null;
  makerRebateUsdc?: string | null;
};

export type RuntimePolybeatsQuality = {
  address?: string | null;
  qualityTier?: string | null;
  errorPct?: string | null;
  reason?: string | null;
  benchmarkSource?: string | null;
  benchmarkPnl?: string | null;
  calculatedPnl?: string | null;
  safeBlock?: number | string | null;
  flags?: string[];
  userName?: string | null;
  rank?: string | number | null;
};

export type RuntimePolybeatsCategoryEdge = {
  category?: string | null;
  tradeCount?: number | string | null;
  buyNotional?: string | null;
  sellNotional?: string | null;
  netPnl?: string | null;
  winRate?: string | null;
  avgPositionSize?: string | null;
  largestWin?: string | null;
  largestLoss?: string | null;
  resolvedMarketCount?: number | string | null;
};

export type RuntimePolybeatsTradeEvidence = {
  txHash?: string | null;
  marketId?: number | string | null;
  marketTitle?: string | null;
  conditionId?: string | null;
  side?: string | null;
  outcome?: string | null;
  price?: string | null;
  size?: string | null;
  notional?: string | null;
  blockNumber?: number | string | null;
  logIndex?: number | string | null;
  createdAt?: string | null;
  timestamp?: string | null;
  maker?: string | null;
  taker?: string | null;
};

export type RuntimePolybeatsMarketExposure = {
  marketId?: number | string | null;
  title?: string | null;
  conditionId?: string | null;
  category?: string | null;
  side?: string | null;
  outcome?: string | null;
  netPosition?: string | null;
  avgEntryPrice?: string | null;
  realizedPnl?: string | null;
  unrealizedValue?: string | null;
  currentProbability?: string | null;
  tradeCount?: number | string | null;
  firstTradeBlock?: number | string | null;
  lastTradeBlock?: number | string | null;
};

export type RuntimePolybeatsAddressProfile = {
  address?: string | null;
  shortAddress?: string | null;
  generatedAt?: string | null;
  safeBlock?: number | string | null;
  pnlSummary?: RuntimePolybeatsPnlSummary | null;
  quality?: RuntimePolybeatsQuality | null;
  categoryStats?: RuntimePolybeatsCategoryEdge[];
  categoryEdge?: RuntimePolybeatsCategoryEdge | null;
  recentTrades?: RuntimePolybeatsTradeEvidence[];
  marketExposure?: RuntimePolybeatsMarketExposure[];
  riskFlags?: string[];
};

export type RuntimePolybeatsMarketInfo = {
  marketId?: number | string | null;
  title?: string | null;
  slug?: string | null;
  conditionId?: string | null;
  category?: string | null;
  tags?: string[];
  currentProbability?: string | null;
  latestYesPrice?: string | null;
  latestNoPrice?: string | null;
  volume24h?: string | null;
  tradeCount24h?: number | string | null;
  lastTradeAt?: string | null;
  status?: string | null;
  oracleRisk?: string[];
  oracle?: string | null;
  settlementSource?: string | null;
  settlementOutcome?: string | null;
  enableNegRisk?: boolean | null;
};

export type RuntimePolybeatsAgentSummary = {
  brief?: string | null;
  whyItMatters?: string | null;
  walletRead?: string | null;
  marketRead?: string | null;
  risk?: string | null;
  confidence?: string | null;
  contentText?: string | null;
  tags?: string[];
};

export type RuntimePolybeatsItem = RuntimeTradeSignal & {
  id?: string | null;
  signalType?: string | null;
  domain?: string | null;
  marketSlug?: string | null;
  marketCategory?: string | null;
  tags?: string[];
  wallets?: RuntimePolybeatsWallet[];
  addressProfiles?: RuntimePolybeatsAddressProfile[];
  orderfilled?: RuntimePolybeatsTradeEvidence | null;
  tradeEvidence?: RuntimePolybeatsTradeEvidence[];
  market?: RuntimePolybeatsMarketInfo | null;
  marketIntel?: {
    netFlow?: Record<string, unknown> | null;
    pnlQualitySummary?: Record<string, unknown> | null;
    riskFlags?: string[];
  } | null;
  agentSummary?: RuntimePolybeatsAgentSummary | null;
  evidencePacket?: Record<string, unknown> | null;
  reasonCodes?: string[];
  explanation?: string | null;
  narrativeSource?: string | null;
  signalScore?: string | null;
  signalScoreValue?: string | null;
  signalConfidence?: string | null;
  smartSignal?: boolean | null;
  sourceLayer?: string | null;
  sourceTables?: string[];
  contentText?: string | null;
};

export type RuntimePolybeatsPayload = {
  items: RuntimePolybeatsItem[];
  generatedAt?: string;
  status?: string | null;
  cacheMode?: string | null;
  source?: string | null;
  sources?: Record<string, string | null | undefined>;
};

export type RuntimeMarketTvWireCategory = {
  id: string;
  label: string;
  count: number;
};

export type RuntimeMarketTvWireSourceState = {
  status?: string | null;
  count?: number | null;
  lastSuccessAt?: string | null;
  error?: string | null;
};

export type RuntimeMarketTvWireSummary = {
  total?: number | null;
  liveReady?: number | null;
  marketMatched?: number | null;
  regions?: number | null;
  staleCount?: number | null;
  blockedCount?: number | null;
  embedReady?: number | null;
};

export type RuntimeMarketTvWireItem = {
  id: string;
  name?: string | null;
  displayName?: string | null;
  category?: 'macro' | 'geo' | 'weather' | 'sports' | 'crypto' | 'news' | 'other' | string | null;
  sourceRole?: 'channel' | 'visual' | string | null;
  sourceType?: 'hls' | 'youtube' | 'external' | 'timelapse' | string | null;
  region?: string | null;
  country?: string | null;
  language?: string | null;
  hlsUrl?: string | null;
  hlsProxyRequired?: boolean | null;
  hlsProxyReferer?: string | null;
  hlsProbeStatus?: 'playable' | 'blocked' | 'timeout' | 'error' | 'missing' | 'skipped' | 'unverified' | string | null;
  hlsProbeError?: string | null;
  hlsProbeStreams?: string[];
  youtubeHandle?: string | null;
  youtubeLiveVideoId?: string | null;
  youtubeLiveTitle?: string | null;
  youtubeHlsUrl?: string | null;
  youtubeEmbedUrl?: string | null;
  youtubeEmbedMode?: 'live-video' | 'video' | 'channel-live' | string | null;
  youtubeChannelId?: string | null;
  youtubeProbeStatus?: 'live' | 'offline' | 'error' | 'skipped' | 'disabled' | string | null;
  youtubeChannelName?: string | null;
  youtubeChannelExists?: boolean | null;
  youtubeProbeError?: string | null;
  fallbackVideoId?: string | null;
  externalUrl?: string | null;
  quality?: string | null;
  status?: 'ready' | 'stale' | 'not_24_7' | 'blocked' | 'failed' | 'unknown' | string | null;
  availability?: 'public' | 'geo_limited' | 'unknown' | string | null;
  sourceName?: string | null;
  sourceUrl?: string | null;
  playbackTier?: string | null;
  playbackStrategy?: string | null;
  marketTags?: string[];
  matchedTerms?: string[];
  marketUseCase?: string | null;
  relevanceScore?: number | string | null;
  lastCheckedAt?: string | null;
  failureReason?: string | null;
  curated?: boolean | null;
};

export type RuntimeMarketTvWirePayload = {
  generatedAt?: string;
  status?: 'ok' | 'degraded' | 'empty' | 'invalid' | 'warming' | string | null;
  cacheMode?: 'seeded' | 'stale' | 'warming' | 'live-build' | string | null;
  source?: string | null;
  sourceUrl?: string | null;
  selection?: {
    category?: string | null;
    total?: number | string | null;
    returned?: number | string | null;
    limit?: number | string | null;
    truncated?: boolean | null;
  } | null;
  summary?: RuntimeMarketTvWireSummary | null;
  categories?: RuntimeMarketTvWireCategory[];
  sources?: Record<string, RuntimeMarketTvWireSourceState | string | null | undefined>;
  items: RuntimeMarketTvWireItem[];
  errors?: string[];
};

export type RuntimeMarketYoutubeChannelsPayload = RuntimeMarketTvWirePayload;

export type RuntimeEvidenceMarketLink = {
  marketId?: string | number | null;
  slug?: string | null;
  question?: string | null;
  marketUrl?: string | null;
  matchScore?: number | string | null;
  matchReasons?: string[];
};

export type RuntimeBreakingEventRadarItem = {
  id?: string | null;
  topic?: string | null;
  entity?: string | null;
  country?: string | null;
  team?: string | null;
  title?: string | null;
  summary?: string | null;
  eventTime?: string | null;
  source?: string | null;
  sourceUrl?: string | null;
  evidenceType?: string | null;
  mentionCount?: number | string | null;
  mentionCount15m?: number | string | null;
  mentionCount1h?: number | string | null;
  mentionCount24h?: number | string | null;
  velocityScore?: number | string | null;
  sourceDiversity?: number | string | null;
  countrySpread?: number | string | null;
  tone?: number | string | null;
  wikiPageviewDelta?: number | string | null;
  confidence?: number | string | null;
  severity?: 'alert' | 'watch' | 'normal' | string | null;
  tags?: string[];
  relatedPolymarketMarketIds?: Array<string | number>;
  markets?: RuntimeEvidenceMarketLink[];
  evidence?: Record<string, unknown> | null;
};

export type RuntimeBreakingEventRadarPayload = {
  panelId?: string;
  generatedAt?: string;
  status?: string | null;
  cacheMode?: string | null;
  freshness?: string | null;
  source?: string | null;
  sourceUrl?: string | null;
  sources?: Record<string, unknown>;
  summary?: {
    total?: number | string | null;
    alerts?: number | string | null;
    watch?: number | string | null;
    topEntity?: string | null;
    topVelocity?: number | string | null;
  } | null;
  items: RuntimeBreakingEventRadarItem[];
  errors?: string[];
};

export type RuntimeWorldCupMatchOpsItem = {
  id?: string | null;
  topic?: string | null;
  entity?: string | null;
  country?: string | null;
  team?: string | null;
  eventTime?: string | null;
  sourceUrl?: string | null;
  confidence?: number | string | null;
  homeTeam?: string | null;
  awayTeam?: string | null;
  score?: { home?: number | string | null; away?: number | string | null } | null;
  matchStatus?: string | null;
  stage?: string | null;
  group?: string | null;
  round?: string | null;
  kickoffUtc?: string | null;
  kickoffLocal?: string | null;
  kickoffBeijing?: string | null;
  minutesUntilKickoff?: number | string | null;
  venue?: string | null;
  cityId?: string | null;
  city?: string | null;
  weatherRisk?: { level?: string | null; score?: number | string | null; label?: string | null } | null;
  weather?: Record<string, unknown> | null;
  broadcastSources?: unknown[];
  marketLinked?: boolean | null;
  oddsLinked?: boolean | null;
  relatedPolymarketMarketIds?: Array<string | number>;
  markets?: RuntimeEvidenceMarketLink[];
  evidence?: Record<string, unknown> | null;
};

export type RuntimeWorldCupMatchOpsPayload = {
  panelId?: string;
  generatedAt?: string;
  status?: string | null;
  cacheMode?: string | null;
  freshness?: string | null;
  source?: string | null;
  sourceUrl?: string | null;
  sources?: Record<string, unknown>;
  summary?: {
    total?: number | string | null;
    returned?: number | string | null;
    linkedMarkets?: number | string | null;
    weatherWatch?: number | string | null;
    nextKickoffAt?: string | null;
    nextMatch?: string | null;
  } | null;
  items: RuntimeWorldCupMatchOpsItem[];
};

export type RuntimeGlobalTransportShippingItem = {
  id?: string | null;
  topic?: string | null;
  entity?: string | null;
  country?: string | null;
  team?: string | null;
  eventTime?: string | null;
  sourceUrl?: string | null;
  evidenceType?: string | null;
  title?: string | null;
  summary?: string | null;
  metric?: number | string | null;
  metricLabel?: string | null;
  confidence?: number | string | null;
  severity?: 'alert' | 'watch' | 'normal' | string | null;
  tags?: string[];
  relatedPolymarketMarketIds?: Array<string | number>;
  markets?: RuntimeEvidenceMarketLink[];
  evidence?: Record<string, unknown> | null;
};

export type RuntimeGlobalTransportShippingPayload = {
  panelId?: string;
  generatedAt?: string;
  status?: string | null;
  cacheMode?: string | null;
  freshness?: string | null;
  source?: string | null;
  sourceUrl?: string | null;
  sources?: Record<string, unknown>;
  sourceHealth?: Record<string, unknown>;
  cachePolicy?: Record<string, unknown>;
  summary?: {
    airports?: number | string | null;
    routes?: number | string | null;
    visibleRoutes?: number | string | null;
    flightSamples?: number | string | null;
    liveFlightSamples?: number | string | null;
    countries?: number | string | null;
    topHub?: string | null;
    transitFeeds?: number | string | null;
    transitOperators?: number | string | null;
    transitCatalogFiles?: number | string | null;
    transitScannedFiles?: number | string | null;
    aisStatus?: string | null;
    openSkyStatus?: string | null;
    openSkyRegions?: number | string | null;
    adsbStatus?: string | null;
    adsbRegions?: number | string | null;
    liveFlightSource?: string | null;
    evidenceVersion?: string | null;
  } | null;
  evidence?: {
    schemaVersion?: string | null;
    routes?: Array<Record<string, unknown>>;
    risks?: Array<Record<string, unknown>>;
    ops?: Array<Record<string, unknown>>;
  } | null;
  aviation?: {
    generatedAt?: string | null;
    mode?: string | null;
    hubs?: Array<{
      code?: string | null;
      name?: string | null;
      city?: string | null;
      country?: string | null;
      lat?: number | string | null;
      lon?: number | string | null;
      routeCount?: number | string | null;
      status?: string | null;
      riskScore?: number | string | null;
      delayScore?: number | string | null;
      trend?: Array<number | string | null>;
      source?: string | null;
      sourceUrl?: string | null;
    }>;
    routes?: Array<{
      id?: string | null;
      fromCode?: string | null;
      toCode?: string | null;
      fromName?: string | null;
      toName?: string | null;
      fromCity?: string | null;
      toCity?: string | null;
      fromCountry?: string | null;
      toCountry?: string | null;
      fromLat?: number | string | null;
      fromLon?: number | string | null;
      toLat?: number | string | null;
      toLon?: number | string | null;
      airline?: string | null;
      equipment?: string | null;
      corridor?: string | null;
      trafficScore?: number | string | null;
      riskScore?: number | string | null;
      status?: string | null;
      layer?: string | null;
      phase?: number | string | null;
      speed?: number | string | null;
      riskSources?: string[];
      riskReason?: string | null;
      confidence?: number | string | null;
      source?: string | null;
      sourceUrl?: string | null;
      trend?: Array<number | string | null>;
      relatedPolymarketMarketIds?: Array<string | number>;
    }>;
    flights?: Array<{
      id?: string | null;
      callsign?: string | null;
      fromCode?: string | null;
      toCode?: string | null;
      fromLat?: number | string | null;
      fromLon?: number | string | null;
      toLat?: number | string | null;
      toLon?: number | string | null;
      phase?: number | string | null;
      speed?: number | string | null;
      status?: string | null;
      riskScore?: number | string | null;
      trafficScore?: number | string | null;
      riskSources?: string[];
      riskReason?: string | null;
      layer?: string | null;
      source?: string | null;
      sourceUrl?: string | null;
    }>;
    liveFlights?: Array<{
      id?: string | null;
      icao24?: string | null;
      callsign?: string | null;
      originCountry?: string | null;
      region?: string | null;
      regionLabel?: string | null;
      lat?: number | string | null;
      lon?: number | string | null;
      baroAltitude?: number | string | null;
      velocity?: number | string | null;
      heading?: number | string | null;
      verticalRate?: number | string | null;
      onGround?: boolean | null;
      lastContact?: number | string | null;
      status?: string | null;
      riskScore?: number | string | null;
      source?: string | null;
      sourceUrl?: string | null;
      registration?: string | null;
      aircraftType?: string | null;
      updatedAt?: string | null;
    }>;
    ops?: Array<Record<string, unknown>>;
    airlines?: Array<{ name?: string | null; routeCount?: number | string | null; status?: string | null; exposureScore?: number | string | null; trend?: Array<number | string | null>; sourceUrl?: string | null }>;
    news?: Array<Record<string, unknown>>;
  } | null;
  items: RuntimeGlobalTransportShippingItem[];
};

export type AviationViewportPayload = {
  schemaVersion: 'aviation-viewport.v1';
  generatedAt: string;
  status: string;
  bbox: [number, number, number, number];
  zoom: number;
  aircraft: NonNullable<NonNullable<RuntimeGlobalTransportShippingPayload['aviation']>['liveFlights']>;
  aircraftCount: number;
  availableAircraftCount?: number;
  source?: string | null;
  sourceUrl?: string | null;
  errorCode?: string | null;
  limitations?: string[];
};

export type RuntimeNewMarketSignalItem = {
  marketId?: number | null;
  title?: string | null;
  initialYesProbability?: string | number | null;
  probabilitySource?: string | null;
  observedAt?: string | null;
  marketCreatedAt?: string | null;
};

export type RuntimeNewMarketSignalsPayload = {
  items: RuntimeNewMarketSignalItem[];
  generatedAt?: string;
  status?: string | null;
};

export type RuntimeDefiTokenRow = {
  id?: string | null;
  symbol?: string | null;
  name?: string | null;
  price?: number | string | null;
  change24h?: number | string | null;
  change7d?: number | string | null;
  marketCap?: number | string | null;
  volume24h?: number | string | null;
  sparkline?: number[];
  tags?: string[];
  tone?: string | null;
};

export type RuntimeDefiTokenWatchPayload = {
  generatedAt?: string;
  status?: string | null;
  cacheMode?: string | null;
  sources?: Record<string, string>;
  summary?: {
    count?: number | string | null;
    topSymbol?: string | null;
    moveCount?: number | string | null;
  } | null;
  items?: RuntimeDefiTokenRow[];
};

export type RuntimeFinanceWatchItem = {
  id?: string | null;
  label?: string | null;
  symbol?: string | null;
  title?: string | null;
  summary?: string | null;
  source?: string | null;
  url?: string | null;
  publishedAt?: string | null;
  metric?: number | string | null;
  metricLabel?: string | null;
  metricUnit?: string | null;
  secondary?: number | string | null;
  secondaryLabel?: string | null;
  change?: number | string | null;
  changeLabel?: string | null;
  tags?: string[];
  tone?: string | null;
  points?: Array<{ timestamp?: string | null; value?: number | string | null }>;
  company?: string | null;
  institution?: string | null;
  analyst?: string | null;
  rating?: string | null;
  targetPriceLabel?: string | null;
  previousTargetPriceLabel?: string | null;
  reportPageLabel?: string | null;
};

export type RuntimeFinanceWatchPayload = {
  generatedAt?: string;
  status?: string | null;
  cacheMode?: string | null;
  panelId?: string | null;
  title?: string | null;
  headline?: {
    label?: string | null;
    score?: number | string | null;
    previousScore?: number | string | null;
    delta?: number | string | null;
    regime?: string | null;
    tone?: string | null;
  } | null;
  sources?: Record<string, string>;
  summary?: Record<string, unknown>;
  items?: RuntimeFinanceWatchItem[];
};

export type RuntimeTechPanelItem = RuntimeFinanceWatchItem & {
  category?: string | null;
  rank?: number | string | null;
  marketCap?: number | string | null;
  price?: number | string | null;
};

export type RuntimeTechPanelPayload = {
  generatedAt?: string;
  status?: string | null;
  cacheMode?: string | null;
  panelId?: string | null;
  title?: string | null;
  sources?: Record<string, string>;
  summary?: Record<string, unknown>;
  items?: RuntimeTechPanelItem[];
};

export type RuntimeFinanceCoverageKey = 'quote' | 'earn' | 'sec' | 'perp' | 'etf' | 'clob' | 'oracle' | 'flow' | string;

export type RuntimeFinanceLinkedMarket = {
  marketId?: number | string | null;
  title?: string | null;
  probability?: number | string | null;
  change24h?: number | string | null;
  volume24h?: number | string | null;
  liquidity?: number | string | null;
  spread?: number | string | null;
  endDate?: string | null;
  category?: string | null;
  categoryLabel?: string | null;
  coverage?: RuntimeFinanceCoverageKey[];
  topReason?: string | null;
  gapScore?: number | string | null;
};

export type RuntimeFinanceMarketAtlasCategory = {
  id?: string | null;
  label?: string | null;
  activeCount?: number | string | null;
  volume24h?: number | string | null;
  topTitle?: string | null;
  coverage?: RuntimeFinanceCoverageKey[];
};

export type RuntimeFinanceMarketAtlasPayload = {
  generatedAt?: string;
  status?: string | null;
  cacheMode?: string | null;
  sources?: Record<string, string>;
  summary?: {
    activeCount?: number | string | null;
    categoryCount?: number | string | null;
    topCategory?: string | null;
    topDislocation?: RuntimeFinanceLinkedMarket | null;
    coverageCount?: number | string | null;
  } | null;
  categories?: RuntimeFinanceMarketAtlasCategory[];
  items?: RuntimeFinanceLinkedMarket[];
};

export type RuntimeEquityEventRow = {
  symbol?: string | null;
  company?: string | null;
  price?: number | string | null;
  change1d?: number | string | null;
  volume24h?: number | string | null;
  nextEvent?: string | null;
  nextEventAt?: string | null;
  eventType?: string | null;
  eventTone?: string | null;
  badges?: string[];
  linkedMarkets?: RuntimeFinanceLinkedMarket[];
  pmktGapScore?: number | string | null;
};

export type RuntimeEquityEventCommandPayload = {
  generatedAt?: string;
  status?: string | null;
  sources?: Record<string, string>;
  summary?: {
    trackedCount?: number | string | null;
    catalystCount?: number | string | null;
    topSymbol?: string | null;
    signal?: string | null;
  } | null;
  items?: RuntimeEquityEventRow[];
};

export type RuntimeOnchainTradfiPerpRow = {
  symbol?: string | null;
  display?: string | null;
  assetClass?: string | null;
  markPx?: number | string | null;
  oraclePx?: number | string | null;
  spotPx?: number | string | null;
  basisBps?: number | string | null;
  funding?: number | string | null;
  openInterest?: number | string | null;
  dayNotional?: number | string | null;
  compositeScore?: number | string | null;
  alerts?: string[];
  linkedMarkets?: RuntimeFinanceLinkedMarket[];
};

export type RuntimeOnchainTradfiPerpRadarPayload = {
  generatedAt?: string;
  status?: string | null;
  sources?: Record<string, string>;
  summary?: {
    assetCount?: number | string | null;
    alertCount?: number | string | null;
    topSymbol?: string | null;
    signal?: string | null;
  } | null;
  items?: RuntimeOnchainTradfiPerpRow[];
};

export type RuntimeFinanceLiquidityComponent = {
  key?: string | null;
  label?: string | null;
  value?: number | string | null;
  tone?: string | null;
  detail?: string | null;
};

export type RuntimeFinanceLiquidityRow = {
  id?: string | null;
  label?: string | null;
  source?: string | null;
  signal?: string | null;
  value?: number | string | null;
  tone?: string | null;
  linkedMarket?: RuntimeFinanceLinkedMarket | null;
};

export type RuntimeFinanceLiquidityRegimePayload = {
  generatedAt?: string;
  status?: string | null;
  sources?: Record<string, string>;
  summary?: {
    regimeLabel?: string | null;
    regimeScore?: number | string | null;
    alertCount?: number | string | null;
    signal?: string | null;
  } | null;
  components?: RuntimeFinanceLiquidityComponent[];
  items?: RuntimeFinanceLiquidityRow[];
};

export type PanelDefinition = {
  id: string;
  title: string;
  eyebrow: string;
  description: string;
  size?: 'default' | 'wide' | 'tall';
};

export type PanelRenderContext = {
  bootstrap: BootstrapPayload | null;
  markets: MarketListItem[];
  marketGroups: MarketGroupItem[];
  marketGroupSort: MarketGroupSort;
  setMarketGroupSort: (sort: MarketGroupSort) => void;
  marketCatalogRefreshing: boolean;
  marketCatalogError: string | null;
  refreshMarketCatalog: () => Promise<void>;
  selectedMarketId: number | null;
  setSelectedMarketId: (marketId: number | null) => void;
  prefetchMarketFocus: (marketIds: number[]) => void;
  focusMarketGroup: (group: MarketGroupItem, outcomeKey?: string | null, marketId?: number | null) => void;
  selectedMarketGroupId: string | null;
  selectedMarketGroup: MarketGroupItem | null;
  selectedMarketGroupOutcomeKey: string | null;
  setSelectedMarketGroupOutcomeKey: (outcomeKey: string | null) => void;
  selectedMarketGroupDetail: MarketGroupDetail | null;
  selectedMarketGroupChart: MarketGroupChartPayload | null;
  selectedMarketGroupChartRange: MarketGroupChartRange;
  setSelectedMarketGroupChartRange: (range: MarketGroupChartRange) => void;
  selectedMarket: MarketSummary | null;
  selectedWeatherCityId: string | null;
  setSelectedWeatherCityId: (cityId: string | null) => void;
  bundle: WorkspaceBundle | null;
  health: SystemHealth | null;
  globalTrades: TradeRow[];
  globalOracle: OracleEvent[];
  latestContent: ContentItem[];
  runtimeData: Record<string, unknown>;
  commodities?: RuntimeMarketGroup | null;
  crypto?: RuntimeMarketGroup | null;
  f1?: RuntimeF1Payload | null;
  jin10?: RuntimeJin10Payload | null;
  nba?: RuntimeNbaPayload | null;
  nbaIntel?: RuntimeNbaIntelPayload | null;
  nbaMatchupPredictor?: RuntimeNbaMatchupPredictorPayload | null;
  inflationNowcast?: RuntimeInflationNowcastPayload | null;
  alphaSignals?: RuntimeSignalPayload | null;
  whaleTrades?: RuntimeSignalPayload | null;
  suspiciousTrades?: RuntimeSignalPayload | null;
};
