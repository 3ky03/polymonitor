import { useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import type { RuntimeMacroRegistryItem, RuntimeMacroRegistryPayload } from '@/types';
import { RowGlyph, StatusBadge, signalToneClass } from './macro-intel';
import type { PanelGlyphName } from './macro-intel';
import { useSpecialistCopy } from '@/services/specialist-i18n';

export type MacroRegistryConfig = {
  panelId: string;
  title: string;
  badge: string;
  glyph: PanelGlyphName;
  helpTitle: string;
  helpText: string;
  emptyTitle: string;
  implicationItems: string[];
};

function panelStatus(status?: string | null): 'live' | 'muted' {
  return String(status || '').toLowerCase() === 'ok' ? 'live' : 'muted';
}

function rowGlyph(item: RuntimeMacroRegistryItem): PanelGlyphName {
  const text = `${item.group || ''} ${item.type || ''} ${item.label || ''}`.toLowerCase();
  if (/(oil|wti|energy)/.test(text)) return 'oil';
  if (/(gasoline|gas)/.test(text)) return 'gas';
  if (/(food|egg|meat)/.test(text)) return 'food';
  if (/(shelter|rent|oer|housing)/.test(text)) return 'home';
  if (/(job|labor|wage|claim|unemployment|nfp)/.test(text)) return 'labor';
  if (/(fed|sofr|funds|fomc)/.test(text)) return 'fed';
  if (/(2y|10y|rate|curve|treasury)/.test(text)) return 'rates';
  if (/(gdp|growth|retail|demand|recession)/.test(text)) return 'growth';
  if (/(tariff|policy|federal register|ustr)/.test(text)) return 'policy';
  if (/(cpi|pce|nowcast|inflation)/.test(text)) return 'cpi';
  return 'source';
}

function rowTone(item: RuntimeMacroRegistryItem) {
  const tone = String(item.tone || '').toLowerCase();
  if (tone === 'hot' || tone === 'cool' || tone === 'watch') return tone;
  return 'neutral';
}

function compactNumber(value?: number | string | null) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '--';
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(number);
}

function displayValue(value?: number | string | null) {
  const text = String(value ?? '').trim();
  return text || '--';
}

function DataMetric({ label, value, tone }: { label: string; value?: number | string | null; tone?: string }) {
  return (
    <span className={tone ? `wm-macro-data-metric ${tone}` : 'wm-macro-data-metric'}>
      <i>{label}</i>
      <strong>{displayValue(value)}</strong>
    </span>
  );
}

function RegistryRow({ item, panelId }: { item: RuntimeMacroRegistryItem; panelId: string }) {
  const { shared } = useSpecialistCopy(panelId);
  const tone = rowTone(item);
  const meta = String(item.group || item.domainTag || item.type || 'macro').toUpperCase();
  const source = String(item.sourceLabel || item.source || 'SOURCE').toUpperCase();
  const domain = String(item.domainTag || item.type || 'MACRO').toUpperCase();
  const rank = item.rank ? String(item.rank).padStart(2, '0') : null;
  return (
    <div className={`wm-macro-registry-row ${tone}`}>
      <RowGlyph icon={rowGlyph(item)} tone={tone} label={item.label || item.group || shared('macroRow', 'Macro row')} />
      <div className="wm-macro-registry-main">
        <div className="wm-macro-registry-meta">
          {rank ? <span className="wm-macro-registry-rank">{rank}</span> : null}
          <span>{meta}</span>
          <span className="wm-macro-registry-source">{source}</span>
          <span className={`wm-macro-registry-tag ${tone}`}>{domain}</span>
          {item.ageLabel ? <span className="wm-macro-registry-age">{item.ageLabel}</span> : null}
        </div>
        <strong>{item.label || shared('macroRegistryRow', 'Macro registry row')}</strong>
      </div>
      <div className="wm-macro-registry-right">
        <strong className="wm-macro-registry-value">{displayValue(item.valueLabel || item.value)}</strong>
        <StatusBadge tone={tone}>{item.changeLabel || item.severityLabel || '--'}</StatusBadge>
      </div>
    </div>
  );
}

export function MacroRegistryPanel({ config, payload }: { config: MacroRegistryConfig; payload?: RuntimeMacroRegistryPayload | null }) {
  const { copy, shared } = useSpecialistCopy(config.panelId);
  const [showHelp, setShowHelp] = useState(false);
  const summary = payload?.summary;
  const items = payload?.items || [];
  const tone = signalToneClass(summary?.signal || summary?.bias || payload?.status);
  const status = String(payload?.status || '').toLowerCase();
  const badge = status && status !== 'ok' ? String(payload?.status || 'WARMING').toUpperCase() : undefined;
  const topMover = summary?.topMover;
  const title = copy('title', config.title);
  return (
    <Panel
      title={title}
      titleControls={(
        <button
          type="button"
          className="wm-panel-help-button"
          aria-label={shared('explainPanel', 'Explain {title}', { title })}
          aria-expanded={showHelp}
          onClick={() => setShowHelp((current) => !current)}
        >
          ?
        </button>
      )}
      badge={badge}
      status={panelStatus(payload?.status)}
      count={items.length}
      headerOverlay={showHelp ? (
        <div className="wm-panel-help-popover">
          <strong>{copy('helpTitle', config.helpTitle)}</strong>
          <p>{copy('helpText', config.helpText)}</p>
        </div>
      ) : null}
      className="wm-market-panel wm-macro-registry-panel"
      dataPanelId={config.panelId}
    >
      <div className="wm-macro-registry-data-strip" aria-label={shared('dataSummary', '{title} data summary', { title })}>
        <DataMetric label={shared('top', 'Top')} value={topMover?.label || summary?.topLabel || '--'} />
        <DataMetric label={shared('value', 'Value')} value={topMover?.valueLabel || topMover?.value || summary?.topValueLabel || '--'} />
        <DataMetric label={shared('move', 'Move')} value={topMover?.changeLabel || summary?.topChangeLabel || '--'} tone={tone} />
        <DataMetric label={shared('sources', 'Sources')} value={`${compactNumber(summary?.coverage)}/${compactNumber(summary?.sourceCount)}`} />
        <DataMetric label={shared('alert', 'Alert')} value={summary?.hotCount ?? 0} tone="hot" />
        <DataMetric label={shared('cool', 'Cool')} value={summary?.coolCount ?? 0} tone="cool" />
        <DataMetric label={shared('watch', 'Watch')} value={summary?.watchCount ?? 0} tone="watch" />
        <DataMetric label={shared('rows', 'Rows')} value={summary?.rowCount ?? items.length} />
      </div>
      <div className="wm-macro-registry-list">
        {items.length ? items.map((item) => <RegistryRow key={item.key || `${item.group}-${item.label}`} item={item} panelId={config.panelId} />) : (
          <div className="wm-empty-state">
            <strong>{copy('emptyTitle', config.emptyTitle)}</strong>
            <em>{shared('registryWarming', 'Seed cache has not composed this registry yet.')}</em>
          </div>
        )}
      </div>
    </Panel>
  );
}
