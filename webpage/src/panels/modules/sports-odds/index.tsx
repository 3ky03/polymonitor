import { useMemo, useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import { fetchRuntimeSportsOdds } from '@/services/api';
import type { RuntimeSportsOddsItem, RuntimeSportsOddsPayload } from '@/types';
import { formatRelative } from '../../shared/formatters';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { useSpecialistCopy } from '@/services/specialist-i18n';

type SortMode = 'signal' | 'start' | 'dispersion';

function numeric(value?: number | string | null) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function percent(value?: number | string | null) {
  const parsed = numeric(value);
  if (!parsed) return '--';
  return `${Math.round(parsed * 100)}%`;
}

function compact(value?: string | null, maxLength = 28) {
  const text = String(value || '').trim();
  if (text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(1, maxLength - 1)).trim()}...`;
}

function timeLabel(value?: string | null) {
  if (!value) return '--';
  return formatRelative(value).replace(' ago', '').replace('in ', '');
}

function sourceStatus(payload?: RuntimeSportsOddsPayload | null) {
  if (payload?.sources?.theOddsApi === 'missing-key') return 'missing-key';
  if (payload?.status === 'ok') return 'ok';
  if (payload?.status === 'empty') return 'empty';
  return payload?.status || 'warming';
}

function signalRank(signal?: string | null) {
  if (signal === 'PM RICH' || signal === 'PM CHEAP') return 0;
  if (signal === 'WATCH') return 1;
  if (signal === 'IN LINE') return 2;
  return 3;
}

function sortItems(items: RuntimeSportsOddsItem[], sortMode: SortMode) {
  const copy = [...items];
  if (sortMode === 'start') return copy.sort((a, b) => String(a.commenceTime || '').localeCompare(String(b.commenceTime || '')));
  if (sortMode === 'dispersion') return copy.sort((a, b) => numeric(b.dispersion) - numeric(a.dispersion));
  return copy.sort((a, b) => signalRank(a.signal) - signalRank(b.signal) || numeric(b.dispersion) - numeric(a.dispersion));
}

function nextSortMode(current: SortMode): SortMode {
  if (current === 'signal') return 'start';
  if (current === 'start') return 'dispersion';
  return 'signal';
}

function summaryLine(payload: RuntimeSportsOddsPayload | null | undefined, items: RuntimeSportsOddsItem[], copy: ReturnType<typeof useSpecialistCopy>['copy']) {
  if (sourceStatus(payload) === 'missing-key') return copy('keyMissing', 'The Odds API key missing');
  if (!items.length) return copy('noneLoaded', 'No sportsbook odds loaded');
  const wide = Number(payload?.summary?.wideCount || 0);
  if (wide > 0) return copy('wideChecks', '{count} wide consensus checks', { count: wide });
  return copy('eventsReady', '{count} sportsbook events ready', { count: items.length });
}

function OddsSummary({ payload, items }: { payload?: RuntimeSportsOddsPayload | null; items: RuntimeSportsOddsItem[] }) {
  const { copy, shared } = useSpecialistCopy('sports-odds');
  const summary = payload?.summary || {};
  return (
    <div className="wm-odds-summary">
      <div className="wm-odds-hero-icon">ODDS</div>
      <div className="wm-odds-hero-main">
        <strong>{summaryLine(payload, items, copy)}</strong>
        <span>
          <em>BOOK</em>
          <em>{(payload?.cacheMode || 'live-build').toUpperCase()}</em>
          <em>{payload?.generatedAt ? timeLabel(payload.generatedAt) : '--'}</em>
        </span>
      </div>
      <div className="wm-odds-hero-metrics">
        <span>{shared('events', 'Events')} <strong>{summary.eventCount ?? items.length}</strong></span>
        <span>{copy('books', 'Books')} <strong>{summary.bookmakerCount ?? 0}</strong></span>
        <span>PMKT <strong>{summary.pmLinked ?? 0}</strong></span>
      </div>
    </div>
  );
}

function OddsRow({ item, index }: { item: RuntimeSportsOddsItem; index: number }) {
  const { copy } = useSpecialistCopy('sports-odds');
  const topQuote = (item.quotes || [])[0];
  const signal = item.signal || 'WATCH';
  return (
    <article className={`wm-odds-row signal-${signal.toLowerCase().replace(/\s+/g, '-')}`}>
      <div className="wm-odds-row-glyph">{(item.sportTitle || item.sportKey || 'SP').slice(0, 3).toUpperCase()}</div>
      <div className="wm-odds-row-main">
        <div className="wm-odds-row-top">
          <strong title={item.event || ''}>{compact(item.event, 22)}</strong>
          <span>{signal}</span>
        </div>
        <div className="wm-odds-row-meta">
          <span>{String(index + 1).padStart(2, '0')}</span>
          <span>{timeLabel(item.commenceTime)}</span>
          <span>{copy('bookCount', '{count} books', { count: item.bookmakerCount || 0 })}</span>
        </div>
        <div className="wm-odds-quote-strip">
          <em>{compact(topQuote?.name || 'market', 16)}</em>
          <em>{topQuote?.bestPrice ? Number(topQuote.bestPrice).toFixed(2) : '--'}</em>
          <em>{percent(topQuote?.consensusProbability)}</em>
        </div>
      </div>
      <div className="wm-odds-right-rail">
        <strong>{percent(item.consensusProbability)}</strong>
        <span>{numeric(item.dispersion).toFixed(3)} disp</span>
      </div>
    </article>
  );
}

function OddsList({ payload, sortMode }: { payload?: RuntimeSportsOddsPayload | null; sortMode: SortMode }) {
  const { copy } = useSpecialistCopy('sports-odds');
  const items = useMemo(() => sortItems(payload?.items || [], sortMode), [payload, sortMode]);
  if (!items.length) {
    return (
      <div className="wm-odds-empty-state">
        <span>{sourceStatus(payload).toUpperCase()}</span>
        <strong>{copy('warming', 'Sportsbook feed is warming.')}</strong>
        <em>{copy('keyHelp', 'Set POLYDATA_THE_ODDS_API_KEY to enable live bookmaker odds.')}</em>
      </div>
    );
  }
  return <div className="wm-odds-table">{items.map((item, index) => <OddsRow key={item.id || index} item={item} index={index} />)}</div>;
}

function SportsOddsPanel({ payload }: { payload?: RuntimeSportsOddsPayload | null }) {
  const { copy } = useSpecialistCopy('sports-odds');
  const [showHelp, setShowHelp] = useState(false);
  const [sortMode, setSortMode] = useState<SortMode>('signal');
  const items = payload?.items || [];
  const degraded = sourceStatus(payload) !== 'ok';
  return (
    <Panel
      title={copy('title', 'SPORTS ODDS')}
      titleControls={<button type="button" className="wm-panel-help-button" aria-label={copy('explainAria', 'Explain sportsbook odds monitor')} aria-expanded={showHelp} onClick={() => setShowHelp((current) => !current)}>?</button>}
      badge={degraded ? 'CACHED' : 'LIVE'}
      status={degraded ? 'muted' : 'live'}
      count={items.length}
      controls={<button type="button" className="wm-panel-action-button" aria-label={copy('sortAria', 'Sort sports odds by {mode}', { mode: nextSortMode(sortMode) })} onClick={() => setSortMode((current) => nextSortMode(current))}>{sortMode.toUpperCase()}</button>}
      headerOverlay={showHelp ? (
        <div className="wm-panel-help-popover">
          <strong>{copy('helpTitle', 'Sports Odds')}</strong>
          <p>{copy('helpText', 'Uses The Odds API h2h decimal odds, converts prices into implied probability, and leaves PMKT comparison disabled until local matching is enabled.')}</p>
        </div>
      ) : null}
      className="wm-market-panel wm-odds-panel"
      dataPanelId="sports-odds"
    >
      <OddsSummary payload={payload} items={items} />
      <OddsList payload={payload} sortMode={sortMode} />
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'sports-odds': {
    render: (ctx) => <SportsOddsPanel payload={ctx.runtimeData['sports-odds'] as RuntimeSportsOddsPayload | undefined} />,
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'sports-odds',
  title: 'Sports Odds',
  eyebrow: 'sports',
  description: 'Bookmaker consensus monitor with Polymarket comparison hooks.',
  defaultEnabled: true,
}, {
  tier: 'slow',
  intervalMs: 45000,
  fetchData: () => fetchRuntimeSportsOdds(8),
});
