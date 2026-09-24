import { describe, expect, it } from 'vitest';
import { mergeCanonicalHazardEvents, parseNaturalHazardsResponse } from './naturalHazards';

const validEarthquake = {
  id: 'earthquake:usgs:test',
  category: 'natural-hazard',
  title: 'M5.0 earthquake',
  severity: 'warning',
  occurredAt: '2026-07-29T11:00:00Z',
  updatedAt: '2026-07-29T11:01:00Z',
  geometry: { type: 'Point', coordinates: [120, 30] },
  locationPrecision: 'exact',
  locationLabel: 'Test region',
  sources: [{ provider: 'USGS', nativeId: 'test' }],
  limitations: [],
  relatedMarketIds: [],
  properties: {},
  hazardKind: 'earthquake',
  lifecycle: 'observed',
  coverage: { scope: 'global', label: 'USGS', isComplete: false, gaps: [] },
  severityEvidence: {
    provider: 'USGS',
    mappingVersion: 'hazard-severity.v1',
    reason: 'magnitude',
  },
  revision: { nativeEventId: 'test' },
  metrics: { kind: 'earthquake', magnitude: 5, depthKm: 10 },
};

function response(events: unknown[]) {
  return {
    schemaVersion: 'natural-hazards.v1',
    generatedAt: '2026-07-29T12:00:00Z',
    events,
    sources: [{
      key: 'usgs',
      status: 'ok',
      coverage: { scope: 'global', label: 'USGS feed', isComplete: false, gaps: [] },
    }],
    isPartial: false,
    errors: [],
    counts: { events: events.length, byHazardKind: { earthquake: events.length } },
  };
}

describe('natural hazard response boundary', () => {
  it('retains valid source-native hazard geometry', () => {
    const parsed = parseNaturalHazardsResponse(response([validEarthquake]));
    expect(parsed.events).toHaveLength(1);
    expect(parsed.events[0]).toMatchObject({
      id: 'earthquake:usgs:test',
      hazardKind: 'earthquake',
      geometry: { type: 'Point', coordinates: [120, 30] },
    });
    expect(parsed.rejected).toHaveLength(0);
  });

  it('isolates invalid coordinates without discarding valid events', () => {
    const parsed = parseNaturalHazardsResponse(response([
      validEarthquake,
      { ...validEarthquake, id: 'earthquake:usgs:bad', geometry: { type: 'Point', coordinates: [999, 30] } },
    ]));
    expect(parsed.events.map((event) => event.id)).toEqual(['earthquake:usgs:test']);
    expect(parsed.rejected).toHaveLength(1);
    expect(parsed.response.isPartial).toBe(true);
  });

  it('fails closed for an unsupported schema', () => {
    expect(() => parseNaturalHazardsResponse({
      ...response([]),
      schemaVersion: 'natural-hazards.v2',
    })).toThrow(/Unsupported natural hazards schema/);
  });

  it('fuses cross-provider records only when they share an explicit canonical identity', () => {
    const discovery = {
      ...validEarthquake,
      id: 'earthquake:eonet:discovery',
      updatedAt: '2026-07-29T11:02:00Z',
      sources: [{ provider: 'NASA EONET', nativeId: 'discovery' }],
      properties: {
        canonicalEventId: validEarthquake.id,
        mergeReason: 'explicit USGS event-page identifier',
      },
      revision: { nativeEventId: 'discovery' },
    };
    const unrelated = {
      ...validEarthquake,
      id: 'earthquake:eonet:nearby-but-unproven',
      sources: [{ provider: 'NASA EONET', nativeId: 'nearby-but-unproven' }],
      properties: {},
    };

    const merged = mergeCanonicalHazardEvents([
      validEarthquake,
      discovery,
      unrelated,
    ] as never);

    expect(merged).toHaveLength(2);
    const canonical = merged.find((event) => event.id === validEarthquake.id);
    expect(canonical?.sources.map((source) => source.provider)).toEqual(['USGS', 'NASA EONET']);
    expect(canonical?.properties.mergeReason).toBe('explicit USGS event-page identifier');
    expect(merged.some((event) => event.id === unrelated.id)).toBe(true);
  });
});
