import type { PickingInfo } from '@deck.gl/core';
import { MapboxOverlay } from '@deck.gl/mapbox';
import { PathLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import maplibregl, { type Map as MapLibreMap } from 'maplibre-gl';
import { feature } from 'topojson-client';
import countriesAtlas from 'world-atlas/countries-50m.json';
import { OPENFREEMAP_DARK_STYLE } from '@/config/weatherBasemap';
import type { MarketGroupItem, RuntimeGeoSanctionsShockItem } from '@/types';
import { matchPolymarketMarkets, WORLD_CUP_HOST_MATCH_COUNTS } from './data';
import type {
  WorldCupCityWeather,
  WorldCupMatch,
  WorldCupOddsSnapshot,
  WorldCupStage,
  WorldCupVenueCity,
} from './types';

type WorldCupMapProps = {
  cities: WorldCupVenueCity[];
  matches: WorldCupMatch[];
  weather: WorldCupCityWeather[];
  marketGroups: MarketGroupItem[];
  odds: WorldCupOddsSnapshot[];
  conflicts: RuntimeGeoSanctionsShockItem[];
  nextMatch: WorldCupMatch | null;
  selectedCityId: string | null;
  selectedMatchId: string | null;
  onSelectCity: (cityId: string) => void;
  onSelectMatch?: (matchId: string) => void;
};

type HostCountryKey = 'us' | 'canada' | 'mexico';
type WorldCupLayerKey =
  | 'cities'
  | 'schedule'
  | 'weather'
  | 'markets'
  | 'odds'
  | 'transit'
  | 'teams'
  | 'conflicts'
  | 'countryRisk'
  | 'globalRisk';
type WorldCupViewportPreset = 'north-america' | 'usa' | 'mexico' | 'canada' | 'next' | 'global-risk';
type WorldCupRoundFilter = 'all' | WorldCupStage;
type WorldCupAtlasFilters = {
  round: WorldCupRoundFilter;
  date: string;
  team: string;
  cityId: string;
  marketOnly: boolean;
  weatherWatchOnly: boolean;
  riskFlaggedOnly: boolean;
};

type MapRegionHover = {
  region: string;
  country: string;
  screenX: number;
  screenY: number;
};

type WorldCupRiskLevel = 'quiet' | 'watch' | 'elevated' | 'critical';

type CountryConflictRisk = {
  iso2: string;
  name: string;
  eventCount: number;
  deaths: number;
  stateCount: number;
  nonStateCount: number;
  oneSidedCount: number;
  latestAt: string | null;
  score: number;
  level: WorldCupRiskLevel;
  topActors: string[];
  topLocations: string[];
  conflicts: ConflictSignal[];
};

type SelectedRiskIntel =
  | { kind: 'country'; risk: CountryConflictRisk }
  | { kind: 'conflict'; conflict: ConflictSignal };

type CitySignal = {
  type: 'host-city';
  city: WorldCupVenueCity;
  weather: WorldCupCityWeather | null;
  matches: WorldCupMatch[];
  nextMatch: WorldCupMatch | null;
  selected: boolean;
  next: boolean;
  important: boolean;
  plannedMatchCount: number;
  marketCount: number;
  oddsCount: number;
  weatherRisk: number;
};

type PointSignal = {
  type: 'weather' | 'market' | 'odds' | 'transit' | 'team';
  id: string;
  city: WorldCupVenueCity;
  label: string;
  sublabel: string;
  lon: number;
  lat: number;
  weight: number;
};

type ConflictSignal = {
  type: 'conflict';
  id: string;
  lon: number;
  lat: number;
  iso2: string | null;
  country: string;
  location: string;
  label: string;
  sublabel: string;
  actors: string;
  sideA: string;
  sideB: string;
  deaths: number;
  deathsLow: number | null;
  deathsHigh: number | null;
  violenceType: string;
  violenceLabel: string;
  occurredAt: string | null;
  source: string | null;
  sourceUrl: string | null;
  tone: 'state' | 'nonstate' | 'onesided' | 'unknown';
  color: [number, number, number, number];
  ringColor: [number, number, number, number];
};

type SchedulePath = {
  type: 'schedule';
  id: string;
  city: WorldCupVenueCity;
  match: WorldCupMatch;
  path: [number, number][];
  selected: boolean;
  next: boolean;
};

type DeckObject = CitySignal | PointSignal | SchedulePath | ConflictSignal;

type EnabledLayers = Record<WorldCupLayerKey, boolean>;

const IMPORTANT_CITY_IDS = new Set(['mexico-city', 'new-york-new-jersey', 'dallas', 'los-angeles']);
const FINAL_CITY_IDS = new Set(['new-york-new-jersey']);
const OPENING_CITY_IDS = new Set(['mexico-city']);
const KNOCKOUT_SLOT_COUNTS: Record<string, number> = {
  atlanta: 3,
  boston: 1,
  dallas: 4,
  houston: 1,
  'kansas-city': 1,
  'los-angeles': 2,
  miami: 2,
  'new-york-new-jersey': 3,
  philadelphia: 1,
  'san-francisco': 1,
  seattle: 1,
  'mexico-city': 2,
  toronto: 1,
  vancouver: 1,
};
const COUNTRIES_GEOJSON = feature(countriesAtlas as any, (countriesAtlas as any).objects.countries) as any;
const LOCAL_US_STATES_TOPOJSON_URL = '/map-data/us-states-10m.json';
const LOCAL_CANADA_PROVINCES_GEOJSON_URL = '/map-data/canada-provinces.geojson';
const LOCAL_MEXICO_STATES_GEOJSON_URL = '/map-data/mexico-states.geojson';
const LOCAL_WORLD_COUNTRIES_GEOJSON_URL = '/map-data/world-countries.geojson';
const WORLDCUP_REMOTE_FALLBACK_STYLE_URL = '/map-styles/worldcup-happy-dark.json';
const WORLDCUP_ATLAS_CENTER: [number, number] = [-96, 34.8];
const WORLDCUP_ATLAS_ZOOM = 3;
const PMTILES_STYLE_URL = import.meta.env.VITE_WORLDCUP_PMTILES_STYLE_URL || import.meta.env.VITE_PMTILES_STYLE_URL || '';
const NO_COUNTRY_MATCH = '__worldcup_no_country__';
const WORLD_CUP_LAYER_PANEL_COLLAPSED_STORAGE_KEY = 'polydata:worldcup-layer-panel-collapsed:v1';

const HOST_COUNTRY_META: Record<string, { key: HostCountryKey; iso2: string; label: string }> = {
  '840': { key: 'us', iso2: 'US', label: 'UNITED STATES' },
  '124': { key: 'canada', iso2: 'CA', label: 'CANADA' },
  '484': { key: 'mexico', iso2: 'MX', label: 'MEXICO' },
};
const HOST_COUNTRY_ISO2 = new Set(Object.values(HOST_COUNTRY_META).map((item) => item.iso2));
const HOST_REGION_RISK_ISO2 = new Set([
  'US', 'CA', 'MX', 'GT', 'BZ', 'SV', 'HN', 'NI', 'CR', 'PA', 'CU', 'DO', 'HT', 'JM',
]);
const NORTH_AMERICA_BOUNDS: [[number, number], [number, number]] = [[-129, 14], [-52, 58]];
const COUNTRY_NAME_TO_ISO2 = new Map<string, string>(
  (COUNTRIES_GEOJSON.features || []).flatMap((item: any) => {
    const props = item.properties || {};
    const iso2 = String(props['ISO3166-1-Alpha-2'] || '').toUpperCase();
    const name = String(props.name || '').toLowerCase();
    return iso2 && name ? [[name, iso2]] : [];
  }),
);
const COUNTRY_ISO2_ALIASES: Record<string, string> = {
  burma: 'MM',
  congo: 'CG',
  'democratic republic of congo': 'CD',
  'democratic republic of the congo': 'CD',
  'dr congo': 'CD',
  kosovo: 'XK',
  palestine: 'PS',
  russia: 'RU',
  'russian federation': 'RU',
  'south sudan': 'SS',
  sri_lanka: 'LK',
  'sri lanka': 'LK',
  syria: 'SY',
  taiwan: 'TW',
  turkey: 'TR',
  turkiye: 'TR',
  'united states': 'US',
  'united states of america': 'US',
  usa: 'US',
};

const DEFAULT_ENABLED_LAYERS: EnabledLayers = {
  cities: true,
  schedule: true,
  weather: false,
  markets: false,
  odds: false,
  transit: false,
  teams: false,
  conflicts: false,
  countryRisk: false,
  globalRisk: false,
};

const DEFAULT_ATLAS_FILTERS: WorldCupAtlasFilters = {
  round: 'all',
  date: '',
  team: '',
  cityId: '',
  marketOnly: false,
  weatherWatchOnly: false,
  riskFlaggedOnly: false,
};

const ROUND_FILTER_OPTIONS: Array<{ value: WorldCupRoundFilter; label: string; short: string }> = [
  { value: 'all', label: 'All rounds', short: 'All' },
  { value: 'group', label: 'Group Stage', short: 'Group' },
  { value: 'round32', label: 'Round of 32', short: 'R32' },
  { value: 'round16', label: 'Round of 16', short: 'R16' },
  { value: 'quarterfinal', label: 'Quarter-final', short: 'QF' },
  { value: 'semifinal', label: 'Semi-final', short: 'SF' },
  { value: 'final', label: 'Final', short: 'Final' },
];

const COLORS = {
  city: [218, 224, 226, 228] as [number, number, number, number],
  cityLine: [255, 255, 255, 226] as [number, number, number, number],
  selected: [62, 211, 244, 242] as [number, number, number, number],
  selectedDim: [62, 211, 244, 38] as [number, number, number, number],
  next: [255, 176, 45, 244] as [number, number, number, number],
  nextDim: [255, 130, 20, 48] as [number, number, number, number],
  nextOuter: [255, 88, 69, 36] as [number, number, number, number],
  weather: [55, 175, 220, 54] as [number, number, number, number],
  market: [98, 190, 255, 130] as [number, number, number, number],
  odds: [244, 183, 70, 148] as [number, number, number, number],
  transit: [155, 164, 166, 112] as [number, number, number, number],
  team: [48, 218, 186, 98] as [number, number, number, number],
  route: [242, 184, 75, 72] as [number, number, number, number],
  conflictState: [255, 74, 74, 176] as [number, number, number, number],
  conflictNonState: [255, 159, 28, 168] as [number, number, number, number],
  conflictOneSided: [255, 214, 0, 156] as [number, number, number, number],
};

function firstSymbolLayerId(map: MapLibreMap) {
  const layers = map.getStyle().layers || [];
  return layers.find((layer) => layer.type === 'symbol')?.id;
}

function applyWorldMonitorMapPaint(map: MapLibreMap) {
  const layers = map.getStyle().layers || [];
  layers.forEach((layer) => {
    if (layer.id.startsWith('country-') || layer.id.startsWith('wc-')) return;
    try {
      if (layer.type === 'background') {
        map.setPaintProperty(layer.id, 'background-color', '#151515');
        map.setPaintProperty(layer.id, 'background-opacity', 1);
      } else if (layer.type === 'fill') {
        const id = layer.id.toLowerCase();
        if (id.includes('water') || id.includes('ocean')) {
          map.setPaintProperty(layer.id, 'fill-color', '#202020');
          map.setPaintProperty(layer.id, 'fill-opacity', 1);
        } else if (id.includes('park') || id.includes('landcover') || id.includes('landuse')) {
          map.setPaintProperty(layer.id, 'fill-color', '#1d1f1f');
          map.setPaintProperty(layer.id, 'fill-opacity', 0.92);
        } else {
          map.setPaintProperty(layer.id, 'fill-color', '#252626');
          map.setPaintProperty(layer.id, 'fill-opacity', 0.88);
        }
      } else if (layer.type === 'line') {
        const id = layer.id.toLowerCase();
        const isBoundary = id.includes('boundary') || id.includes('admin') || id.includes('country') || id.includes('state');
        map.setPaintProperty(layer.id, 'line-color', isBoundary ? '#8a8f91' : '#4a4d4e');
        map.setPaintProperty(layer.id, 'line-opacity', isBoundary
          ? ['interpolate', ['linear'], ['zoom'], 2, 0.34, 3, 0.5, 5, 0.68]
          : ['interpolate', ['linear'], ['zoom'], 2, 0.08, 4, 0.18, 7, 0.34]);
        map.setPaintProperty(layer.id, 'line-width', isBoundary
          ? ['interpolate', ['linear'], ['zoom'], 2, 0.78, 3, 1.05, 5, 1.4]
          : ['interpolate', ['linear'], ['zoom'], 2, 0.28, 5, 0.68, 8, 1.05]);
      } else if (layer.type === 'raster') {
        map.setPaintProperty(layer.id, 'raster-opacity', 0.62);
        map.setPaintProperty(layer.id, 'raster-saturation', -1);
        map.setPaintProperty(layer.id, 'raster-brightness-min', 0.04);
        map.setPaintProperty(layer.id, 'raster-brightness-max', 0.42);
      } else if (layer.type === 'symbol') {
        map.setPaintProperty(layer.id, 'text-color', '#818486');
        map.setPaintProperty(layer.id, 'text-halo-color', '#050505');
        map.setPaintProperty(layer.id, 'text-halo-width', 1.9);
        map.setPaintProperty(layer.id, 'text-opacity', ['interpolate', ['linear'], ['zoom'], 2, 0.52, 3, 0.7, 5, 0.84]);
      }
    } catch {
      // Some third-party style layers do not expose every paint property.
    }
  });
}

function localizeBasemapLabels(map: MapLibreMap, language = 'en') {
  const style = map.getStyle();
  const layers = style.layers || [];
  const expression: any = [
    'coalesce',
    ['get', `name:${language}`],
    ['get', 'name_en'],
    ['get', 'name:en'],
    ['get', 'name:latin'],
    ['get', 'name_int'],
    ['get', 'name'],
  ];

  layers.forEach((layer) => {
    if (layer.type !== 'symbol') return;
    try {
      const textField = map.getLayoutProperty(layer.id, 'text-field');
      if (!textField) return;
      const serialized = typeof textField === 'string' ? textField : JSON.stringify(textField);
      if (!/name/.test(serialized)) return;
      map.setLayoutProperty(layer.id, 'text-field', expression);
    } catch {
      // Third-party styles can contain non-localizable symbol layers.
    }
  });
}

function primaryBasemapStyle(): string {
  return PMTILES_STYLE_URL || OPENFREEMAP_DARK_STYLE;
}

function formatInspectorWeatherDate(date: string) {
  const normalized = /^\d{2}-\d{2}$/.test(date) ? `2026-${date}` : date;
  const parsed = new Date(`${normalized}T00:00:00Z`);
  if (!Number.isFinite(parsed.getTime())) return date.replace(/^2026-/, '');
  return new Intl.DateTimeFormat('en-US', {
    timeZone: 'UTC',
    weekday: 'short',
    month: 'short',
    day: '2-digit',
  }).format(parsed);
}

function weatherToneClass(condition = '') {
  if (/storm|thunder|rain|mist|shower/i.test(condition)) return 'rain';
  if (/humid/i.test(condition)) return 'humid';
  if (/warm|heat/i.test(condition)) return 'warm';
  if (/cloud/i.test(condition)) return 'cloud';
  return 'clear';
}

function hostCountriesGeoJson() {
  return {
    type: 'FeatureCollection',
    features: (COUNTRIES_GEOJSON.features || [])
      .filter((item: any) => HOST_COUNTRY_META[String(item.id)])
      .map((item: any) => {
        const meta = HOST_COUNTRY_META[String(item.id)]!;
        return {
          ...item,
          properties: {
            ...(item.properties || {}),
            hostKey: meta.key,
            iso2: meta.iso2,
            name: meta.label,
            'ISO3166-1-Alpha-2': meta.iso2,
          },
        };
      }),
  } as any;
}

function addLayerSafe(map: MapLibreMap, layer: any, beforeId?: string) {
  if (map.getLayer(layer.id)) return;
  try {
    map.addLayer(layer, beforeId);
  } catch {
    if (!map.getLayer(layer.id)) map.addLayer(layer);
  }
}

function addSourceSafe(map: MapLibreMap, id: string, data: any) {
  if (map.getSource(id)) return;
  map.addSource(id, { type: 'geojson', data });
}

function countryRiskPaintExpression(risks: CountryConflictRisk[], field: 'color' | 'opacity') {
  const pairs = risks.flatMap((risk): any[] => [
    risk.iso2,
    field === 'color' ? riskColor(risk.level) : riskOpacity(risk.level),
  ]);
  return ['match', ['get', 'ISO3166-1-Alpha-2'], ...pairs, field === 'color' ? 'rgba(0,0,0,0)' : 0] as any;
}

function updateCountryRiskPaint(map: MapLibreMap | null, risks: CountryConflictRisk[]) {
  if (!map || !map.getStyle() || !map.getLayer('wc-country-risk-fill')) return;
  try {
    map.setPaintProperty('wc-country-risk-fill', 'fill-color', countryRiskPaintExpression(risks, 'color'));
    map.setPaintProperty('wc-country-risk-fill', 'fill-opacity', countryRiskPaintExpression(risks, 'opacity'));
    map.setPaintProperty('wc-country-risk-border', 'line-color', countryRiskPaintExpression(risks, 'color'));
    map.setPaintProperty('wc-country-risk-border', 'line-opacity', [
      'match',
      ['get', 'ISO3166-1-Alpha-2'],
      ...risks.flatMap((risk): any[] => [risk.iso2, risk.level === 'quiet' ? 0.12 : 0.52]),
      0,
    ]);
  } catch {
    // Style can be briefly unavailable while switching basemaps.
  }
}

function visibleCountryRisksForLayers(risks: CountryConflictRisk[], enabledLayers: EnabledLayers) {
  if (!enabledLayers.countryRisk) return [];
  if (enabledLayers.globalRisk) return risks;
  return risks.filter((risk) => HOST_REGION_RISK_ISO2.has(risk.iso2));
}

function nextMatchFeatureCollection(cities: WorldCupVenueCity[], nextMatch: WorldCupMatch | null) {
  const city = nextMatch ? cities.find((item) => item.id === nextMatch.cityId) : null;
  return {
    type: 'FeatureCollection',
    features: city ? [{
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [city.longitude, city.latitude] },
      properties: {
        cityId: city.id,
        city: city.city,
        matchNumber: nextMatch?.fifaMatchNumber || null,
      },
    }] : [],
  } as any;
}

