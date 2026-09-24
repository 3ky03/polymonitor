import maplibregl, { type StyleSpecification } from 'maplibre-gl';
import {
  OPENFREEMAP_DARK_STYLE,
  OPENFREEMAP_LIGHT_STYLE,
  CARTO_DARK_STYLE,
  CARTO_LIGHT_STYLE,
  WORLD_EVENT_PMTILES_URL,
  type WeatherMapTheme,
} from './weatherBasemapMeta';

export {
  CARTO_DARK_STYLE,
  CARTO_LIGHT_STYLE,
  OPENFREEMAP_DARK_STYLE,
  OPENFREEMAP_LIGHT_STYLE,
  WORLD_EVENT_PMTILES_URL,
  getWeatherBasemapAttribution,
  type WeatherMapTheme,
} from './weatherBasemapMeta';

let pmtilesRegistered = false;
let pmtilesRegistration: Promise<void> | null = null;

/** Register the exact PMTiles protocol used by WorldMonitor, once per page. */
export async function registerWorldEventPMTilesProtocol() {
  if (pmtilesRegistered) return;
  pmtilesRegistration ??= (async () => {
    const { Protocol } = await import('pmtiles');
    if (pmtilesRegistered) return;
    const protocol = new Protocol();
    maplibregl.addProtocol('pmtiles', protocol.tile);
    pmtilesRegistered = true;
  })().catch((error) => {
    pmtilesRegistration = null;
    throw error;
  });
  await pmtilesRegistration;
}

/**
 * Build WorldMonitor's black Protomaps style from ranked vector-tile features.
 * Country/city disclosure, collision, font weight, boundaries and halos all
 * remain owned by the provider style instead of being re-created after load.
 */
