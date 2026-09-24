import type {
  GeoEvent,
  GeoEventCategory,
  GeoEventSeverity,
  HazardEvent,
  HazardKind,
} from '../domain/types';
import type { MapSymbolKey } from './mapSymbols';

export type MapLayerDefinition = {
  id: string;
  label: string;
  legendLabel: string;
  messageKey?: string;
  panelEmoji: string;
  icon: MapSymbolKey;
  hint?: string;
  categories: GeoEventCategory[];
  sourceKeys: string[];
  requiredSources: string[];
  supportedRenderers: Array<'webgl' | 'svg'>;
  availability: 'ready' | 'degraded' | 'unavailable';
  availabilityReason?: string;
  isExecutable: (context?: {
    renderer?: 'webgl' | 'svg';
    availableSources?: ReadonlySet<string>;
  }) => boolean;
  aliases: string[];
  capabilities: Array<'points' | 'areas' | 'paths' | 'animation' | 'clustering' | 'details'>;
  defaultEnabled: boolean;
  selectable: boolean;
  minZoom: number;
  labelMinZoom: number;
  cluster: boolean;
  clusterRadius: number;
  clusterMinPoints: number;
  timeFilter: boolean;
  severities: GeoEventSeverity[];
  legend: Array<{ label: string; symbol: MapSymbolKey }>;
  explanation: {
    purpose: string;
    sources: string[];
    freshness: string;
    confidence: string;
    limitations: string[];
  };
};

