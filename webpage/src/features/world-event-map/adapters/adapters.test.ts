import { describe, expect, it } from 'vitest';
import type { FeatureCollection } from 'geojson';
import { buildCountryGeometryIndex } from '../domain/countryGeometry';
import { adaptBreakingEventMapPayload, adaptBreakingEventPayload } from './breakingEventAdapter';
import { adaptGeoShockCountryRiskPayload, adaptGeoShockPayload } from './geoShockAdapter';
import { adaptTransportDisruptions } from './transportDisruptionAdapter';
import { adaptTransportReference } from './transportReferenceAdapter';

describe('World Event Map adapters', () => {
  const countries = buildCountryGeometryIndex({
    type: 'FeatureCollection',
    features: [{
      type: 'Feature',
      properties: {
        name: 'Japan',
        'ISO3166-1-Alpha-2': 'JP',
        'ISO3166-1-Alpha-3': 'JPN',
      },
      geometry: {
        type: 'Polygon',
        coordinates: [[[130, 30], [145, 30], [145, 45], [130, 30]]],
      },
    }, {
      type: 'Feature',
      properties: {
        name: 'Ukraine',
        'ISO3166-1-Alpha-2': 'UA',
        'ISO3166-1-Alpha-3': 'UKR',
      },
      geometry: {
        type: 'Polygon',
        coordinates: [[[22, 44], [40, 44], [40, 53], [22, 44]]],
      },
    }],
  } satisfies FeatureCollection);

  it('normalizes UCDP data into stable canonical conflict events', () => {
    const result = adaptGeoShockPayload({
      source: 'UCDP',
      status: 'ok',
      items: [{
        id: '123',
        headline: 'Conflict fixture',
        country: 'Ukraine',
        latitude: '49.0',
        longitude: '32.0',
        violenceType: 1,
        deathsBest: 4,
        occurredAt: '2026-07-28T00:00:00Z',
      }],
    });
    expect(result.rejected).toHaveLength(0);
    expect(result.events).toEqual([
      expect.objectContaining({
        id: 'UCDP:123',
        category: 'conflict',
        geometry: { type: 'Point', coordinates: [32, 49] },
        locationPrecision: 'exact',
        properties: expect.objectContaining({ deathsBest: 4, violenceType: '1' }),
      }),
    ]);
  });

  it('keeps country-level records non-spatial instead of inventing a coordinate', () => {
    const result = adaptGeoShockPayload({
      source: 'UCDP',
      items: [{ id: 'country-only', country: 'Sudan', headline: 'Country-level fixture' }],
    });
    expect(result.events[0]).toMatchObject({
      locationPrecision: 'country',
      geometry: undefined,
    });
  });

  it('rejects records without provider-native identity', () => {
    const result = adaptGeoShockPayload({
      source: 'UCDP',
      items: [{ headline: 'No id', latitude: 1, longitude: 2 }],
    });
    expect(result.events).toHaveLength(0);
    expect(result.rejected[0]?.code).toBe('missing-stable-id');
  });

  it('does not turn breaking-news country labels into fake markers', () => {
    const result = adaptBreakingEventPayload({
      source: 'Breaking Radar',
      items: [{
        id: 'news-1',
        title: 'Verified country-level event',
        country: 'Japan',
        severity: 'watch',
        relatedPolymarketMarketIds: [42],
      }],
    });
    expect(result.events[0]).toMatchObject({
      category: 'intel',
      locationPrecision: 'country',
      relatedMarketIds: ['42'],
    });
    expect(result.events[0]?.geometry).toBeUndefined();
  });

  it('renders only corroborated, time-bounded Intel as a country polygon', () => {
    const result = adaptBreakingEventMapPayload({
      source: 'Breaking Radar',
      status: 'ok',
      items: [{
        id: 'news-2',
        title: 'Corroborated event',
        country: 'Japan',
        eventTime: '2026-07-29T00:00:00Z',
        severity: 'watch',
        confidence: 0.72,
        sourceDiversity: 3,
        evidence: { articles: [{}, {}, {}] },
      }],
    }, countries);
    expect(result.rejected).toHaveLength(0);
    expect(result.events[0]).toMatchObject({
      category: 'intel',
      countryCode: 'JP',
      locationPrecision: 'country',
      geometry: { type: 'Polygon' },
      properties: { evidenceGate: 'intel-map-evidence.v1', evidenceArticleCount: 3 },
    });
  });

  it('rejects low-confidence proxies and global Intel instead of drawing them', () => {
    const result = adaptBreakingEventMapPayload({
      source: 'Breaking Radar',
      items: [{
        id: 'proxy',
        country: 'Global',
        eventTime: '2026-07-29T00:00:00Z',
        confidence: 0.43,
        sourceDiversity: 0,
        evidence: { pageviews: [{}], articles: [] },
      }],
    }, countries);
    expect(result.events).toHaveLength(0);
    expect(result.rejected[0]?.code).toBe('unresolved-country-geometry');
  });

  it('renders verified geo-shock targets as countries, never capital markers', () => {
    const result = adaptGeoShockCountryRiskPayload({
      source: 'OFAC / Federal Register / UCDP',
      status: 'partial',
      generatedAt: '2026-07-29T00:00:00Z',
      ofacRecordCountTotal: 120,
      summary: { newSanctionsCount: 2 },
      sanctionsTargetBreakdown: [{
        label: 'Ukraine',
        count: 18,
        latestHeadline: 'Verified source evidence',
        latestOccurredAt: '2026-07-28T00:00:00Z',
        latestSource: 'Federal Register',
      }],
    }, countries);
    expect(result.rejected).toHaveLength(0);
    expect(result.events[0]).toMatchObject({
      id: 'geo-sanctions-shock:UA',
      category: 'sanctions',
      countryCode: 'UA',
      geometry: { type: 'Polygon' },
      properties: {
        riskMappingVersion: 'geo-shock-country-risk.v1',
        sanctionsEvidenceCount: 18,
        countryRiskEvidenceCount: 0,
        sourceContract: 'geo-shock-split-evidence.v1',
      },
    });
  });

  it('keeps sanctions and conflict evidence as separate country metrics', () => {
    const result = adaptGeoShockCountryRiskPayload({
      sanctionsTargetBreakdown: [{
        label: 'Ukraine',
        count: 3,
        latestSource: 'OFAC SDN',
      }],
      countryRiskBreakdown: [{
        label: 'Ukraine',
        count: 28,
        latestSource: 'UCDP',
      }],
    }, countries);
    expect(result.events).toHaveLength(1);
    expect(result.events[0]).toMatchObject({
      category: 'sanctions',
      properties: {
        sanctionsEvidenceCount: 3,
        countryRiskEvidenceCount: 28,
      },
      sources: [
        expect.objectContaining({ provider: 'OFAC SDN' }),
        expect.objectContaining({ provider: 'UCDP' }),
      ],
    });
  });

  it('rejects non-country sanctions targets', () => {
    const result = adaptGeoShockCountryRiskPayload({
      targetBreakdown: [{ label: 'Acme Corporation', count: 4 }],
    }, countries);
    expect(result.events).toHaveLength(0);
    expect(result.rejected[0]?.code).toBe('unresolved-country-geometry');
  });

  it('only emits transport disruptions at watch severity or above', () => {
    const result = adaptTransportDisruptions({
      source: 'Transport',
      items: [
        { id: 'normal', title: 'Normal route', severity: 'normal' },
        {
          id: 'closure',
          title: 'Airport closure',
          severity: 'alert',
          evidence: { lon: 103.99, lat: 1.36 },
        },
      ],
    });
    expect(result.events).toHaveLength(1);
    expect(result.events[0]).toMatchObject({
      id: 'Transport:closure',
      category: 'transport-disruption',
      geometry: { type: 'Point', coordinates: [103.99, 1.36] },
    });
  });

  it('normalizes optional route topology into canonical reference geometry', () => {
    const result = adaptTransportReference({
      source: 'OpenFlights',
      aviation: {
        routes: [{
          id: 'SIN-LHR',
          fromCode: 'SIN',
          toCode: 'LHR',
          fromLon: 103.99,
          fromLat: 1.36,
          toLon: -0.45,
          toLat: 51.47,
          layer: 'trunk',
        }],
        hubs: [{ code: 'SIN', lon: 103.99, lat: 1.36 }],
      },
      items: [],
    });
    expect(result.rejected).toHaveLength(0);
    expect(result.events.map((item) => item.properties.mapEntity)).toEqual(['air-route', 'air-hub']);
    expect(result.events[0]?.geometry).toMatchObject({ type: 'LineString' });
    expect(result.events[0]?.geometry?.type === 'LineString'
      ? result.events[0].geometry.coordinates.length
      : 0).toBeGreaterThan(20);
    expect(result.events[0]?.properties).toMatchObject({
      routeId: 'SIN-LHR',
      segmentIndex: 0,
      layer: 'trunk',
    });
  });
});
