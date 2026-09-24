import type { ContentItem, MarketGroupItem } from '@/types';
import { fetchRuntimeWorldCupDashboard, fetchRuntimeWorldCupIntel } from '@/services/api';
import type {
  WorldCupCityWeather,
  WorldCupDashboardPayload,
  WorldCupMatch,
  WorldCupNewsItem,
  WorldCupOddsSnapshot,
  WorldCupPolymarketMarket,
  WorldCupStage,
  WorldCupVenueCity,
} from './types';

const LOCAL_DATA_URL = '/data/worldcup-2026.json';
const REMOTE_DATA_URL = 'https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json';
const MS_PER_MINUTE = 60 * 1000;
const DASHBOARD_CACHE_TTL_MS = 5 * MS_PER_MINUTE;
const DASHBOARD_STALE_CACHE_TTL_MS = 60 * MS_PER_MINUTE;
const INITIAL_RUNTIME_TIMEOUT_MS = 3500;
const SCHEDULE_TIMEOUT_MS = 12000;
const BACKGROUND_RUNTIME_TIMEOUT_MS = 12000;
const DASHBOARD_PERSISTENT_CACHE_KEY = 'polydata:worldcup-dashboard-cache:v2';
const DASHBOARD_PERSISTENT_CACHE_VERSION = 2;

type DashboardCacheEntry = {
  expiresAt: number;
  payload: WorldCupDashboardPayload;
};

type PersistentDashboardCacheEntry = DashboardCacheEntry & {
  storedAt: number;
  version: number;
};

let dashboardCache: DashboardCacheEntry | null = null;
let dashboardInflight: Promise<WorldCupDashboardPayload> | null = null;
let scheduleInflight: Promise<WorldCupDashboardPayload> | null = null;
let runtimeDashboardInflight: Promise<WorldCupDashboardPayload> | null = null;
let runtimeIntelInflight: Promise<WorldCupDashboardPayload['intelligence']> | null = null;

export const WORLD_CUP_CITIES: WorldCupVenueCity[] = [
  { id: 'atlanta', city: 'Atlanta', country: 'US', countryName: 'United States', venue: 'Mercedes-Benz Stadium', latitude: 33.7554, longitude: -84.4008, timezone: 'America/New_York', capacity: 71000 },
  { id: 'boston', city: 'Boston / Foxborough', country: 'US', countryName: 'United States', venue: 'Gillette Stadium', latitude: 42.0909, longitude: -71.2643, timezone: 'America/New_York', capacity: 65878 },
  { id: 'dallas', city: 'Dallas / Arlington', country: 'US', countryName: 'United States', venue: 'AT&T Stadium', latitude: 32.7473, longitude: -97.0945, timezone: 'America/Chicago', capacity: 80000 },
  { id: 'houston', city: 'Houston', country: 'US', countryName: 'United States', venue: 'NRG Stadium', latitude: 29.6847, longitude: -95.4107, timezone: 'America/Chicago', capacity: 72220 },
  { id: 'kansas-city', city: 'Kansas City', country: 'US', countryName: 'United States', venue: 'Arrowhead Stadium', latitude: 39.0489, longitude: -94.4839, timezone: 'America/Chicago', capacity: 76416 },
  { id: 'los-angeles', city: 'Los Angeles / Inglewood', country: 'US', countryName: 'United States', venue: 'SoFi Stadium', latitude: 33.9535, longitude: -118.3392, timezone: 'America/Los_Angeles', capacity: 70240 },
  { id: 'miami', city: 'Miami Gardens', country: 'US', countryName: 'United States', venue: 'Hard Rock Stadium', latitude: 25.958, longitude: -80.2389, timezone: 'America/New_York', capacity: 65326 },
  { id: 'new-york-new-jersey', city: 'New York / New Jersey', country: 'US', countryName: 'United States', venue: 'MetLife Stadium', latitude: 40.8135, longitude: -74.0745, timezone: 'America/New_York', capacity: 82500 },
  { id: 'philadelphia', city: 'Philadelphia', country: 'US', countryName: 'United States', venue: 'Lincoln Financial Field', latitude: 39.9008, longitude: -75.1675, timezone: 'America/New_York', capacity: 67594 },
  { id: 'san-francisco', city: 'San Francisco Bay Area', country: 'US', countryName: 'United States', venue: "Levi's Stadium", latitude: 37.403, longitude: -121.97, timezone: 'America/Los_Angeles', capacity: 68500 },
  { id: 'seattle', city: 'Seattle', country: 'US', countryName: 'United States', venue: 'Lumen Field', latitude: 47.5952, longitude: -122.3316, timezone: 'America/Los_Angeles', capacity: 69000 },
  { id: 'guadalajara', city: 'Guadalajara / Zapopan', country: 'MX', countryName: 'Mexico', venue: 'Estadio Akron', latitude: 20.6818, longitude: -103.4623, timezone: 'America/Mexico_City', capacity: 49850 },
  { id: 'mexico-city', city: 'Mexico City', country: 'MX', countryName: 'Mexico', venue: 'Estadio Azteca', latitude: 19.3029, longitude: -99.1505, timezone: 'America/Mexico_City', capacity: 87523 },
  { id: 'monterrey', city: 'Monterrey / Guadalupe', country: 'MX', countryName: 'Mexico', venue: 'Estadio BBVA', latitude: 25.6683, longitude: -100.2446, timezone: 'America/Monterrey', capacity: 53500 },
  { id: 'toronto', city: 'Toronto', country: 'CA', countryName: 'Canada', venue: 'BMO Field', latitude: 43.6332, longitude: -79.4186, timezone: 'America/Toronto', capacity: 45000 },
  { id: 'vancouver', city: 'Vancouver', country: 'CA', countryName: 'Canada', venue: 'BC Place', latitude: 49.2767, longitude: -123.1119, timezone: 'America/Vancouver', capacity: 54500 },
];

