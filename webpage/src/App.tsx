import { lazy, Suspense } from 'preact/compat';
import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { AppShell } from '@/components/AppShell';
import { FocusedMarketStrip } from '@/components/FocusedMarketStrip';
import { PanelLoading } from '@/components/Panel';
import {
  PanelRuntimeBoundary,
  PanelWorkspaceSlot,
  type PanelLayoutPrefs,
} from '@/components/PanelWorkspaceSlot';
import { WorldGlobe, type WorldGlobeStatusMetrics } from '@/components/WorldGlobe';
import { DEFAULT_PANEL_IDS, PANEL_LIBRARY, PANEL_REGISTRY, RUNTIME_PANEL_MODULES } from '@/panels/registry';
import { mergeRuntimeData } from '@/panels/runtime-store';
import { usePanelRuntime } from '@/panels/usePanelRuntime';
import {
  fetchAllActiveMarkets,
  fetchBootstrap,
  fetchLatestContent,
  fetchMarketChart,
  fetchMarketFocusTile,
  fetchMarketGroupChart,
  fetchMarketGroupDetail,
  fetchMarketGroups,
  fetchMarketSearch,
  fetchRecentOracle,
  fetchRecentTrades,
  fetchRuntimeGeoSanctionsShock,
  fetchSystemHealth,
  fetchWorkspaceBundle,
} from '@/services/api';
import { AuthApiError, fetchAuthSession } from '@/services/auth';
import { useI18n, type MessageKey } from '@/services/i18n';
import { specialistPanelMeta } from '@/services/specialist-i18n';
import { fetchWorkspaceLayout, saveWorkspaceLayout, type WorkspaceLayout } from '@/services/product';
import {
  adaptBreakingEventMapPayload,
  adaptGeoShockCountryRiskPayload,
  adaptGeoShockPayload,
  adaptTransportReference,
  filterWorldEventMapEvents,
  filterWorldEventMapEventsForLayers,
  MapStatus,
  MapToolbar,
  LayerPanel,
  selectableWorldEventLayers,
  sourceStatusFromAdapter,
  useCountryGeometry,
  useNaturalHazards,
  useAviationViewport,
  useWorldEventMapState,
  worldEventLayerById,
  type GeoEvent,
  type MapSymbolKey,
  type AviationLensMode,
  type AviationRiskSource,
  type WorldEventMapState,
  type WorldEventRegion,
} from '@/features/world-event-map';
import type {
  BootstrapPayload,
  ContentItem,
  MarketGroupChartPayload,
  MarketGroupChartRange,
  MarketGroupDetail,
  MarketListItem,
  MarketGroupItem,
  MarketGroupsPayload,
  MarketGroupSort,
  MarketsPayload,
  MarketSummary,
  OracleEvent,
  PanelRenderContext,
  RuntimeF1Payload,
  RuntimeBreakingEventRadarPayload,
  RuntimeGeoSanctionsShockItem,
  RuntimeGeoSanctionsShockPayload,
  RuntimeGlobalTransportShippingPayload,
  RuntimeInflationNowcastPayload,
  RuntimeJin10Payload,
  RuntimeMarketGroup,
  RuntimeNbaIntelPayload,
  RuntimeNbaMatchupPredictorPayload,
  RuntimeNbaPayload,
  RuntimeSignalPayload,
  SystemHealth,
  TradeRow,
  WorkspaceBundle,
} from '@/types';
import type { PanelRuntimeData } from '@/panels/types';

type LayerToggle = {
  id: string;
  label: string;
  panelEmoji: string;
  icon: MapSymbolKey;
  enabled: boolean;
  hint?: string;
  aliases: string[];
  availability: 'ready' | 'degraded' | 'unavailable';
  availabilityReason?: string;
  isExecutable: boolean;
  sourceKeys: string[];
  requiredSources: string[];
};

type RegionKey = WorldEventRegion;
type MapViewMode = '3d' | '2d';
type CommandPaletteTab = 'markets' | 'panels' | 'commands';
const PANEL_STORAGE_KEY = 'polydata:workspace-panels:v4';
const PANEL_LAYOUT_STORAGE_KEY = 'polydata:workspace-panel-layout:v4';
const PANEL_LAYOUT_PROMOTION_STORAGE_KEY = 'polydata:workspace-panel-layout-promotions:v1';
const PROMOTED_WIDE_PANEL_IDS = ['breaking-event-radar', 'global-transport-shipping'];
const MARKET_GROUP_SORT_STORAGE_KEY = 'wm:marketGroupSort:v1';
const DEFAULT_MAP_VIEW_MODE: MapViewMode = '2d';
const VIEW_STORAGE_KEY = 'polydata:map-view:v4';
const LIBRARY_STORAGE_KEY = 'polydata:panel-library-open:v1';
const WORKSPACE_SYNC_META_KEY = 'polydata:workspace-sync-meta:v1';
const GEO_SHOCK_STORAGE_KEY = 'polydata:seed:world:geo-sanctions-shock:v1';
const GEO_SHOCK_LOCAL_STALE_MS = 24 * 60 * 60 * 1000;
const QuantWorkspace = lazy(() => import('@/workspaces/quant/QuantWorkspace').then((module) => ({ default: module.QuantWorkspace })));
const MarketWorkspace = lazy(() => import('@/workspaces/market/MarketWorkspace').then((module) => ({ default: module.MarketWorkspace })));
const DataQualityWorkspace = lazy(() => import('@/workspaces/data-quality/DataQualityWorkspace').then((module) => ({ default: module.DataQualityWorkspace })));
const LoginWorkspace = lazy(() => import('@/workspaces/auth/AuthWorkspace').then((module) => ({ default: module.LoginWorkspace })));
const AccountWorkspace = lazy(() => import('@/workspaces/auth/AuthWorkspace').then((module) => ({ default: module.AccountWorkspace })));
const WatchlistWorkspace = lazy(() => import('@/workspaces/watchlist/WatchlistWorkspace').then((module) => ({ default: module.WatchlistWorkspace })));
const BriefingManagerWorkspace = lazy(() => import('@/workspaces/briefing/BriefingWorkspace').then((module) => ({ default: module.BriefingManagerWorkspace })));
const PublicBriefingWorkspace = lazy(() => import('@/workspaces/briefing/BriefingWorkspace').then((module) => ({ default: module.PublicBriefingWorkspace })));
const DeveloperWorkspace = lazy(() => import('@/workspaces/developers/DeveloperWorkspace').then((module) => ({ default: module.DeveloperWorkspace })));
const WorldEventMap = lazy(() => import('@/features/world-event-map/components/WorldEventMap').then((module) => ({ default: module.WorldEventMap })));
const FAST_MARKETS_PAGE_SIZE = 80;
const SEARCH_MARKETS_PAGE_SIZE = 120;
const INITIAL_LAYERS: LayerToggle[] = selectableWorldEventLayers().map((layer) => ({
  id: layer.id,
  label: layer.label,
  panelEmoji: layer.panelEmoji,
  icon: layer.icon,
  enabled: layer.defaultEnabled,
  hint: layer.hint,
  aliases: [...layer.aliases],
  availability: layer.availability,
  availabilityReason: layer.availabilityReason,
  isExecutable: layer.isExecutable(),
  sourceKeys: [...layer.sourceKeys],
  requiredSources: [...layer.requiredSources],
}));

const REGION_OPTIONS: Array<{ value: RegionKey; label: string }> = [
  { value: 'global', label: 'Global' },
  { value: 'america', label: 'Americas' },
  { value: 'mena', label: 'MENA' },
  { value: 'eu', label: 'Europe' },
  { value: 'asia', label: 'Asia' },
  { value: 'latam', label: 'LATAM' },
  { value: 'africa', label: 'Africa' },
  { value: 'oceania', label: 'Oceania' },
];
const MAP_VIEW_OPTIONS: Array<{ value: MapViewMode; label: string }> = [
  { value: '2d', label: '2D Map' },
  { value: '3d', label: '3D Globe' },
];
const REGION_MESSAGE_KEYS: Record<RegionKey, MessageKey> = {
  global: 'region.global',
  america: 'region.america',
  mena: 'region.mena',
  eu: 'region.eu',
  asia: 'region.asia',
  latam: 'region.latam',
  africa: 'region.africa',
  oceania: 'region.oceania',
};
const MAP_VIEW_MESSAGE_KEYS: Record<MapViewMode, MessageKey> = {
  '2d': 'map.2d',
  '3d': 'map.3d',
};
const CORE_PANEL_META_KEYS: Record<string, { title: MessageKey; description: MessageKey }> = {
  'active-markets': { title: 'panelMeta.activeMarkets.title', description: 'panelMeta.activeMarkets.description' },
  'market-summary': { title: 'panelMeta.marketSummary.title', description: 'panelMeta.marketSummary.description' },
  'featured-market': { title: 'panelMeta.marketContext.title', description: 'panelMeta.marketContext.description' },
  'price-chart': { title: 'panelMeta.priceSurface.title', description: 'panelMeta.priceSurface.description' },
  'oracle-feed': { title: 'panelMeta.oracleFeed.title', description: 'panelMeta.oracleFeed.description' },
  'related-news': { title: 'panelMeta.relatedIntel.title', description: 'panelMeta.relatedIntel.description' },
};

function localizedLayerLabel(layer: LayerToggle, t: (key: MessageKey, params?: Record<string, string | number>) => string) {
  const messageKey = worldEventLayerById(layer.id)?.messageKey as MessageKey | undefined;
  return messageKey ? t(messageKey) : layer.label;
}

function localizedPanelMeta(
  panel: (typeof PANEL_LIBRARY)[number],
  t: (key: MessageKey, params?: Record<string, string | number>) => string,
) {
  const keys = CORE_PANEL_META_KEYS[panel.id];
  return keys
    ? { title: t(keys.title), description: t(keys.description) }
    : specialistPanelMeta(panel.id, panel.title, panel.description, t);
}

function isMapViewMode(value: unknown): value is MapViewMode {
  return value === '3d' || value === '2d';
}

const MAP_BOTTOM_PANEL_IDS: string[] = [];
const FOCUSED_STRIP_PANEL_IDS = new Set(['active-markets', 'price-chart', 'lob-depth', 'global-orderfilled', 'oracle-feed']);
const MARKET_FOCUS_BROWSER_CACHE_MS = 15_000;
function reorderPanelIds(panelIds: string[], draggedPanelId: string, targetPanelId: string, insertAfter: boolean) {
  if (draggedPanelId === targetPanelId) return panelIds;
  const next = panelIds.filter((panelId) => panelId !== draggedPanelId);
  const targetIndex = next.indexOf(targetPanelId);
  if (targetIndex === -1) return panelIds;
  next.splice(targetIndex + (insertAfter ? 1 : 0), 0, draggedPanelId);
  return next;
}

function clampMapZoom(value: unknown) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 1.25;
  return Math.max(0.75, Math.min(8, Math.round(numeric * 4) / 4));
}

function isLiveStatus(status?: string | null) {
  const normalized = String(status || '').trim().toLowerCase();
  return normalized === 'active' || normalized === 'proposed';
}

type DefaultMarketCandidate = Pick<MarketSummary, 'id' | 'slug' | 'title' | 'category' | 'tags' | 'status'>;

function isSuppressedDefaultMarket(market?: Partial<DefaultMarketCandidate> | null) {
  const text = [
    market?.title,
    market?.slug,
    market?.category,
    ...(market?.tags || []),
  ].filter(Boolean).join(' ').toLowerCase();
  return (
    text.includes(' up or down - ')
    || text.includes('updown-5m')
    || text.includes('updown-15m')
    || text.includes('recurring')
    || text.includes('hide-from-new')
    || text.includes('onchain-registry')
    || text.includes('on-chain recovered market')
  );
}

function pickDefaultMarketId(markets: MarketListItem[], featured?: MarketSummary | null) {
  const firstLive = markets.find((market) => isLiveStatus(market.status) && !isSuppressedDefaultMarket(market));
  if (firstLive) return firstLive.id;
  const firstEligible = markets.find((market) => !isSuppressedDefaultMarket(market));
  if (firstEligible) return firstEligible.id;
  if (featured && !isSuppressedDefaultMarket(featured)) return featured.id;
  return markets[0]?.id ?? featured?.id ?? null;
}

function groupHasTerminalProbability(group: MarketGroupItem) {
  const values = [
    group.latestBlockClosePrice,
    ...(group.outcomes || []).flatMap((outcome) => [outcome.blockCloseYesPrice, outcome.yesPrice, outcome.noPrice]),
    ...(group.topOutcomes || []).flatMap((outcome) => [outcome.blockCloseYesPrice, outcome.yesPrice, outcome.noPrice]),
  ];
  return values.some((value) => {
    const numeric = Number(value);
    return Number.isFinite(numeric) && (numeric <= 0.03 || numeric >= 0.97);
  });
}

function groupOutcomePrice(outcome: { blockCloseYesPrice?: string | number | null; yesPrice?: string | number | null }) {
  const blockClose = Number(outcome.blockCloseYesPrice);
  if (Number.isFinite(blockClose)) return blockClose;
  const yes = Number(outcome.yesPrice);
  return Number.isFinite(yes) ? yes : null;
}

function groupOutcomeIsTerminal(outcome: { blockCloseYesPrice?: string | number | null; yesPrice?: string | number | null }) {
  const price = groupOutcomePrice(outcome);
  return price != null && (price <= 0.03 || price >= 0.97);
}

