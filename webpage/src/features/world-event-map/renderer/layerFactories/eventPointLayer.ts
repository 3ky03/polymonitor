import { IconLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers';
import type { Layer, LayersList } from '@deck.gl/core';
import type { Feature, Point } from 'geojson';
import Supercluster from 'supercluster';
import type { GeoEvent, GeoEventSeverity } from '../../domain/types';
import {
  worldEventLayerById,
  worldEventLayerIdForEvent,
} from '../../config/layerRegistry';
import {
  MAP_SYMBOL_ATLAS,
  MAP_SYMBOL_ICON_MAPPING,
  MAP_SEVERITY_STYLES,
  mapSymbolForEvent,
  type MapSymbolKey,
} from '../../config/mapSymbols';
import {
  MAP_CLUSTER_COUNT_ATLAS,
  MAP_CLUSTER_COUNT_ICON_MAPPING,
  mapClusterCountIcon,
} from '../../config/mapClusterCountAtlas';
import {
  continuousMetricRadiusMeters,
  eventLabel,
  eventGeometryBounds,
  eventRepresentativePoint,
  MAP_MONO_FONT_FAMILY,
  SEVERITY_COLORS,
  isHazardEvent,
} from './shared';
import {
  disclosureTierForZoom,
  eventDisclosureTier,
  eventVisibleAtZoom,
} from '../eventDisclosure';
import { createEventObservationLayer } from './eventObservationLayer';

export { eventDisclosureTier, eventVisibleAtZoom } from '../eventDisclosure';

export type EventCluster = {
  kind: 'event-cluster';
  id: string;
  coordinates: [number, number];
  eventIds: string[];
  count: number;
  severity: GeoEventSeverity;
  bounds: [number, number, number, number];
  expansionZoom: number;
  color: [number, number, number, number];
  symbol: MapSymbolKey;
  label?: string;
  badge?: string;
};

type ClusterPointProperties = {
  eventId: string;
  severityRank: number;
  west: number;
  south: number;
  east: number;
  north: number;
  representativeEventId: string;
  visibilityTier: number;
  majorCount: number;
  contextCount: number;
  majorSeverityRank: number;
  contextSeverityRank: number;
  representativeMajorEventId: string;
  representativeContextEventId: string;
};

type ClusterAggregateProperties = ClusterPointProperties;

type ClusterBucket = {
  id: string;
  layerId: string;
  index: Supercluster<ClusterPointProperties, ClusterAggregateProperties>;
  eventById: Map<string, GeoEvent>;
};

type UnclusteredBucket = { layerId: string; events: GeoEvent[] };
export type ScreenBox = [number, number, number, number];
export type LabelProjection = (position: [number, number]) => { x: number; y: number } | null;

const MAX_CLUSTER_LEAVES = 200;
const WORLD_VIEWPORT: [number, number, number, number] = [-180, -85, 180, 85];

const SEVERITIES: readonly GeoEventSeverity[] = ['info', 'watch', 'warning', 'critical'];
const SEVERITY_RANK: Record<GeoEventSeverity, number> = {
  info: 0,
  watch: 1,
  warning: 2,
  critical: 3,
};

function severityFromRank(rank: number): GeoEventSeverity {
  return SEVERITIES[Math.max(0, Math.min(SEVERITIES.length - 1, Math.round(rank)))] || 'info';
}

function pointFeature(event: GeoEvent, coordinates: [number, number]): Feature<Point, ClusterPointProperties> {
  const [lon, lat] = coordinates;
  const bounds = eventGeometryBounds(event) || [lon, lat, lon, lat];
  const visibilityTier = eventDisclosureTier(event);
  const severityRank = SEVERITY_RANK[event.severity];
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates },
    properties: {
      eventId: event.id,
      severityRank,
      west: bounds[0],
      south: bounds[1],
      east: bounds[2],
      north: bounds[3],
      representativeEventId: event.id,
      visibilityTier,
      majorCount: visibilityTier === 0 ? 1 : 0,
      contextCount: visibilityTier <= 1 ? 1 : 0,
      majorSeverityRank: visibilityTier === 0 ? severityRank : -1,
      contextSeverityRank: visibilityTier <= 1 ? severityRank : -1,
      representativeMajorEventId: visibilityTier === 0 ? event.id : '',
      representativeContextEventId: visibilityTier <= 1 ? event.id : '',
    },
  };
}

