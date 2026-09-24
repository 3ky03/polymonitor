import { executableWorldEventLayers } from '../config/layerRegistry';
import { isWorldEventRegion, worldEventRegionPreset } from '../config/regions';
import type { GeoEventSeverity } from '../domain/types';
import {
  clampLatitude,
  clampLongitude,
  clampWorldEventZoom,
  defaultWorldEventMapState,
  WORLD_EVENT_SEVERITIES,
  WORLD_EVENT_TIME_RANGES,
  AVIATION_LENS_MODES,
  AVIATION_RISK_SOURCES,
  WORLD_EVENT_BASEMAP_PROVIDERS,
  WORLD_EVENT_BASEMAP_THEMES,
  type AviationLensMode,
  type AviationRiskSource,
  type WorldEventMapState,
  type WorldEventTimeRange,
  type WorldEventBasemapProvider,
  type WorldEventBasemapTheme,
} from './mapState';

function parsedList(value: string | null) {
  return value == null ? null : value.split(',').map((item) => item.trim()).filter(Boolean);
}

function finite(value: string | null) {
  if (value == null || value.trim() === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

export function parseWorldEventMapState(
  search: string,
  fallback: WorldEventMapState = defaultWorldEventMapState(),
): WorldEventMapState {
  const params = new URLSearchParams(search);
  // A serialized map snapshot is authoritative. Optional fields deliberately
  // omitted from that snapshot (for example event/country) must clear stale
  // local state instead of silently inheriting a previous investigation.
  // Partial links still layer over the supplied fallback for backwards
  // compatibility with old region-only and layer-only URLs.
  const isCompleteSnapshot = ['center', 'zoom', 'layers', 'time'].every((key) => params.has(key));
  const base = isCompleteSnapshot ? defaultWorldEventMapState() : fallback;
  let state: WorldEventMapState = {
    ...base,
    center: { ...base.center },
    activeLayerIds: [...base.activeLayerIds],
    severities: [...base.severities],
  };
  const region = params.get('region');
  if (isWorldEventRegion(region)) {
    const preset = worldEventRegionPreset(region);
    state = { ...state, region, center: { ...preset.center }, zoom: preset.zoom };
  }
  const center = parsedList(params.get('center'));
  if (center?.length === 2) {
    const lon = finite(center[0] || null);
    const lat = finite(center[1] || null);
    if (lon != null && lat != null && lon >= -180 && lon <= 180 && lat >= -85 && lat <= 85) {
      state.center = { lon: clampLongitude(lon), lat: clampLatitude(lat) };
    }
  }
  const zoom = finite(params.get('zoom'));
  if (zoom != null) state.zoom = clampWorldEventZoom(zoom);

  const selectable = new Set(executableWorldEventLayers().map((layer) => layer.id));
  const rawLayers = params.get('layers');
  const layers = parsedList(rawLayers);
  const validLayers = layers ? layers.filter((layerId) => selectable.has(layerId)) : null;
  if (validLayers && (validLayers.length > 0 || rawLayers === '')) {
    state.activeLayerIds = [...new Set(validLayers)];
  }

  const timeRange = params.get('time');
  if (WORLD_EVENT_TIME_RANGES.includes(timeRange as WorldEventTimeRange)) {
    state.timeRange = timeRange as WorldEventTimeRange;
  }
  const rawSeverities = params.get('severity');
  const severities = parsedList(rawSeverities);
  const validSeverities = severities?.filter(
    (severity): severity is GeoEventSeverity => WORLD_EVENT_SEVERITIES.includes(severity as GeoEventSeverity),
  );
  if (validSeverities && (validSeverities.length > 0 || rawSeverities === '')) {
    state.severities = [...new Set(validSeverities)];
  }
  const selectedEventId = params.get('event');
  if (selectedEventId) state.selectedEventId = selectedEventId;
  const provider = params.get('basemap');
  if (WORLD_EVENT_BASEMAP_PROVIDERS.includes(provider as WorldEventBasemapProvider)) {
    state.basemapProvider = provider as WorldEventBasemapProvider;
  }
  const theme = params.get('theme');
  if (WORLD_EVENT_BASEMAP_THEMES.includes(theme as WorldEventBasemapTheme)) {
    state.basemapTheme = theme as WorldEventBasemapTheme;
  }
  const country = params.get('country')?.trim().toUpperCase();
  if (country && /^[A-Z]{2}$/.test(country)) state.countryCode = country;
  const aviationLens = params.get('air');
  if (AVIATION_LENS_MODES.includes(aviationLens as AviationLensMode)) {
    state.aviationLens = aviationLens as AviationLensMode;
  }
  const aviationRiskSource = params.get('airRisk');
  if (AVIATION_RISK_SOURCES.includes(aviationRiskSource as AviationRiskSource)) {
    state.aviationRiskSource = aviationRiskSource as AviationRiskSource;
  }
  return state;
}

export function serializeWorldEventMapUrl(state: WorldEventMapState, baseUrl: string) {
  const url = new URL(baseUrl);
  url.searchParams.set('center', `${state.center.lon.toFixed(4)},${state.center.lat.toFixed(4)}`);
  url.searchParams.set('zoom', state.zoom.toFixed(2));
  url.searchParams.set('region', state.region);
  url.searchParams.set('layers', state.activeLayerIds.join(','));
  url.searchParams.set('time', state.timeRange);
  url.searchParams.set('severity', state.severities.join(','));
  if (state.selectedEventId) url.searchParams.set('event', state.selectedEventId);
  else url.searchParams.delete('event');
  url.searchParams.set('basemap', state.basemapProvider);
  url.searchParams.set('theme', state.basemapTheme);
  if (state.countryCode) url.searchParams.set('country', state.countryCode);
  else url.searchParams.delete('country');
  url.searchParams.set('air', state.aviationLens);
  url.searchParams.set('airRisk', state.aviationRiskSource);
  return url.toString();
}

export function readStoredWorldEventMapState(
  raw: string | null,
  fallback: WorldEventMapState = defaultWorldEventMapState(),
): WorldEventMapState {
  if (!raw) return fallback;
  try {
    const parsed = JSON.parse(raw) as Partial<WorldEventMapState>;
    const params = new URLSearchParams();
    if (isWorldEventRegion(parsed.region)) params.set('region', parsed.region);
    if (parsed.center) params.set('center', `${parsed.center.lon},${parsed.center.lat}`);
    if (parsed.zoom != null) params.set('zoom', String(parsed.zoom));
    if (Array.isArray(parsed.activeLayerIds)) params.set('layers', parsed.activeLayerIds.join(','));
    if (parsed.timeRange) params.set('time', parsed.timeRange);
    if (Array.isArray(parsed.severities)) params.set('severity', parsed.severities.join(','));
    if (parsed.selectedEventId) params.set('event', parsed.selectedEventId);
    if (parsed.basemapProvider) params.set('basemap', parsed.basemapProvider);
    if (parsed.basemapTheme) params.set('theme', parsed.basemapTheme);
    if (parsed.countryCode) params.set('country', parsed.countryCode);
    if (parsed.aviationLens) params.set('air', parsed.aviationLens);
    if (parsed.aviationRiskSource) params.set('airRisk', parsed.aviationRiskSource);
    return parseWorldEventMapState(params.toString(), fallback);
  } catch {
    return fallback;
  }
}