function pickDefaultGroupOutcome(group: MarketGroupItem, outcomeKey?: string | null, marketId?: number | null) {
  const seen = new Set<string>();
  const candidates = [...(group.outcomes || []), ...(group.topOutcomes || [])].filter((outcome, index) => {
    if (!outcome.marketId && !outcome.yesTokenId) return false;
    const key = String(outcome.marketId ?? outcome.outcomeKey ?? outcome.gammaMarketId ?? index);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const liveCandidates = candidates.filter((outcome) => !groupOutcomeIsTerminal(outcome));
  const eligible = liveCandidates.length ? liveCandidates : candidates;
  const requestedMarketId = marketId != null ? Number(marketId) : null;
  if (requestedMarketId != null && Number.isFinite(requestedMarketId)) {
    const matched = eligible.find((outcome) => Number(outcome.marketId) === requestedMarketId);
    if (matched) return matched;
  }
  if (outcomeKey) {
    const matched = eligible.find((outcome) => outcome.outcomeKey === outcomeKey);
    if (matched) return matched;
  }
  if (group.defaultOutcomeKey) {
    const matched = eligible.find((outcome) => outcome.outcomeKey === group.defaultOutcomeKey);
    if (matched) return matched;
  }
  return eligible
    .slice()
    .sort((left, right) => {
      const leftPrice = groupOutcomePrice(left);
      const rightPrice = groupOutcomePrice(right);
      const leftVolume = Number(left.volume24h || 0);
      const rightVolume = Number(right.volume24h || 0);
      const leftTrades = Number(left.tradeCount24h || 0);
      const rightTrades = Number(right.tradeCount24h || 0);
      const leftDistance = leftPrice == null ? 0 : Math.min(1, Math.abs(leftPrice - 0.5) * 2);
      const rightDistance = rightPrice == null ? 0 : Math.min(1, Math.abs(rightPrice - 0.5) * 2);
      const leftBlockClose = left.blockCloseYesPrice == null || left.blockCloseYesPrice === '' ? 0 : 1;
      const rightBlockClose = right.blockCloseYesPrice == null || right.blockCloseYesPrice === '' ? 0 : 1;
      const leftScore = Math.min(70, Math.pow(Math.max(leftVolume, 0), 0.35))
        + Math.min(70, Math.max(leftTrades, 0) * 3)
        + leftDistance * 24
        + leftBlockClose * 28
        + (left.marketId ? 12 : 0)
        + (left.yesTokenId ? 8 : 0)
        - (leftPrice != null && Math.abs(leftPrice - 0.5) < 0.0001 && leftTrades <= 0 && leftVolume < 25 ? 45 : 0);
      const rightScore = Math.min(70, Math.pow(Math.max(rightVolume, 0), 0.35))
        + Math.min(70, Math.max(rightTrades, 0) * 3)
        + rightDistance * 24
        + rightBlockClose * 28
        + (right.marketId ? 12 : 0)
        + (right.yesTokenId ? 8 : 0)
        - (rightPrice != null && Math.abs(rightPrice - 0.5) < 0.0001 && rightTrades <= 0 && rightVolume < 25 ? 45 : 0);
      return rightScore - leftScore || rightVolume - leftVolume || rightTrades - leftTrades;
    })[0] || null;
}

function pickDefaultMarketGroup(groups: MarketGroupItem[]) {
  const eligibleGroups = groups.filter((group) => !groupHasTerminalProbability(group) || pickDefaultGroupOutcome(group));
  const liveGroups = eligibleGroups.filter((group) => Number(group.tradeCount24h || 0) > 0);
  return (
    liveGroups.find((group) => Number(group.volume24h || 0) > 0 && Number(group.outcomeCount || 0) > 1)
    || liveGroups.find((group) => Number(group.outcomeCount || 0) > 1)
    || liveGroups[0]
    || eligibleGroups[0]
    || null
  );
}

function currentUtcClock(now: Date) {
  return now.toLocaleString('en-GB', {
    weekday: 'short',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: 'UTC',
    hour12: false,
  }).replace(',', '').toUpperCase() + ' UTC';
}

function LiveUtcClock() {
  const [clockNow, setClockNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setClockNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return <div className="wm-map-clock">{currentUtcClock(clockNow)}</div>;
}

function commandMarketStatus(market: MarketListItem) {
  const status = String(market.status || 'market').trim();
  return status ? status.replace(/[_-]+/g, ' ').toUpperCase() : 'MARKET';
}

function commandMarketStatusClass(market: MarketListItem) {
  const status = String(market.status || '').toLowerCase();
  if (status.includes('active') || status.includes('open')) return 'active';
  if (status.includes('closed')) return 'closed';
  if (status.includes('resolved') || status.includes('final')) return 'resolved';
  return 'neutral';
}

function hasGeoConflictCoordinates(item: RuntimeGeoSanctionsShockItem) {
  const lat = Number(item.latitude);
  const lon = Number(item.longitude);
  return Number.isFinite(lat) && Number.isFinite(lon) && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180;
}

function WorldEventInlineMap({
  events,
  state,
  onCameraChange,
  onEventSelect,
  onOpenMarket,
  onAviationLensChange,
  onAviationRiskSourceChange,
  onAviationClose,
  onCountryChange,
}: {
  events: GeoEvent[];
  state: WorldEventMapState;
  onCameraChange: (camera: Pick<WorldEventMapState, 'center' | 'zoom'>) => void;
  onEventSelect: (eventId: string | null) => void;
  onOpenMarket: (marketId: number) => void;
  onAviationLensChange: (lens: AviationLensMode) => void;
  onAviationRiskSourceChange: (source: AviationRiskSource) => void;
  onAviationClose: () => void;
  onCountryChange: (countryCode: string | null) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="wm-inline-weather-map">
      <div className="wm-inline-weather-map-hint">{t('atlas.weatherHint')}</div>
      <Suspense fallback={<div className="wm-world-event-map-shell" role="status">Loading world event renderer…</div>}>
        <WorldEventMap
          events={events}
          state={state}
          onCameraChange={onCameraChange}
          onEventSelect={onEventSelect}
          onOpenMarket={onOpenMarket}
          onAviationLensChange={onAviationLensChange}
          onAviationRiskSourceChange={onAviationRiskSourceChange}
          onAviationClose={onAviationClose}
          onCountryChange={onCountryChange}
          height={620}
        />
      </Suspense>
    </div>
  );
}

function sanitizePanelIds(panelIds: string[]) {
  const valid = new Set(PANEL_LIBRARY.map((panel) => panel.id));
  const unique: string[] = [];
  for (const panelId of panelIds) {
    if (!valid.has(panelId) || unique.includes(panelId)) continue;
    unique.push(panelId);
  }
  return unique;
}

function defaultWorkspacePanelIds(bootstrapPayload?: BootstrapPayload | null) {
  return sanitizePanelIds([
    ...DEFAULT_PANEL_IDS,
    ...(bootstrapPayload?.defaultWorkspace?.panels || []),
  ]);
}

function readJsonStorage<T>(key: string, fallback: T): T {
  if (typeof window === 'undefined') return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function readStringStorage<T extends string>(key: string, fallback: T): T {
  if (typeof window === 'undefined') return fallback;
  const raw = window.localStorage.getItem(key);
  return (raw as T) || fallback;
}

type GeoShockLocalSeed = {
  storedAt: number;
  payload: RuntimeGeoSanctionsShockPayload;
};

type WorkspaceSyncStatus = 'checking' | 'local' | 'saving' | 'synced' | 'conflict' | 'error';

function hasRenderableGeoShockPayload(payload?: RuntimeGeoSanctionsShockPayload | null) {
  return Boolean((payload?.items || []).some(hasGeoConflictCoordinates));
}

function readGeoShockRuntimeSeed(): PanelRuntimeData {
  if (typeof window === 'undefined') return {};
  try {
    const raw = window.localStorage.getItem(GEO_SHOCK_STORAGE_KEY);
    if (!raw) return {};
    const cached = JSON.parse(raw) as GeoShockLocalSeed;
    if (!cached?.payload || !hasRenderableGeoShockPayload(cached.payload)) return {};
    if (Date.now() - Number(cached.storedAt || 0) > GEO_SHOCK_LOCAL_STALE_MS) return {};
    return {
      'geo-sanctions-shock': {
        ...cached.payload,
        cacheMode: 'local-stale',
      },
    };
  } catch {
    return {};
  }
}

function writeGeoShockRuntimeSeed(payload?: RuntimeGeoSanctionsShockPayload | null) {
  if (typeof window === 'undefined' || !hasRenderableGeoShockPayload(payload)) return;
  try {
    window.localStorage.setItem(GEO_SHOCK_STORAGE_KEY, JSON.stringify({ storedAt: Date.now(), payload }));
  } catch {
    // The remote seed remains authoritative; local storage is only a first-paint fallback.
  }
}

function readSearchParam(key: string): string | null {
  if (typeof window === 'undefined') return null;
  return new URLSearchParams(window.location.search).get(key);
}

function readMarketGroupSortStorage(): MarketGroupSort {
  const saved = readStringStorage<string>(MARKET_GROUP_SORT_STORAGE_KEY, 'active');
  return saved === 'new'
    || saved === 'volume'
    || saved === 'active'
    || saved === 'close'
    || saved === 'move'
    || saved === 'trades'
    ? saved
    : 'active';
}

function findGroupForMarketId(groups: MarketGroupItem[], marketId: number | null) {
  if (!marketId) return null;
  return groups.find((group) => (group.outcomes || []).some((outcome) => Number(outcome.marketId) === marketId)) || null;
}

function outcomeKeyForGroupMarket(group: MarketGroupItem, marketId?: number | null, fallbackKey?: string | null) {
  const numericMarketId = marketId != null ? Number(marketId) : null;
  if (numericMarketId != null && Number.isFinite(numericMarketId)) {
    const matchedOutcome = [...(group.outcomes || []), ...(group.topOutcomes || [])]
      .find((outcome) => Number(outcome.marketId) === numericMarketId);
    if (matchedOutcome?.outcomeKey) return matchedOutcome.outcomeKey;
  }
  return fallbackKey || group.defaultOutcomeKey || null;
}

type RuntimePanelRefreshOptions = {
  bootstrapPayload?: BootstrapPayload | null;
  activePanelIds?: string[];
};

function optimisticBundleFromMarket(market: MarketListItem): WorkspaceBundle {
  const latest = market.latestPrice ?? null;
  const numericLatest = Number(latest);
  const latestNo = Number.isFinite(numericLatest) ? String(1 - numericLatest) : null;
  const timestamp = market.lastTradeAt || market.createdAt || new Date().toISOString();
  return {
    market: {
      id: market.id,
      slug: market.slug,
      title: market.title,
      conditionId: market.conditionId,
      questionId: market.questionId,
      status: market.status,
      latestPrice: latest,
      latestYesPrice: latest,
      latestNoPrice: latestNo,
      endDate: market.endDate,
      createdAt: market.createdAt,
      category: market.category,
      tags: market.tags,
      yesTokenId: market.yesTokenId,
      noTokenId: market.noTokenId,
    },
    identity: {
      localMarketId: market.id,
      marketId: market.id,
      gammaMarketId: market.gammaMarketId,
      slug: market.slug,
      conditionId: market.conditionId,
      questionId: market.questionId,
      yesTokenId: market.yesTokenId,
      noTokenId: market.noTokenId,
    },
    diagnostics: null,
    health: null,
    group: null,
    selectedOutcome: null,
    trades: [],
    oracle: null,
    price: {
      marketId: market.id,
      latestPrice: latest == null ? null : String(latest),
      latestYesPrice: latest == null ? null : String(latest),
      latestNoPrice: latestNo,
      change24h: market.change24h == null ? null : String(market.change24h),
      volume24h: market.volume24h == null ? null : String(market.volume24h),
      tradeCount24h: Number(market.tradeCount24h || 0),
      updatedAt: timestamp,
    },
    chart: latest == null
      ? null
      : {
          marketId: market.id,
          range: 'snapshot',
          interval: 'snapshot',
          kind: 'probability',
          points: [
            { timestamp, yesPrice: latest, noPrice: latestNo },
            { timestamp: new Date().toISOString(), yesPrice: latest, noPrice: latestNo },
          ],
        },
    content: null,
    lob: null,
  };
}

function optimisticBundleFromGroup(group: MarketGroupItem, marketId: number | null, outcomeKey?: string | null): WorkspaceBundle {
  const selectedOutcome = pickDefaultGroupOutcome(group, outcomeKey, marketId);
  const selectedMarketId = Number(selectedOutcome?.marketId ?? marketId ?? group.defaultMarketId ?? 0);
  const price = selectedOutcome?.blockCloseYesPrice ?? selectedOutcome?.yesPrice ?? group.latestBlockClosePrice ?? null;
  const numericPrice = Number(price);
  const noPrice = selectedOutcome?.noPrice ?? (Number.isFinite(numericPrice) ? String(1 - numericPrice) : null);
  const timestamp = selectedOutcome?.lastTradeAt || group.lastActivityAt || group.createdAt || new Date().toISOString();
  const marketSlug = selectedOutcome?.slug || group.slug || `market-${selectedMarketId || group.groupId}`;
  const optimisticGroup: MarketGroupDetail = {
    ...group,
    generatedAt: group.generatedAt || new Date().toISOString(),
    status: 'optimistic',
  };
  return {
    market: selectedMarketId ? {
      id: selectedMarketId,
      slug: marketSlug,
      title: selectedOutcome?.title || selectedOutcome?.label || group.title,
      status: 'OPEN',
      latestPrice: price == null ? null : String(price),
      latestYesPrice: price == null ? null : String(price),
      latestNoPrice: noPrice == null ? null : String(noPrice),
      endDate: group.endDate || null,
      createdAt: group.createdAt || null,
      category: group.category || undefined,
      tags: group.tags || [],
      yesTokenId: selectedOutcome?.yesTokenId ?? null,
      noTokenId: selectedOutcome?.noTokenId ?? null,
    } : null,
    identity: {
      localMarketId: selectedMarketId || null,
      marketId: selectedMarketId || null,
      gammaMarketId: selectedOutcome?.gammaMarketId ?? null,
      slug: marketSlug,
      conditionId: selectedOutcome?.conditionId ?? null,
      eventId: group.eventId == null ? null : String(group.eventId),
      selectedOutcomeKey: selectedOutcome?.outcomeKey ?? outcomeKey ?? group.defaultOutcomeKey ?? null,
      yesTokenId: selectedOutcome?.yesTokenId ?? null,
      noTokenId: selectedOutcome?.noTokenId ?? null,
    },
    diagnostics: null,
    health: null,
    group: optimisticGroup,
    selectedOutcome,
    trades: [],
    oracle: null,
    price: {
      marketId: selectedMarketId || 0,
      latestPrice: price == null ? null : String(price),
      latestYesPrice: price == null ? null : String(price),
      latestNoPrice: noPrice == null ? null : String(noPrice),
      change24h: selectedOutcome?.change24h == null ? null : String(selectedOutcome.change24h),
      volume24h: selectedOutcome?.volume24h == null
        ? (group.volume24h == null ? null : String(group.volume24h))
        : String(selectedOutcome.volume24h),
      tradeCount24h: Number(selectedOutcome?.tradeCount24h ?? group.tradeCount24h ?? 0),
      updatedAt: timestamp,
    },
    chart: Number.isFinite(numericPrice) && selectedMarketId
      ? {
          marketId: selectedMarketId,
          range: 'snapshot',
          interval: 'snapshot',
          kind: 'probability',
          points: [
            { timestamp, yesPrice: String(price), noPrice },
            { timestamp: new Date().toISOString(), yesPrice: String(price), noPrice },
          ],
        }
      : null,
    content: null,
    lob: null,
  };
}

function emptyWorkspaceBundle(): WorkspaceBundle {
  return {
    market: null,
    identity: null,
    diagnostics: null,
    health: null,
    group: null,
    selectedOutcome: null,
    trades: [],
    oracle: null,
    price: null,
    chart: null,
    content: null,
    lob: null,
  };
}

function isSnapshotChart(chart: WorkspaceBundle['chart']) {
  if (!chart) return false;
  return chart.range === 'snapshot' || chart.interval === 'snapshot' || (chart.points || []).length <= 2;
}

function chooseWorkspaceChart(current: WorkspaceBundle['chart'], patch: WorkspaceBundle['chart']) {
  const patchPoints = patch?.points || [];
  if (!patchPoints.length) return current;
  if (!patch) return current;
  const currentPoints = current?.points || [];
  if (!currentPoints.length) return patch;
  const patchIsSnapshot = isSnapshotChart(patch);
  const currentIsSnapshot = isSnapshotChart(current);
  if (patchIsSnapshot && !currentIsSnapshot) return current;
  if (!patchIsSnapshot && currentIsSnapshot) return patch;
  if (patch.range !== current?.range && !patchIsSnapshot) return patch;
  return patchPoints.length >= currentPoints.length ? patch : current;
}

function lobHasLevels(lob: WorkspaceBundle['lob']) {
  const yesLevels = (lob?.yes?.bids?.length || 0) + (lob?.yes?.asks?.length || 0);
  const noLevels = (lob?.no?.bids?.length || 0) + (lob?.no?.asks?.length || 0);
  return yesLevels + noLevels > 0;
}

function chooseWorkspaceLob(current: WorkspaceBundle['lob'], patch: WorkspaceBundle['lob']) {
  if (!patch) return current;
  if (lobHasLevels(patch)) return patch;
  if (lobHasLevels(current)) return current;
  return patch;
}

function bundleMatchesMarket(bundle: WorkspaceBundle | null, marketId: number) {
  if (!bundle) return false;
  const ids = [
    bundle.market?.id,
    bundle.identity?.localMarketId,
    bundle.identity?.marketId,
    bundle.price?.marketId,
    bundle.oracle?.localMarketId,
    bundle.oracle?.marketId,
    bundle.chart?.localMarketId,
    bundle.chart?.marketId,
    bundle.content?.marketId,
    bundle.lob?.localMarketId,
    bundle.lob?.marketId,
    bundle.selectedOutcome?.marketId,
    bundle.trades?.[0]?.marketId,
  ];
  return ids.some((id) => Number(id) === Number(marketId));
}

function mergeWorkspaceBundle(base: WorkspaceBundle | null, patch: WorkspaceBundle): WorkspaceBundle {
  const current = base || emptyWorkspaceBundle();
  return {
    market: patch.market || current.market,
    identity: patch.identity || current.identity,
    diagnostics: patch.diagnostics || current.diagnostics,
    health: patch.health || current.health,
    evidence: patch.evidence || current.evidence,
    group: patch.group || current.group,
    selectedOutcome: patch.selectedOutcome || current.selectedOutcome,
    price: patch.price || current.price,
    chart: chooseWorkspaceChart(current.chart, patch.chart),
    trades: patch.trades?.length ? patch.trades : current.trades,
    oracle: patch.oracle || current.oracle,
    content: patch.content?.items?.length ? patch.content : current.content,
    lob: chooseWorkspaceLob(current.lob, patch.lob),
    servingSource: patch.servingSource || current.servingSource,
    servingUpdatedAt: patch.servingUpdatedAt || current.servingUpdatedAt,
    generatedAt: patch.generatedAt || current.generatedAt,
    focusStatus: patch.focusStatus || current.focusStatus,
    cacheLayers: patch.cacheLayers || current.cacheLayers,
  };
}

function WorldMonitorApp() {
  const { locale, setLocale, t, formatDateTime, formatNumber, formatPercent, formatRelativeTime } = useI18n();
  const [bootstrap, setBootstrap] = useState<BootstrapPayload | null>(null);
  const [markets, setMarkets] = useState<MarketListItem[]>([]);
  const [marketGroups, setMarketGroups] = useState<MarketGroupItem[]>([]);
  const [marketGroupSort, setMarketGroupSort] = useState<MarketGroupSort>(() => readMarketGroupSortStorage());
  const [marketCatalogRefreshing, setMarketCatalogRefreshing] = useState(false);
  const [marketCatalogError, setMarketCatalogError] = useState<string | null>(null);
  const [selectedMarketGroupId, setSelectedMarketGroupId] = useState<string | null>(null);
  const [selectedMarketGroupOutcomeKey, setSelectedMarketGroupOutcomeKey] = useState<string | null>(null);
  const [selectedMarketGroupDetail, setSelectedMarketGroupDetail] = useState<MarketGroupDetail | null>(null);
  const [selectedMarketGroupChart, setSelectedMarketGroupChart] = useState<MarketGroupChartPayload | null>(null);
  const [selectedMarketGroupChartRange, setSelectedMarketGroupChartRange] = useState<MarketGroupChartRange>('1d');
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [bundle, setBundle] = useState<WorkspaceBundle | null>(null);
  const [selectedMarketId, setSelectedMarketId] = useState<number | null>(null);
  const [globalTrades, setGlobalTrades] = useState<TradeRow[]>([]);
  const [globalOracle, setGlobalOracle] = useState<OracleEvent[]>([]);
  const [latestContent, setLatestContent] = useState<ContentItem[]>([]);
  const [activePanelIds, setActivePanelIds] = useState<string[]>([]);
  const {
    runtimeData,
    setRuntimeData,
    getStatus: getPanelRuntimeStatus,
    refreshPanels,
    refreshTier,
  } = usePanelRuntime({
    panels: RUNTIME_PANEL_MODULES,
    activePanelIds,
    initialData: readGeoShockRuntimeSeed(),
  });
  const [marketQuery] = useState('');
  const [commandQuery, setCommandQuery] = useState('');
  const [commandTab, setCommandTab] = useState<CommandPaletteTab>('markets');
  const [commandActiveMarketId, setCommandActiveMarketId] = useState<number | null>(null);
  const [commandMarketHits, setCommandMarketHits] = useState<MarketListItem[]>([]);
  const [commandMarketSearchLoading, setCommandMarketSearchLoading] = useState(false);
  const [commandMarketSearchError, setCommandMarketSearchError] = useState('');
  const worldEventMap = useWorldEventMapState();
  const naturalHazards = useNaturalHazards(
    worldEventMap.state.zoom,
    [worldEventMap.state.center.lon, worldEventMap.state.center.lat],
  );
  const airRoutesRequested = worldEventMap.state.activeLayerIds.includes('air-routes');
  const aviationViewport = useAviationViewport(
    airRoutesRequested,
    [worldEventMap.state.center.lon, worldEventMap.state.center.lat],
    worldEventMap.state.zoom,
  );
  const layers = useMemo<LayerToggle[]>(() => {
    const statuses = new Map(naturalHazards.sources.map((source) => [source.key, source]));
    return INITIAL_LAYERS.map((layer) => {
      const relevant = layer.sourceKeys.map((key) => statuses.get(key)).filter(Boolean);
      const required = layer.requiredSources.map((key) => statuses.get(key)).filter(Boolean);
      const requiredUnavailable = required.length > 0 && required.some((source) => (
        source?.status === 'error' && source.eventCount === 0
      ));
      const aviationDegraded = layer.id === 'air-routes'
        && Boolean(aviationViewport.error || aviationViewport.payload?.status === 'unavailable');
      const degraded = aviationDegraded
        || relevant.some((source) => source?.status === 'error' || source?.status === 'degraded');
      const reasons = relevant
        .filter((source) => source?.status === 'error' || source?.status === 'degraded')
        .map((source) => `${source?.label || source?.key}: ${source?.message || source?.status}`);
      if (requiredUnavailable) {
        reasons.unshift('A required authoritative source is unavailable; this layer cannot make its declared claim.');
      }
      if (aviationDegraded) {
        reasons.push(
          aviationViewport.error
            || aviationViewport.payload?.limitations?.join(' · ')
            || aviationViewport.payload?.errorCode
            || 'Live viewport aircraft are unavailable; reference routes remain usable.',
        );
      }
      return {
        ...layer,
        enabled: worldEventMap.state.activeLayerIds.includes(layer.id),
        availability: layer.availability === 'unavailable' || requiredUnavailable
          ? 'unavailable'
          : degraded ? 'degraded' : 'ready',
        availabilityReason: reasons.join(' · ') || layer.availabilityReason,
      };
    });
  }, [
    aviationViewport.error,
    aviationViewport.payload,
    naturalHazards.sources,
    worldEventMap.state.activeLayerIds,
  ]);
  const region = worldEventMap.state.region;
  const mapZoom = worldEventMap.state.zoom;
  const setRegion = (nextRegion: RegionKey) => worldEventMap.setRegion(nextRegion);
  const setMapZoom = (nextZoom: number | ((current: number) => number)) => {
    const value = typeof nextZoom === 'function' ? nextZoom(worldEventMap.state.zoom) : nextZoom;
    worldEventMap.setZoom(clampMapZoom(value));
  };
  const [panelLayoutPrefs, setPanelLayoutPrefs] = useState<PanelLayoutPrefs>(() => readJsonStorage<PanelLayoutPrefs>(PANEL_LAYOUT_STORAGE_KEY, {}));
  const [panelPrefsLoaded, setPanelPrefsLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [bundleLoading, setBundleLoading] = useState(false);
  const [viewMode, setViewMode] = useState<MapViewMode>(() => {
    const override = readSearchParam('view');
    if (isMapViewMode(override)) return override;
    return DEFAULT_MAP_VIEW_MODE;
  });
  const [globeStatus, setGlobeStatus] = useState<WorldGlobeStatusMetrics>({
    fps: 0,
    markerTotal: 0,
    markerVisible: 0,
    qualitySetting: 'auto',
    qualityLevel: 'high',
    dpr: 1,
  });
  const geoShockHydratingRef = useRef(false);
  const [showPanelLibrary, setShowPanelLibrary] = useState<boolean>(() => {
    const stored = readJsonStorage<boolean | null>(LIBRARY_STORAGE_KEY, null);
    if (stored !== null) return stored;
    return typeof window === 'undefined' || !window.matchMedia('(max-width: 720px)').matches;
  });
  const [showCommandPalette, setShowCommandPalette] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [manualCopyLink, setManualCopyLink] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [workspaceSyncStatus, setWorkspaceSyncStatus] = useState<WorkspaceSyncStatus>('checking');
  const [workspaceSyncUpdatedAt, setWorkspaceSyncUpdatedAt] = useState<string | null>(null);
  const [workspaceSyncEpoch, setWorkspaceSyncEpoch] = useState(0);
  const [selectedWeatherCityId, setSelectedWeatherCityId] = useState<string | null>(null);
  const bootstrapRef = useRef<BootstrapPayload | null>(null);
  const selectedMarketIdRef = useRef<number | null>(null);
  const selectedMarketGroupIdRef = useRef<string | null>(null);
  const marketGroupSortRef = useRef<MarketGroupSort>(marketGroupSort);
  const bundleRequestSeqRef = useRef(0);
  const bundleCacheRef = useRef<Map<number, WorkspaceBundle>>(new Map());
  const bundleFocusUpdatedAtRef = useRef<Map<number, number>>(new Map());
  const focusTileInflightRef = useRef<Map<number, Promise<WorkspaceBundle>>>(new Map());
  const workspaceSyncRevisionRef = useRef(0);
  const workspaceSyncReadyRef = useRef(false);
  const workspaceSyncApplyingRef = useRef(false);
  const workspaceSyncSnapshotRef = useRef('');
  const workspaceLocalChangeHydratedRef = useRef(false);
  const manualCopyInputRef = useRef<HTMLInputElement | null>(null);

  const loadMarketFocusTile = useCallback((marketId: number, force = false): Promise<WorkspaceBundle> => {
    const normalizedMarketId = Number(marketId);
    const cachedBundle = bundleCacheRef.current.get(normalizedMarketId);
    const cachedAt = bundleFocusUpdatedAtRef.current.get(normalizedMarketId) || 0;
    if (!force && cachedBundle && Date.now() - cachedAt <= MARKET_FOCUS_BROWSER_CACHE_MS) {
      return Promise.resolve(cachedBundle);
    }
    const inFlight = focusTileInflightRef.current.get(normalizedMarketId);
    if (inFlight) return inFlight;

    const request = fetchMarketFocusTile(normalizedMarketId, 2500)
      .then((loadedBundle) => {
        if (!bundleMatchesMarket(loadedBundle, normalizedMarketId)) return loadedBundle;
        const current = bundleCacheRef.current.get(normalizedMarketId) || emptyWorkspaceBundle();
        const merged = mergeWorkspaceBundle(current, loadedBundle);
        bundleCacheRef.current.set(normalizedMarketId, merged);
        bundleFocusUpdatedAtRef.current.set(normalizedMarketId, Date.now());
        return merged;
      })
      .finally(() => {
        if (focusTileInflightRef.current.get(normalizedMarketId) === request) {
          focusTileInflightRef.current.delete(normalizedMarketId);
        }
      });
    focusTileInflightRef.current.set(normalizedMarketId, request);
    return request;
  }, []);

  const prefetchMarketFocus = useCallback((marketIds: number[]) => {
    const uniqueMarketIds = [...new Set(
      marketIds
        .map((marketId) => Number(marketId))
        .filter((marketId) => Number.isFinite(marketId) && marketId > 0),
    )].slice(0, 3);
    uniqueMarketIds.forEach((marketId) => {
      void loadMarketFocusTile(marketId).catch(() => undefined);
    });
  }, [loadMarketFocusTile]);

  const focusMarketGroup = (group: MarketGroupItem, outcomeKey?: string | null, marketId?: number | null) => {
    const eventId = group.eventId != null ? String(group.eventId) : null;
    const selectedOutcome = pickDefaultGroupOutcome(group, outcomeKey, marketId);
    const nextMarketId = selectedOutcome?.marketId != null ? Number(selectedOutcome.marketId) : (marketId != null ? Number(marketId) : null);
    const nextOutcomeKey = selectedOutcome?.outcomeKey || outcomeKeyForGroupMarket(group, nextMarketId, outcomeKey);
    selectedMarketGroupIdRef.current = eventId;
    selectedMarketIdRef.current = nextMarketId;
    if (nextMarketId != null) {
      const optimisticBundle = optimisticBundleFromGroup(group, nextMarketId, nextOutcomeKey);
      const cachedFocusBundle = bundleCacheRef.current.get(nextMarketId);
      const hydratedBundle = cachedFocusBundle
        ? mergeWorkspaceBundle(cachedFocusBundle, optimisticBundle)
        : optimisticBundle;
      bundleCacheRef.current.set(nextMarketId, hydratedBundle);
      setBundle(hydratedBundle);
      setBundleLoading(false);
      setSelectedMarketGroupDetail(hydratedBundle.group || null);
    }
    setSelectedMarketGroupId(eventId);
    setSelectedMarketGroupOutcomeKey(nextOutcomeKey);
    setSelectedMarketId(nextMarketId);
  };

  useEffect(() => {
    selectedMarketIdRef.current = selectedMarketId;
  }, [selectedMarketId]);

  useEffect(() => {
    selectedMarketGroupIdRef.current = selectedMarketGroupId;
  }, [selectedMarketGroupId]);

  async function refreshFastRuntimePanels(options: RuntimePanelRefreshOptions = {}): Promise<{ marketsPayload: MarketsPayload | null; marketGroupsPayload: MarketGroupsPayload | null }> {
    const bootstrapPayload = options.bootstrapPayload || bootstrapRef.current;
    const settled = await Promise.allSettled([
      fetchSystemHealth(),
      fetchRecentTrades(24),
      fetchRecentOracle(16),
      fetchLatestContent(12),
      fetchMarketGroups('', FAST_MARKETS_PAGE_SIZE, 'active'),
      fetchAllActiveMarkets('', FAST_MARKETS_PAGE_SIZE),
      refreshTier('fast', { panelIds: options.activePanelIds, reason: bootstrapPayload ? 'bootstrap' : 'refresh' }),
    ]);

    const fallbackMarkets = bootstrapPayload?.activeMarketsPreview || [];
    if (settled[0].status === 'fulfilled') setHealth(settled[0].value);
    else if (bootstrapPayload?.systemHealth) setHealth(bootstrapPayload.systemHealth);

    if (settled[1].status === 'fulfilled') setGlobalTrades(settled[1].value);
    else if (bootstrapPayload?.globalTradesPreview) setGlobalTrades(bootstrapPayload.globalTradesPreview);

    if (settled[2].status === 'fulfilled') setGlobalOracle(settled[2].value);
    else if (bootstrapPayload?.globalOraclePreview) setGlobalOracle(bootstrapPayload.globalOraclePreview);

    if (settled[3].status === 'fulfilled') setLatestContent(settled[3].value.items || []);
    else if (bootstrapPayload?.latestContentPreview) setLatestContent(bootstrapPayload.latestContentPreview);

    if (settled[4].status === 'fulfilled') setMarketGroups(settled[4].value.items || []);
    if (settled[4].status === 'fulfilled') setMarketCatalogError(null);
    else setMarketCatalogError(settled[4].reason instanceof Error ? settled[4].reason.message : 'Market catalog refresh failed.');

    if (settled[5].status === 'fulfilled') setMarkets(settled[5].value.items || []);
    else if (fallbackMarkets.length) setMarkets(fallbackMarkets);

    const fastRuntimeResult = settled[6];
    if (fastRuntimeResult.status === 'fulfilled') {
      setRuntimeData((current) => mergeRuntimeData(current, fastRuntimeResult.value));
    } else if (bootstrapPayload?.commoditiesPreview) {
      setRuntimeData((current) => mergeRuntimeData(current, { 'commodities-watch': bootstrapPayload.commoditiesPreview }));
    }
    return {
      marketsPayload: settled[5].status === 'fulfilled' ? settled[5].value : null,
      marketGroupsPayload: settled[4].status === 'fulfilled' ? settled[4].value : null,
    };
  }

  async function refreshRuntimePanels(options: RuntimePanelRefreshOptions = {}) {
    const fastResult = await refreshFastRuntimePanels(options);
    void refreshTier('slow', {
      panelIds: options.activePanelIds,
      reason: options.bootstrapPayload ? 'bootstrap' : 'refresh',
    });
    return fastResult;
  }

  async function refreshMarketCatalog() {
    if (marketCatalogRefreshing) return;
    setMarketCatalogRefreshing(true);
    setMarketCatalogError(null);
    try {
      const [groupsResult, marketsResult] = await Promise.allSettled([
        fetchMarketGroups('', FAST_MARKETS_PAGE_SIZE, 'active'),
        fetchAllActiveMarkets('', FAST_MARKETS_PAGE_SIZE),
      ]);
      if (groupsResult.status === 'fulfilled') {
        setMarketGroups(groupsResult.value.items || []);
        setMarketCatalogError(null);
      }
      if (marketsResult.status === 'fulfilled') setMarkets(marketsResult.value.items || []);
      if (groupsResult.status === 'rejected') {
        throw groupsResult.reason instanceof Error ? groupsResult.reason : new Error('Market catalog refresh failed.');
      }
    } catch (refreshError) {
      setMarketCatalogError(refreshError instanceof Error ? refreshError.message : 'Market catalog refresh failed.');
    } finally {
      setMarketCatalogRefreshing(false);
    }
  }

  useEffect(() => {
    const savedPanelIds = sanitizePanelIds(readJsonStorage<string[]>(PANEL_STORAGE_KEY, []));
    setActivePanelIds(savedPanelIds.length ? sanitizePanelIds([...savedPanelIds, ...DEFAULT_PANEL_IDS]) : DEFAULT_PANEL_IDS);
    setPanelPrefsLoaded(true);
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (window.localStorage.getItem(PANEL_LAYOUT_PROMOTION_STORAGE_KEY) === 'live-evidence-wide') return;
    setPanelLayoutPrefs((current) => {
      let changed = false;
      const next = { ...current };
      for (const panelId of PROMOTED_WIDE_PANEL_IDS) {
        const entry = next[panelId] || {};
        if ((entry.colSpan || 0) >= 2) continue;
        next[panelId] = { ...entry, colSpan: 2 };
        changed = true;
      }
      return changed ? next : current;
    });
    window.localStorage.setItem(PANEL_LAYOUT_PROMOTION_STORAGE_KEY, 'live-evidence-wide');
  }, []);

  useEffect(() => {
    if (!panelPrefsLoaded || typeof window === 'undefined') return;
    window.localStorage.setItem(PANEL_STORAGE_KEY, JSON.stringify(activePanelIds));
  }, [activePanelIds, panelPrefsLoaded]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(PANEL_LAYOUT_STORAGE_KEY, JSON.stringify(panelLayoutPrefs));
  }, [panelLayoutPrefs]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(VIEW_STORAGE_KEY, viewMode);
  }, [viewMode]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(LIBRARY_STORAGE_KEY, JSON.stringify(showPanelLibrary));
  }, [showPanelLibrary]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(MARKET_GROUP_SORT_STORAGE_KEY, marketGroupSort);
  }, [marketGroupSort]);

  const workspaceSyncValue = useMemo(
    () => ({
      activePanelIds,
      panelLayout: panelLayoutPrefs,
      preferences: {
        region,
        viewMode,
        mapZoom,
        showPanelLibrary,
        marketGroupSort,
      },
    }),
    [activePanelIds, mapZoom, marketGroupSort, panelLayoutPrefs, region, showPanelLibrary, viewMode],
  );
  const workspaceSyncSnapshot = useMemo(() => JSON.stringify(workspaceSyncValue), [workspaceSyncValue]);

  const applySyncedWorkspace = (layout: WorkspaceLayout) => {
    workspaceSyncApplyingRef.current = true;
    setActivePanelIds(sanitizePanelIds(layout.activePanelIds));
    const validPanels = new Set(PANEL_LIBRARY.map((panel) => panel.id));
    setPanelLayoutPrefs(
      Object.fromEntries(Object.entries(layout.panelLayout || {}).filter(([panelId]) => validPanels.has(panelId))),
    );
    const preferences = layout.preferences || {};
    // A shared map camera is authoritative even when it does not include a
    // named region. Applying the workspace's saved region after hydration
    // otherwise moves the map away from the URL center/zoom and breaks
    // reproducible links, cluster picking and screenshot baselines.
    if (!readSearchParam('region')
      && !readSearchParam('center')
      && !readSearchParam('zoom')
      && REGION_OPTIONS.some((option) => option.value === preferences.region)) {
      setRegion(preferences.region as RegionKey);
    }
    if (!readSearchParam('view') && isMapViewMode(preferences.viewMode || '')) {
      setViewMode(preferences.viewMode as MapViewMode);
    }
    if (!readSearchParam('zoom') && !readSearchParam('center') && preferences.mapZoom != null) {
      setMapZoom(clampMapZoom(preferences.mapZoom));
    }
    if (typeof preferences.showPanelLibrary === 'boolean') setShowPanelLibrary(preferences.showPanelLibrary);
    if (['active', 'new', 'volume', 'close', 'move', 'trades'].includes(preferences.marketGroupSort || '')) {
      setMarketGroupSort(preferences.marketGroupSort as MarketGroupSort);
    }
    workspaceSyncRevisionRef.current = layout.revision;
    setWorkspaceSyncUpdatedAt(layout.updatedAt);
    window.setTimeout(() => {
      workspaceSyncApplyingRef.current = false;
    }, 0);
  };

  useEffect(() => {
    if (!panelPrefsLoaded || typeof window === 'undefined') return;
    let cancelled = false;
    workspaceSyncReadyRef.current = false;
    setWorkspaceSyncStatus('checking');
    const synchronize = async () => {
      const session = await fetchAuthSession();
      if (cancelled) return;
      if (!session.authenticated || session.user?.forcePasswordChange) {
        setWorkspaceSyncStatus('local');
        return;
      }
      const server = await fetchWorkspaceLayout();
      if (cancelled) return;
      const localMeta = readJsonStorage<{ updatedAt?: string }>(WORKSPACE_SYNC_META_KEY, {});
      const localTimestamp = Date.parse(localMeta.updatedAt || '');
      const serverTimestamp = Date.parse(server.clientUpdatedAt || '');
      const localIsNewer = Number.isFinite(localTimestamp)
        && (!Number.isFinite(serverTimestamp) || localTimestamp > serverTimestamp);
      if (!server.exists || localIsNewer) {
        const clientUpdatedAt = localMeta.updatedAt || new Date().toISOString();
        const saved = await saveWorkspaceLayout({
          revision: server.revision,
          ...workspaceSyncValue,
          clientUpdatedAt,
        });
        if (cancelled) return;
        workspaceSyncRevisionRef.current = saved.revision;
        workspaceSyncSnapshotRef.current = workspaceSyncSnapshot;
        setWorkspaceSyncUpdatedAt(saved.updatedAt);
      } else {
        applySyncedWorkspace(server);
      }
      workspaceSyncReadyRef.current = true;
      setWorkspaceSyncStatus('synced');
    };
    synchronize().catch((caught) => {
      if (cancelled) return;
      setWorkspaceSyncStatus(caught instanceof AuthApiError && caught.status === 409 ? 'conflict' : 'error');
    });
    return () => {
      cancelled = true;
    };
  // The epoch is an explicit retry. Current workspace values are captured after local hydration.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panelPrefsLoaded, workspaceSyncEpoch]);

  useEffect(() => {
    if (!panelPrefsLoaded || typeof window === 'undefined') return;
    if (!workspaceLocalChangeHydratedRef.current) {
      workspaceLocalChangeHydratedRef.current = true;
      return;
    }
    if (workspaceSyncApplyingRef.current) return;
    window.localStorage.setItem(WORKSPACE_SYNC_META_KEY, JSON.stringify({ updatedAt: new Date().toISOString() }));
  }, [panelPrefsLoaded, workspaceSyncSnapshot]);

  useEffect(() => {
    if (!workspaceSyncReadyRef.current) return;
    if (workspaceSyncApplyingRef.current) {
      workspaceSyncApplyingRef.current = false;
      workspaceSyncSnapshotRef.current = workspaceSyncSnapshot;
      return;
    }
    if (workspaceSyncSnapshotRef.current === workspaceSyncSnapshot) return;
    setWorkspaceSyncStatus('saving');
    const timer = window.setTimeout(() => {
      const localMeta = readJsonStorage<{ updatedAt?: string }>(WORKSPACE_SYNC_META_KEY, {});
      const clientUpdatedAt = localMeta.updatedAt || new Date().toISOString();
      saveWorkspaceLayout({
        revision: workspaceSyncRevisionRef.current,
        ...workspaceSyncValue,
        clientUpdatedAt,
      }).then((saved) => {
        workspaceSyncRevisionRef.current = saved.revision;
        workspaceSyncSnapshotRef.current = workspaceSyncSnapshot;
        setWorkspaceSyncUpdatedAt(saved.updatedAt);
        setWorkspaceSyncStatus('synced');
      }).catch((caught) => {
        setWorkspaceSyncStatus(caught instanceof AuthApiError && caught.status === 409 ? 'conflict' : 'error');
        workspaceSyncReadyRef.current = false;
      });
    }, 900);
    return () => window.clearTimeout(timer);
  }, [workspaceSyncSnapshot, workspaceSyncValue]);

  useEffect(() => {
    writeGeoShockRuntimeSeed(runtimeData['geo-sanctions-shock'] as RuntimeGeoSanctionsShockPayload | undefined);
  }, [runtimeData]);

  useEffect(() => {
    const geoShockEnabled = layers.some(
      (layer) => (layer.id === 'ucdp' || layer.id === 'sanctions-country-risk') && layer.enabled,
    );
    const geoShockPayload = runtimeData['geo-sanctions-shock'] as RuntimeGeoSanctionsShockPayload | undefined;
    if (!geoShockEnabled || hasRenderableGeoShockPayload(geoShockPayload) || geoShockHydratingRef.current) return;
    geoShockHydratingRef.current = true;
    let cancelled = false;
    void fetchRuntimeGeoSanctionsShock(2000)
      .then((payload) => {
        if (cancelled || !hasRenderableGeoShockPayload(payload)) return;
        setRuntimeData((current) => mergeRuntimeData(current, { 'geo-sanctions-shock': payload }));
      })
      .catch(() => undefined)
      .finally(() => {
        geoShockHydratingRef.current = false;
      });
    return () => {
      cancelled = true;
    };
  }, [layers, runtimeData]);

  useEffect(() => {
    const intelEnabled = layers.some((layer) => layer.id === 'intel-hotspots' && layer.enabled);
    if (!intelEnabled || runtimeData['breaking-event-radar'] !== undefined) return;
    void refreshPanels(RUNTIME_PANEL_MODULES, {
      panelIds: ['breaking-event-radar'],
      reason: 'refresh',
      force: true,
    });
  }, [layers, refreshPanels, runtimeData]);

  useEffect(() => {
    bootstrapRef.current = bootstrap;
  }, [bootstrap]);

  useEffect(() => {
    marketGroupSortRef.current = marketGroupSort;
  }, [marketGroupSort]);

  useEffect(() => {
    if (selectedMarketId == null) {
      if (!selectedMarketGroupId) {
        setSelectedMarketGroupId(null);
        setSelectedMarketGroupOutcomeKey(null);
        setSelectedMarketGroupDetail(null);
        setSelectedMarketGroupChart(null);
      }
      return;
    }
    const matchedGroup = findGroupForMarketId(marketGroups, selectedMarketId);
    if (!matchedGroup) {
      if (selectedMarketGroupId || selectedMarketGroupDetail || selectedMarketGroupChart || selectedMarketGroupOutcomeKey) {
        setSelectedMarketGroupId(null);
        setSelectedMarketGroupOutcomeKey(null);
        setSelectedMarketGroupDetail(null);
        setSelectedMarketGroupChart(null);
      }
      return;
    }
    const nextEventId = matchedGroup.eventId != null ? String(matchedGroup.eventId) : null;
    const matchedOutcome = (matchedGroup.outcomes || []).find((outcome) => Number(outcome.marketId) === selectedMarketId) || null;
    if (nextEventId && nextEventId !== selectedMarketGroupId) {
      selectedMarketGroupIdRef.current = nextEventId;
      setSelectedMarketGroupId(nextEventId);
      setSelectedMarketGroupDetail(null);
      setSelectedMarketGroupChart(null);
    }
    const nextOutcomeKey = matchedOutcome?.outcomeKey || matchedGroup.defaultOutcomeKey || null;
    if (nextOutcomeKey && nextOutcomeKey !== selectedMarketGroupOutcomeKey) {
      setSelectedMarketGroupOutcomeKey(nextOutcomeKey);
    }
  }, [
    marketGroups,
    selectedMarketGroupChart,
    selectedMarketGroupDetail,
    selectedMarketGroupId,
    selectedMarketGroupOutcomeKey,
    selectedMarketId,
  ]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const bootstrapPayload = await fetchBootstrap();
        if (cancelled) return;

        const defaultPanelIds = defaultWorkspacePanelIds(bootstrapPayload);
        const immediatePanelIds = activePanelIds.length ? sanitizePanelIds([...activePanelIds, ...defaultPanelIds]) : defaultPanelIds;
        const bootstrapMarketGroups = bootstrapPayload.activeMarketGroupsPreview || [];
        const initialDefaultGroup = pickDefaultMarketGroup(bootstrapMarketGroups);
        const initialDefaultMarketId = initialDefaultGroup ? null : pickDefaultMarketId(
          bootstrapPayload.activeMarketsPreview || [],
          bootstrapPayload.featuredMarket,
        );

        setBootstrap(bootstrapPayload);
        setMarkets(bootstrapPayload.activeMarketsPreview || []);
        setMarketGroups(bootstrapMarketGroups);
        setHealth(bootstrapPayload.systemHealth || null);
        setGlobalTrades(bootstrapPayload.globalTradesPreview || []);
        setGlobalOracle(bootstrapPayload.globalOraclePreview || []);
        setLatestContent(bootstrapPayload.latestContentPreview || []);
        setRuntimeData((current) => mergeRuntimeData(current, bootstrapPayload.commoditiesPreview ? { 'commodities-watch': bootstrapPayload.commoditiesPreview } : {}));
        selectedMarketIdRef.current = initialDefaultMarketId;
        if (initialDefaultGroup) {
          focusMarketGroup(initialDefaultGroup, initialDefaultGroup.defaultOutcomeKey || null, initialDefaultGroup.defaultMarketId ?? null);
        } else {
          selectedMarketGroupIdRef.current = null;
          setSelectedMarketGroupId(null);
          setSelectedMarketGroupOutcomeKey(null);
          setSelectedMarketId(initialDefaultMarketId);
        }
        setActivePanelIds((current) => (
          current.length
            ? sanitizePanelIds([...current, ...defaultPanelIds])
            : defaultPanelIds
        ));
        setLoading(false);

        const focusFirstGroupIfInitial = (groups: MarketGroupItem[]) => {
          const selectionStillInitial = !selectedMarketGroupIdRef.current && selectedMarketIdRef.current === initialDefaultMarketId;
          if (!selectionStillInitial) return false;
          const firstGroup = pickDefaultMarketGroup(groups);
          if (!firstGroup) return false;
          focusMarketGroup(firstGroup, firstGroup.defaultOutcomeKey || null, firstGroup.defaultMarketId ?? null);
          return true;
        };

        void refreshRuntimePanels({ bootstrapPayload, activePanelIds: immediatePanelIds })
          .then(({ marketsPayload, marketGroupsPayload }) => {
            if (cancelled) return;
            if (focusFirstGroupIfInitial(marketGroupsPayload?.items || [])) {
              return;
            }
            const selectionStillInitial = !selectedMarketGroupIdRef.current && selectedMarketIdRef.current === initialDefaultMarketId;
            if (!selectionStillInitial) return;
            const marketItems = marketsPayload?.items || bootstrapPayload.activeMarketsPreview || [];
            const nextMarketId = pickDefaultMarketId(marketItems, bootstrapPayload.featuredMarket);
            selectedMarketIdRef.current = nextMarketId;
            setSelectedMarketId(nextMarketId);
          })
          .catch((loadError) => {
            if (!cancelled) {
              setError((previous) => previous || (loadError instanceof Error ? loadError.message : 'Failed to refresh global workspace data.'));
            }
          });
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : 'Failed to load dashboard.');
          setLoading(false);
          void refreshRuntimePanels().catch(() => {
            // Runtime panels can still hydrate from seed snapshots when bootstrap is temporarily unavailable.
          });
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [panelPrefsLoaded]);

  useEffect(() => {
    let cancelled = false;

    async function refreshGlobalPanels() {
      try {
        await refreshRuntimePanels();
        if (cancelled) return;
      } catch (loadError) {
        if (!cancelled) {
          setError((previous) => previous || (loadError instanceof Error ? loadError.message : 'Failed to refresh snapshots.'));
        }
      }
    }

    const timer = window.setInterval(() => {
      void refreshGlobalPanels();
    }, 20000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!marketQuery.trim()) {
      let cancelled = false;
      void fetchMarketGroups('', FAST_MARKETS_PAGE_SIZE, 'active')
        .then((payload) => {
          if (!cancelled) {
            setMarketGroups(payload.items || []);
            setMarketCatalogError(null);
          }
        })
        .catch((refreshError) => {
          if (!cancelled) setMarketCatalogError(refreshError instanceof Error ? refreshError.message : 'Market catalog refresh failed.');
          // Keep the last group list visible when the event feed has a transient miss.
        });
      return () => {
        cancelled = true;
      };
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const [groupsResult, marketsResult] = await Promise.allSettled([
          fetchMarketGroups(marketQuery.trim(), FAST_MARKETS_PAGE_SIZE, 'active'),
          fetchAllActiveMarkets(marketQuery.trim(), SEARCH_MARKETS_PAGE_SIZE),
        ]);
        if (!cancelled && groupsResult.status === 'fulfilled') setMarketGroups(groupsResult.value.items || []);
        if (!cancelled && marketsResult.status === 'fulfilled') setMarkets(marketsResult.value.items || []);
      } catch (loadError) {
        if (!cancelled) {
          setError((previous) => previous || (loadError instanceof Error ? loadError.message : 'Failed to refresh market search.'));
        }
      }
    }, 220);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [marketGroupSort, marketQuery]);

  const selectedFocusDetailReady = Boolean(
    selectedMarketId
      && bundleMatchesMarket(bundle, selectedMarketId)
      && bundle?.focusStatus === 'ready'
      && bundle.group
      && (bundle.group.outcomes || []).length,
  );
  const selectedFocusChartReady = Boolean(
    selectedMarketId
      && bundleMatchesMarket(bundle, selectedMarketId)
      && (bundle?.chart?.points || []).length > 2
      && !['missing', 'snapshot', 'warming'].includes(String(bundle?.chart?.historyStatus || '').toLowerCase()),
  );

  useEffect(() => {
    if (!selectedMarketGroupId) {
      setSelectedMarketGroupDetail(null);
      return;
    }
    if (selectedFocusDetailReady) return;
    let cancelled = false;
    const controller = new AbortController();
    const eventId = selectedMarketGroupId;

    const timer = window.setTimeout(() => {
      fetchMarketGroupDetail(eventId, 3000, controller.signal)
        .then((detailPayload) => {
          if (cancelled || selectedMarketGroupIdRef.current !== eventId) return;
          setSelectedMarketGroupDetail(detailPayload);
          const liveDetailOutcome = pickDefaultGroupOutcome(detailPayload, selectedMarketGroupOutcomeKey, selectedMarketIdRef.current);
          if (liveDetailOutcome?.marketId != null && Number(liveDetailOutcome.marketId) !== selectedMarketIdRef.current) {
            selectedMarketIdRef.current = Number(liveDetailOutcome.marketId);
            setSelectedMarketId(Number(liveDetailOutcome.marketId));
          }
          setSelectedMarketGroupOutcomeKey(liveDetailOutcome?.outcomeKey || detailPayload.defaultOutcomeKey || null);
        })
        .catch(() => {
          // Keep the optimistic/focus-tile detail visible on a transient detail miss.
        });
    }, 2500);

    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [selectedFocusDetailReady, selectedMarketGroupId]);

  useEffect(() => {
    if (!selectedMarketGroupId) {
      setSelectedMarketGroupChart(null);
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    let timer: number | undefined;
    const eventId = selectedMarketGroupId;
    const chartRange = selectedMarketGroupChartRange;
    setSelectedMarketGroupChart(null);
    if (chartRange === '1d' && selectedFocusChartReady) return;

    timer = window.setTimeout(() => {
      fetchMarketGroupChart(eventId, chartRange, 3500, controller.signal)
        .then((chartPayload) => {
          if (!cancelled) setSelectedMarketGroupChart(chartPayload);
        })
        .catch(() => {
          if (!cancelled) setSelectedMarketGroupChart(null);
        });
    }, chartRange === '1d' ? 2500 : 150);

    return () => {
      cancelled = true;
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [selectedFocusChartReady, selectedMarketGroupChartRange, selectedMarketGroupId]);

  useEffect(() => {
    if (!selectedMarketId) return;
    if (selectedMarketGroupChartRange === '1d') return;
    let cancelled = false;
    const controller = new AbortController();
    const currentMarketId = selectedMarketId;
    const chartRange = selectedMarketGroupChartRange;

    fetchMarketChart(currentMarketId, chartRange, undefined, 12000, controller.signal)
      .then((chartPayload) => {
        if (cancelled) return;
        setBundle((previous) => {
          const base = previous || bundleCacheRef.current.get(currentMarketId) || emptyWorkspaceBundle();
          const next = mergeWorkspaceBundle(base, { ...emptyWorkspaceBundle(), chart: chartPayload });
          bundleCacheRef.current.set(currentMarketId, next);
          return next;
        });
      })
      .catch(() => undefined);

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [selectedMarketGroupChartRange, selectedMarketId]);

  useEffect(() => {
    if (!selectedMarketId) return;
    const currentMarketId = selectedMarketId;
    const requestSeq = ++bundleRequestSeqRef.current;
    let cancelled = false;
    const controller = new AbortController();
    const cachedBundle = bundleCacheRef.current.get(currentMarketId);
    const listMarket = markets.find((market) => market.id === currentMarketId)
      || bootstrapRef.current?.activeMarketsPreview?.find((market) => market.id === currentMarketId)
      || null;
    const listGroup = marketGroups.find((group) => {
      if (selectedMarketGroupId && String(group.eventId ?? '') === selectedMarketGroupId) return true;
      return [...(group.outcomes || []), ...(group.topOutcomes || [])].some((outcome) => Number(outcome.marketId) === currentMarketId);
    }) || null;
    const initialBundle = cachedBundle
      || (listMarket ? optimisticBundleFromMarket(listMarket) : null)
      || (listGroup ? optimisticBundleFromGroup(listGroup, currentMarketId, selectedMarketGroupOutcomeKey) : null)
      || emptyWorkspaceBundle();
    setBundle(initialBundle);
    setBundleLoading(!cachedBundle && !listMarket && !listGroup);
    if (!cachedBundle) {
      bundleCacheRef.current.set(currentMarketId, initialBundle);
    }

    function applyLoadedBundle(loadedBundle: WorkspaceBundle) {
      if (cancelled || bundleRequestSeqRef.current !== requestSeq) return;
      if (!bundleMatchesMarket(loadedBundle, currentMarketId)) return;
      const loadedGroup = loadedBundle.group || null;
      const loadedEventId = loadedGroup?.eventId ?? loadedBundle.identity?.eventId ?? null;
      if (loadedGroup && loadedEventId != null) {
        const eventId = String(loadedEventId);
        selectedMarketGroupIdRef.current = eventId;
        setSelectedMarketGroupId(eventId);
        setSelectedMarketGroupDetail(loadedGroup);
        const liveLoadedOutcome = pickDefaultGroupOutcome(
          loadedGroup,
          loadedBundle.selectedOutcome?.outcomeKey || loadedBundle.identity?.selectedOutcomeKey || loadedGroup.defaultOutcomeKey || null,
          currentMarketId,
        );
        const nextOutcomeKey = liveLoadedOutcome?.outcomeKey
          || loadedBundle.selectedOutcome?.outcomeKey
          || loadedBundle.identity?.selectedOutcomeKey
          || outcomeKeyForGroupMarket(loadedGroup, currentMarketId, loadedGroup.defaultOutcomeKey || null);
        if (nextOutcomeKey) {
          setSelectedMarketGroupOutcomeKey(nextOutcomeKey);
        }
      }
      setBundle((previous) => {
        const base = previous || bundleCacheRef.current.get(currentMarketId) || initialBundle;
        const next = mergeWorkspaceBundle(base, loadedBundle);
        bundleCacheRef.current.set(currentMarketId, next);
        return next;
      });
    }

    const loadFocusTile = (force = false) => loadMarketFocusTile(currentMarketId, force)
      .then((loadedBundle) => applyLoadedBundle(loadedBundle))
      .catch((loadError) => {
        if (!cancelled && bundleRequestSeqRef.current === requestSeq && !listMarket && !listGroup && !cachedBundle) {
          setError(loadError instanceof Error ? loadError.message : 'Failed to load market.');
        }
      })
      .finally(() => {
        if (!cancelled && bundleRequestSeqRef.current === requestSeq) {
          setBundleLoading(false);
        }
      });

    void loadFocusTile();
    const workspaceTimer = window.setTimeout(() => {
      if (cancelled || bundleRequestSeqRef.current !== requestSeq) return;
      fetchWorkspaceBundle(currentMarketId, { signal: controller.signal })
        .then((loadedBundle) => applyLoadedBundle(loadedBundle))
        .catch(() => undefined);
    }, 6500);
    const timer = window.setInterval(() => {
      if (cancelled || bundleRequestSeqRef.current !== requestSeq || document.visibilityState === 'hidden') return;
      void loadFocusTile(true);
    }, 20000);

    const loadingTimer = window.setTimeout(() => {
      if (!cancelled && bundleRequestSeqRef.current === requestSeq) {
        setBundleLoading(false);
      }
    }, 4500);

    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
      window.clearTimeout(workspaceTimer);
      window.clearTimeout(loadingTimer);
    };
  }, [loadMarketFocusTile, selectedMarketId]);

  useEffect(() => {
    if (!selectedMarketId || Number(bundle?.market?.id) === Number(selectedMarketId)) return;
    const selectedGroup = findGroupForMarketId(marketGroups, selectedMarketId);
    const selectedListMarket = markets.find((market) => Number(market.id) === Number(selectedMarketId)) || null;
    const optimistic = selectedGroup
      ? optimisticBundleFromGroup(selectedGroup, selectedMarketId, selectedMarketGroupOutcomeKey)
      : selectedListMarket
        ? optimisticBundleFromMarket(selectedListMarket)
        : null;
    if (!optimistic) return;
    setBundle((previous) => {
      const next = mergeWorkspaceBundle(previous, optimistic);
      bundleCacheRef.current.set(selectedMarketId, next);
      return next;
    });
  }, [bundle?.market?.id, marketGroups, markets, selectedMarketGroupOutcomeKey, selectedMarketId]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setShowCommandPalette(true);
      }
      if (event.key === 'Escape') {
        setShowCommandPalette(false);
        setShowSettings(false);
        setManualCopyLink(null);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 2200);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const toggleLayer = (layerId: string) => {
    const target = layers.find((layer) => layer.id === layerId);
    if (!target?.isExecutable || target.availability === 'unavailable') {
      setNotice(target?.availabilityReason || 'This layer is not available in the current runtime.');
      return;
    }
    if (target) {
      const label = localizedLayerLabel(target, t);
      setNotice(t(target.enabled ? 'atlas.hideLayer' : 'atlas.showLayer', { layer: label }));
    }
    worldEventMap.toggleLayer(layerId);
  };

  const togglePanel = (panelId: string) => {
    setActivePanelIds((current) => {
      if (current.includes(panelId)) return current.filter((candidate) => candidate !== panelId);
      return [...current, panelId];
    });
  };

  const moveWorkspacePanel = (draggedPanelId: string, targetPanelId: string, insertAfter: boolean) => {
    setActivePanelIds((current) => {
      const movablePanelIds = current.filter((panelId) => !MAP_BOTTOM_PANEL_IDS.includes(panelId));
      if (!movablePanelIds.includes(draggedPanelId) || !movablePanelIds.includes(targetPanelId)) return current;
      const nextMovablePanelIds = reorderPanelIds(movablePanelIds, draggedPanelId, targetPanelId, insertAfter);
      if (nextMovablePanelIds === movablePanelIds) return current;
      const movablePanelSet = new Set(movablePanelIds);
      let nextIndex = 0;
      return current.map((panelId) => (movablePanelSet.has(panelId) ? (nextMovablePanelIds[nextIndex++] || panelId) : panelId));
    });
  };

  const resizeWorkspacePanel = (panelId: string, patch: { rowSpan?: number; colSpan?: number }) => {
    setPanelLayoutPrefs((current) => {
      const entry = current[panelId] || {};
      return {
        ...current,
        [panelId]: {
          ...entry,
          ...patch,
        },
      };
    });
  };

  const resetWorkspacePanelLayout = (panelId: string) => {
    setPanelLayoutPrefs((current) => {
      if (!current[panelId]) return current;
      const next = { ...current };
      delete next[panelId];
      return next;
    });
  };

  const availableMarkets = useMemo(
    () => (markets.length ? markets : (bootstrap?.activeMarketsPreview || [])),
    [bootstrap?.activeMarketsPreview, markets],
  );

  const filteredMarkets = useMemo(() => {
    const query = commandQuery.trim().toLowerCase();
    if (!query) return availableMarkets;
    return availableMarkets.filter((market) => {
      const text = `${market.title} ${market.slug} ${market.category || ''} ${(market.tags || []).join(' ')}`.toLowerCase();
      return text.includes(query);
    });
  }, [availableMarkets, commandQuery]);

  const selectedMarket = useMemo<MarketSummary | null>(() => {
    if (selectedMarketGroupId && selectedMarketId == null) return null;
    if (bundle?.market && bundle.market.id === selectedMarketId) return bundle.market;
    const selectedListMarket = availableMarkets.find((market) => market.id === selectedMarketId);
    if (selectedListMarket) return selectedListMarket;
    if (bootstrap?.featuredMarket?.id === selectedMarketId) return bootstrap.featuredMarket;
    if (!selectedMarketGroupId && bootstrap?.featuredMarket && !isSuppressedDefaultMarket(bootstrap.featuredMarket)) {
      return bootstrap.featuredMarket;
    }
    return null;
  }, [availableMarkets, bootstrap?.featuredMarket, bundle?.market, selectedMarketGroupId, selectedMarketId]);

  const selectedMarketGroup = useMemo<MarketGroupItem | null>(() => {
    if (!selectedMarketGroupId) return null;
    return marketGroups.find((group) => String(group.eventId ?? '') === selectedMarketGroupId) || null;
  }, [marketGroups, selectedMarketGroupId]);

  const currentGlobalTrades = globalTrades.length ? globalTrades : (bootstrap?.globalTradesPreview || []);
  const currentGlobalOracle = globalOracle.length ? globalOracle : (bootstrap?.globalOraclePreview || []);
  const currentLatestContent = latestContent.length ? latestContent : (bootstrap?.latestContentPreview || []);
  const displayMarkets = filteredMarkets.length ? filteredMarkets : availableMarkets;
  const displayPanelIds = activePanelIds.length
    ? activePanelIds
    : defaultWorkspacePanelIds(bootstrap);
  const mapBottomPanelIds = displayPanelIds.filter((panelId) => MAP_BOTTOM_PANEL_IDS.includes(panelId));
  const sidePanelIds = displayPanelIds.filter((panelId) => !MAP_BOTTOM_PANEL_IDS.includes(panelId));
  const activeMarketsEntry = PANEL_REGISTRY['active-markets'];
  const oracleFeedEntry = PANEL_REGISTRY['oracle-feed'];
  const remainingSidePanelIds = sidePanelIds.filter((panelId) => !FOCUSED_STRIP_PANEL_IDS.has(panelId));

  const liveMetrics = [
    { label: 'ACTIVE MARKETS', value: displayMarkets.length || availableMarkets.length || 0 },
    { label: 'ORDERFILLED', value: currentGlobalTrades.length || 0 },
    { label: 'ORACLE', value: currentGlobalOracle.length || 0 },
    { label: 'INTEL', value: currentLatestContent.length || 0 },
  ];
  const enabledLayerIds = useMemo(() => layers.filter((layer) => layer.enabled).map((layer) => layer.id), [layers]);
  const geoShockPayload = runtimeData['geo-sanctions-shock'] as RuntimeGeoSanctionsShockPayload | undefined;
  const ucdpLayerEnabled = enabledLayerIds.includes('ucdp');
  const intelLayerEnabled = enabledLayerIds.includes('intel-hotspots');
  const countryRiskLayerEnabled = enabledLayerIds.includes('sanctions-country-risk');
  const countryGeometry = useCountryGeometry(
    intelLayerEnabled || countryRiskLayerEnabled || Boolean(worldEventMap.state.countryCode),
  );
  const breakingEventPayload = runtimeData['breaking-event-radar'] as RuntimeBreakingEventRadarPayload | undefined;
  const ucdpRawMapEvents = useMemo(
    () => (ucdpLayerEnabled ? (geoShockPayload?.items || []).filter(hasGeoConflictCoordinates) : []),
    [geoShockPayload, ucdpLayerEnabled],
  );
  const geoShockAdapterResult = useMemo(() => adaptGeoShockPayload(geoShockPayload), [geoShockPayload]);
  const intelAdapterResult = useMemo(
    () => adaptBreakingEventMapPayload(breakingEventPayload, countryGeometry.index),
    [breakingEventPayload, countryGeometry.index],
  );
  const countryRiskAdapterResult = useMemo(
    () => adaptGeoShockCountryRiskPayload(geoShockPayload, countryGeometry.index),
    [countryGeometry.index, geoShockPayload],
  );
  const worldEventFilterState = useMemo(() => ({
    activeLayerIds: worldEventMap.state.activeLayerIds,
    timeRange: worldEventMap.state.timeRange,
    severities: worldEventMap.state.severities,
    countryCode: worldEventMap.state.countryCode,
  }), [
    worldEventMap.state.activeLayerIds,
    worldEventMap.state.severities,
    worldEventMap.state.timeRange,
    worldEventMap.state.countryCode,
  ]);
  const hazardMapEvents = useMemo(
    () => filterWorldEventMapEventsForLayers(
      naturalHazards.events,
      worldEventFilterState,
      Date.now(),
      countryGeometry.index,
    ),
    [countryGeometry.index, naturalHazards.events, worldEventFilterState],
  );
  const ucdpMapEvents = useMemo(
    () => ucdpLayerEnabled
      ? filterWorldEventMapEvents(
        geoShockAdapterResult.events.filter((event) => event.geometry?.type === 'Point'),
        worldEventFilterState,
        Date.now(),
        countryGeometry.index,
      )
      : [],
    [countryGeometry.index, geoShockAdapterResult, ucdpLayerEnabled, worldEventFilterState],
  );
  const intelMapEvents = useMemo(
    () => intelLayerEnabled
      ? filterWorldEventMapEvents(
        intelAdapterResult.events,
        worldEventFilterState,
        Date.now(),
        countryGeometry.index,
      )
      : [],
    [countryGeometry.index, intelAdapterResult.events, intelLayerEnabled, worldEventFilterState],
  );
  const countryRiskMapEvents = useMemo(
    () => countryRiskLayerEnabled
      ? filterWorldEventMapEvents(
        countryRiskAdapterResult.events,
        worldEventFilterState,
        Date.now(),
        countryGeometry.index,
      )
      : [],
    [countryGeometry.index, countryRiskAdapterResult.events, countryRiskLayerEnabled, worldEventFilterState],
  );
  const showAirRoutes = airRoutesRequested;
  const transportPayload = runtimeData['global-transport-shipping'] as RuntimeGlobalTransportShippingPayload | undefined;
  const mapTransportPayload = useMemo<RuntimeGlobalTransportShippingPayload | undefined>(() => {
    if (!showAirRoutes) return transportPayload;
    if (!transportPayload && !aviationViewport.payload) return undefined;
    return {
      ...(transportPayload || { items: [] }),
      status: transportPayload?.status || aviationViewport.payload?.status || 'loading',
      aviation: {
        ...transportPayload?.aviation,
        generatedAt: aviationViewport.payload?.generatedAt || transportPayload?.aviation?.generatedAt,
        liveFlights: aviationViewport.payload?.aircraft || [],
      },
    };
  }, [aviationViewport.payload, showAirRoutes, transportPayload]);
  const airReferenceAdapterResult = useMemo(
    () => showAirRoutes
      ? adaptTransportReference(mapTransportPayload)
      : { events: [], rejected: [] },
    [showAirRoutes, mapTransportPayload],
  );
  const airReferenceEvents = airReferenceAdapterResult.events;
  const worldEventMapEvents = useMemo(
    () => [
      ...hazardMapEvents,
      ...intelMapEvents,
      ...ucdpMapEvents,
      ...countryRiskMapEvents,
      ...airReferenceEvents,
    ],
    [airReferenceEvents, countryRiskMapEvents, hazardMapEvents, intelMapEvents, ucdpMapEvents],
  );
  const mapSourceStatuses = useMemo(() => {
    const activeSourceKeys = new Set(
      enabledLayerIds.flatMap((layerId) => worldEventLayerById(layerId)?.sourceKeys || []),
    );
    const statuses = naturalHazards.sources.filter((source) => activeSourceKeys.has(source.key));
    if (ucdpLayerEnabled) {
      statuses.push(sourceStatusFromAdapter({
        key: 'geo-sanctions-shock',
        label: 'UCDP',
        payloadStatus: geoShockPayload?.conflictState || geoShockPayload?.status,
        generatedAt: geoShockPayload?.generatedAt,
        result: geoShockAdapterResult,
        loaded: Boolean(geoShockPayload),
      }));
    }
    if (intelLayerEnabled) {
      const runtimeStatus = getPanelRuntimeStatus('breaking-event-radar');
      const status = sourceStatusFromAdapter({
        key: 'breaking-event-radar',
        label: 'INTEL',
        payloadStatus: countryGeometry.error
          ? 'error'
          : breakingEventPayload?.status || runtimeStatus.phase,
        generatedAt: breakingEventPayload?.generatedAt,
        result: intelAdapterResult,
        loaded: Boolean(countryGeometry.error)
          || (Boolean(breakingEventPayload) && Boolean(countryGeometry.index)),
      });
      if (countryGeometry.error) status.message = countryGeometry.error;
      statuses.push(status);
    }
    if (countryRiskLayerEnabled) {
      const runtimeStatus = getPanelRuntimeStatus('geo-sanctions-shock');
      const status = sourceStatusFromAdapter({
        key: 'geo-sanctions-shock-risk',
        label: 'COUNTRY RISK',
        payloadStatus: countryGeometry.error
          ? 'error'
          : geoShockPayload?.status || runtimeStatus.phase,
        generatedAt: geoShockPayload?.generatedAt,
        result: countryRiskAdapterResult,
        loaded: Boolean(countryGeometry.error)
          || (Boolean(geoShockPayload) && Boolean(countryGeometry.index)),
      });
      if (countryGeometry.error) status.message = countryGeometry.error;
      statuses.push(status);
    }
    if (showAirRoutes) {
      const runtimeStatus = getPanelRuntimeStatus('global-transport-shipping');
      const status = sourceStatusFromAdapter({
        key: 'global-transport-shipping',
        label: 'AVIATION',
        payloadStatus: aviationViewport.error
          ? (aviationViewport.payload || transportPayload ? 'degraded' : 'error')
          : aviationViewport.payload?.status === 'unavailable'
            ? 'degraded'
            : transportPayload?.status || aviationViewport.payload?.status || runtimeStatus.phase,
        generatedAt: aviationViewport.payload?.generatedAt
          || transportPayload?.aviation?.generatedAt
          || transportPayload?.generatedAt,
        result: airReferenceAdapterResult,
        loaded: Boolean(transportPayload || aviationViewport.payload || aviationViewport.error),
      });
      if (aviationViewport.error) {
        status.message = aviationViewport.payload || transportPayload
          ? `Viewport refresh failed; retaining the last successful aviation snapshot: ${aviationViewport.error}`
          : aviationViewport.error;
      }
      else if (aviationViewport.payload?.limitations?.length) {
        status.message = aviationViewport.payload.limitations.join(' · ');
      }
      statuses.push(status);
    }
    return statuses;
  }, [
    breakingEventPayload,
    countryGeometry.error,
    countryGeometry.index,
    countryRiskAdapterResult,
    countryRiskLayerEnabled,
    enabledLayerIds,
    getPanelRuntimeStatus,
    geoShockAdapterResult,
    geoShockPayload,
    intelAdapterResult,
    intelLayerEnabled,
    naturalHazards.sources,
    showAirRoutes,
    aviationViewport.error,
    aviationViewport.payload,
    transportPayload,
    airReferenceAdapterResult,
    ucdpLayerEnabled,
  ]);
  const mapVisibleEventCount = viewMode === '3d' ? globeStatus.markerVisible : worldEventMapEvents.length;
  const mapQualityLabel = viewMode === '3d'
    ? `${globeStatus.qualitySetting.toUpperCase()} · ${globeStatus.fps ? Math.round(globeStatus.fps) : '--'} FPS`
    : `${t(MAP_VIEW_MESSAGE_KEYS[viewMode])} · Z${mapZoom.toFixed(2)}`;

  const runtimeValue = <T,>(panelId: string): T | null => (runtimeData[panelId] as T | undefined) || null;
  const runtimePayloadLoaded = (panelId: string) => runtimeData[panelId] !== undefined && runtimeData[panelId] !== null;
  const panelShouldShowLoading = (panelId: string) => {
    if (loading && !bootstrap) return true;
    if (getPanelRuntimeStatus(panelId).phase === 'loading' && !runtimePayloadLoaded(panelId)) return true;
    return false;
  };
  const retryRuntimePanel = (panelId: string) => {
    const panel = PANEL_REGISTRY[panelId];
    if (panel?.fetchData) {
      void refreshPanels([panel], { panelIds: [panelId], reason: 'manual', force: true });
    }
  };

  const panelContext: PanelRenderContext = {
    bootstrap,
    markets: displayMarkets,
    marketGroups,
    marketGroupSort,
    setMarketGroupSort,
    marketCatalogRefreshing,
    marketCatalogError,
    refreshMarketCatalog,
    selectedMarketId,
    setSelectedMarketId,
    prefetchMarketFocus,
    focusMarketGroup,
    selectedMarketGroupId,
    selectedMarketGroup,
    selectedMarketGroupOutcomeKey,
    setSelectedMarketGroupOutcomeKey,
    selectedMarketGroupDetail,
    selectedMarketGroupChart,
    selectedMarketGroupChartRange,
    setSelectedMarketGroupChartRange,
    selectedMarket,
    selectedWeatherCityId,
    setSelectedWeatherCityId,
    bundle,
    health,
    globalTrades: currentGlobalTrades,
    globalOracle: currentGlobalOracle,
    latestContent: currentLatestContent,
    runtimeData,
    commodities: runtimeValue<RuntimeMarketGroup>('commodities-watch'),
    crypto: runtimeValue<RuntimeMarketGroup>('crypto-watch'),
    f1: runtimeValue<RuntimeF1Payload>('f1-trackside'),
    jin10: runtimeValue<RuntimeJin10Payload>('jin10-flash'),
    nba: runtimeValue<RuntimeNbaPayload>('nba-scoreboard'),
    nbaIntel: runtimeValue<RuntimeNbaIntelPayload>('nba-intel'),
    nbaMatchupPredictor: runtimeValue<RuntimeNbaMatchupPredictorPayload>('espn-matchup-predictor'),
    inflationNowcast: runtimeValue<RuntimeInflationNowcastPayload>('inflation-nowcast'),
    alphaSignals: runtimeValue<RuntimeSignalPayload>('alpha-signal'),
    whaleTrades: runtimeValue<RuntimeSignalPayload>('whale-tracker'),
    suspiciousTrades: runtimeValue<RuntimeSignalPayload>('suspicious-flow'),
  };

  useEffect(() => {
    const query = commandQuery.trim();
    if (!showCommandPalette || !query) {
      setCommandMarketHits([]);
      setCommandMarketSearchLoading(false);
      setCommandMarketSearchError('');
      return;
    }

    let cancelled = false;
    const timer = window.setTimeout(() => {
      setCommandMarketSearchLoading(true);
      setCommandMarketSearchError('');
      fetchMarketSearch(query, 50)
        .then((payload) => {
          if (!cancelled) {
            setCommandMarketHits(payload.items || []);
            setCommandMarketSearchError('');
          }
        })
        .catch(() => {
          if (!cancelled) {
            setCommandMarketHits([]);
            setCommandMarketSearchError(t('atlas.commandSearchUnavailable'));
          }
        })
        .finally(() => {
          if (!cancelled) setCommandMarketSearchLoading(false);
        });
    }, 180);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [commandQuery, showCommandPalette, t]);

  const commandResults = useMemo(() => {
    const query = commandQuery.trim().toLowerCase();
    const panelHits = PANEL_LIBRARY.filter((panel) => {
      const meta = localizedPanelMeta(panel, t);
      const text = `${meta.title} ${meta.description} ${panel.title} ${panel.description} ${panel.eyebrow} ${panel.id} ${panel.size || 'default'}`.toLowerCase();
      return !query || text.includes(query);
    });
    const localMarketHits = availableMarkets.filter((market) => {
      const text = `${market.title} ${market.category || ''} ${market.slug}`.toLowerCase();
      return !query || text.includes(query);
    }).slice(0, 30);
    const marketHits = query && commandMarketHits.length ? commandMarketHits : localMarketHits;
    return { panelHits, marketHits };
  }, [availableMarkets, commandMarketHits, commandQuery, t]);

  const commandPanelStats = useMemo(() => ({
    enabled: PANEL_LIBRARY.filter((panel) => displayPanelIds.includes(panel.id)).length,
    matching: commandResults.panelHits.length,
    total: PANEL_LIBRARY.length,
  }), [commandResults.panelHits.length, displayPanelIds]);

  useEffect(() => {
    if (!showCommandPalette || commandTab !== 'markets') return;
    setCommandActiveMarketId((current) => {
      if (current != null && commandResults.marketHits.some((market) => market.id === current)) return current;
      return commandResults.marketHits[0]?.id ?? null;
    });
  }, [commandResults.marketHits, commandTab, showCommandPalette]);

  const commandActiveMarket = useMemo(() => (
    commandResults.marketHits.find((market) => market.id === commandActiveMarketId)
    || commandResults.marketHits[0]
    || null
  ), [commandActiveMarketId, commandResults.marketHits]);

  const handleCommandKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      setShowCommandPalette(false);
      return;
    }
    if (commandTab !== 'markets' || !commandResults.marketHits.length) return;
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp' && event.key !== 'Enter') return;
    event.preventDefault();
    if (event.key === 'Enter') {
      if (commandActiveMarket) focusCommandMarket(commandActiveMarket);
      return;
    }
    const currentIndex = Math.max(0, commandResults.marketHits.findIndex((market) => market.id === commandActiveMarketId));
    const direction = event.key === 'ArrowDown' ? 1 : -1;
    const nextIndex = (currentIndex + direction + commandResults.marketHits.length) % commandResults.marketHits.length;
    setCommandActiveMarketId(commandResults.marketHits[nextIndex]?.id ?? null);
  };

  const resetWorkspace = () => {
    worldEventMap.reset();
    setViewMode(DEFAULT_MAP_VIEW_MODE);
    const firstGroup = pickDefaultMarketGroup(marketGroups);
    if (firstGroup) {
      focusMarketGroup(firstGroup, firstGroup.defaultOutcomeKey || null, firstGroup.defaultMarketId ?? null);
    } else {
      const nextMarketId = pickDefaultMarketId(availableMarkets, bootstrap?.featuredMarket);
      selectedMarketGroupIdRef.current = null;
      selectedMarketIdRef.current = nextMarketId;
      setSelectedMarketGroupId(null);
      setSelectedMarketGroupOutcomeKey(null);
      setSelectedMarketId(nextMarketId);
    }
    setNotice(t('atlas.workspaceReset'));
  };

  const resetMap = () => {
    worldEventMap.reset();
    setNotice(t('atlas.workspaceReset'));
  };

  const copyLink = async () => {
    const writeClipboard = async (value: string) => {
      try {
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(value);
          return;
        }
      } catch {
        // Fall through to the selection-based copy path for restricted browsers.
      }
      const textarea = document.createElement('textarea');
      textarea.value = value;
      textarea.setAttribute('readonly', '');
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      textarea.style.pointerEvents = 'none';
      document.body.appendChild(textarea);
      textarea.select();
      const copied = document.execCommand('copy');
      textarea.remove();
      if (!copied) throw new Error('Clipboard copy was rejected');
    };
    const shareUrl = new URL(worldEventMap.shareUrl(window.location.href));
    shareUrl.searchParams.set('view', viewMode);
    const value = shareUrl.toString();
    try {
      await writeClipboard(value);
      setNotice(t('atlas.linkCopied'));
    } catch {
      setManualCopyLink(value);
      window.setTimeout(() => {
        manualCopyInputRef.current?.focus();
        manualCopyInputRef.current?.select();
      }, 0);
    }
  };

  const focusCommandMarket = (market: MarketListItem) => {
    selectedMarketGroupIdRef.current = null;
    selectedMarketIdRef.current = market.id;
    setSelectedMarketGroupId(null);
    setSelectedMarketGroupOutcomeKey(null);
    setSelectedMarketId(market.id);
    setShowCommandPalette(false);
    setNotice(t('atlas.focusedMarket', { title: `${market.title.slice(0, 72)}${market.title.length > 72 ? '...' : ''}` }));
    window.setTimeout(() => {
      document.querySelector('.wm-focused-market-row')?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    }, 0);
  };

  const focusRelatedMarket = (marketId: number) => {
    const group = findGroupForMarketId(marketGroups, marketId);
    if (group) {
      focusMarketGroup(group, outcomeKeyForGroupMarket(group, marketId), marketId);
    } else {
      selectedMarketGroupIdRef.current = null;
      selectedMarketIdRef.current = marketId;
      setSelectedMarketGroupId(null);
      setSelectedMarketGroupOutcomeKey(null);
      setSelectedMarketId(marketId);
    }
    setNotice(`Focused evidence-linked market ${marketId}.`);
    window.setTimeout(() => {
      document.querySelector('.wm-map-bottom-grid')?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    }, 0);
  };

  const changeViewMode = (nextMode: MapViewMode) => {
    setViewMode(nextMode);
    setNotice(t('atlas.viewEnabled', { view: t(MAP_VIEW_MESSAGE_KEYS[nextMode]) }));
  };

  const zoomIn = () => setMapZoom((current) => clampMapZoom(current + 1));
  const zoomOut = () => setMapZoom((current) => clampMapZoom(current - 1));
  const formatMarketPercent = (value?: string | number | null) => {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? formatPercent(numeric) : '--';
  };
  const formatMarketCompact = (value?: string | number | null) => {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? formatNumber(numeric, { notation: 'compact', maximumFractionDigits: 1 }) : '--';
  };
  const formatMarketCurrency = (value?: string | number | null) => {
    const numeric = Number(value);
    return Number.isFinite(numeric)
      ? formatNumber(numeric, { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 })
      : '--';
  };

  return (
    <AppShell
      regionValue={region}
      regionOptions={REGION_OPTIONS.map((option) => ({
        value: option.value,
        label: t(REGION_MESSAGE_KEYS[option.value]),
      }))}
      orderFilledCount={liveMetrics[1]?.value || 0}
      onRegionChange={(nextRegion) => {
        if (REGION_OPTIONS.some((option) => option.value === nextRegion)) setRegion(nextRegion as RegionKey);
      }}
      onResetWorkspace={resetWorkspace}
      onOpenCommandPalette={() => setShowCommandPalette(true)}
      onTogglePanelLibrary={() => setShowPanelLibrary((current) => !current)}
      onOpenSettings={() => setShowSettings(true)}
      onCopyLink={() => void copyLink()}
    >

      <main className="wm-dashboard">
        <div className="wm-main-content">
        <section className="wm-map-section">
          <div className="wm-map-header">
            <div className="wm-map-heading">
              <span className="wm-map-kicker">{t('atlas.kicker')}</span>
              <div className="wm-map-title">{t('atlas.title')}</div>
            </div>
            <div className="wm-map-status-strip" aria-label={t('atlas.mapStatus')}>
              <span className="wm-status-chip">{t('atlas.liveStatus')}</span>
              <LiveUtcClock />
              <MapStatus sources={mapSourceStatuses} />
              <span className="wm-map-status-metric">{t('atlas.events')} <b>{formatNumber(worldEventMapEvents.length)}</b></span>
              <span className="wm-map-status-metric">{t('atlas.visible')} <b>{formatNumber(mapVisibleEventCount)}</b></span>
              <span className="wm-map-status-metric">{t('atlas.quality')} <b>{mapQualityLabel}</b></span>
            </div>
            <div className="wm-map-view-toggle" role="tablist" aria-label={t('atlas.viewMode')}>
              {MAP_VIEW_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  role="tab"
                  aria-selected={viewMode === option.value}
                  className={viewMode === option.value ? 'active' : ''}
                  onClick={() => changeViewMode(option.value)}
                >
                  {t(MAP_VIEW_MESSAGE_KEYS[option.value])}
                </button>
              ))}
            </div>
          </div>

          <MapToolbar
            state={worldEventMap.state}
            onTimeRangeChange={worldEventMap.setTimeRange}
            onSeveritiesChange={worldEventMap.setSeverities}
            onBasemapProviderChange={worldEventMap.setBasemapProvider}
            onBasemapThemeChange={worldEventMap.setBasemapTheme}
            onClearCountry={() => worldEventMap.setCountry(null)}
          />

          <div className="wm-map-stage">
            <div className={`wm-globe-area ${viewMode !== '3d' ? 'wm-globe-area-flat' : ''}`}>
              <LayerPanel
                items={layers}
                events={worldEventMapEvents}
                collapsed={!showPanelLibrary}
                onToggle={toggleLayer}
                onCollapse={() => setShowPanelLibrary(false)}
                onExpand={() => setShowPanelLibrary(true)}
              />

              <div className="wm-globe-hero">
                {viewMode === '3d' ? (
                  <WorldGlobe
                    markets={displayMarkets}
                    selectedMarket={selectedMarket}
                    recentTrades={currentGlobalTrades}
                    recentOracle={currentGlobalOracle}
                    contentItems={currentLatestContent}
                    ucdpEvents={ucdpRawMapEvents}
                    region={region}
                    zoomLevel={Math.min(4, mapZoom)}
                    enabledLayerIds={enabledLayerIds}
                    onMetricsChange={setGlobeStatus}
                  />
                ) : (
                  <WorldEventInlineMap
                    events={worldEventMapEvents}
                    state={worldEventMap.state}
                    onCameraChange={(nextCamera) => worldEventMap.setCamera(nextCamera.center, nextCamera.zoom)}
                    onEventSelect={worldEventMap.selectEvent}
                    onOpenMarket={focusRelatedMarket}
                    onAviationLensChange={worldEventMap.setAviationLens}
                    onAviationRiskSourceChange={worldEventMap.setAviationRiskSource}
                    onAviationClose={() => worldEventMap.toggleLayer('air-routes')}
                    onCountryChange={worldEventMap.setCountry}
                  />
                )}

              </div>

              <div className="wm-map-controls">
                <button type="button" className="wm-side-beta" onClick={() => setShowSettings(true)}>BETA</button>
                <button type="button" onClick={zoomIn}>＋</button>
                <button type="button" onClick={zoomOut}>－</button>
                <button type="button" onClick={resetMap}>⌂</button>
              </div>

              {loading ? <div className="wm-banner">{t('atlas.bootstrapping')}</div> : null}
              {bundleLoading ? <div className="wm-banner secondary">{t('atlas.switchingMarket')}</div> : null}
              {error ? <div className="wm-banner error">{error}</div> : null}
              {notice ? <div className="wm-banner notice">{notice}</div> : null}
            </div>
          </div>

          <div className="wm-map-bottom-grid">
            {mapBottomPanelIds.map((panelId) => {
              const entry = PANEL_REGISTRY[panelId];
              if (!entry) return null;
              const sizeClass = entry.size ? `size-${entry.size}` : '';
              return (
                <div className={`wm-panel-slot ${sizeClass}`.trim()} key={`bottom-${panelId}`}>
                  <PanelRuntimeBoundary
                    loading={panelShouldShowLoading(panelId)}
                    status={getPanelRuntimeStatus(panelId)}
                    onRetry={() => retryRuntimePanel(panelId)}
                  >
                    {entry.render(panelContext)}
                  </PanelRuntimeBoundary>
                </div>
              );
            })}
          </div>
        </section>

        <section className="wm-focused-market-row">
          {activeMarketsEntry ? (
            <PanelWorkspaceSlot
              panelId="active-markets"
              size={activeMarketsEntry.size}
              layoutPrefs={panelLayoutPrefs}
              className="wm-focused-market-list"
              layoutManaged={false}
              resizeEnabled={false}
              loading={panelShouldShowLoading('active-markets')}
              runtimeStatus={getPanelRuntimeStatus('active-markets')}
              onRetry={() => retryRuntimePanel('active-markets')}
              onMovePanel={moveWorkspacePanel}
              onResizePanel={resizeWorkspacePanel}
              onResetPanelLayout={resetWorkspacePanelLayout}
            >
              {activeMarketsEntry.render(panelContext)}
            </PanelWorkspaceSlot>
          ) : null}
          <div className="wm-focused-market-right">
            <FocusedMarketStrip
              {...panelContext}
              renderPanelSlot={(panelId, className, panel) => {
                const entry = PANEL_REGISTRY[panelId];
                return (
                  <PanelWorkspaceSlot
                    key={panelId}
                    panelId={panelId}
                    size={entry?.size}
                    layoutPrefs={panelLayoutPrefs}
                    className={className}
                    layoutManaged={false}
                    resizeEnabled={false}
                    loading={panelShouldShowLoading(panelId)}
                    runtimeStatus={getPanelRuntimeStatus(panelId)}
                    onRetry={() => retryRuntimePanel(panelId)}
                    onMovePanel={moveWorkspacePanel}
                    onResizePanel={resizeWorkspacePanel}
                    onResetPanelLayout={resetWorkspacePanelLayout}
                  >
                    {panel}
                  </PanelWorkspaceSlot>
                );
              }}
            />
          </div>
          {oracleFeedEntry ? (
            <PanelWorkspaceSlot
              panelId="oracle-feed"
              size={oracleFeedEntry.size}
              layoutPrefs={panelLayoutPrefs}
              className="wm-focused-oracle-feed"
              layoutManaged={false}
              resizeEnabled={false}
              loading={panelShouldShowLoading('oracle-feed')}
              runtimeStatus={getPanelRuntimeStatus('oracle-feed')}
              onRetry={() => retryRuntimePanel('oracle-feed')}
              onMovePanel={moveWorkspacePanel}
              onResizePanel={resizeWorkspacePanel}
              onResetPanelLayout={resetWorkspacePanelLayout}
            >
              {oracleFeedEntry.render(panelContext)}
            </PanelWorkspaceSlot>
          ) : null}
        </section>

        <section className="wm-panels-grid">
          {remainingSidePanelIds.map((panelId) => {
            const entry = PANEL_REGISTRY[panelId];
            if (!entry) return null;
            return (
              <PanelWorkspaceSlot
                key={panelId}
                panelId={panelId}
                size={entry.size}
                layoutPrefs={panelLayoutPrefs}
                loading={panelShouldShowLoading(panelId)}
                runtimeStatus={getPanelRuntimeStatus(panelId)}
                onRetry={() => retryRuntimePanel(panelId)}
                onMovePanel={moveWorkspacePanel}
                onResizePanel={resizeWorkspacePanel}
                onResetPanelLayout={resetWorkspacePanelLayout}
              >
                {entry.render(panelContext)}
              </PanelWorkspaceSlot>
            );
          })}
        </section>
        </div>
      </main>

      {showCommandPalette ? (
        <div className="wm-modal-backdrop" onClick={() => setShowCommandPalette(false)}>
          <div className="wm-modal wm-command-modal" onClick={(event) => event.stopPropagation()} onKeyDown={handleCommandKeyDown}>
            <div className="wm-command-header">
              <div>
                <span>{t('atlas.commandTitle')}</span>
                <strong>{t('atlas.commandSearchTitle')}</strong>
              </div>
              <div className="wm-command-source">
                <span>PostgreSQL</span>
                <span>ClickHouse TX</span>
                <span>Live Index</span>
              </div>
            </div>
            <div className="wm-command-searchbar">
              <span aria-hidden="true">⌕</span>
              <input
                autoFocus
                className="wm-command-input"
                value={commandQuery}
                onInput={(event) => setCommandQuery((event.currentTarget as HTMLInputElement).value)}
                placeholder={t('atlas.commandPlaceholder')}
              />
              <kbd>⌘K</kbd>
            </div>
            <div className="wm-command-tabs" role="tablist" aria-label={t('atlas.commandSections')}>
              <button type="button" className={commandTab === 'markets' ? 'active' : ''} onClick={() => setCommandTab('markets')}>{t('atlas.commandMarkets')} <span>{formatNumber(commandResults.marketHits.length)}</span></button>
              <button type="button" className={commandTab === 'panels' ? 'active' : ''} onClick={() => setCommandTab('panels')}>{t('atlas.commandPanels')} <span>{formatNumber(commandPanelStats.matching)}/{formatNumber(commandPanelStats.total)}</span></button>
              <button type="button" className={commandTab === 'commands' ? 'active' : ''} onClick={() => setCommandTab('commands')}>{t('atlas.commandCommands')} <span>{formatNumber(3)}</span></button>
            </div>
            <div className="wm-command-body">
              {commandTab === 'markets' ? (
                <div className="wm-command-market-layout">
                  <div className="wm-command-group wm-command-market-list">
                    <div className="wm-command-list-head">
                      <span>{t('atlas.commandMarket')}</span>
                      <span>YES</span>
                      <span>{t('atlas.commandVolume')}</span>
                      <span>{t('atlas.commandTransactions')}</span>
                      <span>{t('atlas.commandAge')}</span>
                    </div>
                    {commandMarketSearchLoading ? <div className="wm-command-empty">{t('atlas.commandSearching')}</div> : null}
                    {!commandMarketSearchLoading && commandMarketSearchError ? (
                      <div className="wm-command-empty error">{commandMarketSearchError}</div>
                    ) : null}
                    {commandResults.marketHits.map((market) => {
                      const active = commandActiveMarket?.id === market.id;
                      return (
                        <button
                          key={market.id}
                          type="button"
                          className={`wm-command-result wm-command-market-result ${active ? 'active' : ''}`}
                          onMouseEnter={() => setCommandActiveMarketId(market.id)}
                          onFocus={() => setCommandActiveMarketId(market.id)}
                          onClick={() => focusCommandMarket(market)}
                        >
                          <div className="wm-command-result-main">
                            <strong>{market.title}</strong>
                            <span>
                              <i className={`wm-command-status ${commandMarketStatusClass(market)}`}>{commandMarketStatus(market)}</i>
                              <em>{market.category || t('atlas.commandUncategorized')}</em>
                            </span>
                          </div>
                          <b>{formatMarketPercent(market.latestPrice)}</b>
                          <b>{formatMarketCurrency(market.volume24h)}</b>
                          <b>{formatMarketCompact(market.tradeCount24h)}</b>
                          <b>{formatRelativeTime(market.lastTradeAt || null)}</b>
                        </button>
                      );
                    })}
                    {!commandMarketSearchLoading && !commandMarketSearchError && commandQuery.trim() && !commandResults.marketHits.length ? (
                      <div className="wm-command-empty">{t('atlas.commandNoMarkets')}</div>
                    ) : null}
                  </div>
                  <aside className="wm-command-preview" aria-label={t('atlas.commandPreview')}>
                    {commandActiveMarket ? (
                      <>
                        <div className="wm-command-preview-top">
                          <span className={`wm-command-status ${commandMarketStatusClass(commandActiveMarket)}`}>{commandMarketStatus(commandActiveMarket)}</span>
                          <em>{commandActiveMarket.category || t('atlas.commandMarket')}</em>
                        </div>
                        <strong>{commandActiveMarket.title}</strong>
                        <div className="wm-command-preview-price">
                          <span><em>YES</em><b>{formatMarketPercent(commandActiveMarket.latestPrice)}</b></span>
                          <span><em>{t('atlasMarket.volume24h')}</em><b>{formatMarketCurrency(commandActiveMarket.volume24h)}</b></span>
                          <span><em>{t('atlasMarket.trades24h')}</em><b>{formatMarketCompact(commandActiveMarket.tradeCount24h)}</b></span>
                          <span><em>{t('atlas.commandLastTrade')}</em><b>{formatRelativeTime(commandActiveMarket.lastTradeAt || null)}</b></span>
                        </div>
                        <div className="wm-command-preview-meta">
                          <span><em>{t('atlas.commandMarketId')}</em><b>{formatNumber(commandActiveMarket.id)}</b></span>
                          <span><em>{t('atlas.commandOutcomes')}</em><b>{formatNumber(commandActiveMarket.outcomeCount || 2)}</b></span>
                          <span><em>{t('atlas.commandCloses')}</em><b>{commandActiveMarket.endDate ? formatDateTime(commandActiveMarket.endDate) : '--'}</b></span>
                        </div>
                        <div className="wm-command-preview-tags">
                          {(commandActiveMarket.tags || []).slice(0, 5).map((tag) => <span key={tag}>{tag}</span>)}
                        </div>
                        <a className="wm-command-primary" href={`/markets/${commandActiveMarket.id}`}>{t('atlas.commandOpenMarket')}</a>
                      </>
                    ) : (
                      <div className="wm-command-empty">{t('atlas.commandPreviewHelp')}</div>
                    )}
                  </aside>
                </div>
              ) : null}
              {commandTab === 'panels' ? (
                <div className="wm-command-panel-section">
                  <div className="wm-command-panel-summary">
                    <span>{t('atlas.commandShowingPanels', { matching: formatNumber(commandPanelStats.matching), total: formatNumber(commandPanelStats.total) })}</span>
                    <span>{t('atlas.commandEnabledCount', { count: formatNumber(commandPanelStats.enabled) })}</span>
                    {commandQuery.trim() ? <em>{t('atlas.commandFilteredBy', { query: commandQuery.trim() })}</em> : <em>{t('atlas.commandFullLibrary')}</em>}
                  </div>
                  <div className="wm-command-panel-grid">
                    {commandResults.panelHits.map((panel) => {
                      const meta = localizedPanelMeta(panel, t);
                      return (
                        <button
                          key={panel.id}
                          type="button"
                          className={`wm-command-result wm-command-panel-result ${displayPanelIds.includes(panel.id) ? 'enabled' : ''}`}
                          onClick={() => {
                            if (!displayPanelIds.includes(panel.id)) togglePanel(panel.id);
                            setShowCommandPalette(false);
                          }}
                        >
                          <div className="wm-command-panel-main">
                            <strong>{meta.title}</strong>
                            <small>{panel.id}</small>
                          </div>
                          <span>{meta.description}</span>
                          <div className="wm-command-panel-meta">
                            <i>{panel.eyebrow || t('atlas.commandPanel')}</i>
                            <i>{panel.size || t('atlas.commandDefaultSize')}</i>
                            <em>{displayPanelIds.includes(panel.id) ? t('atlas.commandEnabled') : t('atlas.commandAddPanel')}</em>
                          </div>
                        </button>
                      );
                    })}
                    {!commandResults.panelHits.length ? (
                      <div className="wm-command-empty">{t('atlas.commandNoPanels')}</div>
                    ) : null}
                  </div>
                </div>
              ) : null}
              {commandTab === 'commands' ? (
                <div className="wm-command-panel-grid wm-command-actions-grid">
                  <button type="button" className="wm-command-result wm-command-panel-result" onClick={() => {
                    resetWorkspace();
                    setShowCommandPalette(false);
                  }}>
                    <strong>{t('atlas.commandReset')}</strong>
                    <span>{t('atlas.commandResetDetail')}</span>
                    <em>{t('atlas.commandRun')}</em>
                  </button>
                  <button type="button" className="wm-command-result wm-command-panel-result" onClick={() => {
                    setShowCommandPalette(false);
                    setShowSettings(true);
                  }}>
                    <strong>{t('atlas.commandSettings')}</strong>
                    <span>{t('atlas.commandSettingsDetail')}</span>
                    <em>{t('atlas.commandOpen')}</em>
                  </button>
                  <button type="button" className="wm-command-result wm-command-panel-result" onClick={() => {
                    void copyLink();
                    setShowCommandPalette(false);
                  }}>
                    <strong>{t('atlas.commandCopyLink')}</strong>
                    <span>{t('atlas.commandCopyLinkDetail')}</span>
                    <em>{t('atlas.commandCopy')}</em>
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      {showSettings ? (
        <div className="wm-modal-backdrop" onClick={() => setShowSettings(false)}>
          <div className="wm-modal wm-settings-modal" onClick={(event) => event.stopPropagation()}>
            <div className="wm-modal-title">{t('settings.title')}</div>
            <label className="wm-settings-row">
              <span>{t('settings.language')}</span>
              <select value={locale} onChange={(event) => setLocale(event.currentTarget.value === 'zh' ? 'zh' : 'en')}>
                <option value="en">{t('language.english')}</option>
                <option value="zh">{t('language.chinese')}</option>
              </select>
            </label>
            <label className="wm-settings-row">
              <span>{t('settings.region')}</span>
              <select value={region} onChange={(event) => setRegion((event.currentTarget as HTMLSelectElement).value as RegionKey)}>
                {REGION_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{t(REGION_MESSAGE_KEYS[option.value])}</option>
                ))}
              </select>
            </label>
            <label className="wm-settings-row">
              <span>{t('settings.mapMode')}</span>
              <select value={viewMode} onChange={(event) => setViewMode((event.currentTarget as HTMLSelectElement).value as MapViewMode)}>
                {MAP_VIEW_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{t(MAP_VIEW_MESSAGE_KEYS[option.value])}</option>
                ))}
              </select>
            </label>
            <label className="wm-settings-row">
              <span>{t('settings.mapZoom')}</span>
              <input type="range" min="0.75" max="8" step="0.25" value={String(mapZoom)} onInput={(event) => setMapZoom(clampMapZoom((event.currentTarget as HTMLInputElement).value))} />
            </label>
            <section className={`wm-settings-sync is-${workspaceSyncStatus}`} aria-live="polite">
              <div>
                <span>{t('settings.cloud')}</span>
                <strong>
                  {workspaceSyncStatus === 'synced' ? t('settings.synced')
                    : workspaceSyncStatus === 'saving' ? t('settings.saving')
                    : workspaceSyncStatus === 'local' ? t('settings.local')
                    : workspaceSyncStatus === 'checking' ? t('settings.checking')
                    : workspaceSyncStatus === 'conflict' ? t('settings.conflict')
                    : t('settings.unavailable')}
                </strong>
                <small>
                  {workspaceSyncUpdatedAt
                    ? t('settings.serverObserved', { date: formatDateTime(workspaceSyncUpdatedAt) })
                    : t('settings.syncDescription')}
                </small>
              </div>
              <div>
                {workspaceSyncStatus === 'local' ? <a href="/login?next=/">{t('settings.signIn')}</a> : null}
                {workspaceSyncStatus === 'error' || workspaceSyncStatus === 'conflict'
                  ? <button type="button" onClick={() => {
                    if (workspaceSyncStatus === 'conflict') window.localStorage.removeItem(WORKSPACE_SYNC_META_KEY);
                    setWorkspaceSyncEpoch((current) => current + 1);
                  }}>{workspaceSyncStatus === 'conflict' ? t('settings.latestCloud') : t('settings.retry')}</button>
                  : null}
                <a href="/briefings">{t('settings.briefings')}</a>
              </div>
            </section>
            <div className="wm-settings-actions">
              <button type="button" className="wm-settings-btn" onClick={() => setActivePanelIds(sanitizePanelIds(PANEL_LIBRARY.map((panel) => panel.id)))}>{t('settings.enableAll')}</button>
              <button type="button" className="wm-settings-btn" onClick={() => setActivePanelIds(defaultWorkspacePanelIds(bootstrap))}>{t('settings.restore')}</button>
              <button type="button" className="wm-settings-btn primary" onClick={() => { resetWorkspace(); setShowSettings(false); }}>{t('settings.reset')}</button>
            </div>
          </div>
        </div>
      ) : null}

      {manualCopyLink ? (
        <div className="wm-modal-backdrop" onClick={() => setManualCopyLink(null)}>
          <div
            className="wm-modal wm-copy-link-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="wm-copy-link-title"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="wm-modal-title" id="wm-copy-link-title">{t('atlas.copyManualTitle')}</div>
            <p>{t('atlas.copyManualDetail')}</p>
            <input
              ref={manualCopyInputRef}
              type="text"
              readOnly
              value={manualCopyLink}
              onFocus={(event) => event.currentTarget.select()}
            />
            <div className="wm-settings-actions">
              <button type="button" className="wm-settings-btn" onClick={() => {
                manualCopyInputRef.current?.focus();
                manualCopyInputRef.current?.select();
              }}>{t('atlas.selectLink')}</button>
              <button type="button" className="wm-settings-btn primary" onClick={() => setManualCopyLink(null)}>{t('atlas.closeCopy')}</button>
            </div>
          </div>
        </div>
      ) : null}

    </AppShell>
  );
}

export function App() {
  const pathname = typeof window === 'undefined' ? '/' : window.location.pathname;
  if (pathname === '/login' || pathname.startsWith('/login/')) {
    return (
      <Suspense fallback={<PanelLoading label="Loading secure access" detail="Preparing administrator sign in" />}>
        <LoginWorkspace />
      </Suspense>
    );
  }
  if (pathname === '/account' || pathname.startsWith('/account/')) {
    return (
      <Suspense fallback={<PanelLoading label="Loading access control" detail="Reading session and credential registry" />}>
        <AccountWorkspace />
      </Suspense>
    );
  }
  if (/^\/briefings\/[A-Za-z0-9_-]{32}(?:\/|$)/.test(pathname)) {
    return (
      <Suspense fallback={<PanelLoading label="Loading briefing" detail="Opening canonical prediction-market snapshot" />}>
        <PublicBriefingWorkspace />
      </Suspense>
    );
  }
  if (pathname === '/briefings' || pathname === '/briefings/') {
    return (
      <Suspense fallback={<PanelLoading label="Loading briefings" detail="Reading revocable share registry" />}>
        <BriefingManagerWorkspace />
      </Suspense>
    );
  }
  if (pathname === '/developers' || pathname.startsWith('/developers/')) {
    return (
      <Suspense fallback={<PanelLoading label="Loading developer surface" detail="Reading MCP discovery and security contracts" />}>
        <DeveloperWorkspace />
      </Suspense>
    );
  }
  if (pathname === '/watchlist' || pathname.startsWith('/watchlist/')) {
    return (
      <Suspense fallback={<PanelLoading label="Loading Watchlist" detail="Reading tracked markets, Oracle rules and alert events" />}>
        <WatchlistWorkspace />
      </Suspense>
    );
  }
  if (pathname === '/data-quality' || pathname.startsWith('/data-quality/')) {
    return (
      <Suspense fallback={<PanelLoading label="Loading Data Quality workspace" detail="Auditing market identity, Oracle lifecycle and synchronization watermarks" />}>
        <DataQualityWorkspace />
      </Suspense>
    );
  }
  if (/^\/markets\/\d+(?:\/|$)/.test(pathname)) {
    return (
      <Suspense fallback={<PanelLoading label="Loading Market workspace" detail="Resolving market identity, probability and evidence sources" />}>
        <MarketWorkspace />
      </Suspense>
    );
  }
  return pathname === '/quant' || pathname.startsWith('/quant/')
    ? (
      <Suspense fallback={<PanelLoading label="Loading Quant workspace" detail="Opening chart, command palette and backtest tools" />}>
        <QuantWorkspace />
      </Suspense>
    )
    : <WorldMonitorApp />;
}