function updateNextMatchPulseSource(map: MapLibreMap | null, cities: WorldCupVenueCity[], nextMatch: WorldCupMatch | null) {
  if (!map || !map.getStyle() || !map.getSource('wc-next-match-source')) return;
  try {
    (map.getSource('wc-next-match-source') as any).setData(nextMatchFeatureCollection(cities, nextMatch));
  } catch {
    // Style/source may be transient while fallback basemap loads.
  }
}

function ensureNextMatchPulseLayer(map: MapLibreMap, cities: WorldCupVenueCity[], nextMatch: WorldCupMatch | null) {
  if (!map.getStyle()) return;
  if (!map.getSource('wc-next-match-source')) {
    map.addSource('wc-next-match-source', {
      type: 'geojson',
      data: nextMatchFeatureCollection(cities, nextMatch),
    });
  } else {
    updateNextMatchPulseSource(map, cities, nextMatch);
  }
  const beforeId = firstSymbolLayerId(map);
  addLayerSafe(map, {
    id: 'wc-next-match-pulse',
    type: 'circle',
    source: 'wc-next-match-source',
    paint: {
      'circle-radius': 20,
      'circle-color': '#ffb02d',
      'circle-opacity': 0.16,
      'circle-stroke-color': '#fff1b8',
      'circle-stroke-opacity': 0.2,
      'circle-stroke-width': 1,
    },
  }, beforeId);
  addLayerSafe(map, {
    id: 'wc-next-match-core',
    type: 'circle',
    source: 'wc-next-match-source',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 2, 6, 4, 9, 6, 12],
      'circle-color': '#ffb02d',
      'circle-opacity': 0.92,
      'circle-stroke-color': '#fff3cb',
      'circle-stroke-opacity': 0.92,
      'circle-stroke-width': 1.8,
    },
  }, beforeId);
}

function startNextMatchPulse(map: MapLibreMap, rafRef: { current: number | null }) {
  if (rafRef.current) return;
  const start = performance.now();
  const step = (now: number) => {
    if (!map.getLayer('wc-next-match-pulse')) {
      rafRef.current = null;
      return;
    }
    const t = ((now - start) % 1800) / 1800;
    const ease = 1 - (1 - t) * (1 - t);
    try {
      map.setPaintProperty('wc-next-match-pulse', 'circle-radius', 14 + ease * 24);
      map.setPaintProperty('wc-next-match-pulse', 'circle-opacity', Math.max(0, 0.22 * (1 - ease)));
      map.setPaintProperty('wc-next-match-pulse', 'circle-stroke-opacity', Math.max(0, 0.26 * (1 - ease)));
    } catch {
      rafRef.current = null;
      return;
    }
    rafRef.current = requestAnimationFrame(step);
  };
  rafRef.current = requestAnimationFrame(step);
}

function ensureCountryRiskLayers(map: MapLibreMap, risks: CountryConflictRisk[]) {
  if (!map.getStyle()) return;
  const beforeId = firstSymbolLayerId(map);
  addSourceSafe(map, 'country-boundaries', LOCAL_WORLD_COUNTRIES_GEOJSON_URL);
  addLayerSafe(map, {
    id: 'wc-country-risk-fill',
    type: 'fill',
    source: 'country-boundaries',
    paint: {
      'fill-color': countryRiskPaintExpression(risks, 'color'),
      'fill-opacity': countryRiskPaintExpression(risks, 'opacity'),
    },
  }, beforeId);
  addLayerSafe(map, {
    id: 'wc-country-risk-border',
    type: 'line',
    source: 'country-boundaries',
    paint: {
      'line-color': countryRiskPaintExpression(risks, 'color'),
      'line-opacity': 0.28,
      'line-width': ['interpolate', ['linear'], ['zoom'], 1.75, 0.8, 4, 1.25, 6, 1.8],
    },
  }, beforeId);
  updateCountryRiskPaint(map, risks);
}

function setupCountryHover(
  map: MapLibreMap,
  setRegionHover: (hover: MapRegionHover | null) => void,
  riskByIsoRef?: { current: Map<string, CountryConflictRisk> },
  onCountrySelectRef?: { current: (risk: CountryConflictRisk) => void },
) {
  if ((map as any).__worldCupCountryHoverSetup) return;
  (map as any).__worldCupCountryHoverSetup = true;
  let hoveredIso2 = '';

  const clearHover = () => {
    hoveredIso2 = '';
    map.getCanvas().style.cursor = '';
    setRegionHover(null);
    const noMatch: any = ['==', ['get', 'ISO3166-1-Alpha-2'], NO_COUNTRY_MATCH];
    if (map.getLayer('country-hover-fill')) map.setFilter('country-hover-fill', noMatch);
    if (map.getLayer('country-hover-border')) map.setFilter('country-hover-border', noMatch);
  };

  map.on('mousemove', (event) => {
    if (!map.getLayer('country-interactive')) return;
    const features = map.queryRenderedFeatures(event.point, { layers: ['country-interactive'] });
    const props = features[0]?.properties as Record<string, string> | undefined;
    const iso2 = props?.['ISO3166-1-Alpha-2'] || '';
    if (!iso2) {
      if (hoveredIso2) clearHover();
      return;
    }

    if (iso2 !== hoveredIso2) {
      hoveredIso2 = iso2;
      const filter: any = ['==', ['get', 'ISO3166-1-Alpha-2'], iso2];
      if (map.getLayer('country-hover-fill')) map.setFilter('country-hover-fill', filter);
      if (map.getLayer('country-hover-border')) map.setFilter('country-hover-border', filter);
      map.getCanvas().style.cursor = 'pointer';
    }

    const canvasRect = map.getCanvas().getBoundingClientRect();
    const risk = riskByIsoRef?.current.get(iso2);
    setRegionHover({
      region: props?.name || iso2,
      country: risk ? `${iso2} · ${risk.score}/100 · ${risk.eventCount} events` : HOST_COUNTRY_ISO2.has(iso2) ? `${iso2} HOST COUNTRY` : `${iso2} · no conflict rows`,
      screenX: event.point.x + canvasRect.left,
      screenY: event.point.y + canvasRect.top,
    });
  });

  map.on('mouseout', clearHover);
  map.on('click', (event) => {
    if (!map.getLayer('country-interactive')) return;
    const features = map.queryRenderedFeatures(event.point, { layers: ['country-interactive'] });
    const props = features[0]?.properties as Record<string, string> | undefined;
    const iso2 = props?.['ISO3166-1-Alpha-2'] || '';
    if (!iso2) return;
    const risk = riskByIsoRef?.current.get(iso2) || emptyCountryRisk(iso2, props?.name || iso2);
    onCountrySelectRef?.current(risk);
  });
}

async function loadMapSupportLayers(
  map: MapLibreMap,
  setRegionHover: (hover: MapRegionHover | null) => void,
  countryRisks: CountryConflictRisk[],
  riskByIsoRef: { current: Map<string, CountryConflictRisk> },
  onCountrySelectRef: { current: (risk: CountryConflictRisk) => void },
) {
  if (!map.getStyle() || (map as any).__worldCupSupportLayersLoading) return;
  (map as any).__worldCupSupportLayersLoading = true;
  const beforeId = firstSymbolLayerId(map);
  try {
    addSourceSafe(map, 'country-boundaries', LOCAL_WORLD_COUNTRIES_GEOJSON_URL);
    ensureCountryRiskLayers(map, countryRisks);
    addLayerSafe(map, {
      id: 'country-interactive',
      type: 'fill',
      source: 'country-boundaries',
      paint: {
        'fill-color': '#ffffff',
        'fill-opacity': 0,
      },
    }, beforeId);
    addLayerSafe(map, {
      id: 'country-hover-fill',
      type: 'fill',
      source: 'country-boundaries',
      paint: {
        'fill-color': '#ffffff',
        'fill-opacity': 0.055,
      },
      filter: ['==', ['get', 'ISO3166-1-Alpha-2'], NO_COUNTRY_MATCH],
    }, beforeId);
    addLayerSafe(map, {
      id: 'country-hover-border',
      type: 'line',
      source: 'country-boundaries',
      paint: {
        'line-color': '#ffffff',
        'line-opacity': 0.28,
        'line-width': ['interpolate', ['linear'], ['zoom'], 2, 1.35, 4, 1.85, 6, 2.35],
      },
      filter: ['==', ['get', 'ISO3166-1-Alpha-2'], NO_COUNTRY_MATCH],
    }, beforeId);
    addLayerSafe(map, {
      id: 'country-highlight-fill',
      type: 'fill',
      source: 'country-boundaries',
      paint: {
        'fill-color': '#3b82f6',
        'fill-opacity': 0,
      },
      filter: ['==', ['get', 'ISO3166-1-Alpha-2'], NO_COUNTRY_MATCH],
    }, beforeId);
    addLayerSafe(map, {
      id: 'country-highlight-border',
      type: 'line',
      source: 'country-boundaries',
      paint: {
        'line-color': '#3b82f6',
        'line-opacity': 0,
        'line-width': ['interpolate', ['linear'], ['zoom'], 2, 1.35, 4, 1.85, 6, 2.25],
      },
      filter: ['==', ['get', 'ISO3166-1-Alpha-2'], NO_COUNTRY_MATCH],
    }, beforeId);
    addSourceSafe(map, 'wc-host-countries', hostCountriesGeoJson());
    addLayerSafe(map, {
      id: 'wc-host-country-fill',
      type: 'fill',
      source: 'wc-host-countries',
      paint: {
        'fill-color': '#5f7880',
        'fill-opacity': 0.035,
      },
    }, beforeId);
    addLayerSafe(map, {
      id: 'wc-host-country-border',
      type: 'line',
      source: 'wc-host-countries',
      paint: {
        'line-color': '#c2ccd0',
        'line-opacity': ['interpolate', ['linear'], ['zoom'], 2, 0.16, 4, 0.24, 6, 0.34],
        'line-width': ['interpolate', ['linear'], ['zoom'], 2, 0.72, 4, 1.05, 6, 1.28],
      },
    }, beforeId);

    const [usTopology, canada, mexico] = await Promise.all([
      fetch(LOCAL_US_STATES_TOPOJSON_URL).then((response) => response.json()),
      fetch(LOCAL_CANADA_PROVINCES_GEOJSON_URL).then((response) => response.json()),
      fetch(LOCAL_MEXICO_STATES_GEOJSON_URL).then((response) => response.json()),
    ]);
    if (!map.getStyle()) return;
    addSourceSafe(map, 'wc-us-states', feature(usTopology, usTopology.objects.states) as any);
    addSourceSafe(map, 'wc-canada-provinces', canada);
    addSourceSafe(map, 'wc-mexico-states', mexico);
    const adminPaint = {
      'line-color': '#a9acae',
      'line-opacity': ['interpolate', ['linear'], ['zoom'], 2, 0.2, 3.5, 0.32, 6, 0.46],
      'line-dasharray': [3, 2],
      'line-width': ['interpolate', ['linear'], ['zoom'], 2, 0.62, 4, 0.88, 6, 1.12],
    };
    addLayerSafe(map, { id: 'wc-us-state-lines', type: 'line', source: 'wc-us-states', paint: adminPaint }, beforeId);
    addLayerSafe(map, { id: 'wc-canada-province-lines', type: 'line', source: 'wc-canada-provinces', paint: adminPaint }, beforeId);
    addLayerSafe(map, { id: 'wc-mexico-state-lines', type: 'line', source: 'wc-mexico-states', paint: adminPaint }, beforeId);
    setupCountryHover(map, setRegionHover, riskByIsoRef, onCountrySelectRef);
  } finally {
    (map as any).__worldCupSupportLayersLoading = false;
  }
}

