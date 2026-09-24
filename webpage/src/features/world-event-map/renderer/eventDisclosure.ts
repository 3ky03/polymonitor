import type { GeoEvent } from '../domain/types';
import { isHazardEvent } from './layerFactories/shared';

/**
 * Zoom disclosure is a presentation decision, not a clustering decision.
 * Keeping it outside the Supercluster factory lets the WebGL and SVG
 * renderers share the same visibility and context-texture contract.
 */
export function isMajorWorldEvent(event: GeoEvent) {
  if (event.severity === 'critical') return true;
  if (event.severity !== 'warning') return false;
  if (!isHazardEvent(event)) return true;
  if (event.metrics.kind === 'earthquake') {
    const pager = String(event.metrics.pagerAlert || '').toLowerCase();
    return event.metrics.magnitude >= 5.2
      || Number(event.metrics.significance || 0) >= 500
      || event.metrics.tsunami === true
      || pager === 'orange'
      || pager === 'red';
  }
  if ((event.hazardKind === 'wildfire' || event.hazardKind === 'fire-detection')
    && event.metrics.kind === 'wildfire') {
    return event.hazardKind === 'wildfire'
      || Number(event.metrics.detectionCount || 0) >= 20
      || Number(event.metrics.fireRadiativePowerMw || 0) >= 100;
  }
  return true;
}

export function eventDisclosureTier(event: GeoEvent) {
  if (isMajorWorldEvent(event)) return 0;
  if (event.severity === 'watch' || event.severity === 'warning') return 1;
  return 2;
}

export function disclosureTierForZoom(zoom: number) {
  return zoom < 2.5 ? 0 : zoom < 4 ? 1 : 2;
}

export function eventVisibleAtZoom(event: GeoEvent, zoom: number, selectedEventId: string | null) {
  return event.id === selectedEventId || eventDisclosureTier(event) <= disclosureTierForZoom(zoom);
}