export const WORLD_CUP_HOST_MATCH_COUNTS: Record<string, number> = {
  atlanta: 8,
  boston: 7,
  dallas: 9,
  houston: 7,
  'kansas-city': 6,
  'los-angeles': 8,
  miami: 7,
  'new-york-new-jersey': 8,
  philadelphia: 6,
  'san-francisco': 6,
  seattle: 6,
  guadalajara: 4,
  'mexico-city': 5,
  monterrey: 4,
  toronto: 6,
  vancouver: 7,
};

const GROUND_TO_CITY_ID: Record<string, string> = {
  Atlanta: 'atlanta',
  'Boston (Foxborough)': 'boston',
  'Dallas (Arlington)': 'dallas',
  Houston: 'houston',
  'Kansas City': 'kansas-city',
  'Los Angeles (Inglewood)': 'los-angeles',
  'Miami (Miami Gardens)': 'miami',
  'New York/New Jersey (East Rutherford)': 'new-york-new-jersey',
  Philadelphia: 'philadelphia',
  'San Francisco Bay Area (Santa Clara)': 'san-francisco',
  Seattle: 'seattle',
  'Guadalajara (Zapopan)': 'guadalajara',
  'Mexico City': 'mexico-city',
  'Monterrey (Guadalupe)': 'monterrey',
  Toronto: 'toronto',
  Vancouver: 'vancouver',
};

function parseKickoff(source: { date: string; time: string }) {
  const match = /^(\d{1,2}):(\d{2})\s+UTC([+-]\d{1,2})(?::?(\d{2}))?$/.exec(source.time);
  if (!match) return new Date(`${source.date}T00:00:00Z`);
  const [, hour = '00', minute = '00', offsetHours = '+0', offsetMinutes = '00'] = match;
  const utcMs = Date.UTC(
    Number(source.date.slice(0, 4)),
    Number(source.date.slice(5, 7)) - 1,
    Number(source.date.slice(8, 10)),
    Number(hour),
    Number(minute),
  );
  const sign = offsetHours.startsWith('-') ? -1 : 1;
  const offsetMs = sign * (Math.abs(Number(offsetHours)) * 60 + Number(offsetMinutes)) * MS_PER_MINUTE;
  return new Date(utcMs - offsetMs);
}

