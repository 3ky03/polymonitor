import { geoMercator, geoPath, type GeoProjection } from 'd3-geo';
import type {
  Feature,
  FeatureCollection,
  Geometry,
  MultiPolygon,
  Polygon,
  Position,
} from 'geojson';
import type { GeoEvent } from '../domain/types';
import {
  MAP_SEVERITY_STYLES,
  MAP_SYMBOL_SIZE,
  mapSymbolForEvent,
  mapSymbolPalette,
  mapSymbolPaths,
  type MapSymbolKey,
} from '../config/mapSymbols';
import {
  clampLatitude,
  clampLongitude,
  clampWorldEventZoom,
  type WorldEventMapState,
} from '../state/mapState';
import {
  continuousMetricRadiusMeters,
  eventColor,
  eventLabel,
  eventRepresentativePoint,
  eventSeverityColor,
  hazardAreaPresentation,
  isHazardEvent,
  pointRadiusMeters,
  SEVERITY_COLORS,
} from './layerFactories/shared';
import {
  HAZARD_PULSE_INTERVAL_MS,
  hazardPulseTargets,
  hasAnimatedHazardPulse,
  selectEventPulseCandidates,
} from './layerFactories/eventEmphasisLayers';
import { EventClusterIndex } from './layerFactories/eventPointLayer';
import { eventObservationTextureCandidates } from './layerFactories/eventObservationLayer';
import {
  aviationRouteMotionPoints,
  aviationRouteTone,
  aviationSeededFlightPoints,
  aviationAltitudeColor,
  aviationLiveAircraftMarkers,
  selectAviationRenderData,
} from './layerFactories/aviationLayers';
import {
  countryBasemapLabels,
  visibleCountryBasemapLabels,
  type CountryBasemapLabel,
} from './countryBasemapLabels';
import {
  advanceAnimationTime,
  boundedAnimationDelta,
  MAP_ANIMATION_FRAME_INTERVAL_MS,
} from './animationClock';
import type { MapCountryTarget, MapHoverPosition, MapRenderer, MapRendererCallbacks } from './MapRenderer';
import {
  worldEventTooltipModel,
  type WorldEventPickedObject,
} from './hoverTooltip';
import { RendererTooltip } from './rendererTooltip';

const SVG_NS = 'http://www.w3.org/2000/svg';
const LOCAL_BASEMAP_URL = '/map-data/world-countries.geojson';
const LOCAL_BASEMAP_TIMEOUT_MS = 4_000;

function svgElement<K extends keyof SVGElementTagNameMap>(name: K) {
  return document.createElementNS(SVG_NS, name);
}

function cssColor([red, green, blue, alpha]: [number, number, number, number]) {
  return `rgba(${red}, ${green}, ${blue}, ${alpha / 255})`;
}

function eventGeoJson(event: GeoEvent): Geometry | null {
  if (!event.geometry || event.geometry.type === 'Point') return null;
  return normalizePolygonWinding(event.geometry as Geometry);
}

function eventNamedGeometry(event: GeoEvent, name: string): Geometry | null {
  const geometries = event.properties.geometries;
  if (!geometries || typeof geometries !== 'object' || Array.isArray(geometries)) return null;
  const geometry = (geometries as Record<string, unknown>)[name];
  if (!geometry || typeof geometry !== 'object' || Array.isArray(geometry)) return null;
  const type = String((geometry as { type?: unknown }).type || '');
  return ['LineString', 'MultiLineString', 'Polygon', 'MultiPolygon'].includes(type)
    ? geometry as Geometry
    : null;
}

function aviationEntity(event: GeoEvent) {
  return event.category === 'infrastructure'
    ? String(event.properties.mapEntity || '')
    : '';
}

function aircraftMarker(x: number, y: number, angle: number) {
  const aircraft = mapSymbolMarker(x, y, 'aircraft', 22, angle);
  aircraft.removeAttribute('pointer-events');
  aircraft.removeAttribute('aria-hidden');
  aircraft.classList.add('wm-world-event-svg-aircraft');
  return aircraft;
}

function mapSymbolMarker(x: number, y: number, symbol: MapSymbolKey, size: number, angle = 0) {
  const marker = svgElement('g');
  const scale = size / MAP_SYMBOL_SIZE;
  marker.setAttribute(
    'transform',
    `translate(${x} ${y}) rotate(${angle}) translate(${-size / 2} ${-size / 2}) scale(${scale})`,
  );
  marker.setAttribute('aria-hidden', 'true');
  marker.setAttribute('pointer-events', 'none');
  marker.setAttribute('fill-rule', 'evenodd');
  const palette = mapSymbolPalette(symbol);
  marker.setAttribute('fill', palette.primary);
  marker.setAttribute('stroke', palette.secondary);
  marker.setAttribute('stroke-width', '0.65');
  marker.setAttribute('paint-order', 'stroke');
  for (const pathData of mapSymbolPaths(symbol)) {
    const path = svgElement('path');
    path.setAttribute('d', pathData);
    marker.append(path);
  }
  return marker;
}

function mapSymbolBackdrop(x: number, y: number, symbol: MapSymbolKey, size: number) {
  const palette = mapSymbolPalette(symbol);
  const backdrop = svgElement('circle');
  backdrop.setAttribute('cx', String(x));
  backdrop.setAttribute('cy', String(y));
  backdrop.setAttribute('r', String(size * 0.4375));
  backdrop.setAttribute('fill', palette.surface);
  backdrop.setAttribute('fill-opacity', '0.92');
  backdrop.setAttribute('stroke', palette.primary);
  backdrop.setAttribute('stroke-opacity', '0.44');
  backdrop.setAttribute('stroke-width', '1');
  backdrop.setAttribute('pointer-events', 'none');
  return backdrop;
}

function severityRing(x: number, y: number, radius: number, severity: GeoEvent['severity'], outer = false) {
  const style = MAP_SEVERITY_STYLES[severity];
  const ring = svgElement('circle');
  ring.setAttribute('cx', String(x));
  ring.setAttribute('cy', String(y));
  ring.setAttribute('r', String(radius));
  ring.setAttribute('fill', 'none');
  ring.setAttribute('stroke', style.color);
  ring.setAttribute('stroke-opacity', outer ? '0.57' : '0.94');
  ring.setAttribute('stroke-width', String(outer ? 1 : style.lineWidth));
  ring.setAttribute('pointer-events', 'none');
  ring.setAttribute('vector-effect', 'non-scaling-stroke');
  return ring;
}

