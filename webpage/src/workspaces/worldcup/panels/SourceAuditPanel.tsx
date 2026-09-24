import { SourceRequired, type SourceRequiredRow } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupDashboardPayload, WorldCupNewsItem, WorldCupOddsSnapshot, WorldCupPolymarketMarket } from '../types';

export function SourceAuditPanel({
  payload,
  markets,
  odds,
  news,
}: {
  payload: WorldCupDashboardPayload;
  markets: WorldCupPolymarketMarket[];
  odds: WorldCupOddsSnapshot[];
  news: WorldCupNewsItem[];
}) {
  const states = payload.intelligence?.providerStates || {};
  const rows: SourceRequiredRow[] = [
    { source: 'Calendar / match control', status: payload.matches.length ? payload.cacheMode : 'missing', detail: `${payload.matches.length} schedule rows; no hardcoded fixtures` },
    { source: 'News', status: news.length ? 'ok' : 'empty', detail: `${news.length} ESPN/latest-content rows; no generated news` },
    { source: 'Weather / venue risk', status: payload.weather.length ? (states.openMeteo || states.wttr || 'ok') : 'missing', detail: `${payload.weather.length} host-city weather rows` },
    { source: 'Polymarket markets', status: markets.length ? 'local-db' : 'not matched', detail: `${markets.length} linked local DB / Gamma market rows` },
    { source: 'Bookmaker odds', status: odds.length ? 'ok' : 'source required', detail: `${odds.length} licensed odds snapshots` },
    { source: 'Official facts', status: states.espnScoreboard || 'source required', detail: 'ESPN scoreboard if available; FIFA Match Centre connector still required' },
    { source: 'Injury / lineup / xG / referee', status: 'source required', detail: 'Panels show empty-state until trusted provider rows arrive' },
  ];
  return (
    <Panel title="SOURCE AUDIT" count={rows.length} className="wm-worldcup-panel wm-worldcup-source-audit-panel">
      <SourceRequired
        title="VERIFIED DATA MODE"
        detail="This workspace only renders rows backed by an upstream provider, cached snapshot, or local Polymarket DB link; generated odds, squads, news and market rows are blocked."
        rows={rows}
      />
    </Panel>
  );
}