function stageFromRound(round = '', group = ''): WorldCupStage {
  const text = `${round} ${group}`.toLowerCase();
  if (text.includes('final') && text.includes('third')) return 'third_place';
  if (text.includes('final')) return 'final';
  if (text.includes('semi')) return 'semifinal';
  if (text.includes('quarter')) return 'quarterfinal';
  if (text.includes('round of 16')) return 'round16';
  if (text.includes('round of 32')) return 'round32';
  return 'group';
}

function formatInTimeZone(date: Date, timeZone: string, withDate = true) {
  return new Intl.DateTimeFormat('en-GB', {
    month: withDate ? 'short' : undefined,
    day: withDate ? '2-digit' : undefined,
    weekday: withDate ? 'short' : undefined,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone,
  }).format(date);
}

function normalizePlaceholderTeam(team?: string) {
  if (!team) return 'TBD';
  const winner = /^W(\d+)$/.exec(team);
  if (winner) return `Winner M${winner[1]}`;
  const loser = /^L(\d+)$/.exec(team);
  if (loser) return `Loser M${loser[1]}`;
  const groupRank = /^([123])([A-L])$/.exec(team);
  if (groupRank) return `${groupRank[2]}${groupRank[1]}`;
  const thirdPlace = /^3([A-L](?:\/[A-L])*)$/.exec(team);
  if (thirdPlace) return `3rd ${thirdPlace[1]}`;
  return team;
}

export function normalizeWorldCupMatches(matches: any[], marketGroups: MarketGroupItem[] = []): WorldCupMatch[] {
  const linkedText = marketGroups.map((group) => `${group.title} ${group.slug || ''} ${(group.tags || []).join(' ')}`.toLowerCase());
  return matches
    .filter((match) => match?.date && match?.time)
    .map((match, index) => {
      const kickoff = parseKickoff(match);
      const cityId = GROUND_TO_CITY_ID[match.ground] || 'new-york-new-jersey';
      const city = WORLD_CUP_CITIES.find((item) => item.id === cityId) || WORLD_CUP_CITIES[7]!;
      const homeTeam = normalizePlaceholderTeam(match.team1);
      const awayTeam = normalizePlaceholderTeam(match.team2);
      const marketNeedles = [homeTeam, awayTeam, match.team1, match.team2]
        .filter(Boolean)
        .map((value) => String(value).toLowerCase());
      const marketLinked = linkedText.some((text) => (
        text.includes('world cup') && marketNeedles.some((needle) => needle !== 'tbd' && text.includes(needle))
      ));
      return {
        id: `wc2026-${String(match.num || match.match || index + 1).padStart(3, '0')}`,
        fifaMatchNumber: Number(match.num || match.match || index + 1),
        stage: stageFromRound(match.round, match.group),
        group: match.group || '',
        round: match.round || 'World Cup',
        kickoffUtc: kickoff.toISOString(),
        kickoffBeijing: formatInTimeZone(kickoff, 'Asia/Shanghai'),
        kickoffLocal: formatInTimeZone(kickoff, city.timezone),
        cityId,
        city: city.city,
        venue: city.venue,
        homeTeam,
        awayTeam,
        status: kickoff.getTime() < Date.now() ? 'finished' : 'scheduled',
        marketLinked,
        oddsLinked: index < 12,
      } satisfies WorldCupMatch;
    })
    .sort((a, b) => new Date(a.kickoffUtc).getTime() - new Date(b.kickoffUtc).getTime());
}

function isUsableDashboard(payload: WorldCupDashboardPayload | null | undefined): payload is WorldCupDashboardPayload {
  return Boolean(payload?.cities?.length && payload?.matches?.length);
}

function writeDashboardCache(payload: WorldCupDashboardPayload) {
  const now = Date.now();
  const entry = { payload, expiresAt: now + DASHBOARD_CACHE_TTL_MS };
  dashboardCache = entry;
  if (typeof window !== 'undefined' && isUsableDashboard(payload)) {
    try {
      const persistent: PersistentDashboardCacheEntry = {
        ...entry,
        storedAt: now,
        version: DASHBOARD_PERSISTENT_CACHE_VERSION,
      };
      window.localStorage.setItem(DASHBOARD_PERSISTENT_CACHE_KEY, JSON.stringify(persistent));
    } catch {
      // Best-effort cache only; runtime loading must not depend on storage.
    }
  }
  return payload;
}