export const WORLD_EVENT_LAYER_REGISTRY: readonly MapLayerDefinition[] = [
  {
    id: 'weather-alerts',
    label: 'Storms, Cyclones & Floods',
    legendLabel: 'Storms / floods',
    messageKey: 'atlas.layer.weatherAlerts',
    panelEmoji: '⛈️',
    icon: 'storm',
    hint: 'ALERTS',
    categories: ['weather', 'natural-hazard'],
    sourceKeys: ['nws', 'nhc', 'eonet', 'gdacs'],
    requiredSources: [],
    supportedRenderers: ['webgl', 'svg'],
    availability: 'ready',
    isExecutable: () => true,
    aliases: ['hurricane', 'typhoon', 'tornado', 'flood', 'tsunami', 'storm'],
    capabilities: ['points', 'areas', 'paths', 'clustering', 'details'],
    defaultEnabled: true,
    selectable: true,
    minZoom: 0,
    labelMinZoom: 3,
    cluster: true,
    clusterRadius: 34,
    clusterMinPoints: 5,
    timeFilter: true,
    severities: ['info', 'watch', 'warning', 'critical'],
    legend: [
      { label: 'Severe storm', symbol: 'storm' },
      { label: 'Tornado', symbol: 'tornado' },
      { label: 'Cyclone', symbol: 'cyclone' },
      { label: 'Flood', symbol: 'flood' },
      { label: 'Tsunami', symbol: 'tsunami' },
    ],
    explanation: {
      purpose: 'Active severe storms, tornadoes, tropical cyclones, floods and tsunamis.',
      sources: ['NOAA NWS active alerts', 'NOAA NHC advisories and GIS products', 'NASA EONET', 'GDACS international alerts'],
      freshness: 'NWS refreshes every 60 seconds; NHC every two minutes; EONET every five minutes.',
      confidence: 'Provider-native alert geometry, advisory identity and event tracks.',
      limitations: ['NWS coverage is United States focused.', 'EONET is a discovery feed, not an exhaustive global alert service.'],
    },
  },
  {
    id: 'earthquakes-volcanoes',
    label: 'Earthquakes & Volcanoes',
    legendLabel: 'Quakes / volcanoes',
    messageKey: 'atlas.layer.earthquakesVolcanoes',
    panelEmoji: '🌋',
    icon: 'earthquake',
    hint: 'GEO',
    categories: ['natural-hazard'],
    sourceKeys: ['usgs', 'usgs-volcano-cap', 'eonet', 'gdacs'],
    requiredSources: ['usgs'],
    supportedRenderers: ['webgl', 'svg'],
    availability: 'ready',
    isExecutable: () => true,
    aliases: ['quake', 'eruption', 'seismic', 'volcano'],
    capabilities: ['points', 'areas', 'clustering', 'details'],
    defaultEnabled: true,
    selectable: true,
    minZoom: 0,
    labelMinZoom: 3,
    cluster: true,
    clusterRadius: 32,
    clusterMinPoints: 5,
    timeFilter: true,
    severities: ['info', 'watch', 'warning', 'critical'],
    legend: [
      { label: 'Earthquake', symbol: 'earthquake' },
      { label: 'Volcano', symbol: 'volcano' },
    ],
    explanation: {
      purpose: 'Recent earthquakes and reported volcanic events.',
      sources: ['USGS Earthquake Hazards Program', 'USGS Volcano Hazards Program HANS CAP', 'NASA EONET', 'GDACS international alerts'],
      freshness: 'USGS refreshes every 60 seconds; EONET refreshes every five minutes.',
      confidence: 'Magnitude, depth, PAGER and provider-native event identity are retained.',
      limitations: ['USGS CAP status covers US observatory responsibility areas; EONET is discovery-only and neither is complete global volcano coverage.'],
    },
  },
  {
    id: 'wildfires',
    label: 'Wildfires',
    legendLabel: 'Wildfires',
    messageKey: 'atlas.layer.wildfires',
    panelEmoji: '🔥',
    icon: 'wildfire',
    hint: 'FIRE',
    categories: ['natural-hazard'],
    sourceKeys: ['eonet', 'gdacs', 'firms'],
    requiredSources: [],
    supportedRenderers: ['webgl', 'svg'],
    availability: 'ready',
    isExecutable: () => true,
    aliases: ['fire', 'firms', 'hotspot', 'satellite detection'],
    capabilities: ['points', 'areas', 'clustering', 'details'],
    defaultEnabled: true,
    selectable: true,
    minZoom: 0,
    labelMinZoom: 3,
    cluster: true,
    clusterRadius: 36,
    clusterMinPoints: 6,
    timeFilter: true,
    severities: ['info', 'watch', 'warning', 'critical'],
    legend: [
      { label: 'Wildfire event', symbol: 'wildfire' },
      { label: 'Satellite detection', symbol: 'fire-detection' },
    ],
    explanation: {
      purpose: 'Active wildfire events, with aggregated satellite detections when configured.',
      sources: ['NASA EONET', 'NASA FIRMS'],
      freshness: 'EONET refreshes every five minutes; FIRMS availability is source-configured.',
      confidence: 'Event and detection evidence remains source-native.',
      limitations: ['FIRMS raw detections must be spatially aggregated before rendering.', 'FIRMS is unavailable without a MAP_KEY.'],
    },
  },
  {
    id: 'extreme-temperature',
    label: 'Extreme Temperature Alerts',
    legendLabel: 'Extreme temp',
    messageKey: 'atlas.layer.extremeTemperature',
    panelEmoji: '🌡️',
    icon: 'heat',
    hint: 'TEMP',
    categories: ['weather', 'natural-hazard'],
    sourceKeys: ['nws', 'eonet'],
    requiredSources: [],
    supportedRenderers: ['webgl', 'svg'],
    availability: 'ready',
    isExecutable: () => true,
    aliases: ['heat', 'cold', 'temperature', 'freeze'],
    capabilities: ['points', 'areas', 'clustering', 'details'],
    defaultEnabled: true,
    selectable: true,
    minZoom: 0,
    labelMinZoom: 3,
    cluster: true,
    clusterRadius: 34,
    clusterMinPoints: 5,
    timeFilter: true,
    severities: ['info', 'watch', 'warning', 'critical'],
    legend: [
      { label: 'Extreme heat', symbol: 'heat' },
      { label: 'Extreme cold', symbol: 'cold' },
    ],
    explanation: {
      purpose: 'Active extreme heat and extreme cold alerts.',
      sources: ['NWS active CAP alerts', 'NASA EONET'],
      freshness: 'NWS refreshes every 60 seconds; EONET refreshes every five minutes.',
      confidence: 'Provider severity, urgency and certainty are retained.',
      limitations: ['Global coverage is incomplete and varies by provider.'],
    },
  },
  {
    id: 'climate-anomalies',
    label: 'Major Weather Anomalies',
    legendLabel: 'Anomalies',
    messageKey: 'atlas.layer.climateAnomalies',
    panelEmoji: '🌀',
    icon: 'anomaly',
    hint: 'ANOMALY',
    categories: ['weather', 'natural-hazard'],
    sourceKeys: ['climate-anomaly'],
    requiredSources: ['climate-anomaly'],
    supportedRenderers: ['webgl', 'svg'],
    availability: 'ready',
    isExecutable: () => true,
    aliases: ['anomaly', 'climatology', 'temperature departure', 'precipitation departure'],
    capabilities: ['points', 'areas', 'details'],
    defaultEnabled: true,
    selectable: true,
    minZoom: 0,
    labelMinZoom: 3,
    cluster: true,
    clusterRadius: 34,
    clusterMinPoints: 5,
    timeFilter: true,
    severities: ['info', 'watch', 'warning', 'critical'],
    legend: [{ label: 'Major anomaly', symbol: 'anomaly' }],
    explanation: {
      purpose: 'Major observed weather anomalies and quantitative climate departures.',
      sources: ['NOAA NCEI Climate at a Glance global mapping'],
      freshness: 'Monthly, using the newest published observation month.',
      confidence: 'Quantitative anomalies require a declared baseline period and calculation version.',
      limitations: ['The 5 degree monthly grid is an observation, not an active warning or local impact forecast.', 'The declared baseline is 1991-2020.'],
    },
  },
  {
    id: 'intel-hotspots',
    label: 'Intel Hotspots',
    legendLabel: 'Intel',
    messageKey: 'atlas.layer.intelHotspots',
    panelEmoji: '🎯',
    icon: 'intel',
    hint: 'INTEL',
    categories: ['intel'],
    sourceKeys: ['breaking-event-radar'],
    requiredSources: ['breaking-event-radar'],
    supportedRenderers: ['webgl', 'svg'],
    availability: 'ready',
    isExecutable: () => true,
    aliases: ['intelligence', 'breaking event', 'news hotspot'],
    capabilities: ['points', 'areas', 'clustering', 'details'],
    defaultEnabled: false,
    selectable: true,
    minZoom: 0,
    labelMinZoom: 3,
    cluster: true,
    clusterRadius: 34,
    clusterMinPoints: 5,
    timeFilter: true,
    severities: ['watch', 'warning', 'critical'],
    legend: [{ label: 'Evidence-qualified intelligence', symbol: 'intel' }],
    explanation: {
      purpose: 'Verified, time-bounded real-world intelligence with reliable spatial evidence.',
      sources: ['Breaking Event Radar', 'GDELT', 'Wikimedia'],
      freshness: 'Source-specific runtime freshness.',
      confidence: 'Provider confidence and evidence diversity.',
      limitations: ['Only time-bounded country records with confidence >= 0.60 and at least two articles from two sources are rendered.', 'Country polygons do not imply a precise incident footprint.'],
    },
  },
  {
    id: 'ucdp',
    label: 'Conflict & Unrest',
    legendLabel: 'Conflict',
    messageKey: 'atlas.layer.ucdp',
    panelEmoji: '⚔️',
    icon: 'conflict-state',
    hint: 'CONFLICT',
    categories: ['conflict', 'unrest'],
    sourceKeys: ['geo-sanctions-shock'],
    requiredSources: ['geo-sanctions-shock'],
    supportedRenderers: ['webgl', 'svg'],
    availability: 'ready',
    isExecutable: () => true,
    aliases: ['war', 'unrest', 'ucdp', 'armed conflict'],
    capabilities: ['points', 'areas', 'clustering', 'details'],
    defaultEnabled: true,
    selectable: true,
    minZoom: 0,
    labelMinZoom: 3,
    cluster: true,
    clusterRadius: 32,
    clusterMinPoints: 5,
    timeFilter: true,
    severities: ['info', 'watch', 'warning', 'critical'],
    legend: [
      { label: 'State-based', symbol: 'conflict-state' },
      { label: 'Non-state', symbol: 'conflict-nonstate' },
      { label: 'One-sided', symbol: 'conflict-one-sided' },
    ],
    explanation: {
      purpose: 'Verified conflict events and fatality estimates.',
      sources: ['UCDP'],
      freshness: 'Runtime snapshot with source status.',
      confidence: 'Source-native event identity and coordinates.',
      limitations: ['Fatality estimates and classifications may be revised.'],
    },
  },
  {
    id: 'sanctions-country-risk',
    label: 'Sanctions & Country Risk',
    legendLabel: 'Country risk',
    messageKey: 'atlas.layer.countryRisk',
    panelEmoji: '🛡️',
    icon: 'country-risk',
    hint: 'RISK',
    categories: ['sanctions', 'country-risk'],
    sourceKeys: ['geo-sanctions-shock'],
    requiredSources: ['geo-sanctions-shock'],
    supportedRenderers: ['webgl', 'svg'],
    availability: 'ready',
    isExecutable: () => true,
    aliases: ['sanctions', 'risk', 'country exposure'],
    capabilities: ['areas', 'details'],
    defaultEnabled: true,
    selectable: true,
    minZoom: 0,
    labelMinZoom: 2,
    cluster: false,
    clusterRadius: 0,
    clusterMinPoints: 0,
    timeFilter: true,
    severities: ['watch', 'warning', 'critical'],
    legend: [
      { label: 'Country risk evidence', symbol: 'country-risk' },
    ],
    explanation: {
      purpose: 'Country-level sanctions and verified risk changes.',
      sources: ['OFAC', 'Federal Register', 'UCDP aggregate'],
      freshness: 'Per-source runtime status.',
      confidence: 'Official action and source-backed country aggregation.',
      limitations: ['Country polygons aggregate configured sources and are not legal advice.', 'Unknown entities and non-country targets are rejected rather than mapped to a capital.'],
    },
  },
  {
    id: 'air-routes',
    label: 'Air Routes',
    legendLabel: 'Aviation',
    messageKey: 'atlas.layer.airRoutes',
    panelEmoji: '✈️',
    icon: 'air-route',
    hint: 'REFERENCE',
    categories: ['infrastructure'],
    sourceKeys: ['global-transport-shipping'],
    requiredSources: ['global-transport-shipping'],
    supportedRenderers: ['webgl', 'svg'],
    availability: 'ready',
    isExecutable: () => true,
    aliases: ['aviation', 'aircraft', 'airport', 'flight', 'corridor'],
    capabilities: ['points', 'paths', 'animation', 'details'],
    defaultEnabled: true,
    selectable: true,
    minZoom: 1,
    labelMinZoom: 4,
    cluster: false,
    clusterRadius: 0,
    clusterMinPoints: 0,
    timeFilter: false,
    severities: ['info', 'watch', 'warning', 'critical'],
    legend: [
      { label: 'Trunk corridor', symbol: 'air-route' },
      { label: 'Weather exposure', symbol: 'weather-exposure' },
      { label: 'Conflict exposure', symbol: 'conflict-exposure' },
      { label: 'Aircraft', symbol: 'aircraft' },
    ],
    explanation: {
      purpose: 'Low-priority aviation topology reference with a bounded default trunk view.',
      sources: ['OpenFlights', 'OpenSky/ADSB where available'],
      freshness: 'Static topology plus source-specific live snapshots.',
      confidence: 'Provider-native coordinates and identifiers.',
      limitations: ['A route line does not prove a flight is operating.'],
    },
  },
] as const;

