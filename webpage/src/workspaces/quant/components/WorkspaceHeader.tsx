import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { fetchQuantEventPriceHead, isAbortLikeError } from '@/services/api';
import type { QuantMarketSeriesOutcome, QuantPriceMarket } from '@/types';
import type { BacktestEngine, DataStatus, PriceSource } from '../types';
import './WorkspaceHeader.css';

type WorkspaceHeaderProps = {
  marketSlug: string;
  marketQuery: string;
  timeframe: string;
  viewportMode?: 'preset' | 'custom';
  priceSource: PriceSource;
  backtestEngine: BacktestEngine;
  loading: boolean;
  marketOptions: QuantPriceMarket[];
  selectedMarket?: QuantPriceMarket;
  marketSearchStatus: DataStatus;
  onMarketSlugChange: (value: string) => void;
  onMarketQueryChange: (value: string) => void;
  onTimeframeChange: (value: string) => void;
  onPriceSourceChange: (value: PriceSource) => void;
  onBacktestEngineChange: (value: BacktestEngine) => void;
  onRunBacktest: () => void;
  onSave: () => void;
  onExport: (format: 'csv' | 'json') => void;
  onMarketPreview?: (slug: string) => void;
};

type SearchFilter = 'all' | 'events' | 'markets' | 'tokens' | 'ready' | 'active' | 'recent' | 'favorites' | 'official';
type SearchSort = 'relevance' | 'volume' | 'coverage' | 'outcomes' | 'updated';
type SearchResultKind = 'event' | 'market' | 'token';
type SearchPaletteMode = 'compact' | 'full';
type OutcomeDrilldownFilter = 'all' | 'ready' | 'partial' | 'none';
type EventOutcomeCacheEntry = {
  status: 'loading' | 'ready' | 'error';
  items: QuantPriceMarket[];
};

type SearchResult = {
  key: string;
  kind: SearchResultKind;
  market: QuantPriceMarket;
  title: string;
  slug: string;
  subtitle: string;
  coverage: string;
  price: string;
  status: 'ready' | 'partial' | 'stale' | 'none';
  confidence: string;
  count: string;
  rows: number;
  outcomes: number;
  volume: number;
  updated: number;
  priority: number;
  matchScore?: number;
  matchReason?: string;
};

const DEFAULT_QUANT_EVENT_SLUG = '2026-fifa-world-cup-winner-595';

const FILTERS: Array<{ value: SearchFilter; label: string }> = [
  { value: 'all', label: 'All' },
  { value: 'events', label: 'Events' },
  { value: 'markets', label: 'Markets' },
  { value: 'tokens', label: 'Tokens' },
  { value: 'ready', label: 'Ready' },
  { value: 'active', label: 'Active' },
  { value: 'recent', label: 'Recent' },
  { value: 'favorites', label: 'Favorites' },
  { value: 'official', label: 'Official only' },
];

const SORTS: Array<{ value: SearchSort; label: string }> = [
  { value: 'relevance', label: 'Relevance' },
  { value: 'volume', label: 'Volume' },
  { value: 'coverage', label: 'Coverage' },
  { value: 'outcomes', label: 'Outcomes' },
  { value: 'updated', label: 'Recently updated' },
];

const OUTCOME_DRILLDOWN_FILTERS: Array<{ value: OutcomeDrilldownFilter; label: string }> = [
  { value: 'all', label: 'All outcomes' },
  { value: 'ready', label: 'Ready' },
  { value: 'partial', label: 'Partial' },
  { value: 'none', label: 'No rows' },
];

function toNumber(value: unknown) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

function extraString(market: QuantPriceMarket, key: string) {
  const value = (market as unknown as Record<string, unknown>)[key];
  return value == null ? '' : String(value);
}

function isActiveMarket(market: QuantPriceMarket) {
  if (!market.endDate) return true;
  const time = Date.parse(String(market.endDate));
  return !Number.isFinite(time) || time > Date.now();
}

function rowsForMarket(market: QuantPriceMarket) {
  return toNumber(market.orderfilledRows) || toNumber(market.blockRows) || toNumber(market.frontendRows);
}

function coverageStatus(market: QuantPriceMarket): SearchResult['status'] {
  if (market.itemKind === 'event') {
    const ready = toNumber(market.readyMembers);
    const total = toNumber(market.totalMembers || market.outcomeCount);
    if (total > 0 && ready >= total) return 'ready';
    if (ready > 0) return 'partial';
    return rowsForMarket(market) > 0 ? 'partial' : 'none';
  }
  const rows = rowsForMarket(market);
  if (rows > 0) return 'ready';
  return market.marketSlug ? 'none' : 'stale';
}

function latestPrice(market: QuantPriceMarket) {
  const yes = toNumber(market.latestBlockPrice ?? market.latestFrontendPrice);
  if (!yes) return '';
  return `YES ${yes.toFixed(3)} · NO ${Math.max(0, 1 - yes).toFixed(3)}`;
}

function titleForMarket(market: QuantPriceMarket) {
  return market.marketTitle || market.eventTitle || market.marketSlug || 'Untitled market';
}

function tokenLike(query: string) {
  const text = query.trim();
  return text.length >= 10 && (/^[0-9]+$/.test(text) || /^0x[0-9a-f]+$/i.test(text) || /^[a-z0-9_-]{16,}$/i.test(text));
}

function searchTextForMarket(market: QuantPriceMarket, result?: SearchResult) {
  return [
    result?.title,
    result?.slug,
    result?.subtitle,
    market.marketTitle,
    market.marketSlug,
    market.eventTitle,
    market.eventSlug,
    market.conditionId,
    extraString(market, 'tokenId'),
    market.tokenSide,
  ].filter(Boolean).join(' ').toLowerCase();
}

function normalizedQueryTermGroups(query: string) {
  const normalized = query
    .trim()
    .toLowerCase()
    .replace(/[_-]+/g, ' ')
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ');
  if (!normalized) return [];
  const terms = normalized.split(' ').filter(Boolean);
  const compact = normalized.replace(/\s+/g, '');
  const groups = terms.map((term) => {
    if (term === 'nba') return ['nba', 'basketball'];
    if (term === 'fifa') return ['fifa', 'world cup', 'soccer'];
    if (term === 'nfl') return ['nfl', 'football'];
    if (term === 'trump') return ['trump', 'donald trump'];
    if (term === 'btc' || term === 'bitcoin') return ['btc', 'bitcoin', 'crypto'];
    return [term];
  });
  if (compact.includes('worldcup') && !groups.some((group) => group.includes('world cup'))) {
    groups.push(['world cup', 'worldcup', 'fifa', 'soccer']);
  }
  return groups;
}

function marketQueryMatch(haystack: string, query: string) {
  const trimmed = query.trim().toLowerCase();
  if (!trimmed) return { matched: true, score: 0, reason: '' };
  const normalizedHaystack = haystack
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .toLowerCase();
  if (haystack.includes(trimmed) || normalizedHaystack.includes(trimmed)) {
    return { matched: true, score: 0, reason: 'exact phrase' };
  }
  const groups = normalizedQueryTermGroups(trimmed);
  if (!groups.length) return { matched: true, score: 0, reason: '' };
  const matchedTerms: string[] = [];
  let score = 0;
  const matched = groups.every((group) => {
    const hit = group.find((term) => normalizedHaystack.includes(term) || haystack.includes(term));
    if (!hit) return false;
    matchedTerms.push(hit);
    score += hit === group[0] ? 0.18 : 0.32;
    return true;
  });
  return {
    matched,
    score: matched ? score : Number.POSITIVE_INFINITY,
    reason: matched ? `matched ${matchedTerms.slice(0, 3).join(' + ')}` : '',
  };
}

