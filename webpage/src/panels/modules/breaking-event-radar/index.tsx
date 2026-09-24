import { useMemo, useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import { fetchRuntimeBreakingEventRadar } from '@/services/api';
import type { RuntimeBreakingEventRadarItem, RuntimeBreakingEventRadarPayload } from '@/types';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { useSpecialistCopy } from '@/services/specialist-i18n';

type SortMode = 'velocity' | 'latest' | 'markets';

function statusBadge(payload?: RuntimeBreakingEventRadarPayload | null) {
  const mode = String(payload?.cacheMode || '').toLowerCase();
  const status = String(payload?.status || '').toLowerCase();
  if (mode.includes('stale') || mode.includes('preserved')) return 'STALE';
  if (mode.includes('seed')) return status === 'degraded' ? 'PARTIAL' : 'SEED';
  if (status === 'empty' || status === 'warming') return 'WARM';
  return status === 'degraded' ? 'PARTIAL' : 'LIVE';
}

function severityClass(item: RuntimeBreakingEventRadarItem) {
  const severity = String(item.severity || '').toLowerCase();
  if (severity === 'alert') return 'alert';
  if (severity === 'watch') return 'watch';
  return 'normal';
}

function score(value?: number | string | null) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? String(Math.round(numeric)) : '--';
}

function clamp(value: number, min = 0, max = 100) {
  return Math.max(min, Math.min(max, value));
}

function marketCount(item: RuntimeBreakingEventRadarItem) {
  return (item.markets || []).length || (item.relatedPolymarketMarketIds || []).length || 0;
}

function sortItems(items: RuntimeBreakingEventRadarItem[], mode: SortMode) {
  const rows = [...items];
  if (mode === 'latest') {
    rows.sort((a, b) => String(b.eventTime || '').localeCompare(String(a.eventTime || '')));
    return rows;
  }
  if (mode === 'markets') {
    rows.sort((a, b) => marketCount(b) - marketCount(a) || Number(b.velocityScore || 0) - Number(a.velocityScore || 0));
    return rows;
  }
  rows.sort((a, b) => Number(b.velocityScore || 0) - Number(a.velocityScore || 0) || String(b.eventTime || '').localeCompare(String(a.eventTime || '')));
  return rows;
}

function nextMode(mode: SortMode): SortMode {
  if (mode === 'velocity') return 'latest';
  if (mode === 'latest') return 'markets';
  return 'velocity';
}

function modeLabel(mode: SortMode, shared: ReturnType<typeof useSpecialistCopy>['shared']) {
  if (mode === 'latest') return shared('latest', 'Latest');
  if (mode === 'markets') return 'PMKT';
  return shared('heat', 'Heat');
}

function openSource(url?: string | null) {
  const target = String(url || '').trim();
  if (target) window.open(target, '_blank', 'noopener,noreferrer');
}

function heatBars(items: RuntimeBreakingEventRadarItem[]) {
  const values = items.slice(0, 14).map((item) => clamp(Number(item.velocityScore || item.mentionCount || 0)));
  return values.length ? values : [18, 24, 31, 44, 38, 52, 47, 61, 55, 69, 63, 75, 71, 84];
}

function topicInitial(value?: string | null) {
  return String(value || 'E').trim().slice(0, 1).toUpperCase() || 'E';
}

function RadarScope({ items, payload }: { items: RuntimeBreakingEventRadarItem[]; payload?: RuntimeBreakingEventRadarPayload | null }) {
  const { copy } = useSpecialistCopy('breaking-event-radar');
  const top = items[0];
  const heat = clamp(Number(payload?.summary?.topVelocity || top?.velocityScore || 0));
  const alerts = clamp(Number(payload?.summary?.alerts || 0), 0, 9);
  const bars = heatBars(items);
  return (
    <div className="wm-breaking-scope">
      <div className="wm-breaking-orbit" style={{ '--heat': `${heat}%` } as any}>
        <span className="wm-breaking-orbit-core">{score(heat)}</span>
        {items.slice(0, 5).map((item, index) => (
          <i
            key={item.id || `${item.entity}-${index}`}
            className={`wm-breaking-orbit-dot ${severityClass(item)}`}
            style={{ '--dot-index': index } as any}
          >
            {topicInitial(item.topic || item.entity)}
          </i>
        ))}
      </div>
      <div className="wm-breaking-pulse-stack" aria-hidden="true">
        {bars.map((value, index) => (
          <span key={index} style={{ height: `${18 + value * 0.74}%`, animationDelay: `${index * 90}ms` }} />
        ))}
      </div>
      <div className="wm-breaking-scope-copy">
        <em>{copy('topEvent', 'TOP EVENT')}</em>
        <strong>{payload?.summary?.topEntity || top?.entity || '--'}</strong>
        <span>{copy('trackedSummary', '{alerts} alerts / {count} tracked', { alerts, count: items.length })}</span>
      </div>
    </div>
  );
}

