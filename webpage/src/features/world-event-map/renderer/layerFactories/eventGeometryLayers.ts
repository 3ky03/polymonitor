import { GeoJsonLayer, PathLayer, ScatterplotLayer } from '@deck.gl/layers';
import type { LayersList } from '@deck.gl/core';
import type { GeoEvent } from '../../domain/types';
import {
  eventSeverityColor,
  eventGeometryBounds,
  hazardAreaPresentation,
  isHazardEvent,
  type HazardAreaPresentation,
} from './shared';
import { createCountryRiskLayers, isCountryRiskArea } from './countryRiskLayers';
import { eventColor } from './shared';

type CycloneCenter = {
  event: GeoEvent;
  coordinates: [number, number];
};

type HazardAreaFeatureProperties = {
  event: GeoEvent;
  presentation: HazardAreaPresentation;
};

type NamedCyclonePath = {
  event: GeoEvent;
  path: [number, number][];
  mode: 'observed' | 'forecast';
};

type NamedCycloneCone = { event: GeoEvent; geometry: Record<string, unknown> };

function namedGeometry(event: GeoEvent, name: string) {
  const geometries = event.properties.geometries;
  if (!geometries || typeof geometries !== 'object' || Array.isArray(geometries)) return null;
  const geometry = (geometries as Record<string, unknown>)[name];
  return geometry && typeof geometry === 'object' && !Array.isArray(geometry)
    ? geometry as { type?: string; coordinates?: unknown }
    : null;
}

function namedPaths(event: GeoEvent, name: string, mode: NamedCyclonePath['mode']): NamedCyclonePath[] {
  const geometry = namedGeometry(event, name);
  if (geometry?.type === 'LineString' && Array.isArray(geometry.coordinates)) {
    return [{ event, path: geometry.coordinates as [number, number][], mode }];
  }
  if (geometry?.type === 'MultiLineString' && Array.isArray(geometry.coordinates)) {
    return (geometry.coordinates as [number, number][][]).map((path) => ({ event, path, mode }));
  }
  return [];
}

