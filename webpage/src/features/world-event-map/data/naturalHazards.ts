import type {
  GeoEventAdapterIssue,
  HazardDetailResponse,
  HazardEvent,
  HazardMapResponse,
  HazardMapSource,
} from '../domain/types';
import { isHazardGeoEvent } from '../config/layerRegistry';
import { validateGeoEvents } from '../domain/validation';

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function isHazardSource(value: unknown): value is HazardMapSource {
  if (!isRecord(value) || typeof value.key !== 'string') return false;
  if (!['ok', 'partial', 'degraded', 'error'].includes(String(value.status || ''))) return false;
  const coverage = value.coverage;
  return isRecord(coverage)
    && typeof coverage.label === 'string'
    && typeof coverage.scope === 'string'
    && typeof coverage.isComplete === 'boolean'
    && Array.isArray(coverage.gaps)
    && coverage.gaps.every((gap) => typeof gap === 'string');
}

export type ParsedNaturalHazards = {
  response: HazardMapResponse;
  events: HazardEvent[];
  rejected: GeoEventAdapterIssue[];
};

function hazardCanonicalId(event: HazardEvent) {
  return String(event.properties.canonicalEventId || event.id);
}

function canonicalPrimaryRank(event: HazardEvent) {
  const providers = event.sources.map((source) => source.provider.toLowerCase()).join(' ');
  const authoritativeEarthquake = event.hazardKind === 'earthquake' && providers.includes('usgs') ? 2 : 1;
  return [authoritativeEarthquake, String(event.updatedAt || event.occurredAt || '')] as const;
}

/** Fuse only events carrying the same explicit canonical identity. */
export function mergeCanonicalHazardEvents(events: readonly HazardEvent[]) {
  const groups = new Map<string, HazardEvent[]>();
  for (const event of events) {
    const canonicalId = hazardCanonicalId(event);
    groups.set(canonicalId, [...(groups.get(canonicalId) || []), event]);
  }
  return [...groups.entries()].map(([canonicalId, group]) => {
    const primary = group.slice().sort((left, right) => {
      const leftRank = canonicalPrimaryRank(left);
      const rightRank = canonicalPrimaryRank(right);
      return rightRank[0] - leftRank[0] || rightRank[1].localeCompare(leftRank[1]);
    })[0]!;
    if (group.length === 1 && primary.id === canonicalId) return primary;
    const seenSources = new Set<string>();
    const sources = group.flatMap((event) => event.sources).filter((source) => {
      const key = `${source.provider}:${source.nativeId || ''}`;
      if (seenSources.has(key)) return false;
      seenSources.add(key);
      return true;
    });
    const sourceProvenance = group.flatMap((event) => {
      const declared = event.properties.sourceProvenance;
      if (Array.isArray(declared)) return declared;
      return event.sources.map((source) => ({
        provider: source.provider,
        nativeEventId: event.revision.nativeEventId || source.nativeId || event.id,
        revisionAt: event.revision.revisionAt || event.updatedAt || '',
      }));
    });
    return {
      ...primary,
      id: canonicalId,
      sources,
      limitations: [...new Set(group.flatMap((event) => event.limitations))],
      properties: {
        ...primary.properties,
        canonicalEventId: canonicalId,
        mergeReason: String(
          group.find((event) => event.properties.mergeReason)?.properties.mergeReason
          || 'explicit canonical identity',
        ),
        sourceProvenance,
      },
    } satisfies HazardEvent;
  }).sort((left, right) => String(
    right.updatedAt || right.occurredAt || '',
  ).localeCompare(String(left.updatedAt || left.occurredAt || '')));
}

export function parseNaturalHazardsResponse(value: unknown): ParsedNaturalHazards {
  if (!isRecord(value)) throw new Error('Natural hazards response must be an object');
  if (value.schemaVersion !== 'natural-hazards.v1' && value.schemaVersion !== 'natural-hazards-map.v1') {
    throw new Error(`Unsupported natural hazards schema: ${String(value.schemaVersion || 'missing')}`);
  }
  if (!Array.isArray(value.events)) throw new Error('Natural hazards response is missing events');
  if (!Array.isArray(value.sources) || !value.sources.every(isHazardSource)) {
    throw new Error('Natural hazards response contains invalid source status');
  }
  const validated = validateGeoEvents(value.events);
  const events = validated.events.filter(isHazardGeoEvent);
  const nonHazardCount = validated.events.length - events.length;
  const rejected = [...validated.rejected];
  if (nonHazardCount) {
    rejected.push({
      index: -1,
      code: 'invalid-hazard-event',
      message: `${nonHazardCount} validated event(s) did not declare a hazard kind`,
    });
  }
  const counts = isRecord(value.counts) ? value.counts : {};
  const response: HazardMapResponse = {
    schemaVersion: value.schemaVersion,
    generatedAt: typeof value.generatedAt === 'string' ? value.generatedAt : new Date(0).toISOString(),
    events,
    sources: value.sources as HazardMapSource[],
    isPartial: Boolean(value.isPartial) || rejected.length > 0,
    errors: Array.isArray(value.errors)
      ? value.errors.filter(isRecord).map((error) => ({
          source: String(error.source || 'unknown'),
          code: error.code == null ? null : String(error.code),
        }))
      : [],
    counts: {
      events: events.length,
      byHazardKind: isRecord(counts.byHazardKind)
        ? counts.byHazardKind as HazardMapResponse['counts']['byHazardKind']
        : {},
    },
    meta: isRecord(value.meta) ? {
      source: typeof value.meta.source === 'string' ? value.meta.source : undefined,
      geometryMode: value.meta.geometryMode === 'full' ? 'full' : value.meta.geometryMode === 'simplified' ? 'simplified' : undefined,
      geometryZoom: typeof value.meta.geometryZoom === 'number' ? value.meta.geometryZoom : undefined,
      detailEndpoint: typeof value.meta.detailEndpoint === 'string' ? value.meta.detailEndpoint : undefined,
      fullSchemaVersion: typeof value.meta.fullSchemaVersion === 'string' ? value.meta.fullSchemaVersion : undefined,
    } : undefined,
  };
  return { response, events, rejected };
}

export function parseNaturalHazardDetail(value: unknown): HazardDetailResponse {
  if (!isRecord(value) || value.schemaVersion !== 'natural-hazard-detail.v1') {
    throw new Error('Unsupported natural hazard detail response');
  }
  const validated = validateGeoEvents([value.event]);
  const event = validated.events.find(isHazardGeoEvent);
  if (!event || validated.rejected.length) {
    throw new Error('Natural hazard detail contains an invalid event');
  }
  return {
    schemaVersion: 'natural-hazard-detail.v1',
    generatedAt: typeof value.generatedAt === 'string' ? value.generatedAt : new Date(0).toISOString(),
    event,
  };
}
