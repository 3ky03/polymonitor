import { useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import { fetchRuntimeCryptoFundingWatch } from '@/services/api';
import type { RuntimeCryptoFundingAsset, RuntimeCryptoFundingPayload } from '@/types';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { useSpecialistCopy } from '@/services/specialist-i18n';

function absolutePercentLabel(value?: number | null, digits = 4) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return `${Math.abs(numeric).toFixed(digits)}%`;
}

function compactPercentLabel(value?: number | null, digits = 3) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  const abs = Math.abs(numeric);
  const precision = abs >= 1 ? 1 : digits;
  return `${numeric > 0 ? '+' : ''}${numeric.toFixed(precision)}%`;
}

function sourceStatus(payload?: RuntimeCryptoFundingPayload | null) {
  if (!payload?.sources) return payload?.status || 'live';
  const states = Object.values(payload.sources);
  if (states.some((state) => state === 'error' || state === 'missing-url')) return 'degraded';
  if (states.every((state) => state === 'empty')) return 'empty';
  return payload.status || 'live';
}

function groupedAssets(payload?: RuntimeCryptoFundingPayload | null): RuntimeCryptoFundingAsset[] {
  return payload?.assets || [];
}

function venueCount(payload: RuntimeCryptoFundingPayload | null | undefined, assets: RuntimeCryptoFundingAsset[]) {
  if (payload?.venues?.length) return payload.venues.length;
  return new Set(
    assets.flatMap((asset) => asset.quotes || []).map((quote) => quote.exchange || 'Exchange'),
  ).size;
}

function countByBias(assets: RuntimeCryptoFundingAsset[], bias: string) {
  return assets.filter((asset) => asset.bias === bias).length;
}

function averageAbsFundingValue(assets: RuntimeCryptoFundingAsset[]) {
  const values = assets
    .map((asset) => Math.abs(Number(asset.maxAbsFundingPercent)))
    .filter((value) => Number.isFinite(value));
  if (!values.length) return '--';
  const avg = values.reduce((sum, value) => sum + value, 0) / values.length;
  return absolutePercentLabel(avg, 3);
}

function biasLabel(asset: RuntimeCryptoFundingAsset) {
  if (asset.bias === 'longs-pay') return 'Longs Pay';
  if (asset.bias === 'shorts-pay') return 'Shorts Pay';
  if (asset.bias === 'mixed') return 'Mixed';
  return 'Flat';
}

function orderQuotes(asset: RuntimeCryptoFundingAsset, payload?: RuntimeCryptoFundingPayload | null) {
  const venueOrder = payload?.venues || [];
  return [...(asset.quotes || [])].sort((left, right) => {
    const leftIndex = venueOrder.indexOf(left.exchange || 'Exchange');
    const rightIndex = venueOrder.indexOf(right.exchange || 'Exchange');
    return (leftIndex === -1 ? 999 : leftIndex) - (rightIndex === -1 ? 999 : rightIndex);
  });
}

function fundingPressureStats(payload: RuntimeCryptoFundingPayload | null | undefined, assets: RuntimeCryptoFundingAsset[]) {
  const longs = countByBias(assets, 'longs-pay');
  const shorts = countByBias(assets, 'shorts-pay');
  const mixed = countByBias(assets, 'mixed');
  const alertCount = assets.filter((asset) => {
    const maxAbs = Math.abs(Number(asset.maxAbsFundingPercent));
    return asset.tone === 'critical' || asset.tone === 'warning' || maxAbs >= 0.008;
  }).length;
  const topAsset = [...assets]
    .sort((left, right) => Math.abs(Number(right.maxAbsFundingPercent)) - Math.abs(Number(left.maxAbsFundingPercent)))[0];
  const venueTotal = venueCount(payload, assets);

  if (shorts > longs && shorts >= mixed) {
    return {
      label: 'SHORT CROWDING',
      badge: 'SHORTS PAY',
      tone: 'shorts',
      subline: `${shorts} shorts pay / ${longs} longs pay / ${mixed} mixed`,
      alertCount,
      topAsset,
      venueTotal,
    };
  }

  if (longs > shorts && longs >= mixed) {
    return {
      label: 'LONG CROWDING',
      badge: 'LONGS PAY',
      tone: 'longs',
      subline: `${longs} longs pay / ${shorts} shorts pay / ${mixed} mixed`,
      alertCount,
      topAsset,
      venueTotal,
    };
  }

  return {
    label: 'MIXED FUNDING',
    badge: 'MIXED',
    tone: 'mixed',
    subline: `${mixed} mixed / ${longs} longs pay / ${shorts} shorts pay`,
    alertCount,
    topAsset,
    venueTotal,
  };
}

function fundingRowTone(asset: RuntimeCryptoFundingAsset) {
  if (asset.bias === 'longs-pay') return 'up';
  if (asset.bias === 'shorts-pay') return 'down';
  return 'flat';
}

function fundingVenueSummary(asset: RuntimeCryptoFundingAsset) {
  const quotes = orderQuotes(asset);
  const venueTotal = Number(asset.venues || quotes.length || 0);
  const positive = quotes.filter((quote) => quote.direction === 'positive').length;
  const negative = quotes.filter((quote) => quote.direction === 'negative').length;
  const lead = venueTotal ? `${venueTotal} venues` : '--';
  if (positive > negative) return { lead, sub: `${positive} long pay` };
  if (negative > positive) return { lead, sub: `${negative} short pay` };
  return { lead, sub: biasLabel(asset) };
}

