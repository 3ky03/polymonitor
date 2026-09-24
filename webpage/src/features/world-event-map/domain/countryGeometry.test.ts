import { describe, expect, it } from 'vitest';
import type { FeatureCollection } from 'geojson';
import { buildCountryGeometryIndex, normalizeCountryIdentity } from './countryGeometry';

const collection: FeatureCollection = {
  type: 'FeatureCollection',
  features: [{
    type: 'Feature',
    properties: {
      name: 'United States of America',
      'ISO3166-1-Alpha-2': 'US',
      'ISO3166-1-Alpha-3': 'USA',
    },
    geometry: {
      type: 'Polygon',
      coordinates: [[[-100, 30], [-90, 30], [-90, 40], [-100, 30]]],
    },
  }],
};

describe('country geometry identity', () => {
  it('resolves names, ISO codes and conservative aliases to the same polygon', () => {
    const index = buildCountryGeometryIndex(collection);
    expect(index.resolve('US')?.iso3).toBe('USA');
    expect(index.resolve('USA')?.iso2).toBe('US');
    expect(index.resolve('United States')?.geometry.type).toBe('Polygon');
  });

  it('rejects global and unknown labels instead of inventing coordinates', () => {
    const index = buildCountryGeometryIndex(collection);
    expect(index.resolve('Global')).toBeNull();
    expect(index.resolve('Acme Corporation')).toBeNull();
  });

  it('normalizes punctuation without conflating arbitrary entities', () => {
    expect(normalizeCountryIdentity('Côte d’Ivoire')).toBe('cote d ivoire');
  });

  it('locates points and intersects event polygons without requiring fabricated country fields', () => {
    const index = buildCountryGeometryIndex(collection);
    expect(index.locate([-95, 34])?.iso2).toBe('US');
    expect(index.locate([10, 10])).toBeNull();
    expect(index.intersects('US', { type: 'Point', coordinates: [-95, 34] })).toBe(true);
    expect(index.intersects('US', {
      type: 'Polygon',
      coordinates: [[[-96, 32], [-92, 32], [-92, 36], [-96, 32]]],
    })).toBe(true);
    expect(index.intersects('US', { type: 'Point', coordinates: [10, 10] })).toBe(false);
  });
});
