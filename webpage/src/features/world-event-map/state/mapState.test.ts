import { describe, expect, it } from 'vitest';
import { worldEventMapReducer } from './mapReducer';
import { defaultWorldEventMapState } from './mapState';
import { filterWorldEventMapEvents, filterWorldEventMapEventsForLayers } from './selectors';
import {
  parseWorldEventMapState,
  readStoredWorldEventMapState,
  serializeWorldEventMapUrl,
} from './urlState';
import type { GeoEvent, HazardEvent } from '../domain/types';

describe('World Event Map state', () => {
  it('defaults to hazards, risk, and a bounded trunk aviation reference', () => {
    const defaults = defaultWorldEventMapState();
    expect(defaults.activeLayerIds).toEqual([
      'weather-alerts',
      'earthquakes-volcanoes',
      'wildfires',
      'extreme-temperature',
      'climate-anomalies',
      'air-routes',
      'ucdp',
      'sanctions-country-risk',
    ]);
    expect(defaults.activeLayerIds).toContain('air-routes');
    expect(defaults.aviationLens).toBe('trunk');
    expect(defaults.timeRange).toBe('7d');
  });

  it('applies URL state after stored state and round-trips canonical fields', () => {
    const stored = readStoredWorldEventMapState(JSON.stringify({
      region: 'asia',
      center: { lon: 100, lat: 20 },
      zoom: 3,
      activeLayerIds: ['air-routes'],
      timeRange: '7d',
      severities: ['critical'],
      aviationLens: 'trunk',
      aviationRiskSource: 'weather',
    }));
    const fromUrl = parseWorldEventMapState(
      '?region=eu&center=11.5,48.2&zoom=4.25&layers=ucdp&time=24h&severity=warning,critical&event=UCDP%3A1&air=watch&airRisk=conflict',
      stored,
    );
    expect(fromUrl).toMatchObject({
      region: 'eu',
      center: { lon: 11.5, lat: 48.2 },
      zoom: 4.25,
      activeLayerIds: ['ucdp'],
      timeRange: '24h',
      severities: ['warning', 'critical'],
      selectedEventId: 'UCDP:1',
      aviationLens: 'watch',
      aviationRiskSource: 'conflict',
    });
    const serialized = serializeWorldEventMapUrl(fromUrl, 'https://example.test/?view=2d');
    expect(parseWorldEventMapState(new URL(serialized).search)).toMatchObject(fromUrl);
    expect(new URL(serialized).searchParams.get('view')).toBe('2d');
  });

  it('treats a complete shared map snapshot as authoritative over stale local investigation state', () => {
    const stale = {
      ...defaultWorldEventMapState(),
      selectedEventId: 'earthquake:stale',
      countryCode: 'US',
      basemapTheme: 'positron' as const,
    };
    const shared = parseWorldEventMapState(
      '?center=-122.1,37.4&zoom=2.2&layers=earthquakes-volcanoes&time=all',
      stale,
    );
    expect(shared).toMatchObject({
      center: { lon: -122.1, lat: 37.4 },
      zoom: 2.2,
      activeLayerIds: ['earthquakes-volcanoes'],
      timeRange: 'all',
      selectedEventId: null,
      countryCode: null,
      basemapTheme: 'dark',
    });
    const partial = parseWorldEventMapState('?region=eu', stale);
    expect(partial.selectedEventId).toBe('earthquake:stale');
    expect(partial.countryCode).toBe('US');
  });

  it('ignores unknown layers, regions and malformed coordinates', () => {
    const defaults = defaultWorldEventMapState();
    const parsed = parseWorldEventMapState(
      '?region=moon&center=999,999&layers=fake&time=forever&severity=catastrophic',
      defaults,
    );
    expect(parsed).toMatchObject({
      region: defaults.region,
      center: defaults.center,
      activeLayerIds: defaults.activeLayerIds,
      timeRange: defaults.timeRange,
      severities: defaults.severities,
    });
  });

  it('region actions update the real camera and reset affects only map state', () => {
    const initial = defaultWorldEventMapState();
    const asia = worldEventMapReducer(initial, { type: 'set-region', region: 'asia' });
    expect(asia).toMatchObject({ region: 'asia', center: { lon: 101, lat: 29 }, zoom: 2.35 });
    const withoutRoutes = worldEventMapReducer(asia, { type: 'toggle-layer', layerId: 'air-routes' });
    expect(withoutRoutes.activeLayerIds).not.toContain('air-routes');
    const withRoutes = worldEventMapReducer(withoutRoutes, { type: 'toggle-layer', layerId: 'air-routes' });
    expect(withRoutes.activeLayerIds).toContain('air-routes');
    const trunk = worldEventMapReducer(withRoutes, { type: 'set-aviation-lens', lens: 'trunk' });
    expect(trunk.aviationLens).toBe('trunk');
    expect(worldEventMapReducer(withRoutes, { type: 'reset' })).toEqual(defaultWorldEventMapState());
  });

  it('filters by severity and a deterministic time window', () => {
    const now = Date.parse('2026-07-29T12:00:00Z');
    const base: GeoEvent = {
      id: 'fixture:1',
      category: 'conflict',
      title: 'Fixture',
      severity: 'warning',
      occurredAt: '2026-07-29T11:30:00Z',
      locationPrecision: 'unknown',
      sources: [{ provider: 'fixture' }],
      limitations: [],
      relatedMarketIds: [],
      properties: {},
    };
    expect(filterWorldEventMapEvents(
      [base, { ...base, id: 'fixture:2', severity: 'info' }],
      { timeRange: '1h', severities: ['warning'] },
      now,
    ).map((event) => event.id)).toEqual(['fixture:1']);
    expect(filterWorldEventMapEvents(
      [{ ...base, occurredAt: '2026-07-29T08:00:00Z' }],
      { timeRange: '1h', severities: ['warning'] },
      now,
    )).toHaveLength(0);
  });

  it('filters broad hazard categories through the active hazard layer mapping', () => {
    const earthquake = {
      id: 'earthquake:usgs:test',
      category: 'natural-hazard' as const,
      title: 'Earthquake',
      severity: 'warning' as const,
      locationPrecision: 'exact' as const,
      sources: [{ provider: 'USGS' }],
      limitations: [],
      relatedMarketIds: [],
      properties: {},
      hazardKind: 'earthquake' as const,
      lifecycle: 'observed' as const,
      coverage: { scope: 'global' as const, label: 'USGS', isComplete: false, gaps: [] },
      severityEvidence: { provider: 'USGS', mappingVersion: 'v1', reason: 'magnitude' },
      revision: { nativeEventId: 'test' },
      metrics: { kind: 'earthquake' as const, magnitude: 5 },
    };
    expect(filterWorldEventMapEventsForLayers(
      [earthquake],
      { activeLayerIds: ['earthquakes-volcanoes'], timeRange: 'all', severities: ['warning'] },
    )).toHaveLength(1);
    expect(filterWorldEventMapEventsForLayers(
      [earthquake],
      { activeLayerIds: ['weather-alerts'], timeRange: 'all', severities: ['warning'] },
    )).toHaveLength(0);
  });

  it('excludes expired, ended and cancelled hazards even when their update is recent', () => {
    const now = Date.parse('2026-07-29T12:00:00Z');
    const base: HazardEvent = {
      id: 'flood:nws:test',
      category: 'weather',
      title: 'Flood warning',
      severity: 'warning',
      updatedAt: '2026-07-29T11:30:00Z',
      expiresAt: '2026-07-29T13:00:00Z',
      locationPrecision: 'region',
      sources: [{ provider: 'NWS' }],
      limitations: [],
      relatedMarketIds: [],
      properties: {},
      hazardKind: 'flood',
      lifecycle: 'active',
      coverage: { scope: 'provider-area', label: 'NWS', isComplete: false, gaps: [] },
      severityEvidence: { provider: 'NWS', mappingVersion: 'v1', reason: 'CAP' },
      revision: { nativeEventId: 'test', cancelled: false },
      metrics: { kind: 'weather-alert' },
    };
    const state = { activeLayerIds: ['weather-alerts'], timeRange: '24h' as const, severities: ['warning' as const] };
    const expired: HazardEvent = { ...base, id: 'expired', expiresAt: '2026-07-29T11:59:00Z' };
    const ended: HazardEvent = { ...base, id: 'ended', lifecycle: 'ended' };
    const cancelled: HazardEvent = {
      ...base,
      id: 'cancelled',
      revision: { ...base.revision, cancelled: true },
    };
    expect(filterWorldEventMapEventsForLayers([base], state, now)).toHaveLength(1);
    expect(filterWorldEventMapEventsForLayers([expired, ended, cancelled], state, now)).toHaveLength(0);
  });
});
