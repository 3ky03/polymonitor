import type { GeoEvent } from '../domain/types';
import { eventMatchesWorldEventLayers, isHazardGeoEvent } from '../config/layerRegistry';
import type { WorldEventMapState, WorldEventTimeRange } from './mapState';
import type { CountryGeometryIndex } from '../domain/countryGeometry';

const TIME_RANGE_MS: Record<Exclude<WorldEventTimeRange, 'all'>, number> = {
  '1h': 60 * 60 * 1000,
  '6h': 6 * 60 * 60 * 1000,
  '24h': 24 * 60 * 60 * 1000,
  '48h': 48 * 60 * 60 * 1000,
  '7d': 7 * 24 * 60 * 60 * 1000,
};

export function filterWorldEventMapEvents(
  events: GeoEvent[],
  state: Pick<WorldEventMapState, 'timeRange' | 'severities'> & Partial<Pick<WorldEventMapState, 'countryCode'>>,
  now = Date.now(),
  countryGeometry: CountryGeometryIndex | null = null,
) {
  const cutoff = state.timeRange === 'all' ? null : now - TIME_RANGE_MS[state.timeRange];
  const severities = new Set(state.severities);
  const countryCode = state.countryCode?.toUpperCase() || null;
  return events.filter((event) => {
    if (!severities.has(event.severity)) return false;
    if (countryCode) {
      const properties = event.properties || {};
      const directCodes = [
        properties.countryCode,
        properties.countryIso2,
        properties.iso2,
        properties['ISO3166-1-Alpha-2'],
      ].filter(Boolean).map((value) => String(value).toUpperCase());
      const listCodes = Array.isArray(properties.countryCodes)
        ? properties.countryCodes.map((value) => String(value).toUpperCase())
        : [];
      const explicitlyMatches = directCodes.includes(countryCode) || listCodes.includes(countryCode);
      const spatiallyMatches = event.geometry && countryGeometry
        ? countryGeometry.intersects(countryCode, event.geometry)
        : false;
      if (!explicitlyMatches && !spatiallyMatches) return false;
    }
    if (isHazardGeoEvent(event)) {
      if (event.lifecycle === 'ended' || event.revision.cancelled) return false;
      const expiresAt = event.expiresAt ? Date.parse(event.expiresAt) : Number.NaN;
      if (Number.isFinite(expiresAt) && expiresAt <= now) return false;
    }
    if (cutoff == null) return true;
    const timestamp = event.updatedAt || event.occurredAt;
    if (!timestamp) return false;
    const parsed = Date.parse(timestamp);
    return Number.isFinite(parsed) && parsed >= cutoff && parsed <= now;
  });
}

export function filterWorldEventMapEventsForLayers(
  events: GeoEvent[],
  state: Pick<WorldEventMapState, 'activeLayerIds' | 'timeRange' | 'severities'>
    & Partial<Pick<WorldEventMapState, 'countryCode'>>,
  now = Date.now(),
  countryGeometry: CountryGeometryIndex | null = null,
) {
  return filterWorldEventMapEvents(
    events.filter((event) => eventMatchesWorldEventLayers(event, state.activeLayerIds)),
    state,
    now,
    countryGeometry,
  );
}
