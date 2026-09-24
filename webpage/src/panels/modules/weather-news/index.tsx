import { useMemo, useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import { fetchRuntimeWeatherNews } from '@/services/api';
import type { RuntimeWeatherNewsItem, RuntimeWeatherNewsPayload } from '@/types';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { useSpecialistCopy } from '@/services/specialist-i18n';

type SortMode = 'latest' | 'severity' | 'city';

function statusBadge(status?: string | null, cacheMode?: string | null) {
  const text = String(status || '').toLowerCase();
  if (text === 'ok') return 'LIVE';
  if (text === 'degraded') return 'PARTIAL';
  if (text === 'warming' || String(cacheMode || '').includes('refresh')) return 'WARMING';
  return text ? text.toUpperCase() : 'SEED';
}

function severityRank(item: RuntimeWeatherNewsItem) {
  const value = String(item.severity || '').toLowerCase();
  if (value === 'warning') return 3;
  if (value === 'watch') return 2;
  return 1;
}

function sortItems(items: RuntimeWeatherNewsItem[], mode: SortMode) {
  const sorted = [...items];
  if (mode === 'city') {
    sorted.sort((a, b) => String(a.city || '').localeCompare(String(b.city || '')) || String(b.publishedAt || '').localeCompare(String(a.publishedAt || '')));
    return sorted;
  }
  if (mode === 'severity') {
    sorted.sort((a, b) => severityRank(b) - severityRank(a) || String(b.publishedAt || '').localeCompare(String(a.publishedAt || '')));
    return sorted;
  }
  sorted.sort((a, b) => String(b.publishedAt || '').localeCompare(String(a.publishedAt || '')));
  return sorted;
}

function nextMode(mode: SortMode): SortMode {
  if (mode === 'latest') return 'severity';
  if (mode === 'severity') return 'city';
  return 'latest';
}

function modeLabel(mode: SortMode, shared: ReturnType<typeof useSpecialistCopy>['shared']) {
  if (mode === 'latest') return shared('latest', 'Latest');
  if (mode === 'severity') return shared('alerts', 'Alerts');
  return shared('city', 'City');
}

function emptyMessage(payload: RuntimeWeatherNewsPayload | null | undefined, copy: ReturnType<typeof useSpecialistCopy>['copy']) {
  if (!payload) return copy('apiUnavailable', 'Weather news API unavailable');
  const cacheMode = String(payload.cacheMode || '').toLowerCase();
  const status = String(payload.status || '').toLowerCase();
  if (cacheMode.includes('refresh')) return copy('warming', 'Weather headlines warming');
  if (cacheMode === 'seed-miss') return copy('seedMissing', 'Weather headlines seed missing');
  if (status === 'degraded') return copy('degraded', 'Weather news source degraded');
  return copy('empty', 'No weather headlines seeded');
}

function displaySeverity(severity: string, shared: ReturnType<typeof useSpecialistCopy>['shared']) {
  if (severity === 'warning') return shared('alert', 'Alert');
  if (severity === 'watch') return shared('watch', 'Watch');
  return shared('forecast', 'Forecast');
}

function HighlightedText({ text, city }: { text?: string | null; city?: string | null }) {
  const value = text || '';
  const cityName = String(city || '').trim();
  if (!cityName) return <>{value}</>;
  const pattern = new RegExp(`(${cityName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'ig');
  return (
    <>
      {value.split(pattern).map((part, index) => (
        part.toLowerCase() === cityName.toLowerCase()
          ? <span className="wm-weather-news-title-city" key={`${part}-${index}`}>{part}</span>
          : part
      ))}
    </>
  );
}

function NewsItem({ item }: { item: RuntimeWeatherNewsItem }) {
  const { copy, shared, formatRelativeTime } = useSpecialistCopy('weather-news');
  const severity = String(item.severity || 'normal').toLowerCase();
  const tags = (item.tags || []).slice(0, 2);
  const city = item.city || shared('global', 'Global');
  return (
    <a className={`wm-weather-news-item ${severity}`} href={item.url || '#'} target="_blank" rel="noreferrer">
      <div className="wm-weather-news-card-head">
        <div className="wm-weather-news-meta">
          <span className="wm-weather-news-dot" aria-hidden="true" />
          <span className="wm-weather-news-city">{city}</span>
          <span className="wm-weather-news-source">{item.source || copy('weatherSource', 'Weather source')}</span>
          <span className={`wm-weather-news-severity ${severity}`}>{displaySeverity(severity, shared)}</span>
          {tags.map((tag) => <span className="wm-weather-news-tag" key={tag}>{tag}</span>)}
        </div>
      </div>
      <strong><HighlightedText text={item.title || copy('weatherUpdate', 'Weather update')} city={city} /></strong>
      <em><HighlightedText text={item.summary || copy('summaryPending', 'Weather summary pending')} city={city} /></em>
      <div className="wm-weather-news-card-foot">
        <span>{formatRelativeTime(item.publishedAt)}</span>
        <b>{shared('readSource', 'Read source')}</b>
      </div>
    </a>
  );
}

function WeatherNewsPanel({ payload }: { payload?: RuntimeWeatherNewsPayload | null }) {
  const { copy, shared } = useSpecialistCopy('weather-news');
  const [showHelp, setShowHelp] = useState(false);
  const [sortMode, setSortMode] = useState<SortMode>('latest');
  const items = useMemo(() => sortItems(payload?.items || [], sortMode), [payload?.items, sortMode]);
  return (
    <Panel
      title={copy('title', 'WEATHER NEWS')}
      titleControls={(
        <button type="button" className="wm-panel-help-button" aria-label={copy('explainAria', 'Explain weather news source')} aria-expanded={showHelp} onClick={() => setShowHelp((current) => !current)}>?</button>
      )}
      controls={(
        <button type="button" className="wm-weather-sort-button" aria-label={copy('sortAria', 'Change weather news sort')} onClick={() => setSortMode((current) => nextMode(current))}>{modeLabel(sortMode, shared)}</button>
      )}
      badge={statusBadge(payload?.status, payload?.cacheMode)}
      status={payload?.status === 'ok' ? 'live' : 'muted'}
      count={items.length}
      headerOverlay={showHelp ? (
        <div className="wm-panel-help-popover">
          <strong>{copy('helpTitle', 'Weather News')}</strong>
          <p>{copy('helpText', 'Seeded Google News RSS results filtered to city weather, forecasts, storms, heat, rain, wind, snow, floods, and alerts. Header sort changes the visible order.')}</p>
        </div>
      ) : null}
      className="wm-market-panel wm-weather-news-panel"
      dataPanelId="weather-news"
    >
      <div className="wm-weather-news-list">
        {items.length ? items.map((item) => <NewsItem key={item.id || `${item.city}-${item.title}`} item={item} />) : (
          <div className="wm-registry-empty"><strong>{emptyMessage(payload, copy)}</strong></div>
        )}
      </div>
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'weather-news': {
    render: (ctx) => <WeatherNewsPanel payload={ctx.runtimeData['weather-news'] as RuntimeWeatherNewsPayload | undefined} />,
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'weather-news',
  title: 'Weather News',
  eyebrow: 'weather',
  description: 'City weather headlines from seeded Google News RSS.',
  defaultEnabled: true,
}, {
  tier: 'slow',
  intervalMs: 300000,
  fetchData: () => fetchRuntimeWeatherNews(24),
});
