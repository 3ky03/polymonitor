import { useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import type { RuntimeMacroDriverItem, RuntimeMacroDriverPayload, RuntimePolymarketMacroMapPayload } from '@/types';
import { MacroAlertStrip, PanelGlyph, RowGlyph, StatusBadge, signalToneClass } from './macro-intel';
import type { PanelGlyphName } from './macro-intel';
import { useSpecialistCopy } from '@/services/specialist-i18n';

export type MacroDriverConfig = {
  panelId: string;
  title: string;
  badge: string;
  glyph: PanelGlyphName;
  driverLabel: string;
  helpTitle: string;
  helpText: string;
  emptyTitle: string;
  implicationItems: string[];
  linkedCategories: string[];
  linkedTitle: string;
};

function badgeStatus(status?: string | null): 'live' | 'muted' {
  return String(status || '').toLowerCase() === 'ok' ? 'live' : 'muted';
}

function compactNumber(value?: string | number | null) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '--';
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: Math.abs(n) < 10 ? 2 : 1 }).format(n);
}

function valueLabel(item?: RuntimeMacroDriverItem | null) {
  const n = Number(item?.value);
  if (!Number.isFinite(n)) return '--';
  const unit = String(item?.unit || '').trim();
  if (unit === '%' || unit === 'pp' || unit === 'z') return `${n.toFixed(2)}${unit === '%' ? '%' : ''}`;
  if (unit === '$') return `$${n.toFixed(2)}`;
  return compactNumber(n);
}

function changeLabel(item?: RuntimeMacroDriverItem | null) {
  const metric = String(item?.metric || '').toLowerCase();
  const raw = metric === 'pct' ? item?.changePct : item?.change;
  const n = Number(raw);
  if (!Number.isFinite(n)) return '--';
  const suffix = metric === 'pct' ? '%' : '';
  return `${n >= 0 ? '+' : ''}${n.toFixed(Math.abs(n) < 10 ? 2 : 1)}${suffix}`;
}

function rowTone(item?: RuntimeMacroDriverItem | null) {
  const tone = String(item?.tone || '').toLowerCase();
  if (tone === 'hot' || tone === 'cool' || tone === 'watch') return tone;
  return 'neutral';
}

function asGlyph(icon?: string | null): PanelGlyphName {
  const value = String(icon || '').toLowerCase();
  if (value === 'geo' || value === 'radar' || value === 'calendar' || value === 'energy' || value === 'basket' || value === 'market' || value === 'cpi' || value === 'fed' || value === 'growth' || value === 'labor' || value === 'oil' || value === 'gas' || value === 'diesel' || value === 'food' || value === 'home' || value === 'policy' || value === 'rates' || value === 'source') return value;
  return 'source';
}

function MacroDriverRow({ item }: { item: RuntimeMacroDriverItem }) {
  const { shared } = useSpecialistCopy('macro-driver');
  const tone = rowTone(item);
  return (
    <div className={`wm-macro-driver-row ${tone}`}>
      <RowGlyph icon={asGlyph(item.icon)} tone={tone} label={item.label || item.group || shared('macroDriver', 'Macro driver')} />
      <div className="wm-macro-driver-main">
        <span>{String(item.group || item.seriesId || item.key || 'macro').toUpperCase()}</span>
        <strong>{item.label || shared('macroDriver', 'Macro driver')}</strong>
      </div>
      <strong className="wm-macro-driver-value">{valueLabel(item)}</strong>
      <StatusBadge tone={tone}>{changeLabel(item)}</StatusBadge>
    </div>
  );
}

export function MacroDriverPanel({ config, payload, macroPayload: _macroPayload }: { config: MacroDriverConfig; payload?: RuntimeMacroDriverPayload | null; macroPayload?: RuntimePolymarketMacroMapPayload | null }) {
  const { copy, shared } = useSpecialistCopy(config.panelId);
  const [showHelp, setShowHelp] = useState(false);
  const summary = payload?.summary;
  const items = payload?.items || [];
  const signalTone = signalToneClass(summary?.signal || summary?.bias || payload?.status);
  const status = String(payload?.status || 'warming').toLowerCase();
  const badge = status === 'ok' ? undefined : status === 'degraded' ? 'PARTIAL' : 'WARMING';
  const title = copy('title', config.title);
  const emptyTitle = copy('emptyTitle', config.emptyTitle);
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
      status={badgeStatus(payload?.status)}
      count={items.length}
      headerOverlay={showHelp ? (
        <div className="wm-panel-help-popover">
          <strong>{copy('helpTitle', config.helpTitle)}</strong>
          <p>{copy('helpText', config.helpText)}</p>
        </div>
      ) : null}
      className="wm-market-panel wm-macro-driver-panel"
      dataPanelId={config.panelId}
    >
      <div className={`wm-intel-signal-band ${signalTone}`}>
        <div className="wm-intel-signal-main">
          <PanelGlyph icon={config.glyph} tone={signalTone} />
          <div className="wm-intel-signal-copy">
            <span>{copy('driverLabel', config.driverLabel)}</span>
            <strong>{summary?.signal || emptyTitle}</strong>
          </div>
        </div>
        <em>{config.badge}</em>
      </div>
      <MacroAlertStrip hot={summary?.hotCount} cool={summary?.coolCount} watch={summary?.watchCount} />
      <div className="wm-macro-driver-list">
        {items.length ? items.map((item) => <MacroDriverRow key={item.key || item.seriesId || item.label || 'macro-driver'} item={item} />) : (
          <div className="wm-empty-state">
            <strong>{emptyTitle}</strong>
            <em>{shared('panelWarming', 'Seed cache has not warmed this panel yet.')}</em>
          </div>
        )}
      </div>
    </Panel>
  );
}