function compactCityName(city: string) {
  return city.replace(' / ', '/').replace(' Bay Area', '').replace(' Gardens', '');
}

function shortCityName(city: WorldCupVenueCity) {
  const names: Record<string, string> = {
    boston: 'Boston/Foxboro',
    'los-angeles': 'LA/Inglewood',
    'new-york-new-jersey': 'NY/NJ',
    'san-francisco': 'San Francisco',
    guadalajara: 'Guadalajara',
    monterrey: 'Monterrey',
    'mexico-city': 'Mexico City',
  };
  return names[city.id] || compactCityName(city.city);
}

function labelPriority(signal: CitySignal) {
  if (signal.next) return 100;
  if (signal.selected) return 95;
  if (OPENING_CITY_IDS.has(signal.city.id)) return 88;
  if (FINAL_CITY_IDS.has(signal.city.id)) return 86;
  if (IMPORTANT_CITY_IDS.has(signal.city.id)) return 74;
  return Math.min(68, 42 + signal.plannedMatchCount * 3 + knockoutSlotCount(signal));
}

const CITY_LABEL_OFFSETS: Record<string, [number, number]> = {
  boston: [16, -22],
  'new-york-new-jersey': [18, 4],
  philadelphia: [14, 24],
  toronto: [12, -18],
  'mexico-city': [17, 5],
  guadalajara: [-118, -14],
  monterrey: [12, -22],
  dallas: [14, -20],
  houston: [14, 16],
  'los-angeles': [14, -22],
  'san-francisco': [14, -12],
  seattle: [14, -14],
  vancouver: [14, -20],
};

function visibleCityLabels(citySignals: CitySignal[], zoom: number) {
  const budget = zoom < 3.2 ? 5 : zoom < 3.85 ? 8 : zoom < 4.5 ? 12 : citySignals.length;
  return citySignals
    .slice()
    .sort((a, b) => labelPriority(b) - labelPriority(a) || b.plannedMatchCount - a.plannedMatchCount)
    .slice(0, budget);
}

function formatKickoffUtcShort(match: WorldCupMatch | null) {
  if (!match) return 'kickoff pending';
  const parsed = new Date(match.kickoffUtc);
  if (!Number.isFinite(parsed.getTime())) return shortBeijingKickoff(match);
  const monthDay = new Intl.DateTimeFormat('en-US', {
    timeZone: 'UTC',
    month: 'short',
    day: '2-digit',
  }).format(parsed);
  const time = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'UTC',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(parsed);
  return `${monthDay} · ${time} UTC`;
}

function hostCityLabel(signal: CitySignal, zoom: number) {
  if (signal.next) {
    return `NEXT · ${shortCityName(signal.city)}\nM#${signal.nextMatch?.fifaMatchNumber || '--'} · ${formatKickoffUtcShort(signal.nextMatch)}`;
  }
  const name = zoom >= 4.45 ? compactCityName(signal.city.city) : shortCityName(signal.city);
  if (zoom >= 4.55) return `${name}\n${signal.plannedMatchCount} matches`;
  if (zoom >= 3.65) return `${name}\n${cityRoleShort(signal)}`;
  return `${name}\n${cityRoleShort(signal)}`;
}

function isHostRegionConflict(signal: ConflictSignal) {
  if (signal.iso2 && HOST_REGION_RISK_ISO2.has(signal.iso2)) return true;
  return signal.lon >= -130 && signal.lon <= -48 && signal.lat >= 6 && signal.lat <= 72;
}

function visibleConflictsForLayers(conflicts: ConflictSignal[], enabledLayers: EnabledLayers) {
  if (!enabledLayers.conflicts) return [];
  return enabledLayers.globalRisk ? conflicts : conflicts.filter(isHostRegionConflict);
}

function plannedMatchCount(cityId: string, matches: WorldCupMatch[]) {
  return Math.max(WORLD_CUP_HOST_MATCH_COUNTS[cityId] || 0, matches.filter((match) => match.cityId === cityId).length);
}

function matchTitle(match: WorldCupMatch) {
  return `${match.homeTeam} vs ${match.awayTeam}`;
}

function shortKickoff(match: WorldCupMatch) {
  return (match.kickoffLocal || match.kickoffUtc || 'kickoff pending').replace(',', ' ·');
}

function shortBeijingKickoff(match: WorldCupMatch) {
  return (match.kickoffBeijing || match.kickoffUtc || 'kickoff pending').replace(',', ' ·');
}

function venueTimezoneLabel(city: WorldCupVenueCity) {
  return (city.timezone || city.country || 'HOST').replace('America/', '');
}

function stageFilterLabel(stage: WorldCupRoundFilter) {
  if (stage === 'all') return 'All rounds';
  return ROUND_FILTER_OPTIONS.find((option) => option.value === stage)?.label || stage;
}

function matchDateKey(match: WorldCupMatch) {
  const parsed = new Date(match.kickoffUtc);
  if (!Number.isFinite(parsed.getTime())) return '';
  return parsed.toISOString().slice(0, 10);
}

function dateFilterLabel(dateKey: string) {
  if (!dateKey) return 'All dates';
  const parsed = new Date(`${dateKey}T00:00:00Z`);
  if (!Number.isFinite(parsed.getTime())) return dateKey;
  return new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', month: 'short', day: '2-digit' }).format(parsed);
}

function isPlaceholderTeam(team: string) {
  return /^(TBD|Winner|Loser|[A-H][1-4]|[A-L][123]|3rd|Third)/i.test(team.trim());
}

function atlasFiltersActive(filters: WorldCupAtlasFilters) {
  return filters.round !== 'all'
    || !!filters.date
    || !!filters.team
    || !!filters.cityId
    || filters.marketOnly
    || filters.weatherWatchOnly
    || filters.riskFlaggedOnly;
}

function matchPassesAtlasFilters(
  match: WorldCupMatch,
  filters: WorldCupAtlasFilters,
  signal: CitySignal | undefined,
) {
  if (filters.round !== 'all' && match.stage !== filters.round) return false;
  if (filters.date && matchDateKey(match) !== filters.date) return false;
  if (filters.team && match.homeTeam !== filters.team && match.awayTeam !== filters.team) return false;
  if (filters.cityId && match.cityId !== filters.cityId) return false;
  if (filters.marketOnly && !match.marketLinked) return false;
  if (filters.weatherWatchOnly && (!signal || signal.weatherRisk < 28)) return false;
  if (filters.riskFlaggedOnly && (!signal || cityRisk(signal).score < 48)) return false;
  return true;
}

function matchesForAtlasFilters(
  matches: WorldCupMatch[],
  filters: WorldCupAtlasFilters,
  citySignals: CitySignal[],
) {
  if (!atlasFiltersActive(filters)) return matches;
  const signalByCity = new Map(citySignals.map((signal) => [signal.city.id, signal]));
  return matches.filter((match) => matchPassesAtlasFilters(match, filters, signalByCity.get(match.cityId)));
}

function visibleCitySignalsForAtlasFilters(
  citySignals: CitySignal[],
  filteredMatches: WorldCupMatch[],
  filters: WorldCupAtlasFilters,
) {
  if (!atlasFiltersActive(filters)) return citySignals;
  const cityIds = new Set(filteredMatches.map((match) => match.cityId));
  citySignals.forEach((signal) => {
    const passesCityFilter = (!filters.cityId || signal.city.id === filters.cityId)
      && (!filters.weatherWatchOnly || signal.weatherRisk >= 28)
      && (!filters.riskFlaggedOnly || cityRisk(signal).score >= 48);
    if (passesCityFilter && (filters.weatherWatchOnly || filters.riskFlaggedOnly || signal.selected || signal.next)) {
      cityIds.add(signal.city.id);
    }
    if (signal.selected || signal.next) cityIds.add(signal.city.id);
  });
  const visible = citySignals.filter((signal) => cityIds.has(signal.city.id));
  return visible.length ? visible : citySignals;
}

function teamFilterOptions(matches: WorldCupMatch[]) {
  return Array.from(new Set(matches.flatMap((match) => [match.homeTeam, match.awayTeam]).filter((team) => team && !isPlaceholderTeam(team))))
    .sort((a, b) => a.localeCompare(b));
}

function dateFilterOptions(matches: WorldCupMatch[]) {
  return Array.from(new Set(matches.map(matchDateKey).filter(Boolean))).sort();
}

function escapeHtml(value: unknown) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[char] || char));
}

