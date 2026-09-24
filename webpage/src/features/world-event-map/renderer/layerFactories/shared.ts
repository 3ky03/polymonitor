import type {
  GeoEvent,
  GeoPoint,
  GeoEventSeverity,
  HazardEvent,
  HazardKind,
} from '../../domain/types';
import { MAP_SEVERITY_STYLES } from '../../config/mapSymbols';

export const SEVERITY_COLORS: Record<GeoEventSeverity, [number, number, number, number]> = {
  info: [...MAP_SEVERITY_STYLES.info.rgba],
  watch: [...MAP_SEVERITY_STYLES.watch.rgba],
  warning: [...MAP_SEVERITY_STYLES.warning.rgba],
  critical: [...MAP_SEVERITY_STYLES.critical.rgba],
};

export const MAP_MONO_FONT_FAMILY = '"JetBrains Mono", "SFMono-Regular", Consolas, monospace';

const HAZARD_COLORS: Record<HazardKind, [number, number, number, number]> = {
  'severe-storm': [167, 139, 250, 210],
  tornado: [192, 132, 252, 220],
  'tropical-cyclone': [56, 189, 248, 220],
  flood: [45, 212, 191, 210],
  'extreme-heat': [255, 77, 79, 220],
  'extreme-cold': [96, 165, 250, 215],
  earthquake: [251, 146, 60, 220],
  volcano: [244, 63, 94, 220],
  tsunami: [34, 211, 238, 220],
  wildfire: [255, 107, 53, 225],
  'fire-detection': [249, 115, 22, 205],
  'temperature-anomaly': [232, 121, 249, 210],
  'precipitation-anomaly': [129, 140, 248, 210],
  'other-weather-anomaly': [217, 70, 239, 210],
};

export function eventColor(event: GeoEvent, alpha?: number): [number, number, number, number] {
  let color: [number, number, number, number];
  if (isHazardEvent(event)) {
    color = [...HAZARD_COLORS[event.hazardKind]];
  } else if (event.category === 'conflict' || event.category === 'unrest') {
    const violenceType = String(event.properties.violenceType || '');
    color = violenceType === '1'
      ? [255, 103, 91, 225]
      : violenceType === '2'
        ? [240, 180, 60, 225]
        : violenceType === '3'
          ? [158, 232, 95, 225]
          : [...SEVERITY_COLORS[event.severity]];
  } else if (event.category === 'intel') {
    color = [238, 199, 71, 225];
  } else {
    color = [...SEVERITY_COLORS[event.severity]];
  }
  if (alpha != null) color[3] = alpha;
  return color;
}

export function isHazardEvent(event: GeoEvent): event is HazardEvent {
  return (event.category === 'natural-hazard' || event.category === 'weather')
    && typeof (event as Partial<HazardEvent>).hazardKind === 'string';
}

function visitGeometryCoordinates(
  value: unknown,
  visitor: (coordinate: GeoPoint) => void,
) {
  if (!Array.isArray(value)) return;
  if (value.length >= 2 && Number.isFinite(value[0]) && Number.isFinite(value[1])) {
    visitor([Number(value[0]), Number(value[1])]);
    return;
  }
  for (const child of value) visitGeometryCoordinates(child, visitor);
}

/** Bounds are derived only from the provider geometry; no fallback location is invented. */
export function eventGeometryBounds(event: GeoEvent): [number, number, number, number] | null {
  if (!event.geometry) return null;
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  visitGeometryCoordinates(event.geometry.coordinates, ([lon, lat]) => {
    west = Math.min(west, lon);
    south = Math.min(south, lat);
    east = Math.max(east, lon);
    north = Math.max(north, lat);
  });
  return [west, south, east, north].every(Number.isFinite)
    ? [west, south, east, north]
    : null;
}

function polygonOuterRings(event: GeoEvent): number[][][] {
  if (event.geometry?.type === 'Polygon') return event.geometry.coordinates.slice(0, 1);
  if (event.geometry?.type === 'MultiPolygon') {
    return event.geometry.coordinates.flatMap((polygon) => polygon.slice(0, 1));
  }
  return [];
}

function ringCentroid(ring: number[][]): { point: GeoPoint; weight: number } | null {
  if (ring.length < 3) return null;
  let areaTwice = 0;
  let longitudeTotal = 0;
  let latitudeTotal = 0;
  for (let index = 0; index < ring.length; index += 1) {
    const current = ring[index];
    const next = ring[(index + 1) % ring.length];
    const x0 = Number(current?.[0]);
    const y0 = Number(current?.[1]);
    const x1 = Number(next?.[0]);
    const y1 = Number(next?.[1]);
    if (![x0, y0, x1, y1].every(Number.isFinite)) continue;
    const cross = x0 * y1 - x1 * y0;
    areaTwice += cross;
    longitudeTotal += (x0 + x1) * cross;
    latitudeTotal += (y0 + y1) * cross;
  }
  if (Math.abs(areaTwice) < 1e-9) return null;
  return {
    point: [longitudeTotal / (3 * areaTwice), latitudeTotal / (3 * areaTwice)],
    weight: Math.abs(areaTwice),
  };
}