function FundingSummary({ payload, assets }: { payload?: RuntimeCryptoFundingPayload | null; assets: RuntimeCryptoFundingAsset[] }) {
  const { copy, shared } = useSpecialistCopy('crypto-funding-watch');
  const stats = fundingPressureStats(payload, assets);
  const topAssetName = stats.topAsset?.asset || stats.topAsset?.symbol || 'watchlist';
  return (
    <section className="wm-funding-summary">
      <div className="wm-market-radar-strip wm-funding-radar-strip">
        <span>
          <strong>{topAssetName}</strong>
          <em>{shared('topMove', 'top move')}</em>
        </span>
        <span>
          <strong>{averageAbsFundingValue(assets)}</strong>
          <em>{copy('averageAbsolute', 'avg abs')}</em>
        </span>
        <span>
          <strong>{stats.alertCount}/{assets.length}</strong>
          <em>{shared('alerts', 'alerts')}</em>
        </span>
      </div>
    </section>
  );
}

function FundingRow({ asset }: { asset: RuntimeCryptoFundingAsset }) {
  const bias = asset.bias || 'flat';
  const tone = asset.tone || 'normal';
  const rowTone = fundingRowTone(asset);
  return (
    <article className={`wm-funding-asset-row bias-${bias} tone-${tone} ${rowTone}`}>
      <div className="wm-funding-row-asset">
        <strong>{asset.asset || asset.symbol || 'CRYPTO'}</strong>
        <span>{biasLabel(asset)}</span>
      </div>
      <div className="wm-funding-row-context">
        <strong>{fundingVenueSummary(asset).lead}</strong>
        <span>{fundingVenueSummary(asset).sub}</span>
      </div>
      <div className="wm-funding-row-value">
        <strong>{compactPercentLabel(asset.maxAbsFundingPercent)}</strong>
        <span className={rowTone}>max</span>
      </div>
      <div className="wm-funding-row-flow">
        <b>{compactPercentLabel(asset.consensusFundingPercent)}</b>
        <em>avg</em>
      </div>
    </article>
  );
}

function FundingList({ payload }: { payload?: RuntimeCryptoFundingPayload | null }) {
  const { copy } = useSpecialistCopy('crypto-funding-watch');
  const assets = groupedAssets(payload);
  if (!assets.length) {
    return (
      <div className="wm-funding-empty-state">
        <span>{copy('standby', 'Standby')}</span>
        <strong>{copy('empty', 'No funding rates loaded yet.')}</strong>
        <em>{sourceStatus(payload).toUpperCase()}</em>
      </div>
    );
  }
  return (
    <div className="wm-funding-monitor">
      <FundingSummary payload={payload} assets={assets} />
      <div className="wm-funding-table">
        <div className="wm-funding-table-body">
          {assets.map((asset) => <FundingRow key={asset.id} asset={asset} />)}
        </div>
      </div>
    </div>
  );
}

function FundingRatePanel({ payload }: { payload?: RuntimeCryptoFundingPayload | null }) {
  const { copy } = useSpecialistCopy('crypto-funding-watch');
  const [showHelp, setShowHelp] = useState(false);
  const assets = payload?.assets || [];
  const degraded = sourceStatus(payload) !== 'ok' && sourceStatus(payload) !== 'live';
  const stats = fundingPressureStats(payload, assets);

  return (
    <Panel
      title={copy('title', 'FUNDING RATE')}
      titleControls={(
        <button
          type="button"
          className="wm-panel-help-button"
          aria-label={copy('explainAria', 'Explain perpetual funding rate')}
          aria-expanded={showHelp}
          onClick={() => setShowHelp((current) => !current)}
        >
          ?
        </button>
      )}
      badge={degraded ? 'STALE' : assets.length ? stats.badge : 'LIVE'}
      status="live"
      count={assets.length ? stats.alertCount || assets.length : 0}
      headerOverlay={showHelp ? (
        <div className="wm-panel-help-popover">
          <strong>{copy('helpTitle', 'Funding Rate')}</strong>
          <p>{copy('helpText', 'Perpetual funding keeps perp prices anchored near spot. Positive funding means longs pay shorts. Negative funding means shorts pay longs.')}</p>
        </div>
      ) : null}
      className="wm-market-panel wm-funding-panel"
    >
      <FundingList payload={payload} />
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'crypto-funding-watch': {
    render: (ctx) => {
      const payload = ctx.runtimeData['crypto-funding-watch'] as RuntimeCryptoFundingPayload | undefined;
      return <FundingRatePanel payload={payload} />;
    },
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'crypto-funding-watch',
  title: 'Crypto Funding Watch',
  eyebrow: 'macro',
  description: 'Cross-venue perpetual funding heatmap with long/short crowding bias.',
  defaultEnabled: true,
}, {
  tier: 'slow',
  intervalMs: 15000,
  fetchData: () => fetchRuntimeCryptoFundingWatch(18),
});
