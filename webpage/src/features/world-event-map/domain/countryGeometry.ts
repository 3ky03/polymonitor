import type { Feature, FeatureCollection, MultiPolygon, Polygon } from 'geojson';
import type { GeoEventGeometry } from './types';

type CountryProperties = {
  name?: unknown;
  'ISO3166-1-Alpha-2'?: unknown;
  'ISO3166-1-Alpha-3'?: unknown;
};

export type CountryGeometry = {
  name: string;
  iso2: string;
  iso3: string;
  geometry: Extract<GeoEventGeometry, { type: 'Polygon' | 'MultiPolygon' }>;
};

export type CountryGeometryIndex = {
  countries: CountryGeometry[];
  resolve: (identity?: string | null) => CountryGeometry | null;
  locate: (position: [number, number]) => CountryGeometry | null;
  intersects: (identity: string, geometry: GeoEventGeometry) => boolean;
};

const COUNTRY_ALIASES: Record<string, string> = {
  america: 'US',
  bolivia: 'BO',
  'brunei darussalam': 'BN',
  burma: 'MM',
  'cape verde': 'CV',
  'congo brazzaville': 'CG',
  'congo kinshasa': 'CD',
  'cote d ivoire': 'CI',
  'democratic republic of congo': 'CD',
  'democratic republic of the congo': 'CD',
  'dr congo': 'CD',
  iran: 'IR',
  laos: 'LA',
  moldova: 'MD',
  northkorea: 'KP',
  'north korea': 'KP',
  palestine: 'PS',
  russia: 'RU',
  southkorea: 'KR',
  'south korea': 'KR',
  syria: 'SY',
  taiwan: 'TW',
  tanzania: 'TZ',
  'the gambia': 'GM',
  turkey: 'TR',
  uk: 'GB',
  'u k': 'GB',
  'united kingdom': 'GB',
  us: 'US',
  'u s': 'US',
  usa: 'US',
  'u s a': 'US',
  'united states': 'US',
  'united states of america': 'US',
  venezuela: 'VE',
  vietnam: 'VN',
};

export function normalizeCountryIdentity(value?: string | null) {
  return String(value || '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/&/g, ' and ')
    .replace(/[^a-z0-9]+/g, ' ')
    .replace(/\b(the|republic|federal|democratic|state|states|of)\b/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function text(value: unknown) {
  return typeof value === 'string' ? value.trim() : '';
}

function countryFeature(feature: Feature): CountryGeometry | null {
  if (feature.geometry?.type !== 'Polygon' && feature.geometry?.type !== 'MultiPolygon') return null;
  const properties = (feature.properties || {}) as CountryProperties;
  const name = text(properties.name);
  const iso2 = text(properties['ISO3166-1-Alpha-2']).toUpperCase();
  const iso3 = text(properties['ISO3166-1-Alpha-3']).toUpperCase();
  if (!name || !/^[A-Z]{2}$/.test(iso2) || !/^[A-Z]{3}$/.test(iso3)) return null;
  return {
    name,
    iso2,
    iso3,
    geometry: feature.geometry as Polygon | MultiPolygon,
  };
}

type Position = [number, number];

function pointInRing([x, y]: Position, ring: number[][]) {
  let inside = false;
  for (let current = 0, previous = ring.length - 1; current < ring.length; previous = current++) {
    const currentPosition = ring[current];
    const previousPosition = ring[previous];
    if (!currentPosition || !previousPosition) continue;
    const xi = currentPosition[0];
    const yi = currentPosition[1];
    const xj = previousPosition[0];
    const yj = previousPosition[1];
    if (![xi, yi, xj, yj].every(Number.isFinite)) continue;
    if (xi === undefined || yi === undefined || xj === undefined || yj === undefined) continue;
    const crosses = (yi > y) !== (yj > y)
      && x < ((xj - xi) * (y - yi)) / ((yj - yi) || Number.EPSILON) + xi;
    if (crosses) inside = !inside;
  }
  return inside;
}

function pointInPolygon(position: Position, coordinates: number[][][]) {
  const exterior = coordinates[0];
  if (!exterior || !pointInRing(position, exterior)) return false;
  return coordinates.slice(1).every((hole) => !pointInRing(position, hole));
}

function pointInGeometry(position: Position, geometry: GeoEventGeometry) {
  if (geometry.type === 'Polygon') return pointInPolygon(position, geometry.coordinates);
  if (geometry.type === 'MultiPolygon') {
    return geometry.coordinates.some((polygon) => pointInPolygon(position, polygon));
  }
  return false;
}

function sampledPositions(geometry: GeoEventGeometry, limit = 96): Position[] {
  if (geometry.type === 'Point') return [geometry.coordinates];
  const positions: Position[] = [];
  const collect = (candidate: unknown) => {
    if (!Array.isArray(candidate)) return;
    if (candidate.length >= 2 && Number.isFinite(candidate[0]) && Number.isFinite(candidate[1])) {
      positions.push([Number(candidate[0]), Number(candidate[1])]);
      return;
    }
    candidate.forEach(collect);
  };
  collect(geometry.coordinates);
  if (positions.length <= limit) return positions;
  const stride = Math.max(1, Math.floor(positions.length / limit));
  const sampled = positions.filter((_position, index) => index % stride === 0).slice(0, limit - 1);
  const last = positions[positions.length - 1];
  if (last) sampled.push(last);
  return sampled;
}

function geometryIntersectsCountry(country: CountryGeometry, geometry: GeoEventGeometry) {
  if (sampledPositions(geometry).some((position) => pointInGeometry(position, country.geometry))) return true;
  if (geometry.type !== 'Polygon' && geometry.type !== 'MultiPolygon') return false;
  return sampledPositions(country.geometry).some((position) => pointInGeometry(position, geometry));
}

export function buildCountryGeometryIndex(collection: FeatureCollection): CountryGeometryIndex {
  const countries = collection.features
    .map(countryFeature)
    .filter((country): country is CountryGeometry => country !== null);
  const lookup = new Map<string, CountryGeometry>();
  countries.forEach((country) => {
    lookup.set(country.iso2.toLowerCase(), country);
    lookup.set(country.iso3.toLowerCase(), country);
    lookup.set(normalizeCountryIdentity(country.name), country);
  });
  Object.entries(COUNTRY_ALIASES).forEach(([alias, iso2]) => {
    const country = lookup.get(iso2.toLowerCase());
    if (country) lookup.set(normalizeCountryIdentity(alias), country);
  });
  return {
    countries,
    resolve(identity) {
      const raw = String(identity || '').trim();
      if (!raw || /^global$/i.test(raw)) return null;
      return lookup.get(raw.toLowerCase()) || lookup.get(normalizeCountryIdentity(raw)) || null;
    },
    locate(position) {
      if (!position.every(Number.isFinite)) return null;
      return countries.find((country) => pointInGeometry(position, country.geometry)) || null;
    },
    intersects(identity, geometry) {
      const country = this.resolve(identity);
      return country ? geometryIntersectsCountry(country, geometry) : false;
    },
  };
}
