import { geoArea, geoCentroid } from 'd3-geo';
import type { Feature, FeatureCollection, Geometry } from 'geojson';

export type CountryBasemapLabel = {
  id: string;
  name: string;
  coordinates: [number, number];
  area: number;
};

function countryName(feature: Feature<Geometry>) {
  const value = feature.properties?.name;
  return typeof value === 'string' ? value.trim() : '';
}

function isCoordinate(value: number[]): value is [number, number] {
  if (value.length < 2) return false;
  const lon = value[0]!;
  const lat = value[1]!;
  return Number.isFinite(lon)
    && Number.isFinite(lat)
    && lon >= -180
    && lon <= 180
    && lat >= -85
    && lat <= 85;
}

/**
 * The SVG renderer only has verified country geometry. Country labels are
 * therefore derived from geometry centroids and intentionally do not invent
 * city coordinates. The primary vector basemap supplies city labels.
 */
export function countryBasemapLabels(countries: FeatureCollection): CountryBasemapLabel[] {
  return countries.features.flatMap((feature, index) => {
    if (!feature.geometry) return [];
    const typedFeature = feature as Feature<Geometry>;
    const name = countryName(typedFeature);
    const coordinates = geoCentroid(typedFeature);
    // GeoJSON winding may describe the complementary sphere. Label priority is
    // based on the smaller spherical area so it remains stable across sources.
    const rawArea = geoArea(typedFeature);
    const area = Math.min(rawArea, 4 * Math.PI - rawArea);
    if (!name || !isCoordinate(coordinates) || !Number.isFinite(area) || area <= 0) return [];
    return [{
      id: String(typedFeature.id || typedFeature.properties?.['ISO3166-1-Alpha-3'] || `${name}:${index}`),
      name,
      coordinates,
      area,
    }];
  }).sort((left, right) => right.area - left.area);
}

export function visibleCountryBasemapLabels(labels: CountryBasemapLabel[], zoom: number) {
  const limit = zoom < 1.8 ? 12 : zoom < 2.4 ? 22 : zoom < 3.2 ? 36 : 72;
  return labels.slice(0, limit);
}
