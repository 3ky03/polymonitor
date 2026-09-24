import { SignalRow, type WorldCupSignalItem } from '../components/SignalRow';
import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupOddsSnapshot, WorldCupPolymarketMarket } from '../types';
import { formatCompact, formatNumber } from './formatters';

export function OddsLiquidityPanel({
  odds,
  markets,
  oddsSignals,
  marketSignals,
}: {
  odds: WorldCupOddsSnapshot[];
  markets: WorldCupPolymarketMarket[];
  oddsSignals: WorldCupSignalItem[];
  marketSignals: WorldCupSignalItem[];
}) {
  const liquidity = markets.reduce((sum, market) => sum + (market.liquidity || market.volume24h || 0), 0);
  return (
    <Panel title="ODDS / LIQUIDITY" count={odds.length + markets.length} className="wm-worldcup-panel wm-worldcup-odds-liquidity-panel">
      <div className="wm-worldcup-liquidity-strip">
        <span><em>LIQUIDITY</em><strong>{formatCompact(liquidity)}</strong></span>
        <span><em>ODDS FEEDS</em><strong>{odds.length}</strong></span>
        <span><em>MARKETS</em><strong>{markets.length}</strong></span>
      </div>
      <div className="wm-worldcup-odds-table">
        {odds.slice(0, 5).map((snapshot) => (
          <article key={`${snapshot.matchId}-${snapshot.provider}`}>
            <header><strong>{snapshot.provider}</strong><span>{String(snapshot.providerType || 'provider').replace('_', ' ')}</span></header>
            <div>
              {snapshot.outcomes.slice(0, 3).map((outcome) => (
                <span key={outcome.name}>
                  <em>{outcome.name}</em>
                  <b>{formatNumber(outcome.decimalOdds, 2)}</b>
                  <i style={{ width: `${Math.max(3, Math.min(100, outcome.impliedProbability || 0))}%` }} />
                </span>
              ))}
            </div>
          </article>
        ))}
      </div>
      <div className="wm-worldcup-mini-feed wm-worldcup-mini-feed-tight">
        {[...oddsSignals.slice(0, 2), ...marketSignals.slice(0, 2)].map((item) => <SignalRow item={item} key={item.id} />)}
      </div>
      {!odds.length && !markets.length ? (
        <SourceRequired
          detail="No odds or liquidity feed is connected for the selected match. The panel is waiting for real bookmaker or market data."
          rows={[
            { source: 'Bookmaker odds API', status: 'required', detail: 'moneyline/totals snapshots' },
            { source: 'Polymarket local DB / Gamma', status: 'not matched', detail: 'liquidity and outcome prices' },
          ]}
        />
      ) : null}
    </Panel>
  );
}
