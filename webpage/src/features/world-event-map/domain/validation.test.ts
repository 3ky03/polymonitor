import { describe, expect, it } from 'vitest';
import type { GeoEvent } from './types';
import { validateGeoEvent, validateGeoEvents } from './validation';

function event(overrides: Partial<GeoEvent> = {}): GeoEvent {
  return {
    id: 'provider:event-1',
    category: 'conflict',
    title: 'Verified event',
    severity: 'warning',
    geometry: { type: 'Point', coordinates: [12.5, 45.4] },
    locationPrecision: 'exact',
    sources: [{ provider: 'fixture', nativeId: 'event-1' }],
    limitations: [],
    relatedMarketIds: [],
    properties: {},
    ...overrides,
  };
}

describe('GeoEvent validation', () => {
  it('accepts a valid canonical point', () => {
    expect(validateGeoEvent(event())).toMatchObject({ ok: true });
  });

  it('rejects out-of-range coordinates', () => {
    const result = validateGeoEvent(event({
      geometry: { type: 'Point', coordinates: [181, 45] },
    }));
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errors.join(' ')).toContain('coordinates');
  });

  it('rejects open polygon rings and geometry attached to unknown precision', () => {
    expect(validateGeoEvent(event({
      geometry: {
        type: 'Polygon',
        coordinates: [[[0, 0], [1, 0], [1, 1], [0, 1]]],
      },
    })).ok).toBe(false);
    expect(validateGeoEvent(event({ locationPrecision: 'unknown' })).ok).toBe(false);
  });

  it('deduplicates stable event ids without silently accepting the second record', () => {
    const result = validateGeoEvents([event(), event()]);
    expect(result.events).toHaveLength(1);
    expect(result.rejected).toEqual([
      expect.objectContaining({ code: 'duplicate-event' }),
    ]);
  });
});
