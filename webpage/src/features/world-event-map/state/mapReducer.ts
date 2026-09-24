import { executableWorldEventLayers } from '../config/layerRegistry';
import { isWorldEventRegion, worldEventRegionPreset, type WorldEventRegion } from '../config/regions';
import type { GeoEventSeverity } from '../domain/types';
import {
  clampLatitude,
  clampLongitude,
  clampWorldEventZoom,
  defaultWorldEventMapState,
  type WorldEventMapState,
  type WorldEventTimeRange,
  type AviationLensMode,
  type AviationRiskSource,
  type WorldEventBasemapProvider,
  type WorldEventBasemapTheme,
} from './mapState';

export type WorldEventMapAction =
  | { type: 'set-camera'; center: { lon: number; lat: number }; zoom: number }
  | { type: 'set-zoom'; zoom: number }
  | { type: 'set-region'; region: WorldEventRegion }
  | { type: 'toggle-layer'; layerId: string }
  | { type: 'set-layers'; layerIds: string[] }
  | { type: 'set-time-range'; timeRange: WorldEventTimeRange }
  | { type: 'set-severities'; severities: GeoEventSeverity[] }
  | { type: 'select-event'; eventId: string | null }
  | { type: 'set-basemap-provider'; provider: WorldEventBasemapProvider }
  | { type: 'set-basemap-theme'; theme: WorldEventBasemapTheme }
  | { type: 'set-country'; countryCode: string | null }
  | { type: 'set-aviation-lens'; lens: AviationLensMode }
  | { type: 'set-aviation-risk-source'; source: AviationRiskSource }
  | { type: 'replace'; state: WorldEventMapState }
  | { type: 'reset' };

function validLayerIds(layerIds: string[]) {
  const selectable = new Set(executableWorldEventLayers().map((layer) => layer.id));
  return [...new Set(layerIds.filter((layerId) => selectable.has(layerId)))];
}

export function worldEventMapReducer(
  state: WorldEventMapState,
  action: WorldEventMapAction,
): WorldEventMapState {
  if (action.type === 'set-camera') {
    return {
      ...state,
      center: {
        lon: clampLongitude(action.center.lon),
        lat: clampLatitude(action.center.lat),
      },
      zoom: clampWorldEventZoom(action.zoom),
    };
  }
  if (action.type === 'set-zoom') return { ...state, zoom: clampWorldEventZoom(action.zoom) };
  if (action.type === 'set-region') {
    if (!isWorldEventRegion(action.region)) return state;
    const preset = worldEventRegionPreset(action.region);
    return { ...state, region: action.region, center: { ...preset.center }, zoom: preset.zoom };
  }
  if (action.type === 'toggle-layer') {
    const layerIds = state.activeLayerIds.includes(action.layerId)
      ? state.activeLayerIds.filter((layerId) => layerId !== action.layerId)
      : [...state.activeLayerIds, action.layerId];
    return { ...state, activeLayerIds: validLayerIds(layerIds) };
  }
  if (action.type === 'set-layers') return { ...state, activeLayerIds: validLayerIds(action.layerIds) };
  if (action.type === 'set-time-range') return { ...state, timeRange: action.timeRange };
  if (action.type === 'set-severities') {
    return { ...state, severities: [...new Set(action.severities)] };
  }
  if (action.type === 'select-event') return { ...state, selectedEventId: action.eventId };
  if (action.type === 'set-basemap-provider') return { ...state, basemapProvider: action.provider };
  if (action.type === 'set-basemap-theme') return { ...state, basemapTheme: action.theme };
  if (action.type === 'set-country') {
    const normalized = action.countryCode?.trim().toUpperCase() || null;
    return { ...state, countryCode: normalized && /^[A-Z]{2}$/.test(normalized) ? normalized : null };
  }
  if (action.type === 'set-aviation-lens') return { ...state, aviationLens: action.lens };
  if (action.type === 'set-aviation-risk-source') return { ...state, aviationRiskSource: action.source };
  if (action.type === 'replace') return action.state;
  if (action.type === 'reset') return defaultWorldEventMapState();
  return state;
}