function storedFilter(): SearchFilter {
  try {
    const value = window.localStorage.getItem('polydata.quant.search.filter') as SearchFilter | null;
    if (value && FILTERS.some((filter) => filter.value === value)) return value;
    return 'all';
  } catch {
    return 'all';
  }
}

function persistedMarkets(key: string): QuantPriceMarket[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) || '[]');
    return Array.isArray(parsed)
      ? parsed.filter((market): market is QuantPriceMarket => Boolean(market && typeof market.marketSlug === 'string'))
      : [];
  } catch {
    return [];
  }
}

function persistedSlugs(key: string) {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) || '[]');
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === 'string') : [];
  } catch {
    return [];
  }
}

function compactMarketForStorage(market: QuantPriceMarket): QuantPriceMarket {
  return {
    itemKind: market.itemKind,
    eventId: market.eventId,
    eventSlug: market.eventSlug,
    eventTitle: market.eventTitle,
    groupingConfidence: market.groupingConfidence,
    source: market.source,
    outcomeCount: market.outcomeCount,
    totalMembers: market.totalMembers,
    readyMembers: market.readyMembers,
    orderfilledRows: market.orderfilledRows,
    marketId: market.marketId,
    marketSlug: market.marketSlug,
    marketTitle: market.marketTitle,
    tokenSide: market.tokenSide || 'YES',
    conditionId: market.conditionId,
    status: market.status,
    endDate: market.endDate,
    blockRows: market.blockRows,
    frontendRows: market.frontendRows,
    firstBlock: market.firstBlock,
    lastBlock: market.lastBlock,
    latestBlockPrice: market.latestBlockPrice,
    latestBlockAt: market.latestBlockAt,
    firstTs: market.firstTs,
    lastTs: market.lastTs,
    latestFrontendPrice: market.latestFrontendPrice,
    latestFrontendAt: market.latestFrontendAt,
  };
}

function apiPriceSource(priceSource: PriceSource) {
  return priceSource === 'orderfilled' ? 'orderfilled_block_close' : 'frontend';
}

function outcomeToMarket(outcome: QuantMarketSeriesOutcome, eventMarket: QuantPriceMarket): QuantPriceMarket | null {
  const marketSlug = outcome.marketSlug || '';
  if (!marketSlug) return null;
  const tokenSide = outcome.buyYesTokenSide || outcome.tokenSide || 'YES';
  return {
    itemKind: 'market',
    eventId: outcome.eventId || eventMarket.eventId,
    eventSlug: outcome.eventSlug || eventMarket.marketSlug || eventMarket.eventSlug,
    eventTitle: eventMarket.marketTitle || eventMarket.eventTitle,
    groupingConfidence: eventMarket.groupingConfidence || 'event-head',
    source: 'event_price_head',
    outcomeCount: 1,
    totalMembers: eventMarket.totalMembers || eventMarket.outcomeCount,
    readyMembers: outcome.rows ? 1 : 0,
    orderfilledRows: outcome.rows,
    marketId: outcome.marketId,
    marketSlug,
    marketTitle: outcome.marketTitle || outcome.outcomeLabel,
    tokenSide,
    conditionId: outcome.conditionId,
    status: outcome.coverageStatus || eventMarket.status,
    endDate: outcome.endDate || eventMarket.endDate,
    blockRows: outcome.rows,
    frontendRows: outcome.rows,
    firstBlock: outcome.firstX,
    lastBlock: outcome.lastX,
    latestBlockPrice: outcome.buyYesPrice ?? outcome.latestPrice,
    latestBlockAt: outcome.lastX == null ? null : String(outcome.lastX),
  };
}

function buildResult(market: QuantPriceMarket, kind: SearchResultKind, index: number): SearchResult {
  const isEvent = kind === 'event';
  const rows = rowsForMarket(market);
  const outcomes = toNumber(market.outcomeCount || market.totalMembers);
  const ready = toNumber(market.readyMembers);
  const total = toNumber(market.totalMembers || market.outcomeCount);
  const volume = toNumber(extraString(market, 'volume') || extraString(market, 'volume24h'));
  const first = toNumber(market.firstBlock || market.firstTs);
  const last = toNumber(market.lastBlock || market.lastTs);
  const title = titleForMarket(market);
  const condition = market.conditionId || extraString(market, 'tokenId');
  return {
    key: `${kind}:${market.marketSlug}:${condition || index}`,
    kind,
    market,
    title: kind === 'token' ? (condition || market.marketSlug) : title,
    slug: market.marketSlug,
    subtitle: kind === 'token'
      ? `${title} · ${market.tokenSide || 'YES'} side`
      : (isEvent ? market.eventSlug || market.marketSlug : market.eventTitle || market.eventSlug || 'Individual market'),
    coverage: isEvent
      ? `${ready.toLocaleString('en-US')} ready / ${Math.max(total, outcomes).toLocaleString('en-US')} members`
      : rows ? `${rows.toLocaleString('en-US')} rows` : 'No block-close rows',
    price: latestPrice(market),
    status: coverageStatus(market),
    confidence: isEvent ? market.groupingConfidence || 'inferred' : extraString(market, 'source') || 'quant',
    count: isEvent ? `${outcomes.toLocaleString('en-US')} outcomes` : (kind === 'token' ? 'Token/condition' : 'Market'),
    rows,
    outcomes,
    volume,
    updated: last || first || index,
    priority: (isEvent ? 0 : kind === 'token' ? 2 : 1) + (coverageStatus(market) === 'ready' ? -0.25 : 0),
  };
}

