import type {
  BootstrapPayload,
  ChartPayload,
  ContentPayload,
  LobPayload,
  LobSnapshotPayload,
  MarketAiInsightPayload,
  MarketAiInsightResponse,
  MarketWideAiInsightLens,
  MarketWideAiInsightResponse,
  MarketSummary,
  MarketGroupChartPayload,
  MarketGroupDetail,
  MarketGroupOutcome,
  MarketGroupsPayload,
  MarketGroupSort,
  MarketListItem,
  MarketDataQualityPayload,
  MarketsPayload,
  MarketWorkspaceHealth,
  MarketWorkspaceEvidence,
  OraclePayload,
  PriceSummary,
  QuantBacktestBenchmarkArtifact,
  QuantBacktestBenchmarkCreatePayload,
  QuantBacktestBenchmarkQueue,
  QuantBacktestBenchmarkRow,
  QuantBacktestBenchmarkRun,
  QuantBacktestUniverse,
  QuantBacktestCreatePayload,
  QuantBacktestEquityPoint,
  QuantBacktestLedgerRow,
  QuantBacktestMetric,
  QuantBacktestOrder,
  QuantBacktestRun,
  QuantBacktestTrade,
  QuantBlockClosePoint,
  QuantBuildRun,
  QuantFrontendPricePoint,
  QuantListPayload,
  QuantMarketSeriesPayload,
  QuantPriceMarket,
  RuntimeMarketGroup,
  RuntimeBreakingEventRadarPayload,
  RuntimeCryptoFundingPayload,
  RuntimeCommodityTransmissionPayload,
  RuntimeCpiReleaseCalendarPayload,
  RuntimeDefiTokenWatchPayload,
  RuntimeEnergyGasolineShockPayload,
  RuntimeEquityEventCommandPayload,
  RuntimeFinanceLiquidityRegimePayload,
  RuntimeFinanceMarketAtlasPayload,
  RuntimeFinanceWatchPayload,
  RuntimeGlobalWeatherMapPayload,
  RuntimeGlobalTransportShippingPayload,
  RuntimeGridEsportsPayload,
  RuntimeFoodRetailBasketPayload,
  RuntimeGeoSanctionsShockPayload,
  RuntimeInflationNowcastPayload,
  RuntimeF1Payload,
  RuntimeJin10Payload,
  RuntimeMacroDriverPayload,
  RuntimeMacroRegistryPayload,
  RuntimeMarketYoutubeChannelsPayload,
  RuntimeMarketTvWirePayload,
  RuntimeCpiReleaseCommandPayload,
  RuntimeNbaMatchupPredictorPayload,
  RuntimeNbaPayload,
  RuntimeNbaIntelPayload,
  RuntimeNewMarketSignalsPayload,
  RuntimeOnchainTradfiPerpRadarPayload,
  RuntimePolybeatsPayload,
  RuntimePolymarketMacroMapPayload,
  RuntimeSignalPayload,
  RuntimeSportsOddsPayload,
  RuntimeTechPanelPayload,
  RuntimeWeatherNewsPayload,
  RuntimeWorldCupMatchOpsPayload,
  SeedHealthPayload,
  SystemHealth,
  TradeRow,
  WorkspaceBundle,
  WorkspaceDiagnostics,
  WorkspaceIdentity,
} from '@/types';
import type { WorldCupDashboardPayload, WorldCupIntelPayload } from '@/workspaces/worldcup/types';
import type {
  HazardDetailResponse,
  HazardMapResponse,
  HazardMarketLinksResponse,
} from '@/features/world-event-map/domain/types';

const RAW_BASE = import.meta.env.VITE_POLYDATA_API_BASE_URL || '/wm-api';
const API_BASE = RAW_BASE.endsWith('/') ? RAW_BASE.slice(0, -1) : RAW_BASE;
const AGENT_RESPONSE_TTL_MS = 5 * 60 * 1000;

type AgentCacheEntry<T> = {
  expiresAt: number;
  data: T;
};

const agentResponseCache = new Map<string, AgentCacheEntry<unknown>>();
const agentInflight = new Map<string, Promise<unknown>>();

export class ApiTimeoutError extends Error {
  constructor(path: string, timeoutMs: number) {
    super(`API timeout after ${(timeoutMs / 1000).toFixed(1)}s for ${path}`);
    this.name = 'ApiTimeoutError';
  }
}

export function isAbortLikeError(error: unknown) {
  if (!error || typeof error !== 'object') return false;
  const maybe = error as { name?: string; message?: string };
  return maybe.name === 'AbortError'
    || maybe.name === 'ApiTimeoutError'
    || String(maybe.message || '').toLowerCase().includes('signal is aborted');
}

async function apiGetWithTimeout<T>(path: string, timeoutMs = 12000, externalSignal?: AbortSignal): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  const abortFromExternal = () => controller.abort();
  externalSignal?.addEventListener('abort', abortFromExternal, { once: true });
  let response: Response;
  try {
    if (externalSignal?.aborted) controller.abort();
    response = await fetch(`${API_BASE}${path}`, {
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
  } catch (error) {
    if (externalSignal?.aborted) throw error;
    if (isAbortLikeError(error)) throw new ApiTimeoutError(path, timeoutMs);
    throw error;
  } finally {
    window.clearTimeout(timer);
    externalSignal?.removeEventListener('abort', abortFromExternal);
  }
  if (!response.ok) {
    throw new Error(`API ${response.status} for ${path}`);
  }
  return response.json() as Promise<T>;
}

async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  return apiGetWithTimeout<T>(path, 12000, signal);
}

async function apiPostWithTimeout<T>(path: string, body: unknown, timeoutMs = 18000): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (error) {
    if (isAbortLikeError(error)) throw new ApiTimeoutError(path, timeoutMs);
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
  if (!response.ok) {
    throw new Error(`API ${response.status} for ${path}`);
  }
  return response.json() as Promise<T>;
}

function agentRequestKey(path: string, body: unknown) {
  return `${path}:${JSON.stringify(body)}`;
}

async function apiPostAgentOnce<T>(path: string, body: unknown, timeoutMs = 18000): Promise<T> {
  const key = agentRequestKey(path, body);
  const now = Date.now();
  const cached = agentResponseCache.get(key);
  if (cached && cached.expiresAt > now) {
    return cached.data as T;
  }
  const inflight = agentInflight.get(key);
  if (inflight) {
    return inflight as Promise<T>;
  }

  const request = apiPostWithTimeout<T>(path, body, timeoutMs)
    .then((data) => {
      agentResponseCache.set(key, { data, expiresAt: Date.now() + AGENT_RESPONSE_TTL_MS });
      return data;
    })
    .finally(() => {
      agentInflight.delete(key);
    });
  agentInflight.set(key, request);
  return request;
}

