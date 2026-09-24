import { useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import { fetchRuntimeCpiReleaseCalendar } from '@/services/api';
import type { RuntimeCpiCalendarItem, RuntimeCpiReleaseCalendarPayload, RuntimePolymarketMacroMapPayload } from '@/types';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { PanelGlyph, RowGlyph, StatusBadge, signalToneClass } from '../macro-intel';
import type { PanelGlyphName } from '../macro-intel';
import { useSpecialistCopy } from '@/services/specialist-i18n';

function badgeLabel(status?: string | null) {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'ok') return undefined;
  if (normalized === 'degraded') return 'PARTIAL';
  if (normalized === 'warming') return 'WARMING';
  return 'STALE';
}

function panelTone(status?: string | null): 'live' | 'muted' {
  return String(status || '').toLowerCase() === 'ok' ? 'live' : 'muted';
}

function eventKindLabel(kind?: string | null) {
  const value = String(kind || '').toLowerCase();
  if (value === 'cpi') return 'CPI';
  if (value === 'pce') return 'PCE';
  if (value === 'nfp') return 'NFP';
  if (value === 'fomc') return 'FOMC';
  return 'MACRO';
}

function eventIcon(kind?: string | null): PanelGlyphName {
  const value = String(kind || '').toLowerCase();
  if (value === 'cpi' || value === 'pce') return 'cpi';
  if (value === 'fomc') return 'fed';
  if (value === 'nfp') return 'labor';
  return 'calendar';
}

function dateShortLabel(value?: string | null) {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: '2-digit',
    timeZone: 'America/New_York',
  }).format(date);
}

function probabilityLabel(value?: string | number | null) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return `${Math.round(numeric * 100)}%`;
}

function compactHours(value?: string | number | null) {
  const hours = Number(value);
  if (!Number.isFinite(hours)) return '--';
  if (hours < 1) return '<1h';
  if (hours < 48) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}

function EventRow({ item }: { item: RuntimeCpiCalendarItem }) {
  const { copy, formatRelativeTime } = useSpecialistCopy('cpi-release-calendar');
  const kind = String(item.kind || '').toLowerCase();
  return (
    <div className={`wm-cpi-calendar-row ${kind}`}>
      <RowGlyph icon={eventIcon(item.kind)} tone={kind === 'fomc' ? 'watch' : kind === 'cpi' ? 'cool' : 'neutral'} label={eventKindLabel(item.kind)} />
      <div className="wm-cpi-calendar-row-time">
        <StatusBadge tone={kind === 'cpi' ? 'official' : 'neutral'}>{eventKindLabel(item.kind)}</StatusBadge>
        <strong>{dateShortLabel(item.releaseAt)}</strong>
      </div>
      <div className="wm-cpi-calendar-row-main">
        <strong>{item.title || copy('macroRelease', 'Macro release')}</strong>
        <div>
          <span>{item.referencePeriod || copy('referencePending', 'Reference period pending')}</span>
          <span>/</span>
          <span>{item.marketRelevance || copy('marketRelevance', 'Macro market relevance')}</span>
        </div>
      </div>
      <StatusBadge tone={kind === 'fomc' ? 'watch' : 'official'}>{formatRelativeTime(item.releaseAt)}</StatusBadge>
    </div>
  );
}

function CpiReleaseCalendarPanel({ payload, macroPayload: _macroPayload }: { payload?: RuntimeCpiReleaseCalendarPayload | null; macroPayload?: RuntimePolymarketMacroMapPayload | null }) {
  const { copy } = useSpecialistCopy('cpi-release-calendar');
  const [showHelp, setShowHelp] = useState(false);
  const summary = payload?.summary;
  const items = payload?.items || [];
  const riskTone = signalToneClass(summary?.signal || summary?.risk);
  return (
    <Panel
      title={copy('title', 'CPI CALENDAR')}
      titleControls={(
        <button
          type="button"
          className="wm-panel-help-button"
          aria-label={copy('explainAria', 'Explain CPI calendar baseline')}
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
          <strong>{copy('helpTitle', 'CPI Calendar')}</strong>
          <p>{copy('helpText', 'Tracks official BLS, BEA, and Fed release times, then anchors the panel to the top CPI market outcome from the macro map. Consensus is optional and not assumed.')}</p>
        </div>
      ) : null}
      className="wm-market-panel wm-cpi-calendar-panel"
      dataPanelId="cpi-release-calendar"
    >
      <div className={`wm-intel-signal-band ${riskTone}`}>
        <div className="wm-intel-signal-main">
          <PanelGlyph icon="calendar" tone={riskTone} />
          <div className="wm-intel-signal-copy">
            <span>{copy('eventRisk', 'Event Risk')}</span>
            <strong>{summary?.signal || copy('signalWarming', 'CALENDAR WARMING')}</strong>
          </div>
        </div>
        <em>{copy('signalHint', 'Release timing / baseline probability')}</em>
      </div>
      <div className={`wm-cpi-calendar-hero compact ${summary?.risk || 'unknown'}`}>
        <div>
          <span>{copy('timeToEvent', 'Time To Event')}</span>
          <strong>{compactHours(summary?.hoursToEvent)}</strong>
        </div>
        <div>
          <span>{copy('pmktBaseline', 'PMKT Baseline')}</span>
          <strong>{probabilityLabel(summary?.baselineProbability)}</strong>
        </div>
      </div>
      {items.length ? (
        <div className="wm-cpi-calendar-list">
          {items.map((item, index) => (
            <EventRow key={`${item.id || item.kind || 'event'}-${index}`} item={item} />
          ))}
        </div>
      ) : (
        <div className="wm-empty-state">
          <strong>{copy('warming', 'Calendar snapshot warming.')}</strong>
          <em>{copy('warmingText', 'No upcoming CPI/PCE/FOMC/NFP rows are cached yet.')}</em>
        </div>
      )}
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'cpi-release-calendar': {
    render: (ctx) => {
      const payload = ctx.runtimeData['cpi-release-calendar'] as RuntimeCpiReleaseCalendarPayload | undefined;
      const macroPayload = ctx.runtimeData['polymarket-macro-map'] as RuntimePolymarketMacroMapPayload | undefined;
      return <CpiReleaseCalendarPanel payload={payload} macroPayload={macroPayload} />;
    },
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'cpi-release-calendar',
  title: 'CPI Release Calendar & Consensus Baseline',
  eyebrow: 'macro',
  description: 'Official CPI, PCE, NFP, and FOMC release timing with Polymarket implied CPI baseline.',
  defaultEnabled: false,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeCpiReleaseCalendar(8),
});
