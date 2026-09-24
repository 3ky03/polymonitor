import { type ComponentChildren } from 'preact';
import { useEffect, useMemo, useState } from 'preact/hooks';
import { Panel, PanelLoading } from '@/components/Panel';
import type { PanelDefinition } from '@/types';
import type { PanelModule, PanelRuntimeContext } from '../../types';
import {
  applyWorldCupMarketLinks,
  filterWorldCupNews,
  getNextWorldCupMatch,
  loadWorldCupDashboard,
  matchCity,
  matchPolymarketMarkets,
  refreshWorldCupDashboard,
  WORLD_CUP_HOST_MATCH_COUNTS,
} from '@/workspaces/worldcup/data';
import type {
  WorldCupCityWeather,
  WorldCupDashboardPayload,
  WorldCupMatch,
  WorldCupNewsItem,
  WorldCupOddsSnapshot,
  WorldCupPolymarketMarket,
  WorldCupTeamRoster,
  WorldCupVenueCity,
} from '@/workspaces/worldcup/types';

type WorldCupHomeView =
  | 'calendar'
  | 'match-control'
  | 'win-probability'
  | 'venue-risk'
  | 'market-board'
  | 'group-advance'
  | 'team-power'
  | 'injury-load'
  | 'match-tempo'
  | 'odds-liquidity'
  | 'ref-cards'
  | 'travel-load'
  | 'news-impact'
  | 'news'
  | 'team-status'
  | 'lineup-board'
  | 'match-model'
  | 'group-table'
  | 'media-wire'
  | 'host-venue'
  | 'venue-ref'
  | 'source-audit';

type WorldCupHomePanelConfig = {
  id: string;
  title: string;
  description: string;
  view: WorldCupHomeView;
  size?: PanelDefinition['size'];
  question: string;
};

type SignalTone = 'red' | 'gold' | 'blue' | 'purple' | 'gray' | 'green';

type SignalRow = {
  id: string;
  source: string;
  title: string;
  summary: string;
  age?: string;
  tone?: SignalTone;
  tags?: Array<{ label: string; tone?: SignalTone }>;
  url?: string;
};

type WorldCupHomeModel = {
  payload: WorldCupDashboardPayload;
  now: Date;
  selectedMatch: WorldCupMatch | null;
  selectMatch: (matchId: string) => void;
  selectedCity: WorldCupVenueCity;
  selectedWeather: WorldCupCityWeather | null;
  selectedOdds: WorldCupOddsSnapshot[];
  selectedMarkets: WorldCupPolymarketMarket[];
  news: WorldCupNewsItem[];
  signals: SignalRow[];
};

let backgroundRefreshInflight: Promise<WorldCupDashboardPayload> | null = null;
const WORLD_CUP_HOME_SELECTED_MATCH_STORAGE_KEY = 'polydata:worldcup-home-selected-match:v1';
const WORLD_CUP_HOME_SELECTED_MATCH_EVENT = 'polydata:worldcup-home-selected-match';

function readWorldCupHomeSelectedMatchId() {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage.getItem(WORLD_CUP_HOME_SELECTED_MATCH_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeWorldCupHomeSelectedMatchId(matchId: string) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(WORLD_CUP_HOME_SELECTED_MATCH_STORAGE_KEY, matchId);
  } catch {
    // Selection still works for the current page through the custom event.
  }
  window.dispatchEvent(new CustomEvent(WORLD_CUP_HOME_SELECTED_MATCH_EVENT, { detail: matchId }));
}

function statusBadge(payload?: WorldCupDashboardPayload | null) {
  const mode = String(payload?.cacheMode || '').toLowerCase();
  if (!payload) return 'LOAD';
  if (mode.includes('stale') || mode.includes('preserved')) return 'STALE';
  if (mode.includes('source')) return 'SEED';
  return 'LIVE';
}

function panelStatus(payload?: WorldCupDashboardPayload | null) {
  const mode = String(payload?.cacheMode || '').toLowerCase();
  return mode.includes('stale') || mode.includes('source') ? 'muted' : 'live';
}

function formatDateTime(value?: string | null) {
  if (!value) return '--';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Shanghai',
  }).format(date);
}

function formatCompact(value?: number | string | null) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  if (Math.abs(numeric) >= 1_000_000) return `${(numeric / 1_000_000).toFixed(1)}M`;
  if (Math.abs(numeric) >= 1_000) return `${(numeric / 1_000).toFixed(1)}K`;
  return String(Math.round(numeric));
}

type WeatherWithSource = WorldCupCityWeather & { source?: string };

function venueRiskScore(temp: number, precipitation: number, wind: number, matchCount: number) {
  return Math.max(5, Math.min(96, Math.round((temp > 27 ? 18 : 6) + precipitation * 0.35 + wind * 0.7 + matchCount * 1.4)));
}

function venueRiskBand(score: number) {
  if (score >= 68) return { key: 'high', label: 'HIGH STRESS' };
  if (score >= 42) return { key: 'watch', label: 'WATCH STRESS' };
  return { key: 'low', label: 'LOW STRESS' };
}

function venueWeatherSource(weather: WorldCupCityWeather, payload: WorldCupDashboardPayload) {
  return (weather as WeatherWithSource).source || payload.intelligence?.source?.split('/').pop()?.trim() || 'Weather feed';
}

function percentLabel(value?: number | string | null, digits = 1) {
  if (value === null || value === undefined || value === '') return '--';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  const pct = numeric >= 0 && numeric <= 1 ? numeric * 100 : numeric;
  return `${pct.toFixed(digits)}%`;
}

function percentFromUnknown(value?: number | string | null) {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return numeric >= 0 && numeric <= 1 ? numeric * 100 : numeric;
}

function probabilityWidth(value?: number | string | null) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '0%';
  const pct = numeric >= 0 && numeric <= 1 ? numeric * 100 : numeric;
  return `${Math.max(3, Math.min(100, pct))}%`;
}

function daysBetween(a?: string, b?: string) {
  const left = new Date(a || '').getTime();
  const right = new Date(b || '').getTime();
  if (!Number.isFinite(left) || !Number.isFinite(right)) return null;
  return Math.round(Math.abs(left - right) / 86_400_000);
}

function scoreText(match: WorldCupMatch) {
  if (match.homeScore == null || match.awayScore == null) return 'vs';
  return `${match.homeScore}-${match.awayScore}`;
}

function finalResultOutcome(match: WorldCupMatch | null) {
  if (!match || match.status !== 'finished' || match.homeScore == null || match.awayScore == null) return null;
  if (match.homeScore > match.awayScore) return match.homeTeam;
  if (match.homeScore < match.awayScore) return match.awayTeam;
  return 'Draw';
}

function stageLabel(value?: string | null) {
  return String(value || 'group').replace(/_/g, ' ').toUpperCase();
}

function outcomeMatchesTeam(name: string | undefined, team: string) {
  const left = String(name || '').trim().toLowerCase();
  const right = String(team || '').trim().toLowerCase();
  if (!left || !right) return false;
  if (right === 'draw') return /\b(draw|tie|x)\b/i.test(left);
  if (/\b(draw|tie|x)\b/i.test(left)) return false;
  if (left === right) return true;
  const leftTokens = new Set(left.split(/[^a-z0-9]+/).filter(Boolean));
  const rightTokens = right.split(/[^a-z0-9]+/).filter((part) => part.length > 1);
  return rightTokens.some((token) => leftTokens.has(token));
}