export function fetchBootstrap() {
  return apiGet<BootstrapPayload>('/bootstrap');
}

export function fetchMarketSearch(query: string, limit = 12) {
  const params = new URLSearchParams({
    q: query.trim(),
    limit: String(limit),
  });
  return apiGetWithTimeout<{ items: MarketListItem[] }>(`/search?${params.toString()}`, 5000);
}

export function fetchMarkets(query = '', pageSize = 160) {
  return fetchMarketsPage(1, query, pageSize);
}

export function fetchMarketsPage(page = 1, query = '', pageSize = 160) {
  const params = new URLSearchParams({
    page: String(page),
    pageSize: String(pageSize),
    status: 'active',
  });
  if (query.trim()) params.set('q', query.trim());
  return apiGetWithTimeout<MarketsPayload>(`/markets?${params.toString()}`, 3500);
}

export async function fetchAllActiveMarkets(query = '', pageSize = 160, maxPages = 8) {
  const items: MarketsPayload['items'] = [];
  let page = 1;
  let total = 0;
  let totalPages = 1;
  let hasMore = false;

  do {
    const payload = await fetchMarketsPage(page, query, pageSize);
    items.push(...(payload.items || []));
    total = payload.pagination?.total || items.length;
    totalPages = payload.pagination?.totalPages || page;
    hasMore = Boolean(payload.pagination?.hasMore);
    page += 1;
  } while (hasMore && page <= maxPages);

  return {
    items,
    pagination: {
      page: 1,
      pageSize: items.length,
      total,
      totalPages,
      hasMore,
    },
  } satisfies MarketsPayload;
}

export function fetchMarketGroups(query = '', pageSize = 80, sort: MarketGroupSort = 'active') {
  const params = new URLSearchParams({
    page: '1',
    pageSize: String(pageSize),
    sort,
  });
  if (query.trim()) params.set('q', query.trim());
  return apiGetWithTimeout<MarketGroupsPayload>(`/market-groups?${params.toString()}`, 9000);
}

export function fetchMarketGroupDetail(eventId: string, timeoutMs = 3500, signal?: AbortSignal) {
  return apiGetWithTimeout<MarketGroupDetail>(`/market-groups/${encodeURIComponent(eventId)}/detail`, timeoutMs, signal);
}

export function fetchMarketGroupChart(
  eventId: string,
  range: '1h' | '6h' | '1d' | '1w' | '1m' | 'all' = '1d',
  timeoutMs = 4000,
  signal?: AbortSignal,
) {
  return apiGetWithTimeout<MarketGroupChartPayload>(
    `/market-groups/${encodeURIComponent(eventId)}/chart?range=${encodeURIComponent(range)}`,
    timeoutMs,
    signal,
  );
}

export function fetchSystemHealth(signal?: AbortSignal) {
  return apiGet<SystemHealth>('/system/health', signal);
}

export function fetchSeedHealth(signal?: AbortSignal) {
  return apiGet<SeedHealthPayload>('/system/seed-health', signal);
}

export function fetchMarketDataQuality(signal?: AbortSignal) {
  return apiGetWithTimeout<MarketDataQualityPayload>('/data-quality/markets', 45_000, signal);
}

export function fetchRecentTrades(limit = 24) {
  return apiGet<TradeRow[]>(`/trades/recent?limit=${limit}`);
}

export function fetchRecentOracle(limit = 24) {
  return apiGet<OraclePayload['timeline']>(`/oracle/recent?limit=${limit}`);
}

export type QuantPriceQuery = {
  entityType?: 'market' | 'event' | string;
  marketSlug?: string;
  eventSlug?: string;
  tokenSide?: string;
  tokenId?: string;
  from?: string;
  to?: string;
  fromBlock?: string;
  toBlock?: string;
  limit?: number;
  pointFormat?: 'lite' | 'full';
  topN?: number;
  maxPoints?: number;
  range?: string;
  resolution?: string;
  viewportWidth?: number;
  live?: boolean;
  timeoutMs?: number;
};

export function fetchQuantPriceMarkets(query = '', limit = 40) {
  const params = new URLSearchParams({ limit: String(limit), token_side: 'YES' });
  if (query.trim()) params.set('q', query.trim());
  return apiGetWithTimeout<QuantListPayload<QuantPriceMarket>>(`/quant/markets?${params.toString()}`, 15000);
}

export function fetchQuantPriceEvents(query = '', limit = 40) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (query.trim()) params.set('q', query.trim());
  return apiGetWithTimeout<QuantListPayload<QuantPriceMarket>>(`/quant/events?${params.toString()}`, 15000);
}

function appendQuantParams(query: QuantPriceQuery, mode: 'frontend' | 'block') {
  const params = new URLSearchParams();
  if (query.marketSlug?.trim()) params.set('market_slug', query.marketSlug.trim());
  if (query.tokenSide?.trim()) params.set('token_side', query.tokenSide.trim());
  if (query.tokenId?.trim()) params.set('token_id', query.tokenId.trim());
  if (mode === 'frontend') {
    if (query.from?.trim()) params.set('from', query.from.trim());
    if (query.to?.trim()) params.set('to', query.to.trim());
  } else {
    if (query.fromBlock?.trim()) params.set('from_block', query.fromBlock.trim());
    if (query.toBlock?.trim()) params.set('to_block', query.toBlock.trim());
  }
  params.set('limit', String(query.limit || 240));
  return params.toString();
}

export function fetchQuantFrontendPrices(query: QuantPriceQuery = {}) {
  return apiGetWithTimeout<QuantListPayload<QuantFrontendPricePoint>>(
    `/quant/frontend-prices?${appendQuantParams(query, 'frontend')}`,
    15000,
  );
}

export function fetchQuantBlockClosePrices(query: QuantPriceQuery = {}) {
  return apiGetWithTimeout<QuantListPayload<QuantBlockClosePoint>>(
    `/quant/block-close-prices?${appendQuantParams(query, 'block')}`,
    15000,
  );
}