export function createEventGeometryLayers(
  events: GeoEvent[],
  selectedEventId: string | null,
  zoom = Number.POSITIVE_INFINITY,
  beforeId?: string,
  viewport?: [number, number, number, number],
): LayersList {
  const visibleEvents = viewport
    ? events.filter((event) => {
      if (event.id === selectedEventId) return true;
      const bounds = eventGeometryBounds(event);
      return !bounds || (
        bounds[0] <= viewport[2]
        && bounds[2] >= viewport[0]
        && bounds[1] <= viewport[3]
        && bounds[3] >= viewport[1]
      );
    })
    : events;
  const lines = visibleEvents.filter((event) => (
    event.properties.mapEntity !== 'air-route'
    && event.properties.mapEntity !== 'air-flight'
    && event.geometry?.type === 'LineString'
  ));
  const cycloneCenters: CycloneCenter[] = lines.flatMap((event) => {
    if (!isHazardEvent(event) || event.hazardKind !== 'tropical-cyclone' || event.geometry?.type !== 'LineString') {
      return [];
    }
    const coordinates = event.geometry.coordinates[event.geometry.coordinates.length - 1];
    return coordinates ? [{ event, coordinates }] : [];
  });
  const hazardAreas = visibleEvents.flatMap((event) => {
    if (!isHazardEvent(event)
      || (event.geometry?.type !== 'Polygon' && event.geometry?.type !== 'MultiPolygon')) return [];
    const presentation = hazardAreaPresentation(event, zoom, selectedEventId);
    return presentation.mode === 'hidden' ? [] : [{ event, presentation }];
  });
  const cyclonePaths = visibleEvents.flatMap((event) => (
    isHazardEvent(event) && event.hazardKind === 'tropical-cyclone'
      ? [...namedPaths(event, 'observedTrack', 'observed'), ...namedPaths(event, 'forecastTrack', 'forecast')]
      : []
  ));
  const cycloneCones: NamedCycloneCone[] = visibleEvents.flatMap((event) => {
    if (!isHazardEvent(event) || event.hazardKind !== 'tropical-cyclone') return [];
    const geometry = namedGeometry(event, 'forecastCone');
    return geometry?.type === 'Polygon' || geometry?.type === 'MultiPolygon'
      ? [{ event, geometry }]
      : [];
  });
  const contextAreas = visibleEvents.filter((event) => (
    !isHazardEvent(event)
    && !isCountryRiskArea(event)
    && (event.geometry?.type === 'Polygon' || event.geometry?.type === 'MultiPolygon')
  ));
  const layers: LayersList = [];

  layers.push(...createCountryRiskLayers(visibleEvents, selectedEventId));

  if (cycloneCones.length) {
    layers.push(new GeoJsonLayer({
      id: 'world-event-cyclone-forecast-cones',
      data: {
        type: 'FeatureCollection',
        features: cycloneCones.map(({ event, geometry }) => ({
          type: 'Feature', id: event.id, properties: { event }, geometry,
        })),
      } as any,
      filled: true,
      stroked: true,
      getFillColor: (feature) => eventColor(feature.properties?.event as GeoEvent, 28),
      getLineColor: (feature) => eventColor(feature.properties?.event as GeoEvent, 135),
      getLineWidth: 1,
      lineWidthMinPixels: 0.75,
      pickable: true,
      autoHighlight: false,
      wrapLongitude: true,
      beforeId,
    }));
  }

  if (cyclonePaths.length) {
    layers.push(new PathLayer<NamedCyclonePath>({
      id: 'world-event-cyclone-tracks',
      data: cyclonePaths,
      getPath: (item) => item.path,
      getColor: (item) => item.mode === 'observed'
        ? eventColor(item.event, item.event.id === selectedEventId ? 245 : 205)
        : [205, 225, 232, item.event.id === selectedEventId ? 225 : 155],
      getWidth: (item) => item.mode === 'observed' ? 2.2 : 1.2,
      widthMinPixels: 1,
      widthMaxPixels: 4,
      jointRounded: true,
      capRounded: true,
      pickable: true,
      wrapLongitude: true,
    }));
  }

  if (hazardAreas.length) {
    layers.push(new GeoJsonLayer({
      id: 'world-event-hazard-areas',
      data: {
        type: 'FeatureCollection',
        features: hazardAreas.map((event) => ({
          type: 'Feature',
          id: event.event.id,
          properties: event,
          geometry: event.event.geometry,
        })),
      } as any,
      filled: true,
      stroked: true,
      getFillColor: (feature) => {
        const properties = feature.properties as HazardAreaFeatureProperties;
        return eventSeverityColor(properties.event, properties.presentation.fillAlpha);
      },
      getLineColor: (feature) => {
        const properties = feature.properties as HazardAreaFeatureProperties;
        return eventSeverityColor(properties.event, properties.presentation.lineAlpha);
      },
      getLineWidth: (feature) => (
        (feature.properties as HazardAreaFeatureProperties).presentation.lineWidth
      ),
      lineWidthMinPixels: 0.5,
      pickable: true,
      autoHighlight: false,
      beforeId,
    }));
  }

  if (contextAreas.length) {
    layers.push(new GeoJsonLayer({
      id: 'world-event-context-areas',
      data: {
        type: 'FeatureCollection',
        features: contextAreas.map((event) => ({
          type: 'Feature',
          id: event.id,
          properties: { event },
          geometry: event.geometry,
        })),
      } as any,
      filled: true,
      stroked: true,
      getFillColor: (feature) => eventColor(feature.properties?.event as GeoEvent, 38),
      getLineColor: (feature) => eventColor(feature.properties?.event as GeoEvent, 185),
      getLineWidth: (feature) => feature.properties?.event?.id === selectedEventId ? 2.5 : 1,
      lineWidthMinPixels: 1,
      pickable: true,
      autoHighlight: false,
      beforeId,
    }));
  }

  if (lines.length) {
    layers.push(new PathLayer<GeoEvent>({
      id: 'world-event-paths',
      data: lines,
      getPath: (event) => event.geometry?.type === 'LineString' ? event.geometry.coordinates : [],
      getColor: (event) => eventColor(event, event.id === selectedEventId ? 240 : 165),
      getWidth: (event) => event.id === selectedEventId ? 2.4 : 1.5,
      widthMinPixels: 0.6,
      widthMaxPixels: 5,
      jointRounded: true,
      capRounded: true,
      pickable: true,
    }));
  }
  if (cycloneCenters.length) {
    layers.push(new ScatterplotLayer<CycloneCenter>({
      id: 'world-event-cyclone-centers',
      data: cycloneCenters,
      getPosition: (center) => center.coordinates,
      getRadius: (center) => center.event.id === selectedEventId ? 34_000 : 24_000,
      getFillColor: (center) => eventColor(center.event, 245),
      getLineColor: [235, 250, 255, 255],
      getLineWidth: (center) => center.event.id === selectedEventId ? 2.8 : 1.5,
      radiusMinPixels: 7,
      radiusMaxPixels: 22,
      lineWidthMinPixels: 1.2,
      pickable: true,
      stroked: true,
    }));
  }
  return layers;
}
