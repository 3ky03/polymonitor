import { Panel } from '@/components/Panel';
import type { PanelRenderContext } from '@/types';
import type { PanelRenderMap } from './types';
import { AiMarketWidePanel } from './shared/ai-market-wide';
import { formatCompact, formatPercent } from './shared/formatters';
import { emptyState, levelTotal, orderfilledList, tradeSignalList, whaleTrackerList } from './shared/renderers';
import { focusedTrades, globalMarkets } from './shared/selectors';

function maxLevelSize(levels: Array<{ size?: string | number | null }>) {
  return levels.reduce((max, level) => Math.max(max, Number(level.size) || 0), 0) || 1;
}

function depthRows(levels: Array<{ price?: string | number | null; size?: string | number | null }>, tone: 'bid' | 'ask') {
  const maxSize = maxLevelSize(levels);
  return levels.map((level, index) => {
    const size = Number(level.size) || 0;
    return (
      <div className={`wm-depth-level ${tone}`} key={`${tone}-${index}-${level.price}`}>
        <div className="wm-depth-bar" style={{ width: `${Math.min(100, (size / maxSize) * 100)}%` }} />
        <span>{tone.toUpperCase()}</span>
        <strong>{formatPercent(level.price)}</strong>
        <em>{formatCompact(level.size)}</em>
      </div>
    );
  });
}

function lobPanel(ctx: PanelRenderContext, showDepth: boolean) {
  const lob = ctx.bundle?.lob;
  if (!lob) return emptyState('LOB runtime unavailable for the selected market.');
  if (!showDepth) {
    return (
      <div className="wm-bbo-grid">
        <article className="wm-bbo-card yes">
          <div className="wm-bbo-card-head">
            <span>YES BBO</span>
            <strong>{formatPercent(lob.yes?.bestBid)} / {formatPercent(lob.yes?.bestAsk)}</strong>
          </div>
          <div className="wm-bbo-card-meta">
            <span>spread {formatPercent(lob.yes?.spread)}</span>
            <span>depth {formatCompact(levelTotal([...(lob.yes?.bids || []).slice(0, 4), ...(lob.yes?.asks || []).slice(0, 4)]))}</span>
          </div>
        </article>
        <article className="wm-bbo-card no">
          <div className="wm-bbo-card-head">
            <span>NO BBO</span>
            <strong>{formatPercent(lob.no?.bestBid)} / {formatPercent(lob.no?.bestAsk)}</strong>
          </div>
          <div className="wm-bbo-card-meta">
            <span>spread {formatPercent(lob.no?.spread)}</span>
            <span>depth {formatCompact(levelTotal([...(lob.no?.bids || []).slice(0, 4), ...(lob.no?.asks || []).slice(0, 4)]))}</span>
          </div>
        </article>
      </div>
    );
  }
  return (
    <div className="wm-lob-layout">
      <div className="wm-lob-side-card">
        <div className="wm-lob-head">
          <span>YES BOOK</span>
          <strong>{formatPercent(lob.yes?.bestBid)} / {formatPercent(lob.yes?.bestAsk)}</strong>
        </div>
        <div className="wm-lob-stats">
          <span>spread {formatPercent(lob.yes?.spread)}</span>
          <span>depth {formatCompact(levelTotal([...(lob.yes?.bids || []).slice(0, 6), ...(lob.yes?.asks || []).slice(0, 6)]))}</span>
        </div>
        <div className="wm-depth-ladder">
          {depthRows((lob.yes?.asks || []).slice(0, 4), 'ask')}
          <div className="wm-depth-mid">{formatPercent(lob.yes?.bestAsk)}</div>
          {depthRows((lob.yes?.bids || []).slice(0, 4), 'bid')}
        </div>
      </div>
      <div className="wm-lob-side-card">
        <div className="wm-lob-head">
          <span>NO BOOK</span>
          <strong>{formatPercent(lob.no?.bestBid)} / {formatPercent(lob.no?.bestAsk)}</strong>
        </div>
        <div className="wm-lob-stats">
          <span>spread {formatPercent(lob.no?.spread)}</span>
          <span>depth {formatCompact(levelTotal([...(lob.no?.bids || []).slice(0, 6), ...(lob.no?.asks || []).slice(0, 6)]))}</span>
        </div>
        <div className="wm-depth-ladder">
          {depthRows((lob.no?.asks || []).slice(0, 4), 'ask')}
          <div className="wm-depth-mid">{formatPercent(lob.no?.bestAsk)}</div>
          {depthRows((lob.no?.bids || []).slice(0, 4), 'bid')}
        </div>
      </div>
    </div>
  );
}


export const chainPanelRenderers: PanelRenderMap = {
  'global-orderfilled': {
    render: (ctx) => {
      const marketTitleMap = new Map(globalMarkets(ctx).map((market) => [market.id, market.title]));
      const focusedTitle = ctx.selectedMarket?.title || ctx.bundle?.market?.title || ctx.bootstrap?.featuredMarket?.title;
      return (
        <Panel title="ORDERFILLED FLOW" badge="FOCUSED" status="live" count={focusedTrades(ctx).length} className="wm-orderfilled-panel">
          {orderfilledList(focusedTrades(ctx), 10, (trade) => {
            if (trade.marketTitle) return trade.marketTitle;
            if (trade.marketId && marketTitleMap.has(trade.marketId)) return marketTitleMap.get(trade.marketId);
            if (trade.marketId && ctx.selectedMarket?.id === trade.marketId) return focusedTitle;
            return focusedTitle;
          })}
        </Panel>
      );
    },
  },
  'sample-chain-trades': {
    render: (ctx) => (
      <AiMarketWidePanel ctx={ctx} lens="special" title="SPECIAL RADAR" badge="RADAR" />
    ),
  },
  'bbo-monitor': {
    render: (ctx) => (
      <Panel title="BBO MONITOR" badge="LIVE" status="live">
        {lobPanel(ctx, false)}
      </Panel>
    ),
  },
  'lob-depth': {
    render: (ctx) => (
      <Panel title="LOB DEPTH" badge="LIVE" status="live">
        {lobPanel(ctx, true)}
      </Panel>
    ),
  },
  'whale-tracker': {
    render: (ctx) => (
      <Panel title="WHALE TRACKER" badge="CHAIN" status="live" count={ctx.whaleTrades?.items.length || 0}>
        {whaleTrackerList(ctx.whaleTrades?.items || [], 'No whale trades loaded.')}
      </Panel>
    ),
  },
  'suspicious-flow': {
    render: (ctx) => (
      <Panel title="FLOW WATCH" badge="CHAIN" status="live" count={ctx.suspiciousTrades?.items.length || 0} className="wm-market-panel wm-flow-watch-panel">
        {tradeSignalList(ctx.suspiciousTrades?.items || [], 'No suspicious flow loaded.')}
      </Panel>
    ),
  },
};
