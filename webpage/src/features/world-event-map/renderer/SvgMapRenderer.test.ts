import { describe, expect, it } from 'vitest';
import type { Polygon } from 'geojson';
import { normalizePolygonWinding } from './SvgMapRenderer';

function signedArea(ring: number[][]) {
  return ring.slice(0, -1).reduce((area, point, index) => {
    const next = ring[index + 1]!;
    return area + point[0]! * next[1]! - next[0]! * point[1]!;
  }, 0) / 2;
}

describe('SVG fallback polygon normalization', () => {
  it('rewinds outer rings clockwise and holes counter-clockwise for d3', () => {
    const geometry: Polygon = {
      type: 'Polygon',
      coordinates: [
        [[0, 0], [2, 0], [2, 2], [0, 0]],
        [[0.5, 0.5], [0.5, 1], [1, 1], [0.5, 0.5]],
      ],
    };
    const normalized = normalizePolygonWinding(geometry) as Polygon;
    expect(signedArea(normalized.coordinates[0]!)).toBeLessThan(0);
    expect(signedArea(normalized.coordinates[1]!)).toBeGreaterThan(0);
  });
});