function readPersistentDashboardCache(allowStale = false) {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(DASHBOARD_PERSISTENT_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PersistentDashboardCacheEntry>;
    const payload = parsed.payload;
    const expiresAt = Number(parsed.expiresAt || 0);
    const storedAt = Number(parsed.storedAt || 0);
    const now = Date.now();
    const fresh = expiresAt > now;
    const staleUsable = allowStale && storedAt > now - DASHBOARD_STALE_CACHE_TTL_MS;
    if (parsed.version !== DASHBOARD_PERSISTENT_CACHE_VERSION || !isUsableDashboard(payload) || (!fresh && !staleUsable)) {
      window.localStorage.removeItem(DASHBOARD_PERSISTENT_CACHE_KEY);
      return null;
    }
    if (fresh) dashboardCache = { payload, expiresAt };
    return payload;
  } catch {
    try {
      window.localStorage.removeItem(DASHBOARD_PERSISTENT_CACHE_KEY);
    } catch {
      // Ignore storage cleanup failures.
    }
    return null;
  }
}

function readDashboardCache(options: { allowStale?: boolean } = {}) {
  const now = Date.now();
  if (dashboardCache && dashboardCache.expiresAt > now) return dashboardCache.payload;
  return readPersistentDashboardCache(Boolean(options.allowStale));
}

async function loadWorldCupScheduleDashboard(): Promise<WorldCupDashboardPayload> {
  if (scheduleInflight) return scheduleInflight;
  scheduleInflight = (async () => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), SCHEDULE_TIMEOUT_MS);
    try {
      for (const url of [LOCAL_DATA_URL, REMOTE_DATA_URL]) {
        try {
          const response = await fetch(url, { cache: 'force-cache', signal: controller.signal });
          if (!response.ok) throw new Error(`world cup schedule ${response.status}`);
          const data = await response.json();
          const matches = normalizeWorldCupMatches(data.matches || []);
          if (matches.length) return buildWorldCupDashboard(matches, 'remote');
        } catch {
          // Try the next official schedule source before showing a source-required state.
        }
      }
      return buildWorldCupDashboard([], 'source-required');
    } catch {
      return buildWorldCupDashboard([], 'source-required');
    } finally {
      window.clearTimeout(timer);
      scheduleInflight = null;
    }
  })();
  return scheduleInflight;
}

async function loadRuntimeDashboard(timeoutMs: number): Promise<WorldCupDashboardPayload> {
  if (timeoutMs >= BACKGROUND_RUNTIME_TIMEOUT_MS && runtimeDashboardInflight) return runtimeDashboardInflight;
  const request = fetchRuntimeWorldCupDashboard(timeoutMs)
    .then((payload) => {
      if (!isUsableDashboard(payload)) throw new Error('World Cup runtime dashboard missing schedule rows');
      return payload;
    });
  if (timeoutMs >= BACKGROUND_RUNTIME_TIMEOUT_MS) {
    runtimeDashboardInflight = request.finally(() => {
      runtimeDashboardInflight = null;
    });
    return runtimeDashboardInflight;
  }
  return request;
}

async function loadRuntimeIntel(): Promise<WorldCupDashboardPayload['intelligence']> {
  if (runtimeIntelInflight) return runtimeIntelInflight;
  runtimeIntelInflight = fetchRuntimeWorldCupIntel(120, BACKGROUND_RUNTIME_TIMEOUT_MS)
    .catch(() => null)
    .finally(() => {
      runtimeIntelInflight = null;
    });
  return runtimeIntelInflight;
}

function firstUsableDashboard(promises: Array<Promise<WorldCupDashboardPayload | null>>): Promise<WorldCupDashboardPayload> {
  return new Promise((resolve, reject) => {
    let pending = promises.length;
    promises.forEach((promise) => {
      promise
        .then((payload) => {
          if (isUsableDashboard(payload)) {
            resolve(payload);
            return;
          }
          pending -= 1;
          if (pending <= 0) reject(new Error('World Cup dashboard unavailable'));
        })
        .catch(() => {
          pending -= 1;
          if (pending <= 0) reject(new Error('World Cup dashboard unavailable'));
        });
    });
  });
}