export function WorkspaceHeader({
  marketSlug,
  marketQuery,
  timeframe,
  viewportMode = 'preset',
  priceSource,
  backtestEngine,
  loading,
  marketOptions,
  selectedMarket: selectedMarketProp,
  marketSearchStatus,
  onMarketSlugChange,
  onMarketQueryChange,
  onTimeframeChange,
  onPriceSourceChange,
  onBacktestEngineChange,
  onRunBacktest,
  onSave,
  onExport,
  onMarketPreview,
}: WorkspaceHeaderProps) {
  const [marketMenuOpen, setMarketMenuOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const [searchFilter, setSearchFilter] = useState<SearchFilter>(storedFilter);
  const [sortMode, setSortMode] = useState<SearchSort>('relevance');
  const [paletteMode, setPaletteMode] = useState<SearchPaletteMode>('compact');
  const [recentMarkets, setRecentMarkets] = useState<QuantPriceMarket[]>(() => persistedMarkets('polydata.quant.search.recentMarkets'));
  const [favoriteMarketSlugs, setFavoriteMarketSlugs] = useState<string[]>(() => persistedSlugs('polydata.quant.search.favoriteSlugs'));
  const [previewOutcomesExpanded, setPreviewOutcomesExpanded] = useState(false);
  const [previewOutcomeQuery, setPreviewOutcomeQuery] = useState('');
  const [previewOutcomeStatusFilter, setPreviewOutcomeStatusFilter] = useState<OutcomeDrilldownFilter>('all');
  const [outcomeFocusMode, setOutcomeFocusMode] = useState(false);
  const [highlightedOutcomeIndex, setHighlightedOutcomeIndex] = useState(0);
  const [eventOutcomeCache, setEventOutcomeCache] = useState<Record<string, EventOutcomeCacheEntry>>({});
  const inputRef = useRef<HTMLInputElement>(null);
  const commandRef = useRef<HTMLDivElement>(null);
  const previousQueryRef = useRef('');
  const autoOpenedQueryRef = useRef('');
  const eventOutcomeFetchKeysRef = useRef<Set<string>>(new Set());
  const marketChoices = useMemo(() => {
    const choices = new Map<string, QuantPriceMarket>();
    if (selectedMarketProp?.marketSlug) choices.set(selectedMarketProp.marketSlug, selectedMarketProp);
    for (const market of recentMarkets) {
      if (market.marketSlug && !choices.has(market.marketSlug)) choices.set(market.marketSlug, market);
    }
    for (const market of marketOptions) {
      const current = choices.get(market.marketSlug);
      if (!current || (current.itemKind !== 'event' && (market.itemKind === 'event' || market.tokenSide === 'YES'))) {
        choices.set(market.marketSlug, market);
      }
    }
    return Array.from(choices.values()).slice(0, 1500);
  }, [marketOptions, recentMarkets, selectedMarketProp]);
  const selectedMarket = useMemo(
    () => selectedMarketProp || marketChoices.find((market) => market.marketSlug === marketSlug),
    [marketChoices, marketSlug, selectedMarketProp],
  );
  const activeSearchText = marketQuery.trim();
  const queryIsTokenLike = tokenLike(activeSearchText);
  const isSearching = marketSearchStatus === 'loading';
  const isRefining = marketSearchStatus === 'partial';
  const hasSearchError = marketSearchStatus === 'error';
  const activeFilter = activeSearchText && (searchFilter === 'recent' || searchFilter === 'favorites')
    ? 'all'
    : !activeSearchText && searchFilter === 'all'
      ? 'recent'
      : searchFilter;
  const searchScopeNotice = activeSearchText && searchFilter !== activeFilter
    ? `Searching all results; ${searchFilter} is for empty queries.`
    : '';
  const recentSlugSet = useMemo(() => new Set(recentMarkets.map((market) => market.marketSlug)), [recentMarkets]);
  const favoriteSlugSet = useMemo(() => new Set(favoriteMarketSlugs), [favoriteMarketSlugs]);
  const marketBySlug = useMemo(() => {
    const values = new Map<string, QuantPriceMarket>();
    for (const market of [...marketChoices, ...recentMarkets]) {
      if (market.marketSlug && !values.has(market.marketSlug)) values.set(market.marketSlug, market);
    }
    return values;
  }, [marketChoices, recentMarkets]);
  const favoriteMarkets = useMemo(
    () => favoriteMarketSlugs.map((slug) => marketBySlug.get(slug)).filter((market): market is QuantPriceMarket => Boolean(market)).slice(0, 8),
    [favoriteMarketSlugs, marketBySlug],
  );
  const memoryRecentMarkets = useMemo(
    () => recentMarkets.filter((market) => market.marketSlug).slice(0, 8),
    [recentMarkets],
  );

  const searchResults = useMemo(() => {
    const query = activeSearchText.toLowerCase();
    const base = marketChoices.flatMap((market, index) => {
      const isEvent = market.itemKind === 'event';
      const results: SearchResult[] = [buildResult(market, isEvent ? 'event' : 'market', index)];
      const tokenText = `${market.conditionId || ''} ${extraString(market, 'tokenId')}`.trim();
      if (tokenText && (!query || tokenText.toLowerCase().includes(query) || queryIsTokenLike)) {
        results.push(buildResult(market, 'token', index));
      }
      return results;
    });
    const filtered = base.map((result) => {
      const match = marketQueryMatch(searchTextForMarket(result.market, result), query);
      return {
        ...result,
        matchScore: match.score,
        matchReason: match.reason,
      };
    }).filter((result) => {
      if (query && !marketQueryMatch(searchTextForMarket(result.market, result), query).matched) return false;
      if (activeFilter === 'events' && result.kind !== 'event') return false;
      if (activeFilter === 'markets' && result.kind !== 'market') return false;
      if (activeFilter === 'tokens' && result.kind !== 'token') return false;
      if (activeFilter === 'ready' && result.status !== 'ready') return false;
      if (activeFilter === 'active' && !isActiveMarket(result.market)) return false;
      if (activeFilter === 'official' && result.confidence !== 'official') return false;
      if (activeFilter === 'recent' && result.kind === 'token') return false;
      if (activeFilter === 'recent' && recentSlugSet.size && !recentSlugSet.has(result.market.marketSlug)) return false;
      if (activeFilter === 'favorites' && (!favoriteSlugSet.has(result.market.marketSlug) || result.kind === 'token')) return false;
      return true;
    });
    const priorityFor = (result: SearchResult) => (
      result.priority
      + (query ? (result.matchScore || 0) : 0)
      + (!query && result.market.marketSlug === DEFAULT_QUANT_EVENT_SLUG ? -0.75 : 0)
      + (favoriteSlugSet.has(result.market.marketSlug) ? -0.35 : 0)
      + (recentMarkets.findIndex((market) => market.marketSlug === result.market.marketSlug) >= 0
        ? recentMarkets.findIndex((market) => market.marketSlug === result.market.marketSlug) * 0.015
        : 0)
    );
    const sorted = filtered.slice().sort((left, right) => {
      if (queryIsTokenLike && left.kind !== right.kind) return left.kind === 'token' ? -1 : right.kind === 'token' ? 1 : 0;
      if (sortMode === 'volume') return right.volume - left.volume || priorityFor(left) - priorityFor(right);
      if (sortMode === 'coverage') return right.rows - left.rows || priorityFor(left) - priorityFor(right);
      if (sortMode === 'outcomes') return right.outcomes - left.outcomes || priorityFor(left) - priorityFor(right);
      if (sortMode === 'updated') return right.updated - left.updated || priorityFor(left) - priorityFor(right);
      return priorityFor(left) - priorityFor(right) || right.rows - left.rows || left.title.localeCompare(right.title);
    });
    return activeFilter === 'recent' ? sorted.slice(0, 18) : sorted.slice(0, 48);
  }, [activeFilter, activeSearchText, favoriteSlugSet, marketChoices, queryIsTokenLike, recentMarkets, recentSlugSet, sortMode]);

  const sections = useMemo(() => {
    const visibleKinds: SearchResultKind[] = queryIsTokenLike ? ['token', 'event', 'market'] : ['event', 'market', 'token'];
    return visibleKinds.map((kind) => ({
      kind,
      title: kind === 'event' ? 'Events' : kind === 'market' ? 'Markets' : 'Tokens / Condition IDs',
      items: searchResults.filter((result) => result.kind === kind),
    })).filter((section) => section.items.length);
  }, [queryIsTokenLike, searchResults]);

  const flatResults = useMemo(() => sections.flatMap((section) => section.items), [sections]);
  const activeResult = flatResults[Math.min(highlightedIndex, Math.max(0, flatResults.length - 1))] || null;
  const activeEventSlug = activeResult?.kind === 'event' ? activeResult.market.marketSlug : '';
  const activeEventOutcomeCacheKey = activeEventSlug ? `${activeEventSlug}:${apiPriceSource(priceSource)}` : '';
  const activeEventOutcomeCache = activeEventOutcomeCacheKey ? eventOutcomeCache[activeEventOutcomeCacheKey] : undefined;
  const relatedOutcomeMarkets = useMemo(() => {
    if (!activeResult) return [];
    const eventId = activeResult.market.eventId;
    const eventSlug = activeResult.kind === 'event' ? activeResult.market.marketSlug : activeResult.market.eventSlug;
    const related = [
      ...marketChoices,
      ...(activeResult.kind === 'event' ? activeEventOutcomeCache?.items || [] : []),
    ];
    const seen = new Set<string>();
    return related
      .filter((market) => market.itemKind !== 'event')
      .filter((market) => (
        (eventId && market.eventId === eventId)
        || (eventSlug && (market.eventSlug === eventSlug || market.marketSlug.includes(eventSlug)))
      ))
      .filter((market) => {
        if (!market.marketSlug || seen.has(market.marketSlug)) return false;
        seen.add(market.marketSlug);
        return true;
      })
      .sort((left, right) => rowsForMarket(right) - rowsForMarket(left) || titleForMarket(left).localeCompare(titleForMarket(right)));
  }, [activeEventOutcomeCache?.items, activeResult, marketChoices]);
  const filteredRelatedOutcomeMarkets = useMemo(() => {
    const query = previewOutcomeQuery.trim().toLowerCase();
    return relatedOutcomeMarkets.filter((market) => {
      if (previewOutcomeStatusFilter !== 'all' && coverageStatus(market) !== previewOutcomeStatusFilter) return false;
      if (!query) return true;
      return (
        titleForMarket(market).toLowerCase().includes(query)
        || market.marketSlug.toLowerCase().includes(query)
        || String(market.tokenSide || '').toLowerCase().includes(query)
        || String(market.conditionId || '').toLowerCase().includes(query)
      );
    });
  }, [previewOutcomeQuery, previewOutcomeStatusFilter, relatedOutcomeMarkets]);
  const relatedOutcomeSummary = useMemo(() => {
    const counts = relatedOutcomeMarkets.reduce(
      (acc, market) => {
        const status = coverageStatus(market);
        acc.total += 1;
        acc.rows += rowsForMarket(market);
        if (status === 'ready') acc.ready += 1;
        else if (status === 'partial') acc.partial += 1;
        else acc.none += 1;
        return acc;
      },
      { total: 0, ready: 0, partial: 0, none: 0, rows: 0 },
    );
    const readyPct = counts.total ? (counts.ready / counts.total) * 100 : 0;
    return { ...counts, readyPct };
  }, [relatedOutcomeMarkets]);
  const visibleRelatedOutcomeMarkets = useMemo(
    () => filteredRelatedOutcomeMarkets.slice(0, previewOutcomesExpanded ? filteredRelatedOutcomeMarkets.length : 6),
    [filteredRelatedOutcomeMarkets, previewOutcomesExpanded],
  );
  const activeOutcomeMarket = filteredRelatedOutcomeMarkets[Math.min(highlightedOutcomeIndex, Math.max(0, filteredRelatedOutcomeMarkets.length - 1))] || null;

  useEffect(() => {
    setHighlightedIndex(0);
    setOutcomeFocusMode(false);
    setHighlightedOutcomeIndex(0);
  }, [marketQuery, marketOptions, searchFilter, sortMode]);

  useEffect(() => {
    setPreviewOutcomesExpanded(false);
    setPreviewOutcomeQuery('');
    setPreviewOutcomeStatusFilter('all');
    setOutcomeFocusMode(false);
    setHighlightedOutcomeIndex(0);
  }, [activeResult?.key]);

  useEffect(() => {
    setHighlightedOutcomeIndex((current) => Math.max(0, Math.min(current, Math.max(0, filteredRelatedOutcomeMarkets.length - 1))));
  }, [filteredRelatedOutcomeMarkets.length]);

  useEffect(() => {
    if (!activeEventSlug || !activeEventOutcomeCacheKey || eventOutcomeFetchKeysRef.current.has(activeEventOutcomeCacheKey)) return undefined;
    let cancelled = false;
    eventOutcomeFetchKeysRef.current.add(activeEventOutcomeCacheKey);
    setEventOutcomeCache((current) => ({
      ...current,
      [activeEventOutcomeCacheKey]: { status: 'loading', items: [] },
    }));
    void fetchQuantEventPriceHead({
      eventSlug: activeEventSlug,
      priceSource: apiPriceSource(priceSource),
      maxOutcomes: 30,
      topN: 30,
      pointFormat: 'lite',
      timeoutMs: 18000,
    })
      .then((payload) => {
        if (cancelled) return;
        const items = (payload.outcomes || [])
          .map((outcome) => outcomeToMarket(outcome, activeResult?.market || { marketSlug: activeEventSlug, tokenSide: 'YES' }))
          .filter((market): market is QuantPriceMarket => Boolean(market));
        setEventOutcomeCache((current) => ({
          ...current,
          [activeEventOutcomeCacheKey]: { status: 'ready', items },
        }));
      })
      .catch((error) => {
        if (cancelled) return;
        if (!isAbortLikeError(error)) console.warn('quant event outcome preview failed', error);
        setEventOutcomeCache((current) => ({
          ...current,
          [activeEventOutcomeCacheKey]: { status: 'error', items: [] },
        }));
        eventOutcomeFetchKeysRef.current.delete(activeEventOutcomeCacheKey);
      });
    return () => {
      cancelled = true;
    };
  }, [activeEventOutcomeCacheKey, activeEventSlug]);

  useEffect(() => {
    const previous = previousQueryRef.current;
    if (!previous && activeSearchText) {
      setSearchFilter('all');
      setSortMode('relevance');
    }
    previousQueryRef.current = activeSearchText;
  }, [activeSearchText]);

  useEffect(() => {
    if (!activeSearchText || autoOpenedQueryRef.current === activeSearchText) return;
    autoOpenedQueryRef.current = activeSearchText;
    setPaletteMode('full');
    setMarketMenuOpen(true);
  }, [activeSearchText]);

  useEffect(() => {
    try {
      window.localStorage.setItem('polydata.quant.search.filter', searchFilter);
    } catch {
      // localStorage can be blocked in private browsing; the palette still works.
    }
  }, [searchFilter]);

  useEffect(() => {
    try {
      window.localStorage.setItem('polydata.quant.search.recentMarkets', JSON.stringify(recentMarkets.map(compactMarketForStorage)));
    } catch {
      // Recent search history is a convenience; the palette should not depend on storage.
    }
  }, [recentMarkets]);

  useEffect(() => {
    try {
      window.localStorage.setItem('polydata.quant.search.favoriteSlugs', JSON.stringify(favoriteMarketSlugs));
    } catch {
      // Favorites remain session-local when storage is unavailable.
    }
  }, [favoriteMarketSlugs]);

  useEffect(() => {
    const onGlobalKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const isTyping = Boolean(target?.closest('input, textarea, select, [contenteditable="true"]'));
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setPaletteMode('full');
        setMarketMenuOpen(true);
        inputRef.current?.focus();
        inputRef.current?.select();
      }
      if (!isTyping && event.key === '/') {
        event.preventDefault();
        setPaletteMode('compact');
        setMarketMenuOpen(true);
        inputRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onGlobalKeyDown);
    return () => window.removeEventListener('keydown', onGlobalKeyDown);
  }, []);

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (!marketMenuOpen) return;
      const target = event.target as Node | null;
      if (target && commandRef.current?.contains(target)) return;
      setMarketMenuOpen(false);
    };
    window.addEventListener('pointerdown', onPointerDown, true);
    return () => window.removeEventListener('pointerdown', onPointerDown, true);
  }, [marketMenuOpen]);

  const rememberMarket = (market: QuantPriceMarket | null | undefined) => {
    if (!market?.marketSlug) return;
    setRecentMarkets((current) => [
      compactMarketForStorage(market),
      ...current.filter((item) => item.marketSlug !== market.marketSlug),
    ].slice(0, 24));
  };

  const chooseMarket = (slug: string, market?: QuantPriceMarket) => {
    rememberMarket(market || marketChoices.find((choice) => choice.marketSlug === slug));
    onMarketSlugChange(slug);
    onMarketQueryChange('');
    setMarketMenuOpen(false);
    setPaletteMode('compact');
  };

  const setExplicitPaletteMode = (mode: SearchPaletteMode) => {
    setPaletteMode(mode);
  };

  const chooseResult = (result: SearchResult | null) => {
    if (result?.market.marketSlug) chooseMarket(result.market.marketSlug, result.market);
  };

  const toggleFavorite = (market: QuantPriceMarket) => {
    if (!market.marketSlug) return;
    rememberMarket(market);
    setFavoriteMarketSlugs((current) => (
      current.includes(market.marketSlug)
        ? current.filter((slug) => slug !== market.marketSlug)
        : [market.marketSlug, ...current].slice(0, 80)
    ));
  };

  const clearRecentMarkets = () => {
    setRecentMarkets([]);
  };

  const chooseHighlightedResult = () => {
    if (outcomeFocusMode && activeOutcomeMarket?.marketSlug) {
      chooseMarket(activeOutcomeMarket.marketSlug, activeOutcomeMarket);
      return;
    }
    chooseResult(activeResult);
  };

  const chooseFirstOutcomeForActiveResult = () => {
    if (activeResult?.kind !== 'event') {
      chooseResult(activeResult);
      return;
    }
    const firstOutcome = filteredRelatedOutcomeMarkets[0] || relatedOutcomeMarkets[0] || visibleRelatedOutcomeMarkets[0] || null;
    if (firstOutcome?.marketSlug) {
      chooseMarket(firstOutcome.marketSlug, firstOutcome);
      return;
    }
    chooseResult(activeResult);
  };

  const enterOutcomeFocus = () => {
    if (activeResult?.kind !== 'event') return;
    setExplicitPaletteMode('full');
    setPreviewOutcomesExpanded(true);
    setOutcomeFocusMode(true);
    setHighlightedOutcomeIndex((current) => Math.max(0, Math.min(current, Math.max(0, filteredRelatedOutcomeMarkets.length - 1))));
  };

  const leaveOutcomeFocus = () => {
    setOutcomeFocusMode(false);
  };

  const moveOutcomeHighlight = (delta: number) => {
    setHighlightedOutcomeIndex((current) => {
      if (!filteredRelatedOutcomeMarkets.length) return 0;
      return (current + delta + filteredRelatedOutcomeMarkets.length) % filteredRelatedOutcomeMarkets.length;
    });
  };

  const toggleActiveEventOutcomes = () => {
    if (activeResult?.kind !== 'event') return;
    setPreviewOutcomesExpanded((current) => !current);
    setExplicitPaletteMode('full');
  };

  const toggleActiveFavorite = () => {
    if (!activeResult) return;
    toggleFavorite(activeResult.market);
  };

  useEffect(() => {
    if (!selectedMarket?.marketSlug) return;
    const compact = compactMarketForStorage(selectedMarket);
    setRecentMarkets((current) => {
      if (current[0]?.marketSlug === compact.marketSlug) return current;
      return [
        compact,
        ...current.filter((item) => item.marketSlug !== compact.marketSlug),
      ].slice(0, 24);
    });
  }, [
    selectedMarket?.blockRows,
    selectedMarket?.frontendRows,
    selectedMarket?.itemKind,
    selectedMarket?.marketSlug,
    selectedMarket?.marketTitle,
    selectedMarket?.orderfilledRows,
    selectedMarket?.outcomeCount,
    selectedMarket?.readyMembers,
    selectedMarket?.totalMembers,
  ]);

  const moveHighlight = (delta: number) => {
    setHighlightedIndex((current) => {
      if (!flatResults.length) return 0;
      return (current + delta + flatResults.length) % flatResults.length;
    });
  };

  const cycleFilter = () => {
    setSearchFilter((current) => {
      const index = FILTERS.findIndex((filter) => filter.value === current);
      return FILTERS[(index + 1) % FILTERS.length]?.value || 'all';
    });
  };

  const selectedRows = selectedMarket ? rowsForMarket(selectedMarket) : 0;
  const selectedKind = selectedMarket?.itemKind === 'event' ? 'Event' : 'Market';
  const selectedSubtitle = selectedMarket?.marketSlug || marketSlug || 'No market selected';
  const selectedIsFavorite = selectedMarket?.marketSlug ? favoriteSlugSet.has(selectedMarket.marketSlug) : false;
  const sourceLabel = priceSource === 'orderfilled' ? 'OrderFilled block close' : 'Frontend price-history';
  const engineLabel = backtestEngine === 'nautilus_trader' ? 'Nautilus Trader' : backtestEngine === 'backtrader' ? 'Backtrader' : 'Built-in';

  return (
    <header className="qtv-header">
      <div className="qtv-globalbar">
        <a className="qtv-logo" href="/">POLYDATA</a>
        <button className="qtv-menu-button" type="button" title="Menu">Menu</button>
        <nav className="qtv-app-tabs" aria-label="PolyData workspaces">
          {['Markets', 'Quant', 'Replay', 'Strategies', 'Data'].map((item) => (
            <button key={item} className={item === 'Quant' ? 'active' : ''} type="button">{item}</button>
          ))}
        </nav>
        <div className="qtv-global-actions">
          <button type="button" onClick={onSave}>Save</button>
          <button type="button" onClick={() => onExport('json')}>Snapshot</button>
          <button type="button" onClick={() => onExport('csv')}>CSV</button>
        </div>
      </div>

      <div className="qtv-workbar">
        <button
          className="qtv-market-identity"
          type="button"
          title={selectedSubtitle}
          onClick={() => {
            setPaletteMode('compact');
            setMarketMenuOpen(true);
            inputRef.current?.focus();
          }}
        >
          <span>
            <strong>{selectedMarket?.marketTitle || marketSlug || 'Select market'}</strong>
            <em>Polymarket {selectedKind.toLowerCase()} · outcome probabilities · {sourceLabel}</em>
          </span>
          <b>{selectedMarket?.itemKind === 'event' ? `${Number(selectedMarket.outcomeCount || 0).toLocaleString('en-US')} outcomes` : selectedRows ? `${selectedRows.toLocaleString('en-US')} rows` : 'No rows'}</b>
        </button>
        <button
          className={`qtv-current-favorite ${selectedIsFavorite ? 'active' : ''}`}
          type="button"
          title={selectedIsFavorite ? 'Remove current market from favorites' : 'Add current market to favorites'}
          disabled={!selectedMarket?.marketSlug}
          onClick={() => {
            if (selectedMarket) toggleFavorite(selectedMarket);
          }}
        >
          ★
        </button>

        <div className="qtv-workbar-group qtv-search-group">
        <div
          ref={commandRef}
          className={`qtv-market-command ${marketMenuOpen ? 'open' : ''} ${paletteMode === 'full' && marketMenuOpen ? 'full-mode' : ''}`}
          role="combobox"
          aria-expanded={marketMenuOpen}
          aria-haspopup="listbox"
        >
          <span className="qtv-command-label">Search</span>
          <input
            value={marketQuery}
            ref={inputRef}
            onFocus={() => {
              if (!marketMenuOpen) setPaletteMode('compact');
              setMarketMenuOpen(true);
            }}
            onClick={() => {
              if (!marketMenuOpen) setPaletteMode('compact');
              setMarketMenuOpen(true);
            }}
            onInput={(event) => {
              onMarketQueryChange(event.currentTarget.value);
              setMarketMenuOpen(true);
            }}
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown') {
                event.preventDefault();
                setMarketMenuOpen(true);
                if (outcomeFocusMode) moveOutcomeHighlight(1);
                else moveHighlight(1);
              }
              if (event.key === 'ArrowUp') {
                event.preventDefault();
                setMarketMenuOpen(true);
                if (outcomeFocusMode) moveOutcomeHighlight(-1);
                else moveHighlight(-1);
              }
              if (event.key === 'ArrowRight') {
                event.preventDefault();
                enterOutcomeFocus();
              }
              if (event.key === 'ArrowLeft') {
                event.preventDefault();
                leaveOutcomeFocus();
              }
              if (event.key === 'Enter') {
                event.preventDefault();
                if (event.shiftKey) chooseFirstOutcomeForActiveResult();
                else chooseHighlightedResult();
              }
              if (event.altKey && event.key.toLowerCase() === 'e') {
                event.preventDefault();
                toggleActiveEventOutcomes();
              }
              if (event.altKey && event.key.toLowerCase() === 'f') {
                event.preventDefault();
                toggleActiveFavorite();
              }
              if (event.key === 'Escape') {
                event.preventDefault();
                setMarketMenuOpen(false);
              }
              if (event.key === 'Tab' && marketMenuOpen) {
                event.preventDefault();
                cycleFilter();
              }
            }}
            placeholder="Search events, markets, slugs, token IDs..."
            aria-autocomplete="list"
            aria-controls="quant-market-search-results"
          />
          <button
            className="qtv-command-toggle"
            type="button"
            title={marketMenuOpen ? 'Close market search' : 'Open market search'}
            onClick={() => {
              setPaletteMode('compact');
              setMarketMenuOpen((current) => !current);
              if (!marketMenuOpen) window.setTimeout(() => inputRef.current?.focus(), 0);
            }}
          >
            ▾
          </button>
          {marketMenuOpen ? (
            <>
            {paletteMode === 'full' ? <div className="qtv-market-palette-backdrop" aria-hidden="true" /> : null}
            <div id="quant-market-search-results" className={`qtv-market-palette ${paletteMode}`} role="listbox">
              <div className="qtv-palette-head">
                <div>
                  <strong>{activeSearchText ? 'Search results' : activeFilter === 'favorites' ? 'Favorite markets' : 'Recent coverage'}</strong>
                  <span>
                    {outcomeFocusMode && activeOutcomeMarket
                      ? `Outcome focus · ${titleForMarket(activeOutcomeMarket)}`
                      : searchScopeNotice || (activeSearchText ? 'Events · Markets · Tokens · Conditions' : 'Recents and favorites stay local to this browser')}
                  </span>
                </div>
                <div className="qtv-palette-head-actions" onMouseDown={(event) => event.preventDefault()}>
                  {isRefining ? <em>Updating events...</em> : null}
                  <button type="button" onClick={() => setExplicitPaletteMode(paletteMode === 'full' ? 'compact' : 'full')}>
                    {paletteMode === 'full' ? 'Collapse' : 'Expand'}
                  </button>
                  <button type="button" aria-label="Close search" onClick={() => setMarketMenuOpen(false)}>×</button>
                </div>
              </div>
              <div className="qtv-palette-tools" onMouseDown={(event) => event.preventDefault()}>
                <div className="qtv-filter-chips" aria-label="Search filters">
                  {FILTERS.map((filter) => (
                    <button
                      key={filter.value}
                      className={activeFilter === filter.value ? 'active' : ''}
                      type="button"
                      onClick={() => setSearchFilter(filter.value)}
                    >
                      {filter.label}
                    </button>
                  ))}
                </div>
                <label className="qtv-sort-select" onMouseDown={(event) => event.stopPropagation()}>
                  <span>Sort</span>
                  <select value={sortMode} onChange={(event) => setSortMode(event.currentTarget.value as SearchSort)}>
                    {SORTS.map((sort) => <option key={sort.value} value={sort.value}>{sort.label}</option>)}
                  </select>
                </label>
              </div>
              {(favoriteMarkets.length || memoryRecentMarkets.length) ? (
                <div className="qtv-palette-memory-rail" onMouseDown={(event) => event.preventDefault()}>
                  {favoriteMarkets.length ? (
                    <section>
                      <strong>Favorites</strong>
                      {favoriteMarkets.map((market) => (
                        <button
                          key={`fav-${market.marketSlug}`}
                          type="button"
                          title={market.marketSlug}
                          onClick={() => chooseMarket(market.marketSlug, market)}
                        >
                          <span>{titleForMarket(market)}</span>
                          <b>{market.itemKind === 'event' ? `${toNumber(market.outcomeCount || market.totalMembers).toLocaleString('en-US')} outcomes` : `${rowsForMarket(market).toLocaleString('en-US')} rows`}</b>
                        </button>
                      ))}
                    </section>
                  ) : null}
                  {memoryRecentMarkets.length ? (
                    <section>
                      <strong>Recent</strong>
                      {memoryRecentMarkets.map((market) => (
                        <button
                          key={`recent-${market.marketSlug}`}
                          type="button"
                          title={market.marketSlug}
                          onClick={() => chooseMarket(market.marketSlug, market)}
                        >
                          <span>{titleForMarket(market)}</span>
                          <b>{market.itemKind === 'event' ? `${toNumber(market.outcomeCount || market.totalMembers).toLocaleString('en-US')} outcomes` : `${rowsForMarket(market).toLocaleString('en-US')} rows`}</b>
                        </button>
                      ))}
                      <button className="clear" type="button" onClick={clearRecentMarkets}>Clear</button>
                    </section>
                  ) : null}
                </div>
              ) : null}
              <div className="qtv-palette-body">
                <div className="qtv-results-list">
                  {isSearching ? (
                    <div className="qtv-skeleton-stack" aria-label="Searching events and markets">
                      <strong>Searching events and markets...</strong>
                      {Array.from({ length: 6 }).map((_, index) => <span key={index} />)}
                    </div>
                  ) : hasSearchError ? (
                    <div className="qtv-market-menu-empty">
                      <strong>Search failed</strong>
                      <span>Retry by editing the query. Debug: {activeSearchText || 'recent'}</span>
                    </div>
                  ) : sections.length ? (
                    sections.map((section) => (
                      <section key={section.kind} className="qtv-result-section">
                        <h3>{section.title}</h3>
                        {section.items.map((result) => {
                          const index = flatResults.findIndex((item) => item.key === result.key);
                          const selected = result.market.marketSlug === marketSlug;
                          const inlineOutcomeLimit = previewOutcomesExpanded ? 24 : 5;
                          const inlineOutcomes = result.kind === 'event' && index === highlightedIndex ? visibleRelatedOutcomeMarkets.slice(0, inlineOutcomeLimit) : [];
                          const inlineOutcomeRemainder = Math.max(0, filteredRelatedOutcomeMarkets.length - inlineOutcomes.length);
                          return (
                            <div key={result.key} className="qtv-result-entry">
                              <button
                                className={`qtv-result-row ${selected ? 'selected' : ''} ${index === highlightedIndex ? 'highlighted' : ''}`}
                                type="button"
                                role="option"
                                aria-selected={selected}
                                id={`quant-market-option-${index}`}
                                onMouseDown={(event) => event.preventDefault()}
                                onMouseEnter={() => {
                                  setHighlightedIndex(index);
                                  onMarketPreview?.(result.market.marketSlug);
                                }}
                                onClick={() => chooseResult(result)}
                              >
                                <span className={`qtv-type-badge ${result.kind}`}>{result.kind === 'event' ? 'Event' : result.kind === 'market' ? 'Market' : 'Token'}</span>
                                <span className="qtv-result-main">
                                  <strong>{result.title}</strong>
                                  <small>{result.kind === 'token' ? result.subtitle : result.slug}</small>
                                  <em>{result.coverage}{result.price ? ` · ${result.price}` : ''}</em>
                                  {result.kind === 'event' && index === highlightedIndex ? <kbd>Shift+Enter first outcome</kbd> : null}
                                </span>
                                <span className="qtv-result-badges">
                                  <span
                                    className={`qtv-favorite-toggle ${favoriteSlugSet.has(result.market.marketSlug) ? 'active' : ''}`}
                                    role="button"
                                    tabIndex={-1}
                                    title={favoriteSlugSet.has(result.market.marketSlug) ? 'Remove from favorites' : 'Add to favorites'}
                                    onMouseDown={(event) => {
                                      event.preventDefault();
                                      event.stopPropagation();
                                    }}
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      toggleFavorite(result.market);
                                    }}
                                  >
                                    ★
                                  </span>
                                  <b>{result.count}</b>
                                  {activeSearchText && result.matchReason ? <small className="match">{result.matchReason}</small> : null}
                                  <small className={`coverage ${result.status}`}>{result.status === 'none' ? 'no rows' : result.status}</small>
                                  <small>{result.confidence}</small>
                                </span>
                              </button>
                              {inlineOutcomes.length || (result.kind === 'event' && index === highlightedIndex && activeEventOutcomeCache?.status === 'loading') ? (
                                <div className="qtv-inline-outcomes" onMouseDown={(event) => event.preventDefault()}>
                                  <div className="qtv-inline-outcome-head">
                                    <div>
                                      <strong>Event drilldown</strong>
                                      <span>
                                        {activeEventOutcomeCache?.status === 'loading'
                                          ? 'Loading outcome members...'
                                          : `${filteredRelatedOutcomeMarkets.length.toLocaleString('en-US')} shown · ${relatedOutcomeSummary.ready.toLocaleString('en-US')} ready · ${relatedOutcomeSummary.rows.toLocaleString('en-US')} rows`}
                                      </span>
                                    </div>
                                    <button type="button" onClick={() => chooseResult(result)}>Open event</button>
                                    <button
                                      type="button"
                                      disabled={!filteredRelatedOutcomeMarkets.length}
                                      onClick={enterOutcomeFocus}
                                    >
                                      Focus outcomes
                                    </button>
                                  </div>
                                  {inlineOutcomes.map((market) => (
                                    <button
                                      key={`inline-${market.marketSlug}`}
                                      className={outcomeFocusMode && activeOutcomeMarket?.marketSlug === market.marketSlug ? 'highlighted' : ''}
                                      type="button"
                                      title={`${titleForMarket(market)}\n${market.marketSlug}`}
                                      onMouseEnter={() => {
                                        const nextIndex = filteredRelatedOutcomeMarkets.findIndex((item) => item.marketSlug === market.marketSlug);
                                        if (nextIndex >= 0) {
                                          setHighlightedOutcomeIndex(nextIndex);
                                          setOutcomeFocusMode(true);
                                        }
                                      }}
                                      onClick={() => chooseMarket(market.marketSlug, market)}
                                    >
                                      <span>
                                        <strong>{titleForMarket(market)}</strong>
                                        <small>{market.marketSlug}</small>
                                      </span>
                                      <b>{latestPrice(market) || `${rowsForMarket(market).toLocaleString('en-US')} rows`}</b>
                                    </button>
                                  ))}
                                  {!inlineOutcomes.length ? <span>Loading event outcomes...</span> : null}
                                  {filteredRelatedOutcomeMarkets.length > inlineOutcomes.length || previewOutcomesExpanded ? (
                                    <button
                                      className="more"
                                      type="button"
                                      title={previewOutcomesExpanded ? 'Collapse event outcomes' : 'Expand event outcomes inline'}
                                      onClick={() => setPreviewOutcomesExpanded((current) => !current)}
                                    >
                                      {previewOutcomesExpanded ? 'Show fewer' : `+${inlineOutcomeRemainder.toLocaleString('en-US')} more`}
                                    </button>
                                  ) : null}
                                </div>
                              ) : null}
                            </div>
                          );
                        })}
                      </section>
                    ))
                  ) : (
                    <div className="qtv-market-menu-empty">
                      <strong>No events or markets found</strong>
                      <span>Try title, slug, token ID, or condition ID.</span>
                      <div>
                        <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => setSearchFilter('all')}>Clear filters</button>
                        <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => setSearchFilter('recent')}>Show recent coverage</button>
                      </div>
                    </div>
                  )}
                </div>
                <aside className="qtv-search-preview" aria-label="Selected search result preview">
                  {activeResult ? (
                    <>
                      <span className={`qtv-type-badge ${activeResult.kind}`}>{activeResult.kind === 'event' ? 'Event' : activeResult.kind === 'market' ? 'Market' : 'Token'}</span>
                      <strong>{activeResult.kind === 'token' ? activeResult.subtitle : activeResult.title}</strong>
                      <small>{activeResult.slug}</small>
                      <div className="qtv-preview-actions">
                        <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => chooseResult(activeResult)}>Open</button>
                        {activeResult.kind === 'event' ? (
                          <button
                            type="button"
                            disabled={!filteredRelatedOutcomeMarkets.length && !relatedOutcomeMarkets.length}
                            onMouseDown={(event) => event.preventDefault()}
                            onClick={chooseFirstOutcomeForActiveResult}
                          >
                            Open first outcome
                          </button>
                        ) : null}
                        {activeResult.kind === 'event' ? (
                          <button
                            type="button"
                            disabled={!activeOutcomeMarket}
                            onMouseDown={(event) => event.preventDefault()}
                            onClick={() => activeOutcomeMarket && chooseMarket(activeOutcomeMarket.marketSlug, activeOutcomeMarket)}
                          >
                            Open highlighted
                          </button>
                        ) : null}
                        <button type="button" className={favoriteSlugSet.has(activeResult.market.marketSlug) ? 'active' : ''} onMouseDown={(event) => event.preventDefault()} onClick={() => toggleFavorite(activeResult.market)}>
                          {favoriteSlugSet.has(activeResult.market.marketSlug) ? 'Favorited' : 'Favorite'}
                        </button>
                      </div>
                      <dl>
                        <div>
                          <dt>Coverage</dt>
                          <dd>
                            {activeResult.kind === 'event' && relatedOutcomeSummary.total
                              ? `${relatedOutcomeSummary.ready.toLocaleString('en-US')} ready / ${relatedOutcomeSummary.total.toLocaleString('en-US')} previewed`
                              : activeResult.coverage}
                          </dd>
                        </div>
                        <div><dt>Status</dt><dd>{activeResult.status}</dd></div>
                        <div><dt>Price</dt><dd>{activeResult.price || '--'}</dd></div>
                        <div><dt>Source</dt><dd>{sourceLabel}</dd></div>
                      </dl>
                      {activeResult.kind === 'event' && (relatedOutcomeMarkets.length || activeEventOutcomeCache?.status === 'loading' || activeEventOutcomeCache?.status === 'error') ? (
                        <div className="qtv-preview-outcomes">
                          <header>
                            <div>
                              <strong>Outcomes in this event</strong>
                              <span>
                                {activeEventOutcomeCache?.status === 'loading'
                                  ? 'loading event head...'
                                  : activeEventOutcomeCache?.status === 'error'
                                    ? 'event head unavailable'
                                    : `${relatedOutcomeSummary.ready.toLocaleString('en-US')} ready · ${relatedOutcomeSummary.partial.toLocaleString('en-US')} partial · ${relatedOutcomeSummary.none.toLocaleString('en-US')} no rows`}
                              </span>
                            </div>
                            <em>{relatedOutcomeSummary.readyPct.toFixed(0)}% ready</em>
                          </header>
                          <div className="qtv-preview-outcome-health">
                            <span>
                              <b style={{ width: `${Math.max(2, Math.round(relatedOutcomeSummary.readyPct))}%` }} />
                            </span>
                            <small>{filteredRelatedOutcomeMarkets.length.toLocaleString('en-US')} / {relatedOutcomeMarkets.length.toLocaleString('en-US')} shown · {relatedOutcomeSummary.rows.toLocaleString('en-US')} rows</small>
                          </div>
                          <div className="qtv-preview-outcome-filters" role="group" aria-label="Outcome readiness filters">
                            {OUTCOME_DRILLDOWN_FILTERS.map((filter) => (
                              <button
                                key={filter.value}
                                type="button"
                                className={previewOutcomeStatusFilter === filter.value ? 'active' : ''}
                                onMouseDown={(event) => event.preventDefault()}
                                onClick={() => setPreviewOutcomeStatusFilter(filter.value)}
                              >
                                {filter.label}
                              </button>
                            ))}
                          </div>
                          <input
                            className="qtv-preview-outcome-search"
                            value={previewOutcomeQuery}
                            placeholder="Filter event outcomes by team, slug, token..."
                            onInput={(event) => setPreviewOutcomeQuery(event.currentTarget.value)}
                          />
                          {activeEventOutcomeCache?.status === 'loading' && !visibleRelatedOutcomeMarkets.length ? (
                            <span className="qtv-preview-outcome-loading">Loading real outcome prices...</span>
                          ) : null}
                          {activeEventOutcomeCache?.status === 'error' && !visibleRelatedOutcomeMarkets.length ? (
                            <span className="qtv-preview-outcome-loading error">Could not load outcome members for this event.</span>
                          ) : null}
                          {!visibleRelatedOutcomeMarkets.length && relatedOutcomeMarkets.length && (previewOutcomeQuery.trim() || previewOutcomeStatusFilter !== 'all') ? (
                            <span className="qtv-preview-outcome-loading">No outcomes match the current event drilldown filter.</span>
                          ) : null}
                          {visibleRelatedOutcomeMarkets.map((market) => (
                            <button
                              key={market.marketSlug}
                              className={outcomeFocusMode && activeOutcomeMarket?.marketSlug === market.marketSlug ? 'highlighted' : ''}
                              type="button"
                              title={`${titleForMarket(market)}\n${market.marketSlug}`}
                              onMouseDown={(event) => event.preventDefault()}
                              onMouseEnter={() => {
                                const nextIndex = filteredRelatedOutcomeMarkets.findIndex((item) => item.marketSlug === market.marketSlug);
                                if (nextIndex >= 0) {
                                  setHighlightedOutcomeIndex(nextIndex);
                                  setOutcomeFocusMode(true);
                                }
                              }}
                              onClick={() => chooseMarket(market.marketSlug, market)}
                            >
                              <span>
                                <b>{titleForMarket(market)}</b>
                                <small>{market.marketSlug}</small>
                              </span>
                              <em>{latestPrice(market) || `${rowsForMarket(market).toLocaleString('en-US')} rows`}</em>
                            </button>
                          ))}
                          {filteredRelatedOutcomeMarkets.length > visibleRelatedOutcomeMarkets.length || previewOutcomesExpanded ? (
                            <button
                              className="qtv-preview-more"
                              type="button"
                              onMouseDown={(event) => event.preventDefault()}
                              onClick={() => setPreviewOutcomesExpanded((current) => !current)}
                            >
                              {previewOutcomesExpanded ? 'Show fewer outcomes' : `Show all ${filteredRelatedOutcomeMarkets.length.toLocaleString('en-US')} outcomes`}
                            </button>
                          ) : null}
                        </div>
                      ) : null}
                      <p>Enter to open {activeResult.kind === 'event' ? 'event chart' : 'market chart'}</p>
                    </>
                  ) : (
                    <>
                      <strong>Ready when you are</strong>
                      <small>Use arrows to preview, Enter to open.</small>
                    </>
                  )}
                </aside>
              </div>
              <div className={`qtv-palette-foot ${outcomeFocusMode ? 'outcome-focus' : ''}`}>
                <span>Ctrl/Cmd+K</span><span>{outcomeFocusMode ? '↑↓ outcome' : '↑↓ result'}</span><span>Enter open</span><span>→ outcomes</span><span>← results</span><span>Shift+Enter first outcome</span><span>Alt+E outcomes</span><span>Alt+F favorite</span><span>Esc close</span><span>Tab filter</span>
              </div>
            </div>
            </>
          ) : null}
        </div>
        <button className="qtv-icon-button" type="button" title="Add market">+</button>
        </div>

        <div className="qtv-workbar-group qtv-timeframes" aria-label="Block range">
          {([
            ['500', '500blk'],
            ['1000', '1k'],
            ['2500', '2.5k'],
            ['5000', '5k'],
            ['15000', '15k'],
            ['all', 'All'],
          ] as Array<[string, string]>).map(([value, label]) => (
            <button key={value} className={viewportMode !== 'custom' && timeframe === value ? 'active' : ''} type="button" onClick={() => onTimeframeChange(value)}>{label}</button>
          ))}
          {viewportMode === 'custom' ? <span className="qtv-custom-view">Custom view</span> : null}
        </div>

        <div className="qtv-workbar-group qtv-select-group">
          <label>
            <span>Source</span>
            <select value={priceSource} onChange={(event) => onPriceSourceChange(event.currentTarget.value as PriceSource)}>
              <option value="orderfilled">OrderFilled block close</option>
              <option value="frontend">Frontend price-history</option>
              <option value="orderbook" disabled>Orderbook mid · pending history</option>
              <option value="conservative" disabled>Conservative bid/ask · pending history</option>
            </select>
          </label>
          <label>
            <span>Engine</span>
        <select value={backtestEngine} onChange={(event) => onBacktestEngineChange(event.currentTarget.value as BacktestEngine)}>
          <option value="builtin">Built-in</option>
          <option value="backtrader">Backtrader</option>
          <option value="nautilus_trader">Nautilus Trader</option>
        </select>
          </label>
        </div>

        <div className="qtv-workbar-group qtv-run-group">
          <span>{engineLabel}</span>
          <button className="primary" type="button" onClick={onRunBacktest}>{loading ? 'Running...' : 'Run Backtest'}</button>
        </div>
      </div>
    </header>
  );
}