function eventSemanticKey(event: GeoEvent) {
  if (isHazardEvent(event)) return event.hazardKind;
  if (event.category === 'conflict' || event.category === 'unrest') {
    return `conflict-${String(event.properties.violenceType || 'unknown')}`;
  }
  return event.category;
}

function semanticLabel(event: GeoEvent) {
  if (isHazardEvent(event)) return event.hazardKind.replace(/-/g, ' ');
  if (event.category === 'conflict' || event.category === 'unrest') {
    const violenceType = String(event.properties.violenceType || '');
    if (violenceType === '1') return 'state-based conflict';
    if (violenceType === '2') return 'non-state conflict';
    if (violenceType === '3') return 'one-sided violence';
  }
  return event.category.replace(/-/g, ' ');
}

function semanticBadge(event: GeoEvent) {
  if (isHazardEvent(event)) {
    switch (event.hazardKind) {
      case 'earthquake': return 'EQ';
      case 'volcano': return 'VO';
      case 'severe-storm':
      case 'tornado': return 'ST';
      case 'tropical-cyclone': return 'CY';
      case 'flood':
      case 'tsunami': return 'FL';
      case 'wildfire':
      case 'fire-detection': return 'FI';
      case 'extreme-heat': return 'HT';
      case 'extreme-cold': return 'CL';
      case 'temperature-anomaly':
      case 'precipitation-anomaly':
      case 'other-weather-anomaly': return 'AN';
    }
  }
  if (event.category === 'conflict' || event.category === 'unrest') {
    const violenceType = String(event.properties.violenceType || '');
    if (violenceType === '1') return 'SB';
    if (violenceType === '2') return 'NS';
    if (violenceType === '3') return 'OS';
    return 'CF';
  }
  if (event.category === 'intel') return 'IN';
  return '';
}

function inViewport(event: GeoEvent, viewport: [number, number, number, number]) {
  const position = eventRepresentativePoint(event);
  if (!position) return false;
  const [lon, lat] = position;
  return lon >= viewport[0] && lon <= viewport[2] && lat >= viewport[1] && lat <= viewport[3];
}

function isClusterableMarker(event: GeoEvent) {
  if (event.geometry?.type === 'Point') return true;
  return isHazardEvent(event)
    && (event.geometry?.type === 'Polygon' || event.geometry?.type === 'MultiPolygon');
}

/** Persistent source index. Viewport/zoom queries never rebuild Supercluster. */
export class EventClusterIndex {
  private source: GeoEvent[] | null = null;
  private eventById = new Map<string, GeoEvent>();
  private unclustered: UnclusteredBucket[] = [];
  private buckets: ClusterBucket[] = [];
  private queryKey = '';
  private queryResult: { singles: GeoEvent[]; clusters: EventCluster[] } | null = null;
  buildCount = 0;