export async function loadWorldCupDashboard(): Promise<WorldCupDashboardPayload> {
  const cached = readDashboardCache();
  if (cached) return cached;
  const stale = readDashboardCache({ allowStale: true });
  if (stale) return stale;
  if (dashboardInflight) return dashboardInflight;

  const runtimePromise = loadRuntimeDashboard(INITIAL_RUNTIME_TIMEOUT_MS).catch(() => null);
  const schedulePromise = loadWorldCupScheduleDashboard().catch(() => null);
  dashboardInflight = firstUsableDashboard([runtimePromise, schedulePromise])
    .then(writeDashboardCache)
    .catch(() => writeDashboardCache(buildWorldCupDashboard([], 'source-required')))
    .finally(() => {
      dashboardInflight = null;
    });

  runtimePromise.then((payload) => {
    if (isUsableDashboard(payload)) writeDashboardCache(payload);
  }).catch(() => {
    // The browser-side schedule payload remains the first-paint fallback.
  });

  return dashboardInflight;
}

export async function refreshWorldCupDashboard(current?: WorldCupDashboardPayload | null): Promise<WorldCupDashboardPayload> {
  const base = current || readDashboardCache() || await loadWorldCupScheduleDashboard();
  const [dashboardResult, intelResult] = await Promise.allSettled([
    loadRuntimeDashboard(BACKGROUND_RUNTIME_TIMEOUT_MS),
    loadRuntimeIntel(),
  ]);
  let next = base;
  if (dashboardResult.status === 'fulfilled' && isUsableDashboard(dashboardResult.value)) {
    next = dashboardResult.value;
  }
  if (intelResult.status === 'fulfilled' && intelResult.value) {
    next = mergeWorldCupRuntimeIntel(next, intelResult.value);
  }
  return writeDashboardCache(next);
}

export function applyWorldCupMarketLinks(payload: WorldCupDashboardPayload, marketGroups: MarketGroupItem[]): WorldCupDashboardPayload {
  return mergeWorldCupMarketLinks(payload, marketGroups);
}

export async function loadWorldCupDashboardWithMarketLinks(marketGroups: MarketGroupItem[] = []): Promise<WorldCupDashboardPayload> {
  return mergeWorldCupMarketLinks(await loadWorldCupDashboard(), marketGroups);
}

function mergeWorldCupMarketLinks(payload: WorldCupDashboardPayload, marketGroups: MarketGroupItem[]): WorldCupDashboardPayload {
  if (!marketGroups.length) return payload;
  const linkedText = marketGroups.map((group) => `${group.title} ${group.slug || ''} ${(group.tags || []).join(' ')}`.toLowerCase());
  return {
    ...payload,
    matches: payload.matches.map((match) => {
      const needles = [match.homeTeam, match.awayTeam, match.city, match.venue]
        .filter(Boolean)
        .map((value) => String(value).toLowerCase());
      const marketLinked = Boolean(match.marketLinked) || linkedText.some((text) => (
        text.includes('world cup') && needles.some((needle) => needle !== 'tbd' && text.includes(needle))
      ));
      return { ...match, marketLinked };
    }),
  };
}

function mergeWorldCupRuntimeIntel(payload: WorldCupDashboardPayload, intel: WorldCupDashboardPayload['intelligence']): WorldCupDashboardPayload {
  if (!intel) return payload;
  const news = Array.isArray(intel.news) && intel.news.length ? [...intel.news, ...payload.news] : payload.news;
  const weather = Array.isArray(intel.weather) && intel.weather.length ? mergeWeatherRows(payload.weather, intel.weather) : payload.weather;
  return {
    ...payload,
    cacheMode: intel.status === 'ok' ? 'remote' : payload.cacheMode,
    generatedAt: intel.generatedAt || payload.generatedAt,
    news,
    weather,
    intelligence: intel,
  };
}

function mergeWeatherRows(baseWeather: WorldCupCityWeather[], runtimeWeather: WorldCupCityWeather[]) {
  const rows = new Map(baseWeather.map((item) => [item.cityId, item]));
  runtimeWeather.forEach((item) => {
    if (item?.cityId) rows.set(item.cityId, item);
  });
  return Array.from(rows.values());
}

