import { matchCity, WORLD_CUP_HOST_MATCH_COUNTS } from '../data';
import { cleanSignalTags, type WorldCupSignalItem, type WorldCupSignalTone } from '../components/SignalRow';
import type { WorldCupDashboardPayload, WorldCupMatch, WorldCupOddsSnapshot, WorldCupPolymarketMarket } from '../types';
import { formatCompact, formatNumber, stageLabel } from './formatters';

export function mergeSignalRows(...groups: WorldCupSignalItem[][]) {
  const seen = new Set<string>();
  return groups.flat().filter((item) => {
    const key = `${item.source}:${item.title}`.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function coerceSignalTone(tone?: string | null): WorldCupSignalTone {
  if (tone === 'red' || tone === 'gold' || tone === 'blue' || tone === 'purple' || tone === 'gray' || tone === 'green') return tone;
  return 'blue';
}

export function runtimeSignalItems(payload: WorldCupDashboardPayload, category: string, match: WorldCupMatch | null): WorldCupSignalItem[] {
  const signals = payload.intelligence?.signals || [];
  return signals
    .filter((item) => item.category === category && (!item.matchId || item.matchId === match?.id))
    .slice(0, 16)
    .map((item, index) => ({
      id: item.id || `${category}-${index}`,
      source: item.source || item.provider || 'WORLD CUP DESK',
      title: item.title || 'World Cup live signal',
      summary: item.summary || 'Runtime feed item from World Cup intelligence provider.',
      age: item.age || payload.intelligence?.generatedAt || '',
      tags: cleanSignalTags((item.tags?.length ? item.tags : [{ label: 'INFO', tone: 'blue' }]).slice(0, 3).map((tag) => ({
        label: tag.label,
        tone: coerceSignalTone(tag.tone),
      }))),
      accent: coerceSignalTone(item.accent),
    }));
}

export function buildMatchSignals(match: WorldCupMatch | null, markets: WorldCupPolymarketMarket[]): WorldCupSignalItem[] {
  if (!match) return [];
  const group = match.group || stageLabel(match.stage);
  return [
    {
      id: 'match-clock',
      source: 'MATCH DESK',
      title: `${match.homeTeam} vs ${match.awayTeam}: kickoff control and match state`,
      summary: `${match.kickoffBeijing || match.kickoffUtc || 'Kickoff pending'} Beijing · ${match.kickoffLocal || 'local pending'} local · ${String(match.status || 'scheduled').toUpperCase()}`,
      age: '',
      tags: [{ label: 'SCHEDULE', tone: 'green' }, { label: group, tone: 'gold' }],
      accent: 'green',
    },
    {
      id: 'match-venue',
      source: 'VENUE OPS',
      title: `${match.venue}: host venue readiness and pitch watch`,
      summary: `${match.city}. Venue and city metadata come from the dashboard host-city registry.`,
      age: '',
      tags: [{ label: 'VENUE', tone: 'blue' }],
      accent: 'blue',
    },
    {
      id: 'match-market',
      source: 'POLYDATA',
      title: `${markets.length} linked market candidates for selected fixture`,
      summary: 'Only local DB / Polymarket matched markets are counted.',
      age: '',
      tags: [{ label: 'MARKET', tone: 'purple' }],
      accent: 'purple',
    },
  ];
}

export function buildHostOpsSignals(payload: WorldCupDashboardPayload, selectedCityId: string | null): WorldCupSignalItem[] {
  return payload.weather.slice(0, 12).map((weather, index) => {
    const city = matchCity(payload.cities, weather.cityId);
    const matchCount = Math.max(
      WORLD_CUP_HOST_MATCH_COUNTS[weather.cityId] || 0,
      payload.matches.filter((match) => match.cityId === weather.cityId).length,
    );
    const active = city.id === selectedCityId;
    return {
      id: `host-${city.id}`,
      source: active ? 'SELECTED HOST' : 'HOST OPS',
      title: `${city.city}: ${matchCount} matches, ${city.venue}`,
      summary: `${weather.current.tempC}C · ${weather.current.condition} · wind ${weather.current.windKph || '--'} kph · rain ${weather.current.precipitationProbability || 0}%`,
      age: weather.generatedAt,
      tags: [
        { label: active ? 'ACTIVE' : 'WEATHER', tone: active ? 'green' : 'blue' },
        { label: String(weather.current.condition || 'WEATHER').toUpperCase(), tone: /storm|rain/i.test(weather.current.condition || '') ? 'red' : 'gold' },
      ],
      accent: active ? 'green' : index % 3 === 0 ? 'blue' : 'gold',
    } satisfies WorldCupSignalItem;
  });
}

export function buildMarketSignals(markets: WorldCupPolymarketMarket[], match: WorldCupMatch | null): WorldCupSignalItem[] {
  if (!match) return [];
  return markets.flatMap<WorldCupSignalItem>((market, marketIndex) => [
    {
      id: `market-${marketIndex}-headline`,
      source: String(market.source || 'market').toUpperCase(),
      title: market.title,
      summary: `${Math.round(market.confidence * 100)} confidence · 24h volume ${formatCompact(market.volume24h)} · ${market.outcomes.length} outcomes`,
      age: '',
      tags: [{ label: 'MARKET', tone: 'purple' }],
      accent: marketIndex % 2 ? 'blue' : 'purple',
    },
    {
      id: `market-${marketIndex}-price`,
      source: 'PRICE WATCH',
      title: market.outcomes.slice(0, 3).map((outcome) => `${outcome.name} ${outcome.yesPrice == null ? '--' : `${(outcome.yesPrice * 100).toFixed(1)}%`}`).join(' · '),
      summary: 'Outcome spread is displayed only from real market outcome prices.',
      age: '',
      tags: [{ label: 'ODDS', tone: 'gold' }, { label: 'SPREAD', tone: 'blue' }],
      accent: 'gold',
    },
  ]).slice(0, 10);
}

export function buildSquadSignals(payload: WorldCupDashboardPayload, match: WorldCupMatch | null): WorldCupSignalItem[] {
  const teams = match ? [match.homeTeam, match.awayTeam] : payload.rosters.slice(0, 2).map((roster) => roster.team);
  const rosters = payload.rosters.filter((roster) => teams.includes(roster.team));
  return rosters.flatMap((roster) => roster.players.map((player) => ({
    id: `squad-${roster.team}-${player.name}`,
    source: roster.team,
    title: player.name,
    summary: `${player.position || 'ALL'} · ${player.club || player.status || 'official roster row'}`,
    age: roster.updatedAt,
    tags: [
      { label: player.status?.toUpperCase() || 'PLAYER', tone: player.status === 'injured' ? 'red' : 'gold' },
      { label: 'TEAM', tone: 'green' },
    ],
    accent: player.status === 'injured' ? 'red' : 'blue',
  } satisfies WorldCupSignalItem))).slice(0, 8);
}

export function buildOddsSignals(odds: WorldCupOddsSnapshot[], match: WorldCupMatch | null): WorldCupSignalItem[] {
  return odds.slice(0, 8).map((snapshot, index) => ({
    id: `odds-${snapshot.matchId}-${snapshot.provider}-${index}`,
    source: String(snapshot.providerType || 'provider').replace('_', ' ').toUpperCase(),
    title: snapshot.provider,
    summary: snapshot.outcomes.map((outcome) => `${outcome.name} ${formatNumber(outcome.decimalOdds, 2)} / ${formatNumber(outcome.impliedProbability, 1)}%`).join(' · '),
    age: snapshot.generatedAt || (match ? match.kickoffBeijing : ''),
    tags: [{ label: String(snapshot.marketType || 'odds').toUpperCase(), tone: 'purple' }],
    accent: index % 2 ? 'purple' : 'blue',
  }));
}

export function buildRiskSignals(payload: WorldCupDashboardPayload, match: WorldCupMatch | null): WorldCupSignalItem[] {
  const selected = match || payload.matches[0] || null;
  const weather = selected ? payload.weather.find((item) => item.cityId === selected.cityId) : null;
  if (!weather) return [];
  return [
    {
      id: 'risk-weather',
      source: 'WEATHER RISK',
      title: selected ? `${selected.city}: ${weather.current.condition || 'host conditions'} before kickoff` : 'Host weather monitor',
      summary: `Temperature ${weather.current.tempC ?? '--'}C · precipitation ${weather.current.precipitationProbability ?? 0}% · wind ${weather.current.windKph ?? '--'} kph.`,
      age: weather.generatedAt,
      tags: [{ label: 'ALERT', tone: /storm|rain/i.test(weather.current.condition || '') ? 'red' : 'gold' }, { label: 'WEATHER', tone: 'blue' }],
      accent: /storm|rain/i.test(weather.current.condition || '') ? 'red' : 'gold',
    },
  ];
}

export function buildBroadcastSignals(match: WorldCupMatch | null): WorldCupSignalItem[] {
  if (!match) return [];
  return [
    {
      id: 'broadcast-bjt',
      source: 'BROADCAST',
      title: `${match.homeTeam} vs ${match.awayTeam}: Beijing time window`,
      summary: `${match.kickoffBeijing}. Desk view keeps BJT, local kickoff and venue status together.`,
      age: '',
      tags: [{ label: 'BJT', tone: 'green' }, { label: 'LIVE OPS', tone: 'blue' }],
      accent: 'green',
    },
    {
      id: 'broadcast-local',
      source: 'LOCAL FEED',
      title: `${match.city}: local matchday handoff`,
      summary: `${match.kickoffLocal}. Host city context is paired with weather and venue ops.`,
      age: '',
      tags: [{ label: 'LOCAL', tone: 'blue' }, { label: 'VENUE', tone: 'gold' }],
      accent: 'blue',
    },
    {
      id: 'broadcast-market',
      source: 'MARKET FEED',
      title: 'Market desk watches news latency and odds spread',
      summary: 'News tags, source age and market confidence are normalized into the same row grammar.',
      age: '',
      tags: [{ label: 'MARKET', tone: 'purple' }, { label: 'WATCH', tone: 'gray' }],
      accent: 'purple',
    },
  ];
}

export function buildOfficialFactSignals(payload: WorldCupDashboardPayload, match: WorldCupMatch | null, city: WorldCupDashboardPayload['cities'][number] | null): WorldCupSignalItem[] {
  const liveSignals = runtimeSignalItems(payload, 'officialFacts', match);
  if (!match) return liveSignals;
  const cityMatches = payload.matches.filter((item) => item.cityId === match.cityId).slice(0, 3);
  const factRows: WorldCupSignalItem[] = [
    {
      id: 'facts-match-centre',
      source: payload.cacheMode === 'remote' ? 'SCHEDULE SOURCE' : 'SCHEDULE SOURCE REQUIRED',
      title: `${match.homeTeam} vs ${match.awayTeam}: fixture identity verified`,
      summary: `Match #${match.fifaMatchNumber || '--'} · ${match.group || stageLabel(match.stage)} · ${match.round}. Official FIFA API connector is still required for final verification.`,
      age: '',
      tags: [{ label: 'FACT', tone: 'green' }, { label: payload.cacheMode === 'remote' ? 'REMOTE' : 'REQUIRED', tone: payload.cacheMode === 'remote' ? 'blue' : 'red' }],
      accent: 'green',
    },
    {
      id: 'facts-kickoff',
      source: 'WORLD CUP DESK',
      title: `Kickoff lock: ${match.kickoffBeijing} BJT / ${match.kickoffLocal} local`,
      summary: 'Beijing desk, local desk and venue desk should all reference this same match card.',
      age: '',
      tags: [{ label: 'TIME', tone: 'blue' }, { label: 'VERIFY', tone: 'gray' }],
      accent: 'blue',
    },
    {
      id: 'facts-venue',
      source: 'HOST CITY',
      title: `${match.venue}, ${match.city}`,
      summary: `${city?.countryName || 'Host country'} · capacity ${city?.capacity ? city.capacity.toLocaleString() : 'pending'} · timezone ${city?.timezone || 'local'}.`,
      age: '',
      tags: [{ label: 'VENUE', tone: 'gold' }, { label: 'OPS', tone: 'green' }],
      accent: 'gold',
    },
    {
      id: 'facts-team-source',
      source: 'TEAM CHANNELS',
      title: 'National team official channels are not connected yet',
      summary: 'Roster and injury panels remain empty unless federation/team data is ingested.',
      age: '',
      tags: [{ label: 'ROSTER', tone: 'purple' }, { label: 'REQUIRED', tone: 'red' }],
      accent: 'purple',
    },
    {
      id: 'facts-city-window',
      source: 'HOST SCHEDULE',
      title: `${match.city}: host-city match window and operational load`,
      summary: `${cityMatches.length || 1} visible fixture rows in this city panel · venue, weather and travel context share the same city key.`,
      age: '',
      tags: [{ label: 'CITY', tone: 'blue' }, { label: 'OPS', tone: 'gold' }],
      accent: 'blue',
    },
    {
      id: 'facts-market-link',
      source: 'POLYDATA',
      title: 'Market identity requires a real local DB / Gamma hit',
      summary: 'No inferred market rows are generated when a matching market is absent.',
      age: '',
      tags: [{ label: 'MARKET', tone: 'purple' }, { label: 'VERIFY', tone: 'green' }],
      accent: 'purple',
    },
    {
      id: 'facts-status-rules',
      source: 'MATCH STATE',
      title: `Current state: ${String(match.status || 'scheduled').toUpperCase()}${match.minute ? ` · ${match.minute}` : ''}`,
      summary: 'Scheduled, live, finished and postponed states drive calendar filters, match detail and group table updates.',
      age: '',
      tags: [{ label: 'STATE', tone: 'green' }, { label: String(match.status || 'scheduled').toUpperCase(), tone: match.status === 'postponed' ? 'red' : 'gold' }],
      accent: match.status === 'postponed' ? 'red' : 'green',
    },
    ...cityMatches.map((cityMatch) => ({
      id: `facts-city-match-${cityMatch.id}`,
      source: 'CITY FIXTURE',
      title: `#${cityMatch.fifaMatchNumber || '--'} ${cityMatch.homeTeam} vs ${cityMatch.awayTeam}`,
      summary: `${cityMatch.kickoffBeijing} BJT · ${cityMatch.group || stageLabel(cityMatch.stage)} · ${cityMatch.venue}.`,
      age: '',
      tags: [{ label: 'FIXTURE', tone: 'blue' }, { label: cityMatch.group || stageLabel(cityMatch.stage), tone: 'gold' }],
      accent: 'blue',
    } satisfies WorldCupSignalItem)),
  ];
  return mergeSignalRows(liveSignals, factRows).slice(0, 12);
}

export function buildInjurySignals(payload: WorldCupDashboardPayload, match: WorldCupMatch | null): WorldCupSignalItem[] {
  return runtimeSignalItems(payload, 'injuryTracker', match).slice(0, 12);
}

export function buildLineupSignals(payload: WorldCupDashboardPayload, match: WorldCupMatch | null): WorldCupSignalItem[] {
  return runtimeSignalItems(payload, 'lineupWatch', match).slice(0, 10);
}

export function buildPlayerPoolSignals(payload: WorldCupDashboardPayload, match: WorldCupMatch | null): WorldCupSignalItem[] {
  const focusTeams = match ? [match.homeTeam, match.awayTeam] : payload.rosters.slice(0, 4).map((roster) => roster.team);
  return payload.rosters
    .filter((roster) => focusTeams.includes(roster.team))
    .flatMap<WorldCupSignalItem>((roster) => [
      {
        id: `pool-${roster.team}-status`,
        source: roster.team,
        title: `${roster.team}: final roster window`,
        summary: `${roster.players.length || 0} roster candidates · official federation squad page remains authority.`,
        age: '',
        tags: [{ label: 'SQUAD', tone: 'green' }, { label: 'OFFICIAL', tone: 'blue' }],
        accent: 'green',
      } satisfies WorldCupSignalItem,
      ...roster.players.map((player) => ({
        id: `pool-${roster.team}-${player.name}`,
        source: player.position || 'PLAYER',
        title: player.name,
        summary: `${player.club || 'federation source'} · ${player.status || 'probable'} · number ${player.number || '--'}.`,
        age: player.status || 'pool',
        tags: [{ label: player.status?.toUpperCase() || 'WATCH', tone: player.status === 'injured' ? 'red' : 'gold' }, { label: 'TEAM', tone: 'gray' }],
        accent: player.status === 'injured' ? 'red' : 'blue',
      } satisfies WorldCupSignalItem)),
    ])
    .slice(0, 12);
}

export function buildXgSignals(payload: WorldCupDashboardPayload, match: WorldCupMatch | null): WorldCupSignalItem[] {
  return runtimeSignalItems(payload, 'xgModel', match).slice(0, 10);
}

export function buildTacticalSignals(payload: WorldCupDashboardPayload, match: WorldCupMatch | null): WorldCupSignalItem[] {
  return runtimeSignalItems(payload, 'tacticalMatchup', match).slice(0, 10);
}

export function buildLocalMediaSignals(payload: WorldCupDashboardPayload, match: WorldCupMatch | null): WorldCupSignalItem[] {
  return runtimeSignalItems(payload, 'localMedia', match).slice(0, 14);
}

export function buildRefVenueSignals(payload: WorldCupDashboardPayload, match: WorldCupMatch | null): WorldCupSignalItem[] {
  return runtimeSignalItems(payload, 'refVenue', match).slice(0, 10);
}