  update(events: GeoEvent[]) {
    if (events === this.source) return;
    this.source = events;
    this.eventById = new Map();
    this.unclustered = [];
    this.buckets = [];
    this.queryKey = '';
    this.queryResult = null;
    const grouped = new Map<string, GeoEvent[]>();
    for (const event of events) {
      if (!isClusterableMarker(event)) continue;
      if (event.properties.mapEntity === 'air-hub' || event.properties.mapEntity === 'live-aircraft') continue;
      if (!eventRepresentativePoint(event)) continue;
      const layerId = worldEventLayerIdForEvent(event);
      if (!layerId) continue;
      this.eventById.set(event.id, event);
      const bucketId = `${layerId}:${eventSemanticKey(event)}`;
      const bucket = grouped.get(bucketId) || [];
      bucket.push(event);
      grouped.set(bucketId, bucket);
    }

    for (const [id, bucketEvents] of grouped) {
      const layerId = id.split(':', 1)[0]!;
      const layer = worldEventLayerById(layerId);
      if (!layer?.cluster || layer.clusterRadius <= 0) {
        this.unclustered.push({ layerId, events: bucketEvents });
        continue;
      }
      const index = new Supercluster<ClusterPointProperties, ClusterAggregateProperties>({
        radius: layer.clusterRadius,
        minPoints: layer.clusterMinPoints,
        maxZoom: 7,
        map: (properties) => ({ ...properties }),
        reduce: (accumulated, properties) => {
          if (properties.severityRank > accumulated.severityRank) {
            accumulated.severityRank = properties.severityRank;
            accumulated.representativeEventId = properties.representativeEventId;
          }
          accumulated.majorCount += properties.majorCount;
          accumulated.contextCount += properties.contextCount;
          if (properties.majorSeverityRank > accumulated.majorSeverityRank) {
            accumulated.majorSeverityRank = properties.majorSeverityRank;
            accumulated.representativeMajorEventId = properties.representativeMajorEventId;
          }
          if (properties.contextSeverityRank > accumulated.contextSeverityRank) {
            accumulated.contextSeverityRank = properties.contextSeverityRank;
            accumulated.representativeContextEventId = properties.representativeContextEventId;
          }
          accumulated.visibilityTier = Math.min(accumulated.visibilityTier, properties.visibilityTier);
          accumulated.west = Math.min(accumulated.west, properties.west);
          accumulated.south = Math.min(accumulated.south, properties.south);
          accumulated.east = Math.max(accumulated.east, properties.east);
          accumulated.north = Math.max(accumulated.north, properties.north);
        },
      });
      const eventById = new Map(bucketEvents.map((event) => [event.id, event]));
      index.load(bucketEvents.map((event) => pointFeature(event, eventRepresentativePoint(event)!)));
      this.buckets.push({ id, layerId, index, eventById });
    }
    this.buildCount += 1;
  }

  query(
    zoom: number,
    selectedEventId: string | null,
    viewport: [number, number, number, number] = WORLD_VIEWPORT,
  ) {
    const normalizedZoom = Math.max(0, Math.floor(zoom));
    const disclosureTier = disclosureTierForZoom(zoom);
    // Supercluster only needs integer zooms, but disclosure changes at 2.5 and
    // 4.0. Include that tier so crossing 2.5 cannot reuse a world-view result
    // until the next integer zoom.
    const key = `${normalizedZoom}|${disclosureTier}|${selectedEventId || ''}|${viewport.map((value) => value.toFixed(3)).join(':')}`;
    if (key === this.queryKey && this.queryResult) return this.queryResult;
    const selected = selectedEventId ? this.eventById.get(selectedEventId) : undefined;
    const singles: GeoEvent[] = selected ? [selected] : [];
    const addVisible = (event: GeoEvent) => {
      if (event.id === selectedEventId || !inViewport(event, viewport)) return;
      if (eventVisibleAtZoom(event, zoom, selectedEventId)) singles.push(event);
    };
    for (const bucket of this.unclustered) {
      const layer = worldEventLayerById(bucket.layerId);
      if (!layer || zoom < layer.minZoom) continue;
      for (const event of bucket.events) addVisible(event);
    }

    const clusters: EventCluster[] = [];
    for (const bucket of this.buckets) {
      const layer = worldEventLayerById(bucket.layerId);
      if (!layer || zoom < layer.minZoom) continue;
      for (const feature of bucket.index.getClusters(viewport, normalizedZoom)) {
        if (!('cluster' in feature.properties)) {
          const event = bucket.eventById.get(feature.properties.eventId);
          if (event) addVisible(event);
          continue;
        }
        const clusterId = Number(feature.properties.cluster_id);
        const properties = feature.properties as typeof feature.properties & ClusterAggregateProperties;
        // Progressive disclosure controls standalone points, labels and heavy
        // footprints. A cluster is the bounded world-view representation of
        // every active event it contains; removing watch/info leaves from the
        // aggregate made a healthy 500-event feed look empty.
        const visibleCount = Number(feature.properties.point_count);
        const representativeId = properties.representativeEventId;
        const representative = bucket.eventById.get(representativeId)
          || bucket.eventById.get(properties.representativeEventId)
          || bucket.eventById.values().next().value as GeoEvent | undefined;
        if (!representative) continue;
        const leaves = bucket.index.getLeaves(clusterId, MAX_CLUSTER_LEAVES);
        const visibleLeaves = leaves
          .map((leaf) => bucket.eventById.get(leaf.properties.eventId))
          .filter((event): event is GeoEvent => event != null);
        if (visibleCount === 1) {
          addVisible(representative);
          continue;
        }
        const severityRank = properties.severityRank;
        const severity = severityFromRank(Number(severityRank || 0));
        const [red, green, blue] = SEVERITY_COLORS[severity];
        clusters.push({
          kind: 'event-cluster',
          id: `cluster:${bucket.id}:${clusterId}`,
          coordinates: feature.geometry.coordinates as [number, number],
          eventIds: visibleLeaves.map((event) => event.id),
          count: visibleCount,
          severity,
          bounds: [properties.west, properties.south, properties.east, properties.north],
          expansionZoom: bucket.index.getClusterExpansionZoom(clusterId),
          color: [red, green, blue, SEVERITY_COLORS[severity][3]],
          symbol: mapSymbolForEvent(representative),
          label: semanticLabel(representative),
          badge: semanticBadge(representative),
        });
      }
    }
    this.queryKey = key;
    this.queryResult = { singles, clusters };
    return this.queryResult;
  }
}