export function fetchQuantMarketPriceSeries(
  query: QuantPriceQuery & { priceSource?: string; scope?: string; maxOutcomes?: number } = {},
  signal?: AbortSignal,
) {
  const params = new URLSearchParams();
  if (query.marketSlug?.trim()) params.set('market_slug', query.marketSlug.trim());
  if (query.tokenSide?.trim()) params.set('token_side', query.tokenSide.trim());
  if (query.tokenId?.trim()) params.set('token_id', query.tokenId.trim());
  if (query.priceSource?.trim()) params.set('price_source', query.priceSource.trim());
  if (query.scope?.trim()) params.set('scope', query.scope.trim());
  if (query.from?.trim()) params.set('from', query.from.trim());
  if (query.to?.trim()) params.set('to', query.to.trim());
  if (query.fromBlock?.trim()) params.set('from_block', query.fromBlock.trim());
  if (query.toBlock?.trim()) params.set('to_block', query.toBlock.trim());
  params.set('limit', String(query.limit || 2500));
  params.set('max_outcomes', String(query.maxOutcomes || 24));
  if (query.maxPoints) params.set('max_points', String(query.maxPoints));
  params.set('point_format', query.pointFormat || 'lite');
  if (query.live) {
    params.set('live', '1');
    params.set('_ts', String(Date.now()));
  }
  return apiGetWithTimeout<QuantMarketSeriesPayload>(
    `/quant/market-price-series?${params.toString()}`,
    query.timeoutMs || 20000,
    signal,
  );
}

export function fetchQuantEventPriceSeries(query: QuantPriceQuery & { eventSlug?: string; priceSource?: string; maxOutcomes?: number } = {}) {
  const params = new URLSearchParams();
  const eventSlug = query.eventSlug || query.marketSlug;
  if (eventSlug?.trim()) params.set('event_slug', eventSlug.trim());
  if (query.priceSource?.trim()) params.set('price_source', query.priceSource.trim());
  if (query.from?.trim()) params.set('from', query.from.trim());
  if (query.to?.trim()) params.set('to', query.to.trim());
  if (query.fromBlock?.trim()) params.set('from_block', query.fromBlock.trim());
  if (query.toBlock?.trim()) params.set('to_block', query.toBlock.trim());
  params.set('limit', String(query.limit || 2500));
  params.set('max_outcomes', String(query.maxOutcomes || 100));
  params.set('top_n', String(query.topN || 12));
  params.set('max_points', String(query.maxPoints || 600));
  params.set('range', query.range || 'latest');
  params.set('resolution', query.resolution || 'auto');
  params.set('point_format', query.pointFormat || 'lite');
  if (query.live) params.set('live', '1');
  return apiGetWithTimeout<QuantMarketSeriesPayload>(`/quant/event-price-tile?${params.toString()}`, query.timeoutMs || 12000);
}

export function fetchQuantEventPriceHead(query: QuantPriceQuery & { eventSlug?: string; priceSource?: string; maxOutcomes?: number } = {}) {
  const params = new URLSearchParams();
  const eventSlug = query.eventSlug || query.marketSlug;
  if (eventSlug?.trim()) params.set('event_slug', eventSlug.trim());
  if (query.priceSource?.trim()) params.set('price_source', query.priceSource.trim());
  if (query.from?.trim()) params.set('from', query.from.trim());
  if (query.to?.trim()) params.set('to', query.to.trim());
  if (query.fromBlock?.trim()) params.set('from_block', query.fromBlock.trim());
  if (query.toBlock?.trim()) params.set('to_block', query.toBlock.trim());
  params.set('max_outcomes', String(query.maxOutcomes || 100));
  params.set('top_n', String(query.topN || 12));
  params.set('point_format', query.pointFormat || 'lite');
  return apiGetWithTimeout<QuantMarketSeriesPayload>(`/quant/event-price-head?${params.toString()}`, query.timeoutMs || 8000);
}

export function fetchQuantEntitySnapshot(query: QuantPriceQuery & { priceSource?: string; maxOutcomes?: number } = {}) {
  const params = new URLSearchParams();
  if (query.entityType?.trim()) params.set('entity_type', query.entityType.trim());
  if (query.marketSlug?.trim()) params.set('market_slug', query.marketSlug.trim());
  if (query.eventSlug?.trim()) params.set('event_slug', query.eventSlug.trim());
  if (query.priceSource?.trim()) params.set('price_source', query.priceSource.trim());
  params.set('max_outcomes', String(query.maxOutcomes || 100));
  params.set('top_n', String(query.topN || 12));
  params.set('point_format', query.pointFormat || 'lite');
  if (query.live) params.set('live', '1');
  return apiGetWithTimeout<QuantMarketSeriesPayload>(`/quant/entity-snapshot?${params.toString()}`, query.timeoutMs || 3500);
}

export function fetchQuantPriceWindow(query: QuantPriceQuery & { priceSource?: string; maxOutcomes?: number } = {}) {
  const params = new URLSearchParams();
  if (query.entityType?.trim()) params.set('entity_type', query.entityType.trim());
  if (query.marketSlug?.trim()) params.set('market_slug', query.marketSlug.trim());
  if (query.eventSlug?.trim()) params.set('event_slug', query.eventSlug.trim());
  if (query.tokenSide?.trim()) params.set('token_side', query.tokenSide.trim());
  if (query.tokenId?.trim()) params.set('token_id', query.tokenId.trim());
  if (query.priceSource?.trim()) params.set('price_source', query.priceSource.trim());
  if (query.from?.trim()) params.set('from', query.from.trim());
  if (query.to?.trim()) params.set('to', query.to.trim());
  if (query.fromBlock?.trim()) params.set('from_block', query.fromBlock.trim());
  if (query.toBlock?.trim()) params.set('to_block', query.toBlock.trim());
  params.set('limit', String(query.limit || 25000));
  params.set('max_outcomes', String(query.maxOutcomes || 100));
  params.set('top_n', String(query.topN || 12));
  params.set('max_points', String(query.maxPoints || 900));
  params.set('range', query.range || 'window');
  params.set('resolution', query.resolution || 'auto');
  params.set('point_format', query.pointFormat || 'lite');
  if (query.viewportWidth) params.set('viewport_width', String(Math.max(320, Math.floor(query.viewportWidth))));
  if (query.live) params.set('live', '1');
  return apiGetWithTimeout<QuantMarketSeriesPayload>(`/quant/price-window?${params.toString()}`, query.timeoutMs || 12000);
}

export function fetchQuantEventMembers(eventSlug: string, limit = 200) {
  const params = new URLSearchParams({ event_slug: eventSlug.trim(), limit: String(limit) });
  return apiGetWithTimeout<{ eventSlug: string; items: Array<Record<string, unknown>>; count: number }>(
    `/quant/event-members?${params.toString()}`,
    5000,
  );
}

