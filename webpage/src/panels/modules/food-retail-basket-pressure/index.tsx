import { useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import { fetchRuntimeFoodRetailBasket } from '@/services/api';
import type { RuntimeFoodBasketItem, RuntimeFoodRetailBasketPayload, RuntimePolymarketMacroMapPayload } from '@/types';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { PanelGlyph, RowGlyph, StatusBadge, signalToneClass } from '../macro-intel';
import type { PanelGlyphName } from '../macro-intel';
import { useSpecialistCopy } from '@/services/specialist-i18n';

function badgeLabel(status?: string | null) {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'ok') return undefined;
  if (normalized === 'degraded') return 'PARTIAL';
  return 'WARMING';
}

function panelTone(status?: string | null): 'live' | 'muted' {
  return String(status || '').toLowerCase() === 'ok' ? 'live' : 'muted';
}

function pctLabel(value?: string | number | null) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '--';
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
}

function indexLabel(value?: string | number | null) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '--';
  return n.toFixed(1);
}

function toneClass(value?: string | number | null) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 'flat';
  if (n >= 0.35) return 'hot';
  if (n <= -0.2) return 'cool';
  return 'flat';
}

function componentCode(item: RuntimeFoodBasketItem) {
  const key = String(item.key || '').toLowerCase();
  if (key === 'food') return 'FOOD';
  if (key === 'home') return 'HOME';
  if (key === 'meat_eggs') return 'MEAT';
  if (key === 'fruit_veg') return 'F/V';
  if (key === 'eggs') return 'EGGS';
  return 'CPI';
}

function componentIcon(item: RuntimeFoodBasketItem): PanelGlyphName {
  const key = String(item.key || item.label || '').toLowerCase();
  if (key.includes('egg') || key.includes('meat') || key.includes('fruit') || key.includes('food')) return 'food';
  return 'basket';
}

function FoodBasketRow({ item }: { item: RuntimeFoodBasketItem }) {
  const { copy } = useSpecialistCopy('food-retail-basket-pressure');
  const tone = toneClass(item.momPct);
  return (
    <div className={`wm-food-basket-row ${tone}`}>
      <RowGlyph icon={componentIcon(item)} tone={tone === 'flat' ? 'neutral' : tone} label={item.label || 'Food component'} />
      <div>
        <span>{componentCode(item)}</span>
        <strong>{item.label || copy('component', 'Food CPI component')}</strong>
      </div>
      <StatusBadge tone={tone === 'flat' ? 'neutral' : tone}>{pctLabel(item.momPct)}</StatusBadge>
      <em>{pctLabel(item.yoyPct)} Y</em>
    </div>
  );
}

function FoodRetailBasketPanel({ payload, macroPayload: _macroPayload }: { payload?: RuntimeFoodRetailBasketPayload | null; macroPayload?: RuntimePolymarketMacroMapPayload | null }) {
  const { copy } = useSpecialistCopy('food-retail-basket-pressure');
  const [showHelp, setShowHelp] = useState(false);
  const summary = payload?.summary;
  const items = payload?.items || [];
  const topMover = summary?.topMover;
  const signalTone = signalToneClass(summary?.signal);
  return (
    <Panel
      title={copy('title', 'FOOD / RETAIL')}
      titleControls={(
        <button
          type="button"
          className="wm-panel-help-button"
          aria-label={copy('explainAria', 'Explain food basket data source')}
          aria-expanded={showHelp}
          onClick={() => setShowHelp((current) => !current)}
        >
          ?
        </button>
      )}
      badge={badgeLabel(payload?.status)}
      status={panelTone(payload?.status)}
      count={items.length}
      headerOverlay={showHelp ? (
        <div className="wm-panel-help-popover">
          <strong>{copy('helpTitle', 'Food Basket Pressure')}</strong>
          <p>{copy('helpText', 'Uses free official FRED/BLS CPI food components as a seed-first pressure gauge. It is not a live retailer price scraper, so it is best for CPI basket direction and Polymarket macro context.')}</p>
        </div>
      ) : null}
      className="wm-market-panel wm-food-basket-panel"
      dataPanelId="food-retail-basket-pressure"
    >
      <div className={`wm-intel-signal-band ${signalTone}`}>
        <div className="wm-intel-signal-main">
          <PanelGlyph icon="basket" tone={signalTone} />
          <div className="wm-intel-signal-copy">
            <span>{copy('driver', 'Food CPI Driver')}</span>
            <strong>{summary?.signal || copy('signalWarming', 'FOOD WARMING')}</strong>
          </div>
        </div>
        <em>{copy('coverage', 'Coverage {count}', { count: summary?.coverage ?? items.length })}</em>
      </div>
      <div className="wm-food-basket-metrics">
        <StatusBadge tone={Number(summary?.pressureScore) > 0 ? 'hot' : Number(summary?.pressureScore) < 0 ? 'cool' : 'neutral'}>{`PRESSURE ${pctLabel(summary?.pressureScore)}`}</StatusBadge>
        <StatusBadge tone="official">{`COVERAGE ${summary?.coverage ?? items.length}`}</StatusBadge>
      </div>
      {items.length ? (
        <div className="wm-food-basket-grid">
          {items.map((item) => <FoodBasketRow key={item.key || item.seriesId || item.label || 'food'} item={item} />)}
        </div>
      ) : (
        <div className="wm-empty-state">
          <strong>{copy('warming', 'Food basket snapshot warming.')}</strong>
          <em>{copy('warmingText', 'Food component data has not been seeded yet.')}</em>
        </div>
      )}
      <div className="wm-food-basket-top">
        <span>{copy('topMover', 'Top mover')}</span>
        <strong>{topMover?.label || copy('awaitingData', 'Awaiting CPI component data')}</strong>
        <em>{topMover ? `${pctLabel(topMover.momPct)} MoM / ${indexLabel(topMover.value)} idx` : '--'}</em>
      </div>
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'food-retail-basket-pressure': {
    render: (ctx) => <FoodRetailBasketPanel payload={ctx.runtimeData['food-retail-basket-pressure'] as RuntimeFoodRetailBasketPayload | undefined} macroPayload={ctx.runtimeData['polymarket-macro-map'] as RuntimePolymarketMacroMapPayload | undefined} />,
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'food-retail-basket-pressure',
  title: 'Food & Retail Basket Pressure',
  eyebrow: 'macro',
  description: 'Official CPI food-component pressure for inflation market positioning.',
  defaultEnabled: false,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeFoodRetailBasket(8),
});
