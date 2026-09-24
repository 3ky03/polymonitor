import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupDashboardPayload, WorldCupMatch, WorldCupOddsSnapshot, WorldCupPolymarketMarket } from '../types';
import { formatCompact } from './formatters';

function snapshotKey(snapshot: WorldCupOddsSnapshot) {
  const value = String(snapshot.marketKey || snapshot.marketType || '').toLowerCase();
  if (value === 'moneyline') return 'h2h';
  if (value === 'spread') return 'spreads';
  if (value === 'total_goals') return 'totals';
  return value;
}

function marketLabel(key: string) {
  if (key === 'h2h') return '1X2';
  if (key === 'spreads') return 'AH';
  if (key === 'totals') return 'O/U';
  if (key === 'btts') return 'BTTS';
  if (key === 'draw_no_bet') return 'DNB';
  if (key === 'double_chance') return 'DC';
  return key.toUpperCase();
}

function lineLabel(value?: number | string | null) {
  if (value === null || value === undefined || value === '') return '--';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  const text = Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
  return numeric > 0 ? `+${text}` : text;
}

function oddsLabel(value?: number | string | null) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return numeric.toFixed(2);
}

function latestUpdate(snapshot: WorldCupOddsSnapshot) {
  const values = (snapshot.bookmakers || []).map((book) => book.lastUpdate).filter(Boolean).sort();
  const value = values[values.length - 1] || snapshot.generatedAt;
  if (!value) return '--';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'UTC',
    timeZoneName: 'short',
  }).format(date);
}

function sparkWidths(snapshot: WorldCupOddsSnapshot) {
  const outcomes = (snapshot.outcomes || []).slice(0, 8);
  return outcomes.map((outcome) => Math.max(8, Math.min(100, Number(outcome.impliedProbability || 0))));
}

export function LineMovementPanel({
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
  const bookmakerOdds = odds.filter((snapshot) => snapshot.providerType !== 'prediction_market');
  const grouped = ['h2h', 'spreads', 'totals', 'btts', 'draw_no_bet', 'double_chance']
    .map((key) => bookmakerOdds.find((snapshot) => snapshotKey(snapshot) === key))
    .filter((snapshot): snapshot is WorldCupOddsSnapshot => Boolean(snapshot));
  const selectedLiquidity = markets.reduce((sum, market) => sum + Number(market.liquidity || market.volume24h || 0), 0);
  const states = payload.providerStates || {};
  return (
    <Panel title="LINE MOVEMENT" count={grouped.length} className="wm-worldcup-panel wm-worldcup-line-movement-panel">
      <div className="wm-worldcup-line-status">
        <span><em>MATCH</em><strong>{match ? `${match.homeTeam} vs ${match.awayTeam}` : '--'}</strong></span>
        <span><em>POLY FLOW</em><strong>{formatCompact(selectedLiquidity)}</strong></span>
        <span><em>10M SOURCE</em><strong>{states.theRundown === 'configured' ? 'READY' : 'WAIT KEY'}</strong></span>
      </div>
      <div className="wm-worldcup-line-cards">
        {grouped.map((snapshot) => {
          const key = snapshotKey(snapshot);
          const widths = sparkWidths(snapshot);
          return (
            <article key={`${snapshot.matchId}-${key}`}>
              <header>
                <span>{marketLabel(key)}</span>
                <strong>{snapshot.bookmakerCount || 0} books</strong>
                <em>{latestUpdate(snapshot)}</em>
              </header>
              <div className="wm-worldcup-line-spark">
                {widths.map((width, index) => <i style={{ height: `${width}%` }} key={`${key}-${index}`} />)}
              </div>
              <div className="wm-worldcup-line-outcomes">
                {(snapshot.outcomes || []).slice(0, 4).map((outcome) => (
                  <b key={`${outcome.name}-${outcome.point ?? 'x'}`}>
                    {outcome.name} {outcome.point == null ? '' : lineLabel(outcome.point)}
                    <strong>{oddsLabel(outcome.decimalOdds)}</strong>
                  </b>
                ))}
              </div>
            </article>
          );
        })}
      </div>
      <div className="wm-worldcup-line-note">
        <b>10M TREND</b>
        <span>{states.theRundown === 'configured' ? 'Waiting for high-frequency source probe to start historical line cache.' : 'TheRundown/API-Football key not configured; panel shows current real lines only, no fake trend.'}</span>
      </div>
    </Panel>
  );
}
