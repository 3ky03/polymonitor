import { InfoDot } from '../components/InfoDot';
import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupOddsSnapshot, WorldCupPolymarketMarket } from '../types';
import { formatNumber } from './formatters';

export function OddsPanel({ odds, polymarket }: { odds: WorldCupOddsSnapshot[]; polymarket: WorldCupPolymarketMarket[] }) {
  return (
    <Panel
      title="ODDS"
      count={odds.length}
      titleControls={<InfoDot label="Bookmaker snapshots show decimal odds and implied probability for the selected match." />}
      className="wm-worldcup-panel wm-worldcup-odds-panel"
    >
      <div className="wm-worldcup-odds-list">
        {odds.slice(0, 8).map((snapshot) => (
          <article className="wm-worldcup-odds-row" key={`${snapshot.matchId}-${snapshot.provider}`}>
            <div>
              <span>{String(snapshot.providerType || 'provider').replace('_', ' ')}</span>
              <strong>{snapshot.provider}</strong>
            </div>
            <div className="wm-worldcup-odds-cells">
              {snapshot.outcomes.map((outcome) => (
                <span key={outcome.name}>
                  <b>{outcome.name}</b>
                  <strong>{formatNumber(outcome.decimalOdds, 2)}</strong>
                  <em>{formatNumber(outcome.impliedProbability, 1)}%</em>
                  <i style={{ width: outcome.impliedProbability == null ? '2%' : `${Math.max(2, Math.min(100, outcome.impliedProbability))}%` }} />
                </span>
              ))}
            </div>
          </article>
        ))}
        {!odds.length ? (
          <SourceRequired
            detail="No sportsbook feed is connected for this fixture. Odds rows are hidden until a licensed odds API supplies bookmaker snapshots."
            rows={[
              { source: 'The Odds API / Sportradar odds / bookmaker feed', status: 'required', detail: 'moneyline, totals and timestamped implied probabilities' },
              { source: 'Polymarket local DB', status: polymarket.length ? 'available' : 'not matched', detail: `${polymarket.length} linked prediction markets` },
            ]}
          />
        ) : null}
      </div>
    </Panel>
  );
}
