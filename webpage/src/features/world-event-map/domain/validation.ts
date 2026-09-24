import type {
  GeoEvent,
  GeoEventAdapterIssue,
  GeoEventGeometry,
  GeoEventSource,
  HazardEvent,
} from './types';

const VALID_CATEGORIES = new Set([
  'intel',
  'conflict',
  'unrest',
  'sanctions',
  'country-risk',
  'weather',
  'natural-hazard',
  'transport-disruption',
  'infrastructure',
]);
const VALID_SEVERITIES = new Set(['info', 'watch', 'warning', 'critical']);
const VALID_PRECISIONS = new Set(['exact', 'city', 'region', 'country', 'unknown']);
const VALID_HAZARD_KINDS = new Set([
  'severe-storm',
  'tornado',
  'tropical-cyclone',
  'flood',
  'extreme-heat',
  'extreme-cold',
  'earthquake',
  'volcano',
  'tsunami',
  'wildfire',
  'fire-detection',
  'temperature-anomaly',
  'precipitation-anomaly',
  'other-weather-anomaly',
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function isFiniteCoordinate(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

export function isGeoPoint(value: unknown): value is [number, number] {
  return Array.isArray(value)
    && value.length === 2
    && isFiniteCoordinate(value[0])
    && isFiniteCoordinate(value[1])
    && value[0] >= -180
    && value[0] <= 180
    && value[1] >= -90
    && value[1] <= 90;
}

function pointsEqual(first: number[], second: number[]) {
  return first.length === 2
    && second.length === 2
    && first[0] === second[0]
    && first[1] === second[1];
}

function isClosedRing(value: unknown): value is number[][] {
  return Array.isArray(value)
    && value.length >= 4
    && value.every(isGeoPoint)
    && pointsEqual(value[0] as number[], value[value.length - 1] as number[]);
}

export function isGeoEventGeometry(value: unknown): value is GeoEventGeometry {
  if (!isRecord(value) || typeof value.type !== 'string') return false;
  if (value.type === 'Point') return isGeoPoint(value.coordinates);
  if (value.type === 'LineString') {
    return Array.isArray(value.coordinates)
      && value.coordinates.length >= 2
      && value.coordinates.every(isGeoPoint);
  }
  if (value.type === 'Polygon') {
    return Array.isArray(value.coordinates)
      && value.coordinates.length > 0
      && value.coordinates.every(isClosedRing);
  }
  if (value.type === 'MultiPolygon') {
    return Array.isArray(value.coordinates)
      && value.coordinates.length > 0
      && value.coordinates.every(
        (polygon) => Array.isArray(polygon) && polygon.length > 0 && polygon.every(isClosedRing),
      );
  }
  return false;
}

function isIsoTimestamp(value: unknown) {
  return typeof value === 'string'
    && value.trim().length > 0
    && Number.isFinite(Date.parse(value));
}

function isSource(value: unknown): value is GeoEventSource {
  return isRecord(value) && typeof value.provider === 'string' && value.provider.trim().length > 0;
}

function hazardMetricsMatch(event: HazardEvent) {
  if (event.hazardKind === 'earthquake') return event.metrics.kind === 'earthquake';
  if (event.hazardKind === 'tropical-cyclone') return event.metrics.kind === 'tropical-cyclone';
  if (event.hazardKind === 'severe-storm'
    || event.hazardKind === 'tornado'
    || event.hazardKind === 'flood'
    || event.hazardKind === 'extreme-heat'
    || event.hazardKind === 'extreme-cold'
    || event.hazardKind === 'tsunami') {
    return event.metrics.kind === 'weather-alert';
  }
  if (event.hazardKind === 'wildfire' || event.hazardKind === 'fire-detection') {
    return event.metrics.kind === 'wildfire';
  }
  if (event.hazardKind === 'temperature-anomaly'
    || event.hazardKind === 'precipitation-anomaly') {
    return event.metrics.kind === 'climate-anomaly';
  }
  if (event.hazardKind === 'other-weather-anomaly') {
    return event.metrics.kind === 'climate-anomaly' || event.metrics.kind === 'volcano-or-other';
  }
  return event.metrics.kind === 'volcano-or-other';
}

function climateMetricsAreReproducible(event: HazardEvent) {
  if (event.metrics.kind !== 'climate-anomaly') return false;
  return typeof event.metrics.value === 'number'
    && Number.isFinite(event.metrics.value)
    && Boolean(String(event.metrics.unit || '').trim())
    && Boolean(String(event.metrics.baselinePeriod || '').trim())
    && Boolean(String(event.metrics.calculationVersion || '').trim())
    && Boolean(String(event.metrics.timeWindow || '').trim())
    && Boolean(String(event.metrics.spatialResolution || '').trim())
    && Boolean(String(event.metrics.provider || '').trim());
}

export function validateGeoEvent(value: unknown): { ok: true; event: GeoEvent } | { ok: false; errors: string[] } {
  const errors: string[] = [];
  if (!isRecord(value)) return { ok: false, errors: ['event must be an object'] };

  const id = typeof value.id === 'string' ? value.id.trim() : '';
  const title = typeof value.title === 'string' ? value.title.trim() : '';
  if (!id) errors.push('id must be a stable non-empty string');
  if (!title) errors.push('title must be non-empty');
  if (!VALID_CATEGORIES.has(String(value.category || ''))) errors.push('category is invalid');
  if (!VALID_SEVERITIES.has(String(value.severity || ''))) errors.push('severity is invalid');
  if (!VALID_PRECISIONS.has(String(value.locationPrecision || ''))) errors.push('locationPrecision is invalid');

  if (value.geometry != null && !isGeoEventGeometry(value.geometry)) {
    errors.push('geometry contains invalid or out-of-range coordinates');
  }
  if (value.locationPrecision === 'unknown' && value.geometry != null) {
    errors.push('unknown location precision cannot carry map geometry');
  }
  for (const key of ['occurredAt', 'updatedAt', 'expiresAt'] as const) {
    if (value[key] != null && !isIsoTimestamp(value[key])) errors.push(`${key} must be ISO 8601`);
  }
  if (value.confidence != null
    && (typeof value.confidence !== 'number'
      || !Number.isFinite(value.confidence)
      || value.confidence < 0
      || value.confidence > 1)) {
    errors.push('confidence must be between 0 and 1');
  }
  if (!Array.isArray(value.sources) || value.sources.length === 0 || !value.sources.every(isSource)) {
    errors.push('sources must contain at least one valid provider');
  }
  if (!Array.isArray(value.limitations) || !value.limitations.every((item) => typeof item === 'string')) {
    errors.push('limitations must be a string array');
  }
  if (!Array.isArray(value.relatedMarketIds)) errors.push('relatedMarketIds must be an array');
  if (!isRecord(value.properties)) errors.push('properties must be an object');

  if ((value.category === 'weather' || value.category === 'natural-hazard') && 'hazardKind' in value) {
    const hazard = value as unknown as HazardEvent;
    if (!VALID_HAZARD_KINDS.has(String(hazard.hazardKind || ''))) errors.push('hazardKind is invalid');
    if (!hazardMetricsMatch(hazard)) errors.push('hazard metrics are incompatible with hazardKind');
    if ((hazard.hazardKind === 'temperature-anomaly' || hazard.hazardKind === 'precipitation-anomaly')
      && !climateMetricsAreReproducible(hazard)) {
      errors.push('climate anomaly requires value, unit, baseline, time window, resolution, version and provider');
    }
    if (!hazard.coverage || !Array.isArray(hazard.coverage.gaps)) errors.push('hazard coverage is invalid');
    if (!hazard.revision?.nativeEventId) errors.push('hazard revision.nativeEventId is required');
    if (!hazard.severityEvidence?.mappingVersion) errors.push('hazard severity mappingVersion is required');
  }

  return errors.length
    ? { ok: false, errors }
    : { ok: true, event: value as unknown as GeoEvent };
}

export function validateGeoEvents(values: unknown[]): { events: GeoEvent[]; rejected: GeoEventAdapterIssue[] } {
  const events: GeoEvent[] = [];
  const rejected: GeoEventAdapterIssue[] = [];
  const seen = new Set<string>();
  values.forEach((value, index) => {
    const result = validateGeoEvent(value);
    if (!result.ok) {
      rejected.push({ index, code: 'invalid-event', message: result.errors.join('; ') });
      return;
    }
    if (seen.has(result.event.id)) {
      rejected.push({ index, code: 'duplicate-event', message: `duplicate id ${result.event.id}` });
      return;
    }
    seen.add(result.event.id);
    events.push(result.event);
  });
  return { events, rejected };
}
