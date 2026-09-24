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

export function adaptTransportDisruptions(
  payload?: RuntimeGlobalTransportShippingPayload | null,
): GeoEventAdapterResult {
  const provider = nonEmpty(payload?.source) || 'global-transport-shipping';
  const candidates: GeoEvent[] = [];
  const rejected: GeoEventAdapterResult['rejected'] = [];
  (payload?.items || []).forEach((item, index) => {
    const rawSeverity = normalizedSeverity(item.severity);
    if (rawSeverity === 'info') return;
    const id = stableId(provider, item.id);
    if (!id) {
      rejected.push({ index, code: 'missing-stable-id', message: 'transport event has no provider-native id' });
      return;
    }
    const evidence = item.evidence && typeof item.evidence === 'object' ? item.evidence : {};
    const longitude = finiteNumber(evidence.lon ?? evidence.longitude);
    const latitude = finiteNumber(evidence.lat ?? evidence.latitude);
    const hasPoint = longitude != null
      && latitude != null
      && longitude >= -180
      && longitude <= 180
      && latitude >= -90
      && latitude <= 90;
    candidates.push({
      id,
      category: 'transport-disruption',
      title: nonEmpty(item.title) || nonEmpty(item.entity) || 'Transport disruption',
      summary: nonEmpty(item.summary),
      severity: rawSeverity,
      occurredAt: isoTimestamp(item.eventTime),
      geometry: hasPoint ? { type: 'Point', coordinates: [longitude, latitude] } : undefined,
      locationPrecision: hasPoint ? 'exact' : nonEmpty(item.country) ? 'country' : 'unknown',
      locationLabel: nonEmpty(item.country),
      confidence: normalizedConfidence(item.confidence),
      sources: [{
        provider,
        url: nonEmpty(item.sourceUrl) || nonEmpty(payload?.sourceUrl),
        nativeId: nonEmpty(item.id),
        observedAt: isoTimestamp(item.eventTime),
        freshness: normalizedFreshness(payload?.freshness || payload?.cacheMode),
        status: normalizedSourceStatus(payload?.status),
      }],
      limitations: hasPoint ? [] : ['No verified point or area geometry was supplied for this disruption.'],
      relatedMarketIds: (item.relatedPolymarketMarketIds || []).map(String),
      properties: {
        mapEntity: 'transport-disruption',
        topic: nonEmpty(item.topic),
        metric: finiteNumber(item.metric),
        metricLabel: nonEmpty(item.metricLabel),
        tags: item.tags || [],
      },
    });
  });
  const validated = validateGeoEvents(candidates);
  return { events: validated.events, rejected: [...rejected, ...validated.rejected] };
}
