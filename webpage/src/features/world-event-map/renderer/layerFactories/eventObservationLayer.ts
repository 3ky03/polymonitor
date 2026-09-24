import { ScatterplotLayer } from '@deck.gl/layers';
import type { LayersList } from '@deck.gl/core';
import type { GeoEvent } from '../../domain/types';
import { eventVisibleAtZoom } from '../eventDisclosure';
import {
  continuousMetricRadiusMeters,
  eventRepresentativePoint,
  isHazardEvent,
  SEVERITY_COLORS,
} from './shared';

const WORLD_VIEWPORT: [number, number, number, number] = [-180, -85, 180, 85];

function inViewport(event: GeoEvent, viewport: [number, number, number, number]) {
  const position = eventRepresentativePoint(event);
  return position != null
    && position[0] >= viewport[0]
    && position[0] <= viewport[2]
    && position[1] >= viewport[1]
    && position[1] <= viewport[3];
}

function isTextureObservation(event: GeoEvent, zoom: number, selectedEventId: string | null) {
  if (event.id === selectedEventId || event.geometry?.type !== 'Point') return false;
  if (event.properties.mapEntity === 'air-hub'
    || event.properties.mapEntity === 'live-aircraft'
    || event.properties.mapEntity === 'air-flight'
    || event.properties.mapEntity === 'air-route') return false;
  if (isHazardEvent(event) && event.hazardKind === 'fire-detection') return zoom < 4;
  return !eventVisibleAtZoom(event, zoom, selectedEventId);
}

/**
 * Low-priority observations retain spatial texture without becoming another
 * interactive event layer. Selection, hover and reports remain attached to
 * canonical entities and clusters only.
 */
export function eventObservationTextureCandidates(
  events: GeoEvent[],
  zoom: number,
  selectedEventId: string | null,
  viewport: [number, number, number, number] = WORLD_VIEWPORT,
) {
  const budget = zoom < 2.5 ? 900 : 1_400;
  return events
    .filter((event) => isTextureObservation(event, zoom, selectedEventId) && inViewport(event, viewport))
    .sort((left, right) => {
      const severityRank = { info: 0, watch: 1, warning: 2, critical: 3 } as const;
      const severityDelta = severityRank[right.severity] - severityRank[left.severity];
      if (severityDelta) return severityDelta;
      return right.id.localeCompare(left.id);
    })
    .slice(0, budget);
}

export function createEventObservationLayer(
  events: GeoEvent[],
  zoom: number,
  selectedEventId: string | null,
  viewport?: [number, number, number, number],
): LayersList {
  const observations = eventObservationTextureCandidates(
    events,
    zoom,
    selectedEventId,
    viewport,
  );
  if (!observations.length) return [];
  return [new ScatterplotLayer<GeoEvent>({
    id: 'world-event-observation-texture',
    data: observations,
    getPosition: (event) => eventRepresentativePoint(event)!,
    getRadius: (event) => continuousMetricRadiusMeters(event) || 7_000,
    getFillColor: (event) => {
      const [red, green, blue] = SEVERITY_COLORS[event.severity];
      const alpha = event.severity === 'warning' ? 92 : event.severity === 'watch' ? 68 : 46;
      return [red, green, blue, alpha];
    },
    radiusMinPixels: zoom < 2.5 ? 1.5 : 1.8,
    radiusMaxPixels: zoom < 2.5 ? 4 : 5.5,
    pickable: false,
    stroked: false,
    filled: true,
  })];
}
