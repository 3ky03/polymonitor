import { InfoDot } from '../components/InfoDot';
import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupOddsSnapshot, WorldCupPolymarketMarket } from '../types';
import { formatCompact, percentLabel, probabilityLabel, probabilityWidth } from './formatters';

export function MarketBoardPanel({
  markets,
  odds,
}: {
  markets: WorldCupPolymarketMarket[];
  odds: WorldCupOddsSnapshot[];
}) {
  const firstMarket = markets[0] || null;
  const totalVolume = markets.reduce((sum, market) => sum + (market.volume24h || 0), 0);
  return (
    <Panel
      title="MARKET BOARD"
      count={markets.length}
      titleControls={<InfoDot label="Only verified local DB / Polymarket market links are shown. No inferred market rows are generated." />}
      className="wm-worldcup-panel wm-worldcup-market-board-panel"
    >
      <div className="wm-worldcup-board-stats">
        <span><em>24H VOL</em><strong>{formatCompact(totalVolume)}</strong></span>
        <span><em>LINKS</em><strong>{markets.length}</strong></span>
        <span><em>BOOKS</em><strong>{odds.length}</strong></span>
        <span><em>CONF</em><strong>{firstMarket ? percentLabel(firstMarket.confidence * 100, 0) : '--'}</strong></span>
      </div>
      {markets.slice(0, 4).map((market, index) => (
        <article className="wm-worldcup-market-card" key={`${market.eventId || market.title}`}>
          <div className="wm-worldcup-card-head">
            <span>{formatCompact(market.volume24h)} · {Math.round(market.confidence * 100)} conf</span>
            <b>{index === 0 ? 'VERIFIED' : String(market.source || 'market').toUpperCase()}</b>
          </div>
          {market.marketUrl || market.tradeUrl ? (
            <a href={market.tradeUrl || market.marketUrl || '#'} target="_blank" rel="noreferrer">
              <strong>{market.title}</strong>
            </a>
          ) : (
            <strong>{market.title}</strong>
          )}
          <div className="wm-worldcup-prob-grid">
            {market.outcomes.slice(0, 3).map((outcome) => (
              <span key={outcome.name}>
                <em>{outcome.name}</em>
                <strong>{probabilityLabel(outcome.yesPrice)}</strong>
                <i style={{ width: probabilityWidth(outcome.yesPrice) }} />
              </span>
            ))}
          </div>
        </article>
      ))}
      {!markets.length ? (
        <SourceRequired
          detail="No trusted market row is available for this fixture. The board is intentionally empty until local DB/Gamma returns a matched event."
          rows={[{ source: 'Polymarket local DB / Gamma', status: 'not matched', detail: 'real outcomes and volume required' }]}
        />
      ) : null}
    </Panel>
  );
}
