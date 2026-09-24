import { GeoJsonLayer } from '@deck.gl/layers';
import type { LayersList } from '@deck.gl/core';
import type { GeoEvent } from '../../domain/types';

function evidenceCount(event: GeoEvent) {
  const value = Number(event.properties.evidenceCount);
  return Number.isFinite(value) ? Math.max(0, value) : 0;
}

export function isCountryRiskArea(event: GeoEvent) {
  return event.category === 'country-risk'
    || event.category === 'sanctions'
    || event.properties.mapEntity === 'country-risk-area';
}

/** Country evidence uses a separate warm risk scale; it is not a hazard footprint. */
export function countryRiskColor(event: GeoEvent, alpha: number): [number, number, number, number] {
  const evidence = evidenceCount(event);
  if (event.severity === 'critical' || evidence >= 30) return [225, 61, 49, alpha];
  if (event.severity === 'warning' || evidence >= 15) return [244, 112, 48, alpha];
  if (event.severity === 'watch' || evidence >= 5) return [210, 164, 61, alpha];
  return [130, 111, 70, alpha];
}

export function createCountryRiskLayers(events: GeoEvent[], selectedEventId: string | null): LayersList {
  const areas = events.filter((event) => (
    isCountryRiskArea(event)
    && (event.geometry?.type === 'Polygon' || event.geometry?.type === 'MultiPolygon')
  ));
  if (!areas.length) return [];

  return [new GeoJsonLayer({
    id: 'world-event-country-risk',
    data: {
      type: 'FeatureCollection',
      features: areas.map((event) => ({
        type: 'Feature',
        id: event.id,
        properties: { event },
        geometry: event.geometry,
      })),
    } as any,
    filled: true,
    stroked: true,
    getFillColor: (feature) => countryRiskColor(
      feature.properties?.event as GeoEvent,
      feature.properties?.event?.id === selectedEventId ? 72 : 28,
    ),
    getLineColor: (feature) => countryRiskColor(
      feature.properties?.event as GeoEvent,
      feature.properties?.event?.id === selectedEventId ? 255 : 132,
    ),
    getLineWidth: (feature) => feature.properties?.event?.id === selectedEventId ? 2.6 : 0.85,
    lineWidthMinPixels: 0.65,
    pickable: true,
    autoHighlight: false,
  })];
}
