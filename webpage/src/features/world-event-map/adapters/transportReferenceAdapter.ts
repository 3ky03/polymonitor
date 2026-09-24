import type { RuntimeGlobalTransportShippingPayload } from '@/types';
import type { GeoEvent, GeoEventAdapterResult } from '../domain/types';
import { validateGeoEvents } from '../domain/validation';
import {
  finiteNumber,
  isoTimestamp,
  nonEmpty,
  normalizedConfidence,
  normalizedFreshness,
  normalizedSeverity,
  normalizedSourceStatus,
  stableId,
} from './shared';

function point(lonValue: unknown, latValue: unknown): [number, number] | null {
  const lon = finiteNumber(lonValue);
  const lat = finiteNumber(latValue);
  return lon != null && lat != null && lon >= -180 && lon <= 180 && lat >= -90 && lat <= 90
    ? [lon, lat]
    : null;
}

function wrapLongitudeDelta(delta: number) {
  if (delta > 180) return delta - 360;
  if (delta < -180) return delta + 360;
  return delta;
}

function normalizeLongitude(longitude: number) {
  let normalized = ((longitude + 180) % 360 + 360) % 360 - 180;
  if (normalized === -180 && longitude > 0) normalized = 180;
  return normalized;
}

export function buildAviationArc(
  from: [number, number],
  to: [number, number],
  steps = 36,
): [number, number][] {
  const [fromLon, fromLat] = from;
  const [toLon, toLat] = to;
  const deltaLon = wrapLongitudeDelta(toLon - fromLon);
  const distance = Math.hypot(deltaLon, toLat - fromLat);
  const lift = Math.min(26, Math.max(5, distance * 0.18));
  const controlLon = fromLon + deltaLon * 0.5;
  const controlLat = (fromLat + toLat) * 0.5 + lift;
  return Array.from({ length: Math.max(2, steps) + 1 }, (_, index) => {
    const progress = index / Math.max(2, steps);
    const inverse = 1 - progress;
    const longitude = inverse * inverse * fromLon
      + 2 * inverse * progress * controlLon
      + progress * progress * (fromLon + deltaLon);
    const latitude = inverse * inverse * fromLat
      + 2 * inverse * progress * controlLat
      + progress * progress * toLat;
    return [longitude, Math.max(-85, Math.min(85, latitude))] as [number, number];
  });
}

export function splitAviationArc(points: [number, number][]): [number, number][][] {
  if (points.length < 2) return [];
  const segments: [number, number][][] = [];
  let current: [number, number][] = [[normalizeLongitude(points[0]![0]), points[0]![1]]];
  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1]!;
    const next = points[index]!;
    let crossing: 180 | -180 | null = null;
    if (previous[0] <= 180 && next[0] > 180) crossing = 180;
    else if (previous[0] >= -180 && next[0] < -180) crossing = -180;
    if (crossing != null) {
      const progress = (crossing - previous[0]) / (next[0] - previous[0] || 1);
      const crossingLatitude = previous[1] + (next[1] - previous[1]) * Math.max(0, Math.min(1, progress));
      current.push([crossing, crossingLatitude]);
      if (current.length >= 2) segments.push(current);
      current = [[crossing === 180 ? -180 : 180, crossingLatitude], [normalizeLongitude(next[0]), next[1]]];
    } else {
      current.push([normalizeLongitude(next[0]), next[1]]);
    }
  }
  if (current.length >= 2) segments.push(current);
  return segments;
}

