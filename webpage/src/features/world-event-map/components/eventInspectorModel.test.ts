import { describe, expect, it } from 'vitest';
import type { GeoEvent, HazardEvent } from '../domain/types';
import {
  eventContextFields,
  eventTimeFields,
  geometryLabel,
  hazardLabel,
  hazardMetricFields,
} from './eventInspectorModel';

function earthquake(): HazardEvent {
  return {
    id: 'earthquake:usgs:test',
    category: 'natural-hazard',
    title: 'M6.4 earthquake',
    severity: 'warning',
    occurredAt: '2026-07-29T01:00:00Z',
    updatedAt: '2026-07-29T01:02:00Z',
    geometry: { type: 'Point', coordinates: [120, 30] },
    locationPrecision: 'exact',
    locationLabel: 'Test region',
    sources: [{ provider: 'USGS', nativeId: 'test' }],
    limitations: ['Magnitude may be revised.'],
    relatedMarketIds: [],
    properties: {},
    hazardKind: 'earthquake',
    lifecycle: 'observed',
    coverage: { scope: 'global', label: 'USGS', isComplete: false, gaps: [] },
    severityEvidence: {
      provider: 'USGS',
      rawLevel: 'yellow',
      mappingVersion: 'hazard-severity.v1',
      reason: 'PAGER yellow',
    },
    revision: { nativeEventId: 'test' },
    metrics: {
      kind: 'earthquake',
      magnitude: 6.4,
      depthKm: 12.5,
      significance: 700,
      pagerAlert: 'yellow',
      tsunami: true,
    },
  };
}

describe('event inspector model', () => {
  it('exposes hazard evidence instead of a generic marker summary', () => {
    const event = earthquake();
    expect(hazardLabel(event)).toBe('Earthquake');
    expect(geometryLabel(event)).toBe('30.000°, 120.000°');
    expect(hazardMetricFields(event)).toEqual([
      { label: 'Magnitude', value: '6.4' },
      { label: 'Depth', value: '12.5 km' },
      { label: 'Significance', value: '700' },
      { label: 'PAGER alert', value: 'YELLOW' },
      { label: 'Tsunami flag', value: 'Yes' },
    ]);
  });

  it('keeps provider lifecycle timestamps distinct', () => {
    const event: HazardEvent = {
      ...earthquake(),
      effectiveAt: '2026-07-29T00:50:00Z',
      onsetAt: '2026-07-29T00:55:00Z',
      expiresAt: '2026-07-29T04:00:00Z',
    };
    expect(eventTimeFields(event).map((item) => item.label)).toEqual([
      'Occurred',
      'Effective',
      'Onset',
      'Updated',
      'Expires',
    ]);
  });

  it('labels satellite detections as observations, never named wildfires', () => {
    const event: HazardEvent = {
      ...earthquake(),
      id: 'fire-detection:firms:test',
      title: 'Thermal anomaly',
      hazardKind: 'fire-detection',
      lifecycle: 'observed',
      metrics: {
        kind: 'wildfire',
        detectionCount: 42,
        fireRadiativePowerMw: 320.5,
        sensor: 'VIIRS',
      },
    };
    expect(hazardLabel(event)).toBe('Satellite thermal anomaly');
    expect(hazardMetricFields(event)[0]).toEqual({
      label: 'Record type',
      value: 'Thermal anomaly observation',
    });
  });

  it('turns aircraft evidence into a readable report without inventing missing state', () => {
    const event: GeoEvent = {
      id: 'aircraft:fixture',
      category: 'infrastructure',
      title: 'Fixture aircraft',
      severity: 'watch',
      geometry: { type: 'Point', coordinates: [120, 30] },
      locationPrecision: 'exact',
      sources: [{ provider: 'OpenSky', nativeId: 'fixture' }],
      limitations: [],
      relatedMarketIds: [],
      properties: {
        mapEntity: 'live-aircraft',
        callsign: 'TEST123',
        icao24: 'abc123',
        originCountry: 'Fixture',
        baroAltitude: 10_000,
      },
    };

    expect(eventContextFields(event)).toEqual([
      { label: 'Reference type', value: 'Live surveillance aircraft' },
      { label: 'Callsign', value: 'TEST123' },
      { label: 'ICAO24', value: 'abc123' },
      { label: 'Origin country', value: 'Fixture' },
      { label: 'Altitude', value: '10000 m' },
    ]);
  });
});
