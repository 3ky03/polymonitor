import type { LayersList } from '@deck.gl/core';
import type { GeoEvent } from '../../domain/types';
import type { WorldEventMapState } from '../../state/mapState';
import { createEventGeometryLayers } from './eventGeometryLayers';
import {
  createEventPointLayers,
  type EventClusterIndex,
  type LabelProjection,
  type ScreenBox,
} from './eventPointLayer';
import { createAviationLayers } from './aviationLayers';

export { EventClusterIndex, type EventCluster } from './eventPointLayer';
export { isHazardEvent } from './shared';
export {
  createEventInteractionLayers,
  createEventPulseLayers,
  eventRepresentativePoint,
  HAZARD_PULSE_INTERVAL_MS,
  hasAnimatedHazardPulse,
  hazardPulseTargets,
  selectEventPulseCandidates,
  RECENT_EVENT_PULSE_MS,
} from './eventEmphasisLayers';
export {
  aviationLayerStats,
  aviationLayerStatsForState,
  aviationLiveAircraftMarkers,
  createAviationDynamicLayers,
  createAviationLayers,
  createAviationStaticLayerSections,
  type AviationRenderData,
  type AviationMotionPoint,
  type AviationStaticLayerSections,
  type AviationViewport,
} from './aviationLayers';

export type WorldEventStaticLayerSections = {
  geometry: LayersList;
  points: LayersList;
};

/**
 * Static event data is expensive to normalize and cluster.  Keep it separate
 * from the moving aviation overlay so a route runner does not rebuild every
 * hazard polygon, point and label on each animation tick.
 */
export function createWorldEventStaticLayerSections(
  events: GeoEvent[],
  state: WorldEventMapState,
  showLabels = true,
  viewport?: [number, number, number, number],
  clusterIndex?: EventClusterIndex,
): WorldEventStaticLayerSections {
  return {
    geometry: createEventGeometryLayers(events, state.selectedEventId, state.zoom, undefined, viewport),
    points: createEventPointLayers({
      events,
      zoom: state.zoom,
      selectedEventId: state.selectedEventId,
      showLabels,
      viewport,
      clusterIndex,
    }),
  };
}

export function createWorldEventGeometryLayers(
  events: GeoEvent[],
  selectedEventId: string | null,
  zoom: number,
  beforeId?: string,
  viewport?: [number, number, number, number],
) {
  return createEventGeometryLayers(events, selectedEventId, zoom, beforeId, viewport);
}

export function createWorldEventPointLayers(
  events: GeoEvent[],
  state: WorldEventMapState,
  viewport: [number, number, number, number] | undefined,
  clusterIndex: EventClusterIndex,
  project?: LabelProjection,
  occupiedScreenBoxes?: ScreenBox[],
) {
  return createEventPointLayers({
    events,
    zoom: state.zoom,
    selectedEventId: state.selectedEventId,
    showLabels: true,
    viewport,
    clusterIndex,
    project,
    occupiedScreenBoxes,
  });
}

export function createWorldEventLayers(
  events: GeoEvent[],
  state: WorldEventMapState,
  showLabels = true,
  viewport?: [number, number, number, number],
  animationTime = 0,
): LayersList {
  const sections = createWorldEventStaticLayerSections(events, state, showLabels, viewport);
  return [
    // Risk and hazard polygons are the base thematic overlay.  Aviation must
    // sit above them; putting it first let translucent country fills mute the
    // route cores and runners in the final compositing order.
    ...sections.geometry,
    ...createAviationLayers(events, state, animationTime),
    ...sections.points,
  ];
}