export function resolveWorldEventPMTilesUrl(
  url: string,
  origin = typeof window !== 'undefined' ? window.location.origin : '',
) {
  if (/^https?:\/\//i.test(url) || !origin) return url;
  return new URL(url, origin).href;
}

export async function buildWorldEventPMTilesStyle(url: string): Promise<StyleSpecification> {
  const { layers, namedFlavor } = await import('@protomaps/basemaps');
  const archiveUrl = resolveWorldEventPMTilesUrl(url);
  const rankedLayers = layers('basemap', namedFlavor('black'), { lang: 'en' }) as StyleSpecification['layers'];
  const eventBasemapLayerIds = new Set([
    'background',
    'earth',
    'water',
    'boundaries_country',
    'boundaries',
    'water_label_ocean',
    'water_label_lakes',
    'earth_label_islands',
    'places_region',
    'places_locality',
    'places_country',
  ]);
  const tunedLayers = rankedLayers.filter((layer) => eventBasemapLayerIds.has(layer.id)).flatMap((layer) => {
    if (layer.id === 'places_country') {
      const regional = { ...layer, minzoom: 2.6 };
      const global = {
        ...layer,
        id: 'places_country_global',
        maxzoom: 2.6,
        filter: ['all', ['==', 'kind', 'country'], ['>=', 'population_rank', 9]],
        layout: {
          ...layer.layout,
          'text-size': ['interpolate', ['linear'], ['zoom'], 0, 14, 2, 18, 2.6, 19],
          'text-padding': 8,
        },
        paint: {
          ...layer.paint,
          'text-color': '#aeb7ba',
          'text-halo-color': '#070a0c',
          'text-halo-width': 1.25,
          'text-halo-blur': 0.15,
          'text-opacity': 0.95,
        },
      };
      return [global, regional] as typeof rankedLayers;
    }
    if (layer.id === 'places_locality') {
      const detail = { ...layer, minzoom: 4.5 };
      const regional = {
        ...layer,
        id: 'places_locality_regional',
        minzoom: 2.6,
        maxzoom: 4.5,
        filter: ['all', ['==', 'kind', 'locality'], ['>=', 'population_rank', 11]],
      };
      const global = {
        ...layer,
        id: 'places_locality_global',
        maxzoom: 2.6,
        filter: ['all', ['==', 'kind', 'locality'], ['>=', 'population_rank', 11]],
        layout: {
          ...layer.layout,
          'text-size': ['interpolate', ['linear'], ['zoom'], 0, 13, 2, 17, 2.6, 18],
          'text-padding': 10,
        },
        paint: {
          ...layer.paint,
          'text-color': '#aeb7ba',
          'text-halo-color': '#070a0c',
          'text-halo-width': 1.25,
          'text-halo-blur': 0.15,
          'text-opacity': 0.94,
        },
      };
      return [global, regional, detail] as typeof rankedLayers;
    }
    if (layer.id === 'water_label_lakes' || layer.id === 'earth_label_islands') return [{ ...layer, minzoom: 4 }];
    if (layer.id === 'places_region') return [{ ...layer, minzoom: 5 }];
    if (layer.id === 'places_subplace') return [{ ...layer, minzoom: 7 }];
    if (layer.id === 'boundaries') return [{ ...layer, minzoom: 5 }];
    return [layer];
  }) as StyleSpecification['layers'];
  return {
    version: 8,
    glyphs: 'https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf',
    sprite: 'https://protomaps.github.io/basemaps-assets/sprites/v4/dark',
    sources: {
      basemap: {
        type: 'vector',
        url: `pmtiles://${archiveUrl}`,
        attribution: '<a href="https://protomaps.com">Protomaps</a> | <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>',
      },
    },
    layers: tunedLayers,
  };
}

export type WeatherBasemapProvider = 'auto' | 'pmtiles' | 'openfreemap' | 'carto';

export async function getWeatherMapStyle(
  theme: WeatherMapTheme = 'dark',
  provider: WeatherBasemapProvider = 'auto',
): Promise<StyleSpecification | string> {
  const resolvedProvider = provider === 'auto'
    ? (theme !== 'positron' && WORLD_EVENT_PMTILES_URL ? 'pmtiles' : 'openfreemap')
    : provider;
  if (resolvedProvider === 'pmtiles' && WORLD_EVENT_PMTILES_URL) {
    await registerWorldEventPMTilesProtocol();
    return buildWorldEventPMTilesStyle(WORLD_EVENT_PMTILES_URL);
  }
  if (resolvedProvider === 'carto') return theme === 'positron' ? CARTO_LIGHT_STYLE : CARTO_DARK_STYLE;
  return theme === 'positron' ? OPENFREEMAP_LIGHT_STYLE : OPENFREEMAP_DARK_STYLE;
}


type LabelCapableMap = {
  getZoom: () => number;
  getStyle: () => {
    sources?: Record<string, unknown>;
    layers?: Array<{ id: string; type?: string; source?: string; 'source-layer'?: string }>;
  };
  getLayoutProperty: (layerId: string, name: 'text-field') => unknown;
  setLayoutProperty: (
    layerId: string,
    name: 'text-field' | 'text-size' | 'visibility',
    value: unknown,
  ) => void;
  setPaintProperty: (
    layerId: string,
    name: 'text-color' | 'text-halo-color' | 'text-halo-width' | 'text-halo-blur' | 'text-opacity',
    value: unknown,
  ) => void;
};

function usesProtomapsStyle(map: LabelCapableMap) {
  const style = map.getStyle();
  return Boolean(style.sources?.basemap)
    || (style.layers || []).some((layer) => layer.source === 'basemap' && layer['source-layer'] === 'places');
}

function hasNameField(field: unknown) {
  return JSON.stringify(field || '').toLowerCase().includes('name');
}

const ENGLISH_NAME_EXPRESSION = [
  'coalesce',
  ['get', 'name_en'],
  ['get', 'name:en'],
  ['get', 'name:latin'],
  ['get', 'name'],
];

const PROTOMAPS_ENGLISH_NAME_EXPRESSION = [
  'coalesce',
  ['get', 'name:en'],
  ['get', 'name'],
];

type LabelKind =
  | 'continent'
  | 'country-major'
  | 'country-minor'
  | 'country-other'
  | 'city-major'
  | 'city'
  | 'state'
  | 'locality'
  | 'context';

type LabelDensity = 'global' | 'regional' | 'area' | 'detail';

const labelDensityByMap = new WeakMap<LabelCapableMap, LabelDensity>();

function labelKind(layer: { id: string; 'source-layer'?: string }): LabelKind {
  const identity = `${layer.id} ${layer['source-layer'] || ''}`.toLowerCase();
  if (identity.includes('place_continent')) return 'continent';
  if (identity.includes('place_country_other')) return 'country-other';
  if (identity.includes('place_country_major')) return 'country-major';
  if (identity.includes('place_country')) return 'country-minor';
  if (identity.includes('place_city_large')) return 'city-major';
  if (identity.includes('place_city')) return 'city';
  if (identity.includes('place_state')) return 'state';
  if (/place_(town|village|suburb|other)/.test(identity)) return 'locality';
  return 'context';
}

function labelDensity(zoom: number): LabelDensity {
  if (zoom < 1.85) return 'global';
  if (zoom < 3.1) return 'regional';
  if (zoom < 4.5) return 'area';
  return 'detail';
}

function labelVisible(kind: LabelKind, density: LabelDensity) {
  if (kind === 'continent' || kind === 'country-major' || kind === 'city-major') return true;
  if (density === 'global') return false;
  if (kind === 'country-minor') return true;
  if (density === 'regional') return false;
  if (kind === 'country-other' || kind === 'city' || kind === 'state') return true;
  if (density === 'area') return false;
  return true;
}

function labelSize(kind: LabelKind): unknown | null {
  if (kind === 'country-major') return ['interpolate', ['linear'], ['zoom'], 0, 13, 3, 15, 5, 17];
  if (kind === 'country-minor') return ['interpolate', ['linear'], ['zoom'], 1.85, 11, 4, 13, 6, 15];
  if (kind === 'city-major') return ['interpolate', ['linear'], ['zoom'], 0, 14, 3, 16, 6, 18];
  if (kind === 'city') return ['interpolate', ['linear'], ['zoom'], 3.1, 11, 5, 13, 7, 15];
  return null;
}

/**
 * OpenFreeMap-only fallback tuning. Protomaps already implements ranked labels
 * and must never be overwritten by this compatibility path.
 */
export function reinforceWorldEventBasemapLabels(map: LabelCapableMap) {
  labelDensityByMap.delete(map);
  if (usesProtomapsStyle(map)) {
    // WorldMonitor runs localizeMapLabels() after the Protomaps style loads.
    // This removes the generated bilingual second line while preserving the
    // provider's population rank, min_zoom, collision and font hierarchy.
    for (const layer of map.getStyle().layers || []) {
      if (layer.type !== 'symbol') continue;
      try {
        if (!hasNameField(map.getLayoutProperty(layer.id, 'text-field'))) continue;
        map.setLayoutProperty(layer.id, 'text-field', PROTOMAPS_ENGLISH_NAME_EXPRESSION);
      } catch {
        // A style can replace a symbol layer during load.
      }
    }
    return;
  }
  for (const layer of map.getStyle().layers || []) {
    if (layer.type !== 'symbol' || layer['source-layer'] !== 'place') continue;
    try {
      if (!hasNameField(map.getLayoutProperty(layer.id, 'text-field'))) continue;
      const kind = labelKind(layer);
      map.setLayoutProperty(layer.id, 'text-field', ENGLISH_NAME_EXPRESSION);
      const size = labelSize(kind);
      if (size) map.setLayoutProperty(layer.id, 'text-size', size);
      map.setPaintProperty(layer.id, 'text-color', '#aeb7ba');
      map.setPaintProperty(layer.id, 'text-halo-color', '#070a0c');
      map.setPaintProperty(layer.id, 'text-halo-width', 1.25);
      map.setPaintProperty(layer.id, 'text-halo-blur', 0.15);
      map.setPaintProperty(
        layer.id,
        'text-opacity',
        kind === 'context' ? 0.72 : kind === 'country-major' || kind === 'city-major' ? 0.95 : 0.9,
      );
    } catch {
      // A remote style may replace a layer during load. Its defaults remain usable.
    }
  }
  refreshWorldEventBasemapLabelDensity(map);
}

export function refreshWorldEventBasemapLabelDensity(map: LabelCapableMap) {
  if (usesProtomapsStyle(map)) return;
  const density = labelDensity(map.getZoom());
  if (labelDensityByMap.get(map) === density) return;
  for (const layer of map.getStyle().layers || []) {
    if (layer.type !== 'symbol' || layer['source-layer'] !== 'place') continue;
    try {
      if (!hasNameField(map.getLayoutProperty(layer.id, 'text-field'))) continue;
      map.setLayoutProperty(layer.id, 'visibility', labelVisible(labelKind(layer), density) ? 'visible' : 'none');
    } catch {
      // A style can be replaced between getStyle() and setLayoutProperty().
    }
  }
  labelDensityByMap.set(map, density);
}

export function getWeatherMapFallbackStyle(theme: WeatherMapTheme = 'dark') {
  const light = theme === 'positron';
  const background = light ? '#dce5e8' : '#070a0c';
  const land = light ? '#f4f1e9' : '#1c252a';
  const border = light ? '#7d8a90' : '#69767d';

  return {
    version: 8,
    glyphs: 'https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf',
    sources: {
      'wm-weather-country-boundaries': {
        type: 'geojson',
        data: '/map-data/world-countries.geojson',
      },
    },
    layers: [
      {
        id: 'wm-local-background',
        type: 'background',
        paint: { 'background-color': background },
      },
      {
        id: 'wm-local-land',
        type: 'fill',
        source: 'wm-weather-country-boundaries',
        paint: { 'fill-color': land, 'fill-opacity': 1 },
      },
      {
        id: 'wm-local-country-border',
        type: 'line',
        source: 'wm-weather-country-boundaries',
        paint: {
          'line-color': border,
          'line-opacity': light ? 0.72 : 0.82,
          'line-width': ['interpolate', ['linear'], ['zoom'], 0, 0.45, 4, 0.9, 7, 1.4],
        },
      },
      {
        id: 'wm-local-country-labels',
        type: 'symbol',
        source: 'wm-weather-country-boundaries',
        layout: {
          'text-field': ['coalesce', ['get', 'name:en'], ['get', 'name']],
          'text-font': ['Noto Sans Medium'],
          'text-size': ['interpolate', ['linear'], ['zoom'], 0, 13, 3, 15, 5, 17],
          'text-padding': 8,
          'text-max-width': 8,
          'text-letter-spacing': 0.035,
          'text-allow-overlap': false,
          'text-ignore-placement': false,
        },
        paint: {
          'text-color': light ? '#4a5459' : '#aeb7ba',
          'text-halo-color': light ? '#eef3f4' : '#070a0c',
          'text-halo-width': 1.25,
          'text-halo-blur': 0.15,
          'text-opacity': 0.94,
        },
      },
    ],
  } satisfies StyleSpecification;
}
