import { Fragment } from 'preact';
import { useMemo, useState } from 'preact/hooks';
import {
  worldEventLayerById,
  worldEventLayerIdForEvent,
  type MapLayerDefinition,
} from '../config/layerRegistry';
import type { GeoEvent } from '../domain/types';
import type { MapSymbolKey } from '../config/mapSymbols';
import { MapSymbolIcon } from './MapSymbolIcon';

export type LayerPanelItem = {
  id: string;
  label: string;
  panelEmoji: string;
  icon: MapSymbolKey;
  hint?: string;
  enabled: boolean;
  aliases: string[];
  availability: 'ready' | 'degraded' | 'unavailable';
  availabilityReason?: string;
  isExecutable: boolean;
};

export const LAYER_PANEL_COPY = {
  title: 'LAYERS',
  searchPlaceholder: 'Search layers…',
  emptyLabel: 'No matching layers',
  openLabel: 'Open layers panel',
  collapseLabel: 'Collapse layers panel',
} as const;

function LayerBrief({
  layer,
  runtime,
  eventCount,
  onClose,
}: {
  layer: MapLayerDefinition;
  runtime?: LayerPanelItem;
  eventCount: number;
  onClose: () => void;
}) {
  return (
    <aside className="wm-layer-brief" aria-labelledby="wm-layer-brief-title">
      <button type="button" className="wm-layer-brief-close" onClick={onClose} aria-label="Close layer brief">×</button>
      <header>
        <span>Layer intelligence brief</span>
        <h2 id="wm-layer-brief-title">
          <MapSymbolIcon symbol={layer.icon} size={18} />
          {layer.label}
        </h2>
        <p>{layer.explanation.purpose}</p>
      </header>
      <dl>
        <div>
          <dt>Mapped now</dt>
          <dd>{eventCount} events</dd>
        </div>
        <div>
          <dt>Freshness</dt>
          <dd>{layer.explanation.freshness}</dd>
        </div>
        <div>
          <dt>Evidence standard</dt>
          <dd>{layer.explanation.confidence}</dd>
        </div>
        <div>
          <dt>Runtime capability</dt>
          <dd>
            {(runtime?.availability || layer.availability).toUpperCase()}
            {runtime?.availabilityReason ? ` · ${runtime.availabilityReason}` : ''}
          </dd>
        </div>
      </dl>
      <section>
        <h3>Sources</h3>
        <div className="wm-layer-brief-sources">
          {layer.explanation.sources.map((source) => <span key={source}>{source}</span>)}
        </div>
      </section>
      <section>
        <h3>Coverage & limitations</h3>
        <ul>
          {layer.explanation.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}
        </ul>
      </section>
      <p className="wm-layer-brief-note">
        Select a colored event on the map to open its full event report, native sources and related markets.
      </p>
    </aside>
  );
}