function buildWorldCupDashboard(matches: WorldCupMatch[], cacheMode: 'remote' | 'source-required'): WorldCupDashboardPayload {
  return {
    generatedAt: new Date().toISOString(),
    cacheMode,
    tournament: {
      id: 'fifa-world-cup-2026',
      name: 'FIFA World Cup 2026',
      startsAt: matches[0]?.kickoffUtc || '2026-06-11T19:00:00Z',
      endsAt: matches[matches.length - 1]?.kickoffUtc || '2026-07-19T19:00:00Z',
      timezone: 'Asia/Shanghai',
    },
    cities: WORLD_CUP_CITIES,
    matches,
    news: [],
    weather: [],
    rosters: [],
    odds: [],
  };
}

export function getNextWorldCupMatch(matches: WorldCupMatch[], now = new Date()) {
  return matches.find((match) => new Date(match.kickoffUtc) > now) || null;
}

export function matchCity(cities: WorldCupVenueCity[], cityId: string) {
  return cities.find((city) => city.id === cityId) || cities[0] || WORLD_CUP_CITIES[0]!;
}

export function filterWorldCupNews(content: ContentItem[], selected?: WorldCupMatch | null): WorldCupNewsItem[] {
  const terms = ['world cup', 'fifa', 'soccer', 'football', 'mexico', 'canada', 'united states'];
  const selectedTerms = selected ? [selected.homeTeam, selected.awayTeam, selected.city].map((item) => item.toLowerCase()) : [];
  const items = content
    .filter((item) => {
      const text = `${item.title || ''} ${item.summary || ''} ${item.source || ''}`.toLowerCase();
      return terms.some((term) => text.includes(term)) || selectedTerms.some((term) => text.includes(term));
    })
    .slice(0, 18)
    .map((item, index) => ({
      id: String(item.id || item.url || `content-${index}`),
      title: item.title || 'World Cup intelligence item',
      source: item.source || 'INTEL',
      url: item.url || '#',
      publishedAt: item.publishedAt || new Date().toISOString(),
      summary: item.summary || '',
      matchId: selected?.id,
    }));
  return items.slice(0, 18);
}

export function matchPolymarketMarkets(match: WorldCupMatch | null, marketGroups: MarketGroupItem[]): WorldCupPolymarketMarket[] {
  const worldCup2026Terms = ['fifa world cup', 'world cup 2026', '2026 world cup', 'fifa 2026'];
  const blockedTerms = ['club world cup', 'fifa club', 'uefa', 'champions league'];
  const teamTerms = match ? [match.homeTeam, match.awayTeam].map((item) => item.toLowerCase()).filter((item) => item && item !== 'tbd') : [];
  const venueTerms = match ? [match.city, match.venue].map((item) => item.toLowerCase()).filter(Boolean) : [];
  return marketGroups
    .map((group) => {
      const text = `${group.title} ${group.slug || ''} ${(group.tags || []).join(' ')}`.toLowerCase();
      if (blockedTerms.some((term) => text.includes(term))) return { group, confidence: 0 };
      const hasWorldCup2026 = worldCup2026Terms.some((term) => text.includes(term));
      const teamScore = teamTerms.filter((term) => text.includes(term)).length * 0.28;
      const venueScore = venueTerms.some((term) => text.includes(term)) ? 0.18 : 0;
      const baseScore = hasWorldCup2026 ? 0.45 : 0;
      const broadSoccerScore = !match && (text.includes('world cup') || text.includes('soccer') || text.includes('football')) ? 0.12 : 0;
      const confidence = Math.min(0.99, baseScore + teamScore + venueScore + broadSoccerScore);
      return { group, confidence };
    })
    .filter((item) => item.confidence >= (match ? 0.45 : 0.3))
    .sort((a, b) => b.confidence - a.confidence)
    .slice(0, 8)
    .map(({ group, confidence }) => ({
      matchId: match?.id,
      eventId: group.eventId,
      marketId: group.defaultMarketId,
      slug: group.slug,
      title: group.title,
      confidence,
      source: 'local-db',
      volume24h: numeric(group.volume24h),
      outcomes: (group.topOutcomes?.length ? group.topOutcomes : group.outcomes || []).slice(0, 4).map((outcome) => ({
        name: outcome.label || outcome.title || outcome.outcomeKey || 'Outcome',
        yesPrice: numeric(outcome.yesPrice),
        volume24h: numeric(outcome.volume24h),
      })),
    }));
}