function numberValue(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function normalizeCountryName(value?: string | null) {
  return String(value || '').toLowerCase().replace(/[_-]+/g, ' ').replace(/[().]/g, '').replace(/\s+/g, ' ').trim();
}

function iso2ForConflictCountry(country?: string | null) {
  const normalized = normalizeCountryName(country);
  if (!normalized) return null;
  return COUNTRY_NAME_TO_ISO2.get(normalized) || COUNTRY_ISO2_ALIASES[normalized] || null;
}

function conflictTone(type?: string | number | null): ConflictSignal['tone'] {
  const normalized = String(type || '').trim();
  if (normalized === '1') return 'state';
  if (normalized === '2') return 'nonstate';
  if (normalized === '3') return 'onesided';
  return 'unknown';
}

function conflictViolenceLabel(type?: string | number | null) {
  const normalized = String(type || '').trim();
  if (normalized === '1') return 'STATE';
  if (normalized === '2') return 'NON-STATE';
  if (normalized === '3') return 'ONE-SIDED';
  return 'CONFLICT';
}

function riskLevel(score: number): WorldCupRiskLevel {
  if (score >= 72) return 'critical';
  if (score >= 46) return 'elevated';
  if (score >= 16) return 'watch';
  return 'quiet';
}

function riskColor(level: WorldCupRiskLevel) {
  if (level === 'critical') return '#ff3535';
  if (level === 'elevated') return '#ff7a1a';
  if (level === 'watch') return '#f4c400';
  return '#3b82f6';
}

function riskOpacity(level: WorldCupRiskLevel) {
  if (level === 'critical') return 0.43;
  if (level === 'elevated') return 0.32;
  if (level === 'watch') return 0.22;
  return 0.08;
}

function parseDateMs(value?: string | null) {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function shortDate(value?: string | null) {
  if (!value) return '--';
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return value.slice(0, 10);
  return parsed.toISOString().slice(0, 10);
}

function emptyCountryRisk(iso2: string, name: string): CountryConflictRisk {
  return {
    iso2,
    name,
    eventCount: 0,
    deaths: 0,
    stateCount: 0,
    nonStateCount: 0,
    oneSidedCount: 0,
    latestAt: null,
    score: 0,
    level: 'quiet',
    topActors: [],
    topLocations: [],
    conflicts: [],
  };
}

function weatherRiskScore(weather: WorldCupCityWeather | null) {
  if (!weather) return 0;
  const precip = weather.current.precipitationProbability || 0;
  const wind = weather.current.windKph || 0;
  const storm = /storm|rain|watch|humid/i.test(weather.current.condition) ? 18 : 0;
  return Math.min(100, precip + storm + Math.max(0, wind - 14));
}

function cityRole(signal: CitySignal) {
  if (FINAL_CITY_IDS.has(signal.city.id)) return 'Final City';
  if (OPENING_CITY_IDS.has(signal.city.id)) return 'Opening Match City';
  if (signal.matches.some((match) => match.stage !== 'group') || (KNOCKOUT_SLOT_COUNTS[signal.city.id] || 0) > 0) return 'Knockout Venue';
  return 'Group Stage Host';
}

function cityRoleShort(signal: CitySignal) {
  const role = cityRole(signal);
  if (role === 'Opening Match City') return 'OPENING';
  if (role === 'Final City') return 'FINAL';
  if (role === 'Knockout Venue') return 'KNOCKOUT';
  return 'HOST CITY';
}

function knockoutSlotCount(signal: CitySignal) {
  const seededKnockouts = signal.matches.filter((match) => match.stage !== 'group').length;
  return Math.max(seededKnockouts, KNOCKOUT_SLOT_COUNTS[signal.city.id] || 0);
}

function cityRisk(signal: CitySignal) {
  const score = Math.min(100, Math.round(
    24
    + signal.weatherRisk * 0.58
    + (signal.next ? 12 : 0)
    + (knockoutSlotCount(signal) > 0 ? 6 : 0)
    - Math.min(8, signal.marketCount * 1.5),
  ));
  const level = score >= 70 ? 'HIGH' : score >= 48 ? 'MED' : 'LOW';
  return { score, level };
}

function marketCoverage(signal: CitySignal) {
  const covered = Math.max(signal.marketCount, signal.oddsCount, signal.nextMatch ? 1 : 0);
  return `${Math.min(signal.plannedMatchCount, covered)}/${signal.plannedMatchCount}`;
}

function opsStatus(signal: CitySignal) {
  if (signal.weatherRisk >= 58) return 'Watch';
  if (signal.next) return 'Active prep';
  return 'Normal';
}

function weatherImpact(weather: WorldCupCityWeather | null) {
  const condition = weather?.current.condition || 'Forecast pending';
  const humid = /humid/i.test(condition);
  const storm = /storm|rain|watch/i.test(condition);
  return {
    pace: humid || storm ? 'Medium risk' : 'Normal',
    fatigue: humid ? 'High' : weather && weather.current.tempC >= 28 ? 'Medium' : 'Low',
    pitch: storm ? 'Watch' : 'Normal',
    totals: storm ? 'Under bias' : humid ? 'Slight under' : 'Neutral',
  };
}

function formatCompact(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '--';
  if (Math.abs(value) >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 1_000) return `$${(value / 1_000).toFixed(1)}K`;
  return `$${value.toFixed(0)}`;
}

function formatCount(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '--';
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(Math.round(value));
}

function probabilityWidth(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '0%';
  const percentage = value > 1 ? value : value * 100;
  return `${Math.max(3, Math.min(100, percentage))}%`;
}

function cityPolymarketMarkets(signal: CitySignal | null, marketGroups: MarketGroupItem[]) {
  if (!signal) return [];
  const linked = signal.matches.flatMap((match) => matchPolymarketMarkets(match, marketGroups));
  return linked.slice(0, 8);
}

function cityOddsSnapshots(signal: CitySignal | null, odds: WorldCupOddsSnapshot[]) {
  if (!signal) return [];
  const matchIds = new Set(signal.matches.map((match) => match.id));
  return odds.filter((snapshot) => matchIds.has(snapshot.matchId)).slice(0, 6);
}

function stageGroupLabel(match: WorldCupMatch) {
  return match.stage === 'group' ? 'Group Stage' : 'Knockout Stage';
}

function nextMatchRank(cityId: string, matches: WorldCupMatch[], nextMatch: WorldCupMatch | null) {
  if (cityId === nextMatch?.cityId) return 0;
  const upcoming = matches.filter((match) => match.status !== 'finished');
  const index = upcoming.findIndex((match) => match.cityId === cityId);
  return index >= 0 ? index + 1 : 999;
}

function offsetPoint(city: WorldCupVenueCity, index: number, radius = 0.33): [number, number] {
  const angle = (index * 137.5 * Math.PI) / 180;
  return [city.longitude + Math.cos(angle) * radius, city.latitude + Math.sin(angle) * radius * 0.72];
}

function buildCitySignals(
  cities: WorldCupVenueCity[],
  matches: WorldCupMatch[],
  weatherByCity: Map<string, WorldCupCityWeather>,
  nextMatch: WorldCupMatch | null,
  selectedMatchId: string | null,
  explicitSelectedCityId: string | null = null,
) {
  const selectedMatch = matches.find((match) => match.id === selectedMatchId) || null;
  const selectedFromMatch = selectedMatch?.cityId || null;
  const explicitMatchSelected = !!selectedMatch && selectedMatch.id !== nextMatch?.id;
  return cities.map((city) => {
    const cityMatches = matches.filter((match) => match.cityId === city.id);
    const cityNextMatch = cityMatches.find((match) => match.id === nextMatch?.id)
      || cityMatches.find((match) => match.status === 'scheduled')
      || null;
    const weather = weatherByCity.get(city.id) || null;
    return {
      type: 'host-city',
      city,
      weather,
      matches: cityMatches,
      nextMatch: cityNextMatch,
      selected: city.id === explicitSelectedCityId || (explicitMatchSelected && city.id === selectedFromMatch),
      next: city.id === nextMatch?.cityId,
      important: IMPORTANT_CITY_IDS.has(city.id) || nextMatchRank(city.id, matches, nextMatch) <= 4,
      plannedMatchCount: plannedMatchCount(city.id, matches),
      marketCount: cityMatches.filter((match) => match.marketLinked).length,
      oddsCount: cityMatches.filter((match) => match.oddsLinked).length,
      weatherRisk: weatherRiskScore(weather),
    } satisfies CitySignal;
  });
}

function buildDeckSignals(citySignals: CitySignal[], matches: WorldCupMatch[]) {
  const cityById = new Map(citySignals.map((signal) => [signal.city.id, signal.city]));
  const upcoming = matches.filter((match) => match.status !== 'finished').slice(0, 10);
  const schedulePaths: SchedulePath[] = [];
  for (let index = 1; index < upcoming.length; index += 1) {
    const previous = cityById.get(upcoming[index - 1]!.cityId);
    const current = cityById.get(upcoming[index]!.cityId);
    if (!previous || !current) continue;
    schedulePaths.push({
      type: 'schedule',
      id: `schedule-${upcoming[index]!.id}`,
      city: current,
      match: upcoming[index]!,
      path: [[previous.longitude, previous.latitude], [current.longitude, current.latitude]],
      selected: citySignals.find((signal) => signal.city.id === current.id)?.selected || false,
      next: index === 1,
    });
  }

  const weather: PointSignal[] = citySignals
    .filter((signal) => signal.weatherRisk >= 28)
    .map((signal, index) => {
      const [lon, lat] = offsetPoint(signal.city, index, 0.48);
      return {
        type: 'weather',
        id: `weather-${signal.city.id}`,
        city: signal.city,
        label: `${signal.weather?.current.condition || 'Weather risk'}`,
        sublabel: `${signal.weatherRisk.toFixed(0)} risk`,
        lon,
        lat,
        weight: Math.min(70, signal.weatherRisk),
      };
    });

  const markets: PointSignal[] = citySignals
    .filter((signal) => signal.marketCount > 0)
    .map((signal, index) => {
      const [lon, lat] = offsetPoint(signal.city, index + 2, 0.42);
      return {
        type: 'market',
        id: `market-${signal.city.id}`,
        city: signal.city,
        label: 'Polymarket markets',
        sublabel: `${signal.marketCount} linked`,
        lon,
        lat,
        weight: signal.marketCount,
      };
    });

  const odds: PointSignal[] = citySignals
    .filter((signal) => signal.oddsCount > 0)
    .map((signal, index) => {
      const [lon, lat] = offsetPoint(signal.city, index + 4, 0.35);
      return {
        type: 'odds',
        id: `odds-${signal.city.id}`,
        city: signal.city,
        label: 'Sportsbook odds',
        sublabel: `${signal.oddsCount} snapshots`,
        lon,
        lat,
        weight: signal.oddsCount,
      };
    });

  const transit: PointSignal[] = citySignals.map((signal, index) => {
    const [lon, lat] = offsetPoint(signal.city, index + 7, 0.26);
    return {
      type: 'transit',
      id: `transit-${signal.city.id}`,
      city: signal.city,
      label: 'Airport / transit',
      sublabel: signal.city.country,
      lon,
      lat,
      weight: 1,
    };
  });

  const teams: PointSignal[] = citySignals.flatMap((signal, index) => {
    const match = signal.nextMatch;
    if (!match) return [];
    const home = offsetPoint(signal.city, index + 11, 0.22);
    const away = offsetPoint(signal.city, index + 13, 0.22);
    return [
      { type: 'team', id: `team-home-${match.id}`, city: signal.city, label: match.homeTeam, sublabel: 'team base', lon: home[0], lat: home[1], weight: 1 },
      { type: 'team', id: `team-away-${match.id}`, city: signal.city, label: match.awayTeam, sublabel: 'team base', lon: away[0], lat: away[1], weight: 1 },
    ] satisfies PointSignal[];
  });

  return { schedulePaths, weather, markets, odds, transit, teams };
}

function conflictColor(item: RuntimeGeoSanctionsShockItem): [number, number, number, number] {
  const type = String(item.violenceType || '').trim();
  if (type === '1') return COLORS.conflictState;
  if (type === '2') return COLORS.conflictNonState;
  if (type === '3') return COLORS.conflictOneSided;
  return String(item.severity || '').toLowerCase() === 'critical' ? COLORS.conflictState : COLORS.conflictNonState;
}

function buildConflictSignals(items: RuntimeGeoSanctionsShockItem[]): ConflictSignal[] {
  return items.slice(0, 1200).flatMap((item, index): ConflictSignal[] => {
    const lat = numberValue(item.latitude);
    const lon = numberValue(item.longitude);
    if (lat == null || lon == null || lat < -90 || lat > 90 || lon < -180 || lon > 180) return [];
    const deaths = Math.max(0, numberValue(item.deathsBest) ?? 0);
    const country = String(item.country || item.locationLabel || 'UCDP');
    const sideA = String(item.sideA || '').trim();
    const sideB = String(item.sideB || '').trim();
    const actors = [sideA, sideB].filter(Boolean).join(' vs ');
    const color = conflictColor(item);
    const tone = conflictTone(item.violenceType);
    return [{
      type: 'conflict',
      id: String(item.id || `ucdp-${index}`),
      lon,
      lat,
      iso2: iso2ForConflictCountry(country),
      country,
      location: String(item.locationLabel || country),
      label: country,
      sublabel: `${deaths} deaths${actors ? ` · ${actors}` : ''}`,
      actors,
      sideA,
      sideB,
      deaths,
      deathsLow: numberValue(item.deathsLow),
      deathsHigh: numberValue(item.deathsHigh),
      violenceType: String(item.violenceType || ''),
      violenceLabel: conflictViolenceLabel(item.violenceType),
      occurredAt: item.occurredAt ? String(item.occurredAt) : null,
      source: item.source ? String(item.source) : null,
      sourceUrl: item.sourceUrl ? String(item.sourceUrl) : null,
      tone,
      color,
      ringColor: [color[0], color[1], color[2], 48],
    }];
  });
}

function buildCountryConflictRisks(conflicts: ConflictSignal[]): CountryConflictRisk[] {
  const groups = new Map<string, CountryConflictRisk>();
  conflicts.forEach((conflict) => {
    if (!conflict.iso2) return;
    const existing = groups.get(conflict.iso2) || emptyCountryRisk(conflict.iso2, conflict.country);
    existing.conflicts.push(conflict);
    existing.eventCount += 1;
    existing.deaths += conflict.deaths;
    if (conflict.tone === 'state') existing.stateCount += 1;
    if (conflict.tone === 'nonstate') existing.nonStateCount += 1;
    if (conflict.tone === 'onesided') existing.oneSidedCount += 1;
    if (!existing.latestAt || parseDateMs(conflict.occurredAt) > parseDateMs(existing.latestAt)) {
      existing.latestAt = conflict.occurredAt;
    }
    groups.set(conflict.iso2, existing);
  });

  return Array.from(groups.values()).map((risk) => {
    const actorCounts = new Map<string, number>();
    const locationCounts = new Map<string, number>();
    risk.conflicts.forEach((conflict) => {
      if (conflict.actors) actorCounts.set(conflict.actors, (actorCounts.get(conflict.actors) || 0) + 1);
      if (conflict.location) locationCounts.set(conflict.location, (locationCounts.get(conflict.location) || 0) + 1);
    });
    const score = Math.min(100, Math.round(
      Math.log10(risk.deaths + 1) * 24
      + Math.min(30, risk.eventCount * 1.65)
      + risk.stateCount * 2.2
      + risk.oneSidedCount * 1.4,
    ));
    return {
      ...risk,
      score,
      level: riskLevel(score),
      conflicts: risk.conflicts.slice().sort((a, b) => parseDateMs(b.occurredAt) - parseDateMs(a.occurredAt)),
      topActors: Array.from(actorCounts.entries()).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([actor]) => actor),
      topLocations: Array.from(locationCounts.entries()).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([location]) => location),
    };
  }).sort((a, b) => b.score - a.score || b.deaths - a.deaths || b.eventCount - a.eventCount);
}

function getActiveSignal(citySignals: CitySignal[], selectedCityId: string | null, selectedMatchId: string | null, matches: WorldCupMatch[], nextMatch: WorldCupMatch | null) {
  const selectedMatch = matches.find((match) => match.id === selectedMatchId) || null;
  return citySignals.find((signal) => signal.city.id === selectedCityId)
    || citySignals.find((signal) => signal.city.id === selectedMatch?.cityId)
    || citySignals.find((signal) => signal.city.id === nextMatch?.cityId)
    || citySignals[0]
    || null;
}

function selectedCountryCode(
  cities: WorldCupVenueCity[],
  matches: WorldCupMatch[],
  selectedCityId: string | null,
  selectedMatchId: string | null,
  nextMatch: WorldCupMatch | null,
  explicitSelectedCityId: string | null,
) {
  const selectedMatch = selectedMatchId ? matches.find((match) => match.id === selectedMatchId) : null;
  if (selectedMatch) {
    const implicitNextMatch = selectedMatch.id === nextMatch?.id
      && selectedMatch.cityId === nextMatch.cityId
      && selectedCityId === nextMatch.cityId
      && explicitSelectedCityId !== selectedMatch.cityId;
    if (implicitNextMatch) return null;
    return cities.find((city) => city.id === selectedMatch.cityId)?.country || null;
  }
  const selectedCity = selectedCityId ? cities.find((city) => city.id === selectedCityId) : null;
  if (!selectedCity) return null;
  if (!nextMatch && selectedCity.id !== explicitSelectedCityId) return null;
  if (selectedCity.id === nextMatch?.cityId && selectedCity.id !== explicitSelectedCityId) return null;
  return selectedCity.country;
}

