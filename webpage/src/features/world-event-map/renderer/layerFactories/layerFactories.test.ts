import { describe, expect, it } from 'vitest';
import type { Layer } from '@deck.gl/core';
import type { GeoEvent } from '../../domain/types';
import { defaultWorldEventMapState } from '../../state/mapState';
import { createWorldEventGeometryLayers, createWorldEventLayers } from '.';
import {
  aviationRouteMotionPoints,
  aviationSeededFlightPoints,
  aviationAltitudeColor,
  aviationLayerStatsForState,
  createAviationDynamicLayers,
  createAviationStaticLayerSections,
  selectAviationRenderData,
} from './aviationLayers';
import {
  clusterEventPoints,
  eventVisibleAtZoom,
  EventClusterIndex,
} from './eventPointLayer';
import {
  createEventInteractionLayers,
  createEventPulseLayers,
  eventRepresentativePoint,
  hazardPulseTargets,
} from './eventEmphasisLayers';

const pointEvent = (id: string, lon: number, lat: number, severity: GeoEvent['severity'] = 'watch'): GeoEvent => ({
  id,
  category: 'conflict',
  title: id,
  severity,
  geometry: { type: 'Point', coordinates: [lon, lat] },
  locationPrecision: 'exact',
  sources: [{ provider: 'fixture', nativeId: id }],
  limitations: [],
  relatedMarketIds: [],
  properties: {},
});

const hazardPoint = (
  id: string,
  hazardKind: 'earthquake' | 'volcano',
  lon: number,
  lat: number,
  severity: GeoEvent['severity'] = 'watch',
): GeoEvent => ({
  ...pointEvent(id, lon, lat, severity),
  category: 'natural-hazard',
  properties: { mapEntity: 'hazard-event' },
  hazardKind,
  lifecycle: 'active',
  coverage: { scope: 'global', label: 'fixture', isComplete: false, gaps: [] },
  severityEvidence: { provider: 'fixture', mappingVersion: 'fixture', reason: 'fixture' },
  revision: { nativeEventId: id },
  metrics: hazardKind === 'earthquake'
    ? { kind: 'earthquake', magnitude: 4.5 }
    : { kind: 'volcano-or-other', statusLabel: 'active' },
} as GeoEvent);

const hazardArea = (
  id: string,
  west: number,
  south: number,
  severity: GeoEvent['severity'] = 'warning',
): GeoEvent => ({
  ...hazardPoint(id, 'volcano', west, south, severity),
  geometry: {
    type: 'Polygon',
    coordinates: [[
      [west, south],
      [west + 2, south],
      [west + 2, south + 2],
      [west, south + 2],
      [west, south],
    ]],
  },
});

const aviationState = () => {
  const state = defaultWorldEventMapState();
  return {
    ...state,
    activeLayerIds: [...state.activeLayerIds, 'air-routes'],
    aviationLens: 'all' as const,
  };
};

