import { describe, expect, it } from 'vitest';
import type { FeatureCollection } from 'geojson';
import { countryBasemapLabels, visibleCountryBasemapLabels } from './countryBasemapLabels';

const countries: FeatureCollection = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: { name: 'Large verified country', 'ISO3166-1-Alpha-3': 'LGC' },
      geometry: { type: 'Polygon', coordinates: [[[0, 0], [8, 0], [8, 8], [0, 8], [0, 0]]] },
    },
    {
      type: 'Feature',
      properties: { name: 'Small verified country', 'ISO3166-1-Alpha-3': 'SMC' },
      geometry: { type: 'Polygon', coordinates: [[[20, 0], [21, 0], [21, 1], [20, 1], [20, 0]]] },
    },
  ],
};

describe('SVG fallback country labels', () => {
  it('derives labels from verified country geometry without city coordinates', () => {
    const labels = countryBasemapLabels(countries);
    expect(labels.map((label) => label.name)).toEqual([
      'Large verified country',
      'Small verified country',
    ]);
    expect(labels[0]?.coordinates.every(Number.isFinite)).toBe(true);
  });

  it('uses deterministic zoom disclosure for fallback labels', () => {
    const labels = Array.from({ length: 80 }, (_, index) => ({
      id: String(index), name: String(index), coordinates: [index, 0] as [number, number], area: 80 - index,
    }));
    expect(visibleCountryBasemapLabels(labels, 1.25)).toHaveLength(12);
    expect(visibleCountryBasemapLabels(labels, 3.5)).toHaveLength(72);
  });
});