function buildDeckLayers(
  citySignals: CitySignal[],
  signals: ReturnType<typeof buildDeckSignals>,
  conflicts: ConflictSignal[],
  enabledLayers: EnabledLayers,
  zoom: number,
) {
  const layers = [];

  if (enabledLayers.conflicts) {
    layers.push(new ScatterplotLayer<ConflictSignal>({
      id: 'wc-ucdp-conflict-ring-layer',
      data: conflicts.filter((signal) => signal.deaths >= 20).slice(0, enabledLayers.globalRisk ? 320 : 120),
      getPosition: (d) => [d.lon, d.lat],
      getRadius: (d) => 15000 + Math.min(96000, Math.log10(d.deaths + 1) * 25000),
      getFillColor: (d) => [d.color[0], d.color[1], d.color[2], 34],
      getLineColor: (d) => [d.color[0], d.color[1], d.color[2], 90],
      radiusMinPixels: 8,
      radiusMaxPixels: 26,
      lineWidthMinPixels: 1,
      stroked: true,
      pickable: false,
    }));
    layers.push(new ScatterplotLayer<ConflictSignal>({
      id: 'wc-ucdp-conflict-layer',
      data: conflicts,
      getPosition: (d) => [d.lon, d.lat],
      getRadius: (d) => 5600 + Math.min(54000, Math.log10(d.deaths + 1) * 12500),
      getFillColor: (d) => [d.color[0], d.color[1], d.color[2], Math.min(d.color[3], enabledLayers.globalRisk ? 150 : 118)],
      getLineColor: [255, 245, 190, 130],
      radiusMinPixels: 3,
      radiusMaxPixels: enabledLayers.globalRisk ? 16 : 12,
      lineWidthMinPixels: 0.75,
      stroked: true,
      pickable: true,
    }));
  }

  if (enabledLayers.schedule) {
    layers.push(new PathLayer<SchedulePath>({
      id: 'wc-schedule-route-layer',
      data: signals.schedulePaths,
      getPath: (d) => d.path,
      getColor: (d) => (d.selected || d.next ? [255, 190, 72, 128] : [224, 180, 102, 38]),
      getWidth: (d) => (d.selected || d.next ? 1.35 : 0.58),
      widthMinPixels: 1,
      widthMaxPixels: 2.4,
      pickable: true,
    }));
  }

  if (enabledLayers.weather) {
    layers.push(new ScatterplotLayer<CitySignal>({
      id: 'wc-city-weather-risk-ring-layer',
      data: citySignals.filter((signal) => signal.weatherRisk >= 28),
      getPosition: (d) => [d.city.longitude, d.city.latitude],
      getRadius: (d) => 19000 + d.weatherRisk * 420,
      getFillColor: [55, 175, 220, 10],
      getLineColor: [75, 210, 255, 92],
      radiusMinPixels: 9,
      radiusMaxPixels: 27,
      lineWidthMinPixels: 1,
      stroked: true,
      pickable: false,
    }));
    layers.push(new ScatterplotLayer<PointSignal>({
      id: 'wc-weather-risk-layer',
      data: signals.weather,
      getPosition: (d) => [d.lon, d.lat],
      getRadius: (d) => 16000 + d.weight * 500,
      getFillColor: COLORS.weather,
      getLineColor: [72, 210, 255, 92],
      lineWidthMinPixels: 0.5,
      radiusMinPixels: 3,
      radiusMaxPixels: 12,
      stroked: true,
      pickable: true,
    }));
  }

  if (enabledLayers.markets) {
    layers.push(new ScatterplotLayer<PointSignal>({
      id: 'wc-polymarket-layer',
      data: signals.markets,
      getPosition: (d) => [d.lon, d.lat],
      getRadius: (d) => 6500 + d.weight * 2400,
      getFillColor: COLORS.market,
      radiusMinPixels: 2,
      radiusMaxPixels: 6,
      pickable: true,
    }));
  }

  if (enabledLayers.odds) {
    layers.push(new ScatterplotLayer<PointSignal>({
      id: 'wc-odds-layer',
      data: signals.odds,
      getPosition: (d) => [d.lon, d.lat],
      getRadius: (d) => 6500 + d.weight * 2100,
      getFillColor: COLORS.odds,
      radiusMinPixels: 2,
      radiusMaxPixels: 7,
      pickable: true,
    }));
  }

  if (enabledLayers.transit) {
    layers.push(new ScatterplotLayer<PointSignal>({
      id: 'wc-transit-layer',
      data: signals.transit,
      getPosition: (d) => [d.lon, d.lat],
      getRadius: 7000,
      getFillColor: COLORS.transit,
      radiusMinPixels: 2,
      radiusMaxPixels: 6,
      pickable: true,
    }));
  }

  if (enabledLayers.teams) {
    layers.push(new ScatterplotLayer<PointSignal>({
      id: 'wc-team-base-layer',
      data: signals.teams,
      getPosition: (d) => [d.lon, d.lat],
      getRadius: 6400,
      getFillColor: COLORS.team,
      radiusMinPixels: 2,
      radiusMaxPixels: 6,
      pickable: true,
    }));
  }

  if (enabledLayers.cities) {
    layers.push(new ScatterplotLayer<CitySignal>({
      id: 'wc-knockout-venue-ring-layer',
      data: citySignals.filter((signal) => knockoutSlotCount(signal) > 0 && !signal.next && !signal.selected),
      getPosition: (d) => [d.city.longitude, d.city.latitude],
      getRadius: (d) => FINAL_CITY_IDS.has(d.city.id) ? 42000 : 28000,
      getFillColor: [255, 198, 94, 8],
      getLineColor: (d) => FINAL_CITY_IDS.has(d.city.id) ? [255, 220, 142, 152] : [255, 201, 96, 78],
      radiusMinPixels: 9,
      radiusMaxPixels: 22,
      lineWidthMinPixels: 0.85,
      stroked: true,
      pickable: false,
    }));
    layers.push(new ScatterplotLayer<CitySignal>({
      id: 'wc-host-city-halo-layer',
      data: citySignals.filter((signal) => signal.selected || signal.next || signal.important),
      getPosition: (d) => [d.city.longitude, d.city.latitude],
      getRadius: (d) => d.selected ? 44000 : d.next ? 52000 : FINAL_CITY_IDS.has(d.city.id) ? 38000 : 21000,
      getFillColor: (d) => d.selected ? COLORS.selectedDim : d.next ? COLORS.nextDim : FINAL_CITY_IDS.has(d.city.id) ? [255, 203, 94, 18] : [255, 255, 255, 13],
      getLineColor: (d) => d.selected ? COLORS.selected : d.next ? COLORS.next : FINAL_CITY_IDS.has(d.city.id) ? [255, 224, 148, 128] : [255, 255, 255, 40],
      radiusMinPixels: 8,
      radiusMaxPixels: 28,
      lineWidthMinPixels: 1,
      stroked: true,
      pickable: false,
    }));
    layers.push(new ScatterplotLayer<CitySignal>({
      id: 'wc-host-city-layer',
      data: citySignals,
      getPosition: (d) => [d.city.longitude, d.city.latitude],
      getRadius: (d) => {
        if (d.next) return 30500;
        if (d.selected) return 27000;
        if (FINAL_CITY_IDS.has(d.city.id)) return 24500;
        if (knockoutSlotCount(d) > 0) return 16500 + d.plannedMatchCount * 1050;
        return 10500 + d.plannedMatchCount * 1050;
      },
      getFillColor: (d) => d.selected ? COLORS.selected : d.next ? COLORS.next : FINAL_CITY_IDS.has(d.city.id) ? [255, 241, 216, 238] : COLORS.city,
      getLineColor: (d) => d.next ? [255, 244, 210, 245] : d.selected ? [228, 252, 255, 244] : knockoutSlotCount(d) > 0 ? [255, 214, 139, 188] : COLORS.cityLine,
      radiusMinPixels: 6,
      radiusMaxPixels: 15,
      lineWidthMinPixels: 1.35,
      stroked: true,
      pickable: true,
    }));
    layers.push(new TextLayer<CitySignal>({
      id: 'wc-host-city-label-layer',
      data: visibleCityLabels(citySignals, zoom),
      getPosition: (d) => [d.city.longitude, d.city.latitude],
      getText: (d) => hostCityLabel(d, zoom),
      getSize: (d) => d.selected || d.next ? 13 : d.important ? 10.8 : 9.6,
      getColor: (d) => d.next ? [255, 235, 188, 246] : d.selected ? [225, 250, 255, 244] : [230, 234, 234, 205],
      getTextAnchor: 'start',
      getAlignmentBaseline: 'center',
      getPixelOffset: (d) => CITY_LABEL_OFFSETS[d.city.id] || [14, 4],
      fontFamily: '"SF Mono", "Monaco", "Cascadia Code", "Fira Code", "DejaVu Sans Mono", "Liberation Mono", monospace',
      fontWeight: 900,
      lineHeight: 0.96,
      background: false,
      pickable: false,
    }));
  }

  return layers;
}

function getDeckTooltip(info: PickingInfo<DeckObject>) {
  if (!info.object) return null;
  const obj = info.object as any;
  const iso2 = String(obj.properties?.['ISO3166-1-Alpha-2'] || '');
  if (iso2) {
    return {
      html: `<div class="deckgl-tooltip"><strong>${escapeHtml(obj.properties?.name || iso2)}</strong><br/>${escapeHtml(iso2)} country layer</div>`,
    };
  }
  if (obj.type === 'host-city') {
    return {
      html: `<div class="deckgl-tooltip"><strong>${escapeHtml(obj.city.city)}</strong><br/>${escapeHtml(obj.city.venue)}<br/>${obj.plannedMatchCount} matches · ${escapeHtml(obj.weather?.current.condition || 'weather pending')}</div>`,
    };
  }
  if (obj.type === 'schedule') {
    return {
      html: `<div class="deckgl-tooltip"><strong>${escapeHtml(matchTitle(obj.match))}</strong><br/>${escapeHtml(obj.city.city)} · ${escapeHtml(shortKickoff(obj.match))}</div>`,
    };
  }
  if (obj.type === 'conflict') {
    return {
      html: `<div class="deckgl-tooltip"><strong>${escapeHtml(obj.label)}</strong><br/>${escapeHtml(obj.sublabel)}<br/>UCDP event</div>`,
    };
  }
  return {
    html: `<div class="deckgl-tooltip"><strong>${escapeHtml(obj.label)}</strong><br/>${escapeHtml(obj.city.city)} · ${escapeHtml(obj.sublabel)}</div>`,
  };
}

function RiskMetricBar({ label, value, max, tone }: { label: string; value: number; max: number; tone: string }) {
  const width = max > 0 ? Math.max(4, Math.round((value / max) * 100)) : 0;
  return (
    <p className={`wm-worldcup-risk-bar ${tone}`}>
      <span>{label}</span>
      <i><b style={{ width: `${width}%` }} /></i>
      <strong>{value}</strong>
    </p>
  );
}

function MapRiskIntelPanel({ intel, onClose }: { intel: SelectedRiskIntel; onClose: () => void }) {
  if (intel.kind === 'conflict') {
    const conflict = intel.conflict;
    return (
      <aside className={`wm-worldcup-risk-panel conflict tone-${conflict.tone}`}>
        <button type="button" onClick={onClose} aria-label="Close risk detail">×</button>
        <header>
          <span>{conflict.violenceLabel}</span>
          <strong>{conflict.country}</strong>
          <em>{shortDate(conflict.occurredAt)} · {conflict.source || 'UCDP'}</em>
        </header>
        <div className="wm-worldcup-risk-scoreline">
          <b>{formatCount(conflict.deaths)}</b>
          <small>deaths</small>
        </div>
        <section className="wm-worldcup-risk-details">
          <span>LOCATION</span>
          <strong>{conflict.location || conflict.country}</strong>
          <span>ACTORS</span>
          <strong>{conflict.actors || '--'}</strong>
          {conflict.deathsLow != null || conflict.deathsHigh != null ? (
            <>
              <span>RANGE</span>
              <strong>{conflict.deathsLow ?? '--'} - {conflict.deathsHigh ?? '--'}</strong>
            </>
          ) : null}
        </section>
        {conflict.sourceUrl ? <a href={conflict.sourceUrl} target="_blank" rel="noreferrer">OPEN SOURCE</a> : null}
      </aside>
    );
  }

  const risk = intel.risk;
  const max = Math.max(1, risk.stateCount, risk.nonStateCount, risk.oneSidedCount);
  return (
    <aside className={`wm-worldcup-risk-panel country level-${risk.level}`}>
      <button type="button" onClick={onClose} aria-label="Close country risk detail">×</button>
      <header>
        <span>{risk.iso2}</span>
        <strong>{risk.name}</strong>
        <em>Updated {shortDate(risk.latestAt)}</em>
      </header>
      <div className="wm-worldcup-risk-scoreline">
        <b>{risk.score}/100</b>
        <small>{risk.level === 'quiet' ? 'stable' : risk.level}</small>
      </div>
      <div className="wm-worldcup-risk-bars">
        <RiskMetricBar label="State" value={risk.stateCount} max={max} tone="state" />
        <RiskMetricBar label="Non-state" value={risk.nonStateCount} max={max} tone="nonstate" />
        <RiskMetricBar label="One-sided" value={risk.oneSidedCount} max={max} tone="onesided" />
      </div>
      <div className="wm-worldcup-risk-stats">
        <span><b>{formatCount(risk.eventCount)}</b><em>events</em></span>
        <span><b>{formatCount(risk.deaths)}</b><em>deaths</em></span>
        <span><b>{formatCount(risk.conflicts.length)}</b><em>rows</em></span>
      </div>
      {risk.topActors.length ? (
        <section className="wm-worldcup-risk-details compact">
          <span>TOP ACTORS</span>
          {risk.topActors.map((actor) => <strong key={actor}>{actor}</strong>)}
        </section>
      ) : null}
      {risk.topLocations.length ? (
        <section className="wm-worldcup-risk-details compact">
          <span>HOTSPOTS</span>
          {risk.topLocations.map((location) => <strong key={location}>{location}</strong>)}
        </section>
      ) : null}
    </aside>
  );
}