function isPredictionMarketSnapshot(snapshot: WorldCupOddsSnapshot) {
  const providerText = `${snapshot.provider || ''} ${snapshot.source || ''}`.toLowerCase();
  return snapshot.providerType === 'prediction_market' || /polymarket|gamma|clob/.test(providerText);
}

function priceToProbability(value: unknown) {
  const parsed = numeric(value);
  if (parsed === null) return null;
  if (parsed > 1) return parsed / 100;
  if (parsed < 0) return null;
  return parsed;
}

function snapshotOutcomePrice(snapshot: WorldCupOddsSnapshot, index: number, outcomeName: string) {
  const probability = snapshot.probabilities?.find((item) => {
    const label = String(item.outcome || item.name || '').toLowerCase();
    return label === outcomeName.toLowerCase() || (/draw/i.test(label) && /draw/i.test(outcomeName));
  });
  return (
    priceToProbability(snapshot.outcomes[index]?.price) ??
    priceToProbability(snapshot.outcomes[index]?.impliedProbability) ??
    priceToProbability(probability?.price) ??
    priceToProbability(probability?.impliedProbability) ??
    priceToProbability(snapshot.outcomePrices?.[index])
  );
}

export function matchRuntimePolymarketMarkets(match: WorldCupMatch | null, odds: WorldCupOddsSnapshot[]): WorldCupPolymarketMarket[] {
  if (!match) return [];
  return odds
    .filter((snapshot) => snapshot.matchId === match.id && isPredictionMarketSnapshot(snapshot))
    .map((snapshot) => {
      const runtimeSnapshot = snapshot as WorldCupOddsSnapshot & {
        eventId?: string | number | null;
        eventSlug?: string | null;
        id?: string | number | null;
        marketTitle?: string | null;
        slug?: string | null;
        tradeUrl?: string | null;
      };
      const confidenceValue = numeric(snapshot.confidence);
      const confidence = confidenceValue === null ? 0.99 : Math.min(0.99, confidenceValue > 1 ? confidenceValue / 100 : confidenceValue);
      const title = String(runtimeSnapshot.marketTitle || snapshot.provider || `${match.homeTeam} vs ${match.awayTeam}`);
      return {
        matchId: match.id,
        eventId: runtimeSnapshot.eventId || runtimeSnapshot.id || snapshot.marketUrl || title,
        slug: runtimeSnapshot.eventSlug || runtimeSnapshot.slug || null,
        marketUrl: snapshot.marketUrl || null,
        tradeUrl: runtimeSnapshot.tradeUrl || snapshot.marketUrl || null,
        title,
        confidence,
        source: /gamma/i.test(String(snapshot.source || '')) ? 'gamma' : 'polymarket',
        volume24h: null,
        liquidity: null,
        outcomes: snapshot.outcomes.slice(0, 4).map((outcome, index) => ({
          name: outcome.name || String(snapshot.rawOutcomes?.[index] || `Outcome ${index + 1}`),
          yesPrice: snapshotOutcomePrice(snapshot, index, outcome.name),
          volume24h: null,
        })),
      } satisfies WorldCupPolymarketMarket;
    })
    .filter((market) => market.outcomes.some((outcome) => outcome.yesPrice !== null && outcome.yesPrice !== undefined));
}

export function mergeWorldCupSelectedMarkets(
  match: WorldCupMatch | null,
  marketGroups: MarketGroupItem[],
  odds: WorldCupOddsSnapshot[],
): WorldCupPolymarketMarket[] {
  const runtimeMarkets = matchRuntimePolymarketMarkets(match, odds);
  const localMarkets = matchPolymarketMarkets(match, marketGroups);
  const seen = new Set<string>();
  return [...runtimeMarkets, ...localMarkets].filter((market) => {
    const key = String(market.eventId || market.slug || market.marketUrl || market.title).toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function numeric(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