describe('world event layer factories', () => {
  it('uses stable layer ids and clusters only genuinely dense global points', () => {
    const state = defaultWorldEventMapState();
    const layers = createWorldEventLayers(Array.from({ length: 6 }, (_, index) => (
      pointEvent(`dense:${index}`, 10 + index * 0.03, 10 + index * 0.02, index === 5 ? 'critical' : 'warning')
    )), state);
    expect((layers as Layer[]).map((layer) => layer.id)).toEqual([
      'world-event-cluster-severity-rings',
      'world-event-cluster-critical-rings',
      'world-event-clusters',
      'world-event-cluster-counts',
    ]);
    const clusterCounts = (layers as Layer[]).find((layer) => layer.id === 'world-event-cluster-counts');
    const clusterCountProps = clusterCounts?.props as unknown as {
      characterSet?: string;
      getIcon?: unknown;
    };
    expect(clusterCountProps.getIcon).toBeTypeOf('function');
    expect(clusterCountProps.characterSet).toBeUndefined();
  });

  it('keeps the selected event outside a cluster', () => {
    const state = { ...defaultWorldEventMapState(), selectedEventId: 'a' };
    const layers = createWorldEventLayers([
      pointEvent('a', 10, 10),
      pointEvent('b', 10.2, 10.1),
      pointEvent('c', 10.3, 10.2),
    ], state);
    expect((layers as Layer[]).some((layer) => layer.id === 'world-event-points')).toBe(true);
  });

  it('clusters dense earthquakes at world zoom and preserves hazard semantics', () => {
    const events = [
      ...Array.from({ length: 5 }, (_, index) => (
        hazardPoint(`eq:${index}`, 'earthquake', 10 + index * 0.04, 10 + index * 0.04, 'critical')
      )),
      hazardPoint('vo:a', 'volcano', 11, 11),
    ];
    const clustered = clusterEventPoints(events, 1.25, null);
    expect(clustered.clusters).toHaveLength(1);
    expect(clustered.clusters[0]?.count).toBe(5);
    expect(clustered.clusters[0]?.label).toBe('earthquake');
    expect(clustered.clusters[0]?.symbol).toBe('earthquake');
    expect(clustered.clusters[0]?.badge).toBe('EQ');
    expect(clustered.singles).toHaveLength(0);
  });

  it('progressively discloses lower-priority events without hiding the selected event', () => {
    const info = hazardPoint('info', 'volcano', 10, 10, 'info');
    const moderateQuake = hazardPoint('moderate-quake', 'earthquake', 11, 11, 'warning');
    expect(eventVisibleAtZoom(info, 1.25, null)).toBe(false);
    expect(eventVisibleAtZoom(info, 4, null)).toBe(true);
    expect(eventVisibleAtZoom(moderateQuake, 1.25, null)).toBe(false);
    expect(eventVisibleAtZoom(moderateQuake, 3, null)).toBe(true);
    expect(eventVisibleAtZoom(info, 1.25, info.id)).toBe(true);
  });

  it('keeps lower-priority events represented by semantic clusters at world zoom', () => {
    const events = [
      ...Array.from({ length: 5 }, (_, index) => (
        hazardPoint(`eq:info-${index}`, 'earthquake', 10 + index * 0.03, 10 + index * 0.03, 'info')
      )),
      hazardPoint('eq:watch', 'earthquake', 10.15, 10.15, 'watch'),
    ];
    const clustered = clusterEventPoints(events, 1.25, null);

    expect(clustered.singles).toHaveLength(0);
    expect(clustered.clusters).toHaveLength(1);
    expect(clustered.clusters[0]).toMatchObject({
      count: 6,
      severity: 'watch',
      symbol: 'earthquake',
    });
    expect(clustered.clusters[0]?.eventIds).toHaveLength(6);
  });

  it('invalidates the query cache when zoom crosses a disclosure boundary', () => {
    const events = [
      hazardPoint('volcano:watch-a', 'volcano', 10, 10, 'watch'),
      hazardPoint('volcano:watch-b', 'volcano', 30, 20, 'watch'),
    ];
    const index = new EventClusterIndex();
    index.update(events);

    expect(index.query(2.49, null).singles).toHaveLength(0);
    expect(index.query(2.5, null).singles.map((event) => event.id)).toEqual([
      'volcano:watch-a',
      'volcano:watch-b',
    ]);
  });

  it('represents official hazard areas as semantic markers and clusters at world zoom', () => {
    const events = Array.from({ length: 5 }, (_, index) => (
      hazardArea(`area:${index}`, 10 + index * 0.08, 10 + index * 0.04, index === 4 ? 'critical' : 'warning')
    ));
    const clustered = clusterEventPoints(events, 1.25, null);
    expect(clustered.clusters).toHaveLength(1);
    expect(clustered.clusters[0]?.count).toBe(5);
    expect(clustered.clusters[0]?.symbol).toBe('volcano');
    expect(clustered.clusters[0]?.bounds).toEqual([10, 10, 12.32, 12.16]);
    expect(clustered.singles).toHaveLength(0);
  });

  it('hides hazard footprints globally, reveals restrained regional fill, and preserves selection', () => {
    const area = hazardArea('area:progressive', 10, 10, 'warning');
    const globalLayers = createWorldEventLayers(
      [area],
      { ...defaultWorldEventMapState(), zoom: 1.25 },
    ) as Layer[];
    expect(globalLayers.some((layer) => layer.id === 'world-event-hazard-areas')).toBe(false);
    expect(globalLayers.some((layer) => layer.id === 'world-event-points')).toBe(true);

    const regional = createWorldEventGeometryLayers([area], null, 3.2, 'country-labels') as Layer[];
    const regionalLayer = regional.find((layer) => layer.id === 'world-event-hazard-areas');
    const regionalProps = regionalLayer?.props as unknown as Record<string, any>;
    const regionalFeature = regionalProps.data.features[0];
    expect(regionalProps.getFillColor(regionalFeature)[3]).toBe(16);
    expect(regionalProps.getLineColor(regionalFeature)[3]).toBe(0);
    expect(regionalProps.beforeId).toBe('country-labels');

    const selected = createWorldEventGeometryLayers([area], area.id, 1.25) as Layer[];
    const selectedLayer = selected.find((layer) => layer.id === 'world-event-hazard-areas');
    const selectedProps = selectedLayer?.props as unknown as Record<string, any>;
    const selectedFeature = selectedProps.data.features[0];
    expect(selectedProps.getFillColor(selectedFeature)[3]).toBe(46);
    expect(selectedProps.getLineColor(selectedFeature)[3]).toBe(245);
  });

  it('does not tessellate offscreen hazard polygons but always keeps the selection', () => {
    const nearby = hazardArea('area:nearby', 10, 10, 'warning');
    const remote = hazardArea('area:remote', -120, 35, 'warning');
    const viewport: [number, number, number, number] = [0, 0, 40, 40];

    const visible = createWorldEventGeometryLayers(
      [nearby, remote], null, 3.2, undefined, viewport,
    ) as Layer[];
    const visibleFeatures = (visible.find((layer) => layer.id === 'world-event-hazard-areas')
      ?.props as unknown as Record<string, any>).data.features;
    expect(visibleFeatures.map((feature: any) => feature.id)).toEqual([nearby.id]);

    const withSelection = createWorldEventGeometryLayers(
      [nearby, remote], remote.id, 3.2, undefined, viewport,
    ) as Layer[];
    const selectedFeatures = (withSelection.find((layer) => layer.id === 'world-event-hazard-areas')
      ?.props as unknown as Record<string, any>).data.features;
    expect(selectedFeatures.map((feature: any) => feature.id)).toEqual([nearby.id, remote.id]);
  });

  it('uses metric-sized circles only when a continuous metric exists', () => {
    const quake = hazardPoint('quake', 'earthquake', 10, 10, 'warning');
    const volcano = hazardPoint('volcano', 'volcano', 11, 11, 'warning');
    const state = { ...defaultWorldEventMapState(), zoom: 3 };
    const quakeLayers = createWorldEventLayers([quake], state) as Layer[];
    const volcanoLayers = createWorldEventLayers([volcano], state) as Layer[];
    expect(quakeLayers.some((layer) => layer.id === 'world-event-point-intensity')).toBe(true);
    expect(volcanoLayers.some((layer) => layer.id === 'world-event-point-intensity')).toBe(false);
    expect(volcanoLayers.some((layer) => layer.id === 'world-event-points')).toBe(true);
  });

  it('uses a colored semantic icon with a separate non-pickable severity ring', () => {
    const warning = hazardPoint('volcano:warning', 'volcano', 10, 10, 'warning');
    const state = { ...defaultWorldEventMapState(), zoom: 1.25 };
    const layers = createWorldEventLayers([warning], state) as Layer[];
    const point = layers.find((layer) => layer.id === 'world-event-points');
    const pointProps = point?.props as unknown as Record<string, unknown>;
    const color = (pointProps.getColor as (event: GeoEvent) => number[])(warning);

    expect(point?.constructor.name).toBe('IconLayer');
    expect(layers.some((layer) => layer.id === 'world-event-point-underlays')).toBe(false);
    const ring = layers.find((layer) => layer.id === 'world-event-point-severity-rings');
    expect(pointProps.getIcon).toBeDefined();
    expect(color.slice(0, 3)).toEqual([255, 255, 255]);
    expect(color[3]).toBeLessThan(220);
    expect(pointProps.sizeMaxPixels).toBe(22);
    expect((ring?.props as unknown as Record<string, unknown>).pickable).toBe(false);
  });

  it('retains low-priority spatial texture without making observations pickable', () => {
    const events = [
      pointEvent('context:a', -40, 10, 'info'),
      pointEvent('context:b', 60, -15, 'watch'),
    ];
    const layers = createWorldEventLayers(events, defaultWorldEventMapState()) as Layer[];
    const texture = layers.find((layer) => layer.id === 'world-event-observation-texture');
    const props = texture?.props as unknown as Record<string, unknown>;

    expect(texture?.constructor.name).toBe('ScatterplotLayer');
    expect(props.pickable).toBe(false);
    expect(props.stroked).toBe(false);
    expect((props.data as GeoEvent[]).map((event) => event.id)).toEqual(['context:b', 'context:a']);
  });

  it('does not combine different conflict types into one generic cluster', () => {
    const events = [
      ...Array.from({ length: 5 }, (_, index) => ({
        ...pointEvent(`state:${index}`, 10 + index * 0.03, 10 + index * 0.03, 'warning'),
        properties: { violenceType: '1' },
      })),
      ...Array.from({ length: 5 }, (_, index) => ({
        ...pointEvent(`nonstate:${index}`, 10 + index * 0.03, 10 + index * 0.03, 'warning'),
        properties: { violenceType: '2' },
      })),
    ];
    const clustered = clusterEventPoints(events, 1.25, null);
    expect(clustered.clusters.map((cluster) => cluster.label).sort()).toEqual([
      'non-state conflict',
      'state-based conflict',
    ]);
  });

  it('reuses persistent Supercluster indexes across viewport and selection changes', () => {
    const events = Array.from({ length: 240 }, (_, index) => pointEvent(
      `persistent:${index}`,
      10 + (index % 20) * 0.01,
      10 + Math.floor(index / 20) * 0.01,
      index === 239 ? 'critical' : 'warning',
    ));
    const index = new EventClusterIndex();
    index.update(events);
    const first = index.query(1.25, null, [9, 9, 12, 12]);
    const selected = index.query(2.1, 'persistent:239', [9.5, 9.5, 11.5, 11.5]);
    index.update(events);

    expect(index.buildCount).toBe(1);
    expect(first.clusters[0]?.eventIds.length).toBeLessThanOrEqual(200);
    expect(first.clusters[0]?.count).toBe(240);
    expect(selected.singles.some((event) => event.id === 'persistent:239')).toBe(true);
    expect(index.buildCount).toBe(1);

    index.update([...events]);
    expect(index.buildCount).toBe(2);
  });

  it('keeps stable clickable hazard entities separate from hollow non-pickable pulse rings', () => {
    const warning = hazardPoint('warning', 'earthquake', 10, 10, 'warning');
    const stableLayers = createWorldEventLayers([
      warning,
    ], { ...defaultWorldEventMapState(), zoom: 3 }) as Layer[];
    const pulseLayers = createEventPulseLayers({
      events: [warning],
      selectedEventId: null,
      firstSeenAt: new Map(),
      pulseTime: 1_000,
    }) as Layer[];
    const point = stableLayers.find((layer) => layer.id === 'world-event-points');
    const pulse = pulseLayers.find((layer) => layer.id === 'world-event-status-pulses');

    const pointProps = point?.props as unknown as Record<string, unknown>;
    const pulseProps = pulse?.props as unknown as Record<string, unknown>;
    expect(pointProps.pickable).toBe(true);
    expect(pointProps.autoHighlight).toBe(false);
    expect(pulseProps.pickable).toBe(false);
    expect(pulseProps.filled).toBe(false);
    expect(pulseProps.stroked).toBe(true);
  });

  it('creates polygon and line layers without accessing the DOM', () => {
    const area: GeoEvent = {
      ...pointEvent('area', 0, 0),
      category: 'natural-hazard',
      geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
    };
    const line: GeoEvent = {
      ...pointEvent('line', 0, 0),
      category: 'infrastructure',
      geometry: { type: 'LineString', coordinates: [[0, 0], [1, 1]] },
    };
    expect((createWorldEventLayers([area, line], defaultWorldEventMapState()) as Layer[]).map((layer) => layer.id))
      .toEqual(['world-event-context-areas', 'world-event-paths']);
  });

  it('renders aviation reference arcs, route runners, aircraft and hubs as a dedicated lens', () => {
    const state = aviationState();
    const route: GeoEvent = {
      ...pointEvent('route:0', 0, 0),
      category: 'infrastructure',
      geometry: { type: 'LineString', coordinates: [[0, 0], [4, 3], [8, 4]] },
      properties: {
        mapEntity: 'air-route',
        routeId: 'route',
        layer: 'trunk',
        trafficScore: 90,
      },
    };
    const hub: GeoEvent = {
      ...pointEvent('hub', 0, 0),
      category: 'infrastructure',
      properties: { mapEntity: 'air-hub', code: 'HUB', routeCount: 100 },
    };
    expect((createWorldEventLayers([route, hub], state, true, undefined, 5) as Layer[]).map((layer) => layer.id))
      .toEqual([
        'aviation-route-core',
        'aviation-route-runners',
        'aviation-hubs',
        'aviation-hub-labels',
      ]);
    const hubLabel = (createWorldEventLayers([route, hub], state, true, undefined, 5) as Layer[])
      .find((layer) => layer.id === 'aviation-hub-labels');
    const hubLabelProps = hubLabel?.props as unknown as {
      characterSet?: string;
      fontSettings?: { sdf?: boolean };
      outlineWidth?: number;
    } | undefined;
    expect(hubLabelProps?.characterSet).toBe('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-');
    expect(hubLabelProps?.fontSettings?.sdf).not.toBe(true);
    expect(hubLabelProps?.outlineWidth).toBe(0);
    const atStart = aviationRouteMotionPoints([route], 0)[0]?.position;
    const afterMotion = aviationRouteMotionPoints([route], 8)[0]?.position;
    expect(afterMotion).not.toEqual(atStart);
  });

  it('keeps geometry below aviation and points in the final compositing order', () => {
    const state = defaultWorldEventMapState();
    const area: GeoEvent = {
      ...pointEvent('risk-area', 0, 0),
      category: 'country-risk',
      geometry: { type: 'Polygon', coordinates: [[[0, 0], [2, 0], [2, 2], [0, 0]]] },
      properties: { mapEntity: 'country-risk-area', evidenceCount: 18 },
    };
    const route: GeoEvent = {
      ...pointEvent('route:above-area', 0, 0),
      category: 'infrastructure',
      geometry: { type: 'LineString', coordinates: [[0, 0], [3, 3]] },
      properties: { mapEntity: 'air-route', routeId: 'above-area', layer: 'trunk' },
    };
    const enabled = { ...aviationState(), activeLayerIds: [...state.activeLayerIds, 'air-routes'] };
    const ids = (createWorldEventLayers([area, route], enabled) as Layer[]).map((layer) => layer.id);
    expect(ids.indexOf('world-event-country-risk')).toBeLessThan(ids.indexOf('aviation-route-core'));
  });

  it('uses an icon layer instead of a font glyph for animated aircraft', () => {
    const state = aviationState();
    const flight: GeoEvent = {
      ...pointEvent('flight:0', 0, 0),
      category: 'infrastructure',
      geometry: { type: 'LineString', coordinates: [[0, 0], [4, 3]] },
      properties: { mapEntity: 'air-flight', flightId: 'flight', phase: 0.1, speed: 0.06 },
    };
    const layers = createWorldEventLayers([flight], state, true, undefined, 5) as Layer[];
    const aircraftLayer = layers.find((layer) => layer.id === 'aviation-seeded-aircraft');
    expect(aircraftLayer?.constructor.name).toBe('IconLayer');
    expect(aircraftLayer?.props.pickable).toBe(false);
    const picked = (aircraftLayer?.props.data as Array<{ event?: GeoEvent }>)[0];
    expect(picked?.event?.id).toBe('flight:0');
  });

  it('shares the bounded aviation selection with the WebGL and SVG renderers', () => {
    const routes = Array.from({ length: 220 }, (_, index): GeoEvent => ({
      ...pointEvent(`route:${index}`, 0, 0, 'info'),
      category: 'infrastructure',
      geometry: { type: 'LineString', coordinates: [[0, 0], [index / 10, 4]] },
      properties: {
        mapEntity: 'air-route',
        routeId: `route-${index}`,
        layer: index < 12 ? 'trunk' : 'international',
        trafficScore: 220 - index,
      },
    }));
    const selected = selectAviationRenderData(routes, aviationState());

    expect(selected.routes).toHaveLength(32);
    expect(new Set(selected.routes.map((route) => route.properties.routeId)).size).toBe(32);
  });

  it('precomputes a bounded world-view aviation generation outside the animation frame', () => {
    const routes = Array.from({ length: 180 }, (_, index): GeoEvent => ({
      ...pointEvent(`route:${index}`, 0, 0, 'info'),
      category: 'infrastructure',
      geometry: { type: 'LineString', coordinates: [[-70 + index / 10, 0], [-68 + index / 10, 5]] },
      properties: { mapEntity: 'air-route', routeId: `route-${index}`, layer: 'international', trafficScore: 180 - index },
    }));
    const flights = Array.from({ length: 80 }, (_, index): GeoEvent => ({
      ...pointEvent(`flight:${index}`, 0, 0, 'info'),
      category: 'infrastructure',
      geometry: { type: 'LineString', coordinates: [[-30 + index / 10, -3], [-28 + index / 10, 4]] },
      properties: { mapEntity: 'air-flight', flightId: `flight-${index}`, layer: 'international', phase: index / 100 },
    }));
    const live = Array.from({ length: 40 }, (_, index): GeoEvent => ({
      ...pointEvent(`live:${index}`, -20 + index, 8, 'info'),
      category: 'infrastructure',
      properties: { mapEntity: 'live-aircraft', heading: 90 },
    }));
    const selected = selectAviationRenderData([...routes, ...flights, ...live], aviationState());

    expect(selected.routes).toHaveLength(32);
    expect(selected.routeMotionGroups).toHaveLength(24);
    expect(selected.flights).toHaveLength(24);
    expect(selected.flightMotionGroups).toHaveLength(24);
    expect(selected.liveAircraft).toHaveLength(18);
    expect(aviationLayerStatsForState([...routes, ...flights, ...live], aviationState()))
      .toMatchObject({
        visibleRoutes: 32,
        visibleFlights: 24,
        visibleLiveAircraft: 18,
      });

    const staticSections = createAviationStaticLayerSections(
      [...routes, ...flights, ...live],
      aviationState(),
    );
    const dynamicLayers = createAviationDynamicLayers(staticSections.data, 1) as Layer[];
    expect((staticSections.routeLayers as Layer[]).map((layer) => layer.id)).toEqual([
      'aviation-route-core',
    ]);
    expect(dynamicLayers.map((layer) => layer.id)).toEqual([
      'aviation-route-runners',
      'aviation-seeded-aircraft',
      'aviation-seeded-aircraft-counts',
    ]);
  });

  it('fills the trunk world budget without dropping untagged hubs or live aircraft', () => {
    const routes = Array.from({ length: 360 }, (_, index): GeoEvent => ({
      ...pointEvent(`route:${index}`, 0, 0, 'info'),
      category: 'infrastructure',
      geometry: { type: 'LineString', coordinates: [[-80 + index, 0], [-79 + index, 5]] },
      properties: {
        mapEntity: 'air-route',
        routeId: `route-${index}`,
        fromCode: `H${String(index).padStart(2, '0')}`,
        toCode: `H${String(index + 1).padStart(2, '0')}`,
        layer: index < 18 ? 'trunk' : 'international',
        trafficScore: 360 - index,
      },
    }));
    const flights = Array.from({ length: 140 }, (_, index): GeoEvent => ({
      ...pointEvent(`flight:${index}`, 0, 0, 'info'),
      category: 'infrastructure',
      geometry: { type: 'LineString', coordinates: [[-40 + index, -3], [-39 + index, 4]] },
      properties: {
        mapEntity: 'air-flight',
        flightId: `flight-${index}`,
        layer: index < 18 ? 'trunk' : 'international',
        trafficScore: 140 - index,
      },
    }));
    const hubs = Array.from({ length: 24 }, (_, index): GeoEvent => ({
      ...pointEvent(`hub:${index}`, index, 8, 'info'),
      category: 'infrastructure',
      properties: {
        mapEntity: 'air-hub',
        code: `H${String(index).padStart(2, '0')}`,
        routeCount: index,
      },
    }));
    const live = Array.from({ length: 61 }, (_, index): GeoEvent => ({
      ...pointEvent(`live:${index}`, index, 12, 'info'),
      category: 'infrastructure',
      properties: { mapEntity: 'live-aircraft', velocity: 300 - index },
    }));
    const selected = selectAviationRenderData(
      [...routes, ...flights, ...hubs, ...live],
      { ...aviationState(), aviationLens: 'trunk' },
    );

    expect(new Set(selected.routes.map((route) => route.properties.routeId)).size).toBe(24);
    expect(selected.routes.filter((route) => route.properties.layer === 'trunk')).toHaveLength(18);
    expect(selected.hubs).toHaveLength(10);
    expect(selected.hubs.every((hub) => Number(String(hub.properties.code).slice(1)) <= 18)).toBe(true);
    expect(selected.flightMotionGroups).toHaveLength(18);
    expect(selected.flightMotionGroups.filter((group) => group.event.properties.layer === 'trunk')).toHaveLength(18);
    expect(selected.liveAircraft).toHaveLength(13);

    const watch = selectAviationRenderData(
      [...routes, ...flights, ...hubs, ...live],
      { ...aviationState(), aviationLens: 'watch', aviationRiskSource: 'weather' },
    );
    expect(watch).toMatchObject({ routes: [], hubs: [], flights: [], liveAircraft: [] });
  });

  it('culls aviation paths and aircraft to a padded local viewport without hiding world-view data', () => {
    const nearRoute: GeoEvent = {
      ...pointEvent('near-route', 0, 0, 'info'),
      category: 'infrastructure',
      geometry: { type: 'LineString', coordinates: [[0, 0], [10, 5]] },
      properties: { mapEntity: 'air-route', routeId: 'near', layer: 'international' },
    };
    const distantRoute: GeoEvent = {
      ...pointEvent('distant-route', 0, 0, 'info'),
      category: 'infrastructure',
      geometry: { type: 'LineString', coordinates: [[120, 0], [130, 5]] },
      properties: { mapEntity: 'air-route', routeId: 'distant', layer: 'international' },
    };
    const nearAircraft: GeoEvent = {
      ...pointEvent('near-aircraft', 2, 2, 'info'),
      category: 'infrastructure',
      properties: { mapEntity: 'live-aircraft' },
    };
    const distantAircraft: GeoEvent = {
      ...pointEvent('distant-aircraft', 130, 2, 'info'),
      category: 'infrastructure',
      properties: { mapEntity: 'live-aircraft' },
    };
    const selected = selectAviationRenderData(
      [nearRoute, distantRoute, nearAircraft, distantAircraft],
      { ...aviationState(), zoom: 3 },
      [-20, -20, 20, 20],
    );

    expect(selected.routes.map((event) => event.properties.routeId)).toEqual(['near']);
    expect(selected.liveAircraft.map((event) => event.id)).toEqual(['near-aircraft']);
  });

  it('uses slow warning rings, strong critical rings and a 30 second recent-event fade', () => {
    const warning = hazardPoint('hazard:warning', 'earthquake', 10, 10, 'warning');
    const critical = hazardPoint('hazard:critical', 'volcano', 20, 20, 'critical');
    const now = 100_000;
    const targets = hazardPulseTargets(
      [warning, critical],
      null,
      new Map([[critical.id, now - 15_000]]),
      now,
    );

    expect(targets.status.find((target) => target.event.id === warning.id)?.strength).toBe('warning');
    expect(targets.status.find((target) => target.event.id === critical.id)?.strength).toBe('strong');
    expect(targets.recent).toHaveLength(1);
    expect(targets.recent[0]?.fade).toBeCloseTo(0.5);
  });

  it('suppresses warning motion at world zoom while retaining critical pulses', () => {
    const warning = hazardPoint('hazard:warning:global', 'earthquake', 10, 10, 'warning');
    const critical = hazardPoint('hazard:critical:global', 'volcano', 20, 20, 'critical');
    const targets = hazardPulseTargets([warning, critical], null, new Map(), 100_000, 1.25);

    expect(targets.status.map((target) => target.event.id)).toEqual([critical.id]);
  });

  it('never pulses low-priority FIRMS cells and only animates major aggregates', () => {
    const firms = (id: string, severity: GeoEvent['severity'], detectionCount: number): GeoEvent => ({
      ...hazardPoint(id, 'volcano', 10, 10, severity),
      hazardKind: 'fire-detection',
      metrics: { kind: 'wildfire', detectionCount, fireRadiativePowerMw: detectionCount * 10 },
    } as GeoEvent);
    const low = firms('firms:low', 'info', 1);
    const watch = firms('firms:watch', 'watch', 20);
    const major = firms('firms:major', 'warning', 100);
    const targets = hazardPulseTargets([low, watch, major], null, new Map(), 10_000);

    expect(targets.status.map((target) => target.event.id)).toEqual(['firms:major']);
  });

  it('animates a polygon centre marker without flashing the polygon fill', () => {
    const polygon = {
      ...hazardPoint('hazard:polygon', 'volcano', 0, 0, 'critical'),
      geometry: { type: 'Polygon', coordinates: [[[10, 10], [14, 10], [14, 12], [10, 10]]] },
    } as GeoEvent;
    expect(eventRepresentativePoint(polygon)?.[0]).toBeCloseTo(12.67, 2);
    expect(eventRepresentativePoint(polygon)?.[1]).toBeCloseTo(10.67, 2);
    const layers = createEventPulseLayers({
      events: [polygon],
      selectedEventId: null,
      firstSeenAt: new Map(),
      pulseTime: 5_000,
    }) as Layer[];
    expect(layers[0]?.constructor.name).toBe('ScatterplotLayer');
    expect((layers[0]?.props as unknown as Record<string, unknown>).filled).toBe(false);
  });

  it('uses a restrained hover outline and double hollow rings only for selection', () => {
    const event = hazardPoint('hazard:selected', 'earthquake', 10, 10, 'watch');
    const hover = createEventInteractionLayers([event], null, event.id) as Layer[];
    const selected = createEventInteractionLayers([event], event.id, null) as Layer[];
    expect(hover.map((layer) => layer.id)).toEqual(['world-event-hover-ring']);
    expect(selected.map((layer) => layer.id)).toEqual([
      'world-event-selected-ring-outer',
      'world-event-selected-ring-inner',
    ]);
    expect(selected.every((layer) => {
      const props = layer.props as unknown as Record<string, unknown>;
      return props.filled === false && props.pickable === false;
    })).toBe(true);
  });

  it('fades seeded aircraft at route endpoints and merges screen-grid overlaps by priority', () => {
    const flight = (id: string, phase: number, severity: GeoEvent['severity']): GeoEvent => ({
      ...pointEvent(id, 0, 0, severity),
      category: 'infrastructure',
      geometry: { type: 'LineString', coordinates: [[0, 0], [20, 0]] },
      properties: { mapEntity: 'air-flight', flightId: id, phase, speed: 0.012 },
    });
    const nearHub = aviationSeededFlightPoints([flight('near', 0.01, 'info')], 0, 4)[0]!;
    const midRoute = aviationSeededFlightPoints([flight('middle', 0.5, 'info')], 0, 4)[0]!;
    const overlap = aviationSeededFlightPoints([
      flight('lower-priority', 0.5, 'info'),
      flight('watch-priority', 0.5, 'warning'),
    ], 0, 2);

    expect(nearHub.color[3]).toBeLessThan(midRoute.color[3]);
    expect(midRoute.color[3]).toBe(170);
    expect(overlap).toHaveLength(1);
    expect(overlap[0]?.event.id).toBe('watch-priority');
    expect(overlap[0]?.count).toBe(2);
  });

  it('dims non-selected routes to alpha 36 and keeps live aircraft at bounded opacity', () => {
    const route = (id: string): GeoEvent => ({
      ...pointEvent(id, 0, 0, 'info'),
      category: 'infrastructure',
      geometry: { type: 'LineString', coordinates: [[0, 0], [10, 5]] },
      properties: { mapEntity: 'air-route', routeId: id, layer: 'trunk' },
    });
    const first = route('route:first');
    const second = route('route:second');
    const state = { ...aviationState(), selectedEventId: first.id };
    const sections = createAviationStaticLayerSections([first, second], state);
    const core = (sections.routeLayers as Layer[]).find((layer) => layer.id === 'aviation-route-core')!;
    const coreProps = core.props as unknown as Record<string, unknown>;
    const getColor = coreProps.getColor as (event: GeoEvent) => [number, number, number, number];

    expect(getColor(first)[3]).toBe(235);
    expect(getColor(second)[3]).toBe(36);
    expect(coreProps.autoHighlight).toBe(false);
    expect(aviationAltitudeColor(0)).toEqual([0, 217, 255]);
    expect(aviationAltitudeColor(12_192)).toEqual([235, 50, 55]);
  });

  it('clusters a 2,000 event fixture within the viewport and retains selection', () => {
    const events = Array.from({ length: 2_000 }, (_, index) => pointEvent(
      `fixture-${index}`,
      -179 + (index % 180) * 2,
      -70 + (index % 70) * 2,
      index % 97 === 0 ? 'critical' : 'watch',
    ));
    const clustered = clusterEventPoints(events, 1.25, 'fixture-1999', [-80, -60, 80, 75]);
    expect(clustered.singles.some((event) => event.id === 'fixture-1999')).toBe(true);
    expect(clustered.clusters.length).toBeGreaterThan(0);
    expect(clustered.singles.length + clustered.clusters.length).toBeLessThan(500);
  });
});