export function quantEventPriceStreamUrl(query: QuantPriceQuery & { eventSlug?: string; priceSource?: string; maxOutcomes?: number; interval?: number } = {}) {
  const params = new URLSearchParams();
  const eventSlug = query.eventSlug || query.marketSlug;
  if (eventSlug?.trim()) params.set('event_slug', eventSlug.trim());
  if (query.priceSource?.trim()) params.set('price_source', query.priceSource.trim());
  params.set('max_outcomes', String(query.maxOutcomes || 24));
  params.set('interval', String(query.interval || 5));
  return `${API_BASE}/quant/event-price-stream?${params.toString()}`;
}

export function quantPriceStreamUrl(query: QuantPriceQuery & { priceSource?: string; maxOutcomes?: number; interval?: number } = {}) {
  const params = new URLSearchParams();
  if (query.entityType?.trim()) params.set('entity_type', query.entityType.trim());
  if (query.eventSlug?.trim()) params.set('event_slug', query.eventSlug.trim());
  if (query.marketSlug?.trim()) params.set('market_slug', query.marketSlug.trim());
  if (query.priceSource?.trim()) params.set('price_source', query.priceSource.trim());
  params.set('max_outcomes', String(query.maxOutcomes || 24));
  params.set('interval', String(query.interval || 2));
  return `${API_BASE}/quant/price-stream?${params.toString()}`;
}

export function fetchQuantBuildStatus(source = '', limit = 24) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (source.trim()) params.set('source', source.trim());
  return apiGetWithTimeout<QuantListPayload<QuantBuildRun>>(`/quant/price-build-status?${params.toString()}`, 8000);
}

export function createQuantBacktestRun(payload: QuantBacktestCreatePayload) {
  return apiPostWithTimeout<{ item: QuantBacktestRun; runId: number; status: string }>('/quant/backtest-runs', payload, 30000);
}

export function fetchQuantBacktestRuns(marketSlug = '', limit = 25) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (marketSlug.trim()) params.set('market_slug', marketSlug.trim());
  return apiGetWithTimeout<QuantListPayload<QuantBacktestRun>>(`/quant/backtest-runs?${params.toString()}`, 15000);
}

export function fetchQuantBacktestRun(runId: number) {
  return apiGetWithTimeout<{ item: QuantBacktestRun }>(`/quant/backtest-runs/${runId}`, 15000);
}

export function fetchQuantBacktestTrades(runId: number, limit = 10000) {
  return apiGetWithTimeout<QuantListPayload<QuantBacktestTrade>>(`/quant/backtest-runs/${runId}/trades?limit=${limit}`, 20000);
}

export function fetchQuantBacktestOrders(runId: number, limit = 10000) {
  return apiGetWithTimeout<QuantListPayload<QuantBacktestOrder>>(`/quant/backtest-runs/${runId}/orders?limit=${limit}`, 20000);
}

export function fetchQuantBacktestLedger(runId: number, limit = 10000) {
  return apiGetWithTimeout<QuantListPayload<QuantBacktestLedgerRow>>(`/quant/backtest-runs/${runId}/ledger?limit=${limit}`, 20000);
}

export function fetchQuantBacktestEquity(runId: number, limit = 25000) {
  return apiGetWithTimeout<QuantListPayload<QuantBacktestEquityPoint>>(`/quant/backtest-runs/${runId}/equity?limit=${limit}`, 20000);
}

export function fetchQuantBacktestMetrics(runId: number) {
  return apiGetWithTimeout<QuantListPayload<QuantBacktestMetric>>(`/quant/backtest-runs/${runId}/metrics`, 15000);
}

export function createQuantBacktestBenchmark(payload: QuantBacktestBenchmarkCreatePayload) {
  return apiPostWithTimeout<{
    item: QuantBacktestBenchmarkRun;
    benchmarkId: number;
    status: string;
    artifacts?: QuantBacktestBenchmarkArtifact[];
  }>('/quant/backtest-benchmarks', payload, 20000);
}

export function fetchQuantBacktestBenchmarks(limit = 25) {
  return apiGetWithTimeout<QuantListPayload<QuantBacktestBenchmarkRun>>(`/quant/backtest-benchmarks?limit=${limit}`, 20000);
}

export function fetchQuantBacktestBenchmarkQueue() {
  return apiGetWithTimeout<QuantBacktestBenchmarkQueue>('/quant/backtest-benchmark-queue', 15000);
}

export function fetchQuantBacktestBenchmark(benchmarkId: number, includeArtifacts = true) {
  const query = includeArtifacts ? '' : '?includeArtifacts=0';
  return apiGetWithTimeout<{ item: QuantBacktestBenchmarkRun; artifacts: QuantBacktestBenchmarkArtifact[] }>(
    `/quant/backtest-benchmarks/${benchmarkId}${query}`,
    20000,
  );
}

export function fetchQuantBacktestBenchmarkRows(benchmarkId: number, limit = 10000) {
  return apiGetWithTimeout<QuantListPayload<QuantBacktestBenchmarkRow>>(
    `/quant/backtest-benchmarks/${benchmarkId}/rows?limit=${limit}`,
    30000,
  );
}

export function fetchQuantBacktestUniverses() {
  return apiGetWithTimeout<QuantListPayload<QuantBacktestUniverse>>('/quant/backtest-universes', 15000);
}

export function buildQuantBacktestReplayCoverage(payload: {
  universe?: string;
  limit?: number;
  windowStartHours?: number;
  windowEndHours?: number;
  force?: boolean;
}) {
  return apiPostWithTimeout<{ item: Record<string, unknown>; status: string }>(
    '/quant/backtest-replay-coverage/build',
    payload,
    180000,
  );
}

export function fetchLatestContent(limit = 8) {
  return apiGet<ContentPayload>(`/content/latest?limit=${limit}`);
}

export function fetchRuntimeCommodities() {
  return apiGet<RuntimeMarketGroup>('/runtime/markets/commodities');
}

export function fetchRuntimeCrypto() {
  return apiGet<RuntimeMarketGroup>('/runtime/markets/crypto');
}

export function fetchRuntimeCryptoFundingWatch(limit = 18) {
  return apiGet<RuntimeCryptoFundingPayload>(`/runtime/crypto/funding-watch?limit=${limit}`);
}

export function fetchRuntimeDefiTokenWatch(limit = 10) {
  return apiGet<RuntimeDefiTokenWatchPayload>(`/runtime/finance/defi-token-watch?limit=${limit}`);
}

export function fetchRuntimeFinanceWatchPanel(panelId: string, limit = 10) {
  return apiGet<RuntimeFinanceWatchPayload>(`/runtime/finance/${panelId}?limit=${limit}`);
}

export function fetchRuntimeTechPanel(panelId: string, limit = 10) {
  return apiGet<RuntimeTechPanelPayload>(`/runtime/tech/${panelId}?limit=${limit}`);
}

