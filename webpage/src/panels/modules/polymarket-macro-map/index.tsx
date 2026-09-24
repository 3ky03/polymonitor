import { useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import { fetchRuntimePolymarketMacroMap } from '@/services/api';
import type {
  RuntimePolymarketMacroMapItem,
  RuntimePolymarketMacroMapPayload,
} from '@/types';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { PanelGlyph, RowGlyph, StatusBadge, signalToneClass } from '../macro-intel';
import type { PanelGlyphName } from '../macro-intel';
import { useSpecialistCopy } from '@/services/specialist-i18n';

function badgeLabel(status?: string | null) {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'ok') return 'LIVE';
  if (normalized === 'degraded') return 'PARTIAL';
  if (normalized === 'empty') return 'EMPTY';
  return 'STALE';
}

function panelTone(status?: string | null): 'live' | 'muted' {
  return String(status || '').toLowerCase() === 'ok' ? 'live' : 'muted';
}

function numberLabel(value?: string | number | null) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  if (Math.abs(numeric) >= 1000) {
    return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(numeric);
  }
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(numeric);
}

function probabilityLabel(value?: string | number | null) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return `${Math.round(numeric * 100)}%`;
}

function catalystLabel(payload: RuntimePolymarketMacroMapPayload | null | undefined, copy: ReturnType<typeof useSpecialistCopy>['copy'], formatRelativeTime: ReturnType<typeof useSpecialistCopy>['formatRelativeTime']) {
  const catalyst = payload?.summary?.topCatalyst;
  if (!catalyst?.endDate) return copy('noDatedCatalyst', 'No dated catalyst');
  return formatRelativeTime(catalyst.endDate);
}

function categoryIcon(value?: string | null): PanelGlyphName {
  const text = String(value || '').toLowerCase();
  if (text.includes('energy') || text.includes('oil')) return 'energy';
  if (text.includes('fed') || text.includes('rate')) return 'fed';
  if (text.includes('cpi') || text.includes('inflation')) return 'cpi';
  if (text.includes('growth') || text.includes('recession')) return 'growth';
  if (text.includes('labor') || text.includes('jobs')) return 'labor';
  return 'market';
}

function signalLabel(value?: string | null) {
  return String(value || 'WARMING')
    .replace(/oil\s*\/\s*energy/gi, 'ENERGY')
    .replace(/cpi\s*\/\s*inflation/gi, 'CPI')
    .replace(/growth\s*\/\s*recession/gi, 'GROWTH')
    .replace(/fed\s*\/\s*rates/gi, 'FED');
}

function clusterLabel(value?: string | null) {
  return signalLabel(value).replace(' CLUSTER ACTIVE', '');
}

function MacroMarketRow({ item }: { item: RuntimePolymarketMacroMapItem }) {
  const { copy, shared, formatRelativeTime } = useSpecialistCopy('polymarket-macro-map');
  const topOutcome = item.topOutcomes?.[0];
  const category = item.categoryLabels?.[0] || 'Macro';
  const outcomeLabel = String(topOutcome?.label || '').trim();
  const tone = signalToneClass(category);
  return (
    <div className={`wm-macro-map-row ${tone}`}>
      <RowGlyph icon={categoryIcon(category)} tone={tone} label={category} />
      <div className="wm-macro-map-row-main">
        <div className="wm-macro-map-meta">
          <span>{category.toUpperCase()}</span>
          <span>/</span>
          <span>{item.endDate ? formatRelativeTime(item.endDate) : shared('open', 'OPEN')}</span>
          <span>/</span>
          <span>VOL {numberLabel(item.volume24h)}</span>
        </div>
        <strong>{item.title || copy('untitledMarket', 'Untitled macro market')}</strong>
        <div className="wm-macro-map-subline">
          {(item.marketTypes || []).slice(0, 2).join(' / ') || copy('macroRoute', 'Polymarket macro route')}
        </div>
      </div>
      <div className="wm-macro-map-prob">
        <StatusBadge tone="market">{probabilityLabel(topOutcome?.yesPrice)}</StatusBadge>
        <em>{outcomeLabel && outcomeLabel.length <= 14 ? outcomeLabel : 'YES'}</em>
      </div>
    </div>
  );
}

