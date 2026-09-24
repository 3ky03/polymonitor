import { useMemo, useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import { fetchRuntimeCommodityEquityTransmission } from '@/services/api';
import type {
  RuntimeCommodityTapeItem,
  RuntimeCommodityTransmissionPayload,
  RuntimeEquityExposure,
  RuntimeTransmissionChain,
} from '@/types';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { useSpecialistCopy } from '@/services/specialist-i18n';

function statusBadge(payload?: RuntimeCommodityTransmissionPayload | null) {
  const status = String(payload?.status || '').toLowerCase();
  const cacheMode = String(payload?.cacheMode || '').toLowerCase();
  if (cacheMode.includes('stale')) return 'STALE';
  if (status === 'ok') return 'LIVE';
  if (status === 'partial') return 'PARTIAL';
  if (status === 'model') return 'MODEL';
  return status ? status.toUpperCase() : 'MODEL';
}

function panelStatus(payload?: RuntimeCommodityTransmissionPayload | null): 'live' | 'muted' {
  return String(payload?.status || '').toLowerCase() === 'ok' ? 'live' : 'muted';
}

function toneClass(value?: string | null) {
  const tone = String(value || 'neutral').toLowerCase();
  if (tone === 'up' || tone === 'positive' || tone === 'beneficiary') return 'tone-up';
  if (tone === 'down' || tone === 'negative' || tone === 'pressure') return 'tone-down';
  if (tone === 'watch' || tone === 'spread' || tone === 'mixed') return 'tone-watch';
  return 'tone-neutral';
}

function confidenceClass(value?: string | null) {
  const confidence = String(value || 'low').toLowerCase();
  if (confidence === 'high') return 'conf-high';
  if (confidence === 'medium') return 'conf-medium';
  return 'conf-low';
}

function formatPrice(value?: number | null) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--';
  if (value >= 1000) return new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(value);
  if (value >= 100) return value.toFixed(1);
  if (value >= 1) return value.toFixed(2);
  return value.toFixed(4);
}

