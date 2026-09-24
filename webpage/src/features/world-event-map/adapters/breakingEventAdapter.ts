import type { RuntimeBreakingEventRadarPayload } from '@/types';
import type { CountryGeometryIndex } from '../domain/countryGeometry';
import type { GeoEvent, GeoEventAdapterResult } from '../domain/types';
import { validateGeoEvents } from '../domain/validation';
import {
  isoTimestamp,
  nonEmpty,
  normalizedConfidence,
  normalizedFreshness,
  normalizedSeverity,
  normalizedSourceStatus,
  stableId,
} from './shared';

export function adaptBreakingEventPayload(payload?: RuntimeBreakingEventRadarPayload | null): GeoEventAdapterResult {
  const provider = nonEmpty(payload?.source) || 'breaking-event-radar';
  const candidates: GeoEvent[] = [];
  const missingId: GeoEventAdapterResult['rejected'] = [];
  (payload?.items || []).forEach((item, index) => {
    const id = stableId(provider, item.id);
    if (!id) {
      missingId.push({ index, code: 'missing-stable-id', message: 'breaking event has no provider-native id' });
      return;
    }
    const marketIds = [
      ...(item.relatedPolymarketMarketIds || []),
      ...(item.markets || []).flatMap((market) => market.marketId == null ? [] : [market.marketId]),
    ];
    candidates.push({
      id,
      category: 'intel',
      title: nonEmpty(item.title) || nonEmpty(item.entity) || nonEmpty(item.topic) || 'Breaking event',
      summary: nonEmpty(item.summary),
      severity: normalizedSeverity(item.severity),
      occurredAt: isoTimestamp(item.eventTime),
      locationPrecision: nonEmpty(item.country) ? 'country' : 'unknown',
      locationLabel: nonEmpty(item.country),
      confidence: normalizedConfidence(item.confidence),
      sources: [{
        provider: nonEmpty(item.source) || provider,
        url: nonEmpty(item.sourceUrl) || nonEmpty(payload?.sourceUrl),
        nativeId: nonEmpty(item.id),
        observedAt: isoTimestamp(item.eventTime),
        freshness: normalizedFreshness(payload?.freshness || payload?.cacheMode),
        status: normalizedSourceStatus(payload?.status),
      }],
      limitations: [
        'Country-level intelligence remains non-spatial until a verified region or polygon is available.',
      ],
      relatedMarketIds: [...new Set(marketIds.map(String))],
      properties: {
        mapEntity: 'intel-hotspot',
        topic: nonEmpty(item.topic),
        entity: nonEmpty(item.entity),
        country: nonEmpty(item.country),
        evidenceType: nonEmpty(item.evidenceType),
        sourceDiversity: item.sourceDiversity,
        velocityScore: item.velocityScore,
        tags: item.tags || [],
      },
    });
  });
  const validated = validateGeoEvents(candidates);
  return { events: validated.events, rejected: [...missingId, ...validated.rejected] };
}

function evidenceArticleCount(evidence: Record<string, unknown> | null | undefined) {
  return Array.isArray(evidence?.articles) ? evidence.articles.length : 0;
}

export function adaptBreakingEventMapPayload(
  payload: RuntimeBreakingEventRadarPayload | null | undefined,
  countries: CountryGeometryIndex | null,
): GeoEventAdapterResult {
  if (!payload || !countries) return { events: [], rejected: [] };
  const base = adaptBreakingEventPayload(payload);
  const accepted: GeoEvent[] = [];
  const rejected = [...base.rejected];
  base.events.forEach((event, index) => {
    const rawItem = payload.items.find((item) => stableId(nonEmpty(payload.source) || 'breaking-event-radar', item.id) === event.id);
    const country = countries.resolve(event.locationLabel);
    if (!country) {
      rejected.push({
        index,
        code: 'unresolved-country-geometry',
        message: `intel record country is global, unknown or not in the verified country geometry: ${event.locationLabel || 'unknown'}`,
      });
      return;
    }
    const confidence = event.confidence ?? 0;
    const sourceDiversity = Number(rawItem?.sourceDiversity || 0);
    const articleCount = evidenceArticleCount(rawItem?.evidence);
    const corroborated = sourceDiversity >= 2 && articleCount >= 2;
    if (confidence < 0.6 || !corroborated || !event.occurredAt) {
      rejected.push({
        index,
        code: 'insufficient-intel-evidence',
        message: `intel map evidence requires time, confidence >= 0.60 and at least two articles from two sources (confidence=${confidence.toFixed(2)}, sources=${sourceDiversity}, articles=${articleCount})`,
      });
      return;
    }
    accepted.push({
      ...event,
      geometry: country.geometry,
      countryCode: country.iso2,
      locationPrecision: 'country',
      locationLabel: country.name,
      limitations: [
        'The polygon identifies the affected country, not the precise incident footprint.',
        'News velocity and confidence may change as additional sources arrive.',
      ],
      properties: {
        ...event.properties,
        country: country.name,
        countryCode: country.iso2,
        evidenceArticleCount: articleCount,
        evidenceGate: 'intel-map-evidence.v1',
      },
    });
  });
  const validated = validateGeoEvents(accepted);
  return { events: validated.events, rejected: [...rejected, ...validated.rejected] };
}