export function fetchRuntimeFinanceMarketAtlas(limit = 16) {
  return apiGet<RuntimeFinanceMarketAtlasPayload>(`/runtime/finance/market-atlas?limit=${limit}`);
}

export function fetchRuntimeEquityEventCommand(limit = 12) {
  return apiGet<RuntimeEquityEventCommandPayload>(`/runtime/finance/equity-event-command?limit=${limit}`);
}

export function fetchRuntimeOnchainTradfiPerpRadar(limit = 12) {
  return apiGet<RuntimeOnchainTradfiPerpRadarPayload>(`/runtime/finance/onchain-tradfi-perp-radar?limit=${limit}`);
}

export function fetchRuntimeFinanceLiquidityRegime(limit = 12) {
  return apiGet<RuntimeFinanceLiquidityRegimePayload>(`/runtime/finance/liquidity-regime?limit=${limit}`);
}

export function fetchRuntimeCommodityEquityTransmission(limit = 8) {
  return apiGet<RuntimeCommodityTransmissionPayload>(`/runtime/finance/commodity-equity-transmission?limit=${limit}`);
}

export function fetchRuntimeF1(limit = 10) {
  return apiGet<RuntimeF1Payload>(`/runtime/sports/f1?limit=${limit}`);
}

export function fetchRuntimeJin10(limit = 24) {
  return apiGet<RuntimeJin10Payload>(`/runtime/macro/jin10?limit=${limit}`);
}

export function fetchRuntimeNba(limit = 10) {
  return apiGet<RuntimeNbaPayload>(`/runtime/sports/nba?limit=${limit}`);
}

export function fetchRuntimeNbaIntel(limit = 12) {
  return apiGet<RuntimeNbaIntelPayload>(`/runtime/sports/nba-intel?limit=${limit}`);
}

export function fetchRuntimeNbaMatchupPredictor(limit = 8) {
  return apiGet<RuntimeNbaMatchupPredictorPayload>(`/runtime/sports/nba-matchup-predictor?limit=${limit}`);
}

export function fetchRuntimeGridEsports(limit = 10) {
  return apiGet<RuntimeGridEsportsPayload>(`/runtime/esports/grid-intel?limit=${limit}`);
}

export function fetchRuntimeSportsOdds(limit = 8) {
  return apiGet<RuntimeSportsOddsPayload>(`/runtime/sports/odds-monitor?limit=${limit}`);
}

export function fetchRuntimeWorldCupIntel(limit = 96, timeoutMs = 12000) {
  return apiGetWithTimeout<WorldCupIntelPayload>(`/runtime/sports/worldcup-intel?limit=${limit}`, timeoutMs);
}

export function fetchRuntimeWorldCupDashboard(timeoutMs = 12000) {
  return apiGetWithTimeout<WorldCupDashboardPayload>('/runtime/worldcup/dashboard', timeoutMs);
}

export function fetchRuntimeInflationNowcast() {
  return apiGet<RuntimeInflationNowcastPayload>('/runtime/macro/inflation-nowcast');
}

export function fetchRuntimePolymarketMacroMap(limit = 12) {
  return apiGet<RuntimePolymarketMacroMapPayload>(`/runtime/macro/polymarket-map?limit=${limit}`);
}

export function fetchRuntimeCpiReleaseCalendar(limit = 8) {
  return apiGet<RuntimeCpiReleaseCalendarPayload>(`/runtime/macro/cpi-release-calendar?limit=${limit}`);
}

export function fetchRuntimeEnergyGasolineShock(limit = 6) {
  return apiGet<RuntimeEnergyGasolineShockPayload>(`/runtime/macro/energy-gasoline-shock?limit=${limit}`);
}

export function fetchRuntimeGlobalWeatherMap(limit = 60) {
  return apiGet<RuntimeGlobalWeatherMapPayload>(`/runtime/weather/global-map?limit=${limit}`);
}

export function fetchRuntimeGlobalTemperatureMonitor(limit = 60) {
  return apiGet<RuntimeGlobalWeatherMapPayload>(`/runtime/weather/temperature-monitor?limit=${limit}`);
}

export function fetchRuntimeWeatherNews(limit = 24) {
  return apiGet<RuntimeWeatherNewsPayload>(`/runtime/weather/news?limit=${limit}`);
}

export function fetchRuntimeGlobalTransportShipping(limit = 14) {
  return apiGet<RuntimeGlobalTransportShippingPayload>(`/runtime/transport/global-shipping?limit=${limit}`);
}

export function fetchAviationViewport(
  bbox: [number, number, number, number],
  zoom: number,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({ bbox: bbox.join(','), zoom: String(zoom), limit: '180' });
  return apiGetWithTimeout<import('@/types').AviationViewportPayload>(
    `/runtime/transport/aviation-viewport?${params.toString()}`,
    10_000,
    signal,
  );
}

export function fetchRuntimeBreakingEventRadar(limit = 12) {
  return apiGet<RuntimeBreakingEventRadarPayload>(`/runtime/evidence/breaking-event-radar?limit=${limit}`);
}

export function fetchRuntimeWorldCupMatchOps(limit = 12) {
  return apiGet<RuntimeWorldCupMatchOpsPayload>(`/runtime/sports/world-cup-match-ops?limit=${limit}`);
}

export function fetchRuntimeFoodRetailBasket(limit = 8) {
  return apiGet<RuntimeFoodRetailBasketPayload>(`/runtime/macro/food-retail-basket?limit=${limit}`);
}

export function fetchRuntimeSupplyTariffImportWatch(limit = 8) {
  return apiGet<RuntimeMacroDriverPayload>(`/runtime/macro/supply-tariff-import-watch?limit=${limit}`);
}

export function fetchRuntimeShelterRentOerPressure(limit = 8) {
  return apiGet<RuntimeMacroDriverPayload>(`/runtime/macro/shelter-rent-oer-pressure?limit=${limit}`);
}

export function fetchRuntimeLaborWageServicesPressure(limit = 8) {
  return apiGet<RuntimeMacroDriverPayload>(`/runtime/macro/labor-wage-services-pressure?limit=${limit}`);
}

export function fetchRuntimeGrowthDemandRecessionTracker(limit = 8) {
  return apiGet<RuntimeMacroDriverPayload>(`/runtime/macro/growth-demand-recession-tracker?limit=${limit}`);
}

export function fetchRuntimeFedRatesPolymarketGap(limit = 8) {
  return apiGet<RuntimeMacroDriverPayload>(`/runtime/macro/fed-rates-polymarket-gap?limit=${limit}`);
}

