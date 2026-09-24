import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { isHazardGeoEvent } from '../config/layerRegistry';
import type {
  GeoEvent,
  GeoEventGeometry,
  GeoEventSeverity,
  GeoPoint,
} from '../domain/types';
import { mapSymbolForEvent } from '../config/mapSymbols';
import { MapSymbolIcon } from './MapSymbolIcon';

export type EventListTimeFilter = 'all' | '1h' | '6h' | '24h' | '48h' | '7d';
export type EventListRegion =
  | 'all'
  | 'america'
  | 'latam'
  | 'eu'
  | 'mena'
  | 'africa'
  | 'asia'
  | 'oceania'
  | 'unknown';

export type EventListFilters = {
  query: string;
  eventType: string;
  severity: GeoEventSeverity | 'all';
  time: EventListTimeFilter;
  region: EventListRegion;
};

export const EVENT_LIST_ROW_HEIGHT = 68;
const EVENT_LIST_OVERSCAN = 6;
const DEFAULT_LIST_VIEWPORT_HEIGHT = 360;
const DEFAULT_FILTERS: EventListFilters = {
  query: '',
  eventType: 'all',
  severity: 'all',
  time: 'all',
  region: 'all',
};
const SEVERITY_RANK: Record<GeoEventSeverity, number> = {
  critical: 3,
  warning: 2,
  watch: 1,
  info: 0,
};
const TIME_RANGE_MS: Record<Exclude<EventListTimeFilter, 'all'>, number> = {
  '1h': 60 * 60 * 1000,
  '6h': 6 * 60 * 60 * 1000,
  '24h': 24 * 60 * 60 * 1000,
  '48h': 48 * 60 * 60 * 1000,
  '7d': 7 * 24 * 60 * 60 * 1000,
};
const EVENT_TYPE_LABELS: Record<string, string> = {
  'severe-storm': 'Severe storm',
  tornado: 'Tornado',
  'tropical-cyclone': 'Tropical cyclone',
  flood: 'Flood',
  'extreme-heat': 'Extreme heat',
  'extreme-cold': 'Extreme cold',
  earthquake: 'Earthquake',
  volcano: 'Volcano',
  tsunami: 'Tsunami',
  wildfire: 'Wildfire',
  'fire-detection': 'Satellite fire detection',
  'temperature-anomaly': 'Temperature anomaly',
  'precipitation-anomaly': 'Precipitation anomaly',
  'other-weather-anomaly': 'Weather anomaly',
  intel: 'Intel hotspot',
  conflict: 'State-based conflict',
  unrest: 'Unrest / one-sided violence',
  sanctions: 'Sanctions',
  'country-risk': 'Country risk',
  'transport-disruption': 'Transport disruption',
  infrastructure: 'Aviation reference',
  weather: 'Weather event',
  'natural-hazard': 'Natural hazard',
};
const REGION_OPTIONS: ReadonlyArray<{ value: EventListRegion; label: string }> = [
  { value: 'all', label: 'All regions' },
  { value: 'america', label: 'North America' },
  { value: 'latam', label: 'Latin America & Caribbean' },
  { value: 'eu', label: 'Europe' },
  { value: 'mena', label: 'Middle East & North Africa' },
  { value: 'africa', label: 'Sub-Saharan Africa' },
  { value: 'asia', label: 'Asia' },
  { value: 'oceania', label: 'Oceania & Pacific' },
  { value: 'unknown', label: 'Other / unknown region' },
];

function eventTimestamp(event: GeoEvent) {
  const parsed = Date.parse(event.updatedAt || event.occurredAt || '');
  return Number.isFinite(parsed) ? parsed : 0;
}

export function eventListType(event: GeoEvent) {
  return isHazardGeoEvent(event) ? event.hazardKind : event.category;
}

function geometryPoints(geometry: GeoEventGeometry | undefined): GeoPoint[] {
  if (!geometry) return [];
  if (geometry.type === 'Point') return [geometry.coordinates];
  if (geometry.type === 'LineString') return geometry.coordinates;
  if (geometry.type === 'Polygon') return geometry.coordinates.flat() as GeoPoint[];
  return geometry.coordinates.flat(2) as GeoPoint[];
}