function RadarRow({ item }: { item: RuntimeBreakingEventRadarItem }) {
  const { copy, shared } = useSpecialistCopy('breaking-event-radar');
  const severity = severityClass(item);
  const tags = (item.tags || []).slice(0, 2);
  const points = [
    Number(item.mentionCount15m || 0),
    Number(item.mentionCount1h || 0),
    Number(item.mentionCount24h || 0) / 8,
    Number(item.sourceDiversity || 0) * 16,
    Number(item.velocityScore || 0),
  ].map((value) => clamp(Number.isFinite(value) ? value : 0, 7, 100));
  return (
    <button type="button" className={`wm-breaking-card ${severity}`} onClick={() => openSource(item.sourceUrl)}>
      <span className="wm-breaking-card-rail">
        <i>{severity === 'alert' ? '!' : severity === 'watch' ? '?' : 'i'}</i>
        <b>{score(item.velocityScore)}</b>
      </span>
      <span className="wm-breaking-card-main">
        <span className="wm-evidence-meta">
          <b>{item.entity || shared('event', 'Event')}</b>
          <em>{item.source || item.country || 'GDELT'}</em>
          <i className={severity}>{severity.toUpperCase()}</i>
          {tags.map((tag) => <i key={tag}>{tag.toUpperCase()}</i>)}
        </span>
        <strong>{item.title || copy('sourceWarming', 'Breaking source warming')}</strong>
        <small>{item.summary || copy('evidenceStream', '{region} evidence stream', { region: item.country || shared('global', 'Global') })}</small>
      </span>
      <span className="wm-breaking-card-viz" aria-hidden="true">
        {points.map((value, index) => <i key={index} style={{ height: `${value}%` }} />)}
        <em>{marketCount(item)}M</em>
      </span>
    </button>
  );
}

function BreakingEventRadarPanel({ payload }: { payload?: RuntimeBreakingEventRadarPayload | null }) {
  const { copy, shared, formatRelativeTime } = useSpecialistCopy('breaking-event-radar');
  const [showHelp, setShowHelp] = useState(false);
  const [sortMode, setSortMode] = useState<SortMode>('velocity');
  const items = useMemo(() => sortItems(payload?.items || [], sortMode), [payload?.items, sortMode]);
  return (
    <Panel
      title={copy('title', 'BREAKING RADAR')}
      titleControls={<button type="button" className="wm-panel-help-button" aria-label={copy('explainAria', 'Explain breaking radar')} aria-expanded={showHelp} onClick={() => setShowHelp((value) => !value)}>?</button>}
      controls={<button type="button" className="wm-evidence-sort-button" aria-label={copy('sortAria', 'Change breaking radar sort')} onClick={() => setSortMode((value) => nextMode(value))}>{modeLabel(sortMode, shared)}</button>}
      badge={statusBadge(payload)}
      status={payload?.status === 'ok' ? 'live' : 'muted'}
      count={items.length}
      headerOverlay={showHelp ? (
        <div className="wm-panel-help-popover">
          <strong>{copy('helpTitle', 'Breaking Event Radar')}</strong>
          <p>{copy('helpText', 'Seeded GDELT headlines plus Wikimedia pageview proxies, ranked by velocity and linked to related Polymarket markets when available.')}</p>
        </div>
      ) : null}
      className="wm-market-panel wm-evidence-panel wm-breaking-radar-panel"
      dataPanelId="breaking-event-radar"
    >
      <RadarScope items={items} payload={payload} />
      <div className="wm-breaking-feed">
        {items.length ? items.map((item) => <RadarRow key={item.id || `${item.entity}-${item.title}`} item={item} />) : (
          <div className="wm-registry-empty"><strong>{copy('warming', 'Breaking evidence seed warming')}</strong><span>{formatRelativeTime(payload?.generatedAt)}</span></div>
        )}
      </div>
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'breaking-event-radar': {
    render: (ctx) => <BreakingEventRadarPanel payload={ctx.runtimeData['breaking-event-radar'] as RuntimeBreakingEventRadarPayload | undefined} />,
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'breaking-event-radar',
  title: 'Breaking Event Radar',
  eyebrow: 'evidence',
  description: 'GDELT and Wikimedia evidence velocity linked to Polymarket topics.',
  size: 'wide',
  defaultEnabled: true,
}, {
  tier: 'slow',
  intervalMs: 300000,
  fetchData: () => fetchRuntimeBreakingEventRadar(12),
});
