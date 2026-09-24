import { executableWorldEventLayers } from '../config/layerRegistry';
import { worldEventRegionPreset, type WorldEventRegion } from '../config/regions';
import type { GeoEventSeverity } from '../domain/types';

export type WorldEventTimeRange = '1h' | '6h' | '24h' | '48h' | '7d' | 'all';
export type AviationLensMode = 'all' | 'trunk' | 'watch';
export type AviationRiskSource = 'all' | 'weather' | 'conflict' | 'corridor';
export type WorldEventBasemapProvider = 'auto' | 'pmtiles' | 'openfreemap' | 'carto';
export type WorldEventBasemapTheme = 'dark' | 'positron';

export interface WorldEventMapState {
  center: { lon: number; lat: number };
  zoom: number;
  region: WorldEventRegion;
  activeLayerIds: string[];
  timeRange: WorldEventTimeRange;
  severities: GeoEventSeverity[];
  selectedEventId: string | null;
  basemapProvider: WorldEventBasemapProvider;
  basemapTheme: WorldEventBasemapTheme;
  countryCode: string | null;
  aviationLens: AviationLensMode;
  aviationRiskSource: AviationRiskSource;
}

// v8 resets the map surface after the world-view disclosure regression. It
// restores the bounded trunk aviation reference and the corrected semantic
// cluster defaults without inheriting a stale v7 layer selection.
// Full topology and live-aircraft detail remain Aviation Lens choices, while
// the default retains low-contrast route runners and illustrative aircraft.
export const WORLD_EVENT_MAP_STORAGE_KEY = 'polydata:world-event-map:v8';
export const WORLD_EVENT_TIME_RANGES: readonly WorldEventTimeRange[] = ['1h', '6h', '24h', '48h', '7d', 'all'];
export const WORLD_EVENT_SEVERITIES: readonly GeoEventSeverity[] = ['info', 'watch', 'warning', 'critical'];
export const AVIATION_LENS_MODES: readonly AviationLensMode[] = ['all', 'trunk', 'watch'];
export const AVIATION_RISK_SOURCES: readonly AviationRiskSource[] = ['all', 'weather', 'conflict', 'corridor'];
export const WORLD_EVENT_BASEMAP_PROVIDERS: readonly WorldEventBasemapProvider[] = ['auto', 'pmtiles', 'openfreemap', 'carto'];
export const WORLD_EVENT_BASEMAP_THEMES: readonly WorldEventBasemapTheme[] = ['dark', 'positron'];

export function defaultWorldEventMapState(): WorldEventMapState {
  const global = worldEventRegionPreset('global');
  return {
    center: { ...global.center },
    zoom: global.zoom,
    region: 'global',
    activeLayerIds: executableWorldEventLayers().filter((layer) => layer.defaultEnabled).map((layer) => layer.id),
    timeRange: '7d',
    severities: [...WORLD_EVENT_SEVERITIES],
    selectedEventId: null,
    basemapProvider: 'auto',
    basemapTheme: 'dark',
    countryCode: null,
    aviationLens: 'trunk',
    aviationRiskSource: 'all',
  };
}

export function clampWorldEventZoom(value: unknown) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.max(0.75, Math.min(8, numeric)) : 1.25;
}

export function clampLongitude(value: unknown) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0;
  return Math.max(-180, Math.min(180, numeric));
}

export function clampLatitude(value: unknown) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0;
  return Math.max(-85, Math.min(85, numeric));
}