function representativeCoordinate(event: GeoEvent): GeoPoint | null {
  const points = geometryPoints(event.geometry).filter(([lon, lat]) => (
    Number.isFinite(lon) && Number.isFinite(lat)
  ));
  if (!points.length) return null;
  if (event.geometry?.type === 'Point') return points[0]!;
  let west = 180;
  let east = -180;
  let south = 90;
  let north = -90;
  for (const [lon, lat] of points) {
    west = Math.min(west, lon);
    east = Math.max(east, lon);
    south = Math.min(south, lat);
    north = Math.max(north, lat);
  }
  return [(west + east) / 2, (south + north) / 2];
}

function explicitRegion(regionCode: string | undefined): EventListRegion | null {
  const normalized = String(regionCode || '').trim().toLowerCase();
  const aliases: Record<string, EventListRegion> = {
    america: 'america',
    'north-america': 'america',
    latam: 'latam',
    'latin-america': 'latam',
    eu: 'eu',
    europe: 'eu',
    mena: 'mena',
    africa: 'africa',
    asia: 'asia',
    oceania: 'oceania',
    pacific: 'oceania',
  };
  return aliases[normalized] || null;
}

export function eventListRegion(event: GeoEvent): EventListRegion {
  const declared = explicitRegion(event.regionCode);
  if (declared) return declared;
  const coordinate = representativeCoordinate(event);
  if (!coordinate) return 'unknown';
  const [lon, lat] = coordinate;
  if (lat < 32 && lon >= -120 && lon <= -30) return 'latam';
  if (lon >= -170 && lon <= -50 && lat >= 15) return 'america';
  if (lon >= -25 && lon <= 31 && lat >= 35 && lat <= 72) return 'eu';
  if (lon >= -20 && lon <= 75 && lat >= 12 && lat < 40) return 'mena';
  if (lon >= -25 && lon <= 55 && lat >= -40 && lat < 38) return 'africa';
  if (lon >= 45 && lon <= 180 && lat >= -10) return 'asia';
  if ((lon >= 110 && lat < -10) || (lat < 15 && (lon > 150 || lon < -140))) return 'oceania';
  return 'unknown';
}

export function eventTypeOptions(events: GeoEvent[]) {
  return Array.from(new Set(events.map(eventListType)))
    .map((value) => ({ value, label: EVENT_TYPE_LABELS[value] || value.replace(/-/g, ' ') }))
    .sort((left, right) => left.label.localeCompare(right.label));
}

export function filterEventListEvents(
  events: GeoEvent[],
  filters: EventListFilters,
  now = Date.now(),
) {
  const normalizedQuery = filters.query.trim().toLocaleLowerCase();
  const cutoff = filters.time === 'all' ? null : now - TIME_RANGE_MS[filters.time];
  return events.filter((event) => {
    if (filters.eventType !== 'all' && eventListType(event) !== filters.eventType) return false;
    if (filters.severity !== 'all' && event.severity !== filters.severity) return false;
    if (filters.region !== 'all' && eventListRegion(event) !== filters.region) return false;
    if (cutoff != null) {
      const timestamp = eventTimestamp(event);
      if (!timestamp || timestamp < cutoff || timestamp > now) return false;
    }
    if (!normalizedQuery) return true;
    const searchable = [
      event.title,
      event.summary,
      event.locationLabel,
      event.countryCode,
      event.regionCode,
      eventListType(event),
      ...event.sources.map((source) => source.provider),
    ].filter(Boolean).join(' ').toLocaleLowerCase();
    return searchable.includes(normalizedQuery);
  }).sort((left, right) => (
    SEVERITY_RANK[right.severity] - SEVERITY_RANK[left.severity]
    || eventTimestamp(right) - eventTimestamp(left)
    || left.title.localeCompare(right.title)
  ));
}