function formatScore(value?: number | null) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}`;
}

function CommodityChip({ item }: { item: RuntimeCommodityTapeItem }) {
  return (
    <div className={`wm-commodity-chip ${toneClass(item.tone)}`}>
      <span>{item.label}</span>
      <strong>{formatPrice(item.price)}</strong>
      <em>{item.changeLabel || '--'}</em>
    </div>
  );
}

function ExposureTag({ exposure }: { exposure: RuntimeEquityExposure }) {
  const direction = String(exposure.direction || 'weak').toLowerCase();
  const label = direction === 'positive'
    ? 'BENEFIT'
    : direction === 'negative'
      ? 'PRESSURE'
      : direction === 'spread'
        ? 'SPREAD'
        : 'WEAK';
  const confidence = String(exposure.confidence || 'low').toUpperCase();
  const score = formatScore(exposure.score);
  return (
    <span
      className={`wm-transmission-exposure ${toneClass(direction)} ${confidenceClass(exposure.confidence)}`}
      title={`${exposure.ticker} ${label} ${score} ${confidence}`}
    >
      <b>{exposure.ticker}</b>
      <i>{score}</i>
      <em>{confidence}</em>
    </span>
  );
}

function ExposureColumn({ title, items, empty }: { title: string; items?: RuntimeEquityExposure[]; empty: string }) {
  const visible = (items || []).slice(0, 3);
  const hiddenCount = Math.max(0, (items || []).length - visible.length);
  return (
    <div className="wm-transmission-exposure-col">
      <span>{title}</span>
      <div>
        {visible.length ? visible.map((item) => <ExposureTag exposure={item} key={`${title}-${item.ticker}`} />) : <em>{empty}</em>}
        {hiddenCount ? <small>+{hiddenCount} more</small> : null}
      </div>
    </div>
  );
}

function LinkedMarketStrip({ chain }: { chain: RuntimeTransmissionChain }) {
  const markets = (chain.linkedMarkets || []).slice(0, 2);
  if (!markets.length) return null;
  return (
    <div className="wm-transmission-linked">
      {markets.map((market, index) => (
        <span key={`${market.id || market.query || index}`}>
          <b>PMKT</b>
          {market.title || market.query || 'linked market'}
        </span>
      ))}
    </div>
  );
}

function TransmissionRow({ chain }: { chain: RuntimeTransmissionChain }) {
  const { copy } = useSpecialistCopy('commodity-equity-transmission');
  const shock = typeof chain.shockPct === 'number' && Number.isFinite(chain.shockPct)
    ? `${chain.shockPct >= 0 ? '+' : ''}${chain.shockPct.toFixed(2)}%`
    : chain.shockLabel || '--';
  return (
    <article className={`wm-transmission-row ${toneClass(chain.tone)}`}>
      <div className="wm-transmission-rail" />
      <div className="wm-transmission-main">
        <div className="wm-transmission-topline">
          <div className="wm-transmission-meta">
            <span>{chain.commodityId}</span>
            <b>{chain.lagLabel || 'lag model'}</b>
            <em className={confidenceClass(chain.confidence)}>{String(chain.confidence || 'low').toUpperCase()}</em>
          </div>
          <div className="wm-transmission-value">
            <strong className={toneClass(chain.tone)}>{shock}</strong>
            <span>{chain.demandRegime || copy('mixedDemand', 'mixed demand')}</span>
          </div>
        </div>
        <strong title={chain.chainLabel}>{chain.chainLabel}</strong>
        <p title={chain.formula || undefined}>{chain.formula || copy('formula', 'commodity move * exposure * pass-through * pricing power')}</p>
        <div className="wm-transmission-columns">
          <ExposureColumn title={copy('winners', 'WINNERS')} items={chain.winners} empty={copy('none', 'none')} />
          <ExposureColumn title={copy('losers', 'LOSERS')} items={chain.losers} empty={copy('none', 'none')} />
          <ExposureColumn title={copy('spread', 'SPREAD')} items={chain.spreadWatch} empty={copy('notModeled', 'not modeled')} />
        </div>
        <LinkedMarketStrip chain={chain} />
      </div>
    </article>
  );
}

function CommodityEquityTransmissionPanel({ payload }: { payload?: RuntimeCommodityTransmissionPayload | null }) {
  const { copy, shared } = useSpecialistCopy('commodity-equity-transmission');
  const [showHelp, setShowHelp] = useState(false);
  const transmissions = payload?.transmissions || [];
  const commodities = payload?.commodities || [];
  const summary = payload?.summary;
  const bias = String(summary?.bias || payload?.status || 'model').toLowerCase();
  const sortedTransmissions = useMemo(() => {
    return [...transmissions].sort((a, b) => Math.abs(Number(b.shockPct || 0)) - Math.abs(Number(a.shockPct || 0)));
  }, [transmissions]);

  return (
    <Panel
      title={copy('title', 'COMMODITY FLOW')}
      titleControls={(
        <button
          type="button"
          className="wm-panel-help-button"
          aria-label={copy('explainAria', 'Explain commodity equity transmission')}
          aria-expanded={showHelp}
          onClick={() => setShowHelp((current) => !current)}
        >
          ?
        </button>
      )}
      badge={statusBadge(payload)}
      status={panelStatus(payload)}
      count={transmissions.length}
      headerOverlay={showHelp ? (
        <div className="wm-panel-help-popover">
          <strong>{copy('helpTitle', 'Commodity Equity Transmission')}</strong>
          <p>{copy('helpText', 'Maps live commodity moves into curated equity winners, losers, and spread-dependent names. It is a transmission model, not a chokepoint map or investment advice.')}</p>
        </div>
      ) : null}
      className="wm-market-panel wm-commodity-transmission-panel wm-monitor-panel"
      dataPanelId="commodity-equity-transmission"
    >
      <div className={`wm-transmission-signal ${toneClass(bias)}`}>
        <div>
          <span>{summary?.signalLabel || copy('signalLabel', 'COMMODITY SHOCK MAP')}</span>
          <strong>{summary?.signal || copy('signalWarming', 'Transmission model warming')}</strong>
        </div>
        <em>{summary?.topShockLabel || '--'} {summary?.topShockChangeLabel || ''}</em>
      </div>

      <div className="wm-transmission-scanbar">
        <span><b>{summary?.positiveCount ?? 0}</b><em>{copy('benefit', 'BENEFIT')}</em></span>
        <span><b>{summary?.negativeCount ?? 0}</b><em>{copy('pressure', 'PRESSURE')}</em></span>
        <span><b>{summary?.spreadCount ?? 0}</b><em>{copy('spread', 'SPREAD')}</em></span>
        <span><b>{summary?.liveCommodityCount ?? 0}</b><em>{shared('live', 'LIVE')}</em></span>
      </div>

      {commodities.length ? (
        <div className="wm-commodity-tape">
          {commodities.slice(0, 8).map((item) => <CommodityChip item={item} key={item.id} />)}
        </div>
      ) : null}

      <div className="wm-transmission-list">
        {sortedTransmissions.length ? sortedTransmissions.map((chain) => <TransmissionRow chain={chain} key={chain.id} />) : (
          <div className="wm-empty-state">
            <strong>{copy('warming', 'TRANSMISSION MODEL WARMING')}</strong>
            <em>{copy('warmingText', 'Commodity quotes are unavailable; curated exposure map will appear once the runtime snapshot returns.')}</em>
          </div>
        )}
      </div>
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'commodity-equity-transmission': {
    render: (ctx) => (
      <CommodityEquityTransmissionPanel
        payload={ctx.runtimeData['commodity-equity-transmission'] as RuntimeCommodityTransmissionPayload | undefined}
      />
    ),
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'commodity-equity-transmission',
  title: 'Commodity Equity Transmission',
  eyebrow: 'finance',
  description: 'Maps commodity shocks into equity beneficiaries, cost-pressure names, spread-watch names, and related Polymarket themes.',
  defaultEnabled: true,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeCommodityEquityTransmission(8),
});