export function fetchRuntimeCpiReleaseCommandCenter(limit = 36) {
  return apiGet<RuntimeCpiReleaseCommandPayload>(`/runtime/macro/cpi-release-command-center?limit=${limit}`);
}

export function fetchRuntimeCpiComponentsPressureRegistry(limit = 48) {
  return apiGet<RuntimeMacroRegistryPayload>(`/runtime/macro/cpi-components-pressure-registry?limit=${limit}`);
}

export function fetchRuntimeGoodsTariffSupplyWatch(limit = 36) {
  return apiGet<RuntimeMacroRegistryPayload>(`/runtime/macro/goods-tariff-supply-watch?limit=${limit}`);
}

export function fetchRuntimeLaborServicesInflationMonitor(limit = 36) {
  return apiGet<RuntimeMacroRegistryPayload>(`/runtime/macro/labor-services-inflation-monitor?limit=${limit}`);
}

export function fetchRuntimeFedReactionGrowthRiskBoard(limit = 36) {
  return apiGet<RuntimeMacroRegistryPayload>(`/runtime/macro/fed-reaction-growth-risk-board?limit=${limit}`);
}

export function fetchRuntimeGeoSanctionsShock(limit = 2000) {
  return apiGet<RuntimeGeoSanctionsShockPayload>(`/runtime/world/geo-sanctions-shock?limit=${limit}`);
}

export function fetchNaturalHazardMapSource(
  source: string,
  zoom: number,
  bbox?: [number, number, number, number],
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({ source, limit: '1200', zoom: String(zoom) });
  if (bbox) params.set('bbox', bbox.join(','));
  return apiGetWithTimeout<HazardMapResponse>(
    `/runtime/world/natural-hazards/map?${params.toString()}`,
    10_000,
    signal,
  );
}

/** Kept for diagnostics and backward compatibility; the map no longer uses this heavy aggregate. */
export function fetchNaturalHazards(signal?: AbortSignal) {
  return apiGetWithTimeout<HazardMapResponse>('/runtime/world/natural-hazards?limit=1200', 25_000, signal);
}

export function fetchNaturalHazardDetail(eventId: string, signal?: AbortSignal) {
  return apiGetWithTimeout<HazardDetailResponse>(
    `/runtime/world/natural-hazards/events/${encodeURIComponent(eventId)}`,
    10_000,
    signal,
  );
}

export function fetchNaturalHazardRelatedMarkets(eventId: string, signal?: AbortSignal) {
  const params = new URLSearchParams({ eventId, limit: '8' });
  return apiGetWithTimeout<HazardMarketLinksResponse>(
    `/runtime/world/natural-hazards/related-markets?${params.toString()}`,
    6000,
    signal,
  );
}

export function fetchRuntimeAlpha(limit = 8) {
  return apiGet<RuntimeSignalPayload>(`/runtime/signals/alpha?limit=${limit}`);
}

export function fetchRuntimePolybeats(limit = 8) {
  return apiGet<RuntimePolybeatsPayload>(`/runtime/panels/polybeats-feed?limit=${limit}`);
}

export function fetchRuntimeMarketTvWire(limit = 24, category?: string | null) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (category && category !== 'all') params.set('category', category);
  return apiGet<RuntimeMarketTvWirePayload>(`/runtime/content/market-tv-wire?${params.toString()}`);
}

export function buildRuntimeHlsProxyUrl(hlsUrl: string) {
  const params = new URLSearchParams({ url: hlsUrl });
  return `${API_BASE}/runtime/content/hls-proxy?${params.toString()}`;
}

export function buildRuntimeYoutubeEmbedUrl(videoId: string, options?: { autoplay?: boolean; mute?: boolean; quality?: string }) {
  const params = new URLSearchParams({
    videoId,
    autoplay: options?.autoplay === false ? '0' : '1',
    mute: options?.mute === false ? '0' : '1',
    parentOrigin: window.location.origin,
  });
  if (options?.quality) params.set('vq', options.quality);
  return `${API_BASE}/runtime/content/youtube-embed?${params.toString()}`;
}

export function fetchRuntimeMarketYoutubeChannels(limit = 12, category?: string | null) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (category && category !== 'all') params.set('category', category);
  return apiGet<RuntimeMarketYoutubeChannelsPayload>(`/runtime/content/market-youtube-channels?${params.toString()}`);
}

export function fetchRuntimeNewMarketSignals(limit = 12) {
  return apiGet<RuntimeNewMarketSignalsPayload>(`/runtime/markets/new-signals?limit=${limit}`);
}

export function fetchRuntimeWhales(limit = 14) {
  return apiGet<RuntimeSignalPayload>(`/runtime/trades/whales?limit=${limit}`);
}

export function fetchRuntimeSuspicious(limit = 12) {
  return apiGet<RuntimeSignalPayload>(`/runtime/trades/suspicious?limit=${limit}`);
}

export type RuntimePanelsPayload = {
  generatedAt?: string;
  status?: string;
  panels?: Record<string, unknown>;
  errors?: Record<string, string>;
  requestId?: string;
  metadata?: Record<string, RuntimePanelMetadata>;
};

export type ApiEnvelopeError = {
  code: string;
  message: string;
  panelId?: string;
  retryable: boolean;
};

export type ApiEnvelope<T> = {
  apiVersion: 'v1';
  requestId: string;
  generatedAt: string;
  status: 'ok' | 'partial' | 'error';
  data: T;
  meta: Record<string, unknown>;
  errors: ApiEnvelopeError[];
};

export type RuntimePanelMetadata = {
  panelId: string;
  route: string;
  status: string;
  cache: {
    mode: string;
    ageSeconds: number | null;
  };
  freshness: {
    state: string;
    observedAt: string | null;
    ageSeconds: number | null;
  };
  limits: {
    default: number | null;
    minimum: number | null;
    maximum: number | null;
  };
};

type RuntimePanelsEnvelopeData = {
  panels: Record<string, unknown>;
};

type RuntimePanelsEnvelopeMeta = {
  requestedPanelIds?: string[];
  returnedPanelIds?: string[];
  panels?: Record<string, RuntimePanelMetadata>;
};