function MacroMarketList({ items }: { items: RuntimePolymarketMacroMapItem[] }) {
  const { copy } = useSpecialistCopy('polymarket-macro-map');
  if (!items.length) {
    return (
      <div className="wm-empty-state wm-registry-empty">
        <strong>{copy('empty', 'No macro market cluster found.')}</strong>
        <em>{copy('emptyText', 'Gamma feed is available, but no active CPI/Fed/GDP/oil markets matched the current terms.')}</em>
      </div>
    );
  }
  return (
    <div className="wm-macro-map-list">
      {items.map((item, index) => (
        <MacroMarketRow key={`${item.eventId || item.slug || 'macro'}-${index}`} item={item} />
      ))}
    </div>
  );
}

function PolymarketMacroMapPanel({ payload }: { payload?: RuntimePolymarketMacroMapPayload | null }) {
  const { copy, formatRelativeTime } = useSpecialistCopy('polymarket-macro-map');
  const [showHelp, setShowHelp] = useState(false);
  const items = payload?.items || [];
  const summary = payload?.summary;
  return (
    <Panel
      title={copy('title', 'PMKT MACRO MAP')}
      titleControls={(
        <button
          type="button"
          className="wm-panel-help-button"
          aria-label={copy('explainAria', 'Explain macro market map')}
          aria-expanded={showHelp}
          onClick={() => setShowHelp((current) => !current)}
        >
          ?
        </button>
      )}
      badge={badgeLabel(payload?.status)}
      status={panelTone(payload?.status)}
      count={summary?.activeCount ?? items.length}
      headerOverlay={showHelp ? (
        <div className="wm-panel-help-popover">
          <strong>{copy('helpTitle', 'Macro Market Map')}</strong>
          <p>{copy('helpText', 'Routes active Polymarket events into CPI, Fed, growth, labor, and energy clusters so macro signals can be tied back to tradable markets.')}</p>
        </div>
      ) : null}
      className="wm-market-panel wm-macro-map-panel"
      dataPanelId="polymarket-macro-map"
    >
      <div className={`wm-intel-signal-band ${signalToneClass(summary?.signal)}`}>
        <div className="wm-intel-signal-main">
          <PanelGlyph icon="radar" tone={signalToneClass(summary?.signal)} />
          <div className="wm-intel-signal-copy">
            <span>{copy('signal', 'Signal')}</span>
            <strong>{signalLabel(summary?.signal)}</strong>
          </div>
        </div>
        <em>{copy('activeRoute', 'Polymarket macro route / {count} active', { count: summary?.activeCount ?? items.length })}</em>
      </div>
      <div className="wm-macro-map-summary">
        <div>
          <span><i>◎</i> {copy('coverage', 'PMKT Coverage')}</span>
          <strong>{summary?.activeCount ?? items.length}</strong>
        </div>
        <div>
          <span><i>◷</i> {copy('topCatalyst', 'Top Catalyst')}</span>
          <strong>{catalystLabel(payload, copy, formatRelativeTime)}</strong>
        </div>
        <div>
          <span><i>◎</i> {copy('topCluster', 'Top Cluster')}</span>
          <strong>{clusterLabel(summary?.topCategory || 'Macro')}</strong>
        </div>
      </div>
      <MacroMarketList items={items} />
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'polymarket-macro-map': {
    render: (ctx) => {
      const payload = ctx.runtimeData['polymarket-macro-map'] as RuntimePolymarketMacroMapPayload | undefined;
      return <PolymarketMacroMapPanel payload={payload} />;
    },
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'polymarket-macro-map',
  title: 'Polymarket Macro Market Map',
  eyebrow: 'macro',
  description: 'Active CPI, Fed, growth, labor, and energy market clusters from Polymarket.',
  defaultEnabled: false,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimePolymarketMacroMap(12),
});
