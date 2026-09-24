import type { ContentItem, MarketGroupItem, RuntimeGeoSanctionsShockPayload, RuntimeGlobalWeatherMapPayload } from '@/types';

export type WorldCupStage =
  | 'group'
  | 'round32'
  | 'round16'
  | 'quarterfinal'
  | 'semifinal'
  | 'third_place'
  | 'final';

export type WorldCupMatchStatus = 'scheduled' | 'live' | 'finished' | 'postponed' | 'cancelled';

export type WorldCupVenueCity = {
  id: string;
  city: string;
  country: 'US' | 'CA' | 'MX';
  countryName: string;
  venue: string;
  latitude: number;
  longitude: number;
  timezone: string;
  capacity?: number;
};

export type WorldCupMatch = {
  id: string;
  fifaMatchNumber?: number;
  stage: WorldCupStage;
  group?: string;
  round: string;
  kickoffUtc: string;
  kickoffBeijing: string;
  kickoffLocal: string;
  cityId: string;
  city: string;
  venue: string;
  homeTeam: string;
  awayTeam: string;
  homeScore?: number;
  awayScore?: number;
  status: WorldCupMatchStatus;
  minute?: string;
  marketLinked?: boolean;
  oddsLinked?: boolean;
};

export type WorldCupNewsItem = {
  id: string;
  title: string;
  source: string;
  url: string;
  publishedAt: string;
  summary?: string;
  teams?: string[];
  cityId?: string;
  matchId?: string;
};

export type WorldCupRuntimeSignalTone = 'red' | 'gold' | 'blue' | 'purple' | 'gray' | 'green';

export type WorldCupRuntimeSignal = {
  id: string;
  source: string;
  title: string;
  summary: string;
  category: string;
  age: string;
  url?: string;
  tags?: Array<{ label: string; tone: WorldCupRuntimeSignalTone }>;
  accent?: WorldCupRuntimeSignalTone;
  provider?: string;
  matchId?: string | null;
  cityId?: string | null;
};

export type WorldCupIntelPayload = {
  generatedAt?: string;
  status?: string;
  cacheMode?: string;
  source?: string;
  sourceUrl?: string;
  providerStates?: Record<string, string>;
  summary?: {
    signals?: number;
    news?: number;
    weatherCities?: number;
    liveProviders?: number;
  };
  news?: WorldCupNewsItem[];
  weather?: WorldCupCityWeather[];
  signals?: WorldCupRuntimeSignal[];
};

export type WorldCupCityWeather = {
  cityId: string;
  current: {
    tempC: number;
    condition: string;
    windKph?: number;
    precipitationProbability?: number;
  };
  forecast: Array<{
    date: string;
    highC: number;
    lowC: number;
    condition: string;
    precipitationProbability?: number;
  }>;
  generatedAt: string;
};

export type WorldCupPolymarketMarket = {
  matchId?: string;
  eventId?: string | number | null;
  marketId?: number | null;
  slug?: string | null;
  marketUrl?: string | null;
  tradeUrl?: string | null;
  title: string;
  confidence: number;
  source: 'local-db' | 'gamma' | 'polymarket' | 'manual' | 'inferred';
  outcomes: Array<{
    name: string;
    yesPrice?: number | null;
    volume24h?: number | null;
  }>;
  volume24h?: number | null;
  liquidity?: number | null;
};

export type WorldCupTeamRoster = {
  team: string;
  updatedAt: string;
  players: Array<{
    name: string;
    position?: string;
    club?: string;
    number?: number;
    status?: 'confirmed' | 'probable' | 'injured' | 'reserve';
  }>;
};

export type WorldCupOddsSnapshot = {
  matchId: string;
  provider: string;
  providerType: 'traditional_sportsbook' | 'online_bookmaker' | 'exchange' | 'prediction_market';
  marketKey?: string;
  marketType: 'moneyline' | 'spread' | 'total_goals' | 'both_teams_to_score' | 'draw_no_bet' | 'double_chance' | 'advance' | 'outright' | string;
  outcomes: Array<{
    name: string;
    decimalOdds?: number;
    bestDecimalOdds?: number;
    impliedProbability?: number;
    price?: number | string | null;
    point?: number | string | null;
    bookCount?: number;
    source?: string;
    marketKey?: string;
    marketUrl?: string;
  }>;
  bookmakers?: Array<{
    key?: string;
    title: string;
    lastUpdate?: string;
    markets?: string[];
    outcomes: Array<{
      name: string;
      decimalOdds?: number;
      impliedProbability?: number;
      point?: number | string | null;
      marketKey?: string;
    }>;
  }>;
  generatedAt: string;
  probabilities?: Array<{
    outcome?: string;
    name?: string;
    price?: number | string | null;
    impliedProbability?: number | string | null;
    marketUrl?: string;
  }>;
  outcomePrices?: Array<number | string | null>;
  rawOutcomes?: Array<string>;
  bookmakerCount?: number;
  source?: string;
  sourceUrl?: string;
  marketUrl?: string;
  confidence?: number;
};

export type WorldCupDashboardPayload = {
  generatedAt: string;
  cacheMode: 'seed' | 'seeded' | 'redis' | 'sqlite' | 'stale' | 'preserved' | 'remote' | 'core' | 'live' | 'fallback' | 'source-required';
  tournament: {
    id: 'fifa-world-cup-2026';
    name: string;
    startsAt: string;
    endsAt: string;
    timezone: 'Asia/Shanghai';
  };
  cities: WorldCupVenueCity[];
  matches: WorldCupMatch[];
  news: WorldCupNewsItem[];
  weather: WorldCupCityWeather[];
  rosters: WorldCupTeamRoster[];
  odds: WorldCupOddsSnapshot[];
  providerStates?: Record<string, string>;
  bookmakerLinker?: {
    sportKey?: string;
    events?: number;
    matched?: number;
    snapshots?: number;
    markets?: string[];
    [key: string]: unknown;
  };
  marketLinker?: Record<string, unknown>;
  intelligence?: WorldCupIntelPayload | null;
};

export type WorldCupWorkspaceProps = {
  now: Date;
  marketGroups: MarketGroupItem[];
  latestContent: ContentItem[];
  weatherPayload?: RuntimeGlobalWeatherMapPayload | null;
  geoShockPayload?: RuntimeGeoSanctionsShockPayload | null;
};
