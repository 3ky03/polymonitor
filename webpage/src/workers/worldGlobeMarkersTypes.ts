export type GlobeQualitySetting = 'auto' | 'high' | 'balanced' | 'performance' | 'battery';
export type GlobeQualityLevel = 'ultra' | 'high' | 'medium' | 'low';

export type GlobeWorkerEvent = {
  id?: string | null;
  summary?: string | null;
  source?: string | null;
  sourceUrl?: string | null;
  occurredAt?: string | null;
  severity?: string | null;
  country?: string | null;
  sideA?: string | null;
  sideB?: string | null;
  locationLabel?: string | null;
  latitude?: number | string | null;
  longitude?: number | string | null;
  violenceType?: string | number | null;
  deathsBest?: number | null;
};

export type GlobeMarkerTone = 'state' | 'nonstate' | 'onesided' | 'watch';
export type GlobeGpuMarkerKind = 'event' | 'cluster';

export type GlobeMarkerMeta = {
  id: string;
  kind: GlobeGpuMarkerKind;
  lat: number;
  lng: number;
  color: string;
  size: number;
  tone: GlobeMarkerTone;
  deaths: number;
  count: number;
  label: string;
  country: string;
  location: string;
  occurredAt: string | null;
  source: string;
  sourceUrl: string | null;
  sideA: string;
  sideB: string;
  violenceType: string;
  severity: string;
  summary: string;
  visualKind: 'selected' | 'critical' | 'warning' | 'incident' | 'cluster';
  shape: 'circle' | 'square' | 'triangle' | 'cluster';
  strongGlow: boolean;
  pulsing: boolean;
  priority: number;
};

export type GlobeMarkerWorkerRequest = {
  type: 'BUILD_MARKERS' | 'UPDATE_VIEW' | 'UPDATE_FILTERS' | 'BUILD_CLUSTERS';
  requestId: number;
  events: GlobeWorkerEvent[];
  region: string;
  zoomLevel: number;
  qualityLevel: GlobeQualityLevel;
  idle: boolean;
  selectedId?: string | null;
  hoveredId?: string | null;
};

export type GlobeMarkerWorkerDisposeRequest = {
  type: 'DISPOSE';
  requestId: number;
};

export type GlobeMarkerWorkerMessage = GlobeMarkerWorkerRequest | GlobeMarkerWorkerDisposeRequest;

export type GlobeMarkerWorkerResult = {
  type: 'MARKERS_BUILT';
  requestId: number;
  positions: Float32Array;
  colors: Float32Array;
  sizes: Float32Array;
  opacities: Float32Array;
  flags: Uint8Array;
  meta: GlobeMarkerMeta[];
  htmlMarkers: GlobeMarkerMeta[];
  totalCount: number;
  visibleCount: number;
  htmlCount: number;
  gpuCount: number;
  clusterCount: number;
  workerDurationMs: number;
  generatedAt: number;
};