export async function fetchRuntimePanels(panelIds: string[], limits: Record<string, number> = {}, signal?: AbortSignal) {
  const ids = [...new Set(panelIds.map((panelId) => panelId.trim()).filter(Boolean))];
  const params = new URLSearchParams({ ids: ids.join(',') });
  ids.forEach((panelId) => {
    const limit = limits[panelId];
    if (typeof limit === 'number' && Number.isFinite(limit)) params.set(`limit.${panelId}`, String(limit));
  });
  const envelope = await apiGet<ApiEnvelope<RuntimePanelsEnvelopeData>>(`/v1/runtime/panels?${params.toString()}`, signal);
  const meta = envelope.meta as RuntimePanelsEnvelopeMeta;
  return {
    generatedAt: envelope.generatedAt,
    status: envelope.status,
    panels: envelope.data?.panels || {},
    errors: Object.fromEntries(
      (envelope.errors || [])
        .filter((error) => error.panelId)
        .map((error) => [error.panelId as string, error.code || error.message]),
    ),
    requestId: envelope.requestId,
    metadata: meta.panels || {},
  } satisfies RuntimePanelsPayload;
}

export function fetchMarketSummary(marketId: number, timeoutMs = 3500) {
  return apiGetWithTimeout<MarketSummary>(`/markets/${marketId}`, timeoutMs);
}

type MarketDetailBundlePayload = {
  market?: MarketSummary | null;
  identity?: WorkspaceIdentity | null;
  diagnostics?: WorkspaceDiagnostics | null;
  health?: MarketWorkspaceHealth | null;
  evidence?: MarketWorkspaceEvidence | null;
  group?: MarketGroupDetail | null;
  selectedOutcome?: MarketGroupOutcome | null;
  price?: PriceSummary | null;
  chart?: ChartPayload | null;
  priceSeries?: ChartPayload['points'];
  trades?: TradeRow[];
  oracle?: OraclePayload | null;
  oracleEvents?: OraclePayload['timeline'];
  content?: ContentPayload | null;
  lob?: LobPayload | null;
  servingSource?: string | null;
  servingUpdatedAt?: string | null;
  generatedAt?: string | null;
  focusStatus?: 'ready' | 'warming' | string | null;
  cacheLayers?: Record<string, unknown> | null;
};

function normalizeMarketBundlePayload(payload: MarketDetailBundlePayload, marketId: number): WorkspaceBundle {
  const chart = payload.chart || (
    payload.priceSeries
      ? {
          marketId,
          localMarketId: marketId,
          range: '1d',
          interval: '5m',
          kind: 'probability',
          points: payload.priceSeries,
        }
      : null
  );
  const oracleSource = payload.oracle || null;
  const oracleSummary = oracleSource?.summary || null;
  const identity = payload.identity || null;
  const oracle = oracleSource || payload.oracleEvents || identity
    ? {
        ...(oracleSource || {}),
        marketId: Number(oracleSource?.marketId ?? identity?.marketId ?? marketId),
        localMarketId: Number(oracleSource?.localMarketId ?? identity?.localMarketId ?? marketId),
        gammaMarketId: oracleSource?.gammaMarketId ?? identity?.gammaMarketId ?? payload.market?.gammaMarketId ?? null,
        questionId: oracleSource?.questionId ?? identity?.questionId ?? payload.market?.questionId ?? null,
        conditionId: oracleSource?.conditionId ?? identity?.conditionId ?? payload.market?.conditionId ?? null,
        oracle: oracleSource?.oracle ?? identity?.oracle ?? payload.market?.oracle ?? null,
        currentStatus: oracleSource?.currentStatus ?? payload.market?.status ?? null,
        completionStatus: oracleSource?.completionStatus ?? oracleSummary?.completionStatus ?? null,
        isTradingClosed: oracleSource?.isTradingClosed ?? oracleSummary?.isTradingClosed ?? false,
        isResolved: oracleSource?.isResolved ?? oracleSummary?.isResolved ?? false,
        isFinal: oracleSource?.isFinal ?? oracleSummary?.isFinal ?? false,
        settlementOutcome: oracleSource?.settlementOutcome ?? oracleSummary?.settlementOutcome ?? null,
        settlementSource: oracleSource?.settlementSource ?? oracleSummary?.settlementSource ?? null,
        summary: oracleSummary,
        timeline: oracleSource?.timeline || payload.oracleEvents || [],
      }
    : null;
  return {
    market: payload.market || null,
    identity,
    diagnostics: payload.diagnostics || null,
    health: payload.health || null,
    evidence: payload.evidence || null,
    group: payload.group || null,
    selectedOutcome: payload.selectedOutcome || null,
    price: payload.price || null,
    chart,
    trades: payload.trades || [],
    oracle,
    content: payload.content || null,
    lob: payload.lob || null,
    servingSource: payload.servingSource || null,
    servingUpdatedAt: payload.servingUpdatedAt || null,
    generatedAt: payload.generatedAt || null,
    focusStatus: payload.focusStatus || null,
    cacheLayers: payload.cacheLayers || null,
  };
}

export async function fetchMarketDetailBundle(marketId: number, timeoutMs = 6500, signal?: AbortSignal): Promise<WorkspaceBundle> {
  const payload = await apiGetWithTimeout<MarketDetailBundlePayload>(`/markets/${marketId}/detail`, timeoutMs, signal);
  return normalizeMarketBundlePayload(payload, marketId);
}

export async function fetchMarketWorkspaceBundle(marketId: number, timeoutMs = 6500, signal?: AbortSignal): Promise<WorkspaceBundle> {
  const payload = await apiGetWithTimeout<MarketDetailBundlePayload>(`/markets/${marketId}/workspace`, timeoutMs, signal);
  return normalizeMarketBundlePayload(payload, marketId);
}

export async function fetchMarketFocusTile(marketId: number, timeoutMs = 2200, signal?: AbortSignal): Promise<WorkspaceBundle> {
  const payload = await apiGetWithTimeout<MarketDetailBundlePayload>(`/markets/${marketId}/focus-tile`, timeoutMs, signal);
  return normalizeMarketBundlePayload(payload, marketId);
}

export function fetchMarketPrice(marketId: number, timeoutMs = 5000) {
  return apiGetWithTimeout<PriceSummary>(`/markets/${marketId}/price`, timeoutMs);
}

type MarketChartRange = '1h' | '6h' | '1d' | '1w' | '1m' | 'all' | string;

function intervalForMarketChartRange(range: MarketChartRange) {
  switch (range) {
    case '1h':
      return '1m';
    case '6h':
      return '3m';
    case '1d':
      return '5m';
    case '1w':
      return '1h';
    case '1m':
    case 'all':
      return '4h';
    default:
      return '5m';
  }
}

export function fetchMarketChart(
  marketId: number,
  range: MarketChartRange = '1d',
  interval = intervalForMarketChartRange(range),
  timeoutMs = 6500,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({ range, interval });
  return apiGetWithTimeout<ChartPayload>(`/markets/${marketId}/chart?${params.toString()}`, timeoutMs, signal);
}

