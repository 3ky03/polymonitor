import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import * as THREE from 'three';
import type { ContentItem, MarketListItem, MarketSummary, OracleEvent, RuntimeGeoSanctionsShockItem, TradeRow } from '@/types';
import type {
  GlobeMarkerMeta,
  GlobeMarkerWorkerResult,
  GlobeQualityLevel,
  GlobeQualitySetting,
} from '@/workers/worldGlobeMarkersTypes';

type GlobePoint = {
  layer: GlobeLayerId;
  lat: number;
  lng: number;
  size: number;
  altitude: number;
  color: string;
  label: string;
};

type GlobeArc = {
  layer: GlobeLayerId;
  startLat: number;
  startLng: number;
  endLat: number;
  endLng: number;
  color: string[];
};

type GlobeRing = {
  layer: GlobeLayerId;
  lat: number;
  lng: number;
  color: string;
};

type GlobeHtmlMarker = GlobeMarkerMeta;

type GlobeLayerId = 'markets' | 'oracle' | 'trade' | 'lob' | 'intel' | 'ucdp';

export type WorldGlobeStatusMetrics = {
  fps: number;
  markerTotal: number;
  markerVisible: number;
  qualitySetting: GlobeQualitySetting;
  qualityLevel: GlobeQualityLevel;
  dpr: number;
};

type WorldGlobeProps = {
  markets: MarketListItem[];
  selectedMarket: MarketSummary | null;
  recentTrades: TradeRow[];
  recentOracle: OracleEvent[];
  contentItems: ContentItem[];
  ucdpEvents: RuntimeGeoSanctionsShockItem[];
  region: string;
  zoomLevel: number;
  enabledLayerIds: string[];
  onMetricsChange?: (metrics: WorldGlobeStatusMetrics) => void;
};

const GLOBAL_VIEW = { lat: 20, lng: 6, altitude: 1.54 };
const GLOBE_IDLE_PAUSE_MS = 3000;
const GLOBE_FLUSH_DEBOUNCE_MS = 90;
const GLOBE_FLUSH_MAX_WAIT_MS = 280;
const GLOBE_MAX_PIXEL_RATIO = 1.25;
const GLOBE_QUALITY_STORAGE_KEY = 'polydata:world-globe-quality:v1';
const GLOBE_PERF_OVERLAY_STORAGE_KEY = 'polydata:world-globe-perf-overlay:v1';
const GLOBE_MARKER_INITIAL_CAPACITY = 512;
const QUALITY_PIXEL_RATIO: Record<GlobeQualityLevel, number> = {
  ultra: 1.5,
  high: 1.25,
  medium: 1,
  low: 0.75,
};
const QUALITY_LABELS: Record<GlobeQualitySetting, string> = {
  auto: 'Auto',
  high: 'High Quality',
  balanced: 'Balanced',
  performance: 'Performance',
  battery: 'Battery Saver',
};
const REGION_VIEW: Record<string, { lat: number; lng: number; altitude: number }> = {
  global: { lat: 20, lng: 6, altitude: 1.54 },
  america: { lat: 22, lng: -85, altitude: 1.34 },
  mena: { lat: 27, lng: 38, altitude: 1.06 },
  eu: { lat: 50, lng: 12, altitude: 0.96 },
  asia: { lat: 27, lng: 98, altitude: 1.24 },
  latam: { lat: -12, lng: -68, altitude: 1.35 },
  africa: { lat: 6, lng: 20, altitude: 1.28 },
  oceania: { lat: -26, lng: 140, altitude: 1.36 },
};

function resolveAltitude(baseAltitude: number, zoomLevel: number) {
  if (zoomLevel >= 4) return Math.max(0.36, baseAltitude * 0.28);
  if (zoomLevel >= 3) return Math.max(0.58, baseAltitude * 0.46);
  if (zoomLevel >= 2) return Math.max(0.82, baseAltitude * 0.66);
  return baseAltitude;
}

const GEO_HINTS: Array<{ pattern: RegExp; lat: number; lng: number }> = [
  { pattern: /\b(israel|netanyahu|gaza|jerusalem|tel aviv)\b/i, lat: 31.7683, lng: 35.2137 },
  { pattern: /\b(iran|tehran|hormuz)\b/i, lat: 35.6892, lng: 51.389 },
  { pattern: /\b(ukraine|kyiv|kiev)\b/i, lat: 50.4501, lng: 30.5234 },
  { pattern: /\b(russia|moscow)\b/i, lat: 55.7558, lng: 37.6173 },
  { pattern: /\b(china|beijing)\b/i, lat: 39.9042, lng: 116.4074 },
  { pattern: /\b(taiwan|taipei)\b/i, lat: 25.033, lng: 121.5654 },
  { pattern: /\b(india|delhi)\b/i, lat: 28.6139, lng: 77.209 },
  { pattern: /\b(pakistan|islamabad)\b/i, lat: 33.6844, lng: 73.0479 },
  { pattern: /\b(europe|eu|brussels)\b/i, lat: 50.8503, lng: 4.3517 },
  { pattern: /\b(uk|britain|london)\b/i, lat: 51.5072, lng: -0.1276 },
  { pattern: /\b(france|paris)\b/i, lat: 48.8566, lng: 2.3522 },
  { pattern: /\b(germany|berlin)\b/i, lat: 52.52, lng: 13.405 },
  { pattern: /\b(us|u\\.s\\.|america|trump|kamala|president|washington)\b/i, lat: 38.9072, lng: -77.0369 },
  { pattern: /\b(california|silicon valley|san francisco)\b/i, lat: 37.7749, lng: -122.4194 },
  { pattern: /\b(new york|wall street)\b/i, lat: 40.7128, lng: -74.006 },
  { pattern: /\b(mexico|mexico city)\b/i, lat: 19.4326, lng: -99.1332 },
  { pattern: /\b(canada|ottawa)\b/i, lat: 45.4215, lng: -75.6972 },
  { pattern: /\b(brazil|brasilia)\b/i, lat: -15.7939, lng: -47.8828 },
  { pattern: /\b(japan|tokyo)\b/i, lat: 35.6762, lng: 139.6503 },
  { pattern: /\b(korea|seoul)\b/i, lat: 37.5665, lng: 126.978 },
  { pattern: /\b(australia|sydney)\b/i, lat: -33.8688, lng: 151.2093 },
  { pattern: /\b(africa|sudan|cairo|egypt)\b/i, lat: 30.0444, lng: 31.2357 },
];

