import { InfoDot } from '../components/InfoDot';
import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupPolymarketMarket } from '../types';
import { formatCompact, probabilityWidth } from './formatters';

export function PolymarketPanel({ markets }: { markets: WorldCupPolymarketMarket[] }) {
  return (
    <Panel
      title="MARKETS"
      count={markets.length}
      titleControls={<InfoDot label="Local Polymarket market matches are ranked by team, venue and kickoff context confidence." />}
      className="wm-worldcup-panel wm-worldcup-polymarket-panel"
    >
      {markets.length ? (
        <div className="wm-worldcup-market-list">
          {markets.map((market) => (
            <article className="wm-worldcup-market-row" key={`${market.eventId || market.title}`}>
              <div>
                <span>{formatCompact(market.volume24h)} 24H · {Math.round(market.confidence * 100)}% match</span>
                <strong>{market.title}</strong>
                <em>Polymarket-style probability board</em>
              </div>
              <div className="wm-worldcup-outcomes">
                {market.outcomes.slice(0, 3).map((outcome) => (
                  <span key={outcome.name}>
                    <b>{outcome.name}</b>
                    <strong>{outcome.yesPrice == null ? '--' : `${(outcome.yesPrice * 100).toFixed(1)}%`}</strong>
                    <i style={{ width: probabilityWidth(outcome.yesPrice) }} />
                  </span>
                ))}
              </div>
            </article>
          ))}
        </div>
      ) : (
        <SourceRequired
          detail="No verified Polymarket/local-db market is linked to this fixture. The panel will stay empty instead of creating inferred prices."
          rows={[{ source: 'Polymarket local DB / Gamma', status: 'not matched', detail: 'requires event/market title match and real outcome prices' }]}
        />
      )}
    </Panel>
  );
}
