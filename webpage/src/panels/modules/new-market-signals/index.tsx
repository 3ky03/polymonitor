import { Panel } from '@/components/Panel';
import { fetchRuntimeNewMarketSignals } from '@/services/api';
import type { RuntimeNewMarketSignalItem, RuntimeNewMarketSignalsPayload } from '@/types';
import { runtimePanelFromRenderer } from '../helpers';
import type { PanelRenderMap } from '../../types';

function probabilityLabel(value: RuntimeNewMarketSignalItem['initialYesProbability']) {
  if (value === null || value === undefined || value === '') return 'Pending';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return `${Math.round(numeric * 100)}% YES`;
}

function sourceLabel(value?: string | null) {
  const text = String(value || '').trim();
  if (!text) return 'WATCHER';
  return text.replace(/^clob_book_/, '').replace(/_/g, ' ').toUpperCase();
}

function NewMarketSignalsList({
  items,
  selectedMarketId,
  setSelectedMarketId,
}: {
  items: RuntimeNewMarketSignalItem[];
  selectedMarketId: number | null;
  setSelectedMarketId: (marketId: number) => void;
}) {
  if (!items.length) {
    return (
      <div className="wm-empty-state">
        <strong>No new market signals yet.</strong>
        <em>The watcher will baseline on startup, then show newly indexed markets here.</em>
      </div>
    );
  }

  return (
    <div className="wm-new-market-signal-list">
      {items.map((item, index) => {
        const marketId = Number(item.marketId);
        const canSelect = Number.isFinite(marketId) && marketId > 0;
        const active = canSelect && selectedMarketId === marketId;
        const pending = item.initialYesProbability === null || item.initialYesProbability === undefined || item.initialYesProbability === '';
        return (
          <button
            key={`${item.marketId || 'market'}-${item.observedAt || index}`}
            type="button"
            className={`wm-new-market-signal-card${active ? ' active' : ''}`}
            disabled={!canSelect}
            onClick={() => canSelect && setSelectedMarketId(marketId)}
            title={item.title || 'New market'}
          >
            <div className="wm-new-market-signal-main">
              <div className="wm-new-market-signal-meta">
                <span className="wm-new-market-signal-dot" />
                <span>NEW MARKET</span>
                <span>/</span>
                <span>{sourceLabel(item.probabilitySource)}</span>
              </div>
              <strong className="wm-new-market-signal-title">{item.title || 'Untitled market'}</strong>
            </div>
            <span className={`wm-new-market-signal-prob${pending ? ' pending' : ''}`}>
              {probabilityLabel(item.initialYesProbability)}
            </span>
          </button>
        );
      })}
    </div>
  );
}

const renderers: PanelRenderMap = {
  'new-market-signals': {
    render: (ctx) => {
      const payload = ctx.runtimeData['new-market-signals'] as RuntimeNewMarketSignalsPayload | undefined;
      const items = payload?.items || [];
      return (
        <Panel title="NEW MARKETS" badge={payload?.status === 'degraded' ? 'REDIS' : 'LIVE'} status="live" count={items.length} className="wm-new-market-signal-panel">
          <NewMarketSignalsList
            items={items}
            selectedMarketId={ctx.selectedMarketId}
            setSelectedMarketId={ctx.setSelectedMarketId}
          />
        </Panel>
      );
    },
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'new-market-signals',
  title: 'New Market Signals',
  eyebrow: 'market',
  description: 'First-seen markets with initial YES probability.',
  defaultEnabled: true,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeNewMarketSignals(12),
});