function normalizedTeamText(value?: string | null) {
  return String(value || '')
    .toLowerCase()
    .replace(/\bcabo verde\b/g, 'cape verde')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function isDerivedWorldCupMarketTitle(value?: string | null) {
  return /\b(more markets|exact score|corners?|cards?|goals?|o\/u|over\/under|total|spread|handicap|1st half|2nd half)\b/i.test(String(value || ''));
}

function isHeadToHeadMarket(market: WorldCupPolymarketMarket, match: WorldCupMatch) {
  const title = normalizedTeamText(market.title);
  if (!title || isDerivedWorldCupMarketTitle(market.title)) return false;
  const home = normalizedTeamText(match.homeTeam);
  const away = normalizedTeamText(match.awayTeam);
  if (!title.includes(home) || !title.includes(away)) return false;
  const names = (market.outcomes || []).map((outcome) => normalizedTeamText(outcome.name));
  const hasHome = names.some((name) => name === home || name.includes(home));
  const hasAway = names.some((name) => name === away || name.includes(away));
  const hasDraw = names.some((name) => /\bdraw\b|\btie\b|\bx\b/.test(name));
  return hasHome && hasAway && hasDraw;
}

function snapshotOutcomePercent(snapshot: WorldCupOddsSnapshot | undefined, team: string) {
  if (!snapshot) return null;
  const direct = (snapshot.outcomes || []).find((outcome) => outcomeMatchesTeam(outcome.name, team));
  if (direct) return percentFromUnknown(direct.impliedProbability ?? direct.price ?? null);
  const probability = (snapshot.probabilities || []).find((row) => outcomeMatchesTeam(row.outcome || row.name, team));
  return percentFromUnknown(probability?.impliedProbability ?? probability?.price ?? null);
}

function toneFromText(value: string): SignalTone {
  const text = value.toLowerCase();
  if (/injur|risk|storm|delay|required|not matched|missing/.test(text)) return 'red';
  if (/market|odds|poly|prob/.test(text)) return 'purple';
  if (/weather|venue|host|city/.test(text)) return 'blue';
  if (/group|kickoff|calendar|schedule/.test(text)) return 'gold';
  if (/ok|live|linked|ready|seed/.test(text)) return 'green';
  return 'gray';
}

function sourceRows(rows: Array<{ source: string; status: string; detail: string }>) {
  return (
    <div className="wm-worldcup-home-source-list">
      {rows.map((row) => (
        <article key={`${row.source}-${row.status}`}>
          <span>{row.source}</span>
          <strong className={`tone-${toneFromText(row.status)}`}>{row.status}</strong>
          <em>{row.detail}</em>
        </article>
      ))}
    </div>
  );
}

function EmptyState({ detail, rows }: { detail: string; rows?: Array<{ source: string; status: string; detail: string }> }) {
  return (
    <div className="wm-worldcup-home-empty">
      <strong>SOURCE REQUIRED</strong>
      <p>{detail}</p>
      {rows?.length ? sourceRows(rows) : null}
    </div>
  );
}

function tagList(tags?: SignalRow['tags']) {
  return (
    <>
      {(tags || []).slice(0, 3).map((tag) => (
        <b className={`tone-${tag.tone || toneFromText(tag.label)}`} key={`${tag.label}-${tag.tone || 'tag'}`}>{tag.label}</b>
      ))}
    </>
  );
}

function SignalList({ rows, empty }: { rows: SignalRow[]; empty: ComponentChildren }) {
  if (!rows.length) return <>{empty}</>;
  return (
    <div className="wm-worldcup-home-signal-list">
      {rows.slice(0, 8).map((row) => {
        const body = (
          <>
            <div className="wm-worldcup-home-row-meta">
              <span>{row.source}</span>
              {tagList(row.tags)}
            </div>
            <strong>{row.title}</strong>
            <em>{row.summary}</em>
          </>
        );
        return row.url ? (
          <a className={`wm-worldcup-home-signal-row tone-${row.tone || 'blue'}`} href={row.url} key={row.id} target="_blank" rel="noreferrer">{body}</a>
        ) : (
          <article className={`wm-worldcup-home-signal-row tone-${row.tone || 'blue'}`} key={row.id}>{body}</article>
        );
      })}
    </div>
  );
}

function buildSignals(model: WorldCupHomeModel): SignalRow[] {
  const { payload, selectedMatch, selectedCity, selectedWeather, selectedMarkets, selectedOdds, news } = model;
  const runtimeSignals = (payload.intelligence?.signals || []).slice(0, 8).map((item, index) => ({
    id: item.id || `runtime-${index}`,
    source: item.source || item.provider || 'WORLD CUP',
    title: item.title || 'World Cup signal',
    summary: item.summary || 'Runtime World Cup intelligence signal.',
    age: item.age || payload.intelligence?.generatedAt,
    tone: (item.accent as SignalTone) || toneFromText(item.category || ''),
    tags: (item.tags || []).map((tag) => ({ label: tag.label, tone: tag.tone as SignalTone })),
    url: item.url,
  }));
  const fallback: SignalRow[] = [
    selectedMatch ? {
      id: 'selected-match',
      source: 'MATCH DESK',
      title: `${selectedMatch.homeTeam} vs ${selectedMatch.awayTeam}`,
      summary: `${selectedMatch.kickoffBeijing} BJT · ${selectedMatch.city} · ${selectedMatch.venue}`,
      tone: 'green',
      tags: [{ label: stageLabel(selectedMatch.group || selectedMatch.stage), tone: 'gold' }],
    } : null,
    {
      id: 'selected-city',
      source: 'HOST OPS',
      title: `${selectedCity.city}: ${WORLD_CUP_HOST_MATCH_COUNTS[selectedCity.id] || 0} planned matches`,
      summary: `${selectedCity.venue} · ${selectedWeather ? `${selectedWeather.current.tempC}C ${selectedWeather.current.condition}` : 'weather pending'}`,
      tone: selectedWeather ? toneFromText(selectedWeather.current.condition) : 'blue',
      tags: [{ label: selectedCity.country, tone: 'blue' }],
    },
    {
      id: 'market-links',
      source: 'POLYDATA',
      title: `${selectedMarkets.length} linked Polymarket candidates`,
      summary: `${selectedOdds.length} odds snapshots · ${news.length} World Cup news rows`,
      tone: selectedMarkets.length ? 'purple' : 'gray',
      tags: [{ label: selectedMarkets.length ? 'LINKED' : 'WATCH', tone: selectedMarkets.length ? 'green' : 'gray' }],
    },
  ].filter(Boolean) as SignalRow[];
  return [...runtimeSignals, ...fallback];
}

function useWorldCupPayload() {
  const [payload, setPayload] = useState<WorldCupDashboardPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadWorldCupDashboard()
      .then((nextPayload) => {
        if (cancelled) return;
        setPayload(nextPayload);
        setError(null);
        if (!backgroundRefreshInflight) {
          backgroundRefreshInflight = refreshWorldCupDashboard(nextPayload)
            .finally(() => {
              backgroundRefreshInflight = null;
            });
        }
        void backgroundRefreshInflight
          .then((refreshed) => {
            if (!cancelled) setPayload(refreshed);
          })
          .catch(() => undefined);
      })
      .catch((loadError) => {
        if (!cancelled) setError(loadError instanceof Error ? loadError.message : 'World Cup payload unavailable.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { payload, loading, error };
}

function useWorldCupModel(ctx: PanelRuntimeContext) {
  const { payload, loading, error } = useWorldCupPayload();
  const now = useMemo(() => new Date(), []);
  const [selectedMatchId, setSelectedMatchId] = useState<string | null>(() => readWorldCupHomeSelectedMatchId());
  useEffect(() => {
    const onSelectedMatch = (event: Event) => {
      const nextMatchId = (event as CustomEvent<string>).detail || readWorldCupHomeSelectedMatchId();
      setSelectedMatchId(nextMatchId || null);
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key === WORLD_CUP_HOME_SELECTED_MATCH_STORAGE_KEY) setSelectedMatchId(event.newValue || null);
    };
    window.addEventListener(WORLD_CUP_HOME_SELECTED_MATCH_EVENT, onSelectedMatch);
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener(WORLD_CUP_HOME_SELECTED_MATCH_EVENT, onSelectedMatch);
      window.removeEventListener('storage', onStorage);
    };
  }, []);
  const linkedPayload = useMemo(
    () => payload ? applyWorldCupMarketLinks(payload, ctx.marketGroups || []) : null,
    [ctx.marketGroups, payload],
  );
  const selectedMatch = useMemo(
    () => {
      if (!linkedPayload) return null;
      return linkedPayload.matches.find((match) => match.id === selectedMatchId)
        || getNextWorldCupMatch(linkedPayload.matches, now)
        || linkedPayload.matches[0]
        || null;
    },
    [linkedPayload, now, selectedMatchId],
  );
  const selectMatch = (matchId: string) => {
    setSelectedMatchId(matchId);
    writeWorldCupHomeSelectedMatchId(matchId);
  };
  const selectedCity = linkedPayload ? matchCity(linkedPayload.cities, selectedMatch?.cityId || linkedPayload.cities[0]?.id || '') : null;
  const selectedWeather = linkedPayload?.weather.find((item) => item.cityId === selectedCity?.id) || null;
  const selectedOdds = linkedPayload?.odds.filter((item) => item.matchId === selectedMatch?.id) || [];
  const selectedMarkets = matchPolymarketMarkets(selectedMatch, ctx.marketGroups || []);
  const news = useMemo(() => {
    if (!linkedPayload) return [];
    const runtimeNews = linkedPayload.news || [];
    const contentNews = filterWorldCupNews(ctx.latestContent || [], selectedMatch);
    const seen = new Set<string>();
    return [...runtimeNews, ...contentNews].filter((item) => {
      const key = `${item.source}:${item.title}`.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).slice(0, 24);
  }, [ctx.latestContent, linkedPayload, selectedMatch]);

  const model = linkedPayload && selectedCity ? {
    payload: linkedPayload,
    now,
    selectedMatch,
    selectMatch,
    selectedCity,
    selectedWeather,
    selectedOdds,
    selectedMarkets,
    news,
    signals: [] as SignalRow[],
  } : null;
  if (model) model.signals = buildSignals(model);
  return { model, loading, error };
}

function countForView(view: WorldCupHomeView, model: WorldCupHomeModel) {
  const { payload, selectedMarkets, selectedOdds, news, signals, selectedWeather } = model;
  if (view === 'calendar') return payload.matches.length;
  if (view === 'market-board') return selectedMarkets.length;
  if (view === 'odds-liquidity' || view === 'win-probability') return selectedOdds.length + selectedMarkets.length;
  if (view === 'news' || view === 'news-impact') return news.length;
  if (view === 'host-venue') return payload.weather.length || payload.cities.length;
  if (view === 'source-audit') return 7;
  if (view === 'venue-risk') {
    if (!selectedWeather) return 0;
    const matchCount = Math.max(WORLD_CUP_HOST_MATCH_COUNTS[model.selectedCity.id] || 0, payload.matches.filter((match) => match.cityId === model.selectedCity.id).length);
    return venueRiskScore(selectedWeather.current.tempC, selectedWeather.current.precipitationProbability || 0, selectedWeather.current.windKph || 0, matchCount);
  }
  if (view === 'group-table' || view === 'group-advance') return payload.matches.filter((match) => match.group).length;
  if (view === 'team-power') return payload.rosters.length || selectedOdds.length || selectedMarkets.length;
  if (view === 'team-status' || view === 'injury-load') return payload.rosters.length || news.length || (model.selectedMatch ? 2 : 0);
  if (view === 'lineup-board') return payload.rosters.length || (model.selectedMatch ? 2 : 0);
  if (view === 'ref-cards') return signals.filter((row) => /ref|card|risk/i.test(`${row.source} ${row.title}`)).length + (model.selectedMatch ? 2 : 0);
  if (view === 'travel-load') return signals.filter((row) => /city|host|venue|travel/i.test(`${row.source} ${row.title}`)).length || (model.selectedMatch ? 2 : 0);
  return signals.length;
}

function matchRows(matches: WorldCupMatch[], selectedMatch: WorldCupMatch | null, onSelectMatch?: (matchId: string) => void) {
  return (
    <div className="wm-worldcup-home-match-list">
      {matches.slice(0, 24).map((match) => (
        <article
          aria-pressed={match.id === selectedMatch?.id}
          className={match.id === selectedMatch?.id ? 'active selectable' : 'selectable'}
          key={match.id}
          onClick={() => onSelectMatch?.(match.id)}
          onKeyDown={(event) => {
            if (!onSelectMatch || (event.key !== 'Enter' && event.key !== ' ')) return;
            event.preventDefault();
            onSelectMatch(match.id);
          }}
          role="button"
          tabIndex={0}
        >
          <span>
            <strong>{formatDateTime(match.kickoffUtc).split(',').pop()?.trim() || '--'}</strong>
            <em>#{match.fifaMatchNumber || '--'}</em>
          </span>
          <div>
            <small>{match.group || stageLabel(match.stage)} · {match.round}</small>
            <strong>{match.homeTeam} <i>{scoreText(match)}</i> {match.awayTeam}</strong>
            <em>{match.kickoffBeijing} · {match.city} · {match.venue}</em>
          </div>
        </article>
      ))}
    </div>
  );
}

function groupNames(matches: WorldCupMatch[]) {
  const names = Array.from(new Set(matches.map((match) => match.group).filter(Boolean))) as string[];
  return names.length ? names : ['Group A'];
}

function groupRows(matches: WorldCupMatch[], group: string) {
  const teams = new Map<string, { team: string; played: number; gf: number; ga: number; pts: number }>();
  matches.filter((match) => match.group === group).forEach((match) => {
    [match.homeTeam, match.awayTeam].forEach((team) => {
      if (!teams.has(team)) teams.set(team, { team, played: 0, gf: 0, ga: 0, pts: 0 });
    });
    if (match.homeScore == null || match.awayScore == null) return;
    const home = teams.get(match.homeTeam)!;
    const away = teams.get(match.awayTeam)!;
    home.played += 1;
    away.played += 1;
    home.gf += match.homeScore;
    home.ga += match.awayScore;
    away.gf += match.awayScore;
    away.ga += match.homeScore;
    if (match.homeScore > match.awayScore) home.pts += 3;
    else if (match.homeScore < match.awayScore) away.pts += 3;
    else {
      home.pts += 1;
      away.pts += 1;
    }
  });
  return Array.from(teams.values()).sort((a, b) => b.pts - a.pts || (b.gf - b.ga) - (a.gf - a.ga) || a.team.localeCompare(b.team));
}

function GroupPanel({ model, mode }: { model: WorldCupHomeModel; mode: 'advance' | 'table' }) {
  const groups = groupNames(model.payload.matches);
  const [group, setGroup] = useState(groups[0] || 'Group A');
  const rows = groupRows(model.payload.matches, group);
  const fixtures = model.payload.matches.filter((match) => match.group === group);
  return (
    <>
      <div className="wm-worldcup-home-tabs">
        {groups.slice(0, 12).map((item) => (
          <button className={item === group ? 'active' : ''} key={item} type="button" onClick={() => setGroup(item)}>{item.replace('Group ', '')}</button>
        ))}
      </div>
      <div className="wm-worldcup-home-table">
        <header><span>TEAM</span><span>P</span><span>GD</span><span>PTS</span></header>
        {rows.map((row, index) => (
          <div key={row.team}>
            <strong>{index + 1}. {row.team}</strong>
            <span>{row.played}</span>
            <span>{row.gf - row.ga}</span>
            <b>{row.pts}</b>
          </div>
        ))}
      </div>
      {mode === 'table' ? matchRows(fixtures, model.selectedMatch, model.selectMatch) : (
        <div className="wm-worldcup-home-metric-strip">
          <span><em>GROUP</em><strong>{group.replace('Group ', '')}</strong></span>
          <span><em>FIX</em><strong>{fixtures.length}</strong></span>
          <span><em>PLAYED</em><strong>{rows.reduce((sum, row) => sum + row.played, 0) / 2}</strong></span>
        </div>
      )}
    </>
  );
}

function MarketBoard({ model }: { model: WorldCupHomeModel }) {
  const totalVolume = model.selectedMarkets.reduce((sum, market) => sum + (market.volume24h || 0), 0);
  if (!model.selectedMarkets.length) {
    return (
      <EmptyState
        detail="No verified Polymarket/local-db market is linked to the selected fixture."
        rows={[{ source: 'Polymarket local DB / Gamma', status: 'not matched', detail: 'real outcomes and volume required' }]}
      />
    );
  }
  return (
    <>
      <div className="wm-worldcup-home-metric-strip">
        <span><em>24H VOL</em><strong>{formatCompact(totalVolume)}</strong></span>
        <span><em>LINKS</em><strong>{model.selectedMarkets.length}</strong></span>
        <span><em>ODDS</em><strong>{model.selectedOdds.length}</strong></span>
      </div>
      <div className="wm-worldcup-home-card-list">
        {model.selectedMarkets.slice(0, 5).map((market) => (
          <article key={`${market.eventId || market.title}`}>
            <div className="wm-worldcup-home-row-meta"><span>{market.source}</span><b className="tone-purple">{Math.round(market.confidence * 100)} CONF</b></div>
            <strong>{market.title}</strong>
            <div className="wm-worldcup-home-prob-grid">
              {market.outcomes.slice(0, 3).map((outcome) => (
                <span key={outcome.name}>
                  <em>{outcome.name}</em>
                  <b>{percentLabel(outcome.yesPrice)}</b>
                  <i style={{ width: probabilityWidth(outcome.yesPrice) }} />
                </span>
              ))}
            </div>
          </article>
        ))}
      </div>
    </>
  );
}

type WorldCupOddsTab = 'edge' | string;

function oddsSnapshotKey(snapshot: WorldCupOddsSnapshot) {
  return String(snapshot.marketKey || snapshot.marketType || 'book').toLowerCase();
}

function oddsMarketShortLabel(snapshot: WorldCupOddsSnapshot | { marketKey?: string; marketType?: string }) {
  const key = String(snapshot.marketKey || snapshot.marketType || '').toLowerCase();
  if (key === 'h2h' || key === 'moneyline') return '1X2';
  if (key === 'spreads' || key === 'spread') return 'AH';
  if (key === 'totals' || key === 'total_goals') return 'O/U';
  if (key === 'btts' || key === 'both_teams_to_score') return 'BTTS';
  if (key === 'draw_no_bet') return 'DNB';
  if (key === 'double_chance') return 'DC';
  return key ? key.toUpperCase().slice(0, 8) : 'BOOK';
}

function oddsMarketTitle(snapshot: WorldCupOddsSnapshot) {
  const label = oddsMarketShortLabel(snapshot);
  if (label === 'AH') return 'Asian handicap';
  if (label === 'O/U') return 'Goals total';
  if (label === '1X2') return 'Full-time result';
  if (label === 'BTTS') return 'Both teams score';
  if (label === 'DNB') return 'Draw no bet';
  if (label === 'DC') return 'Double chance';
  return label;
}

function lineLabel(value?: number | string | null) {
  if (value === null || value === undefined || value === '') return '--';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  const text = Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
  return numeric > 0 ? `+${text}` : text;
}

function oddsPriceLabel(value?: number | string | null) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return numeric.toFixed(2);
}

function latestBookUpdate(snapshot: WorldCupOddsSnapshot) {
  const values = (snapshot.bookmakers || [])
    .map((book) => book.lastUpdate)
    .filter(Boolean)
    .sort();
  return values.length ? formatDateTime(values[values.length - 1]) : formatDateTime(snapshot.generatedAt);
}

function BookmakerMicroBars({ snapshot }: { snapshot: WorldCupOddsSnapshot }) {
  const rows = (snapshot.outcomes || []).slice(0, 6);
  if (!rows.length) return null;
  return (
    <div className="wm-worldcup-book-bars" aria-hidden="true">
      {rows.map((row) => (
        <span key={`${row.name}-${row.point ?? 'x'}`}>
          <i style={{ width: probabilityWidth(row.impliedProbability) }} />
        </span>
      ))}
    </div>
  );
}

function BookmakerMarketBoard({ snapshot }: { snapshot: WorldCupOddsSnapshot }) {
  const bookmakerRows = (snapshot.bookmakers || []).slice(0, 8);
  return (
    <div className="wm-worldcup-bookmaker-board">
      <div className="wm-worldcup-bookmaker-hero">
        <span>
          <em>{oddsMarketShortLabel(snapshot)}</em>
          <strong>{oddsMarketTitle(snapshot)}</strong>
        </span>
        <span>
          <em>BOOKS</em>
          <strong>{snapshot.bookmakerCount || bookmakerRows.length || '--'}</strong>
        </span>
        <span>
          <em>UPDATE</em>
          <strong>{latestBookUpdate(snapshot)}</strong>
        </span>
      </div>
      <BookmakerMicroBars snapshot={snapshot} />
      <div className="wm-worldcup-line-grid">
        {(snapshot.outcomes || []).slice(0, 8).map((outcome) => (
          <article key={`${outcome.name}-${outcome.point ?? 'line'}`}>
            <div>
              <span>{outcome.name}</span>
              <strong>{outcome.point == null ? oddsPriceLabel(outcome.decimalOdds) : lineLabel(outcome.point)}</strong>
            </div>
            <b>{outcome.point == null ? percentLabel(outcome.impliedProbability) : oddsPriceLabel(outcome.decimalOdds)}</b>
            <em>{outcome.bookCount || 0} books</em>
            <i style={{ width: probabilityWidth(outcome.impliedProbability) }} />
          </article>
        ))}
      </div>
      <div className="wm-worldcup-bookmaker-list">
        {bookmakerRows.map((book) => (
          <article key={`${snapshot.matchId}-${oddsSnapshotKey(snapshot)}-${book.key || book.title}`}>
            <span>{book.title}</span>
            <div>
              {(book.outcomes || []).slice(0, 3).map((outcome) => (
                <b key={`${book.title}-${outcome.name}-${outcome.point ?? 'p'}`}>
                  {outcome.name}{outcome.point == null ? '' : ` ${lineLabel(outcome.point)}`} <strong>{oddsPriceLabel(outcome.decimalOdds)}</strong>
                </b>
              ))}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

function WinProbability({ model }: { model: WorldCupHomeModel }) {
  const match = model.selectedMatch;
  if (!match) return <EmptyState detail="No selected match is available." />;
  const [activeTab, setActiveTab] = useState<WorldCupOddsTab>('edge');
  const settledOutcome = finalResultOutcome(match);
  if (settledOutcome) {
    const source = String((match as WorldCupMatch & { scoreSource?: string }).scoreSource || 'ESPN scoreboard');
    return (
      <>
        <div className="wm-worldcup-odds-switch">
          <button className="active" type="button">FINAL</button>
        </div>
        <div className="wm-worldcup-home-prob-table">
          <header><span>OUTCOME</span><span>SCORE</span><span>RESULT</span><span>SOURCE</span></header>
          {[match.homeTeam, 'Draw', match.awayTeam].map((name) => {
            const won = name === settledOutcome;
            const score = name === match.homeTeam ? match.homeScore : name === match.awayTeam ? match.awayScore : scoreText(match);
            return (
              <div className={won ? 'settled-winner' : 'source-missing'} key={name}>
                <strong>{name}</strong>
                <span><b>{score}</b><i style={{ width: won ? '100%' : '0%' }} /></span>
                <span><b>{won ? 'WIN' : '--'}</b><i style={{ width: won ? '100%' : '0%' }} /></span>
                <em className={won ? 'tone-green' : ''}>{won ? '100%' : '--'}</em>
              </div>
            );
          })}
        </div>
        <div className="wm-worldcup-home-source-note">
          <b>BOOK SOURCE</b>
          <span>finished match; bookmaker odds are closed by source, result verified from {source}</span>
        </div>
      </>
    );
  }
  const outcomes = [match.homeTeam, 'Draw', match.awayTeam];
  const bookSnapshots = model.selectedOdds.filter((row) => row.providerType !== 'prediction_market');
  const book = bookSnapshots.find((row) => oddsSnapshotKey(row) === 'h2h' || row.marketType === 'moneyline') || bookSnapshots[0];
  const prediction = model.selectedOdds.find((row) => row.providerType === 'prediction_market' || /polymarket/i.test(row.provider || row.source || ''));
  const headToHeadMarket = model.selectedMarkets.find((market) => isHeadToHeadMarket(market, match));
  const marketOutcomes = (headToHeadMarket ? [headToHeadMarket] : model.selectedMarkets).flatMap((market) => market.outcomes || []);
  const rows = outcomes.map((name) => {
    const marketOutcome = marketOutcomes.find((outcome) => outcomeMatchesTeam(outcome.name, name));
    const poly = marketOutcome?.yesPrice == null ? snapshotOutcomePercent(prediction, name) : percentFromUnknown(marketOutcome.yesPrice);
    const bookProb = snapshotOutcomePercent(book, name);
    return { name, poly, book: bookProb, edge: poly != null && bookProb != null ? poly - bookProb : null };
  });
  const hasAnyPrice = rows.some((row) => row.poly != null || row.book != null);
  if (!hasAnyPrice) {
    return (
      <EmptyState
        detail="Win probabilities require real Polymarket outcome prices, bookmaker probabilities, or both."
        rows={[
          { source: 'Polymarket local DB / Gamma', status: model.selectedMarkets.length ? 'linked without prices' : 'not matched', detail: 'yesPrice per outcome' },
          { source: 'Bookmaker odds API', status: model.selectedOdds.length ? 'available' : 'not connected', detail: 'impliedProbability per outcome' },
        ]}
      />
    );
  }
  const bookState = model.payload.providerStates?.bookmakerOdds || (book ? 'ok' : 'missing');
  const selectedSnapshot = bookSnapshots.find((snapshot) => oddsSnapshotKey(snapshot) === activeTab);
  return (
    <>
      <div className="wm-worldcup-odds-switch">
        <button className={activeTab === 'edge' ? 'active' : ''} type="button" onClick={() => setActiveTab('edge')}>EDGE</button>
        {bookSnapshots.slice(0, 6).map((snapshot) => {
          const key = oddsSnapshotKey(snapshot);
          return (
            <button className={activeTab === key ? 'active' : ''} key={`${snapshot.matchId}-${key}`} type="button" onClick={() => setActiveTab(key)}>
              {oddsMarketShortLabel(snapshot)}
              <span>{snapshot.bookmakerCount || 0}</span>
            </button>
          );
        })}
      </div>
      {activeTab === 'edge' || !selectedSnapshot ? (
        <div className="wm-worldcup-home-prob-table">
          <header><span>OUTCOME</span><span>POLY</span><span>BOOK</span><span>EDGE</span></header>
          {rows.map((row) => (
            <div className={row.poly == null && row.book == null ? 'source-missing' : ''} key={row.name}>
              <strong>{row.name}</strong>
              <span><b>{percentLabel(row.poly)}</b><i style={{ width: probabilityWidth(row.poly) }} /></span>
              <span><b>{percentLabel(row.book)}</b><i style={{ width: probabilityWidth(row.book) }} /></span>
              <em className={row.edge == null ? '' : row.edge >= 0 ? 'tone-green' : 'tone-red'}>{row.edge == null ? '--' : `${row.edge >= 0 ? '+' : ''}${row.edge.toFixed(1)}%`}</em>
            </div>
          ))}
        </div>
      ) : (
        <BookmakerMarketBoard snapshot={selectedSnapshot} />
      )}
      {!book ? (
        <div className="wm-worldcup-home-source-note">
          <b>BOOK SOURCE</b>
          <span>{`bookmakerOdds ${bookState}; edge waits for bookmaker probabilities`}</span>
        </div>
      ) : null}
    </>
  );
}

function VenueRisk({ model }: { model: WorldCupHomeModel }) {
  const weather = model.selectedWeather;
  if (!weather) {
    return <EmptyState detail="Venue risk is waiting for live weather rows." rows={[{ source: 'Open-Meteo runtime weather', status: 'required', detail: 'temperature, wind and precipitation probability by host city' }]} />;
  }
  const matchCount = Math.max(WORLD_CUP_HOST_MATCH_COUNTS[model.selectedCity.id] || 0, model.payload.matches.filter((match) => match.cityId === model.selectedCity.id).length);
  const temp = weather.current.tempC;
  const precipitation = weather.current.precipitationProbability || 0;
  const wind = weather.current.windKph || 0;
  const risk = venueRiskScore(temp, precipitation, wind, matchCount);
  const band = venueRiskBand(risk);
  const source = venueWeatherSource(weather, model.payload);
  const metrics = [
    { label: 'TEMP', value: temp, max: 36, tone: 'gold', unit: 'C', note: 'ambient' },
    { label: 'PRECIP', value: precipitation, max: 12, tone: 'blue', unit: 'mm', note: 'current' },
    { label: 'WIND', value: wind, max: 40, tone: 'purple', unit: 'kph', note: '10m speed' },
    { label: 'LOAD', value: matchCount * 6, max: 100, tone: 'green', unit: '%', note: `${matchCount} matches` },
  ] as const;
  return (
    <div className="wm-worldcup-venue-risk-stack">
      <section className={`wm-worldcup-risk-hero risk-${band.key}`}>
        <div className="wm-worldcup-risk-ring" style={{ '--risk-score': `${risk}%` }}>
          <span>{risk}</span>
        </div>
        <div>
          <em>{model.selectedCity.city}</em>
          <strong>{band.label}</strong>
          <b>{weather.current.condition}</b>
        </div>
      </section>
      <div className="wm-worldcup-risk-meta">
        <span><em>SOURCE</em><strong>{source}</strong></span>
        <span><em>UPDATED</em><strong>{formatDateTime(weather.generatedAt)}</strong></span>
      </div>
      <div className="wm-worldcup-risk-metric-grid">
        {metrics.map((metric) => (
          <span className={metric.tone} key={metric.label}>
            <em>{metric.label}</em>
            <strong>{metric.value}{metric.unit}</strong>
            <small>{metric.note}</small>
            <i style={{ width: `${Math.max(4, Math.min(100, (Number(metric.value) / Number(metric.max)) * 100))}%` }} />
          </span>
        ))}
      </div>
    </div>
  );
}

function NewsList({ rows, impact = false }: { rows: WorldCupNewsItem[]; impact?: boolean }) {
  if (!rows.length) return <EmptyState detail="No World Cup-linked news rows are available yet." />;
  return (
    <div className={impact ? 'wm-worldcup-home-impact-list' : 'wm-worldcup-home-news-list'}>
      {rows.slice(0, impact ? 10 : 16).map((item) => {
        const tone = toneFromText(`${item.title} ${item.summary || ''}`);
        return (
          <a href={item.url || '#'} key={item.id} target={item.url === '#' ? undefined : '_blank'} rel="noreferrer">
            <div className="wm-worldcup-home-row-meta"><span>{item.source}</span><b className={`tone-${tone}`}>{impact ? tone.toUpperCase() : 'NEWS'}</b></div>
            <strong>{item.title}</strong>
            <em>{item.summary || 'World Cup-linked market context.'}</em>
          </a>
        );
      })}
    </div>
  );
}

function RosterPanel({ model, mode }: { model: WorldCupHomeModel; mode: 'power' | 'status' | 'injury' }) {
  const teams = model.selectedMatch ? [model.selectedMatch.homeTeam, model.selectedMatch.awayTeam] : model.payload.rosters.slice(0, 2).map((roster) => roster.team);
  const rosters = model.payload.rosters.filter((roster) => teams.includes(roster.team));
  if (!rosters.length) {
    const book = model.selectedOdds.find((row) => row.providerType !== 'prediction_market' && ['h2h', 'moneyline', 'h2h_3_way'].includes(String(row.marketKey || row.marketType)));
    if (mode === 'power' && book) {
      return (
        <>
          <div className="wm-worldcup-home-team-grid">
            {teams.map((team) => {
              const outcome = (book.outcomes || []).find((row) => outcomeMatchesTeam(row.name, team));
              const probability = percentFromUnknown(outcome?.impliedProbability ?? null);
              return (
                <section key={team}>
                  <header><strong>{team}</strong><span>book power</span></header>
                  <div className="wm-worldcup-home-meter"><i style={{ width: probabilityWidth(probability) }} /><b>{percentLabel(probability)} implied</b></div>
                  <p>{outcome?.decimalOdds ? `${outcome.decimalOdds.toFixed(2)} decimal · ${outcome.bookCount || book.bookmakerCount || 0} books` : 'Bookmaker consensus row is linked.'}</p>
                </section>
              );
            })}
          </div>
          <EmptyState
            detail="Showing bookmaker-implied team strength from real odds. Player-level power still waits for official squads and injury status."
            rows={[
              { source: 'The Odds API h2h', status: 'live', detail: `${book.provider || 'bookmaker'} consensus` },
              { source: 'Official squads / injury feed', status: 'required', detail: 'player-level availability' },
            ]}
          />
        </>
      );
    }
    const newsCounts = teams.map((team) => ({
      team,
      count: model.news.filter((item) => `${item.title} ${item.summary || ''}`.toLowerCase().includes(team.toLowerCase())).length,
    }));
    if ((mode === 'injury' || mode === 'status') && newsCounts.length) {
      return (
        <>
          <div className="wm-worldcup-home-team-grid">
            {newsCounts.map((row) => (
              <section key={row.team}>
                <header><strong>{row.team}</strong><span>{row.count ? 'news watch' : 'no feed'}</span></header>
                <div className="wm-worldcup-home-meter"><i style={{ width: `${row.count ? 42 : 6}%` }} /><b>{row.count} team-news rows</b></div>
                <p>Official injury and player-status feed is not connected.</p>
              </section>
            ))}
          </div>
          <EmptyState
            detail={`${mode.toUpperCase()} has schedule/news context, but verified player availability still requires an official injury feed.`}
            rows={[
              { source: 'World Cup news watch', status: newsCounts.some((row) => row.count) ? 'partial' : 'empty', detail: 'team mention count only' },
              { source: 'Official squads / injury feed', status: 'required', detail: 'player-level availability' },
            ]}
          />
        </>
      );
    }
    return (
      <EmptyState
        detail={`${mode.toUpperCase()} requires official squad, injury, or player-status feeds. Values are not estimated in the browser.`}
        rows={[{ source: 'Official squads / injury feed', status: 'required', detail: 'player-level availability' }]}
      />
    );
  }
  return (
    <div className="wm-worldcup-home-team-grid">
      {rosters.slice(0, 2).map((roster: WorldCupTeamRoster) => {
        const confirmed = roster.players.filter((player) => player.status === 'confirmed').length;
        const injured = roster.players.filter((player) => player.status === 'injured').length;
        const ready = roster.players.length ? Math.round((confirmed / roster.players.length) * 100) : 0;
        return (
          <section key={roster.team}>
            <header><strong>{roster.team}</strong><span>{roster.players.length} players</span></header>
            <div className="wm-worldcup-home-meter"><i style={{ width: `${Math.max(2, ready)}%` }} /><b>{ready}% ready</b></div>
            <p>{injured ? `${injured} injury flags` : 'No injury flag in connected roster feed'}</p>
            {roster.players.slice(0, 4).map((player) => (
              <div className="wm-worldcup-home-player-row" key={`${roster.team}-${player.name}`}>
                <span>{player.position || 'ALL'}</span>
                <strong>{player.name}</strong>
                <em>{player.status || 'watch'}</em>
              </div>
            ))}
          </section>
        );
      })}
    </div>
  );
}

function OddsLiquidity({ model }: { model: WorldCupHomeModel }) {
  const totalVolume = model.selectedMarkets.reduce((sum, market) => sum + (market.volume24h || 0), 0);
  if (!model.selectedOdds.length && !model.selectedMarkets.length) {
    return <EmptyState detail="No odds or liquidity feed is connected for the selected match." rows={[{ source: 'Bookmaker / Polymarket odds', status: 'required', detail: 'real price, probability, and volume rows' }]} />;
  }
  const oddsRows = model.selectedOdds.flatMap((snapshot) => snapshot.outcomes.slice(0, 4).map((outcome) => ({ snapshot, outcome }))).slice(0, 8);
  return (
    <>
      <div className="wm-worldcup-home-metric-strip">
        <span><em>POLY VOL</em><strong>{formatCompact(totalVolume)}</strong></span>
        <span><em>MARKETS</em><strong>{model.selectedMarkets.length}</strong></span>
        <span><em>BOOKS</em><strong>{model.selectedOdds.length}</strong></span>
      </div>
      <div className="wm-worldcup-home-odds-list">
        {oddsRows.map(({ snapshot, outcome }) => (
          <article key={`${snapshot.provider}-${outcome.name}`}>
            <span>{snapshot.provider}</span>
            <strong>{outcome.name}</strong>
            <b>{outcome.decimalOdds ? outcome.decimalOdds.toFixed(2) : percentLabel(outcome.impliedProbability)}</b>
          </article>
        ))}
      </div>
      <MarketBoard model={model} />
    </>
  );
}

function HostVenue({ model }: { model: WorldCupHomeModel }) {
  const cityWeather = model.payload.weather.map((weather) => ({
    weather,
    city: matchCity(model.payload.cities, weather.cityId),
    matchCount: Math.max(WORLD_CUP_HOST_MATCH_COUNTS[weather.cityId] || 0, model.payload.matches.filter((match) => match.cityId === weather.cityId).length),
  }));
  if (!cityWeather.length) {
    return <EmptyState detail="Host ops is waiting for runtime weather before showing wind/rain/load rows." rows={[{ source: 'Open-Meteo runtime weather', status: 'required', detail: 'host-city current and forecast payload' }]} />;
  }
  return (
    <div className="wm-worldcup-home-city-list">
      {cityWeather.slice(0, 16).map(({ city, weather, matchCount }) => (
        <article className={city.id === model.selectedCity.id ? 'active' : ''} key={city.id}>
          <div>
            <strong>{city.city}</strong>
            <em>{city.country} · {matchCount} matches · {city.venue}</em>
          </div>
          <span>{weather.current.tempC}C</span>
          <b className={`tone-${toneFromText(weather.current.condition)}`}>{weather.current.condition}</b>
        </article>
      ))}
    </div>
  );
}

function VenueRef({ model }: { model: WorldCupHomeModel }) {
  const nearby = model.payload.matches.filter((match) => match.cityId === model.selectedCity.id);
  return (
    <>
      <article className="wm-worldcup-home-venue-card">
        <span>{model.selectedCity.countryName}</span>
        <strong>{model.selectedCity.venue}</strong>
        <em>{model.selectedCity.city} · {model.selectedCity.capacity ? model.selectedCity.capacity.toLocaleString() : '--'} seats · {model.selectedCity.timezone}</em>
      </article>
      {matchRows(nearby, model.selectedMatch, model.selectMatch)}
    </>
  );
}

function RefCardsFallback({ model }: { model: WorldCupHomeModel }) {
  const match = model.selectedMatch;
  if (!match) {
    return <EmptyState detail="Referee and cards data requires a selected fixture plus an official/statistical provider." />;
  }
  return (
    <SignalList
      rows={[
        {
          id: 'home-ref-required',
          source: 'FIFA REF FEED',
          title: `${match.homeTeam} vs ${match.awayTeam}: referee appointment not connected`,
          summary: 'Cards, fouls, penalties and VAR tendencies remain disabled until an official appointment/history source is connected.',
          tone: 'gold',
          tags: [{ label: 'REF', tone: 'red' }, { label: 'REQUIRED', tone: 'gold' }],
        },
        {
          id: 'home-cards-context',
          source: 'MATCH CONTEXT',
          title: `${match.city}: ${model.selectedWeather?.current.condition || 'weather pending'} card-context watch`,
          summary: `Temp ${model.selectedWeather?.current.tempC ?? '--'}C · wind ${model.selectedWeather?.current.windKph ?? '--'} kph · rain ${model.selectedWeather?.current.precipitationProbability ?? 0}%. Context only, not a referee card model.`,
          tone: 'blue',
          tags: [{ label: 'CONTEXT', tone: 'blue' }, { label: 'NO MODEL', tone: 'gray' }],
        },
      ]}
      empty={null}
    />
  );
}

function RefCardsHomePanel({ model }: { model: WorldCupHomeModel }) {
  const relevant = model.signals.filter((row) => /ref|card|risk/i.test(`${row.source} ${row.title}`));
  const match = model.selectedMatch;
  const fallbackRows: SignalRow[] = match ? [
    {
      id: 'home-ref-required',
      source: 'FIFA REF FEED',
      title: `${match.homeTeam} vs ${match.awayTeam}: referee appointment not connected`,
      summary: 'Cards, fouls, penalties and VAR tendencies remain disabled until an official appointment/history source is connected.',
      tone: 'gold',
      tags: [{ label: 'REF', tone: 'red' }, { label: 'REQUIRED', tone: 'gold' }],
    },
    {
      id: 'home-cards-context',
      source: 'MATCH CONTEXT',
      title: `${match.city}: ${model.selectedWeather?.current.condition || 'weather pending'} card-context watch`,
      summary: `Temp ${model.selectedWeather?.current.tempC ?? '--'}C · wind ${model.selectedWeather?.current.windKph ?? '--'} kph · rain ${model.selectedWeather?.current.precipitationProbability ?? 0}%. Context only, not a referee card model.`,
      tone: 'blue',
      tags: [{ label: 'CONTEXT', tone: 'blue' }, { label: 'NO MODEL', tone: 'gray' }],
    },
  ] : [];
  return <SignalList rows={[...relevant, ...fallbackRows]} empty={<RefCardsFallback model={model} />} />;
}

function TravelLoadFallback({ model }: { model: WorldCupHomeModel }) {
  const match = model.selectedMatch;
  if (!match) {
    return <EmptyState detail="Travel load requires a selected fixture plus team-base, previous-match and itinerary data." />;
  }
  const cityMatches = model.payload.matches.filter((item) => item.cityId === match.cityId);
  const teams = [match.homeTeam, match.awayTeam];
  const rows = teams.map((team) => {
    const teamMatches = model.payload.matches
      .filter((item) => item.homeTeam === team || item.awayTeam === team)
      .sort((a, b) => new Date(a.kickoffUtc).getTime() - new Date(b.kickoffUtc).getTime());
    const index = teamMatches.findIndex((item) => item.id === match.id);
    const previous = index > 0 ? teamMatches[index - 1] : null;
    const restDays = previous ? daysBetween(previous.kickoffUtc, match.kickoffUtc) : null;
    const load = Math.max(8, Math.min(96, Math.round(
      (restDays === null ? 22 : restDays <= 3 ? 72 : restDays <= 4 ? 54 : 30)
      + ((model.selectedWeather?.current.windKph || 0) >= 24 ? 12 : 0)
      + ((model.selectedWeather?.current.precipitationProbability || 0) >= 45 ? 12 : 0),
    )));
    return { team, previous, restDays, load };
  });
  return (
    <>
      <div className="wm-worldcup-home-team-grid">
        {rows.map((row) => (
          <section key={row.team}>
            <header><strong>{row.team}</strong><span>{row.restDays === null ? 'open' : `${row.restDays}d rest`}</span></header>
            <div className="wm-worldcup-home-meter"><i style={{ width: `${row.load}%` }} /><b>{row.load}/100 schedule load</b></div>
            <p>{row.previous ? `${row.previous.city} previous fixture · ${cityMatches.length} host-city matches` : `First visible fixture · ${cityMatches.length} host-city matches`}</p>
          </section>
        ))}
      </div>
      <EmptyState
        detail={`${match.venue} load is computed from real fixture/weather rows. Team-base travel distance still requires official logistics data.`}
        rows={[
          { source: 'FIFA fixture history', status: model.payload.matches.length ? 'partial' : 'required', detail: 'previous fixture and recovery window' },
          { source: 'Official team base / federation logistics', status: 'required', detail: 'camp location, flights and travel dates' },
        ]}
      />
    </>
  );
}

function LineupBoardFallback({ model }: { model: WorldCupHomeModel }) {
  const match = model.selectedMatch;
  if (!match) {
    return <EmptyState detail="Predicted XI and confirmed lineup cards require a selected fixture plus official team feeds." />;
  }
  const teams = [match.homeTeam, match.awayTeam];
  const newsCounts = teams.map((team) => ({
    team,
    count: model.news.filter((item) => `${item.title} ${item.summary || ''}`.toLowerCase().includes(team.toLowerCase())).length,
  }));
  return (
    <>
      <div className="wm-worldcup-home-team-grid">
        {newsCounts.map((row) => (
          <section key={row.team}>
            <header><strong>{row.team}</strong><span>{row.count ? 'news watch' : 'lineup feed'}</span></header>
            <div className="wm-worldcup-home-meter"><i style={{ width: `${row.count ? 38 : 6}%` }} /><b>{row.count} team-news rows</b></div>
            <p>Confirmed XI and predicted XI are not connected yet.</p>
          </section>
        ))}
      </div>
      <EmptyState
        detail="Lineup board is showing team/news context only. Confirmed XI, substitutes and player status require official lineup feeds."
        rows={[
          { source: 'World Cup news watch', status: newsCounts.some((row) => row.count) ? 'partial' : 'empty', detail: 'team mention count only' },
          { source: 'Official lineup / squad feed', status: 'required', detail: 'starting XI, bench and player availability' },
        ]}
      />
    </>
  );
}

function SourceAudit({ model }: { model: WorldCupHomeModel }) {
  const states = model.payload.intelligence?.providerStates || {};
  return sourceRows([
    { source: 'Calendar / match control', status: model.payload.matches.length ? model.payload.cacheMode : 'missing', detail: `${model.payload.matches.length} schedule rows` },
    { source: 'News', status: model.news.length ? 'ok' : 'empty', detail: `${model.news.length} news rows` },
    { source: 'Weather / venue risk', status: model.payload.weather.length ? (states.openMeteo || states.wttr || 'ok') : 'missing', detail: `${model.payload.weather.length} host-city weather rows` },
    { source: 'Polymarket markets', status: model.selectedMarkets.length ? 'local-db' : 'not matched', detail: `${model.selectedMarkets.length} linked local DB / Gamma market rows` },
    { source: 'Bookmaker odds', status: model.selectedOdds.length ? 'ok' : 'source required', detail: `${model.selectedOdds.length} licensed odds snapshots` },
    { source: 'Official facts', status: states.espnScoreboard || 'source required', detail: 'ESPN scoreboard if available; FIFA connector still required' },
    { source: 'Injury / lineup / xG / referee', status: 'source required', detail: 'Empty-state until trusted provider rows arrive' },
  ]);
}

function MatchControl({ model }: { model: WorldCupHomeModel }) {
  const match = model.selectedMatch;
  if (!match) return <EmptyState detail="No match selected." />;
  const facts = [
    ['MATCH', `#${match.fifaMatchNumber || '--'}`, match.round],
    ['GROUP', match.group || stageLabel(match.stage), 'table / fixtures'],
    ['BJT', match.kickoffBeijing, 'desk clock'],
    ['LOCAL', match.kickoffLocal, model.selectedCity.timezone],
    ['VENUE', match.venue, `${match.city} · ${model.selectedCity.capacity ? model.selectedCity.capacity.toLocaleString() : '--'} seats`],
    ['WEATHER', model.selectedWeather ? `${model.selectedWeather.current.tempC}C · ${model.selectedWeather.current.condition}` : 'pending', `wind ${model.selectedWeather?.current.windKph ?? '--'} kph`],
  ];
  return (
    <>
      <div className="wm-worldcup-home-score">
        <span><em>HOME</em><strong>{match.homeTeam}</strong></span>
        <b>{scoreText(match)}</b>
        <span><em>AWAY</em><strong>{match.awayTeam}</strong></span>
      </div>
      <div className="wm-worldcup-home-fact-grid">
        {facts.map(([label, value, meta]) => (
          <div key={label}><span>{label}</span><strong>{value}</strong><em>{meta}</em></div>
        ))}
      </div>
      <SignalList rows={model.signals.slice(0, 4)} empty={null} />
    </>
  );
}

function renderView(config: WorldCupHomePanelConfig, model: WorldCupHomeModel) {
  switch (config.view) {
    case 'calendar':
      return matchRows(
        model.payload.matches.filter((match) => new Date(match.kickoffUtc) >= model.now).concat(model.payload.matches).slice(0, 32),
        model.selectedMatch,
        model.selectMatch,
      );
    case 'match-control':
      return <MatchControl model={model} />;
    case 'win-probability':
      return <WinProbability model={model} />;
    case 'venue-risk':
      return <VenueRisk model={model} />;
    case 'market-board':
      return <MarketBoard model={model} />;
    case 'group-advance':
      return <GroupPanel model={model} mode="advance" />;
    case 'group-table':
      return <GroupPanel model={model} mode="table" />;
    case 'team-power':
      return <RosterPanel model={model} mode="power" />;
    case 'team-status':
      return <RosterPanel model={model} mode="status" />;
    case 'injury-load':
      return <RosterPanel model={model} mode="injury" />;
    case 'odds-liquidity':
      return <OddsLiquidity model={model} />;
    case 'news':
      return <NewsList rows={model.news} />;
    case 'news-impact':
      return <NewsList rows={model.news} impact />;
    case 'host-venue':
      return <HostVenue model={model} />;
    case 'venue-ref':
      return <VenueRef model={model} />;
    case 'source-audit':
      return <SourceAudit model={model} />;
    case 'media-wire':
      return <SignalList rows={[...model.news.slice(0, 6).map((item) => ({ id: `news-${item.id}`, source: item.source, title: item.title, summary: item.summary || 'World Cup media item.', tone: toneFromText(item.title), url: item.url } satisfies SignalRow)), ...model.signals]} empty={<EmptyState detail="No media wire rows are available." />} />;
    case 'match-tempo':
    case 'match-model':
      return <SignalList rows={model.signals.filter((row) => /match|tempo|model|market/i.test(`${row.source} ${row.title}`))} empty={<EmptyState detail="xG, tempo and tactical model rows require a trusted statistical feed." />} />;
    case 'ref-cards':
      return <RefCardsHomePanel model={model} />;
    case 'travel-load':
      return <TravelLoadFallback model={model} />;
    case 'lineup-board':
      return <LineupBoardFallback model={model} />;
    default:
      return <SignalList rows={model.signals} empty={<EmptyState detail="World Cup panel is waiting for source rows." />} />;
  }
}

function WorldCupHomePanel({ config, ctx }: { config: WorldCupHomePanelConfig; ctx: PanelRuntimeContext }) {
  const { model, loading, error } = useWorldCupModel(ctx);
  const [showHelp, setShowHelp] = useState(false);
  const count = model ? countForView(config.view, model) : 0;
  return (
    <Panel
      title={config.title}
      titleControls={<button type="button" className="wm-panel-help-button" aria-label={`Explain ${config.title}`} aria-expanded={showHelp} onClick={() => setShowHelp((current) => !current)}>?</button>}
      badge={statusBadge(model?.payload)}
      status={panelStatus(model?.payload)}
      count={count}
      headerOverlay={showHelp ? (
        <div className="wm-panel-help-popover">
          <strong>{config.title}</strong>
          <p>{config.question}</p>
        </div>
      ) : null}
      className={`wm-market-panel wm-worldcup-home-panel wm-worldcup-${config.view}-panel`}
      dataPanelId={config.id}
    >
      {loading && !model ? <PanelLoading label="Loading World Cup" detail="Syncing schedule, host cities and market links" /> : null}
      {!loading && error && !model ? <EmptyState detail={error} /> : null}
      {model ? renderView(config, model) : null}
    </Panel>
  );
}

export function createWorldCupHomePanel(config: WorldCupHomePanelConfig): PanelModule {
  return {
    id: config.id,
    title: config.title,
    eyebrow: 'World Cup',
    description: config.description,
    size: config.size,
    defaultEnabled: true,
    render: (ctx) => <WorldCupHomePanel config={config} ctx={ctx} />,
  };
}