function pointAlpha(event: GeoEvent, zoom: number, selectedEventId: string | null) {
  if (event.id === selectedEventId) return 255;
  const base = event.severity === 'critical'
    ? 245
    : event.severity === 'warning'
      ? 225
      : event.severity === 'watch'
        ? 180
        : 145;
  const zoomScale = zoom < 2.5 ? 0.72 : zoom < 4 ? 0.86 : 1;
  const scaled = Math.round(base * zoomScale);
  if (event.severity === 'critical') return Math.max(205, scaled);
  if (event.severity === 'warning') return Math.max(150, scaled);
  return Math.max(event.severity === 'watch' ? 118 : 96, scaled);
}

function pointIconSize(event: GeoEvent, zoom: number, selectedEventId: string | null) {
  if (event.id === selectedEventId) return 22;
  if (zoom < 2.5) return 14;
  if (zoom < 4) return 16;
  return 18;
}

function clusterIconSize(cluster: EventCluster) {
  return Math.min(24, 15 + Math.log2(cluster.count + 1) * 1.25);
}

function severityRingColor(severity: GeoEventSeverity, alpha?: number): [number, number, number, number] {
  const [red, green, blue, baseAlpha] = MAP_SEVERITY_STYLES[severity].rgba;
  return [red, green, blue, alpha ?? baseAlpha];
}

export function clusterEventPoints(
  events: GeoEvent[],
  zoom: number,
  selectedEventId: string | null,
  viewport: [number, number, number, number] = WORLD_VIEWPORT,
) {
  const index = new EventClusterIndex();
  index.update(events);
  return index.query(zoom, selectedEventId, viewport);
}

