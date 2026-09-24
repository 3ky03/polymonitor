import { describe, expect, it } from 'vitest';
import type { HazardMapResponse } from '../domain/types';
import {
  sourceStatusFromAdapter,
  sourceStatusesAfterHazardRefreshFailure,
  sourceStatusesFromHazardResponse,
} from './sourceStatus';

describe('map source status', () => {
  it('keeps loading distinct from an empty successful source', () => {
    const loading = sourceStatusFromAdapter({
      key: 'fixture',
      label: 'Fixture',
      result: { events: [], rejected: [] },
      loaded: false,
    });
    const empty = sourceStatusFromAdapter({
      key: 'fixture',
      label: 'Fixture',
      payloadStatus: 'ok',
      result: { events: [], rejected: [] },
      loaded: true,
    });
    expect(loading.status).toBe('loading');
    expect(empty.status).toBe('ok');
  });

  it('marks contract rejection as partial without discarding valid events', () => {
    const status = sourceStatusFromAdapter({
      key: 'fixture',
      label: 'Fixture',
      payloadStatus: 'ok',
      result: {
        events: [{
          id: 'fixture:1',
          category: 'intel',
          title: 'Event',
          severity: 'watch',
          locationPrecision: 'unknown',
          sources: [{ provider: 'fixture' }],
          limitations: [],
          relatedMarketIds: [],
          properties: {},
        }],
        rejected: [{ index: 1, code: 'invalid-event', message: 'bad coordinates' }],
      },
      loaded: true,
    });
    expect(status).toMatchObject({ status: 'partial', eventCount: 1, rejectedCount: 1 });
  });

  it('preserves provider degradation and coverage reasons', () => {
    const response = {
      schemaVersion: 'natural-hazards.v1',
      generatedAt: '2026-07-29T12:00:00Z',
      events: [],
      sources: [{
        key: 'firms',
        status: 'degraded',
        coverage: {
          scope: 'global',
          label: 'NASA FIRMS satellite fire detections',
          isComplete: false,
          gaps: ['MAP_KEY is not configured'],
        },
        errorCode: 'configuration-required',
      }],
      isPartial: true,
      errors: [{ source: 'firms', code: 'configuration-required' }],
      counts: { events: 0, byHazardKind: {} },
    } satisfies HazardMapResponse;
    expect(sourceStatusesFromHazardResponse(response)[0]).toMatchObject({
      label: 'FIRMS',
      status: 'degraded',
      eventCount: 0,
    });
    expect(sourceStatusesFromHazardResponse(response)[0]?.message).toContain('configuration-required');
  });

  it('shows an initial failure and degrades retained snapshots on refresh failure', () => {
    expect(sourceStatusesAfterHazardRefreshFailure([], 'API 503', false)[0]).toMatchObject({
      key: 'natural-hazards',
      status: 'error',
      eventCount: 0,
      message: 'API 503',
    });
    const retained = sourceStatusesAfterHazardRefreshFailure([{
      key: 'usgs',
      label: 'USGS',
      status: 'ok',
      eventCount: 10,
      rejectedCount: 0,
    }], 'timeout', true);
    expect(retained[0]).toMatchObject({ status: 'degraded', eventCount: 10 });
    expect(retained[0]?.message).toContain('retaining the last successful snapshot');
  });

  it('keeps provider identities visible when the initial aggregate request fails', () => {
    const initial = sourceStatusesFromHazardResponse(null, 0, true);
    const failed = sourceStatusesAfterHazardRefreshFailure(initial, 'API timeout', false);
    expect(failed.map((source) => source.key)).toEqual([
      'usgs',
      'usgs-volcano-cap',
      'nhc',
      'eonet',
      'gdacs',
      'nws',
      'firms',
      'climate-anomaly',
    ]);
    expect(failed.every((source) => source.status === 'error')).toBe(true);
    expect(failed[0]?.message).toContain('Initial source load failed');
  });
});
