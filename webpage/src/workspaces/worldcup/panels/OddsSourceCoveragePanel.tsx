import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupDashboardPayload, WorldCupMatch, WorldCupOddsSnapshot, WorldCupPolymarketMarket } from '../types';

const TARGET_MARKETS = [
  { key: 'h2h', label: '1X2', detail: 'full-time result' },
  { key: 'spreads', label: 'AH', detail: 'handicap main line' },
  { key: 'totals', label: 'O/U', detail: 'goals total main line' },
  { key: 'btts', label: 'BTTS', detail: 'both teams score' },
  { key: 'draw_no_bet', label: 'DNB', detail: 'draw no bet' },
  { key: 'double_chance', label: 'DC', detail: 'double chance' },
  { key: 'alternate_spreads', label: 'ALT AH', detail: 'event endpoint; quota-heavy' },
  { key: 'alternate_totals', label: 'ALT O/U', detail: 'event endpoint; quota-heavy' },
];

function snapshotKey(snapshot: WorldCupOddsSnapshot) {
  const value = String(snapshot.marketKey || snapshot.marketType || '').toLowerCase();
  if (value === 'moneyline') return 'h2h';
  if (value === 'spread') return 'spreads';
  if (value === 'total_goals') return 'totals';
  return value;
}

function pct(value: number, total: number) {
  if (!total) return 0;
  return Math.round((value / total) * 100);
}

function statusForCoverage(key: string, count: number, total: number) {
  if (count > 0) return 'ok';
  if (key.startsWith('alternate_')) return 'probe-required';
  if (total > 0) return 'not-returned';
  return 'source-required';
}

function statusClass(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

export function OddsSourceCoveragePanel({
  payload,
  odds,
  markets,
  match,
}: {
  payload: WorldCupDashboardPayload;
  odds: WorldCupOddsSnapshot[];
  markets: WorldCupPolymarketMarket[];
  match: WorldCupMatch | null;
}) {
  const totalMatches = payload.matches.filter((item) => item.status !== 'finished').length || payload.matches.length;
  const bookmakerOdds = odds.filter((snapshot) => snapshot.providerType !== 'prediction_market');
  const counts = new Map<string, Set<string>>();
  bookmakerOdds.forEach((snapshot) => {
    const key = snapshotKey(snapshot);
    if (!key) return;
    if (!counts.has(key)) counts.set(key, new Set());
    counts.get(key)!.add(snapshot.matchId);
  });
  const states = payload.providerStates || {};
  const configuredMarkets = payload.bookmakerLinker?.markets || [];
  const rows = TARGET_MARKETS.map((item) => {
    const matchCount = counts.get(item.key)?.size || 0;
    return {
      ...item,
      matchCount,
      coverage: pct(matchCount, totalMatches),
      status: statusForCoverage(item.key, matchCount, totalMatches),
      configured: configuredMarkets.includes(item.key),
    };
  });
  const sourceRows = [
    { source: 'The Odds API', status: states.bookmakerOdds || 'unknown', detail: `${payload.bookmakerLinker?.snapshots || bookmakerOdds.length} snapshots · ${configuredMarkets.join(', ') || 'no markets'}` },
    { source: 'TheRundown', status: states.theRundown || 'missing-key', detail: 'candidate for 10-minute delayed line movement; no panel data until key is configured' },
    { source: 'API-Football', status: states.apiFootball || 'missing-key', detail: 'candidate for football odds, lineups, injuries; coverage requires key probe' },
    { source: 'Betfair', status: states.betfair || 'missing-key', detail: 'candidate exchange price/volume feed; requires delayed/live app key probe' },
    { source: 'Matchbook', status: states.matchbook || 'missing-key', detail: 'candidate exchange prices; requires account/API probe' },
    { source: 'Polymarket', status: markets.length ? 'local-db' : 'not matched', detail: `${markets.length} linked markets for ${match ? `${match.homeTeam} vs ${match.awayTeam}` : 'selected match'}` },
  ];
  return (
    <Panel title="SOURCE COVERAGE" count={rows.filter((row) => row.matchCount > 0).length} className="wm-worldcup-panel wm-worldcup-source-coverage-panel">
      <div className="wm-worldcup-coverage-grid">
        {rows.map((row) => (
          <article className={`status-${statusClass(row.status)}`} key={row.key}>
            <header><strong>{row.label}</strong><span>{row.status}</span></header>
            <b>{row.coverage}%</b>
            <em>{row.matchCount}/{totalMatches} matches · {row.detail}</em>
            <i style={{ width: `${Math.max(2, row.coverage)}%` }} />
          </article>
        ))}
      </div>
      <div className="wm-worldcup-source-probe-list">
        {sourceRows.map((row) => (
          <article className={`status-${statusClass(row.status)}`} key={row.source}>
            <span>{row.source}</span>
            <strong>{row.status}</strong>
            <em>{row.detail}</em>
          </article>
        ))}
      </div>
    </Panel>
  );
}