export function createEventPointLayers({
  events,
  zoom,
  selectedEventId,
  showLabels,
  viewport,
  clusterIndex,
  project,
  occupiedScreenBoxes = [],
}: {
  events: GeoEvent[];
  zoom: number;
  selectedEventId: string | null;
  showLabels: boolean;
  viewport?: [number, number, number, number];
  clusterIndex?: EventClusterIndex;
  project?: LabelProjection;
  occupiedScreenBoxes?: ScreenBox[];
}): LayersList {
  const index = clusterIndex || new EventClusterIndex();
  index.update(events);
  const { singles, clusters } = index.query(zoom, selectedEventId, viewport);
  const layers: Layer[] = [
    ...createEventObservationLayer(events, zoom, selectedEventId, viewport),
  ].filter((layer): layer is Layer => Boolean(layer) && !Array.isArray(layer));

  if (clusters.length) {
    layers.push(new ScatterplotLayer<EventCluster>({
      id: 'world-event-cluster-severity-rings',
      data: clusters,
      getPosition: (cluster) => cluster.coordinates,
      getRadius: (cluster) => clusterIconSize(cluster) / 2 + 1,
      getFillColor: [0, 0, 0, 0],
      getLineColor: (cluster) => severityRingColor(cluster.severity),
      getLineWidth: (cluster) => MAP_SEVERITY_STYLES[cluster.severity].lineWidth,
      radiusUnits: 'pixels',
      lineWidthUnits: 'pixels',
      filled: false,
      stroked: true,
      pickable: false,
    }));
    const criticalClusters = clusters.filter((cluster) => cluster.severity === 'critical');
    if (criticalClusters.length) {
      layers.push(new ScatterplotLayer<EventCluster>({
        id: 'world-event-cluster-critical-rings',
        data: criticalClusters,
        getPosition: (cluster) => cluster.coordinates,
        getRadius: (cluster) => clusterIconSize(cluster) / 2 + 3.5,
        getFillColor: [0, 0, 0, 0],
        getLineColor: () => severityRingColor('critical', 145),
        getLineWidth: 1,
        radiusUnits: 'pixels',
        lineWidthUnits: 'pixels',
        filled: false,
        stroked: true,
        pickable: false,
      }));
    }
    layers.push(new IconLayer<EventCluster>({
      id: 'world-event-clusters',
      data: clusters,
      iconAtlas: MAP_SYMBOL_ATLAS,
      iconMapping: MAP_SYMBOL_ICON_MAPPING,
      getIcon: (cluster) => cluster.symbol,
      getPosition: (cluster) => cluster.coordinates,
      getSize: clusterIconSize,
      getColor: (cluster) => [255, 255, 255, cluster.color[3]],
      sizeUnits: 'pixels',
      sizeMinPixels: 15,
      sizeMaxPixels: 24,
      alphaCutoff: 0.05,
      pickable: true,
      autoHighlight: false,
    }));
    layers.push(new IconLayer<EventCluster>({
      id: 'world-event-cluster-counts',
      data: clusters,
      iconAtlas: MAP_CLUSTER_COUNT_ATLAS,
      iconMapping: MAP_CLUSTER_COUNT_ICON_MAPPING,
      getIcon: (cluster) => mapClusterCountIcon(cluster.count),
      getPosition: (cluster) => cluster.coordinates,
      getSize: 27,
      getColor: [255, 255, 255, 255],
      getPixelOffset: [9, 7],
      sizeUnits: 'pixels',
      sizeMinPixels: 22,
      sizeMaxPixels: 30,
      alphaCutoff: 0.02,
      pickable: false,
    }));
  }

  if (singles.length) {
    const intensityEvents = singles.filter((event) => continuousMetricRadiusMeters(event) != null);
    if (intensityEvents.length) {
      layers.push(new ScatterplotLayer<GeoEvent>({
        id: 'world-event-point-intensity',
        data: intensityEvents,
        getPosition: (event) => eventRepresentativePoint(event)!,
        getRadius: (event) => continuousMetricRadiusMeters(event) || 0,
        getFillColor: (event) => {
          const [red, green, blue] = SEVERITY_COLORS[event.severity];
          return [red, green, blue, zoom < 2.5 ? 22 : 32];
        },
        getLineColor: (event) => {
          const [red, green, blue] = SEVERITY_COLORS[event.severity];
          return [red, green, blue, zoom < 2.5 ? 78 : 100];
        },
        getLineWidth: 1,
        radiusMinPixels: 7,
        radiusMaxPixels: zoom < 2.5 ? 14 : zoom < 4 ? 18 : 23,
        lineWidthMinPixels: 0.8,
        pickable: false,
        filled: true,
        stroked: true,
      }));
    }
    layers.push(new ScatterplotLayer<GeoEvent>({
      id: 'world-event-point-severity-rings',
      data: singles,
      getPosition: (event) => eventRepresentativePoint(event)!,
      getRadius: (event) => pointIconSize(event, zoom, selectedEventId) / 2 + 0.75,
      getFillColor: [0, 0, 0, 0],
      getLineColor: (event) => severityRingColor(event.severity),
      getLineWidth: (event) => MAP_SEVERITY_STYLES[event.severity].lineWidth,
      radiusUnits: 'pixels',
      lineWidthUnits: 'pixels',
      filled: false,
      stroked: true,
      pickable: false,
    }));
    const criticalSingles = singles.filter((event) => event.severity === 'critical');
    if (criticalSingles.length) {
      layers.push(new ScatterplotLayer<GeoEvent>({
        id: 'world-event-point-critical-rings',
        data: criticalSingles,
        getPosition: (event) => eventRepresentativePoint(event)!,
        getRadius: (event) => pointIconSize(event, zoom, selectedEventId) / 2 + 3.2,
        getFillColor: [0, 0, 0, 0],
        getLineColor: () => severityRingColor('critical', 145),
        getLineWidth: 1,
        radiusUnits: 'pixels',
        lineWidthUnits: 'pixels',
        filled: false,
        stroked: true,
        pickable: false,
      }));
    }
    layers.push(new IconLayer<GeoEvent>({
      id: 'world-event-points',
      data: singles,
      iconAtlas: MAP_SYMBOL_ATLAS,
      iconMapping: MAP_SYMBOL_ICON_MAPPING,
      getIcon: mapSymbolForEvent,
      getPosition: (event) => eventRepresentativePoint(event)!,
      getSize: (event) => pointIconSize(event, zoom, selectedEventId),
      getColor: (event) => [255, 255, 255, pointAlpha(event, zoom, selectedEventId)],
      sizeUnits: 'pixels',
      sizeMinPixels: 14,
      sizeMaxPixels: 22,
      alphaCutoff: 0.05,
      pickable: true,
      autoHighlight: false,
    }));
  }

  const labelCandidates = showLabels
    ? singles
      .filter((event) => {
        const layerId = worldEventLayerIdForEvent(event);
        const labelMinZoom = layerId ? worldEventLayerById(layerId)?.labelMinZoom ?? 3 : 3;
        return zoom >= labelMinZoom
          && (event.id === selectedEventId
            || event.severity === 'critical'
            || (event.category === 'natural-hazard' && (zoom >= 4 || event.severity === 'warning')));
      })
    : [];
  const boxes: ScreenBox[] = [...occupiedScreenBoxes];
  if (project) {
    for (const event of singles) {
      const point = eventRepresentativePoint(event);
      const screen = point ? project(point) : null;
      if (!screen) continue;
      const radius = pointIconSize(event, zoom, selectedEventId) / 2 + 2;
      boxes.push([screen.x - radius, screen.y - radius, screen.x + radius, screen.y + radius]);
    }
    for (const cluster of clusters) {
      const screen = project(cluster.coordinates);
      if (!screen) continue;
      const radius = clusterIconSize(cluster) / 2 + 5;
      boxes.push([screen.x - radius, screen.y - radius, screen.x + radius, screen.y + radius]);
    }
  }
  const overlaps = (candidate: ScreenBox) => boxes.some((box) => !(
    candidate[2] < box[0] || candidate[0] > box[2] || candidate[3] < box[1] || candidate[1] > box[3]
  ));
  const labeled = labelCandidates
    .sort((left, right) => (
      Number(right.id === selectedEventId) - Number(left.id === selectedEventId)
      || SEVERITY_RANK[right.severity] - SEVERITY_RANK[left.severity]
      || Date.parse(right.updatedAt || '') - Date.parse(left.updatedAt || '')
    ))
    .filter((event) => {
      const point = eventRepresentativePoint(event);
      if (!point) return false;
      if (!project) return event.id === selectedEventId;
      const screen = project(point);
      if (!screen) return false;
      const label = eventLabel(event);
      const fontSize = event.id === selectedEventId ? 11 : 9;
      const width = Math.min(220, Math.max(28, label.length * fontSize * 0.62));
      const candidate: ScreenBox = [screen.x + 5, screen.y - fontSize - 13, screen.x + 5 + width, screen.y - 2];
      if (event.id !== selectedEventId && overlaps(candidate)) return false;
      boxes.push(candidate);
      return true;
    })
    .slice(0, zoom < 4 ? 24 : zoom < 5 ? 60 : 120);
  if (labeled.length) {
    layers.push(new TextLayer<GeoEvent>({
      id: 'world-event-labels',
      data: labeled,
      getPosition: (event) => eventRepresentativePoint(event)!,
      getText: eventLabel,
      getPixelOffset: [7, -8],
      getSize: (event) => event.id === selectedEventId ? 11 : 9,
      getColor: [226, 231, 229, 220],
      getTextAnchor: 'start',
      getAlignmentBaseline: 'bottom',
      fontFamily: MAP_MONO_FONT_FAMILY,
      fontWeight: 700,
      characterSet: 'auto',
      pickable: false,
    }));
  }
  return layers;
}
