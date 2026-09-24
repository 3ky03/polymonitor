import { MapboxOverlay } from '@deck.gl/mapbox';
import type { Layer, LayersList, PickingInfo } from '@deck.gl/core';
import { TextLayer } from '@deck.gl/layers';
import type { FeatureCollection, Geometry, Position } from 'geojson';
import maplibregl, {
  type FilterSpecification,
  type Map as MapLibreMap,
  type MapMouseEvent,
  type MapSourceDataEvent,
} from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import {
  getWeatherMapFallbackStyle,
  getWeatherMapStyle,
  refreshWorldEventBasemapLabelDensity,
  reinforceWorldEventBasemapLabels,
} from '@/config/weatherBasemap';
import type { GeoEvent } from '../domain/types';
import type { WorldEventMapState } from '../state/mapState';
import type { BasemapState, MapCountryTarget, MapRenderer, MapRendererCallbacks } from './MapRenderer';
import {
  createAviationDynamicLayers,
  createAviationStaticLayerSections,
  createEventInteractionLayers,
  createEventPulseLayers,
  createWorldEventGeometryLayers,
  createWorldEventPointLayers,
  EventClusterIndex,
  HAZARD_PULSE_INTERVAL_MS,
  hasAnimatedHazardPulse,
  selectEventPulseCandidates,
  type AviationStaticLayerSections,
  type AviationMotionPoint,
  type EventCluster,
} from './layerFactories';
import {
  advanceAnimationTime,
  boundedAnimationDelta,
  MAP_ANIMATION_FRAME_INTERVAL_MS,
} from './animationClock';
import {
  pickedWorldEvent,
  pickedWorldEventCluster,
  worldEventTooltipHtml,
  worldEventTooltipModel,
  type WorldEventPickedObject,
} from './hoverTooltip';
import {
  createCountryHoverQueryController,
  type CountryHoverQueryController,
} from './countryHoverController';
import { DeferredLatestCommit, scheduleAfterMainThreadYield } from './deferredCommit';
import { MapPerformanceMonitor } from './mapPerformance';
import { MapRenderScheduler, type MapRenderInvalidation } from './renderScheduler';
import { RendererTooltip } from './rendererTooltip';
import {
  countryBasemapLabels,
  visibleCountryBasemapLabels,
  type CountryBasemapLabel,
} from './countryBasemapLabels';

const COUNTRY_INTERACTION_SOURCE = 'world-event-country-interaction-source';
const FALLBACK_COUNTRY_SOURCE = 'wm-weather-country-boundaries';
const COUNTRY_INTERACTIVE_LAYER = 'world-event-country-interactive';
const COUNTRY_HOVER_FILL_LAYER = 'world-event-country-hover-fill';
const COUNTRY_HOVER_BORDER_LAYER = 'world-event-country-hover-border';
const EMPTY_COUNTRY_FILTER = ['==', ['get', 'ISO3166-1-Alpha-2'], ''] as FilterSpecification;

type MapPerformanceHarnessHost = HTMLElement & {
  __polymonitorProjectGeoPoint?: (lon: number, lat: number) => { x: number; y: number };
};

function geometryPositions(geometry: Geometry | null | undefined): Position[] {
  if (!geometry) return [];
  if (geometry.type === 'GeometryCollection') return geometry.geometries.flatMap(geometryPositions);
  const positions: Position[] = [];
  const visit = (value: unknown) => {
    if (!Array.isArray(value)) return;
    if (value.length >= 2 && typeof value[0] === 'number' && typeof value[1] === 'number') {
      positions.push(value as Position);
      return;
    }
    value.forEach(visit);
  };
  visit(geometry.coordinates);
  return positions;
}

