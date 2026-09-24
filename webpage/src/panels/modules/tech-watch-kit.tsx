import { useMemo, useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import { fetchRuntimeTechPanel } from '@/services/api';
import type { RuntimeTechPanelItem, RuntimeTechPanelPayload } from '@/types';
import { formatRelative } from '../shared/formatters';
import type { PanelRenderMap } from '../types';
import { runtimePanelFromRenderer } from './helpers';

type TechPanelMode = 'model-race' | 'market-cap' | 'app-pulse';

type TechPanelConfig = {
  id: string;
  title: string;
  description: string;
  question: string;
  mode: TechPanelMode;
  limit?: number;
};

function toneClass(value?: string | null) {
  const tone = String(value || 'neutral').toLowerCase();
  if (tone === 'up') return 'tone-up';
  if (tone === 'down') return 'tone-down';
  if (tone === 'watch') return 'tone-watch';
  return 'tone-neutral';
}

function statusBadge(payload?: RuntimeTechPanelPayload | null) {
  const status = String(payload?.status || '').toLowerCase();
  const cacheMode = String(payload?.cacheMode || '').toLowerCase();
  if (cacheMode.includes('stale')) return 'STALE';
  if (status === 'ok') return 'LIVE';
  if (status === 'partial' || status === 'degraded') return 'PARTIAL';
  if (status === 'empty') return 'WARMING';
  return status ? status.toUpperCase() : 'SEED';
}

function itemKey(item: RuntimeTechPanelItem, index: number) {
  return String(item.id || item.url || item.title || item.label || index);
}

function tags(item: RuntimeTechPanelItem) {
  return (item.tags || []).filter((tag) => String(tag).toUpperCase() !== 'TOP FREE').slice(0, 3).map((tag) => (
    <span className={`wm-tech-tag tag-${String(tag).toLowerCase().replace(/\W+/g, '-')}`} key={tag}>{tag}</span>
  ));
}

function WatchlistStrip({
  activeCategory,
  onSelectCategory,
  payload,
}: {
  activeCategory?: string | null;
  onSelectCategory?: (category: string) => void;
  payload?: RuntimeTechPanelPayload | null;
}) {
  const watchlist = Array.isArray(payload?.summary?.watchlist) ? payload?.summary?.watchlist as Array<Record<string, unknown>> : [];
  if (!watchlist.length) return null;
  return (
    <div className="wm-tech-watch-strip" role="tablist" aria-label={`${payload?.title || 'Tech'} filters`}>
      {watchlist.slice(0, 4).map((item) => {
        const category = String(item.symbol || item.label || '').trim();
        const active = category === activeCategory;
        return (
          <button
            aria-selected={active}
            className={`${toneClass(item.tone as string)}${active ? ' active' : ''}`}
            key={category}
            onClick={() => onSelectCategory?.(category)}
            role="tab"
            type="button"
          >
            <b>{String(item.symbol || item.label || '--')}</b>
            <em>{Number(item.count || 0)}</em>
          </button>
        );
      })}
    </div>
  );
}

function TechFeedRow({ item }: { item: RuntimeTechPanelItem }) {
  const content = (
    <>
      <div className="wm-tech-feed-meta">
        <span className={`wm-tech-dot ${toneClass(item.tone)}`} />
        <b>{item.label || item.symbol || 'TECH'}</b>
        <span>{item.source || item.symbol || 'SOURCE'}</span>
        {tags(item)}
      </div>
      <strong>{item.title || item.label || 'Signal pending'}</strong>
      <em>{item.summary || item.secondaryLabel || 'Source detail pending'}</em>
      <div className="wm-tech-feed-foot">
        <span>{formatRelative(item.publishedAt || undefined)}</span>
        {item.url ? <b>READ SOURCE</b> : null}
        {item.metricLabel ? (
          <span className={`wm-tech-feed-metric ${toneClass(item.tone)}`}>
            {item.metricLabel}
            {item.secondaryLabel ? <em>{item.secondaryLabel}</em> : null}
          </span>
        ) : null}
      </div>
    </>
  );
  if (item.url) {
    return <a className="wm-tech-feed-row" href={item.url} target="_blank" rel="noreferrer">{content}</a>;
  }
  return <article className="wm-tech-feed-row">{content}</article>;
}

function MarketCapRow({ item }: { item: RuntimeTechPanelItem }) {
  return (
    <article className="wm-tech-cap-row">
      <div className="wm-tech-cap-rank">#{item.rank || '--'}</div>
      <div className="wm-tech-cap-main">
        <strong>{item.label || item.symbol}</strong>
      </div>
      <div className="wm-tech-cap-value">
        <strong>{item.metricLabel || '--'}</strong>
        <em className={toneClass(item.tone)}>{item.secondaryLabel || '--'}</em>
      </div>
      <b className={toneClass(item.tone)}>{item.changeLabel || '--'}</b>
    </article>
  );
}

function AppPulseRow({ item }: { item: RuntimeTechPanelItem }) {
  const rankLike = String(item.metricUnit || '').toUpperCase() === 'RANK';
  const content = (
    <>
      <div className="wm-tech-app-icon">{rankLike ? String(item.metricLabel || '#') : String(item.symbol || 'APP').slice(0, 2)}</div>
      <div className="wm-tech-app-main">
        <div className="wm-tech-feed-meta">
          <b>{item.label || item.symbol || 'APP'}</b>
          <span>{item.source || item.symbol || 'APP STORE'}</span>
          {tags(item)}
        </div>
        <strong>{item.title || item.label || 'App signal pending'}</strong>
        <em>{item.summary || item.secondaryLabel || 'Consumer app signal'}</em>
      </div>
      <div className="wm-tech-app-value">
        <strong>{item.metricLabel || 'WATCH'}</strong>
        <em>{rankLike ? 'RANK' : formatRelative(item.publishedAt || undefined)}</em>
      </div>
    </>
  );
  if (item.url) {
    return <a className="wm-tech-app-row" href={item.url} target="_blank" rel="noreferrer">{content}</a>;
  }
  return <article className="wm-tech-app-row">{content}</article>;
}

function TechPanelBody({ payload, mode }: { payload?: RuntimeTechPanelPayload | null; mode: TechPanelMode }) {
  const items = payload?.items || [];
  const watchlist = Array.isArray(payload?.summary?.watchlist) ? payload?.summary?.watchlist as Array<Record<string, unknown>> : [];
  const categoryTabs = watchlist.slice(0, 4).map((item) => String(item.symbol || item.label || '').trim()).filter(Boolean);
  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  const safeActiveCategory = categoryTabs.includes(String(activeCategory || '')) ? activeCategory : categoryTabs[0] || null;
  const visibleItems = safeActiveCategory && mode !== 'market-cap'
    ? items.filter((item) => String(item.category || item.symbol || '').toUpperCase() === String(safeActiveCategory).toUpperCase())
    : items;
  if (!items.length) {
    return (
      <div className="wm-tech-empty">
        <span>STANDBY</span>
        <strong>{payload?.title || 'Tech panel'} warming</strong>
      </div>
    );
  }
  if (mode === 'market-cap') {
    return <div className="wm-tech-cap-list">{items.map((item, index) => <MarketCapRow item={item} key={itemKey(item, index)} />)}</div>;
  }
  if (mode === 'app-pulse') {
    return (
      <div className="wm-tech-app-list">
        <WatchlistStrip activeCategory={safeActiveCategory} onSelectCategory={setActiveCategory} payload={payload} />
        {visibleItems.map((item, index) => <AppPulseRow item={item} key={itemKey(item, index)} />)}
      </div>
    );
  }
  return (
    <div className="wm-tech-feed-list">
      <WatchlistStrip activeCategory={safeActiveCategory} onSelectCategory={setActiveCategory} payload={payload} />
      {visibleItems.map((item, index) => <TechFeedRow item={item} key={itemKey(item, index)} />)}
    </div>
  );
}

function TechWatchPanel({ config, payload }: { config: TechPanelConfig; payload?: RuntimeTechPanelPayload | null }) {
  const [showHelp, setShowHelp] = useState(false);
  const count = useMemo(() => payload?.items?.length || 0, [payload?.items]);
  return (
    <Panel
      title={config.title}
      titleControls={<button type="button" className="wm-panel-help-button" aria-label={`Explain ${config.title}`} aria-expanded={showHelp} onClick={() => setShowHelp((current) => !current)}>?</button>}
      badge={statusBadge(payload)}
      status={payload?.status === 'ok' ? 'live' : 'muted'}
      count={count}
      headerOverlay={showHelp ? (
        <div className="wm-panel-help-popover">
          <strong>{config.title}</strong>
          <p>{config.question}</p>
        </div>
      ) : null}
      className={`wm-market-panel wm-tech-panel mode-${config.mode}`}
      dataPanelId={config.id}
    >
      <TechPanelBody payload={payload} mode={config.mode} />
    </Panel>
  );
}

export function createTechPanel(config: TechPanelConfig) {
  const limit = config.limit || 10;
  const renderers: PanelRenderMap = {
    [config.id]: {
      render: (ctx) => <TechWatchPanel config={config} payload={ctx.runtimeData[config.id] as RuntimeTechPanelPayload | undefined} />,
    },
  };
  return runtimePanelFromRenderer(renderers, {
    id: config.id,
    title: config.title,
    eyebrow: 'tech',
    description: config.description,
    defaultEnabled: true,
  }, {
    tier: 'slow',
    intervalMs: 300000,
    fetchData: () => fetchRuntimeTechPanel(config.id, limit),
  });
}