export function selectableWorldEventLayers() {
  const order = new Map([
    'weather-alerts',
    'earthquakes-volcanoes',
    'wildfires',
    'extreme-temperature',
    'climate-anomalies',
    'air-routes',
    'intel-hotspots',
    'ucdp',
    'sanctions-country-risk',
  ].map((id, index) => [id, index]));
  return WORLD_EVENT_LAYER_REGISTRY
    .filter((layer) => layer.selectable)
    .slice()
    .sort((left, right) => (order.get(left.id) ?? 99) - (order.get(right.id) ?? 99));
}

export type MapLayerExecutionContext = {
  renderer?: 'webgl' | 'svg';
  availableSources?: ReadonlySet<string>;
};

export function isWorldEventLayerExecutable(
  layer: MapLayerDefinition,
  context: MapLayerExecutionContext = {},
) {
  if (layer.availability === 'unavailable') return false;
  if (context.renderer && !layer.supportedRenderers.includes(context.renderer)) return false;
  if (context.availableSources
    && layer.requiredSources.some((source) => !context.availableSources?.has(source))) return false;
  return layer.isExecutable(context);
}

export function executableWorldEventLayers(context: MapLayerExecutionContext = {}) {
  return selectableWorldEventLayers().filter((layer) => isWorldEventLayerExecutable(layer, context));
}