export function LayerPanel({
  items,
  events,
  collapsed,
  onToggle,
  onCollapse,
  onExpand,
}: {
  items: LayerPanelItem[];
  events: GeoEvent[];
  collapsed: boolean;
  onToggle: (layerId: string) => void;
  onCollapse: () => void;
  onExpand: () => void;
}) {
  const [query, setQuery] = useState('');
  const [briefLayerId, setBriefLayerId] = useState<string | null>(null);
  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return normalized
      ? items.filter((item) => `${item.label} ${item.hint || ''} ${item.id} ${item.aliases.join(' ')}`.toLowerCase().includes(normalized))
      : items;
  }, [items, query]);
  const counts = useMemo(() => {
    const next = new Map<string, number>();
    for (const event of events) {
      const layerId = worldEventLayerIdForEvent(event);
      if (layerId) next.set(layerId, (next.get(layerId) || 0) + 1);
    }
    return next;
  }, [events]);
  const briefLayer = briefLayerId ? worldEventLayerById(briefLayerId) : undefined;
  const executableCount = useMemo(
    () => items.filter((item) => item.isExecutable && item.availability !== 'unavailable').length,
    [items],
  );
  const activeCount = useMemo(
    () => items.filter((item) => (
      item.enabled && item.isExecutable && item.availability !== 'unavailable'
    )).length,
    [items],
  );
  const activeSummary = `${activeCount}/${executableCount} LAYERS ACTIVE`;

  const toggleCollapsed = () => {
    if (collapsed) {
      onExpand();
      return;
    }
    setBriefLayerId(null);
    onCollapse();
  };

  return (
    <Fragment>
      <aside
        id="wm-layer-sidebar"
        className={`wm-layer-sidebar ${collapsed ? 'is-collapsed' : ''}`}
        aria-label={LAYER_PANEL_COPY.title}
      >
        <div className="wm-toggle-header">
          <span className="wm-layer-heading">{LAYER_PANEL_COPY.title}</span>
          <span
            className="wm-layer-status-orb"
            role="status"
            aria-label={activeSummary}
            title={activeSummary}
          >
            {activeCount}
          </span>
          <button
            type="button"
            className="wm-toggle-collapse"
            aria-label={collapsed ? LAYER_PANEL_COPY.openLabel : LAYER_PANEL_COPY.collapseLabel}
            aria-controls="wm-layer-panel-body"
            aria-expanded={!collapsed}
            onClick={toggleCollapsed}
          >
            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 8 5 5 5-5" /></svg>
          </button>
        </div>
        <div id="wm-layer-panel-body" className="wm-layer-panel-body" hidden={collapsed}>
          <input
            className="wm-layer-search"
            value={query}
            onInput={(event) => setQuery((event.currentTarget as HTMLInputElement).value)}
            placeholder={LAYER_PANEL_COPY.searchPlaceholder}
            aria-label={LAYER_PANEL_COPY.searchPlaceholder}
            autoComplete="off"
            spellcheck={false}
          />
          <div
            className="wm-layer-list"
            onWheel={(event) => event.stopPropagation()}
            onTouchMove={(event) => event.stopPropagation()}
          >
            {filtered.length ? filtered.map((item) => {
              const briefOpen = briefLayerId === item.id;
              const unavailable = !item.isExecutable || item.availability === 'unavailable';
              const visiblyEnabled = item.enabled && !unavailable;
              const actionLabel = unavailable
                ? `${item.label} unavailable`
                : `${visiblyEnabled ? 'Hide' : 'Show'} ${item.label}`;
              return (
                <div
                  key={item.id}
                  className={`wm-layer-row ${visiblyEnabled ? 'enabled' : ''} ${briefOpen ? 'brief-open' : ''} ${unavailable ? 'is-unavailable' : ''}`}
                  title={unavailable ? item.availabilityReason : undefined}
                >
                  <label className="wm-layer-toggle" title={actionLabel}>
                    <input
                      type="checkbox"
                      checked={visiblyEnabled}
                      disabled={unavailable}
                      onChange={() => onToggle(item.id)}
                      aria-label={actionLabel}
                    />
                    <span
                      className="wm-layer-symbol wm-layer-emoji"
                      aria-hidden="true"
                    >
                      {item.panelEmoji}
                    </span>
                    <span className="wm-layer-label">{item.label}</span>
                    {item.hint ? (
                      <em className="wm-layer-hint">
                        {unavailable ? 'OFFLINE' : item.availability === 'degraded' ? 'DEGRADED' : item.hint}
                      </em>
                    ) : null}
                  </label>
                  <button
                    type="button"
                    className="wm-layer-info"
                    aria-label={`Open ${item.label} source and coverage brief`}
                    aria-pressed={briefOpen}
                    onClick={() => setBriefLayerId(briefOpen ? null : item.id)}
                  >
                    i
                  </button>
                </div>
              );
            }) : <div className="wm-layer-empty">{LAYER_PANEL_COPY.emptyLabel}</div>}
          </div>
          <div className="wm-sidebar-footer">{activeSummary}</div>
        </div>
      </aside>
      {briefLayer && !collapsed ? (
        <LayerBrief
          layer={briefLayer}
          runtime={items.find((item) => item.id === briefLayer.id)}
          eventCount={counts.get(briefLayer.id) || 0}
          onClose={() => setBriefLayerId(null)}
        />
      ) : null}
    </Fragment>
  );
}