function ringSignedArea(ring: Position[]) {
  let area = 0;
  for (let index = 0; index < ring.length - 1; index += 1) {
    const current = ring[index]!;
    const next = ring[index + 1]!;
    area += (current[0] || 0) * (next[1] || 0) - (next[0] || 0) * (current[1] || 0);
  }
  return area / 2;
}

function d3Ring(ring: Position[], outer: boolean) {
  const clockwise = ringSignedArea(ring) < 0;
  return clockwise === outer ? ring : [...ring].reverse();
}

export function normalizePolygonWinding(geometry: Geometry): Geometry {
  if (geometry.type === 'Polygon') {
    return {
      ...geometry,
      coordinates: geometry.coordinates.map((ring, index) => d3Ring(ring, index === 0)),
    } satisfies Polygon;
  }
  if (geometry.type === 'MultiPolygon') {
    return {
      ...geometry,
      coordinates: geometry.coordinates.map((polygon) => (
        polygon.map((ring, index) => d3Ring(ring, index === 0))
      )),
    } satisfies MultiPolygon;
  }
  return geometry;
}

function normalizedFeature(feature: Feature): Feature {
  return feature.geometry
    ? { ...feature, geometry: normalizePolygonWinding(feature.geometry) }
    : feature;
}

function featureCountryTarget(feature: Feature): MapCountryTarget | null {
  const properties = feature.properties || {};
  const iso2 = String(properties['ISO3166-1-Alpha-2'] || '').toUpperCase();
  const name = String(properties['name:en'] || properties.name || iso2);
  const positions: Position[] = [];
  const visit = (value: unknown) => {
    if (!Array.isArray(value)) return;
    if (value.length >= 2 && typeof value[0] === 'number' && typeof value[1] === 'number') {
      positions.push(value as Position);
      return;
    }
    value.forEach(visit);
  };
  if (feature.geometry?.type === 'GeometryCollection') {
    feature.geometry.geometries.forEach((geometry) => {
      if ('coordinates' in geometry) visit(geometry.coordinates);
    });
  } else if (feature.geometry && 'coordinates' in feature.geometry) {
    visit(feature.geometry.coordinates);
  }
  if (!iso2 || !positions.length) return null;
  const lons = positions.map((position) => Number(position[0])).filter(Number.isFinite);
  const lats = positions.map((position) => Number(position[1])).filter(Number.isFinite);
  return {
    iso2,
    name,
    bounds: [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
  };
}

export class SvgMapRenderer implements MapRenderer {
  private host: HTMLElement | null = null;
  private svg: SVGSVGElement | null = null;
  private countryLayer: SVGGElement | null = null;
  private areaLayer: SVGGElement | null = null;
  private countryLabelLayer: SVGGElement | null = null;
  private eventLayer: SVGGElement | null = null;
  private aviationMotionLayer: SVGGElement | null = null;
  private emphasisLayer: SVGGElement | null = null;
  private callbacks: MapRendererCallbacks | null = null;
  private tooltip: RendererTooltip | null = null;
  private state: WorldEventMapState | null = null;
  private events: GeoEvent[] = [];
  private countries: FeatureCollection | null = null;
  private countryLabels: CountryBasemapLabel[] = [];
  private basemapController: AbortController | null = null;
  private basemapTimer: number | null = null;
  private paused = false;
  private reducedMotion = false;
  private animationFrame: number | null = null;
  private renderFrame: number | null = null;
  private hoverFrame: number | null = null;
  private pendingHover: {
    tooltip: ReturnType<typeof worldEventTooltipModel>;
    position: MapHoverPosition | null;
  } | null = null;
  private lastAnimationTimestamp: number | null = null;
  private pendingAnimationDeltaMs = 0;
  private animationTime = 0;
  private hazardPulseTimer: number | null = null;
  private hazardPulseTime = Date.now();
  private readonly eventFirstSeenAt = new Map<string, number>();
  private receivedInitialEventSnapshot = false;
  private pulseEvents: GeoEvent[] = [];
  private hoveredEventId: string | null = null;
  private destroyed = false;
  private readonly clusterIndex = new EventClusterIndex();
  private drag:
    | { pointerId: number; x: number; y: number; center: WorldEventMapState['center'] }
    | null = null;

  async mount(container: HTMLElement, callbacks: MapRendererCallbacks) {
    if (this.svg) return;
    this.host = container;
    this.tooltip = new RendererTooltip(container);
    this.callbacks = callbacks;
    this.destroyed = false;
    callbacks.onBasemapStateChange('initializing');

    const svg = svgElement('svg');
    svg.classList.add('wm-world-event-svg-map');
    svg.classList.toggle('reduced-motion', this.reducedMotion);
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    const countries = svgElement('g');
    countries.classList.add('wm-world-event-svg-countries');
    const areas = svgElement('g');
    areas.classList.add('wm-world-event-svg-areas');
    const countryLabels = svgElement('g');
    countryLabels.classList.add('wm-world-event-svg-country-labels');
    const events = svgElement('g');
    events.classList.add('wm-world-event-svg-events');
    const aviationMotion = svgElement('g');
    aviationMotion.classList.add('wm-world-event-svg-aviation-motion');
    const emphasis = svgElement('g');
    emphasis.classList.add('wm-world-event-svg-emphasis');
    svg.append(countries, areas, countryLabels, events, aviationMotion, emphasis);
    container.append(svg);
    this.svg = svg;
    this.countryLayer = countries;
    this.areaLayer = areas;
    this.countryLabelLayer = countryLabels;
    this.eventLayer = events;
    this.aviationMotionLayer = aviationMotion;
    this.emphasisLayer = emphasis;

    container.addEventListener('wheel', this.handleWheel, { passive: false });
    container.addEventListener('keydown', this.handleKeyDown);
    container.addEventListener('pointerdown', this.handlePointerDown);
    container.addEventListener('pointermove', this.handlePointerMove);
    container.addEventListener('pointerup', this.handlePointerUp);
    container.addEventListener('pointercancel', this.handlePointerUp);

    this.scheduleRender();
    this.syncAnimationLoop();
    this.syncHazardPulseLoop();
    await this.loadLocalBasemap();
  }

  setState(state: WorldEventMapState) {
    const selectionChanged = this.state?.selectedEventId !== state.selectedEventId;
    this.state = state;
    if (selectionChanged) this.pulseEvents = selectEventPulseCandidates(this.events, state.selectedEventId);
    this.scheduleRender();
    this.syncAnimationLoop();
    this.syncHazardPulseLoop();
  }

  setEvents(events: GeoEvent[]) {
    if (events === this.events) return;
    const previousIds = new Set(this.events.map((event) => event.id));
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
    this.pulseEvents = selectEventPulseCandidates(events, this.state?.selectedEventId || null);
    this.clusterIndex.update(events);
    this.scheduleRender();
    this.syncAnimationLoop();
    this.syncHazardPulseLoop();
  }

  resize() {
    this.scheduleRender();
  }

  setReducedMotion(reduced: boolean) {
    this.reducedMotion = reduced;
    this.svg?.classList.toggle('reduced-motion', reduced);
    this.syncAnimationLoop();
    this.syncHazardPulseLoop();
    this.scheduleRender();
  }

  fitCountry(country: MapCountryTarget) {
    const [[west, south], [east, north]] = country.bounds;
    const span = Math.max(1, east - west, north - south);
    const center = { lon: (west + east) / 2, lat: (south + north) / 2 };
    const zoom = clampWorldEventZoom(Math.log2(360 / span) - 0.35);
    this.callbacks?.onCameraChange({ center, zoom });
  }

  pause() {
    this.paused = true;
    this.clearHover();
    this.cancelAnimationLoop();
    this.cancelHazardPulseLoop();
  }

  resume() {
    if (!this.paused) return;
    this.paused = false;
    this.scheduleRender();
    this.syncAnimationLoop();
    this.syncHazardPulseLoop();
  }

  destroy() {
    this.destroyed = true;
    this.clearHover();
    this.cancelAnimationLoop();
    this.cancelHazardPulseLoop();
    this.cancelScheduledRender();
    this.clearBasemapTimer();
    this.basemapController?.abort();
    this.basemapController = null;
    this.tooltip?.destroy();
    this.tooltip = null;
    const host = this.host;
    if (host) {
      host.removeEventListener('wheel', this.handleWheel);
      host.removeEventListener('keydown', this.handleKeyDown);
      host.removeEventListener('pointerdown', this.handlePointerDown);
      host.removeEventListener('pointermove', this.handlePointerMove);
      host.removeEventListener('pointerup', this.handlePointerUp);
      host.removeEventListener('pointercancel', this.handlePointerUp);
    }
    this.svg?.remove();
    this.host = null;
    this.svg = null;
    this.countryLayer = null;
    this.areaLayer = null;
    this.countryLabelLayer = null;
    this.eventLayer = null;
    this.aviationMotionLayer = null;
    this.emphasisLayer = null;
    this.countryLabels = [];
    this.callbacks = null;
    this.drag = null;
  }

  private async loadLocalBasemap() {
    this.basemapController = new AbortController();
    this.basemapTimer = window.setTimeout(
      () => this.basemapController?.abort(),
      LOCAL_BASEMAP_TIMEOUT_MS,
    );
    try {
      const response = await fetch(LOCAL_BASEMAP_URL, {
        headers: { Accept: 'application/geo+json, application/json' },
        signal: this.basemapController.signal,
      });
      if (!response.ok) throw new Error(`Local basemap returned HTTP ${response.status}.`);
      const payload = await response.json() as FeatureCollection;
      if (payload?.type !== 'FeatureCollection' || !Array.isArray(payload.features)) {
        throw new Error('Local basemap is not a GeoJSON FeatureCollection.');
      }
      if (this.destroyed) return;
      this.countries = {
        ...payload,
        features: payload.features.map(normalizedFeature),
      };
      this.countryLabels = countryBasemapLabels(this.countries);
      this.scheduleRender();
      if (typeof performance !== 'undefined'
        && performance.getEntriesByName('polymonitor:map:first-basemap').length === 0) {
        performance.mark('polymonitor:map:first-basemap');
      }
      this.callbacks?.onBasemapStateChange('renderer-fallback-ready');
    } catch (error) {
      if (this.destroyed) return;
      const message = this.basemapController.signal.aborted
        ? `Local SVG basemap timed out after ${LOCAL_BASEMAP_TIMEOUT_MS / 1000}s.`
        : error instanceof Error ? error.message : String(error);
      this.callbacks?.onError(new Error(message));
      // Real events remain selectable even if the decorative country geometry fails.
      this.callbacks?.onBasemapStateChange('renderer-fallback-ready');
    } finally {
      this.clearBasemapTimer();
      this.basemapController = null;
    }
  }

  private projection(width: number, height: number): GeoProjection {
    const state = this.state;
    const center = state?.center || { lon: 20, lat: 24 };
    const zoom = state?.zoom ?? 1.25;
    return geoMercator()
      .center([center.lon, center.lat])
      .scale((512 / (2 * Math.PI)) * Math.pow(2, zoom))
      .translate([width / 2, height / 2]);
  }

  private render() {
    if (this.paused || !this.svg || !this.countryLayer || !this.areaLayer
      || !this.countryLabelLayer || !this.eventLayer || !this.host) return;
    const width = Math.max(1, this.host.clientWidth || 1_200);
    const height = Math.max(1, this.host.clientHeight || 620);
    this.svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    const projection = this.projection(width, height);
    const path = geoPath(projection);

    this.countryLayer.replaceChildren();
    for (const feature of this.countries?.features || []) {
      const data = path(feature);
      if (!data) continue;
      const country = svgElement('path');
      country.setAttribute('d', data);
      const target = featureCountryTarget(feature);
      if (target) {
        country.setAttribute('tabindex', '0');
        country.setAttribute('role', 'button');
        country.setAttribute('aria-label', `${target.name} map area`);
        country.addEventListener('click', (event) => {
          event.stopPropagation();
          const rect = this.host?.getBoundingClientRect();
          const mouse = event as MouseEvent;
          this.callbacks?.onCountrySelect(target, rect
            ? { x: mouse.clientX - rect.left, y: mouse.clientY - rect.top }
            : undefined);
        });
        country.addEventListener('contextmenu', (event) => {
          event.preventDefault();
          event.stopPropagation();
          const rect = this.host?.getBoundingClientRect();
          this.callbacks?.onCountryContextMenu(target, rect
            ? { x: event.clientX - rect.left, y: event.clientY - rect.top }
            : { x: 0, y: 0 });
        });
      }
      this.countryLayer.append(country);
    }

    this.countryLabelLayer.replaceChildren();
    const occupiedCountryLabels: Array<{ left: number; top: number; right: number; bottom: number }> = [];
    const labelSize = (this.state?.zoom || 1.5) < 2.4 ? 11 : 12;
    for (const label of visibleCountryBasemapLabels(this.countryLabels, this.state?.zoom || 1.5)) {
      const position = projection(label.coordinates);
      if (!position) continue;
      const [x, y] = position;
      const halfWidth = Math.max(24, label.name.length * labelSize * 0.29);
      const box = { left: x - halfWidth, top: y - labelSize, right: x + halfWidth, bottom: y + labelSize };
      if (x < -halfWidth || x > width + halfWidth || y < -labelSize || y > height + labelSize) continue;
      if (occupiedCountryLabels.some((other) => (
        box.left < other.right && box.right > other.left && box.top < other.bottom && box.bottom > other.top
      ))) continue;
      const text = svgElement('text');
      text.classList.add('wm-world-event-svg-country-label');
      text.setAttribute('x', String(x));
      text.setAttribute('y', String(y));
      text.setAttribute('font-size', String(labelSize));
      text.textContent = label.name;
      this.countryLabelLayer.append(text);
      occupiedCountryLabels.push(box);
    }

    this.areaLayer.replaceChildren();
    this.eventLayer.replaceChildren();
    const state = this.state;
    const selectedId = this.state?.selectedEventId;
    const aviation = state
      ? selectAviationRenderData(this.events, state)
      : { routes: [], hubs: [], flights: [], liveAircraft: [], routeMotionGroups: [], flightMotionGroups: [] };
    const selectedRoute = selectedId
      ? aviation.routes.find((event) => event.id === selectedId)
      : null;
    const selectedRouteId = selectedRoute ? String(selectedRoute.properties.routeId || selectedRoute.id) : null;
    const visibleAviationIds = new Set([
      ...aviation.routes,
      ...aviation.hubs,
      ...aviation.liveAircraft,
    ].map((event) => event.id));
    const renderEvents = this.events.filter((event) => {
      const entity = aviationEntity(event);
      if (!entity) return true;
      if (entity === 'air-flight') return false;
      return visibleAviationIds.has(event.id);
    });
    const { singles, clusters } = this.clusterIndex.query(
      this.state?.zoom ?? 1.25,
      selectedId || null,
    );
    const occupiedEventLabels = [...occupiedCountryLabels];
    const eventLabelCandidates: Array<{ event: GeoEvent; x: number; y: number; size: number }> = [];
    for (const event of renderEvents) {
      if (!isHazardEvent(event) || event.hazardKind !== 'tropical-cyclone') continue;
      for (const [name, mode] of [
        ['forecastCone', 'cone'],
        ['observedTrack', 'observed'],
        ['forecastTrack', 'forecast'],
      ] as const) {
        const geometry = eventNamedGeometry(event, name);
        if (!geometry) continue;
        const data = path(geometry);
        if (!data) continue;
        const shape = svgElement('path');
        shape.setAttribute('d', data);
        shape.classList.add('wm-world-event-svg-cyclone-geometry', `is-${mode}`);
        this.decorateEventElement(shape, event, event.id === selectedId);
        if (mode === 'cone') {
          shape.setAttribute('fill', cssColor(eventColor(event, 28)));
          shape.setAttribute('stroke', cssColor(eventColor(event, 135)));
          shape.setAttribute('stroke-width', '1');
          this.areaLayer.append(shape);
        } else {
          shape.setAttribute('fill', 'none');
          shape.setAttribute('stroke', mode === 'observed' ? cssColor(eventColor(event, 205)) : '#cde1e89b');
          shape.setAttribute('stroke-width', mode === 'observed' ? '2.2' : '1.2');
          if (mode === 'forecast') shape.setAttribute('stroke-dasharray', '4 4');
          this.eventLayer.append(shape);
        }
      }
    }
    for (const event of renderEvents) {
      if (!event.geometry || event.geometry.type === 'Point') continue;
      const isArea = event.geometry.type === 'Polygon' || event.geometry.type === 'MultiPolygon';
      const areaPresentation = isArea && isHazardEvent(event)
        ? hazardAreaPresentation(event, this.state?.zoom ?? 1.25, selectedId || null)
        : null;
      if (areaPresentation?.mode === 'hidden') continue;
      const geometry = eventGeoJson(event);
      if (!geometry) continue;
      const data = path(geometry);
      if (!data) continue;
      const shape = svgElement('path');
      shape.setAttribute('d', data);
      shape.classList.add('wm-world-event-svg-shape');
      this.decorateEventElement(shape, event, event.id === selectedId);
      if (areaPresentation) {
        shape.classList.add('wm-world-event-svg-hazard-area', `is-${areaPresentation.mode}`);
        shape.setAttribute('fill', cssColor(eventSeverityColor(event, areaPresentation.fillAlpha)));
        shape.setAttribute(
          'stroke',
          areaPresentation.lineAlpha > 0
            ? cssColor(eventSeverityColor(event, areaPresentation.lineAlpha))
            : 'none',
        );
        shape.setAttribute('stroke-width', String(areaPresentation.lineWidth));
      }
      if (event.geometry.type === 'LineString') {
        shape.setAttribute('fill', 'none');
        if (aviationEntity(event) === 'air-route') {
          shape.classList.add('wm-world-event-svg-air-route');
          const sameSelectedRoute = selectedRouteId != null
            && String(event.properties.routeId || event.id) === selectedRouteId;
          shape.setAttribute('stroke', cssColor(aviationRouteTone(
            event,
            selectedRouteId ? sameSelectedRoute ? 235 : 36 : 112,
          )));
          shape.setAttribute('stroke-width', sameSelectedRoute ? '2.2' : '0.85');
        }
      }
      (isArea ? this.areaLayer : this.eventLayer).append(shape);
    }
    for (const observation of eventObservationTextureCandidates(
      renderEvents,
      this.state?.zoom ?? 1.25,
      selectedId || null,
    )) {
      const representativePoint = eventRepresentativePoint(observation);
      const position = representativePoint ? projection(representativePoint) : null;
      if (!position) continue;
      const [x, y] = position;
      if (x < -20 || x > width + 20 || y < -20 || y > height + 20) continue;
      const color = SEVERITY_COLORS[observation.severity];
      const texture = svgElement('circle');
      texture.classList.add('wm-world-event-svg-observation');
      texture.setAttribute('cx', String(x));
      texture.setAttribute('cy', String(y));
      texture.setAttribute('r', String((this.state?.zoom || 1.25) < 2.5 ? 2 : 2.8));
      texture.setAttribute('fill', cssColor([
        color[0],
        color[1],
        color[2],
        observation.severity === 'warning' ? 92 : observation.severity === 'watch' ? 68 : 46,
      ]));
      this.eventLayer.append(texture);
    }
    for (const cluster of clusters) {
      const position = projection(cluster.coordinates);
      if (!position) continue;
      const [x, y] = position;
      if (x < -40 || x > width + 40 || y < -40 || y > height + 40) continue;
      const group = svgElement('g');
      group.classList.add('wm-world-event-svg-cluster');
      group.setAttribute('role', 'button');
      group.setAttribute('tabindex', '0');
      group.setAttribute('aria-label', `${cluster.count} ${cluster.label || 'mapped events'}. Zoom in to expand.`);
      const title = svgElement('title');
      title.textContent = `${cluster.count} ${cluster.label || 'mapped events'} · ${cluster.severity.toUpperCase()} · click to expand`;
      const symbolSize = Math.min(24, 15 + Math.log2(cluster.count + 1) * 1.25);
      occupiedEventLabels.push({
        left: x - symbolSize / 2 - 3,
        top: y - symbolSize / 2 - 3,
        right: x + symbolSize / 2 + 18,
        bottom: y + symbolSize / 2 + 14,
      });
      const underlay = mapSymbolBackdrop(x, y, cluster.symbol, symbolSize);
      underlay.classList.add('wm-world-event-svg-symbol-underlay');
      const ring = severityRing(x, y, symbolSize / 2 + 1, cluster.severity);
      const outerRing = cluster.severity === 'critical'
        ? severityRing(x, y, symbolSize / 2 + 3.5, cluster.severity, true)
        : null;
      const symbol = mapSymbolMarker(
        x,
        y,
        cluster.symbol,
        symbolSize,
      );
      const badgeWidth = Math.max(12, String(cluster.count).length * 5 + 6);
      const badge = svgElement('rect');
      badge.classList.add('wm-world-event-svg-cluster-badge');
      badge.setAttribute('x', String(x + 4));
      badge.setAttribute('y', String(y + 3));
      badge.setAttribute('width', String(badgeWidth));
      badge.setAttribute('height', '12');
      badge.setAttribute('rx', '4');
      badge.setAttribute('fill', 'rgba(4, 10, 14, 0.95)');
      badge.setAttribute('stroke', cssColor(cluster.color));
      const label = svgElement('text');
      label.setAttribute('x', String(x + 4 + badgeWidth / 2));
      label.setAttribute('y', String(y + 9));
      label.textContent = String(cluster.count);
      const expand = () => this.callbacks?.onCameraChange({
        center: { lon: cluster.coordinates[0], lat: cluster.coordinates[1] },
        zoom: clampWorldEventZoom(cluster.expansionZoom),
      });
      const showClusterTooltip = (pointerEvent: PointerEvent) => {
        this.queueHoverTooltip(cluster, pointerEvent, 'world-event-clusters');
      };
      group.addEventListener('pointerenter', showClusterTooltip);
      group.addEventListener('pointermove', showClusterTooltip);
      group.addEventListener('pointerleave', this.clearHover);
      group.addEventListener('pointerdown', (pointerEvent) => pointerEvent.stopPropagation());
      group.addEventListener('click', expand);
      group.addEventListener('keydown', (keyboardEvent) => {
        if (keyboardEvent.key !== 'Enter' && keyboardEvent.key !== ' ') return;
        keyboardEvent.preventDefault();
        expand();
      });
      group.append(title, ...(outerRing ? [outerRing] : []), ring, underlay, symbol, badge, label);
      this.eventLayer.append(group);
    }
    for (const event of singles) {
      const representativePoint = eventRepresentativePoint(event);
      if (!representativePoint) continue;
      const position = projection(representativePoint);
      if (!position) continue;
      const [x, y] = position;
      if (x < -40 || x > width + 40 || y < -40 || y > height + 40) continue;
      const group = svgElement('g');
      group.classList.add('wm-world-event-svg-point');
      this.decorateEventElement(group, event, event.id === selectedId);
      const severityColor = SEVERITY_COLORS[event.severity];
      const metricRadius = continuousMetricRadiusMeters(event);
      if (metricRadius != null) {
        const intensity = svgElement('circle');
        const radius = Math.max(7, Math.min(22, Math.log2(Math.max(2, metricRadius / 1_000)) * 1.8));
        intensity.classList.add('wm-world-event-svg-intensity');
        intensity.setAttribute('cx', String(x));
        intensity.setAttribute('cy', String(y));
        intensity.setAttribute('r', String(radius));
        intensity.setAttribute('fill', cssColor([severityColor[0], severityColor[1], severityColor[2], 28]));
        intensity.setAttribute('stroke', cssColor([severityColor[0], severityColor[1], severityColor[2], 90]));
        group.append(intensity);
      }
      const symbolSize = event.id === selectedId
        ? 22
        : (this.state?.zoom || 1.25) < 2.5
          ? 14
          : (this.state?.zoom || 1.25) < 4 ? 16 : 18;
      const eventSymbol = mapSymbolForEvent(event);
      occupiedEventLabels.push({
        left: x - symbolSize / 2 - 2,
        top: y - symbolSize / 2 - 2,
        right: x + symbolSize / 2 + 2,
        bottom: y + symbolSize / 2 + 2,
      });
      const underlay = mapSymbolBackdrop(x, y, eventSymbol, symbolSize);
      underlay.classList.add('wm-world-event-svg-symbol-underlay');
      const ring = severityRing(x, y, symbolSize / 2 + 0.75, event.severity);
      const outerRing = event.severity === 'critical'
        ? severityRing(x, y, symbolSize / 2 + 3.2, event.severity, true)
        : null;
      const symbol = mapSymbolMarker(
        x,
        y,
        eventSymbol,
        symbolSize,
      );
      group.append(...(outerRing ? [outerRing] : []), ring, underlay, symbol);
      this.eventLayer.append(group);
      const mapZoom = this.state?.zoom || 1.25;
      if (mapZoom >= 3 && (
        event.id === selectedId
        || event.severity === 'critical'
        || (mapZoom >= 4 && event.severity === 'warning')
      )) eventLabelCandidates.push({ event, x, y, size: event.id === selectedId ? 11 : 9 });
    }
    const rank = { info: 0, watch: 1, warning: 2, critical: 3 } as const;
    eventLabelCandidates.sort((left, right) => (
      Number(right.event.id === selectedId) - Number(left.event.id === selectedId)
      || rank[right.event.severity] - rank[left.event.severity]
      || Date.parse(right.event.updatedAt || '') - Date.parse(left.event.updatedAt || '')
    ));
    for (const candidate of eventLabelCandidates.slice(0, (this.state?.zoom || 0) < 4 ? 24 : 100)) {
      const textValue = eventLabel(candidate.event);
      const widthEstimate = Math.min(220, Math.max(30, textValue.length * candidate.size * 0.62));
      const box = {
        left: candidate.x + 6,
        top: candidate.y - candidate.size - 13,
        right: candidate.x + 6 + widthEstimate,
        bottom: candidate.y - 2,
      };
      const collides = occupiedEventLabels.some((other) => (
        box.left < other.right && box.right > other.left && box.top < other.bottom && box.bottom > other.top
      ));
      if (candidate.event.id !== selectedId && collides) continue;
      const label = svgElement('text');
      label.classList.add('wm-world-event-svg-event-label');
      label.setAttribute('x', String(box.left));
      label.setAttribute('y', String(candidate.y - 8));
      label.setAttribute('font-size', String(candidate.size));
      label.textContent = textValue;
      this.eventLayer.append(label);
      occupiedEventLabels.push(box);
    }
    for (const event of aviation.hubs) {
      if (event.geometry?.type !== 'Point') continue;
      const position = projection(event.geometry.coordinates);
      if (!position) continue;
      const [x, y] = position;
      if (x < -40 || x > width + 40 || y < -40 || y > height + 40) continue;
      const hub = svgElement('circle');
      hub.setAttribute('cx', String(x));
      hub.setAttribute('cy', String(y));
      hub.setAttribute('r', event.id === selectedId ? '5.5' : '3.5');
      hub.classList.add('wm-world-event-svg-air-hub');
      this.decorateEventElement(hub, event, event.id === selectedId);
      this.eventLayer.append(hub);
    }
    for (const marker of aviationLiveAircraftMarkers(
      aviation.liveAircraft,
      this.state?.zoom || 1.25,
      selectedId || null,
    )) {
      const position = projection(marker.position);
      if (!position) continue;
      const [x, y] = position;
      if (x < -40 || x > width + 40 || y < -40 || y > height + 40) continue;
      const event = marker.event;
      const aircraft = aircraftMarker(x, y, Number(event.properties.heading || 0) - 90);
      this.decorateEventElement(aircraft, event, event.id === selectedId);
      const altitude = Number(event.properties.baroAltitude || 0);
      const [red, green, blue] = aviationAltitudeColor(altitude);
      const alpha = event.id === selectedId ? 245 : event.severity === 'info' ? 174 : 220;
      aircraft.setAttribute('fill', cssColor([red, green, blue, alpha]));
      this.eventLayer.append(aircraft);
      if (marker.count > 1) {
        const count = svgElement('text');
        count.setAttribute('x', String(x + 8));
        count.setAttribute('y', String(y - 8));
        count.setAttribute('font-size', '8');
        count.setAttribute('fill', '#e1f7fa');
        count.setAttribute('stroke', '#00080c');
        count.setAttribute('stroke-width', '2');
        count.setAttribute('paint-order', 'stroke fill');
        count.textContent = String(marker.count);
        this.eventLayer.append(count);
      }
    }
    this.renderAviationMotion(projection, width, height, aviation, selectedId || null);
    this.renderEmphasis(projection, width, height);
  }

  private renderAviationMotion(
    projection: GeoProjection,
    width: number,
    height: number,
    aviation = this.state ? selectAviationRenderData(this.events, this.state) : null,
    selectedId = this.state?.selectedEventId || null,
  ) {
    const layer = this.aviationMotionLayer;
    if (!layer) return;
    layer.replaceChildren();
    if (!aviation) return;
    for (const runner of aviationRouteMotionPoints(aviation.routes, this.animationTime, selectedId)) {
      const position = projection(runner.position);
      if (!position) continue;
      const [x, y] = position;
      if (x < -40 || x > width + 40 || y < -40 || y > height + 40) continue;
      const mote = svgElement('circle');
      mote.classList.add('wm-world-event-svg-route-runner');
      mote.setAttribute('cx', String(x));
      mote.setAttribute('cy', String(y));
      mote.setAttribute('r', '2.4');
      mote.setAttribute('fill', cssColor(runner.color));
      layer.append(mote);
    }
    for (const flight of aviationSeededFlightPoints(
      aviation.flights,
      this.animationTime,
      this.state?.zoom || 1.25,
      selectedId,
    )) {
      const position = projection(flight.position);
      if (!position) continue;
      const [x, y] = position;
      if (x < -40 || x > width + 40 || y < -40 || y > height + 40) continue;
      const aircraft = aircraftMarker(x, y, flight.angle);
      this.decorateEventElement(aircraft, flight.event, flight.event.id === selectedId);
      aircraft.setAttribute('fill', cssColor(flight.color));
      layer.append(aircraft);
    }
  }

  private renderEmphasis(
    projection: GeoProjection,
    width: number,
    height: number,
  ) {
    const layer = this.emphasisLayer;
    if (!layer) return;
    layer.replaceChildren();
    const appendRing = (
      position: [number, number],
      radius: number,
      color: [number, number, number, number],
      lineWidth: number,
    ) => {
      const projected = projection(position);
      if (!projected) return;
      const [x, y] = projected;
      if (x < -48 || x > width + 48 || y < -48 || y > height + 48) return;
      const circle = svgElement('circle');
      circle.setAttribute('cx', String(x));
      circle.setAttribute('cy', String(y));
      circle.setAttribute('r', String(radius));
      circle.setAttribute('fill', 'none');
      circle.setAttribute('stroke', cssColor(color));
      circle.setAttribute('stroke-width', String(lineWidth));
      circle.setAttribute('pointer-events', 'none');
      circle.setAttribute('vector-effect', 'non-scaling-stroke');
      layer.append(circle);
    };
    const pixelRadius = (event: GeoEvent) => event.geometry?.type === 'Point'
      ? Math.max(5, Math.min(18, Math.log2(Math.max(2, pointRadiusMeters(event) / 1_000)) * 1.8))
      : 9;

    if (!this.reducedMotion) {
      const targets = hazardPulseTargets(
        this.pulseEvents,
        this.state?.selectedEventId || null,
        this.eventFirstSeenAt,
        this.hazardPulseTime,
        this.state?.zoom ?? 0,
      );
      for (const target of targets.status) {
        const wave = 0.5 + 0.5 * Math.sin(
          this.hazardPulseTime / (target.strength === 'warning' ? 900 : 400),
        );
        const multiplier = target.strength === 'warning' ? 1.35 + wave * 0.25 : 1.45 + wave * 0.75;
        appendRing(
          target.position,
          pixelRadius(target.event) * multiplier,
          eventColor(target.event, target.strength === 'warning' ? 58 : 126),
          target.strength === 'warning' ? 1 : 1.5,
        );
      }
      for (const target of targets.recent) {
        const wave = 0.5 + 0.5 * Math.sin(this.hazardPulseTime / 318);
        appendRing(
          target.position,
          pixelRadius(target.event) * (1.6 + wave * 1.05),
          eventColor(target.event, Math.round(150 * target.fade)),
          1.5,
        );
      }
    }

    const hovered = this.hoveredEventId
      ? this.events.find((event) => event.id === this.hoveredEventId)
      : null;
    const hoveredPosition = hovered ? eventRepresentativePoint(hovered) : null;
    if (hovered && hoveredPosition && hovered.id !== this.state?.selectedEventId) {
      appendRing(hoveredPosition, pixelRadius(hovered) * 1.38, eventColor(hovered, 190), 1.2);
    }
    const selected = this.state?.selectedEventId
      ? this.events.find((event) => event.id === this.state?.selectedEventId)
      : null;
    const selectedPosition = selected ? eventRepresentativePoint(selected) : null;
    if (selected && selectedPosition && selected.geometry?.type !== 'LineString') {
      appendRing(selectedPosition, pixelRadius(selected) * 2.05, eventColor(selected, 235), 1.7);
      appendRing(selectedPosition, pixelRadius(selected) * 1.48, [220, 244, 248, 210], 1.25);
    }
  }

  private scheduleRender() {
    if (this.paused || this.destroyed || this.renderFrame != null) return;
    this.renderFrame = window.requestAnimationFrame(() => {
      this.renderFrame = null;
      this.render();
    });
  }

  private cancelScheduledRender() {
    if (this.renderFrame != null) window.cancelAnimationFrame(this.renderFrame);
    this.renderFrame = null;
  }

  private decorateEventElement(element: SVGElement, event: GeoEvent, selected: boolean) {
    const color = cssColor(eventColor(event, selected ? 250 : 205));
    element.setAttribute('fill', color);
    element.setAttribute('stroke', selected ? '#fffade' : color);
    element.setAttribute('stroke-width', selected ? '2.5' : '1.2');
    // Legacy CSS animated the clickable entity itself. Emphasis now lives in a
    // separate hollow-ring layer so hit targets never move under the pointer.
    element.setAttribute('style', 'animation:none');
    element.setAttribute('role', 'button');
    element.setAttribute('tabindex', '0');
    element.setAttribute('aria-label', `${event.title}. ${event.severity} severity.`);
    element.dataset.eventId = event.id;
    element.classList.add(`severity-${event.severity}`);
    if (selected) element.classList.add('is-selected');
    if (isHazardEvent(event)) element.classList.add(`hazard-${event.hazardKind}`);
    const title = svgElement('title');
    title.textContent = `${event.title} · ${event.locationLabel || event.severity}`;
    element.append(title);
    const showEventTooltip = (pointerEvent: PointerEvent) => {
      this.queueHoverTooltip(event, pointerEvent, '', event.id);
    };
    element.addEventListener('pointerenter', showEventTooltip);
    element.addEventListener('pointermove', showEventTooltip);
    element.addEventListener('pointerleave', this.clearHover);
    element.addEventListener('pointerdown', (pointerEvent) => pointerEvent.stopPropagation());
    element.addEventListener('click', (pointerEvent) => {
      pointerEvent.stopPropagation();
      this.callbacks?.onEventSelect(event.id);
    });
    element.addEventListener('keydown', (keyboardEvent) => {
      if (keyboardEvent.key !== 'Enter' && keyboardEvent.key !== ' ') return;
      keyboardEvent.preventDefault();
      this.callbacks?.onEventSelect(event.id);
    });
  }

  private handleWheel = (event: WheelEvent) => {
    if (!this.state || !this.callbacks) return;
    event.preventDefault();
    const delta = event.deltaY > 0 ? -0.35 : 0.35;
    this.callbacks.onCameraChange({
      center: this.state.center,
      zoom: clampWorldEventZoom(this.state.zoom + delta),
    });
  };

  private handleKeyDown = (event: KeyboardEvent) => {
    if (!this.state || !this.callbacks) return;
    const degreesPerStep = 18 / Math.pow(2, Math.max(0, this.state.zoom - 1));
    let { lon, lat } = this.state.center;
    let zoom = this.state.zoom;
    if (event.key === '+' || event.key === '=') zoom = clampWorldEventZoom(zoom + 0.5);
    else if (event.key === '-') zoom = clampWorldEventZoom(zoom - 0.5);
    else if (event.key === 'ArrowLeft') lon -= degreesPerStep;
    else if (event.key === 'ArrowRight') lon += degreesPerStep;
    else if (event.key === 'ArrowUp') lat += degreesPerStep / 2;
    else if (event.key === 'ArrowDown') lat -= degreesPerStep / 2;
    else return;
    event.preventDefault();
    this.callbacks.onCameraChange({
      center: { lon: clampLongitude(lon), lat: clampLatitude(lat) },
      zoom,
    });
  };

  private handlePointerDown = (event: PointerEvent) => {
    if (!this.state || event.button !== 0) return;
    this.clearHover();
    this.cancelAnimationLoop();
    this.cancelHazardPulseLoop();
    this.drag = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      center: { ...this.state.center },
    };
    this.host?.setPointerCapture(event.pointerId);
  };

  private handlePointerMove = (event: PointerEvent) => {
    if (!this.drag || event.pointerId !== this.drag.pointerId || !this.state || !this.callbacks) return;
    const degreesPerPixel = 360 / (512 * Math.pow(2, this.state.zoom));
    this.callbacks.onCameraChange({
      center: {
        lon: clampLongitude(this.drag.center.lon - (event.clientX - this.drag.x) * degreesPerPixel),
        lat: clampLatitude(this.drag.center.lat + (event.clientY - this.drag.y) * degreesPerPixel),
      },
      zoom: this.state.zoom,
    });
  };

  private handlePointerUp = (event: PointerEvent) => {
    if (!this.drag || event.pointerId !== this.drag.pointerId) return;
    this.host?.releasePointerCapture(event.pointerId);
    this.drag = null;
    this.syncAnimationLoop();
    this.syncHazardPulseLoop();
  };

  private clearBasemapTimer() {
    if (this.basemapTimer == null) return;
    window.clearTimeout(this.basemapTimer);
    this.basemapTimer = null;
  }

  private pointerPosition(pointerEvent: PointerEvent) {
    const bounds = this.svg?.getBoundingClientRect();
    return bounds ? {
      x: Math.max(12, Math.min(bounds.width - 12, pointerEvent.clientX - bounds.left + 14)),
      y: Math.max(12, Math.min(bounds.height - 12, pointerEvent.clientY - bounds.top + 14)),
    } : null;
  }

  private queueHoverTooltip(
    object: WorldEventPickedObject,
    pointerEvent: PointerEvent,
    layerId = '',
    eventId: string | null = null,
  ) {
    if (eventId !== this.hoveredEventId) {
      this.hoveredEventId = eventId;
      if (this.host) {
        const width = Math.max(1, this.host.clientWidth || 1_200);
        const height = Math.max(1, this.host.clientHeight || 620);
        this.renderEmphasis(this.projection(width, height), width, height);
      }
    }
    this.pendingHover = {
      tooltip: worldEventTooltipModel(object, layerId),
      position: this.pointerPosition(pointerEvent),
    };
    if (this.hoverFrame != null) return;
    this.hoverFrame = window.requestAnimationFrame(() => {
      this.hoverFrame = null;
      const pending = this.pendingHover;
      this.pendingHover = null;
      if (!pending) return;
      this.tooltip?.show(pending.tooltip, pending.position);
    });
  }

  private clearHover = () => {
    if (this.hoverFrame != null) window.cancelAnimationFrame(this.hoverFrame);
    this.hoverFrame = null;
    this.pendingHover = null;
    this.tooltip?.clear();
    if (this.hoveredEventId != null) {
      this.hoveredEventId = null;
      if (this.host) {
        const width = Math.max(1, this.host.clientWidth || 1_200);
        const height = Math.max(1, this.host.clientHeight || 620);
        this.renderEmphasis(this.projection(width, height), width, height);
      }
    }
  };

  private hasAnimatedAviation() {
    return this.state?.activeLayerIds.includes('air-routes') === true
      && this.events.some((event) => (
        aviationEntity(event) === 'air-route' || aviationEntity(event) === 'air-flight'
      ));
  }

  private syncHazardPulseLoop() {
    const shouldPulse = Boolean(this.svg)
      && !this.destroyed
      && !this.paused
      && !this.drag
      && !this.reducedMotion
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
      if (this.destroyed || this.paused || this.drag || this.reducedMotion) {
        this.cancelHazardPulseLoop();
        return;
      }
      this.hazardPulseTime = Date.now();
      if (!this.host) return;
      const width = Math.max(1, this.host.clientWidth || 1_200);
      const height = Math.max(1, this.host.clientHeight || 620);
      this.renderEmphasis(this.projection(width, height), width, height);
    }, HAZARD_PULSE_INTERVAL_MS);
  }

  private cancelHazardPulseLoop() {
    if (this.hazardPulseTimer != null) {
      window.clearInterval(this.hazardPulseTimer);
      this.hazardPulseTimer = null;
    }
  }

  private syncAnimationLoop() {
    if (!this.svg || this.destroyed || this.paused || this.drag || this.reducedMotion || !this.hasAnimatedAviation()) {
      this.cancelAnimationLoop();
      return;
    }
    if (this.animationFrame != null) return;
    this.animationFrame = window.requestAnimationFrame(this.handleAnimationFrame);
  }

  private handleAnimationFrame = (timestamp: number) => {
    this.animationFrame = null;
    if (this.destroyed || this.paused || this.drag || this.reducedMotion || !this.hasAnimatedAviation()) return;
    this.pendingAnimationDeltaMs += boundedAnimationDelta(this.lastAnimationTimestamp, timestamp);
    this.lastAnimationTimestamp = timestamp;
    if (this.pendingAnimationDeltaMs >= MAP_ANIMATION_FRAME_INTERVAL_MS) {
      this.animationTime = advanceAnimationTime(this.animationTime, this.pendingAnimationDeltaMs);
      this.pendingAnimationDeltaMs = 0;
      if (this.host) {
        const width = Math.max(1, this.host.clientWidth || 1_200);
        const height = Math.max(1, this.host.clientHeight || 620);
        this.renderAviationMotion(this.projection(width, height), width, height);
      }
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
  }
}