export function worldEventLayerById(id: string) {
  return WORLD_EVENT_LAYER_REGISTRY.find((layer) => layer.id === id);
}

const HAZARD_LAYER_KINDS: Record<string, readonly HazardKind[]> = {
  'weather-alerts': ['severe-storm', 'tornado', 'tropical-cyclone', 'flood', 'tsunami'],
  'earthquakes-volcanoes': ['earthquake', 'volcano'],
  wildfires: ['wildfire', 'fire-detection'],
  'extreme-temperature': ['extreme-heat', 'extreme-cold'],
  'climate-anomalies': ['temperature-anomaly', 'precipitation-anomaly', 'other-weather-anomaly'],
};

export function isHazardGeoEvent(event: GeoEvent): event is HazardEvent {
  return (event.category === 'natural-hazard' || event.category === 'weather')
    && typeof (event as Partial<HazardEvent>).hazardKind === 'string';
}

export function worldEventLayerIdForEvent(event: GeoEvent): string | null {
  if (isHazardGeoEvent(event)) {
    return Object.entries(HAZARD_LAYER_KINDS)
      .find(([, kinds]) => kinds.includes(event.hazardKind))?.[0] || null;
  }
  if (event.category === 'conflict' || event.category === 'unrest') return 'ucdp';
  if (event.category === 'infrastructure') return 'air-routes';
  if (event.category === 'sanctions' || event.category === 'country-risk') return 'sanctions-country-risk';
  if (event.category === 'intel') return 'intel-hotspots';
  return null;
}

export function eventMatchesWorldEventLayers(event: GeoEvent, activeLayerIds: readonly string[]) {
  const layerId = worldEventLayerIdForEvent(event);
  return layerId != null && activeLayerIds.includes(layerId);
}