export function virtualEventWindow(
  events: GeoEvent[],
  scrollTop: number,
  viewportHeight: number,
) {
  const safeScrollTop = Math.max(0, scrollTop);
  const safeViewportHeight = Math.max(EVENT_LIST_ROW_HEIGHT, viewportHeight);
  const startIndex = Math.max(
    0,
    Math.floor(safeScrollTop / EVENT_LIST_ROW_HEIGHT) - EVENT_LIST_OVERSCAN,
  );
  const endIndex = Math.min(
    events.length,
    Math.ceil((safeScrollTop + safeViewportHeight) / EVENT_LIST_ROW_HEIGHT) + EVENT_LIST_OVERSCAN,
  );
  return {
    startIndex,
    endIndex,
    totalHeight: events.length * EVENT_LIST_ROW_HEIGHT,
    items: events.slice(startIndex, endIndex).map((event, offset) => ({
      event,
      index: startIndex + offset,
    })),
  };
}

function eventTimeLabel(event: GeoEvent) {
  const timestamp = eventTimestamp(event);
  if (!timestamp) return 'Time unknown';
  return `${new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'UTC',
  }).format(timestamp)} UTC`;
}

function DrawerCloseIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="m5 5 10 10M15 5 5 15" />
    </svg>
  );
}

export function EventList({
  events,
  selectedEventId,
  onSelect,
}: {
  events: GeoEvent[];
  selectedEventId: string | null;
  onSelect: (eventId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [filters, setFilters] = useState<EventListFilters>(DEFAULT_FILTERS);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(DEFAULT_LIST_VIEWPORT_HEIGHT);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const types = useMemo(() => eventTypeOptions(events), [events]);
  const filteredEvents = useMemo(
    () => filterEventListEvents(events, filters),
    [events, filters],
  );
  const virtualWindow = useMemo(
    () => virtualEventWindow(filteredEvents, scrollTop, viewportHeight),
    [filteredEvents, scrollTop, viewportHeight],
  );
  const hasFilters = filters.query !== ''
    || filters.eventType !== 'all'
    || filters.severity !== 'all'
    || filters.time !== 'all'
    || filters.region !== 'all';

  const closeDrawer = (restoreFocus: boolean) => {
    setOpen(false);
    if (restoreFocus && typeof window !== 'undefined') {
      window.requestAnimationFrame(() => toggleRef.current?.focus());
    }
  };

  useEffect(() => {
    if (!open || typeof window === 'undefined') return undefined;
    const focusFrame = window.requestAnimationFrame(() => searchRef.current?.focus());
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      closeDrawer(true);
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  useEffect(() => {
    if (!open || !scrollRef.current) return undefined;
    const scrollNode = scrollRef.current;
    const updateHeight = () => setViewportHeight(scrollNode.clientHeight || DEFAULT_LIST_VIEWPORT_HEIGHT);
    updateHeight();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(updateHeight);
    observer.observe(scrollNode);
    return () => observer.disconnect();
  }, [open]);

  useEffect(() => {
    setScrollTop(0);
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [filters]);

  return (
    <div className={`wm-world-event-list ${open ? 'is-open' : ''}`}>
      <button
        ref={toggleRef}
        type="button"
        className="wm-world-event-list-toggle"
        aria-expanded={open}
        aria-controls="wm-world-event-list-panel"
        onClick={() => setOpen((current) => !current)}
      >
        <span>ALL EVENTS</span><b aria-hidden="true">·</b><strong>{events.length}</strong>
      </button>
      {open ? (
        <section
          id="wm-world-event-list-panel"
          aria-labelledby="wm-world-event-list-heading"
          aria-describedby="wm-world-event-list-summary"
        >
          <header>
            <div>
              <span>WORLD EVENT INDEX</span>
              <h2 id="wm-world-event-list-heading">All mapped events</h2>
            </div>
            <button
              type="button"
              className="wm-world-event-list-close"
              aria-label="Close all events drawer"
              onClick={() => closeDrawer(true)}
            >
              <DrawerCloseIcon />
            </button>
          </header>

          <div className="wm-world-event-list-filters">
            <label className="is-search" htmlFor="wm-event-list-search">
              <span>Search</span>
              <input
                ref={searchRef}
                id="wm-event-list-search"
                type="search"
                value={filters.query}
                placeholder="Title, place or source"
                onInput={(event) => setFilters((current) => ({
                  ...current,
                  query: event.currentTarget.value,
                }))}
              />
            </label>
            <label htmlFor="wm-event-list-type">
              <span>Disaster type</span>
              <select
                id="wm-event-list-type"
                value={filters.eventType}
                onChange={(event) => setFilters((current) => ({
                  ...current,
                  eventType: event.currentTarget.value,
                }))}
              >
                <option value="all">All types</option>
                {types.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}
              </select>
            </label>
            <label htmlFor="wm-event-list-severity">
              <span>Severity</span>
              <select
                id="wm-event-list-severity"
                value={filters.severity}
                onChange={(event) => setFilters((current) => ({
                  ...current,
                  severity: event.currentTarget.value as EventListFilters['severity'],
                }))}
              >
                <option value="all">All severities</option>
                <option value="critical">Critical</option>
                <option value="warning">Warning</option>
                <option value="watch">Watch</option>
                <option value="info">Info</option>
              </select>
            </label>
            <label htmlFor="wm-event-list-time">
              <span>Time</span>
              <select
                id="wm-event-list-time"
                value={filters.time}
                onChange={(event) => setFilters((current) => ({
                  ...current,
                  time: event.currentTarget.value as EventListTimeFilter,
                }))}
              >
                <option value="all">Any time</option>
                <option value="1h">Last 1 hour</option>
                <option value="6h">Last 6 hours</option>
                <option value="24h">Last 24 hours</option>
                <option value="48h">Last 48 hours</option>
                <option value="7d">Last 7 days</option>
              </select>
            </label>
            <label htmlFor="wm-event-list-region">
              <span>Region</span>
              <select
                id="wm-event-list-region"
                value={filters.region}
                onChange={(event) => setFilters((current) => ({
                  ...current,
                  region: event.currentTarget.value as EventListRegion,
                }))}
              >
                {REGION_OPTIONS.map((region) => (
                  <option key={region.value} value={region.value}>{region.label}</option>
                ))}
              </select>
            </label>
          </div>

          <div className="wm-world-event-list-summary" id="wm-world-event-list-summary" aria-live="polite">
            <span>Showing <strong>{filteredEvents.length}</strong> of {events.length}</span>
            {hasFilters ? (
              <button type="button" onClick={() => setFilters(DEFAULT_FILTERS)}>Clear filters</button>
            ) : <span>Sorted by severity and freshness</span>}
          </div>

          {filteredEvents.length ? (
            <div
              ref={scrollRef}
              className="wm-world-event-list-scroll"
              role="region"
              aria-label={`${filteredEvents.length} filtered world events`}
              tabIndex={0}
              onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
            >
              <ol style={{ height: `${virtualWindow.totalHeight}px` }}>
                {virtualWindow.items.map(({ event, index }) => (
                  <li
                    key={event.id}
                    aria-setsize={filteredEvents.length}
                    aria-posinset={index + 1}
                    style={{ transform: `translateY(${index * EVENT_LIST_ROW_HEIGHT}px)` }}
                  >
                    <button
                      type="button"
                      className={event.id === selectedEventId ? 'is-selected' : ''}
                      aria-current={event.id === selectedEventId ? 'true' : undefined}
                      onClick={() => {
                        onSelect(event.id);
                        closeDrawer(false);
                      }}
                    >
                      <span className="wm-event-list-symbol">
                        <MapSymbolIcon
                          symbol={mapSymbolForEvent(event)}
                          severity={event.severity}
                          framed={false}
                          size={18}
                        />
                      </span>
                      <span className={`wm-event-list-severity severity-${event.severity}`}>{event.severity}</span>
                      <strong>{event.title}</strong>
                      <small className="wm-event-list-kind">
                        {EVENT_TYPE_LABELS[eventListType(event)] || eventListType(event).replace(/-/g, ' ')}
                      </small>
                      <small className="wm-event-list-place">{event.locationLabel || 'Mapped geometry'}</small>
                      <time dateTime={event.updatedAt || event.occurredAt}>{eventTimeLabel(event)}</time>
                    </button>
                  </li>
                ))}
              </ol>
            </div>
          ) : (
            <div className="wm-world-event-list-empty">
              <strong>No matching events</strong>
              <p>Adjust the search, type, severity, time or region filters.</p>
              {hasFilters ? <button type="button" onClick={() => setFilters(DEFAULT_FILTERS)}>Clear filters</button> : null}
            </div>
          )}
        </section>
      ) : null}
    </div>
  );
}