export function adaptTransportReference(
  payload?: RuntimeGlobalTransportShippingPayload | null,
): GeoEventAdapterResult {
  const provider = nonEmpty(payload?.source) || 'global-transport-shipping';
  const sourceStatus = normalizedSourceStatus(payload?.status);
  const freshness = normalizedFreshness(payload?.freshness || payload?.cacheMode);
  const generatedAt = isoTimestamp(payload?.aviation?.generatedAt || payload?.generatedAt);
  const candidates: GeoEvent[] = [];
  const rejected: GeoEventAdapterResult['rejected'] = [];

  (payload?.aviation?.routes || []).forEach((route, index) => {
    const from = point(route.fromLon, route.fromLat);
    const to = point(route.toLon, route.toLat);
    const nativeId = nonEmpty(route.id) || [nonEmpty(route.fromCode), nonEmpty(route.toCode)].filter(Boolean).join('-');
    const id = stableId(provider, nativeId);
    if (!id || !from || !to) {
      rejected.push({ index, code: 'invalid-air-route', message: 'air route lacks stable identity or endpoint coordinates' });
      return;
    }
    const segments = splitAviationArc(buildAviationArc(from, to));
    segments.forEach((coordinates, segmentIndex) => {
      candidates.push({
        id: `${id}:route:${segmentIndex}`,
        category: 'infrastructure',
        title: `${nonEmpty(route.fromCode) || '—'} → ${nonEmpty(route.toCode) || '—'}`,
        severity: normalizedSeverity(route.status),
        updatedAt: generatedAt,
        geometry: { type: 'LineString', coordinates },
        locationPrecision: 'exact',
        locationLabel: nonEmpty(route.corridor),
        confidence: normalizedConfidence(route.confidence),
        sources: [{
          provider: nonEmpty(route.source) || provider,
          url: nonEmpty(route.sourceUrl) || nonEmpty(payload?.sourceUrl),
          nativeId,
          observedAt: generatedAt,
          freshness,
          status: sourceStatus,
        }],
        limitations: ['Static route topology is contextual reference data, not evidence that a flight is operating.'],
        relatedMarketIds: (route.relatedPolymarketMarketIds || []).map(String),
        properties: {
          mapEntity: 'air-route',
          routeId: nativeId,
          segmentIndex,
          segmentCount: segments.length,
          fromCode: nonEmpty(route.fromCode),
          toCode: nonEmpty(route.toCode),
          fromCountry: nonEmpty(route.fromCountry),
          toCountry: nonEmpty(route.toCountry),
          corridor: nonEmpty(route.corridor),
          trafficScore: finiteNumber(route.trafficScore),
          riskScore: finiteNumber(route.riskScore),
          status: nonEmpty(route.status),
          airline: nonEmpty(route.airline),
          layer: nonEmpty(route.layer),
          phase: finiteNumber(route.phase),
          speed: finiteNumber(route.speed),
          riskSources: route.riskSources || [],
          riskReason: nonEmpty(route.riskReason),
          trend: route.trend || [],
        },
      });
    });
  });

  (payload?.aviation?.hubs || []).forEach((hub, index) => {
    const coordinates = point(hub.lon, hub.lat);
    const nativeId = nonEmpty(hub.code);
    const id = stableId(provider, nativeId);
    if (!id || !coordinates) {
      rejected.push({ index, code: 'invalid-air-hub', message: 'air hub lacks code or coordinates' });
      return;
    }
    candidates.push({
      id: `${id}:hub`,
      category: 'infrastructure',
      title: nonEmpty(hub.name) || nativeId || 'Air hub',
      severity: normalizedSeverity(hub.status),
      updatedAt: generatedAt,
      geometry: { type: 'Point', coordinates },
      locationPrecision: 'exact',
      locationLabel: [nonEmpty(hub.city), nonEmpty(hub.country)].filter(Boolean).join(', '),
      sources: [{
        provider: nonEmpty(hub.source) || provider,
        url: nonEmpty(hub.sourceUrl) || nonEmpty(payload?.sourceUrl),
        nativeId,
        observedAt: generatedAt,
        freshness,
        status: sourceStatus,
      }],
      limitations: ['Airport risk is contextual and does not by itself indicate closure or delay.'],
      relatedMarketIds: [],
      properties: {
        mapEntity: 'air-hub',
        code: nativeId,
        city: nonEmpty(hub.city),
        country: nonEmpty(hub.country),
        routeCount: finiteNumber(hub.routeCount),
        riskScore: finiteNumber(hub.riskScore),
        status: nonEmpty(hub.status),
      },
    });
  });

  (payload?.aviation?.flights || []).forEach((flight, index) => {
    const from = point(flight.fromLon, flight.fromLat);
    const to = point(flight.toLon, flight.toLat);
    const nativeId = nonEmpty(flight.id) || `${nonEmpty(flight.fromCode) || 'from'}-${nonEmpty(flight.toCode) || 'to'}-${index}`;
    const id = stableId(provider, nativeId);
    if (!id || !from || !to) return;
    const segments = splitAviationArc(buildAviationArc(from, to));
    segments.forEach((coordinates, segmentIndex) => {
      candidates.push({
        id: `${id}:seeded-flight:${segmentIndex}`,
        category: 'infrastructure',
        title: nonEmpty(flight.callsign) || `${nonEmpty(flight.fromCode) || '—'} → ${nonEmpty(flight.toCode) || '—'}`,
        severity: normalizedSeverity(flight.status),
        updatedAt: generatedAt,
        geometry: { type: 'LineString', coordinates },
        locationPrecision: 'exact',
        sources: [{
          provider: nonEmpty(flight.source) || provider,
          url: nonEmpty(flight.sourceUrl) || nonEmpty(payload?.sourceUrl),
          nativeId,
          observedAt: generatedAt,
          freshness,
          status: sourceStatus,
        }],
        limitations: ['Seeded flight motion is illustrative and is not a live aircraft position.'],
        relatedMarketIds: [],
        properties: {
          mapEntity: 'air-flight',
          flightId: nativeId,
          segmentIndex,
          segmentCount: segments.length,
          fromCode: nonEmpty(flight.fromCode),
          toCode: nonEmpty(flight.toCode),
          phase: finiteNumber(flight.phase),
          speed: finiteNumber(flight.speed),
          riskScore: finiteNumber(flight.riskScore),
          riskSources: flight.riskSources || [],
          riskReason: nonEmpty(flight.riskReason),
          layer: nonEmpty(flight.layer),
          status: nonEmpty(flight.status),
        },
      });
    });
  });

  (payload?.aviation?.liveFlights || []).forEach((flight, index) => {
    const coordinates = point(flight.lon, flight.lat);
    const nativeId = nonEmpty(flight.id) || nonEmpty(flight.icao24);
    const id = stableId(nonEmpty(flight.source) || provider, nativeId);
    if (!id || !coordinates) {
      if (nativeId) rejected.push({ index, code: 'invalid-live-aircraft', message: 'live aircraft coordinates are invalid' });
      return;
    }
    candidates.push({
      id: `${id}:live-aircraft`,
      category: 'infrastructure',
      title: nonEmpty(flight.callsign) || nativeId || 'Live aircraft',
      severity: normalizedSeverity(flight.status),
      updatedAt: isoTimestamp(flight.updatedAt) || generatedAt,
      geometry: { type: 'Point', coordinates },
      locationPrecision: 'exact',
      locationLabel: nonEmpty(flight.regionLabel) || nonEmpty(flight.region),
      sources: [{
        provider: nonEmpty(flight.source) || provider,
        url: nonEmpty(flight.sourceUrl),
        nativeId,
        observedAt: isoTimestamp(flight.updatedAt) || generatedAt,
        freshness,
        status: sourceStatus,
      }],
      limitations: ['Aircraft surveillance coverage, update delay and regional availability vary by provider.'],
      relatedMarketIds: [],
      properties: {
        mapEntity: 'live-aircraft',
        icao24: nonEmpty(flight.icao24),
        callsign: nonEmpty(flight.callsign),
        originCountry: nonEmpty(flight.originCountry),
        region: nonEmpty(flight.region),
        regionLabel: nonEmpty(flight.regionLabel),
        baroAltitude: finiteNumber(flight.baroAltitude),
        velocity: finiteNumber(flight.velocity),
        heading: finiteNumber(flight.heading),
        verticalRate: finiteNumber(flight.verticalRate),
        onGround: Boolean(flight.onGround),
        riskScore: finiteNumber(flight.riskScore),
        status: nonEmpty(flight.status),
      },
    });
  });

  const validated = validateGeoEvents(candidates);
  return { events: validated.events, rejected: [...rejected, ...validated.rejected] };
}