/**
 * Derives a stable marker position from the supplied geometry. Polygon markers
 * use an area-weighted centroid and fall back to the geometry bounds centre.
 */
export function eventRepresentativePoint(event: GeoEvent): GeoPoint | null {
  const geometry = event.geometry;
  if (!geometry) return null;
  if (geometry.type === 'Point') return geometry.coordinates;
  if (geometry.type === 'LineString') return geometry.coordinates[geometry.coordinates.length - 1] || null;
  const centroids = polygonOuterRings(event)
    .map(ringCentroid)
    .filter((centroid): centroid is NonNullable<typeof centroid> => centroid != null);
  if (centroids.length) {
    const totalWeight = centroids.reduce((sum, centroid) => sum + centroid.weight, 0);
    if (totalWeight > 0) {
      return [
        centroids.reduce((sum, centroid) => sum + centroid.point[0] * centroid.weight, 0) / totalWeight,
        centroids.reduce((sum, centroid) => sum + centroid.point[1] * centroid.weight, 0) / totalWeight,
      ];
    }
  }
  const bounds = eventGeometryBounds(event);
  return bounds
    ? [(bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2]
    : null;
}

export const HAZARD_AREA_REGIONAL_MIN_ZOOM = 3;
export const HAZARD_AREA_DETAIL_MIN_ZOOM = 4.5;

export type HazardAreaPresentation = {
  mode: 'hidden' | 'regional' | 'detail' | 'selected';
  fillAlpha: number;
  lineAlpha: number;
  lineWidth: number;
};

/** Shared WebGL/SVG progressive-disclosure contract for official hazard areas. */
export function hazardAreaPresentation(
  event: GeoEvent,
  zoom: number,
  selectedEventId: string | null,
): HazardAreaPresentation {
  if (event.id === selectedEventId) {
    return { mode: 'selected', fillAlpha: 46, lineAlpha: 245, lineWidth: 2.2 };
  }
  if (zoom < HAZARD_AREA_REGIONAL_MIN_ZOOM) {
    return { mode: 'hidden', fillAlpha: 0, lineAlpha: 0, lineWidth: 0 };
  }
  if (zoom < HAZARD_AREA_DETAIL_MIN_ZOOM) {
    if (event.severity !== 'warning' && event.severity !== 'critical') {
      return { mode: 'hidden', fillAlpha: 0, lineAlpha: 0, lineWidth: 0 };
    }
    return { mode: 'regional', fillAlpha: 16, lineAlpha: 0, lineWidth: 0 };
  }
  return {
    mode: 'detail',
    fillAlpha: event.severity === 'critical' ? 36 : 28,
    lineAlpha: event.severity === 'critical' ? 142 : 108,
    lineWidth: 0.85,
  };
}

export function eventSeverityColor(
  event: GeoEvent,
  alpha = SEVERITY_COLORS[event.severity][3],
): [number, number, number, number] {
  const [red, green, blue] = SEVERITY_COLORS[event.severity];
  return [red, green, blue, alpha];
}

export function continuousMetricRadiusMeters(event: GeoEvent): number | null {
  if (isHazardEvent(event) && event.metrics.kind === 'earthquake') {
    return Math.max(12_000, Math.pow(Math.max(0.5, event.metrics.magnitude), 2.25) * 4_200);
  }
  if (isHazardEvent(event)
    && (event.hazardKind === 'wildfire' || event.hazardKind === 'fire-detection')
    && event.metrics.kind === 'wildfire') {
    const frp = Number(event.metrics.fireRadiativePowerMw || 0);
    const detections = Number(event.metrics.detectionCount || 0);
    if (frp > 0 || detections > 0) {
      return Math.max(11_000, Math.sqrt(Math.max(frp, detections * 18, 1)) * 5_200);
    }
  }
  const deaths = Number(event.properties.deathsBest || 0);
  if (Number.isFinite(deaths) && deaths > 0) {
    return Math.max(10_000, Math.sqrt(deaths + 1) * 6_200);
  }
  return null;
}

export function pointRadiusMeters(event: GeoEvent) {
  return continuousMetricRadiusMeters(event)
    ?? (event.category === 'infrastructure' ? 13_000 : 18_000);
}

export function eventLabel(event: GeoEvent) {
  if (isHazardEvent(event) && event.metrics.kind === 'earthquake') {
    return `M${event.metrics.magnitude.toFixed(1)}`;
  }
  const code = String(event.properties.code || '').trim();
  return code || event.title;
}