export function fetchMarketTrades(marketId: number, limit = 24, timeoutMs = 4000) {
  return apiGetWithTimeout<TradeRow[]>(`/markets/${marketId}/trades?limit=${limit}`, timeoutMs);
}

export function fetchMarketOracle(marketId: number, timeoutMs = 2200) {
  return apiGetWithTimeout<OraclePayload>(`/markets/${marketId}/oracle`, timeoutMs);
}

export function fetchMarketContent(marketId: number, limit = 20, timeoutMs = 5000) {
  return apiGetWithTimeout<ContentPayload>(`/content/market/${marketId}?limit=${limit}`, timeoutMs);
}

export function fetchMarketLob(marketId: number, timeoutMs = 4000) {
  return apiGetWithTimeout<LobPayload>(`/runtime/lob/${marketId}`, timeoutMs);
}

export function fetchMarketLobByToken(
  tokenId: string,
  title = '',
  noTokenId = '',
  timeoutMs = 4000,
  signal?: AbortSignal,
  marketId?: number | null,
) {
  const params = new URLSearchParams();
  if (title.trim()) params.set('title', title.trim());
  if (noTokenId.trim()) params.set('noTokenId', noTokenId.trim());
  if (marketId != null && Number.isFinite(Number(marketId))) params.set('marketId', String(marketId));
  params.set('_ts', String(Date.now()));
  const suffix = params.toString() ? `?${params.toString()}` : '';
  return apiGetWithTimeout<LobPayload>(`/runtime/lob/token/${encodeURIComponent(tokenId)}${suffix}`, timeoutMs, signal);
}

export function fetchMarketLobSnapshotsByToken(tokenId: string, side = '', limit = 48, timeoutMs = 4500) {
  const params = new URLSearchParams();
  if (side.trim()) params.set('side', side.trim().toUpperCase());
  params.set('limit', String(limit || 48));
  params.set('_ts', String(Date.now()));
  return apiGetWithTimeout<LobSnapshotPayload>(`/runtime/lob/token/${encodeURIComponent(tokenId)}/snapshots?${params.toString()}`, timeoutMs);
}

function preferLoadedBundle(primary: WorkspaceBundle, secondary: WorkspaceBundle): WorkspaceBundle {
  const primaryOracle = primary.oracle;
  const secondaryOracle = secondary.oracle;
  return {
    market: primary.market || secondary.market,
    identity: primary.identity || secondary.identity,
    diagnostics: primary.diagnostics || secondary.diagnostics,
    health: primary.health || secondary.health,
    evidence: primary.evidence || secondary.evidence,
    group: primary.group || secondary.group,
    selectedOutcome: primary.selectedOutcome || secondary.selectedOutcome,
    price: primary.price || secondary.price,
    chart: primary.chart?.points?.length ? primary.chart : secondary.chart,
    trades: primary.trades?.length ? primary.trades : secondary.trades,
    oracle: primaryOracle ? primaryOracle : secondaryOracle,
    content: primary.content?.items?.length ? primary.content : secondary.content,
    lob: primary.lob || secondary.lob,
    servingSource: primary.servingSource || secondary.servingSource,
    servingUpdatedAt: primary.servingUpdatedAt || secondary.servingUpdatedAt,
    generatedAt: primary.generatedAt || secondary.generatedAt,
    focusStatus: primary.focusStatus || secondary.focusStatus,
    cacheLayers: primary.cacheLayers || secondary.cacheLayers,
  };
}

const workspaceBundleInflight = new Map<string, Promise<WorkspaceBundle>>();

function emptyWorkspaceBundle(): WorkspaceBundle {
  return {
    market: null,
    identity: null,
    diagnostics: null,
    health: null,
    evidence: null,
    group: null,
    selectedOutcome: null,
    price: null,
    chart: null,
    trades: [],
    oracle: null,
    content: null,
    lob: null,
    servingSource: null,
    servingUpdatedAt: null,
    generatedAt: null,
    focusStatus: null,
    cacheLayers: null,
  };
}

export function fetchMarketAiInsights(payload: MarketAiInsightPayload, timeoutMs = 20000) {
  return apiPostAgentOnce<MarketAiInsightResponse>('/agent/market-insights', payload, timeoutMs);
}

export function fetchMarketWideAiSnapshot(lens: MarketWideAiInsightLens, timeoutMs = 8000) {
  return apiGetWithTimeout<MarketWideAiInsightResponse>(
    `/runtime/agent/market-wide-insights/${encodeURIComponent(lens)}`,
    timeoutMs,
  );
}

export async function fetchWorkspaceBundle(
  marketId: number,
  options: { includeContent?: boolean; includeLob?: boolean; signal?: AbortSignal } = {},
): Promise<WorkspaceBundle> {
  const includeContent = Boolean(options.includeContent);
  const includeLob = Boolean(options.includeLob);
  const inflightKey = `${marketId}:${includeContent ? 'content' : 'base'}:${includeLob ? 'lob' : 'no-lob'}`;
  const inflight = options.signal ? null : workspaceBundleInflight.get(inflightKey);
  if (inflight) return inflight;

  const request = (async () => {
    const contentPromise = includeContent
      ? fetchMarketContent(marketId, 20, 3800)
      : Promise.resolve(null);
    const lobPromise = includeLob ? fetchMarketLob(marketId, 1800) : Promise.resolve(null);
    const detailPromise = fetchMarketWorkspaceBundle(marketId, 22000, options.signal)
      .catch(() => fetchMarketDetailBundle(marketId, 12000, options.signal));
    const [detailResult, contentResult, lobResult] = await Promise.allSettled([detailPromise, contentPromise, lobPromise]);
    const detailBundle = detailResult.status === 'fulfilled' ? detailResult.value : emptyWorkspaceBundle();
    const secondary: WorkspaceBundle = {
      market: null,
      identity: null,
      diagnostics: null,
      health: null,
      evidence: null,
      group: null,
      selectedOutcome: null,
      price: null,
      chart: null,
      trades: [],
      oracle: null,
      content: contentResult.status === 'fulfilled' ? contentResult.value : null,
      lob: includeLob && lobResult.status === 'fulfilled' ? lobResult.value : null,
      servingSource: null,
      servingUpdatedAt: null,
      generatedAt: null,
      focusStatus: null,
      cacheLayers: null,
    };
    return preferLoadedBundle(detailBundle, secondary);
  })();

  if (!options.signal) {
    workspaceBundleInflight.set(inflightKey, request);
    void request.finally(() => workspaceBundleInflight.delete(inflightKey));
  }
  return request;
}