function countryTarget(feature: { properties?: Record<string, unknown> | null; geometry?: Geometry | null } | undefined) {
  const iso2 = String(feature?.properties?.['ISO3166-1-Alpha-2'] || '').toUpperCase();
  const name = String(feature?.properties?.['name:en'] || feature?.properties?.name || iso2);
  const positions = geometryPositions(feature?.geometry);
  if (!iso2 || !positions.length) return null;
  const lons = positions.map((position) => Number(position[0])).filter(Number.isFinite);
  const lats = positions.map((position) => Number(position[1])).filter(Number.isFinite);
  if (!lons.length || !lats.length) return null;
  return {
    iso2,
    name,
    bounds: [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
  } satisfies MapCountryTarget;
}

function isAviationEvent(event: GeoEvent) {
  if (event.category !== 'infrastructure') return false;
  const entity = String(event.properties.mapEntity || '');
  return entity === 'air-route'
    || entity === 'air-hub'
    || entity === 'air-flight'
    || entity === 'live-aircraft';
}

function sameEventReferences(
  previous: readonly GeoEvent[],
  next: readonly GeoEvent[],
  predicate: (event: GeoEvent) => boolean,
) {
  let previousIndex = 0;
  let nextIndex = 0;
  while (true) {
    while (previousIndex < previous.length && !predicate(previous[previousIndex]!)) previousIndex += 1;
    while (nextIndex < next.length && !predicate(next[nextIndex]!)) nextIndex += 1;
    const previousEvent = previous[previousIndex];
    const nextEvent = next[nextIndex];
    if (!previousEvent || !nextEvent) return previousEvent === nextEvent;
    if (previousEvent !== nextEvent) return false;
    previousIndex += 1;
    nextIndex += 1;
  }
}

export class DeckMapRenderer implements MapRenderer {
  private map: MapLibreMap | null = null;
  private overlay: MapboxOverlay | null = null;
  private aviationOverlay: MapboxOverlay | null = null;
  private callbacks: MapRendererCallbacks | null = null;
  private state: WorldEventMapState | null = null;
  private events: GeoEvent[] = [];
  private fallbackApplied = false;
  private fallbackTimer: number | null = null;
  private fallbackSourceTimer: number | null = null;
  private fallbackCountryLabels: CountryBasemapLabel[] = [];
  private fallbackCountryLabelsLoading: Promise<void> | null = null;
  private contextRecoveryTimer: number | null = null;
  private contextRecoveryAttempts = 0;
  private overlayMounted = false;
  private aviationOverlayMounted = false;
  private aviationOverlayViewSync: (() => void) | null = null;
  private aviationOverlayViewSyncPaused = false;
  private aviationDeckSuspended = false;
  private paused = false;
  private destroyed = false;
  private applyingCamera = false;
  private reducedMotion = false;
  private animationFrame: number | null = null;
  private animationResumeTimer: number | null = null;
  private lastAnimationTimestamp: number | null = null;
  private pendingAnimationDeltaMs = 0;
  private animationTime = 0;
  private pointLayers: LayersList | null = null;
  private geometryLayers: LayersList = [];
  private geometryGeneration = 0;
  private geometryNeedsCommit = true;
  private readonly clusterIndex = new EventClusterIndex();
  private readonly renderScheduler: MapRenderScheduler;
  private readonly heavyGeometryCommit: DeferredLatestCommit<{
    events: GeoEvent[];
    selectedEventId: string | null;
    zoom: number;
    beforeId?: string;
    viewport?: [number, number, number, number];
    generation: number;
  }>;
  private readonly aviationLayerCommit: DeferredLatestCommit<LayersList>;
  private readonly performanceMonitor = new MapPerformanceMonitor();
  private deckHoverActive = false;
  private hoveredDeckEventId: string | null = null;
  private hoveredDeckCluster: EventCluster | null = null;
  private staticDeckHoverActive = false;
  private aviationDeckHoverActive = false;
  private hoveredCountryIso2: string | null = null;
  private countryHoverQueryController: CountryHoverQueryController<MapMouseEvent['point']> | null = null;
  private interacting = false;
  private mapDragging = false;
  private animationIntervalMs = MAP_ANIMATION_FRAME_INTERVAL_MS;
  private animationRecoveryFrames = 0;
  private cancelPickingWarmup: (() => void) | null = null;
  private pickingWarmupStage: 0 | 1 | 2 = 0;
  private hazardPulseTimer: number | null = null;
  private hazardPulseTime = Date.now();
  private readonly eventFirstSeenAt = new Map<string, number>();
  private receivedInitialEventSnapshot = false;
  private pulseEvents: GeoEvent[] = [];
  /**
   * Aviation has a different invalidation cadence from hazards: route geometry,
   * hubs and live positions are static between data/camera changes, while only
   * the small motion subset changes during an animation frame.
   */
  private aviationLayerSections: AviationStaticLayerSections | null = null;
  private aviationDynamicLayers: LayersList | null = null;
  private seededAircraftPickPoints: AviationMotionPoint[] = [];
  private manualAviationEvent: GeoEvent | null = null;
  private manualAviationTooltip: RendererTooltip | null = null;
  private basemapStyleGeneration = 0;
  private readonly quarantinedLayerIds = new Set<string>();
  private performanceHarnessHost: MapPerformanceHarnessHost | null = null;

  constructor() {
    const requestFrame = (callback: FrameRequestCallback) => typeof requestAnimationFrame === 'function'
      ? requestAnimationFrame(callback)
      : setTimeout(() => callback(performance.now()), 0) as unknown as number;
    const cancelFrame = (handle: number) => typeof cancelAnimationFrame === 'function'
      ? cancelAnimationFrame(handle)
      : clearTimeout(handle);
    this.renderScheduler = new MapRenderScheduler(
      requestFrame,
      cancelFrame,
      (invalidation) => this.flushRender(invalidation),
    );
    this.heavyGeometryCommit = new DeferredLatestCommit(
      scheduleAfterMainThreadYield,
      ({ events, selectedEventId, zoom, beforeId, viewport, generation }) => {
        if (this.destroyed || this.paused || generation !== this.geometryGeneration) return;
        this.geometryLayers = this.performanceMonitor.measure(
          'js-build',
          () => createWorldEventGeometryLayers(events, selectedEventId, zoom, beforeId, viewport),
        );
        this.geometryNeedsCommit = false;
        this.requestRender();
      },
    );
    this.aviationLayerCommit = new DeferredLatestCommit(
      scheduleAfterMainThreadYield,
      (layers) => {
        if (this.destroyed || this.paused || this.interacting || !layers.length) return;
        const overlay = this.ensureAviationOverlay();
        if (!overlay) return;
        this.resumeAviationOverlayViewSync();
        this.performanceMonitor.measure('dynamic-commit', () => {
          overlay.setProps({ layers });
        });
        this.resumeAviationDeckLoop();
      },
    );
  }

  async mount(container: HTMLElement, callbacks: MapRendererCallbacks) {
    if (this.map) return;
    this.destroyed = false;
    this.callbacks = callbacks;
    this.manualAviationTooltip = new RendererTooltip(container);
    this.emitBasemapState('initializing');
    const state = this.state;
    const primaryStyle = await getWeatherMapStyle(
      state?.basemapTheme ?? 'dark',
      state?.basemapProvider ?? 'auto',
    );
    if (this.destroyed) return;
    const map = new maplibregl.Map({
      container,
      style: primaryStyle,
      center: state ? [state.center.lon, state.center.lat] : [20, 24],
      zoom: state?.zoom ?? 1.25,
      renderWorldCopies: false,
      attributionControl: false,
      interactive: true,
      pitchWithRotate: false,
      dragRotate: false,
      touchPitch: false,
      canvasContextAttributes: { powerPreference: 'high-performance' },
    });
    this.map = map;
    if (new URLSearchParams(window.location.search).get('mapPerf') === '1') {
      this.performanceHarnessHost = container as MapPerformanceHarnessHost;
      this.performanceHarnessHost.__polymonitorProjectGeoPoint = (lon, lat) => {
        const point = map.project([lon, lat]);
        return { x: point.x, y: point.y };
      };
    }
    this.countryHoverQueryController = createCountryHoverQueryController(
      (callback) => window.requestAnimationFrame(callback),
      (handle) => window.cancelAnimationFrame(handle),
      (point) => this.runCountryHoverQuery(point),
    );

    const getTooltip = (info: PickingInfo<WorldEventPickedObject>) => {
      if (this.manualAviationEvent) return null;
      const html = worldEventTooltipHtml(info.object, info.layer?.id || '');
      return html ? { html } : null;
    };
    const getCursor = ({ isDragging, isHovering }: { isDragging: boolean; isHovering: boolean }) => {
      if (isDragging) return 'grabbing';
      return isHovering || Boolean(this.hoveredCountryIso2) ? 'pointer' : 'grab';
    };
    const onClick = (info: PickingInfo<WorldEventPickedObject>) => {
        const cluster = pickedWorldEventCluster(info.object);
        if (cluster) {
          const [west, south, east, north] = cluster.bounds;
          if (west === east && south === north) {
            map.easeTo({
              center: cluster.coordinates,
              zoom: Math.min(6, cluster.expansionZoom || map.getZoom() + 1.5),
              duration: this.reducedMotion ? 0 : 420,
            });
          } else {
            map.fitBounds(
              [[west, south], [east, north]],
              { padding: 70, maxZoom: 6, duration: this.reducedMotion ? 0 : 480 },
            );
          }
          return;
        }
        const picked = pickedWorldEvent(info.object);
        callbacks.onEventSelect(picked?.id ?? this.manualAviationEvent?.id ?? null);
    };
    const overlay = new MapboxOverlay({
      interleaved: true,
      layers: [],
      pickingRadius: 8,
      useDevicePixels: window.devicePixelRatio > 2 ? 2 : true,
      getCursor,
      getTooltip,
      onHover: (info: PickingInfo<WorldEventPickedObject>) => this.handleDeckHover('static', info),
      onClick,
      onError: (error: Error, layer?: Layer) => this.handleDeckLayerError(error, layer),
    });
    this.overlay = overlay;

    map.once('load', () => {
      if (this.destroyed) return;
      this.mountOverlaysIfNeeded();
      reinforceWorldEventBasemapLabels(map);
      this.ensureCountryHoverLayers();
      if (this.fallbackApplied) {
        if (!this.markLocalFallbackReadyIfLoaded()) this.scheduleFallbackSourceTimeout();
      } else {
        this.clearFallbackTimer();
        this.emitBasemapState('primary-ready');
      }
      this.requestRender({ points: true, aviation: true, geometry: true, dynamic: true });
      this.syncAnimationLoop();
      this.syncHazardPulseLoop();
    });
    map.on('style.load', this.handleStyleLoad);
    map.on('sourcedata', this.handleSourceData);
    map.on('moveend', this.handleMoveEnd);
    map.on('movestart', this.handleMoveStart);
    map.on('mousemove', this.handleCountryHoverMove);
    map.on('click', this.handleManualAviationClick);
    map.on('click', this.handleCountryClick);
    map.on('contextmenu', this.handleCountryContextMenu);
    map.on('mouseout', this.handleCountryHoverLeave);
    map.on('error', this.handleMapError);
    map.getCanvas().addEventListener('webglcontextlost', this.handleContextLost);
    map.getCanvas().addEventListener('webglcontextrestored', this.handleContextRestored);
    map.getCanvas().addEventListener('mousedown', this.handlePointerDown, { capture: true });
    window.addEventListener('mouseup', this.handlePointerUp, { capture: true });

    this.fallbackTimer = window.setTimeout(() => {
      if (!this.map || this.fallbackApplied || this.destroyed) return;
      this.applyLocalFallback(new Error('Primary basemap did not become ready within 10 seconds.'));
    }, 10_000);
  }

  setState(state: WorldEventMapState) {
    const previous = this.state;
    this.state = state;
    if (previous && (previous.basemapProvider !== state.basemapProvider
      || previous.basemapTheme !== state.basemapTheme)) {
      void this.replaceBasemapStyle(state);
    }
    if (!previous || previous.selectedEventId !== state.selectedEventId) {
      this.pulseEvents = selectEventPulseCandidates(this.events, state.selectedEventId);
    }
    const map = this.map;
    if (map && (!previous
      || Math.abs(previous.center.lon - state.center.lon) > 0.0001
      || Math.abs(previous.center.lat - state.center.lat) > 0.0001
      || Math.abs(previous.zoom - state.zoom) > 0.001)) {
      const current = map.getCenter();
      if (Math.abs(current.lng - state.center.lon) > 0.0001
        || Math.abs(current.lat - state.center.lat) > 0.0001
        || Math.abs(map.getZoom() - state.zoom) > 0.001) {
        this.applyingCamera = true;
        map.easeTo({
          center: [state.center.lon, state.center.lat],
          zoom: state.zoom,
          duration: this.reducedMotion ? 0 : 260,
          essential: true,
        });
      }
    }
    const staticLayersChanged = !previous
      || previous.zoom !== state.zoom
      || previous.selectedEventId !== state.selectedEventId
      || previous.timeRange !== state.timeRange
      || previous.severities.join(',') !== state.severities.join(',')
      || previous.activeLayerIds.join(',') !== state.activeLayerIds.join(',');
    const aviationLayersChanged = !previous
      || previous.zoom !== state.zoom
      || previous.selectedEventId !== state.selectedEventId
      || previous.activeLayerIds.join(',') !== state.activeLayerIds.join(',')
      || previous.aviationLens !== state.aviationLens
      || previous.aviationRiskSource !== state.aviationRiskSource;
    if (staticLayersChanged) this.pointLayers = null;
    if (aviationLayersChanged) this.aviationLayerSections = null;
    if (aviationLayersChanged) this.aviationDynamicLayers = null;
    const geometryChanged = !previous
      || previous.zoom !== state.zoom
      || previous.selectedEventId !== state.selectedEventId
      || previous.timeRange !== state.timeRange
      || previous.severities.join(',') !== state.severities.join(',')
      || previous.activeLayerIds.join(',') !== state.activeLayerIds.join(',');
    if (geometryChanged) this.invalidateGeometry();
    this.requestRender({
      points: staticLayersChanged,
      aviation: aviationLayersChanged,
      geometry: geometryChanged,
      dynamic: aviationLayersChanged,
      pulse: previous?.selectedEventId !== state.selectedEventId,
      interaction: previous?.selectedEventId !== state.selectedEventId,
    });
    this.syncAnimationLoop();
    this.syncHazardPulseLoop();
  }

  setEvents(events: GeoEvent[]) {
    if (events === this.events) return;
    const previousEvents = this.events;
    const nonAviationChanged = !sameEventReferences(previousEvents, events, (event) => !isAviationEvent(event));
    const aviationChanged = !sameEventReferences(previousEvents, events, isAviationEvent);
    if (!nonAviationChanged && !aviationChanged) {
      this.events = events;
      return;
    }
    const previousIds = new Set(previousEvents.map((event) => event.id));
    const now = Date.now();
    if (this.receivedInitialEventSnapshot) {
      for (const event of events) {
        if (!previousIds.has(event.id)) this.eventFirstSeenAt.set(event.id, now);
      }
    } else if (events.length > 0) {
      this.receivedInitialEventSnapshot = true;
    }
    const nextIds = new Set(events.map((event) => event.id));
    for (const eventId of this.eventFirstSeenAt.keys()) {
      if (!nextIds.has(eventId)) this.eventFirstSeenAt.delete(eventId);
    }
    this.events = events;
    if (nonAviationChanged) {
      this.pulseEvents = selectEventPulseCandidates(events, this.state?.selectedEventId || null);
      this.clusterIndex.update(events);
      this.pointLayers = null;
      this.invalidateGeometry();
    }
    if (aviationChanged) {
      this.aviationLayerSections = null;
      this.aviationDynamicLayers = null;
    }
    this.requestRender({
      points: nonAviationChanged,
      aviation: aviationChanged,
      geometry: nonAviationChanged,
      dynamic: aviationChanged,
      pulse: nonAviationChanged,
      interaction: true,
    });
    this.syncAnimationLoop();
    this.syncHazardPulseLoop();
  }

  resize() {
    this.map?.resize();
  }

  setReducedMotion(reduced: boolean) {
    this.reducedMotion = reduced;
    this.syncAnimationLoop();
    this.syncHazardPulseLoop();
    this.requestRender({ dynamic: true, pulse: true });
  }

  fitCountry(country: MapCountryTarget) {
    this.map?.fitBounds(country.bounds, {
      padding: 64,
      maxZoom: 5.5,
      duration: this.reducedMotion ? 0 : 480,
    });
  }

  pause() {
    this.paused = true;
    this.clearAllHover();
    this.cancelAnimationLoop();
    this.cancelAnimationResume();
    this.cancelHazardPulseLoop();
    this.cancelPickingWarmup?.();
    this.cancelPickingWarmup = null;
    this.cancelStagedAviationCommit();
    this.renderScheduler.cancel();
    this.heavyGeometryCommit.cancel();
    this.geometryNeedsCommit = true;
    this.overlay?.setProps({ layers: [] });
    this.aviationOverlay?.setProps({ layers: [] });
  }

  resume() {
    if (!this.paused) return;
    this.paused = false;
    this.resize();
    this.requestRender({ points: true, aviation: true, geometry: true, dynamic: true });
    this.syncAnimationLoop();
    this.syncHazardPulseLoop();
  }

  destroy() {
    this.destroyed = true;
    this.basemapStyleGeneration += 1;
    this.clearFallbackTimer();
    this.clearFallbackSourceTimer();
    this.clearContextRecoveryTimer();
    this.cancelAnimationLoop();
    this.cancelAnimationResume();
    this.cancelHazardPulseLoop();
    this.cancelPickingWarmup?.();
    this.cancelPickingWarmup = null;
    this.cancelStagedAviationCommit();
    this.renderScheduler.cancel();
    this.heavyGeometryCommit.cancel();
    this.performanceMonitor.destroy();
    this.manualAviationTooltip?.destroy();
    this.manualAviationTooltip = null;
    this.countryHoverQueryController?.cancel();
    this.clearAllHover();
    if (this.performanceHarnessHost) {
      delete this.performanceHarnessHost.__polymonitorProjectGeoPoint;
      this.performanceHarnessHost = null;
    }
    const map = this.map;
    if (map) {
      map.off('style.load', this.handleStyleLoad);
      map.off('sourcedata', this.handleSourceData);
      map.off('moveend', this.handleMoveEnd);
      map.off('movestart', this.handleMoveStart);
      map.off('mousemove', this.handleCountryHoverMove);
      map.off('click', this.handleManualAviationClick);
      map.off('click', this.handleCountryClick);
      map.off('contextmenu', this.handleCountryContextMenu);
      map.off('mouseout', this.handleCountryHoverLeave);
      map.off('error', this.handleMapError);
      map.getCanvas().removeEventListener('webglcontextlost', this.handleContextLost);
      map.getCanvas().removeEventListener('webglcontextrestored', this.handleContextRestored);
      map.getCanvas().removeEventListener('mousedown', this.handlePointerDown, { capture: true });
      window.removeEventListener('mouseup', this.handlePointerUp, { capture: true });
      if (this.overlay) {
        try {
          map.removeControl(this.overlay as unknown as maplibregl.IControl);
        } catch {
          // MapLibre may already be tearing the style down.
        }
      }
      if (this.aviationOverlay) {
        if (this.aviationOverlayViewSync) {
          map.off('render', this.aviationOverlayViewSync);
        }
        try {
          map.removeControl(this.aviationOverlay as unknown as maplibregl.IControl);
        } catch {
          // MapLibre may already be tearing the style down.
        }
      }
      map.remove();
    }
    this.map = null;
    this.overlay = null;
    this.aviationOverlay = null;
    this.pointLayers = null;
    this.geometryLayers = [];
    this.aviationLayerSections = null;
    this.aviationDynamicLayers = null;
    this.seededAircraftPickPoints = [];
    this.manualAviationEvent = null;
    this.fallbackCountryLabels = [];
    this.fallbackCountryLabelsLoading = null;
    this.overlayMounted = false;
    this.aviationOverlayMounted = false;
    this.aviationOverlayViewSync = null;
    this.aviationOverlayViewSyncPaused = false;
    this.countryHoverQueryController = null;
    this.staticDeckHoverActive = false;
    this.aviationDeckHoverActive = false;
    this.deckHoverActive = false;
    this.hoveredDeckEventId = null;
    this.hoveredCountryIso2 = null;
    this.callbacks = null;
    this.quarantinedLayerIds.clear();
  }

  private requestRender(invalidation: Partial<MapRenderInvalidation> = {}) {
    // Interaction start clears hover state. That cleanup used to enqueue a
    // fresh dynamic deck commit after beginMapInteraction() had cancelled the
    // pending frame, so the supposedly paused aviation canvas still paid a
    // full GPU draw on the first drag frame. All invalidated caches remain on
    // the renderer and are committed once pointerup/moveend resumes it.
    if (this.destroyed || this.paused || this.interacting || !this.overlay || !this.state) return;
    this.renderScheduler.request(invalidation);
  }

  private invalidateGeometry() {
    this.geometryGeneration += 1;
    this.geometryNeedsCommit = true;
  }

  private flushRender(invalidation: MapRenderInvalidation) {
    if (this.paused || !this.overlay || !this.state) return;
    const bounds = this.map?.getBounds();
    const viewport: [number, number, number, number] | undefined = bounds
      ? bounds.getWest() <= bounds.getEast()
        ? [
            Math.max(-180, bounds.getWest() - 8),
            Math.max(-85, bounds.getSouth() - 5),
            Math.min(180, bounds.getEast() + 8),
            Math.min(85, bounds.getNorth() + 5),
          ]
        : [-180, -85, 180, 85]
      : undefined;
    if ((invalidation.points || !this.pointLayers)) {
      const map = this.map;
      const project = map
        ? (position: [number, number]) => {
            const point = map.project(position);
            return Number.isFinite(point.x) && Number.isFinite(point.y) ? { x: point.x, y: point.y } : null;
          }
        : undefined;
      const occupiedScreenBoxes: [number, number, number, number][] = [];
      if (map) {
        try {
          const symbolLayerIds = (map.getStyle()?.layers || [])
            .filter((layer) => layer.type === 'symbol')
            .map((layer) => layer.id);
          const visibleLabels = symbolLayerIds.length
            ? map.queryRenderedFeatures(undefined, { layers: symbolLayerIds })
            : [];
          for (const feature of visibleLabels.slice(0, 500)) {
            if (feature.geometry?.type !== 'Point') continue;
            const coordinates = feature.geometry.coordinates as [number, number];
            const screen = project?.(coordinates);
            if (!screen) continue;
            const properties = feature.properties || {};
            const text = String(properties.name_en || properties.name || properties.name_int || '');
            if (!text) continue;
            const width = Math.min(220, Math.max(30, text.length * 7));
            occupiedScreenBoxes.push([
              screen.x - width / 2, screen.y - 9, screen.x + width / 2, screen.y + 9,
            ]);
          }
        } catch {
          // A basemap may be mid-style-reload; event labels are recomputed on
          // the next style/data render without blocking the map.
        }
      }
      this.pointLayers = this.performanceMonitor.measure(
        'js-build',
        () => createWorldEventPointLayers(
          this.events,
          this.state!,
          viewport,
          this.clusterIndex,
          project,
          occupiedScreenBoxes,
        ),
      );
    }
    if (invalidation.geometry || this.geometryNeedsCommit) {
      this.heavyGeometryCommit.stage({
        events: this.events,
        selectedEventId: this.state.selectedEventId,
        zoom: this.state.zoom,
        beforeId: this.map?.getStyle()?.layers?.find((layer) => layer.type === 'symbol')?.id,
        viewport,
        generation: this.geometryGeneration,
      });
    }
    const aviationActive = this.state.activeLayerIds.includes('air-routes');
    if (!aviationActive) this.aviationLayerSections = null;
    else if (invalidation.aviation || !this.aviationLayerSections) {
      this.aviationLayerSections = this.performanceMonitor.measure(
        'js-build',
        () => createAviationStaticLayerSections(this.events, this.state!, viewport),
      );
    }
    const aviationSections = this.aviationLayerSections;
    const onlyDynamic = (invalidation.dynamic || invalidation.pulse || invalidation.interaction)
      && !invalidation.points
      && !invalidation.aviation
      && !invalidation.geometry;
    const aviationDynamicChanged = invalidation.aviation
      || invalidation.dynamic
      || invalidation.pulse
      || invalidation.interaction
      || this.aviationDynamicLayers == null;
    if (aviationDynamicChanged) {
      this.aviationDynamicLayers = aviationSections
        ? this.performanceMonitor.measure(
          onlyDynamic ? 'dynamic-build' : 'js-build',
          () => createAviationDynamicLayers(
            aviationSections.data,
            this.animationTime,
            this.state!.zoom,
            this.state!.selectedEventId,
            this.hoveredDeckEventId,
          ),
        )
        : [];
    }
    const aviationDynamicLayers = this.aviationDynamicLayers || [];
    const pointLayerList = (this.pointLayers || []).filter(
      (layer): layer is Layer => Boolean(layer) && !Array.isArray(layer),
    ).filter((layer) => !this.quarantinedLayerIds.has(layer.id));
    const aviationDynamicLayerList = aviationDynamicLayers.filter(
      (layer): layer is Layer => Boolean(layer) && !Array.isArray(layer),
    ).filter((layer) => !this.quarantinedLayerIds.has(layer.id));
    const pointLabels = pointLayerList.filter((layer) => (
      layer?.id === 'world-event-labels' || layer?.id === 'world-event-cluster-counts'
    ));
    const pointBaseLayers = pointLayerList.filter((layer) => !pointLabels.includes(layer));
    const routeRunnerLayers = aviationDynamicLayerList.filter((layer) => layer.id === 'aviation-route-runners');
    const seededAircraftLayers = aviationDynamicLayerList.filter((layer) => layer.id === 'aviation-seeded-aircraft');
    const seededAircraftLayer = seededAircraftLayers[0];
    this.seededAircraftPickPoints = Array.isArray(seededAircraftLayer?.props.data)
      ? seededAircraftLayer.props.data as AviationMotionPoint[]
      : [];
    const seededInteractionLayers = aviationDynamicLayerList.filter((layer) => (
      layer.id.startsWith('aviation-seeded-hover-') || layer.id.startsWith('aviation-seeded-selected-')
    ));
    const aviationCountLabels = aviationDynamicLayerList.filter((layer) => layer.id.endsWith('-counts'));
    const pulseLayers = this.reducedMotion
      ? []
      : createEventPulseLayers({
        events: this.pulseEvents,
        selectedEventId: this.state.selectedEventId,
        firstSeenAt: this.eventFirstSeenAt,
        pulseTime: this.hazardPulseTime,
        zoom: this.state.zoom,
      });
    const interactionLayers = createEventInteractionLayers(
      this.events,
      this.state.selectedEventId,
      this.hoveredDeckEventId,
      this.hoveredDeckCluster,
    );
    const staticBaseLayers = [
      ...this.geometryLayers.filter(
        (layer): layer is Layer => Boolean(layer) && !Array.isArray(layer),
      ).filter((layer) => !this.quarantinedLayerIds.has(layer.id)),
      ...(aviationSections?.routeLayers || []),
      ...pointBaseLayers,
      // Keep genuinely static aviation objects and text out of the animation
      // canvas. Drawing hubs, live snapshots and every label at 25 fps made a
      // small runner update repaint hundreds of unchanged glyphs.
      ...(aviationSections?.hubLayers || []),
      ...(aviationSections?.aircraftLayers || []),
    ];
    const staticLabelLayers = [
      ...this.createFallbackCountryLabelLayers(),
      ...pointLabels,
      ...(aviationSections?.labelLayers || []),
    ].filter((layer): layer is Layer => Boolean(layer) && !Array.isArray(layer))
      .filter((layer) => !this.quarantinedLayerIds.has(layer.id));
    const aviationMotionLayers = [
      ...routeRunnerLayers,
      ...seededAircraftLayers,
      ...seededInteractionLayers,
      ...aviationCountLabels,
    ];
    const staticCompleteLayers = [
      ...staticBaseLayers,
      ...pulseLayers,
      ...interactionLayers,
      ...staticLabelLayers,
    ];
    if (!onlyDynamic) {
      // Commit each static layer instance exactly once. The former two-step
      // base/label path first removed the previous label layers (which makes
      // deck.gl finalize them) and then reinserted the same cached instances
      // after yielding. Layer._initialize correctly rejects that finalized
      // instance reuse, leaving cluster counts and aviation labels missing.
      // Heavy polygon work still uses the independent latest-only yielded
      // geometry commit above; static labels are small enough for one atomic
      // deck commit and must remain attached across updates.
      this.suspendAviationDeckLoop();
      this.performanceMonitor.measure('deck-commit', () => {
        this.overlay?.setProps({ layers: staticCompleteLayers });
      });
    } else if (invalidation.pulse || invalidation.interaction) {
      // Hazard pulses and hover state belong to the static renderer. Aircraft
      // ticks therefore no longer repaint the labelled map canvas.
      this.performanceMonitor.measure('deck-commit', () => {
        this.overlay?.setProps({ layers: staticCompleteLayers });
      });
    }
    if (aviationActive && aviationMotionLayers.length) {
      this.ensureAviationOverlay();
      if (onlyDynamic && !invalidation.pulse && !invalidation.interaction && this.aviationOverlay) {
        this.performanceMonitor.measure('dynamic-commit', () => {
          this.aviationOverlay?.setProps({ layers: aviationMotionLayers });
        });
      } else {
        // WorldMonitor-style latest commit: yield once, discard superseded
        // payloads, and never sleep for a fixed 900ms.
        this.aviationLayerCommit.stage(aviationMotionLayers);
      }
    } else {
      this.removeAviationOverlay();
    }
    if (!onlyDynamic) {
      this.map?.triggerRepaint();
      this.schedulePickingWarmup();
    }
  }

  /**
   * deck.gl compiles its picking passes on first use. Paying that cost on the
   * user's first hover or drag creates a visible one-off stall, so warm the two
   * canvases in separate yielded tasks after their first real layer commit.
   */
  private schedulePickingWarmup() {
    if (this.pickingWarmupStage === 2 || this.cancelPickingWarmup || this.interacting
      || this.paused || this.destroyed || !this.overlay) return;
    this.cancelPickingWarmup = scheduleAfterMainThreadYield(() => {
      this.cancelPickingWarmup = null;
      if (this.interacting || this.paused || this.destroyed) return;
      if (this.warmOverlayPicking(this.overlay)) {
        // The motion overlay is deliberately non-pickable; animated aircraft
        // use the renderer's bounded CPU hit test. Warming a second GPU picking
        // pass only wakes both Deck loops during first paint.
        this.pickingWarmupStage = 2;
      } else {
        // A context may still be initializing. A later static commit retries.
        return;
      }
    });
  }

  private warmOverlayPicking(overlay: MapboxOverlay | null) {
    if (!overlay) return false;
    const deck = (overlay as unknown as { _deck?: { isInitialized?: boolean } })._deck;
    const canvas = overlay.getCanvas();
    if (!deck?.isInitialized || !canvas?.clientWidth || !canvas.clientHeight) return false;
    try {
      overlay.pickObject({
        x: Math.round(canvas.clientWidth / 2),
        y: Math.round(canvas.clientHeight / 2),
        radius: 1,
      });
      return true;
    } catch {
      return false;
    }
  }

  private handleMoveEnd = () => {
    const map = this.map;
    if (!map || !this.callbacks) return;
    refreshWorldEventBasemapLabelDensity(map);
    const zoomChanged = Math.abs(map.getZoom() - (this.state?.zoom ?? map.getZoom())) > 0.001;
    this.mapDragging = false;
    this.interacting = false;
    if (!zoomChanged) this.resumeAviationOverlayViewSync();
    this.pointLayers = null;
    if (zoomChanged) {
      this.aviationLayerSections = null;
      this.aviationDynamicLayers = null;
    }
    this.invalidateGeometry();
    this.requestRender({ points: true, aviation: zoomChanged, geometry: true, dynamic: zoomChanged });
    this.scheduleAnimationResume();
    this.syncHazardPulseLoop();
    const center = map.getCenter();
    const camera = { center: { lon: center.lng, lat: center.lat }, zoom: map.getZoom() };
    if (this.applyingCamera) {
      this.applyingCamera = false;
      const state = this.state;
      if (state
        && Math.abs(state.center.lon - camera.center.lon) < 0.0001
        && Math.abs(state.center.lat - camera.center.lat) < 0.0001
        && Math.abs(state.zoom - camera.zoom) < 0.001) return;
    }
    this.callbacks.onCameraChange(camera);
  };

  private handleMoveStart = () => {
    this.mapDragging = true;
    this.beginMapInteraction();
  };

  private handlePointerDown = () => {
    this.beginMapInteraction();
  };

  private handlePointerUp = () => {
    window.requestAnimationFrame(() => {
      if (this.destroyed || this.paused || this.mapDragging || !this.interacting) return;
      this.interacting = false;
      this.resumeAviationOverlayViewSync();
      this.requestRender({ dynamic: true, pulse: true, interaction: true });
      this.syncAnimationLoop();
      this.syncHazardPulseLoop();
    });
  };

  private beginMapInteraction() {
    if (this.interacting || this.destroyed || this.paused) return;
    this.interacting = true;
    this.cancelAnimationLoop();
    this.cancelAnimationResume();
    this.cancelHazardPulseLoop();
    // The non-interleaved motion canvas otherwise performs a synchronous deck
    // redraw on every MapLibre drag frame even though its animation clock is
    // paused. Freeze and hide it until moveend; the labelled basemap, routes
    // and stable events remain visible in the interleaved canvas.
    this.renderScheduler.cancel();
    this.cancelStagedAviationCommit();
    this.pauseAviationOverlayViewSync();
    this.countryHoverQueryController?.cancel();
    this.clearAllHover();
  }

  private handleDeckHover(
    source: 'static' | 'aviation',
    info: PickingInfo<WorldEventPickedObject>,
  ) {
    const previousHoveredEventId = this.hoveredDeckEventId;
    const previousHoveredClusterId = this.hoveredDeckCluster?.id || null;
    if (source === 'static') this.staticDeckHoverActive = Boolean(info.object);
    else this.aviationDeckHoverActive = Boolean(info.object);
    this.deckHoverActive = this.staticDeckHoverActive || this.aviationDeckHoverActive;
    const pickedId = pickedWorldEvent(info.object)?.id || null;
    const pickedCluster = pickedWorldEventCluster(info.object);
    if (pickedCluster) this.hoveredDeckCluster = pickedCluster;
    else if (source === 'static' ? !this.aviationDeckHoverActive : !this.staticDeckHoverActive) {
      this.hoveredDeckCluster = null;
    }
    if (pickedId) this.hoveredDeckEventId = pickedId;
    else if (source === 'static' ? !this.aviationDeckHoverActive : !this.staticDeckHoverActive) {
      this.hoveredDeckEventId = null;
    }
    if (previousHoveredEventId !== this.hoveredDeckEventId
      || previousHoveredClusterId !== (this.hoveredDeckCluster?.id || null)) {
      this.requestRender({ interaction: true });
    }
    this.updateMapCursor();
  }

  private handleCountryHoverMove = (event: MapMouseEvent) => {
    if (this.destroyed || this.paused) return;
    // A static Deck picking readback and an aviation Deck draw in the same RAF
    // serialize on the GPU. Freeze the motion loop while the pointer is active
    // and restart it shortly after pointer traffic becomes idle.
    this.suspendAviationDeckLoop();
    this.cancelAnimationLoop();
    this.scheduleAnimationResume(120);
    this.handleManualAviationHover(event);
    this.countryHoverQueryController?.queue(event.point);
  };

  private handleManualAviationHover(event: MapMouseEvent) {
    const map = this.map;
    if (!map || this.interacting || !this.seededAircraftPickPoints.length) {
      this.clearManualAviationHover();
      return;
    }
    let nearest: AviationMotionPoint | null = null;
    let nearestDistance = 12 * 12;
    for (const point of this.seededAircraftPickPoints) {
      const screen = map.project(point.position);
      const dx = screen.x - event.point.x;
      const dy = screen.y - event.point.y;
      const distance = dx * dx + dy * dy;
      if (distance <= nearestDistance) {
        nearest = point;
        nearestDistance = distance;
      }
    }
    if (!nearest) {
      this.clearManualAviationHover();
      return;
    }
    const changed = this.manualAviationEvent?.id !== nearest.event.id;
    this.manualAviationEvent = nearest.event;
    this.aviationDeckHoverActive = true;
    this.deckHoverActive = true;
    this.hoveredDeckEventId = nearest.event.id;
    this.manualAviationTooltip?.show(
      worldEventTooltipModel(nearest.event, 'aviation-seeded-aircraft'),
      { x: event.point.x + 14, y: event.point.y + 14 },
    );
    if (changed) this.requestRender({ interaction: true });
    this.updateMapCursor();
  }

  private handleManualAviationClick = () => {
    if (this.manualAviationEvent) this.callbacks?.onEventSelect(this.manualAviationEvent.id);
  };

  private countryAtPoint(point: MapMouseEvent['point']) {
    const map = this.map;
    if (!map?.getLayer(COUNTRY_INTERACTIVE_LAYER)) return null;
    try {
      const feature = map.queryRenderedFeatures(point, { layers: [COUNTRY_INTERACTIVE_LAYER] })[0];
      return countryTarget(feature as unknown as { properties?: Record<string, unknown>; geometry?: Geometry });
    } catch {
      return null;
    }
  }

  private handleCountryClick = (event: MapMouseEvent) => {
    // MapLibre emits `click` only when its drag tolerance was not exceeded.
    // `mousedown` deliberately marks the renderer as interacting before that
    // click, so checking `this.interacting` here made every genuine country
    // click impossible. Deck/manual aviation ownership is still respected.
    if (this.deckHoverActive || this.manualAviationEvent) return;
    const country = this.countryAtPoint(event.point);
    this.callbacks?.onCountrySelect(country, country ? { x: event.point.x, y: event.point.y } : undefined);
  };

  private handleCountryContextMenu = (event: MapMouseEvent) => {
    const country = this.countryAtPoint(event.point);
    if (!country) return;
    event.preventDefault();
    this.callbacks?.onCountryContextMenu(country, { x: event.point.x, y: event.point.y });
  };

  private clearManualAviationHover() {
    if (!this.manualAviationEvent) return;
    const previousId = this.manualAviationEvent.id;
    this.manualAviationEvent = null;
    this.manualAviationTooltip?.clear();
    this.aviationDeckHoverActive = false;
    this.deckHoverActive = this.staticDeckHoverActive;
    if (this.hoveredDeckEventId === previousId && !this.staticDeckHoverActive) {
      this.hoveredDeckEventId = null;
      this.requestRender({ interaction: true });
    }
    this.updateMapCursor();
  }

  private handleCountryHoverLeave = () => {
    this.countryHoverQueryController?.cancel();
    this.clearAllHover();
  };

  private handleStyleLoad = () => {
    if (!this.map || this.destroyed) return;
    if (typeof performance !== 'undefined'
      && performance.getEntriesByName('polymonitor:map:first-basemap').length === 0) {
      performance.mark('polymonitor:map:first-basemap');
    }
    reinforceWorldEventBasemapLabels(this.map);
    this.ensureCountryHoverLayers();
    if (this.fallbackApplied) this.markLocalFallbackReadyIfLoaded();
    this.invalidateGeometry();
    this.requestRender({ points: true, aviation: true, geometry: true, dynamic: true });
  };

  private handleSourceData = (event: MapSourceDataEvent) => {
    if (!this.fallbackApplied || event.sourceId !== FALLBACK_COUNTRY_SOURCE) return;
    if (!this.markLocalFallbackReadyIfLoaded()) return;
    this.mountOverlaysIfNeeded();
    this.ensureCountryHoverLayers();
    this.requestRender({ points: true, aviation: true, geometry: true, dynamic: true });
  };

  private mountOverlaysIfNeeded() {
    const map = this.map;
    if (!map || this.destroyed) return;
    if (this.overlay && !this.overlayMounted) {
      map.addControl(this.overlay as unknown as maplibregl.IControl);
      this.overlayMounted = true;
    }
  }

  private ensureAviationOverlay() {
    const map = this.map;
    if (!map || this.destroyed || this.paused || this.interacting) return null;
    if (!this.aviationOverlay) {
      this.aviationOverlay = new MapboxOverlay({
        interleaved: false,
        layers: [],
        // Only moving aircraft and 2-4px route runners use this canvas. The
        // labelled basemap and all static objects keep their full DPR.
        useDevicePixels: Math.min(1, Math.max(0.6, window.devicePixelRatio * 0.6)),
        onError: (error: Error, layer?: Layer) => this.handleDeckLayerError(error, layer),
      });
      this.aviationDeckSuspended = false;
    }
    if (!this.aviationOverlayMounted) {
      map.addControl(this.aviationOverlay as unknown as maplibregl.IControl);
      this.aviationOverlayMounted = true;
      const nativeViewSync = (this.aviationOverlay as unknown as {
        _updateViewState?: () => void;
      })._updateViewState || null;
      if (nativeViewSync) {
        map.off('render', nativeViewSync);
        this.aviationOverlayViewSync = () => {
          if (!this.interacting && !this.paused && !this.destroyed) nativeViewSync();
        };
      }
    }
    return this.aviationOverlay;
  }

  private removeAviationOverlay() {
    this.aviationLayerCommit.cancel();
    this.cancelAnimationLoop();
    this.seededAircraftPickPoints = [];
    this.clearManualAviationHover();
    const overlay = this.aviationOverlay;
    const map = this.map;
    if (overlay) overlay.setProps({ layers: [] });
    if (map && overlay && this.aviationOverlayMounted) {
      try {
        map.removeControl(overlay as unknown as maplibregl.IControl);
      } catch {
        // MapLibre may already be replacing its style or tearing down.
      }
    }
    this.aviationOverlay = null;
    this.aviationOverlayMounted = false;
    this.aviationOverlayViewSync = null;
    this.aviationOverlayViewSyncPaused = false;
    this.aviationDeckSuspended = false;
  }

  private pauseAviationOverlayViewSync() {
    this.aviationOverlayViewSyncPaused = true;
    this.suspendAviationDeckLoop();
    const canvas = this.aviationOverlay?.getCanvas();
    if (canvas) canvas.style.visibility = 'hidden';
  }

  private cancelStagedAviationCommit() {
    this.aviationLayerCommit.cancel();
  }

  private resumeAviationOverlayViewSync() {
    const sync = this.aviationOverlayViewSync;
    if (sync && this.aviationOverlayViewSyncPaused) {
      this.aviationOverlayViewSyncPaused = false;
      this.resumeAviationDeckLoop();
      sync();
    }
    const canvas = this.aviationOverlay?.getCanvas();
    if (canvas) canvas.style.visibility = '';
  }

  private suspendAviationDeckLoop() {
    if (this.aviationDeckSuspended) return;
    const deck = (this.aviationOverlay as unknown as {
      _deck?: { animationLoop?: { stop?: () => void } };
    } | null)?._deck;
    deck?.animationLoop?.stop?.();
    this.aviationDeckSuspended = true;
  }

  private resumeAviationDeckLoop() {
    if (!this.aviationDeckSuspended || this.destroyed || this.paused || this.interacting) return;
    const deck = (this.aviationOverlay as unknown as {
      _deck?: { animationLoop?: { start?: () => void } };
    } | null)?._deck;
    deck?.animationLoop?.start?.();
    this.aviationDeckSuspended = false;
  }

  private markLocalFallbackReadyIfLoaded() {
    const map = this.map;
    if (!map || !this.fallbackApplied || this.destroyed) return false;
    try {
      if (!map.getSource(FALLBACK_COUNTRY_SOURCE)
        || !map.isSourceLoaded(FALLBACK_COUNTRY_SOURCE)) return false;
    } catch {
      return false;
    }
    this.clearFallbackTimer();
    this.clearFallbackSourceTimer();
    this.ensureFallbackCountryLabels();
    this.emitBasemapState('local-fallback-ready');
    return true;
  }

  private ensureFallbackCountryLabels() {
    if (this.fallbackCountryLabels.length || this.fallbackCountryLabelsLoading || this.destroyed) return;
    this.fallbackCountryLabelsLoading = fetch('/map-data/world-countries.geojson')
      .then(async (response) => {
        if (!response.ok) throw new Error(`Country label geometry returned HTTP ${response.status}`);
        return response.json() as Promise<FeatureCollection>;
      })
      .then((countries) => {
        if (this.destroyed) return;
        this.fallbackCountryLabels = countryBasemapLabels(countries);
        this.requestRender({ points: true });
      })
      .catch((error) => {
        if (!this.destroyed) this.callbacks?.onError(
          error instanceof Error ? error : new Error(String(error)),
        );
      })
      .finally(() => {
        this.fallbackCountryLabelsLoading = null;
      });
  }

  private createFallbackCountryLabelLayers(): LayersList {
    if (!this.fallbackApplied || !this.fallbackCountryLabels.length || !this.state) return [];
    const zoom = this.state.zoom;
    return [new TextLayer<CountryBasemapLabel>({
      id: 'world-event-fallback-country-labels',
      data: visibleCountryBasemapLabels(this.fallbackCountryLabels, zoom),
      pickable: false,
      billboard: true,
      characterSet: 'auto',
      fontFamily: 'DejaVu Sans Mono, monospace',
      fontWeight: 700,
      sizeUnits: 'pixels',
      getPosition: (label) => label.coordinates,
      getText: (label) => label.name.toUpperCase(),
      getSize: zoom < 2.4 ? 10 : zoom < 4 ? 11 : 12,
      getColor: [134, 146, 151, 205],
      getTextAnchor: 'middle',
      getAlignmentBaseline: 'center',
      parameters: { depthWriteEnabled: false },
    })];
  }

  private ensureCountryHoverLayers() {
    const map = this.map;
    if (!map || this.destroyed) return;
    try {
      const sourceId = map.getSource(FALLBACK_COUNTRY_SOURCE)
        ? FALLBACK_COUNTRY_SOURCE
        : COUNTRY_INTERACTION_SOURCE;
      if (!map.getSource(sourceId)) {
        map.addSource(sourceId, {
          type: 'geojson',
          data: '/map-data/world-countries.geojson',
        });
      }
      const beforeId = map.getStyle()?.layers?.find((layer) => layer.type === 'symbol')?.id;
      if (!map.getLayer(COUNTRY_INTERACTIVE_LAYER)) {
        map.addLayer({
          id: COUNTRY_INTERACTIVE_LAYER,
          type: 'fill',
          source: sourceId,
          paint: { 'fill-color': '#ffffff', 'fill-opacity': 0 },
        }, beforeId);
      }
      if (!map.getLayer(COUNTRY_HOVER_FILL_LAYER)) {
        map.addLayer({
          id: COUNTRY_HOVER_FILL_LAYER,
          type: 'fill',
          source: sourceId,
          paint: { 'fill-color': '#ffffff', 'fill-opacity': 0.055 },
          filter: EMPTY_COUNTRY_FILTER,
        }, beforeId);
      }
      if (!map.getLayer(COUNTRY_HOVER_BORDER_LAYER)) {
        map.addLayer({
          id: COUNTRY_HOVER_BORDER_LAYER,
          type: 'line',
          source: sourceId,
          paint: {
            'line-color': '#d8f7ff',
            'line-width': 1.35,
            'line-opacity': 0.56,
          },
          filter: EMPTY_COUNTRY_FILTER,
        }, beforeId);
      }
    } catch {
      // Style replacement can race the async local country source load.
    }
  }

  private runCountryHoverQuery(point: MapMouseEvent['point']) {
    const map = this.map;
    if (!map?.getLayer(COUNTRY_INTERACTIVE_LAYER)) return;
    try {
      const feature = map.queryRenderedFeatures(point, { layers: [COUNTRY_INTERACTIVE_LAYER] })[0];
      const iso2 = String(feature?.properties?.['ISO3166-1-Alpha-2'] || '');
      if (iso2 === this.hoveredCountryIso2) return;
      this.hoveredCountryIso2 = iso2 || null;
      const filter = iso2
        ? ['==', ['get', 'ISO3166-1-Alpha-2'], iso2] as FilterSpecification
        : EMPTY_COUNTRY_FILTER;
      map.setFilter(COUNTRY_HOVER_FILL_LAYER, filter);
      map.setFilter(COUNTRY_HOVER_BORDER_LAYER, filter);
      this.updateMapCursor();
    } catch {
      // The style may be changing between pointer sampling and feature query.
    }
  }

  private clearCountryHover() {
    this.hoveredCountryIso2 = null;
    const map = this.map;
    if (map?.getLayer(COUNTRY_HOVER_FILL_LAYER)) {
      try {
        map.setFilter(COUNTRY_HOVER_FILL_LAYER, EMPTY_COUNTRY_FILTER);
        map.setFilter(COUNTRY_HOVER_BORDER_LAYER, EMPTY_COUNTRY_FILTER);
      } catch {
        // The style may already be tearing down.
      }
    }
    this.updateMapCursor();
  }

  private clearAllHover() {
    const hadEventHover = this.hoveredDeckEventId != null;
    const hadClusterHover = this.hoveredDeckCluster != null;
    this.staticDeckHoverActive = false;
    this.aviationDeckHoverActive = false;
    this.deckHoverActive = false;
    this.manualAviationEvent = null;
    this.manualAviationTooltip?.clear();
    this.clearCountryHover();
    this.hoveredDeckEventId = null;
    this.hoveredDeckCluster = null;
    if (hadEventHover || hadClusterHover) this.requestRender({ interaction: true });
  }

  private updateMapCursor() {
    const canvas = this.map?.getCanvas();
    canvas?.classList.toggle(
      'wm-map-hover-target',
      this.deckHoverActive || Boolean(this.hoveredCountryIso2),
    );
  }

  private handleMapError = (event: { error?: Error; message?: string }) => {
    const message = event.error?.message || event.message || 'Unknown MapLibre error';
    if (!this.fallbackApplied && /fetch|ajax|cors|network|403|forbidden|tile|style/i.test(message)) {
      // MapLibre emits transient tile/glyph errors before the first `load`
      // event as well as after it. Counting two resource errors as a fatal
      // style failure made a healthy same-origin PMTiles basemap downgrade
      // immediately on a cold Cloudflare range request. Keep retryable errors
      // visible to diagnostics and let the bounded readiness timer make the
      // only initial fallback decision.
      this.callbacks?.onError(new Error(message));
      return;
    }
    if (this.fallbackApplied && /fetch|ajax|cors|network|404|tile|source/i.test(message)) {
      this.requestRendererFallback(new Error(`Local basemap failed: ${message}`));
      return;
    }
    this.callbacks?.onError(new Error(message));
  };

  private handleContextLost = (event: Event) => {
    event.preventDefault();
    this.paused = true;
    this.cancelAnimationLoop();
    this.cancelAnimationResume();
    this.cancelHazardPulseLoop();
    this.cancelPickingWarmup?.();
    this.cancelPickingWarmup = null;
    this.cancelStagedAviationCommit();
    this.pickingWarmupStage = 0;
    this.renderScheduler.cancel();
    this.heavyGeometryCommit.cancel();
    this.geometryNeedsCommit = true;
    this.overlay?.setProps({ layers: [] });
    this.aviationOverlay?.setProps({ layers: [] });
    this.emitBasemapState('renderer-fallback-ready');
    this.callbacks?.onError(new Error('WebGL context lost. Waiting for one bounded recovery attempt.'));
    this.clearContextRecoveryTimer();
    this.contextRecoveryTimer = window.setTimeout(() => {
      this.contextRecoveryTimer = null;
      this.requestRendererFallback(new Error('WebGL context did not recover within 1.5 seconds.'));
    }, 1_500);
  };

  private handleContextRestored = () => {
    this.clearContextRecoveryTimer();
    this.contextRecoveryAttempts += 1;
    if (this.contextRecoveryAttempts > 1) {
      this.requestRendererFallback(new Error('WebGL context was lost more than once.'));
      return;
    }
    this.paused = false;
    this.emitBasemapState(this.fallbackApplied ? 'local-fallback-ready' : 'primary-ready');
    this.requestRender({ points: true, aviation: true, geometry: true, dynamic: true });
    this.syncAnimationLoop();
    this.syncHazardPulseLoop();
  };

  private applyLocalFallback(error: Error) {
    if (!this.map || this.fallbackApplied || this.destroyed) return;
    this.fallbackApplied = true;
    this.clearFallbackTimer();
    this.emitBasemapState('initializing');
    this.callbacks?.onError(error);
    try {
      this.map.setStyle(getWeatherMapFallbackStyle(this.state?.basemapTheme ?? 'dark'), { diff: false });
      this.scheduleFallbackSourceTimeout();
    } catch (caught) {
      this.requestRendererFallback(caught instanceof Error ? caught : new Error(String(caught)));
    }
  }

  private async replaceBasemapStyle(state: WorldEventMapState) {
    const map = this.map;
    if (!map || this.destroyed) return;
    const generation = ++this.basemapStyleGeneration;
    this.fallbackApplied = false;
    this.clearFallbackTimer();
    this.clearFallbackSourceTimer();
    this.emitBasemapState('initializing');
    try {
      const style = await getWeatherMapStyle(state.basemapTheme, state.basemapProvider);
      if (this.destroyed || generation !== this.basemapStyleGeneration || map !== this.map) return;
      map.setStyle(style, { diff: false });
      this.fallbackTimer = window.setTimeout(() => {
        if (!this.map || this.fallbackApplied || this.destroyed || generation !== this.basemapStyleGeneration) return;
        this.applyLocalFallback(new Error('Selected basemap did not become ready within 10 seconds.'));
      }, 10_000);
    } catch (error) {
      if (this.destroyed || generation !== this.basemapStyleGeneration) return;
      this.applyLocalFallback(error instanceof Error ? error : new Error(String(error)));
    }
  }

  private handleDeckLayerError(error: Error, layer?: Layer) {
    const layerId = layer?.id;
    if (!layerId) {
      this.callbacks?.onError(error);
      return;
    }
    if (this.quarantinedLayerIds.has(layerId)) return;
    this.quarantinedLayerIds.add(layerId);
    this.callbacks?.onLayerDegraded?.(layerId, error);
    this.callbacks?.onError(new Error(`Map layer ${layerId} was isolated after a render error: ${error.message}`));
    if (layerId.startsWith('aviation-')) {
      this.aviationLayerSections = null;
      this.aviationDynamicLayers = null;
      this.requestRender({ aviation: true, dynamic: true });
      return;
    }
    this.pointLayers = null;
    this.invalidateGeometry();
    this.requestRender({ points: true, geometry: true, interaction: true });
  }

  private requestRendererFallback(error: Error) {
    if (this.destroyed) return;
    this.clearFallbackSourceTimer();
    this.clearContextRecoveryTimer();
    this.emitBasemapState('renderer-fallback-ready');
    this.callbacks?.onRendererFallbackRequested(error);
  }

  private emitBasemapState(state: BasemapState) {
    this.callbacks?.onBasemapStateChange(state);
  }

  private clearFallbackTimer() {
    if (this.fallbackTimer != null) {
      window.clearTimeout(this.fallbackTimer);
      this.fallbackTimer = null;
    }
  }

  private scheduleAnimationResume(delayMs = 500) {
    this.cancelAnimationResume();
    this.animationResumeTimer = window.setTimeout(() => {
      this.animationResumeTimer = null;
      if (!this.destroyed && !this.paused && !this.interacting) {
        this.resumeAviationDeckLoop();
        this.syncAnimationLoop();
      }
    }, delayMs);
  }

  private cancelAnimationResume() {
    if (this.animationResumeTimer != null) {
      window.clearTimeout(this.animationResumeTimer);
      this.animationResumeTimer = null;
    }
  }

  private scheduleFallbackSourceTimeout() {
    if (this.fallbackSourceTimer != null || this.destroyed) return;
    this.fallbackSourceTimer = window.setTimeout(() => {
      this.fallbackSourceTimer = null;
      if (this.markLocalFallbackReadyIfLoaded()) return;
      this.requestRendererFallback(new Error(
        'Local country geometry did not become renderable within 6 seconds.',
      ));
    }, 6_000);
  }

  private clearFallbackSourceTimer() {
    if (this.fallbackSourceTimer != null) {
      window.clearTimeout(this.fallbackSourceTimer);
      this.fallbackSourceTimer = null;
    }
  }

  private clearContextRecoveryTimer() {
    if (this.contextRecoveryTimer != null) {
      window.clearTimeout(this.contextRecoveryTimer);
      this.contextRecoveryTimer = null;
    }
  }

  private updateAdaptiveAnimationBudget(frameCostMs: number) {
    if (frameCostMs > 24) {
      this.animationIntervalMs = 80;
      this.animationRecoveryFrames = 0;
      return;
    }
    if (this.animationIntervalMs <= MAP_ANIMATION_FRAME_INTERVAL_MS) return;
    this.animationRecoveryFrames += 1;
    if (this.animationRecoveryFrames >= 600) {
      this.animationIntervalMs = MAP_ANIMATION_FRAME_INTERVAL_MS;
      this.animationRecoveryFrames = 0;
    }
  }

  private hasAnimatedAviation() {
    const airRoutesActive = this.state?.activeLayerIds.includes('air-routes') === true;
    return airRoutesActive && this.events.some((event) => (
      event.category === 'infrastructure'
      && (event.properties.mapEntity === 'air-route' || event.properties.mapEntity === 'air-flight')
    ));
  }

  private syncHazardPulseLoop() {
    const shouldPulse = !this.destroyed
      && !this.paused
      && !this.interacting
      && !this.reducedMotion
      && Boolean(this.overlay)
      && hasAnimatedHazardPulse(
        this.pulseEvents,
        this.state?.selectedEventId || null,
        this.eventFirstSeenAt,
        Date.now(),
        this.state?.zoom ?? 0,
      );
    if (!shouldPulse) {
      this.cancelHazardPulseLoop();
      return;
    }
    if (this.hazardPulseTimer != null) return;
    this.hazardPulseTimer = window.setInterval(() => {
      if (
        this.destroyed
        || this.paused
        || this.interacting
        || this.reducedMotion
        || !hasAnimatedHazardPulse(
          this.pulseEvents,
          this.state?.selectedEventId || null,
          this.eventFirstSeenAt,
          Date.now(),
          this.state?.zoom ?? 0,
        )
      ) {
        this.cancelHazardPulseLoop();
        return;
      }
      this.hazardPulseTime = Date.now();
      if (this.shouldRenderAnimationFrame()) this.requestRender({ pulse: true });
    }, HAZARD_PULSE_INTERVAL_MS);
  }

  private cancelHazardPulseLoop() {
    if (this.hazardPulseTimer != null) {
      window.clearInterval(this.hazardPulseTimer);
      this.hazardPulseTimer = null;
    }
  }

  private syncAnimationLoop() {
    if (this.destroyed || this.paused || this.reducedMotion || !this.hasAnimatedAviation()) {
      this.cancelAnimationLoop();
      return;
    }
    if (this.animationFrame != null) return;
    this.animationFrame = window.requestAnimationFrame(this.handleAnimationFrame);
  }

  private handleAnimationFrame = (timestamp: number) => {
    this.animationFrame = null;
    if (this.destroyed || this.paused || this.reducedMotion || !this.hasAnimatedAviation()) return;
    const actualFrameDelay = this.lastAnimationTimestamp == null
      ? 0
      : Math.max(0, timestamp - this.lastAnimationTimestamp);
    if (actualFrameDelay > 0) this.updateAdaptiveAnimationBudget(actualFrameDelay);
    const delta = boundedAnimationDelta(this.lastAnimationTimestamp, timestamp);
    this.lastAnimationTimestamp = timestamp;
    this.pendingAnimationDeltaMs += delta;
    if (this.pendingAnimationDeltaMs >= this.animationIntervalMs && this.shouldRenderAnimationFrame()) {
      this.animationTime = advanceAnimationTime(this.animationTime, this.pendingAnimationDeltaMs);
      this.pendingAnimationDeltaMs = 0;
      this.requestRender({ dynamic: true });
    } else {
      this.pendingAnimationDeltaMs = Math.min(this.pendingAnimationDeltaMs, 160);
    }
    this.animationFrame = window.requestAnimationFrame(this.handleAnimationFrame);
  };

  private cancelAnimationLoop() {
    if (this.animationFrame != null) {
      window.cancelAnimationFrame(this.animationFrame);
      this.animationFrame = null;
    }
    this.lastAnimationTimestamp = null;
    this.pendingAnimationDeltaMs = 0;
    this.animationRecoveryFrames = 0;
  }

  private shouldRenderAnimationFrame() {
    const scheduling = (globalThis as unknown as {
      navigator?: { scheduling?: { isInputPending?: () => boolean } };
    }).navigator?.scheduling;
    return !this.interacting && scheduling?.isInputPending?.() !== true;
  }
}