function hashString(value: string) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = ((hash << 5) - hash + value.charCodeAt(index)) | 0;
  }
  return Math.abs(hash);
}

function resolveGeo(text: string, index = 0) {
  for (const hint of GEO_HINTS) {
    if (hint.pattern.test(text)) {
      return { lat: hint.lat, lng: hint.lng };
    }
  }
  const hash = hashString(`${text}:${index}`);
  const lat = ((hash % 1200) / 10) - 60;
  const lng = (((Math.floor(hash / 1200) % 3600) / 10) - 180);
  return { lat, lng };
}

function markerDateLabel(value?: string | null) {
  if (!value) return 'DATE --';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value).slice(0, 16).toUpperCase();
  return parsed.toLocaleDateString('en-US', {
    month: 'short',
    day: '2-digit',
    year: 'numeric',
    timeZone: 'UTC',
  }).toUpperCase();
}

function markerViolenceLabelForFilter(value?: unknown) {
  const text = String(value || '').trim();
  if (text === '1') return 'STATE-BASED';
  if (text === '2') return 'NON-STATE';
  if (text === '3') return 'ONE-SIDED';
  return text || 'UCDP EVENT';
}

function createUcdpMarkerElement(marker: GlobeHtmlMarker, onSelect: (marker: GlobeHtmlMarker) => void) {
  const el = document.createElement('div');
  el.className = `wm-globe-html-marker wm-globe-html-marker-${marker.tone} shape-${marker.shape} visual-${marker.visualKind} ${marker.strongGlow ? 'is-strong' : ''}`;
  el.title = marker.label;
  el.setAttribute('role', 'button');
  el.setAttribute('tabindex', '0');
  el.setAttribute('aria-label', marker.label);
  el.style.setProperty('--marker-color', marker.color);
  el.style.setProperty('--marker-size', `${marker.size}px`);
  const selectMarker = (event: Event) => {
    event.preventDefault();
    event.stopPropagation();
    onSelect(marker);
  };
  el.addEventListener('click', selectMarker);
  el.addEventListener('keydown', (event) => {
    if ((event as KeyboardEvent).key === 'Enter' || (event as KeyboardEvent).key === ' ') {
      selectMarker(event);
    }
  });

  const hit = document.createElement('div');
  hit.className = 'wm-globe-html-hit';

  const dot = document.createElement('span');
  dot.className = 'wm-globe-html-dot';
  hit.appendChild(dot);

  if (marker.pulsing) {
    const pulse = document.createElement('span');
    pulse.className = 'wm-globe-html-pulse';
    hit.appendChild(pulse);
  }

  el.appendChild(hit);
  return el;
}

function buildPoints(
  markets: MarketListItem[],
  selectedMarket: MarketSummary | null,
  recentTrades: TradeRow[],
  recentOracle: OracleEvent[],
  contentItems: ContentItem[],
  enabledLayerIds: string[],
) {
  const enabledLayers = new Set(enabledLayerIds);
  const isEnabled = (layer: GlobeLayerId) => enabledLayers.has(layer);
  const selectedText = `${selectedMarket?.title || ''} ${selectedMarket?.category || ''} ${(selectedMarket?.tags || []).join(' ')}`;
  const selectedGeo = resolveGeo(selectedText || 'selected-market');

  const marketPoints: GlobePoint[] = markets.slice(0, 24).map((market, index) => {
    const geo = resolveGeo(`${market.title} ${market.category || ''} ${(market.tags || []).join(' ')}`, index);
    return {
      layer: 'markets',
      lat: geo.lat,
      lng: geo.lng,
      size: market.id === selectedMarket?.id ? 0.14 : 0.065 + (index % 3) * 0.015,
      altitude: market.id === selectedMarket?.id ? 0.04 : 0.018,
      color: market.id === selectedMarket?.id ? 'rgba(255,207,75,0.86)' : 'rgba(88,166,255,0.58)',
      label: market.title,
    };
  });

  const oraclePoints: GlobePoint[] = recentOracle.slice(0, 10).map((event, index) => {
    const geo = resolveGeo(`${event.marketTitle || ''} ${event.questionId || ''}`, index + 40);
    return {
      layer: 'oracle',
      lat: geo.lat,
      lng: geo.lng,
      size: 0.075,
      altitude: 0.024,
      color: 'rgba(255,92,92,0.62)',
      label: event.marketTitle || event.eventStatus || 'Oracle event',
    };
  });

  const contentPoints: GlobePoint[] = contentItems.slice(0, 8).map((item, index) => {
    const geo = resolveGeo(`${item.title || ''} ${item.summary || ''} ${item.source || ''}`, index + 80);
    return {
      layer: 'intel',
      lat: geo.lat,
      lng: geo.lng,
      size: 0.06,
      altitude: 0.018,
      color: 'rgba(57,255,115,0.58)',
      label: item.title || item.source || 'Intel',
    };
  });

  const tradePoints: GlobePoint[] = recentTrades.slice(0, 12).map((trade, index) => {
    const geo = resolveGeo(`${trade.marketId || ''} ${trade.outcome || ''} ${trade.side || ''}`, index + 120);
    return {
      layer: 'trade',
      lat: (selectedGeo.lat + geo.lat) / 2,
      lng: (selectedGeo.lng + geo.lng) / 2,
      size: 0.055,
      altitude: 0.016,
      color: String(trade.side).toLowerCase() === 'buy' ? 'rgba(255,143,36,0.5)' : 'rgba(255,209,102,0.46)',
      label: trade.txHash || 'Trade',
    };
  });
  const arcs: GlobeArc[] = marketPoints.slice(0, 10).map((point, index) => ({
    layer: 'lob',
    startLat: selectedGeo.lat,
    startLng: selectedGeo.lng,
    endLat: point.lat,
    endLng: point.lng,
    color: index % 2 === 0
      ? ['rgba(255,140,36,0.02)', 'rgba(255,140,36,0.34)', 'rgba(255,140,36,0.02)']
      : ['rgba(88,166,255,0.02)', 'rgba(88,166,255,0.32)', 'rgba(88,166,255,0.02)'],
  }));

  const rings: GlobeRing[] = [
    { layer: 'lob', ...selectedGeo, color: '#ffcf4b' },
    ...oraclePoints.slice(0, 3).map((point) => ({ layer: 'oracle' as const, lat: point.lat, lng: point.lng, color: '#ff5c5c' })),
    ...contentPoints.slice(0, 2).map((point) => ({ layer: 'intel' as const, lat: point.lat, lng: point.lng, color: '#39ff73' })),
  ];

  return {
    points: [...marketPoints, ...oraclePoints, ...contentPoints, ...tradePoints].filter((point) => isEnabled(point.layer)),
    rings: rings.filter((ring) => isEnabled(ring.layer)),
    arcs: arcs.filter((arc) => isEnabled(arc.layer)),
    selectedGeo,
  };
}