function EventControlPanel({
  enabledLayers,
  onToggle,
  filters,
  onFiltersChange,
  onClearFilters,
  dateOptions,
  teamOptions,
  cityOptions,
  filteredMatchCount,
  filteredVenueCount,
  summary,
}: {
  enabledLayers: EnabledLayers;
  onToggle: (key: WorldCupLayerKey) => void;
  filters: WorldCupAtlasFilters;
  onFiltersChange: (patch: Partial<WorldCupAtlasFilters>) => void;
  onClearFilters: () => void;
  dateOptions: string[];
  teamOptions: string[];
  cityOptions: WorldCupVenueCity[];
  filteredMatchCount: number;
  filteredVenueCount: number;
  summary: string;
}) {
  const overlayRows: Array<{
    key: WorldCupLayerKey;
    icon: string;
    label: string;
    status: string;
    sourcePending?: boolean;
    tone: 'core' | 'match' | 'weather' | 'market' | 'risk' | 'ops';
  }> = [
    { key: 'schedule', icon: '↔', label: 'Match paths', status: `${filteredMatchCount} fixtures`, tone: 'match' },
    { key: 'cities', icon: '◎', label: 'Venues', status: `${filteredVenueCount} hosts`, tone: 'core' },
    { key: 'weather', icon: '☂', label: 'Weather watch', status: 'Forecast', tone: 'weather' },
    { key: 'transit', icon: '✈', label: 'Travel / transit', status: 'Source pending', sourcePending: true, tone: 'ops' },
    { key: 'markets', icon: '$', label: 'Polymarket markets', status: 'Linked only', tone: 'market' },
    { key: 'conflicts', icon: '!', label: 'Hazards / security', status: enabledLayers.globalRisk ? 'Global' : 'Host region', tone: 'risk' },
    { key: 'odds', icon: '%', label: 'Sportsbook odds', status: 'Optional', tone: 'market' },
    { key: 'globalRisk', icon: '⌖', label: 'Global risk scope', status: enabledLayers.globalRisk ? 'On' : 'Off', tone: 'risk' },
  ];
  const [collapsed, setCollapsed] = useState(() => {
    if (typeof window === 'undefined') return false;
    return window.localStorage.getItem(WORLD_CUP_LAYER_PANEL_COLLAPSED_STORAGE_KEY) === '1';
  });
  const toggleCollapsed = () => {
    setCollapsed((current) => {
      const next = !current;
      if (typeof window !== 'undefined') {
        window.localStorage.setItem(WORLD_CUP_LAYER_PANEL_COLLAPSED_STORAGE_KEY, next ? '1' : '0');
      }
      return next;
    });
  };

  return (
    <aside
      className={`wm-worldcup-map-event-panel deckgl-layer-toggles ${collapsed ? 'collapsed' : ''}`}
      data-filter-active={atlasFiltersActive(filters) ? 'true' : 'false'}
      data-summary={summary}
    >
      <div className="wm-worldcup-map-event-head toggle-header">
        <span>HOST ATLAS</span>
        <button type="button" className="layer-help-btn" aria-label="Atlas filters help">?</button>
        <button type="button" className="toggle-collapse" aria-label={collapsed ? 'Expand atlas filters' : 'Collapse atlas filters'} onClick={toggleCollapsed}>
          {collapsed ? '▶' : '▼'}
        </button>
      </div>
      {!collapsed ? (
        <div className="wm-worldcup-map-event-body toggle-list">
          <section className="wm-worldcup-atlas-section primary">
            <div className="wm-worldcup-atlas-section-head">
              <span>Filters</span>
              {atlasFiltersActive(filters) ? <button type="button" onClick={onClearFilters}>Clear</button> : null}
            </div>
            <div className="wm-worldcup-round-grid">
              {ROUND_FILTER_OPTIONS.map((option) => (
                <button
                  type="button"
                  className={filters.round === option.value ? 'active' : ''}
                  key={option.value}
                  title={option.label}
                  onClick={() => onFiltersChange({ round: option.value })}
                >
                  {option.short}
                </button>
              ))}
            </div>
            <label className="wm-worldcup-atlas-select">
              <span>Date</span>
              <select value={filters.date} onChange={(event) => onFiltersChange({ date: event.currentTarget.value })}>
                <option value="">All dates</option>
                {dateOptions.map((date) => <option value={date} key={date}>{dateFilterLabel(date)}</option>)}
              </select>
            </label>
            <label className="wm-worldcup-atlas-select">
              <span>Team</span>
              <select value={filters.team} onChange={(event) => onFiltersChange({ team: event.currentTarget.value })}>
                <option value="">All teams</option>
                {teamOptions.map((team) => <option value={team} key={team}>{team}</option>)}
              </select>
            </label>
            <label className="wm-worldcup-atlas-select">
              <span>Host City</span>
              <select value={filters.cityId} onChange={(event) => onFiltersChange({ cityId: event.currentTarget.value })}>
                <option value="">All host cities</option>
                {cityOptions.map((city) => <option value={city.id} key={city.id}>{shortCityName(city)}</option>)}
              </select>
            </label>
            <div className="wm-worldcup-atlas-toggles">
              {([
                ['marketOnly', 'Market-linked only'],
                ['weatherWatchOnly', 'Weather watch only'],
                ['riskFlaggedOnly', 'Risk flagged only'],
              ] as Array<[keyof Pick<WorldCupAtlasFilters, 'marketOnly' | 'weatherWatchOnly' | 'riskFlaggedOnly'>, string]>).map(([key, label]) => (
                <label key={key}>
                  <input
                    type="checkbox"
                    checked={filters[key]}
                    onChange={() => onFiltersChange({ [key]: !filters[key] })}
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          </section>
          <section className="wm-worldcup-atlas-section overlays">
            <div className="wm-worldcup-atlas-section-head">
              <span>Optional overlays</span>
              <b>{Object.values(enabledLayers).filter(Boolean).length}</b>
            </div>
            <div className="wm-worldcup-overlay-list">
              {overlayRows.map((row) => (
                <label
                  className={`wm-worldcup-map-layer-row layer-toggle ${enabledLayers[row.key] ? 'active has-data' : ''} ${row.sourcePending ? 'source-pending' : ''}`}
                  data-layer-key={row.key}
                  data-layer-tone={row.tone}
                  key={row.key}
                  title={row.status}
                >
                  <input type="checkbox" checked={enabledLayers[row.key]} onChange={() => onToggle(row.key)} />
                  <span className="toggle-icon">{row.icon}</span>
                  <span className="toggle-label">{row.label}</span>
                  <span className="toggle-status">{row.status}</span>
                </label>
              ))}
            </div>
          </section>
          <footer className="map-author-badge">
            <span>{filteredVenueCount} venues · {filteredMatchCount} matches</span>
            <em>{stageFilterLabel(filters.round)}</em>
          </footer>
        </div>
      ) : null}
    </aside>
  );
}

function MapControls({ map, onPreset }: { map: MapLibreMap | null; onPreset: (preset: WorldCupViewportPreset) => void }) {
  return (
    <div className="wm-worldcup-map-controls">
      <button type="button" onClick={() => map?.zoomIn()} aria-label="Zoom in">+</button>
      <button type="button" onClick={() => map?.zoomOut()} aria-label="Zoom out">−</button>
      <button type="button" onClick={() => onPreset('north-america')} aria-label="North America hosts">NA</button>
      <button type="button" onClick={() => onPreset('usa')} aria-label="USA hosts">US</button>
      <button type="button" onClick={() => onPreset('mexico')} aria-label="Mexico hosts">MX</button>
      <button type="button" onClick={() => onPreset('canada')} aria-label="Canada hosts">CA</button>
      <button type="button" onClick={() => onPreset('next')} aria-label="Zoom to next match">NX</button>
      <button type="button" onClick={() => onPreset('global-risk')} aria-label="Global risk view">GR</button>
      <button type="button" onClick={() => map?.easeTo({ center: WORLDCUP_ATLAS_CENTER, zoom: WORLDCUP_ATLAS_ZOOM, pitch: 0, bearing: 0 })} aria-label="Reset view">⌂</button>
    </div>
  );
}

export function WorldCupMap({
  cities,
  matches,
  weather,
  marketGroups,
  odds,
  conflicts,
  nextMatch,
  selectedCityId,
  selectedMatchId,
  onSelectCity,
  onSelectMatch,
}: WorldCupMapProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const mapHostRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const deckOverlayRef = useRef<MapboxOverlay | null>(null);
  const pulseRafRef = useRef<number | null>(null);
  const nextPulseRafRef = useRef<number | null>(null);
  const styleTimeoutRef = useRef<number | null>(null);
  const explicitSelectedCityRef = useRef<string | null>(null);
  const fallbackAppliedRef = useRef(false);
  const initialViewportAppliedRef = useRef(false);
  const dataRef = useRef({ cities, matches, weather, conflicts, nextMatch, selectedCityId, selectedMatchId });
  const countryRisksRef = useRef<CountryConflictRisk[]>([]);
  const countryRiskByIsoRef = useRef<Map<string, CountryConflictRisk>>(new Map());
  const onCountrySelectRef = useRef<(risk: CountryConflictRisk) => void>(() => undefined);
  const enabledLayersRef = useRef(DEFAULT_ENABLED_LAYERS);
  const atlasFiltersRef = useRef(DEFAULT_ATLAS_FILTERS);
  const [enabledLayers, setEnabledLayers] = useState<EnabledLayers>(DEFAULT_ENABLED_LAYERS);
  const [atlasFilters, setAtlasFilters] = useState<WorldCupAtlasFilters>(DEFAULT_ATLAS_FILTERS);
  const [expandedMatchId, setExpandedMatchId] = useState<string | null>(selectedMatchId || nextMatch?.id || null);
  const [mapReady, setMapReady] = useState(false);
  const [mapDegraded, setMapDegraded] = useState(false);
  const [regionHover, setRegionHover] = useState<MapRegionHover | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [selectedRiskIntel, setSelectedRiskIntel] = useState<SelectedRiskIntel | null>(null);

  const weatherByCity = useMemo(() => {
    const index = new Map<string, WorldCupCityWeather>();
    weather.forEach((item) => index.set(item.cityId, item));
    return index;
  }, [weather]);

  const citySignals = useMemo(
    () => buildCitySignals(cities, matches, weatherByCity, nextMatch, selectedMatchId, explicitSelectedCityRef.current),
    [cities, matches, nextMatch, selectedCityId, selectedMatchId, weatherByCity],
  );
  const conflictSignals = useMemo(() => buildConflictSignals(conflicts), [conflicts]);
  const countryRisks = useMemo(() => buildCountryConflictRisks(conflictSignals), [conflictSignals]);
  const countryRiskByIso = useMemo(() => new Map(countryRisks.map((risk) => [risk.iso2, risk])), [countryRisks]);
  const visibleConflictSignals = useMemo(() => visibleConflictsForLayers(conflictSignals, enabledLayers), [conflictSignals, enabledLayers]);
  const visibleCountryRisks = useMemo(() => visibleCountryRisksForLayers(countryRisks, enabledLayers), [countryRisks, enabledLayers]);
  const filteredMatches = useMemo(() => matchesForAtlasFilters(matches, atlasFilters, citySignals), [atlasFilters, citySignals, matches]);
  const visibleCitySignals = useMemo(
    () => visibleCitySignalsForAtlasFilters(citySignals, filteredMatches, atlasFilters),
    [atlasFilters, citySignals, filteredMatches],
  );
  const activeSignal = getActiveSignal(citySignals, selectedCityId, selectedMatchId, matches, nextMatch);
  const activeMatches = activeSignal?.matches || [];
  const activeFilteredMatches = activeSignal
    ? activeMatches.filter((match) => filteredMatches.some((filtered) => filtered.id === match.id))
    : [];
  const displayedActiveMatches = activeFilteredMatches.length || atlasFiltersActive(atlasFilters) ? activeFilteredMatches : activeMatches;
  const activeRisk = activeSignal ? cityRisk(activeSignal) : null;
  const activeImpact = activeSignal ? weatherImpact(activeSignal.weather) : null;
  const activeMarkets = useMemo(() => cityPolymarketMarkets(activeSignal, marketGroups), [activeSignal, marketGroups]);
  const activeOdds = useMemo(() => cityOddsSnapshots(activeSignal, odds), [activeSignal, odds]);
  const activeMatchSlots = useMemo<Array<{ type: 'match'; match: WorldCupMatch; key: string }>>(() => {
    if (!activeSignal) return [];
    const seeded = displayedActiveMatches.map((match) => ({ type: 'match' as const, match, key: match.id }));
    return seeded;
  }, [activeSignal, displayedActiveMatches]);
  const nextCityMatch = activeSignal?.nextMatch || null;
  const activeSlotGroups = useMemo(() => {
    if (!activeSignal) return [] as Array<[string, typeof activeMatchSlots]>;
    const groups = new Map<string, typeof activeMatchSlots>();
    activeMatchSlots.forEach((slot) => {
      const label = stageGroupLabel(slot.match);
      groups.set(label, [...(groups.get(label) || []), slot]);
    });
    return Array.from(groups.entries());
  }, [activeMatchSlots, activeSignal]);
  const mapSummary = useMemo(() => {
    const enabledCount = Object.values(enabledLayers).filter(Boolean).length;
    const visibleMatches = filteredMatches.length;
    const riskScope = enabledLayers.globalRisk ? 'GLOBAL RISK' : 'HOST REGION';
    return `Showing ${visibleCitySignals.length} venues · ${visibleMatches} matches · ${visibleConflictSignals.length} ${riskScope} conflicts · ${enabledCount} overlays`;
  }, [enabledLayers, filteredMatches.length, visibleCitySignals.length, visibleConflictSignals.length]);
  const teamOptions = useMemo(() => teamFilterOptions(matches), [matches]);
  const dateOptions = useMemo(() => dateFilterOptions(matches), [matches]);

  const applyCountryRiskPaintForLayers = (layers = enabledLayersRef.current) => {
    updateCountryRiskPaint(mapRef.current, visibleCountryRisksForLayers(countryRisksRef.current, layers));
  };

  const updateDeckLayers = () => {
    const map = mapRef.current;
    const overlay = deckOverlayRef.current;
    if (!map || !overlay) return;
    const current = dataRef.current;
    const weatherIndex = new Map<string, WorldCupCityWeather>();
    current.weather.forEach((item) => weatherIndex.set(item.cityId, item));
    const currentCitySignals = buildCitySignals(
      current.cities,
      current.matches,
      weatherIndex,
      current.nextMatch,
      current.selectedMatchId,
      explicitSelectedCityRef.current,
    );
    const currentFilteredMatches = matchesForAtlasFilters(current.matches, atlasFiltersRef.current, currentCitySignals);
    const currentVisibleCitySignals = visibleCitySignalsForAtlasFilters(currentCitySignals, currentFilteredMatches, atlasFiltersRef.current);
    overlay.setProps({
      layers: buildDeckLayers(
        currentVisibleCitySignals,
        buildDeckSignals(currentVisibleCitySignals, currentFilteredMatches),
        visibleConflictsForLayers(buildConflictSignals(current.conflicts), enabledLayersRef.current),
        enabledLayersRef.current,
        map.getZoom(),
      ),
    });
  };

  const focusViewport = (preset: WorldCupViewportPreset) => {
    const map = mapRef.current;
    if (!map) return;
    if (preset === 'global-risk') {
      map.easeTo({ center: [18, 22], zoom: 1.72, duration: 420, bearing: 0, pitch: 0 });
      return;
    }
    const current = dataRef.current;
    const nextCity = current.nextMatch ? current.cities.find((city) => city.id === current.nextMatch?.cityId) : null;
    if (preset === 'next' && nextCity) {
      map.easeTo({ center: [nextCity.longitude, nextCity.latitude], zoom: Math.max(map.getZoom(), 4.05), duration: 420, offset: [-180, 0] });
      return;
    }
    const countries: Record<WorldCupViewportPreset, string[]> = {
      'north-america': ['US', 'CA', 'MX'],
      usa: ['US'],
      mexico: ['MX'],
      canada: ['CA'],
      next: [],
      'global-risk': [],
    };
    const selected = current.cities.filter((city) => countries[preset]?.includes(city.country));
    if (!selected.length) {
      map.fitBounds(NORTH_AMERICA_BOUNDS, { padding: { top: 64, right: 96, bottom: 58, left: 310 }, duration: 420 });
      return;
    }
    const bounds = selected.reduce((next, city) => next.extend([city.longitude, city.latitude]), new maplibregl.LngLatBounds(
      [selected[0]!.longitude, selected[0]!.latitude],
      [selected[0]!.longitude, selected[0]!.latitude],
    ));
    map.fitBounds(bounds, { padding: { top: 86, right: 110, bottom: 78, left: 290 }, maxZoom: preset === 'north-america' ? 3.25 : 4.45, duration: 420 });
  };

  const highlightCountry = (iso2: string | null) => {
    const map = mapRef.current;
    if (!map) return;
    if (pulseRafRef.current) {
      cancelAnimationFrame(pulseRafRef.current);
      pulseRafRef.current = null;
    }
    const filter: any = ['==', ['get', 'ISO3166-1-Alpha-2'], iso2 || NO_COUNTRY_MATCH];
    try {
      if (map.getLayer('country-highlight-fill')) map.setFilter('country-highlight-fill', filter);
      if (map.getLayer('country-highlight-border')) map.setFilter('country-highlight-border', filter);
      if (!map.getLayer('country-highlight-fill')) return;
      if (!iso2) {
        map.setPaintProperty('country-highlight-fill', 'fill-color', '#3b82f6');
        map.setPaintProperty('country-highlight-border', 'line-color', '#3b82f6');
        map.setPaintProperty('country-highlight-fill', 'fill-opacity', 0);
        map.setPaintProperty('country-highlight-border', 'line-opacity', 0);
        return;
      }
      map.setPaintProperty('country-highlight-fill', 'fill-color', '#3b82f6');
      map.setPaintProperty('country-highlight-border', 'line-color', '#3b82f6');
      map.setPaintProperty('country-highlight-fill', 'fill-opacity', 0.12);
      map.setPaintProperty('country-highlight-border', 'line-opacity', 0.5);
      const start = performance.now();
      const step = (now: number) => {
        if (!map.getLayer('country-highlight-fill')) {
          pulseRafRef.current = null;
          return;
        }
        const t = (now - start) / 3000;
        if (t >= 1) {
          map.setPaintProperty('country-highlight-fill', 'fill-opacity', 0.12);
          map.setPaintProperty('country-highlight-border', 'line-opacity', 0.5);
          pulseRafRef.current = null;
          return;
        }
        const pulse = Math.sin(t * Math.PI * 3) ** 2;
        const fade = 1 - t * t;
        map.setPaintProperty('country-highlight-fill', 'fill-opacity', 0.12 + 0.24 * pulse * fade);
        map.setPaintProperty('country-highlight-border', 'line-opacity', 0.5 + 0.44 * pulse * fade);
        pulseRafRef.current = requestAnimationFrame(step);
      };
      pulseRafRef.current = requestAnimationFrame(step);
    } catch {
      // Map style can be mid-switch during fallback.
    }
  };

  const toggleLayer = (key: WorldCupLayerKey) => {
    if (key !== 'cities') setInspectorOpen(true);
    setEnabledLayers((current) => {
      const next = { ...current, [key]: !current[key] };
      if (key === 'globalRisk' && !current.globalRisk) {
        next.countryRisk = true;
        next.conflicts = true;
      }
      if (key === 'countryRisk' && current.countryRisk) {
        next.globalRisk = false;
      }
      enabledLayersRef.current = next;
      applyCountryRiskPaintForLayers(next);
      window.requestAnimationFrame(updateDeckLayers);
      return next;
    });
  };

  const updateAtlasFilters = (patch: Partial<WorldCupAtlasFilters>) => {
    setAtlasFilters((current) => {
      const next = { ...current, ...patch };
      atlasFiltersRef.current = next;
      const currentData = dataRef.current;
      const weatherIndex = new Map<string, WorldCupCityWeather>();
      currentData.weather.forEach((item) => weatherIndex.set(item.cityId, item));
      const currentSignals = buildCitySignals(
        currentData.cities,
        currentData.matches,
        weatherIndex,
        currentData.nextMatch,
        currentData.selectedMatchId,
        explicitSelectedCityRef.current,
      );
      const nextMatches = matchesForAtlasFilters(currentData.matches, next, currentSignals);
      const focusMatch = nextMatches[0] || currentData.nextMatch;
      if (focusMatch && atlasFiltersActive(next)) {
        explicitSelectedCityRef.current = focusMatch.cityId;
        onSelectCity(focusMatch.cityId);
        onSelectMatch?.(focusMatch.id);
        setExpandedMatchId(focusMatch.id);
        setInspectorOpen(true);
      }
      window.requestAnimationFrame(updateDeckLayers);
      return next;
    });
  };

  const clearAtlasFilters = () => {
    atlasFiltersRef.current = DEFAULT_ATLAS_FILTERS;
    setAtlasFilters(DEFAULT_ATLAS_FILTERS);
    window.requestAnimationFrame(updateDeckLayers);
  };

  useEffect(() => {
    countryRisksRef.current = countryRisks;
    countryRiskByIsoRef.current = countryRiskByIso;
    updateCountryRiskPaint(mapRef.current, visibleCountryRisks);
    if (selectedRiskIntel?.kind === 'country') {
      const refreshed = countryRiskByIso.get(selectedRiskIntel.risk.iso2);
      if (refreshed && refreshed !== selectedRiskIntel.risk) {
        setSelectedRiskIntel({ kind: 'country', risk: refreshed });
      }
    }
  }, [countryRiskByIso, countryRisks, selectedRiskIntel, visibleCountryRisks]);

  useEffect(() => {
    onCountrySelectRef.current = (risk: CountryConflictRisk) => {
      setSelectedRiskIntel({ kind: 'country', risk });
      setInspectorOpen(false);
      highlightCountry(risk.iso2);
    };
  });

  useEffect(() => {
    if (selectedMatchId) setExpandedMatchId(selectedMatchId);
  }, [selectedMatchId]);

  useEffect(() => {
    dataRef.current = { cities, matches, weather, conflicts, nextMatch, selectedCityId, selectedMatchId };
    updateNextMatchPulseSource(mapRef.current, cities, nextMatch);
    updateDeckLayers();
    const signal = getActiveSignal(
      buildCitySignals(cities, matches, weatherByCity, nextMatch, selectedMatchId, explicitSelectedCityRef.current),
      selectedCityId,
      selectedMatchId,
      matches,
      nextMatch,
    );
    highlightCountry(signal ? selectedCountryCode(cities, matches, selectedCityId, selectedMatchId, nextMatch, explicitSelectedCityRef.current) : null);
  }, [cities, conflicts, matches, weather, nextMatch, selectedCityId, selectedMatchId, weatherByCity, enabledLayers]);

  useEffect(() => {
    const host = mapHostRef.current;
    if (!host || mapRef.current) return undefined;
    const map = new maplibregl.Map({
      container: host,
      style: primaryBasemapStyle(),
      center: WORLDCUP_ATLAS_CENTER,
      zoom: WORLDCUP_ATLAS_ZOOM,
      minZoom: 1.75,
      maxZoom: 7.6,
      renderWorldCopies: false,
      attributionControl: false,
      interactive: true,
      pitchWithRotate: false,
      dragRotate: false,
      touchPitch: false,
      canvasContextAttributes: { powerPreference: 'high-performance' },
    });
    mapRef.current = map;

    let tileLoadOk = false;
    let tileErrorCount = 0;

    const markTileLoadOk = () => {
      tileLoadOk = true;
      if (styleTimeoutRef.current) {
        window.clearTimeout(styleTimeoutRef.current);
        styleTimeoutRef.current = null;
      }
    };

    const addDeck = () => {
      if (deckOverlayRef.current) return;
      const overlay = new MapboxOverlay({
        interleaved: true,
        layers: [],
        pickingRadius: 9,
        useDevicePixels: window.devicePixelRatio > 2 ? 2 : true,
        getTooltip: (info: PickingInfo<DeckObject>) => getDeckTooltip(info),
        onClick: (info: PickingInfo<DeckObject>) => {
          const object = info.object as any;
          if (!object) return;
          const iso2 = String(object.properties?.['ISO3166-1-Alpha-2'] || '');
          if (iso2) {
            const risk = countryRiskByIsoRef.current.get(iso2) || emptyCountryRisk(iso2, String(object.properties?.name || iso2));
            setSelectedRiskIntel({ kind: 'country', risk });
            setInspectorOpen(false);
            highlightCountry(iso2);
            if (info.coordinate) {
              map.easeTo({ center: info.coordinate as [number, number], zoom: Math.max(map.getZoom(), 2.7), duration: 360, offset: [-170, 0] });
            }
            return;
          }
          if (object.type === 'conflict') {
            setSelectedRiskIntel({ kind: 'conflict', conflict: object });
            setInspectorOpen(false);
            highlightCountry(object.iso2);
            map.easeTo({ center: [object.lon, object.lat], zoom: Math.max(map.getZoom(), 2.9), duration: 360, offset: [-160, 0] });
            return;
          }
          const city = object?.type === 'host-city' ? object.city : object?.city;
          if (!city) return;
          setSelectedRiskIntel(null);
          explicitSelectedCityRef.current = city.id;
          onSelectCity(city.id);
          if (object?.type === 'schedule' && object.match?.id) {
            onSelectMatch?.(object.match.id);
            setExpandedMatchId(object.match.id);
          } else if (object?.type === 'host-city' && object.nextMatch?.id) {
            onSelectMatch?.(object.nextMatch.id);
            setExpandedMatchId(object.nextMatch.id);
          }
          setInspectorOpen(true);
          const targetZoom = Math.min(3.35, Math.max(map.getZoom(), 3.04));
          map.easeTo({ center: [city.longitude, city.latitude], zoom: targetZoom, duration: 360, offset: [-190, 0] });
        },
        onError: (error: Error) => console.warn('[WorldCupMap] deck overlay render warning:', error.message),
      });
      deckOverlayRef.current = overlay;
      map.addControl(overlay as unknown as maplibregl.IControl);
      updateDeckLayers();
    };

    const loadSupport = () => {
      markTileLoadOk();
      setMapReady(true);
      localizeBasemapLabels(map);
      applyWorldMonitorMapPaint(map);
      ensureCountryRiskLayers(map, visibleCountryRisksForLayers(countryRisksRef.current, enabledLayersRef.current));
      ensureNextMatchPulseLayer(map, dataRef.current.cities, dataRef.current.nextMatch);
      startNextMatchPulse(map, nextPulseRafRef);
      if (!initialViewportAppliedRef.current) {
        initialViewportAppliedRef.current = true;
        map.fitBounds(NORTH_AMERICA_BOUNDS, { padding: { top: 72, right: 120, bottom: 66, left: 320 }, duration: 0, maxZoom: 3.18 });
      }
      loadMapSupportLayers(map, setRegionHover, countryRisksRef.current, countryRiskByIsoRef, onCountrySelectRef)
        .then(() => {
          updateCountryRiskPaint(map, visibleCountryRisksForLayers(countryRisksRef.current, enabledLayersRef.current));
          ensureNextMatchPulseLayer(map, dataRef.current.cities, dataRef.current.nextMatch);
          highlightCountry(selectedCountryCode(
            dataRef.current.cities,
            dataRef.current.matches,
            dataRef.current.selectedCityId,
            dataRef.current.selectedMatchId,
            dataRef.current.nextMatch,
            explicitSelectedCityRef.current,
          ));
        })
        .catch(() => {});
      addDeck();
      updateDeckLayers();
    };

    const switchToLocalFallback = () => {
      if (fallbackAppliedRef.current) return;
      fallbackAppliedRef.current = true;
      setMapDegraded(true);
      if (pulseRafRef.current) {
        cancelAnimationFrame(pulseRafRef.current);
        pulseRafRef.current = null;
      }
      if (nextPulseRafRef.current) {
        cancelAnimationFrame(nextPulseRafRef.current);
        nextPulseRafRef.current = null;
      }
      map.setStyle(WORLDCUP_REMOTE_FALLBACK_STYLE_URL, { diff: false });
      map.once('style.load', loadSupport);
    };

    const onError = (event: { error?: Error; message?: string }) => {
      const message = event.error?.message || event.message || '';
      if (/Failed to fetch|AJAXError|CORS|NetworkError|403|Forbidden|ERR_EMPTY_RESPONSE|Could not load style/i.test(message)) {
        tileErrorCount += 1;
        if (!tileLoadOk && tileErrorCount >= 4) switchToLocalFallback();
      }
    };
    const onData = (event: { dataType?: string }) => {
      if (event.dataType === 'source') {
        markTileLoadOk();
      }
    };
    const onIdle = () => {
      markTileLoadOk();
      setMapReady(true);
      updateDeckLayers();
    };

    map.on('load', loadSupport);
    map.on('style.load', loadSupport);
    map.on('idle', onIdle);
    map.on('moveend', updateDeckLayers);
    map.on('zoomend', updateDeckLayers);
    map.on('resize', updateDeckLayers);
    map.on('error', onError);
    map.on('data', onData);

    styleTimeoutRef.current = window.setTimeout(() => {
      if (!tileLoadOk) switchToLocalFallback();
    }, 14000);

    const resizeObserver = new ResizeObserver(() => {
      window.requestAnimationFrame(() => {
        map.resize();
        updateDeckLayers();
      });
    });
    if (rootRef.current) resizeObserver.observe(rootRef.current);

    return () => {
      if (styleTimeoutRef.current) window.clearTimeout(styleTimeoutRef.current);
      if (pulseRafRef.current) cancelAnimationFrame(pulseRafRef.current);
      if (nextPulseRafRef.current) cancelAnimationFrame(nextPulseRafRef.current);
      resizeObserver.disconnect();
      setRegionHover(null);
      deckOverlayRef.current?.finalize();
      deckOverlayRef.current = null;
      map.off('error', onError);
      map.off('data', onData);
      map.off('idle', onIdle);
      map.remove();
      mapRef.current = null;
    };
  }, []);

  return (
    <div ref={rootRef} className={`wm-worldcup-map wm-worldcup-maplibre ${mapReady ? 'ready' : ''} ${mapDegraded ? 'degraded' : ''} ${inspectorOpen ? 'inspector-open' : ''}`}>
      <div ref={mapHostRef} className="wm-worldcup-maplibre-host" />
      <EventControlPanel
        enabledLayers={enabledLayers}
        onToggle={toggleLayer}
        filters={atlasFilters}
        onFiltersChange={updateAtlasFilters}
        onClearFilters={clearAtlasFilters}
        dateOptions={dateOptions}
        teamOptions={teamOptions}
        cityOptions={cities}
        filteredMatchCount={filteredMatches.length}
        filteredVenueCount={visibleCitySignals.length}
        summary={mapSummary}
      />
      {regionHover ? (
        <div
          className="wm-worldcup-map-region-tooltip"
          style={{
            transform: `translate(${Math.round(regionHover.screenX - (rootRef.current?.getBoundingClientRect().left || 0) + 14)}px, ${Math.round(regionHover.screenY - (rootRef.current?.getBoundingClientRect().top || 0) + 14)}px)`,
          }}
        >
          <strong>{regionHover.region}</strong>
          <span>{regionHover.country}</span>
        </div>
      ) : null}
      {selectedRiskIntel ? (
        <MapRiskIntelPanel
          intel={selectedRiskIntel}
          onClose={() => {
            setSelectedRiskIntel(null);
            highlightCountry(null);
          }}
        />
      ) : null}
      {activeSignal ? (
        <aside className={`wm-worldcup-map-inspector wm-worldcup-venue-drawer ${inspectorOpen ? 'open' : 'collapsed'}`}>
          <div className="wm-worldcup-map-inspector-head">
            <span>{activeSignal.next ? 'NEXT MATCH VENUE' : 'VENUE DOSSIER'}</span>
            <button type="button" onClick={() => setInspectorOpen((value) => !value)} aria-label="Toggle city inspector">
              {inspectorOpen ? '−' : '+'}
            </button>
          </div>
          <section className="wm-worldcup-map-city-header">
            <span>{cityRole(activeSignal)}</span>
            <strong>{activeSignal.city.city}</strong>
            <em>{activeSignal.city.venue} · {activeSignal.city.countryName} · {activeSignal.plannedMatchCount} matches</em>
            <button type="button" className="wm-worldcup-map-zoom-city" onClick={() => {
              const map = mapRef.current;
              if (map) map.easeTo({ center: [activeSignal.city.longitude, activeSignal.city.latitude], zoom: Math.max(map.getZoom(), 4.1), duration: 380, offset: [-190, 0] });
            }}>
              Focus venue
            </button>
            <div className="wm-worldcup-map-venue-badges">
              <span>Host city</span>
              {activeSignal.next ? <span className="gold">Next match</span> : null}
              {FINAL_CITY_IDS.has(activeSignal.city.id) ? <span className="gold">Final venue</span> : null}
              {knockoutSlotCount(activeSignal) > 0 ? <span>Knockout</span> : null}
              {activeSignal.marketCount || activeMarkets.length ? <span className="cyan">Market-linked</span> : null}
              {activeSignal.weatherRisk >= 28 ? <span className="cyan">Weather watch</span> : null}
              {activeRisk && activeRisk.score >= 48 ? <span className="risk">Risk flagged</span> : null}
            </div>
          </section>
          <div className="wm-worldcup-map-inspector-body">
            <section className={`wm-worldcup-map-venue-cover country-${activeSignal.city.country.toLowerCase()}`}>
              <div>
                <span>Stadium media</span>
                <strong>{activeSignal.city.venue}</strong>
                <em>Official image / video / 3D venue source pending</em>
              </div>
              <div className="wm-worldcup-map-venue-cover-actions">
                <button type="button" disabled>Official link pending</button>
                <button type="button" disabled>3D venue pending</button>
              </div>
            </section>
            {nextCityMatch ? (
              <section className="wm-worldcup-map-inspector-next wm-worldcup-drawer-section">
                <span>{nextCityMatch.id === nextMatch?.id ? 'NEXT MATCH' : 'NEXT CITY MATCH'}</span>
                <strong>{matchTitle(nextCityMatch)}</strong>
                <em>M#{nextCityMatch.fifaMatchNumber || '--'} · {shortKickoff(nextCityMatch)} local · {shortBeijingKickoff(nextCityMatch)} BJT</em>
                <div className="wm-worldcup-map-next-match-meta">
                  <span><small>City</small><b>{activeSignal.city.city}</b></span>
                  <span><small>Venue</small><b>{activeSignal.city.venue}</b></span>
                  <span><small>UTC kickoff</small><b>{formatKickoffUtcShort(nextCityMatch)}</b></span>
                  <span><small>Market</small><b>{nextCityMatch.marketLinked ? 'Linked' : 'Pending'}</b></span>
                </div>
                {activeOdds.length ? (
                  <div className="wm-worldcup-map-odds-strip">
                    {activeOdds[0]?.outcomes.slice(0, 3).map((odd) => (
                      <span key={odd.name}>
                        <small>{odd.name}</small>
                        <b>{odd.decimalOdds?.toFixed(2) || '--'}</b>
                      </span>
                    ))}
                  </div>
                ) : null}
                <div className="wm-worldcup-map-next-risk-line">
                  <span>Weather: {activeSignal.weather?.current.condition || 'Forecast pending'}</span>
                  <span>Market: {activeSignal.marketCount ? 'Linked' : 'Coverage pending'}</span>
                </div>
              </section>
            ) : null}
            <section className="wm-worldcup-drawer-section wm-worldcup-schedule-accordion">
              <div className="wm-worldcup-map-inspector-section-title">
                <span>Stadium Schedule</span>
                <b>{displayedActiveMatches.length || activeSignal.plannedMatchCount}</b>
              </div>
              {activeSlotGroups.map(([label, slots]) => (
                <div className="wm-worldcup-map-match-group" key={label}>
                  <h5>{label}</h5>
                  {slots.map((slot) => {
                    const expanded = expandedMatchId === slot.match.id;
                    const matchMarkets = matchPolymarketMarkets(slot.match, marketGroups);
                    const matchOdds = odds.filter((snapshot) => snapshot.matchId === slot.match.id);
                    return (
                      <article className={`wm-worldcup-match-accordion-row ${expanded ? 'expanded' : ''} ${slot.match.id === selectedMatchId ? 'selected' : ''}`} key={slot.key}>
                        <button type="button" onClick={() => {
                          setExpandedMatchId(expanded ? null : slot.match.id);
                          explicitSelectedCityRef.current = slot.match.cityId;
                          onSelectCity(slot.match.cityId);
                          onSelectMatch?.(slot.match.id);
                        }}>
                          <b>#{slot.match.fifaMatchNumber || '--'}</b>
                          <span>
                            {matchTitle(slot.match)}
                            <small>{slot.match.group || slot.match.round}</small>
                          </span>
                          <em>{shortKickoff(slot.match)} local</em>
                        </button>
                        {expanded ? (
                          <div className="wm-worldcup-match-accordion-detail">
                            <span><small>Stage</small><b>{stageFilterLabel(slot.match.stage)}</b></span>
                            <span><small>UTC</small><b>{formatKickoffUtcShort(slot.match)}</b></span>
                            <span><small>Markets</small><b>{matchMarkets.length || (slot.match.marketLinked ? 1 : 0)}</b></span>
                            <span><small>Odds</small><b>{matchOdds.length}</b></span>
                            <p>{activeSignal.weather ? `${activeSignal.weather.current.condition} · ${activeSignal.weather.current.tempC}°C · rain ${activeSignal.weather.current.precipitationProbability ?? 0}%` : 'Weather source pending for this venue.'}</p>
                            <p>{activeRisk && activeRisk.score >= 48 ? `Risk watch ${activeRisk.level} · ${activeRisk.score}/100. Enable hazards overlay for source rows.` : 'No elevated venue risk in the current host-city model.'}</p>
                          </div>
                        ) : null}
                      </article>
                    );
                  })}
                </div>
              ))}
              {!displayedActiveMatches.length ? <p className="wm-worldcup-map-empty-row">No matches pass the active filters for this stadium.</p> : null}
            </section>
            <section className="wm-worldcup-drawer-section wm-worldcup-map-market-card">
              <span>Market Intelligence</span>
              <strong>{activeMarkets.length} market candidates · {marketCoverage(activeSignal)} city coverage</strong>
              <div className="wm-worldcup-map-market-list">
                {activeMarkets.slice(0, 4).map((market) => (
                  <article className="wm-worldcup-map-market-row" key={`${market.marketId || market.eventId || market.title}`}>
                    <div>
                      <small>{formatCompact(market.volume24h)} 24H · {Math.round(market.confidence * 100)}% match confidence</small>
                      <b>{market.title}</b>
                    </div>
                    <div className="wm-worldcup-map-market-outcomes">
                      {market.outcomes.slice(0, 3).map((outcome) => (
                        <span key={`${market.title}-${outcome.name}`}>
                          <em>{outcome.name}</em>
                          <strong>{outcome.yesPrice == null ? '--' : `${(outcome.yesPrice * 100).toFixed(1)}%`}</strong>
                          <i style={{ width: probabilityWidth(outcome.yesPrice) }} />
                        </span>
                      ))}
                    </div>
                  </article>
                ))}
                {!activeMarkets.length ? <p className="wm-worldcup-map-empty-row">No matched Polymarket market yet. The drawer stays empty instead of inventing prices.</p> : null}
              </div>
            </section>
            <section className={`wm-worldcup-drawer-section wm-worldcup-map-venue-card ${weatherToneClass(activeSignal.weather?.current.condition || '')}`}>
              <span>Venue Intelligence</span>
              <strong>{activeSignal.city.venue}</strong>
              <em>{activeSignal.city.capacity ? `${activeSignal.city.capacity.toLocaleString()} seats` : 'Capacity source pending'} · {venueTimezoneLabel(activeSignal.city)}</em>
              <div className="wm-worldcup-map-impact-grid">
                <span><small>Weather</small><b>{activeSignal.weather?.current.condition || 'Pending'}</b></span>
                <span><small>Pitch</small><b>{activeImpact?.pitch}</b></span>
                <span><small>Travel</small><b>{enabledLayers.transit ? 'Layer on' : 'Pending'}</b></span>
                <span><small>Security</small><b>{enabledLayers.conflicts ? `${activeRisk?.level || 'LOW'} watch` : 'Overlay off'}</b></span>
                <span><small>Knockout</small><b>{knockoutSlotCount(activeSignal)} slots</b></span>
                <span><small>Ops</small><b>{opsStatus(activeSignal)}</b></span>
              </div>
              {activeSignal.weather ? (
                <div className="wm-worldcup-map-inspector-forecast">
                  {activeSignal.weather.forecast.slice(0, 4).map((item) => (
                    <span key={item.date}>
                      <b>{item.lowC}°/{item.highC}°</b>
                      <small>{formatInspectorWeatherDate(item.date)}</small>
                      <em>{item.condition} · {item.precipitationProbability ?? 0}% rain</em>
                    </span>
                  ))}
                </div>
              ) : null}
            </section>
          </div>
        </aside>
      ) : null}
      <MapControls map={mapRef.current} onPreset={focusViewport} />
      <div className="wm-worldcup-maplibre-legend">
        <span>WORLD CUP LEGEND</span>
        {enabledLayers.schedule ? <><b className="admin" /> <em>Match path</em></> : null}
        {enabledLayers.cities ? <><b className="host" /> <em>Host city</em></> : null}
        {enabledLayers.cities ? <><b className="next" /> <em>Next match</em></> : null}
        {selectedCityId ? <><b className="selected" /> <em>Selected city</em></> : null}
        {enabledLayers.weather ? <><b className="weather" /> <em>Weather risk</em></> : null}
        {enabledLayers.markets || enabledLayers.odds ? <><b className="market" /> <em>Market-linked</em></> : null}
        {enabledLayers.conflicts || enabledLayers.countryRisk ? <><b className="risk" /> <em>{enabledLayers.globalRisk ? 'Global conflict risk' : 'Host-region conflict risk'}</em></> : null}
        {enabledLayers.transit || enabledLayers.teams ? <><b className="unavailable" /> <em>Source pending</em></> : null}
      </div>
      <div className="wm-worldcup-maplibre-status">{mapDegraded ? 'WEBGL FALLBACK' : 'WEBGL'}</div>
    </div>
  );
}
