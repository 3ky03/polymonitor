export type GeoEventCategory =
  | 'intel'
  | 'conflict'
  | 'unrest'
  | 'sanctions'
  | 'country-risk'
  | 'weather'
  | 'natural-hazard'
  | 'transport-disruption'
  | 'infrastructure';

export type GeoEventSeverity = 'info' | 'watch' | 'warning' | 'critical';
export type GeoEventFreshness = 'live' | 'fresh' | 'stale' | 'unknown';
export type GeoEventSourceStatus = 'ok' | 'partial' | 'degraded' | 'error';
export type LocationPrecision = 'exact' | 'city' | 'region' | 'country' | 'unknown';

export type GeoPoint = [number, number];

export type GeoEventGeometry =
  | { type: 'Point'; coordinates: GeoPoint }
  | { type: 'Polygon'; coordinates: number[][][] }
  | { type: 'MultiPolygon'; coordinates: number[][][][] }
  | { type: 'LineString'; coordinates: GeoPoint[] };

export interface GeoEventSource {
  provider: string;
  url?: string;
  nativeId?: string;
  observedAt?: string;
  ingestedAt?: string;
  freshness?: GeoEventFreshness;
  status?: GeoEventSourceStatus;
}

export interface GeoEvent {
  id: string;
  category: GeoEventCategory;
  title: string;
  summary?: string;
  severity: GeoEventSeverity;
  occurredAt?: string;
  updatedAt?: string;
  expiresAt?: string;
  geometry?: GeoEventGeometry;
  locationPrecision: LocationPrecision;
  countryCode?: string;
  regionCode?: string;
  locationLabel?: string;
  confidence?: number;
  sources: GeoEventSource[];
  limitations: string[];
  relatedMarketIds: Array<string | number>;
  properties: Record<string, unknown>;
}

export type HazardKind =
  | 'severe-storm'
  | 'tornado'
  | 'tropical-cyclone'
  | 'flood'
  | 'extreme-heat'
  | 'extreme-cold'
  | 'earthquake'
  | 'volcano'
  | 'tsunami'
  | 'wildfire'
  | 'fire-detection'
  | 'temperature-anomaly'
  | 'precipitation-anomaly'
  | 'other-weather-anomaly';

export type HazardLifecycle =
  | 'forecast'
  | 'watch'
  | 'active'
  | 'observed'
  | 'contained'
  | 'ended'
  | 'unknown';

export interface HazardCoverage {
  scope: 'global' | 'regional' | 'country' | 'provider-area' | 'viewport';
  label: string;
  isComplete: boolean;
  gaps: string[];
}

export interface HazardEvent extends GeoEvent {
  category: 'weather' | 'natural-hazard';
  hazardKind: HazardKind;
  lifecycle: HazardLifecycle;
  effectiveAt?: string;
  onsetAt?: string;
  endedAt?: string;
  coverage: HazardCoverage;
  severityEvidence: {
    provider: string;
    rawLevel?: string;
    mappingVersion: string;
    reason: string;
  };
  revision: {
    nativeEventId: string;
    advisoryId?: string;
    revisionAt?: string;
    replaces?: string[];
    cancelled?: boolean;
  };
  metrics:
    | {
        kind: 'earthquake';
        magnitude: number;
        depthKm?: number;
        significance?: number;
        pagerAlert?: string;
        tsunami?: boolean;
      }
    | {
        kind: 'tropical-cyclone';
        maximumWind?: { value: number; unit: 'kt' | 'km/h' | 'm/s' };
        pressureHpa?: number;
        categoryLabel?: string;
        advisoryNumber?: string;
      }
    | {
        kind: 'weather-alert';
        urgency?: string;
        certainty?: string;
        providerSeverity?: string;
        instruction?: string;
      }
    | {
        kind: 'wildfire';
        detectionCount?: number;
        fireRadiativePowerMw?: number;
        sensor?: string;
        satellite?: string;
        confidenceLabel?: string;
      }
    | {
        kind: 'climate-anomaly';
        variable: string;
        value: number;
        anomaly: number;
        unit: string;
        baselinePeriod: string;
        calculationVersion: string;
        timeWindow: string;
        spatialResolution: string;
        provider: string;
      }
    | {
        kind: 'volcano-or-other';
        statusLabel?: string;
      };
}

export interface HazardMapSource {
  key: string;
  status: GeoEventSourceStatus;
  coverage: HazardCoverage;
  fetchedAt?: string | null;
  dataUpdatedAt?: string | null;
  staleAfter?: string | null;
  lastSuccessAt?: string | null;
  errorCode?: string | null;
}

export interface HazardMapResponse {
  schemaVersion: 'natural-hazards.v1' | 'natural-hazards-map.v1';
  generatedAt: string;
  events: HazardEvent[];
  sources: HazardMapSource[];
  isPartial: boolean;
  errors: Array<{ source: string; code?: string | null }>;
  counts: {
    events: number;
    byHazardKind: Partial<Record<HazardKind, number>>;
  };
  meta?: {
    source?: string;
    geometryMode?: 'simplified' | 'full';
    geometryZoom?: number;
    detailEndpoint?: string;
    fullSchemaVersion?: string;
  };
}

export interface HazardDetailResponse {
  schemaVersion: 'natural-hazard-detail.v1';
  generatedAt: string;
  event: HazardEvent;
}

export type HazardMarketEvidence = {
  passed: boolean;
  level?: 'direct' | 'contextual';
  reason: string;
  distanceKm?: number;
  targetDate?: string | null;
};

export interface RelatedWeatherMarket {
  marketId?: number | null;
  eventSlug?: string | null;
  title: string;
  url?: string | null;
  marketFamily: string;
  relationship: 'direct' | 'contextual';
  matchScore: number;
  matchReasons: {
    type: HazardMarketEvidence;
    space: HazardMarketEvidence;
    time: HazardMarketEvidence;
    metric: HazardMarketEvidence;
  };
  matchedAt: string;
  linkerVersion: string;
  target: {
    cityId?: string | null;
    city?: string | null;
    country?: string | null;
    lat?: number | null;
    lon?: number | null;
    date?: string | null;
  };
  quote: {
    leadingOutcome?: string | null;
    probability?: number | null;
    bestBid?: number | null;
    bestAsk?: number | null;
    spread?: number | null;
    bookStatus?: string | null;
    priceSource?: string | null;
    updatedAt?: string | null;
  };
  oracle: {
    status: string;
    reason: string;
  };
}

export interface HazardMarketLinksResponse {
  schemaVersion: 'hazard-market-links.v1';
  generatedAt: string;
  eventId: string;
  linkerVersion: string;
  markets: RelatedWeatherMarket[];
  counts: {
    candidates: number;
    matched: number;
    returned: number;
    rejected: number;
  };
  limitations: string[];
}

export type GeoEventAdapterIssue = {
  index: number;
  code: string;
  message: string;
};

export type GeoEventAdapterResult = {
  events: GeoEvent[];
  rejected: GeoEventAdapterIssue[];
};
