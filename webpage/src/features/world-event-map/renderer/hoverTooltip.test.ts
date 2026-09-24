import { describe, expect, it } from 'vitest';
import type { GeoEvent, HazardEvent } from '../domain/types';
import type { EventCluster } from './layerFactories';
import {
  pickedWorldEvent,
  pickedWorldEventCluster,
  worldEventTooltipHtml,
} from './hoverTooltip';

function event(overrides: Partial<GeoEvent> = {}): GeoEvent {
  return {
    id: 'event:1',
    category: 'conflict',
    title: 'Evidence <alert>',
    severity: 'warning',
    locationPrecision: 'country',
    locationLabel: 'Example',
    sources: [{ provider: 'UCDP' }],
    limitations: [],
    relatedMarketIds: [],
    properties: {},
    ...overrides,
  };
}

describe('world event hover tooltip', () => {
  it('unwraps GeoJSON features and escapes event content', () => {
    const target = { properties: { event: event() } };
    expect(pickedWorldEvent(target)?.id).toBe('event:1');
    expect(worldEventTooltipHtml(target)).toContain('Evidence &lt;alert&gt;');
    expect(worldEventTooltipHtml(target)).toContain('warning Conflict');
    expect(worldEventTooltipHtml(target)).toContain('<small>Example</small><small>UCDP</small>');
  });

  it('unwraps animated aircraft motion objects', () => {
    const flight = event({
      id: 'flight:1',
      category: 'infrastructure',
      properties: { mapEntity: 'air-flight', fromCode: 'SIN', toCode: 'LHR' },
    });
    const target = { event: flight };
    expect(pickedWorldEvent(target)).toBe(flight);
    expect(worldEventTooltipHtml(target, 'aviation-seeded-aircraft')).toContain('Animated Reference Aircraft');
    expect(worldEventTooltipHtml(target, 'aviation-seeded-aircraft')).toContain('SIN → LHR');
  });

  it('formats routes and hubs from their real adapter fields', () => {
    const route = event({
      category: 'infrastructure',
      properties: {
        mapEntity: 'air-route', fromCode: 'JFK', toCode: 'LHR', layer: 'trunk',
        trafficScore: 91, riskScore: 42, riskSources: ['weather', 'conflict'],
      },
    });
    const hub = event({
      category: 'infrastructure',
      properties: { mapEntity: 'air-hub', code: 'SIN', city: 'Singapore', country: 'SG', routeCount: 128 },
    });
    expect(worldEventTooltipHtml(route, 'aviation-route-core')).toContain('JFK → LHR');
    expect(worldEventTooltipHtml(route, 'aviation-route-core')).toContain('Traffic 91');
    expect(worldEventTooltipHtml(route, 'aviation-route-core')).toContain('Exposure: weather, conflict');
    expect(worldEventTooltipHtml(hub, 'aviation-hubs')).toContain('128 connected routes');
  });

  it('formats live aircraft telemetry with aviation units', () => {
    const aircraft = event({
      category: 'infrastructure',
      properties: {
        mapEntity: 'live-aircraft', callsign: 'TEST123', icao24: 'abc123', originCountry: 'Test',
        baroAltitude: 10_668, velocity: 250, heading: 92,
      },
    });
    const html = worldEventTooltipHtml(aircraft, 'aviation-live-aircraft');
    expect(html).toContain('TEST123');
    expect(html).toContain('35,000 ft');
    expect(html).toContain('486 kt');
    expect(html).toContain('92°');
  });

  it('formats country risk evidence independently from disaster metrics', () => {
    const risk = event({
      category: 'country-risk',
      properties: {
        mapEntity: 'country-risk-area', evidenceCount: 18, sanctionsEvidenceCount: 4,
        countryRiskEvidenceCount: 9, latestSource: 'OFAC',
      },
    });
    const html = worldEventTooltipHtml({ properties: { event: risk } }, 'world-event-country-risk');
    expect(html).toContain('18 evidence records');
    expect(html).toContain('4 sanctions');
    expect(html).toContain('OFAC');
  });

  it('formats hazard-specific metrics for points and areas', () => {
    const earthquake: HazardEvent = {
      ...event(),
      category: 'natural-hazard',
      title: 'M6.4 Test Earthquake',
      properties: {},
      hazardKind: 'earthquake',
      lifecycle: 'active',
      coverage: { scope: 'global', label: 'Global', isComplete: true, gaps: [] },
      severityEvidence: { provider: 'USGS', mappingVersion: 'v1', reason: 'fixture' },
      revision: { nativeEventId: 'eq-1' },
      metrics: { kind: 'earthquake', magnitude: 6.4, depthKm: 12.5, pagerAlert: 'orange' },
    };
    const html = worldEventTooltipHtml(earthquake, 'world-event-hazard-areas');
    expect(html).toContain('Magnitude 6.4 · Depth 12.5 km');
    expect(html).toContain('PAGER ORANGE');
  });

  it('renders clusters instead of discarding their hover state', () => {
    const cluster: EventCluster = {
      kind: 'event-cluster',
      id: 'cluster:1',
      coordinates: [12, 34],
      eventIds: ['one', 'two'],
      count: 2,
      severity: 'critical',
      bounds: [10, 30, 14, 38],
      expansionZoom: 4,
      color: [255, 76, 70, 235],
      symbol: 'earthquake',
    };
    expect(pickedWorldEvent(cluster)).toBeNull();
    expect(pickedWorldEventCluster(cluster)).toBe(cluster);
    expect(worldEventTooltipHtml(cluster)).toContain('2 mapped events');
  });
});