type GlobePerfMetrics = {
  fps: number;
  frameMs: number;
  markerTotal: number;
  markerVisible: number;
  htmlCount: number;
  gpuCount: number;
  clusterCount: number;
  arcCount: number;
  ringCount: number;
  flushMs: number;
  workerMs: number;
  renderUpdateMs: number;
  lastRefreshAt: number;
  qualityLevel: GlobeQualityLevel;
  qualitySetting: GlobeQualitySetting;
  dpr: number;
};

const DEFAULT_PERF_METRICS: GlobePerfMetrics = {
  fps: 0,
  frameMs: 0,
  markerTotal: 0,
  markerVisible: 0,
  htmlCount: 0,
  gpuCount: 0,
  clusterCount: 0,
  arcCount: 0,
  ringCount: 0,
  flushMs: 0,
  workerMs: 0,
  renderUpdateMs: 0,
  lastRefreshAt: 0,
  qualityLevel: 'high',
  qualitySetting: 'auto',
  dpr: 1,
};

function getInitialQualitySetting(): GlobeQualitySetting {
  try {
    const stored = localStorage.getItem(GLOBE_QUALITY_STORAGE_KEY);
    if (stored === 'auto' || stored === 'high' || stored === 'balanced' || stored === 'performance' || stored === 'battery') return stored;
  } catch {
    // ignore
  }
  return 'auto';
}

function qualitySettingToLevel(setting: GlobeQualitySetting, adaptiveLevel: GlobeQualityLevel): GlobeQualityLevel {
  if (setting === 'auto') return adaptiveLevel;
  if (setting === 'high') return 'high';
  if (setting === 'balanced') return 'medium';
  return 'low';
}

function nextLowerQuality(level: GlobeQualityLevel): GlobeQualityLevel {
  if (level === 'ultra') return 'high';
  if (level === 'high') return 'medium';
  return 'low';
}

function nextHigherQuality(level: GlobeQualityLevel): GlobeQualityLevel {
  if (level === 'low') return 'medium';
  if (level === 'medium') return 'high';
  if (level === 'high') return 'ultra';
  return 'ultra';
}

function createMarkerMaterial() {
  return new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      pixelRatio: { value: Math.min(GLOBE_MAX_PIXEL_RATIO, window.devicePixelRatio || 1) },
    },
    vertexShader: `
      attribute vec3 markerColor;
      attribute float markerSize;
      attribute float markerOpacity;
      attribute float markerFlag;
      varying vec3 vColor;
      varying float vOpacity;
      varying float vFlag;
      void main() {
        vColor = markerColor;
        vOpacity = markerOpacity;
        vFlag = markerFlag;
        vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
        float perspective = 280.0 / max(80.0, -mvPosition.z);
        gl_PointSize = markerSize * perspective;
        gl_Position = projectionMatrix * mvPosition;
      }
    `,
    fragmentShader: `
      varying vec3 vColor;
      varying float vOpacity;
      varying float vFlag;
      void main() {
        vec2 p = gl_PointCoord - vec2(0.5);
        float flag = floor(vFlag + 0.5);
        float core = 0.0;
        float halo = 0.0;
        if (flag == 2.0) {
          float edge = max(abs(p.x), abs(p.y));
          if (edge > 0.46) discard;
          core = smoothstep(0.46, 0.26, edge);
          halo = smoothstep(0.5, 0.39, edge) * 0.18;
        } else if (flag == 3.0) {
          float y = p.y + 0.46;
          float width = 0.48 - clamp(y, 0.0, 0.92) * 0.46;
          if (p.y < -0.42 || p.y > 0.46 || abs(p.x) > width) discard;
          float edge = max(abs(p.x) / max(width, 0.01), abs(p.y) / 0.46);
          core = smoothstep(1.0, 0.42, edge);
          halo = smoothstep(1.06, 0.82, edge) * 0.16;
        } else {
          float dist = length(p);
          if (dist > 0.5) discard;
          core = smoothstep(0.48, 0.1, dist);
          halo = smoothstep(0.5, 0.26, dist) * (flag == 1.0 ? 0.24 : 0.12);
        }
        float clusterBoost = mix(1.0, 1.22, step(0.5, flag) * step(flag, 1.5));
        vec3 color = vColor * clusterBoost;
        float alpha = (core + halo) * vOpacity;
        gl_FragColor = vec4(color, alpha);
      }
    `,
  });
}

function createMarkerGeometry(capacity: number) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(capacity * 3), 3));
  geometry.setAttribute('markerColor', new THREE.BufferAttribute(new Float32Array(capacity * 3), 3));
  geometry.setAttribute('markerSize', new THREE.BufferAttribute(new Float32Array(capacity), 1));
  geometry.setAttribute('markerOpacity', new THREE.BufferAttribute(new Float32Array(capacity), 1));
  geometry.setAttribute('markerFlag', new THREE.BufferAttribute(new Float32Array(capacity), 1));
  geometry.setDrawRange(0, 0);
  return geometry;
}

export function WorldGlobe({ markets, selectedMarket, recentTrades, recentOracle, contentItems, ucdpEvents, region, zoomLevel, enabledLayerIds, onMetricsChange }: WorldGlobeProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const hoverCardRef = useRef<HTMLDivElement | null>(null);
  const globeRef = useRef<any>(null);
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  const workerRef = useRef<Worker | null>(null);
  const workerRequestIdRef = useRef(0);
  const markerLayerRef = useRef<any>(null);
  const markerGeometryRef = useRef<any>(null);
  const markerMaterialRef = useRef<any>(null);
  const markerCapacityRef = useRef(0);
  const markerMetaRef = useRef<GlobeMarkerMeta[]>([]);
  const latestWorkerResultRef = useRef<GlobeMarkerWorkerResult | null>(null);
  const htmlMarkersRef = useRef<GlobeHtmlMarker[]>([]);
  const raycasterRef = useRef(new THREE.Raycaster());
  const hoveredMarkerIdRef = useRef<string | null>(null);
  const lastRaycastAtRef = useRef(0);
  const flushTimerRef = useRef<number | null>(null);
  const flushMaxTimerRef = useRef<number | null>(null);
  const idleTimerRef = useRef<number | null>(null);
  const fpsWindowRef = useRef<{ frames: number; start: number; last: number; lowSince: number; highSince: number }>({
    frames: 0,
    start: 0,
    last: 0,
    lowSince: 0,
    highSince: 0,
  });
  const isAnimatingRef = useRef(true);
  const idleRef = useRef(false);
  const lastViewKeyRef = useRef('');
  const pendingFlushRef = useRef<{
    data: ReturnType<typeof buildPoints>;
    region: string;
    zoomLevel: number;
  } | null>(null);
  const onMarkerSelectRef = useRef<(marker: GlobeHtmlMarker) => void>(() => undefined);
  const onMetricsChangeRef = useRef<WorldGlobeProps['onMetricsChange']>(onMetricsChange);
  const currentPixelRatioRef = useRef(1);
  const [qualitySetting, setQualitySetting] = useState<GlobeQualitySetting>(() => getInitialQualitySetting());
  const [adaptiveQualityLevel, setAdaptiveQualityLevel] = useState<GlobeQualityLevel>('high');
  const qualityLevel = qualitySettingToLevel(qualitySetting, adaptiveQualityLevel);
  const [showPerfOverlay, setShowPerfOverlay] = useState(() => {
    if (typeof window === 'undefined') return import.meta.env.DEV;
    return import.meta.env.DEV
      || window.localStorage.getItem(GLOBE_PERF_OVERLAY_STORAGE_KEY) === '1'
      || new URLSearchParams(window.location.search).get('globePerf') === '1';
  });
  const [perfMetrics, setPerfMetrics] = useState<GlobePerfMetrics>({ ...DEFAULT_PERF_METRICS, qualityLevel, qualitySetting });
  const [selectedMarker, setSelectedMarker] = useState<GlobeHtmlMarker | null>(null);
  const [activeViolenceFilter, setActiveViolenceFilter] = useState<string | null>(null);
  onMarkerSelectRef.current = (marker: GlobeHtmlMarker) => setSelectedMarker(marker);
  onMetricsChangeRef.current = onMetricsChange;

  const globeData = useMemo(
    () => buildPoints(markets, selectedMarket, recentTrades, recentOracle, contentItems, enabledLayerIds),
    [contentItems, enabledLayerIds, markets, recentOracle, recentTrades, selectedMarket],
  );

  const updatePerfMetrics = (patch: Partial<GlobePerfMetrics>) => {
    setPerfMetrics((current) => {
      const next = {
        ...current,
        ...patch,
        qualityLevel,
        qualitySetting,
        dpr: currentPixelRatioRef.current,
      };
      onMetricsChangeRef.current?.({
        fps: next.fps,
        markerTotal: next.markerTotal,
        markerVisible: next.markerVisible,
        qualitySetting,
        qualityLevel,
        dpr: next.dpr,
      });
      return next;
    });
  };

  const applyRendererQuality = (nextLevel = qualityLevel) => {
    const globe = globeRef.current;
    if (!globe) return;
    const pixelRatio = Math.min(GLOBE_MAX_PIXEL_RATIO, QUALITY_PIXEL_RATIO[nextLevel], window.devicePixelRatio || 1);
    currentPixelRatioRef.current = pixelRatio;
    try {
      globe.renderer().setPixelRatio(pixelRatio);
      globe.showAtmosphere(nextLevel !== 'low');
      globe.atmosphereAltitude(nextLevel === 'medium' ? 0.08 : nextLevel === 'low' ? 0 : 0.14);
      if (markerMaterialRef.current) {
        markerMaterialRef.current.uniforms.pixelRatio.value = pixelRatio;
      }
    } catch {
      // best-effort renderer tuning
    }
    rootRef.current?.classList.toggle('globe-quality-low', nextLevel === 'low');
    rootRef.current?.classList.toggle('globe-quality-medium', nextLevel === 'medium');
    rootRef.current?.classList.toggle('globe-quality-high', nextLevel === 'high' || nextLevel === 'ultra');
    rootRef.current?.classList.toggle('globe-quality-battery', qualitySetting === 'battery');
  };

  const disposeMarkerLayer = () => {
    const globe = globeRef.current;
    if (globe && markerLayerRef.current) {
      try { globe.scene().remove(markerLayerRef.current); } catch { /* ignore teardown */ }
    }
    markerGeometryRef.current?.dispose();
    markerMaterialRef.current?.dispose();
    markerLayerRef.current = null;
    markerGeometryRef.current = null;
    markerMaterialRef.current = null;
    markerCapacityRef.current = 0;
    markerMetaRef.current = [];
  };

  const ensureMarkerLayer = (capacity: number) => {
    const globe = globeRef.current;
    if (!globe) return null;
    if (markerLayerRef.current && markerGeometryRef.current && markerCapacityRef.current >= capacity) return markerLayerRef.current;

    const nextCapacity = Math.max(GLOBE_MARKER_INITIAL_CAPACITY, 2 ** Math.ceil(Math.log2(Math.max(1, capacity))));
    disposeMarkerLayer();
    const geometry = createMarkerGeometry(nextCapacity);
    const material = createMarkerMaterial();
    const points = new THREE.Points(geometry, material);
    points.name = 'polydata-gpu-risk-markers';
    points.frustumCulled = false;
    markerLayerRef.current = points;
    markerGeometryRef.current = geometry;
    markerMaterialRef.current = material;
    markerCapacityRef.current = nextCapacity;
    globe.scene().add(points);
    applyRendererQuality();
    return points;
  };

  const applyWorkerResult = (result: GlobeMarkerWorkerResult) => {
    const updateStart = performance.now();
    latestWorkerResultRef.current = result;
    markerMetaRef.current = result.meta;
    htmlMarkersRef.current = result.htmlMarkers;

    const layer = ensureMarkerLayer(result.gpuCount);
    const geometry = markerGeometryRef.current;
    if (!layer || !geometry) return;

    const positionAttr = geometry.getAttribute('position');
    const colorAttr = geometry.getAttribute('markerColor');
    const sizeAttr = geometry.getAttribute('markerSize');
    const opacityAttr = geometry.getAttribute('markerOpacity');
    const flagAttr = geometry.getAttribute('markerFlag');

    positionAttr.array.set(result.positions);
    colorAttr.array.set(result.colors);
    sizeAttr.array.set(result.sizes);
    opacityAttr.array.set(result.opacities);
    const flagArray = flagAttr.array as Float32Array;
    for (let index = 0; index < result.flags.length; index += 1) flagArray[index] = result.flags[index] || 0;

    positionAttr.needsUpdate = true;
    colorAttr.needsUpdate = true;
    sizeAttr.needsUpdate = true;
    opacityAttr.needsUpdate = true;
    flagAttr.needsUpdate = true;
    geometry.setDrawRange(0, result.gpuCount);
    geometry.computeBoundingSphere();

    globeRef.current?.htmlElementsData(result.htmlMarkers);
    wakeGlobe();
    updatePerfMetrics({
      markerTotal: result.totalCount,
      markerVisible: result.visibleCount,
      htmlCount: result.htmlCount,
      gpuCount: result.gpuCount,
      clusterCount: result.clusterCount,
      workerMs: result.workerDurationMs,
      renderUpdateMs: performance.now() - updateStart,
      lastRefreshAt: result.generatedAt,
    });
  };

  const requestMarkerBuild = (messageType: 'BUILD_MARKERS' | 'UPDATE_VIEW' | 'UPDATE_FILTERS' | 'BUILD_CLUSTERS' = 'BUILD_MARKERS') => {
    if (!workerRef.current) return;
    const requestId = workerRequestIdRef.current + 1;
    workerRequestIdRef.current = requestId;
    const events = activeViolenceFilter
      ? ucdpEvents.filter((event) => markerViolenceLabelForFilter(event.violenceType) === activeViolenceFilter)
      : ucdpEvents;
    workerRef.current.postMessage({
      type: messageType,
      requestId,
      events,
      region,
      zoomLevel,
      qualityLevel,
      idle: idleRef.current,
      selectedId: selectedMarker?.id || null,
      hoveredId: hoveredMarkerIdRef.current,
    });
  };

  const pickGpuMarker = (event: MouseEvent) => {
    const globe = globeRef.current;
    const canvas = containerRef.current?.querySelector('canvas');
    const points = markerLayerRef.current;
    if (!globe || !canvas || !points) return null;
    const rect = canvas.getBoundingClientRect();
    const pointer = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -(((event.clientY - rect.top) / rect.height) * 2 - 1),
    );
    const raycaster = raycasterRef.current;
    raycaster.params.Points = { threshold: qualityLevel === 'low' ? 2.6 : 1.8 };
    raycaster.setFromCamera(pointer, globe.camera());
    const hit = raycaster.intersectObject(points, false)[0];
    const index = hit?.index;
    return index == null ? null : markerMetaRef.current[index] || null;
  };

  const focusMarker = (marker: GlobeMarkerMeta) => {
    if (marker.kind === 'cluster') {
      const globe = globeRef.current;
      if (globe) {
        const pov = globe.pointOfView();
        globe.pointOfView({ lat: marker.lat, lng: marker.lng, altitude: Math.max(0.42, (pov.altitude || 1.2) * 0.55) }, 760);
      }
      return;
    }
    setSelectedMarker(marker);
  };

  const focusMarkerOnGlobe = (marker: GlobeMarkerMeta) => {
    const globe = globeRef.current;
    if (!globe) return;
    globe.pointOfView({ lat: marker.lat, lng: marker.lng, altitude: marker.kind === 'cluster' ? 0.72 : 0.46 }, 760);
    wakeGlobe();
  };

  const showHoverMarker = (marker: GlobeMarkerMeta | null, event?: MouseEvent) => {
    const card = hoverCardRef.current;
    if (!card) return;
    hoveredMarkerIdRef.current = marker?.id || null;
    if (!marker || !event) {
      card.hidden = true;
      return;
    }
    card.hidden = false;
    card.className = `wm-globe-hover-card tone-${marker.tone}`;
    card.style.left = `${Math.min(window.innerWidth - 280, event.clientX + 14)}px`;
    card.style.top = `${Math.max(70, event.clientY - 20)}px`;
    card.querySelector('[data-field="kind"]')!.textContent = marker.kind === 'cluster' ? `${marker.count} EVENTS` : marker.violenceType;
    card.querySelector('[data-field="country"]')!.textContent = marker.country;
    card.querySelector('[data-field="severity"]')!.textContent = marker.deaths ? `${marker.deaths} deaths` : marker.severity.toUpperCase();
  };

  const setAnimationPaused = (paused: boolean) => {
    const globe = globeRef.current;
    if (!globe) return;
    isAnimatingRef.current = !paused;
    idleRef.current = paused;
    rootRef.current?.classList.toggle('is-render-idle', paused);
    try {
      if (paused) (globe as any).pauseAnimation?.();
      else (globe as any).resumeAnimation?.();
    } catch {
      // globe.gl exposes these methods in current builds; keep this best-effort for older bundles.
    }
    requestMarkerBuild('UPDATE_VIEW');
  };

  const clearIdleTimer = () => {
    if (idleTimerRef.current) {
      window.clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    }
  };

  const scheduleIdlePause = () => {
    clearIdleTimer();
    idleTimerRef.current = window.setTimeout(() => {
      if (document.hidden || !globeRef.current) return;
      const controls = globeRef.current.controls?.();
      if (controls?.autoRotate) return;
      setAnimationPaused(true);
    }, GLOBE_IDLE_PAUSE_MS);
  };

  const wakeGlobe = () => {
    if (!globeRef.current) return;
    if (!isAnimatingRef.current) setAnimationPaused(false);
    scheduleIdlePause();
  };

  const flushGlobeData = () => {
    const globe = globeRef.current;
    const pending = pendingFlushRef.current;
    if (!globe || !pending) return;
    const flushStart = performance.now();
    const arcs = qualityLevel === 'high' || qualityLevel === 'ultra' ? pending.data.arcs.slice(0, 100) : [];
    const rings = qualityLevel === 'low' ? [] : pending.data.rings.slice(0, 30);

    wakeGlobe();
    globe.pointsData(pending.data.points);
    globe.ringsData(rings);
    globe.arcsData(arcs);
    globe.htmlElementsData(htmlMarkersRef.current);

    const viewKey = `${pending.region}:${pending.zoomLevel}`;
    if (lastViewKeyRef.current !== viewKey) {
      lastViewKeyRef.current = viewKey;
      const regionView = REGION_VIEW[pending.region] || GLOBAL_VIEW;
      const altitude = resolveAltitude(regionView.altitude, pending.zoomLevel);
      globe.pointOfView({ lat: regionView.lat, lng: regionView.lng, altitude }, 900);
    }
    updatePerfMetrics({
      arcCount: arcs.length,
      ringCount: rings.length,
      flushMs: performance.now() - flushStart,
    });
  };

  const scheduleGlobeFlush = () => {
    if (!globeRef.current) return;
    if (!flushMaxTimerRef.current) {
      flushMaxTimerRef.current = window.setTimeout(() => {
        flushMaxTimerRef.current = null;
        if (flushTimerRef.current) {
          window.clearTimeout(flushTimerRef.current);
          flushTimerRef.current = null;
        }
        flushGlobeData();
      }, GLOBE_FLUSH_MAX_WAIT_MS);
    }
    if (flushTimerRef.current) window.clearTimeout(flushTimerRef.current);
    flushTimerRef.current = window.setTimeout(() => {
      flushTimerRef.current = null;
      if (flushMaxTimerRef.current) {
        window.clearTimeout(flushMaxTimerRef.current);
        flushMaxTimerRef.current = null;
      }
      flushGlobeData();
    }, GLOBE_FLUSH_DEBOUNCE_MS);
  };

  useEffect(() => {
    try {
      localStorage.setItem(GLOBE_QUALITY_STORAGE_KEY, qualitySetting);
    } catch {
      // ignore
    }
    rootRef.current?.setAttribute('data-globe-quality-setting', qualitySetting);
    applyRendererQuality();
    requestMarkerBuild('UPDATE_FILTERS');
  }, [qualitySetting, qualityLevel]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!event.altKey || !event.shiftKey || event.key.toLowerCase() !== 'g') return;
      setShowPerfOverlay((current) => {
        const next = !current;
        try {
          window.localStorage.setItem(GLOBE_PERF_OVERLAY_STORAGE_KEY, next ? '1' : '0');
        } catch {
          // ignore
        }
        return next;
      });
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  useEffect(() => {
    const worker = new Worker(new URL('../workers/worldGlobeMarkers.worker.ts', import.meta.url), { type: 'module' });
    workerRef.current = worker;
    worker.onmessage = (event: MessageEvent<GlobeMarkerWorkerResult>) => {
      if (event.data.requestId !== workerRequestIdRef.current) return;
      applyWorkerResult(event.data);
    };
    requestMarkerBuild('BUILD_MARKERS');
    return () => {
      worker.postMessage({ type: 'DISPOSE', requestId: workerRequestIdRef.current + 1 });
      worker.terminate();
      workerRef.current = null;
    };
  }, []);

  useEffect(() => {
    requestMarkerBuild('BUILD_MARKERS');
  }, [ucdpEvents, region, zoomLevel, qualityLevel, activeViolenceFilter]);

  useEffect(() => {
    if (!showPerfOverlay && qualitySetting !== 'auto') return undefined;
    let rafId = 0;
    const tick = (now: number) => {
      const fpsState = fpsWindowRef.current;
      if (!fpsState.start) {
        fpsState.start = now;
        fpsState.last = now;
      }
      fpsState.frames += 1;
      const elapsed = now - fpsState.start;
      if (elapsed >= 1000) {
        const fps = (fpsState.frames * 1000) / elapsed;
        const frameMs = fpsState.frames > 0 ? elapsed / fpsState.frames : 0;
        updatePerfMetrics({ fps, frameMs });
        if (qualitySetting === 'auto') {
          if (fps < 25) {
            fpsState.lowSince = fpsState.lowSince || now;
            fpsState.highSince = 0;
            if (now - fpsState.lowSince > 2800) {
              setAdaptiveQualityLevel((current) => nextLowerQuality(current));
              fpsState.lowSince = now;
            }
          } else if (fps > 50) {
            fpsState.highSince = fpsState.highSince || now;
            fpsState.lowSince = 0;
            if (now - fpsState.highSince > 6000) {
              setAdaptiveQualityLevel((current) => nextHigherQuality(current));
              fpsState.highSince = now;
            }
          } else {
            fpsState.lowSince = 0;
            fpsState.highSince = 0;
          }
        }
        fpsState.frames = 0;
        fpsState.start = now;
      }
      fpsState.last = now;
      rafId = window.requestAnimationFrame(tick);
    };
    rafId = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(rafId);
  }, [qualitySetting, showPerfOverlay]);

  useEffect(() => {
    let disposed = false;

    async function mount() {
      if (!containerRef.current || globeRef.current) return;
      const [{ default: Globe }] = await Promise.all([import('globe.gl')]);
      if (disposed || !containerRef.current) return;

      const globe = new Globe(containerRef.current, {
        animateIn: false,
        rendererConfig: {
          antialias: false,
          logarithmicDepthBuffer: false,
          powerPreference: 'high-performance',
        },
      });
      globeRef.current = globe;

      globe
        .globeImageUrl('/textures/earth-topo-bathy.jpg')
        .backgroundImageUrl('')
        .showAtmosphere(true)
        .atmosphereColor('#ff9b42')
        .atmosphereAltitude(0.14)
        .pointAltitude(((point: object) => (point as GlobePoint).altitude) as any)
        .pointRadius(((point: object) => (point as GlobePoint).size) as any)
        .pointColor(((point: object) => (point as GlobePoint).color) as any)
        .pointLabel(((point: object) => (point as GlobePoint).label) as any)
        .pointResolution(10)
        .pointsMerge(false)
        .arcStroke(0.2)
        .arcAltitudeAutoScale(0.22)
        .arcDashLength(0.62)
        .arcDashGap(5.2)
        .arcDashAnimateTime(0)
        .arcColor(((arc: object) => (arc as GlobeArc).color) as any)
        .ringColor(((ring: object) => {
          const color = (ring as GlobeRing).color || '#ffba21';
          return (t: number) => `${color}${Math.round(Math.max(0, 1 - t) * 255).toString(16).padStart(2, '0')}`;
        }) as any)
        .ringMaxRadius(3.5)
        .ringPropagationSpeed(1.35)
        .ringRepeatPeriod(3600)
        .htmlElementsData([])
        .htmlLat(((marker: object) => (marker as GlobeHtmlMarker).lat) as any)
        .htmlLng(((marker: object) => (marker as GlobeHtmlMarker).lng) as any)
        .htmlAltitude(0.003)
        .htmlElement(((marker: object) => createUcdpMarkerElement(marker as GlobeHtmlMarker, onMarkerSelectRef.current)) as any);

      const controls = globe.controls();
      controls.autoRotate = false;
      controls.autoRotateSpeed = 0.3;
      controls.enablePan = false;
      controls.enableZoom = true;
      controls.zoomSpeed = 1.4;
      controls.minDistance = 101;
      controls.maxDistance = 600;
      controls.enableDamping = false;

      try {
        globe.renderer().setPixelRatio(Math.min(GLOBE_MAX_PIXEL_RATIO, Math.max(1, window.devicePixelRatio || 1)));
      } catch {
        // Renderer is best-effort here; globe.gl will still render with its default pixel ratio.
      }

      const glCanvas = containerRef.current.querySelector('canvas');
      if (glCanvas) {
        (glCanvas as HTMLElement).style.cssText =
          'position:absolute;top:0;left:0;width:100% !important;height:100% !important;';
        const canvas = glCanvas as HTMLElement;
        const handleMarkerMove = (event: MouseEvent) => {
          wakeGlobe();
          const now = performance.now();
          if (now - lastRaycastAtRef.current < 90) return;
          lastRaycastAtRef.current = now;
          const marker = pickGpuMarker(event);
          canvas.style.cursor = marker ? 'pointer' : '';
          showHoverMarker(marker, event);
        };
        const handleMarkerClick = (event: MouseEvent) => {
          const marker = pickGpuMarker(event);
          if (!marker) return;
          event.preventDefault();
          event.stopPropagation();
          focusMarker(marker);
        };
        canvas.addEventListener('mousedown', wakeGlobe);
        canvas.addEventListener('touchstart', wakeGlobe, { passive: true });
        canvas.addEventListener('wheel', wakeGlobe, { passive: true });
        canvas.addEventListener('mousemove', handleMarkerMove, { passive: true });
        canvas.addEventListener('mouseleave', () => {
          canvas.style.cursor = '';
          showHoverMarker(null);
        }, { passive: true });
        canvas.addEventListener('click', handleMarkerClick);
      }

      const resize = () => {
        if (!containerRef.current || !globeRef.current) return;
        globeRef.current.width(containerRef.current.clientWidth);
        globeRef.current.height(containerRef.current.clientHeight);
      };

      resize();
      resizeObserverRef.current = new ResizeObserver(resize);
      resizeObserverRef.current.observe(containerRef.current);
      ensureMarkerLayer(GLOBE_MARKER_INITIAL_CAPACITY);
      applyRendererQuality();
      if (latestWorkerResultRef.current) applyWorkerResult(latestWorkerResultRef.current);
      flushGlobeData();
      scheduleIdlePause();
    }

    const handleVisibilityChange = () => {
      if (document.hidden) setAnimationPaused(true);
      else wakeGlobe();
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    void mount();

    return () => {
      disposed = true;
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      clearIdleTimer();
      if (flushTimerRef.current) window.clearTimeout(flushTimerRef.current);
      if (flushMaxTimerRef.current) window.clearTimeout(flushMaxTimerRef.current);
      flushTimerRef.current = null;
      flushMaxTimerRef.current = null;
      resizeObserverRef.current?.disconnect();
      resizeObserverRef.current = null;
      disposeMarkerLayer();
      if (globeRef.current?._destructor) globeRef.current._destructor();
      globeRef.current = null;
    };
  }, []);

  useEffect(() => {
    pendingFlushRef.current = { data: globeData, region, zoomLevel };
    scheduleGlobeFlush();
  }, [globeData, region, zoomLevel]);

  return (
    <div ref={rootRef} className={`wm-globe-runtime-wrap globe-quality-${qualityLevel}`}>
      <div ref={containerRef} className="wm-globe-runtime" />
      <div className="wm-globe-shade" />
      <label className="wm-globe-quality-control">
        <span>Quality · {QUALITY_LABELS[qualitySetting]}</span>
        <select
          value={qualitySetting}
          onChange={(event) => setQualitySetting((event.currentTarget as HTMLSelectElement).value as GlobeQualitySetting)}
          aria-label="Globe render quality"
        >
          {(Object.keys(QUALITY_LABELS) as GlobeQualitySetting[]).map((value) => (
            <option value={value} key={value}>{QUALITY_LABELS[value]}</option>
          ))}
        </select>
      </label>
      {showPerfOverlay ? (
        <div className="wm-globe-perf-overlay">
          <strong>GLOBE PERF</strong>
          <span>FPS <b>{perfMetrics.fps.toFixed(0)}</b></span>
          <span>Frame <b>{perfMetrics.frameMs.toFixed(1)}ms</b></span>
          <span>Total <b>{perfMetrics.markerTotal}</b></span>
          <span>Visible <b>{perfMetrics.markerVisible}</b></span>
          <span>HTML <b>{perfMetrics.htmlCount}</b></span>
          <span>GPU <b>{perfMetrics.gpuCount}</b></span>
          <span>Clusters <b>{perfMetrics.clusterCount}</b></span>
          <span>Arcs/Rings <b>{perfMetrics.arcCount}/{perfMetrics.ringCount}</b></span>
          <span>Flush <b>{perfMetrics.flushMs.toFixed(1)}ms</b></span>
          <span>Worker <b>{perfMetrics.workerMs.toFixed(1)}ms</b></span>
          <span>Render <b>{perfMetrics.renderUpdateMs.toFixed(1)}ms</b></span>
          <span>Quality <b>{perfMetrics.qualitySetting}/{perfMetrics.qualityLevel}</b></span>
          <span>DPR <b>{perfMetrics.dpr.toFixed(2)}</b></span>
          <em>{perfMetrics.lastRefreshAt ? new Date(perfMetrics.lastRefreshAt).toLocaleTimeString() : '--'}</em>
        </div>
      ) : null}
      <div ref={hoverCardRef} className="wm-globe-hover-card" hidden>
        <span data-field="kind" />
        <strong data-field="country" />
        <em data-field="severity" />
      </div>
      {selectedMarker ? (
        <div className={`wm-globe-marker-card tone-${selectedMarker.tone}`}>
          <button type="button" className="wm-globe-marker-close" aria-label="Close conflict detail" onClick={() => setSelectedMarker(null)}>
            ×
          </button>
          <div className="wm-globe-marker-card-header">
            <div>
              <span className="wm-globe-marker-eyebrow">Selected Risk Event</span>
              <h3>{selectedMarker.country}</h3>
            </div>
            <span className={`wm-globe-marker-severity severity-${selectedMarker.visualKind}`}>
              {selectedMarker.visualKind.toUpperCase()}
            </span>
          </div>
          <div className="wm-globe-marker-card-kicker">
            <span>{selectedMarker.violenceType}</span>
            <span>{selectedMarker.source}</span>
            <em>{markerDateLabel(selectedMarker.occurredAt)}</em>
          </div>
          <p className="wm-globe-marker-location">{selectedMarker.location}</p>
          <div className="wm-globe-marker-card-body">
            <div className="wm-globe-marker-deaths">
              <b>{selectedMarker.deaths || '—'}</b>
              <em>reported fatalities</em>
            </div>
            <p>{selectedMarker.summary}</p>
          </div>
          {(selectedMarker.sideA || selectedMarker.sideB) ? (
            <div className="wm-globe-marker-actors">
              <span>{selectedMarker.sideA || 'UNKNOWN'}</span>
              <strong>VS</strong>
              <span>{selectedMarker.sideB || 'UNKNOWN'}</span>
            </div>
          ) : null}
          {activeViolenceFilter ? (
            <button type="button" className="wm-globe-marker-filter-note" onClick={() => setActiveViolenceFilter(null)}>
              Showing {activeViolenceFilter} · clear filter
            </button>
          ) : null}
          <div className="wm-globe-marker-actions">
            <button type="button" onClick={() => focusMarkerOnGlobe(selectedMarker)}>Focus region</button>
            <button type="button" onClick={() => setActiveViolenceFilter(selectedMarker.violenceType)}>
              Filter similar
            </button>
            {selectedMarker.sourceUrl ? (
              <a href={selectedMarker.sourceUrl} target="_blank" rel="noreferrer">View details</a>
            ) : (
              <button type="button" disabled>View details</button>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
