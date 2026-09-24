import type {
  GlobeMarkerMeta,
  GlobeMarkerTone,
  GlobeMarkerWorkerRequest,
  GlobeMarkerWorkerResult,
  GlobeQualityLevel,
  GlobeWorkerEvent,
} from './worldGlobeMarkersTypes';

const GLOBE_RADIUS = 100;
const HTML_MARKER_CAP = 20;

const REGION_BOUNDS: Record<string, { minLat: number; maxLat: number; minLng: number; maxLng: number }> = {
  global: { minLat: -90, maxLat: 90, minLng: -180, maxLng: 180 },
  america: { minLat: -58, maxLat: 72, minLng: -170, maxLng: -32 },
  mena: { minLat: 5, maxLat: 45, minLng: -18, maxLng: 65 },
  eu: { minLat: 34, maxLat: 72, minLng: -26, maxLng: 45 },
  asia: { minLat: -12, maxLat: 62, minLng: 55, maxLng: 150 },
  latam: { minLat: -58, maxLat: 28, minLng: -118, maxLng: -32 },
  africa: { minLat: -36, maxLat: 38, minLng: -20, maxLng: 55 },
  oceania: { minLat: -50, maxLat: 5, minLng: 105, maxLng: 180 },
};

const QUALITY_BUDGETS: Record<GlobeQualityLevel, { visible: number; raw: number; html: number }> = {
  ultra: { visible: 2000, raw: 1500, html: 20 },
  high: { visible: 1200, raw: 820, html: 20 },
  medium: { visible: 680, raw: 380, html: 14 },
  low: { visible: 280, raw: 95, html: 8 },
};

const GLOBAL_REGION_BOUNDS = REGION_BOUNDS.global as { minLat: number; maxLat: number; minLng: number; maxLng: number };

function numberValue(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function severityRank(item: GlobeWorkerEvent, deaths: number) {
  const severity = String(item.severity || '').toLowerCase();
  if (severity === 'critical' || deaths >= 50) return 5;
  if (severity === 'warning' || deaths >= 10) return 4;
  if (deaths >= 3) return 3;
  if (deaths > 0) return 2;
  return 1;
}

function ucdpColorByRank(rank: number, type?: string | number | null) {
  const normalized = String(type || '').trim();
  if (normalized === '3') return '#ffe12b';
  if (rank >= 5) return '#ff3f36';
  if (rank >= 4) return '#ff7b21';
  if (rank >= 3) return '#ffb526';
  return '#ffd83d';
}

function ucdpTone(item: GlobeWorkerEvent): GlobeMarkerTone {
  const type = String(item.violenceType || '').trim();
  if (type === '1') return 'state';
  if (type === '2') return 'nonstate';
  if (type === '3') return 'onesided';
  return 'watch';
}

function markerViolenceLabel(value?: unknown) {
  const text = String(value || '').trim();
  if (text === '1') return 'STATE-BASED';
  if (text === '2') return 'NON-STATE';
  if (text === '3') return 'ONE-SIDED';
  return text || 'UCDP EVENT';
}

function markerDateScore(value?: string | null) {
  if (!value) return 0;
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return 0;
  return parsed;
}

function markerPriority(item: GlobeWorkerEvent, deaths: number, rank: number, selected: boolean, hovered: boolean) {
  const ageScore = markerDateScore(item.occurredAt) / 1000 / 60 / 60 / 24;
  return (selected ? 1_000_000 : 0)
    + (hovered ? 750_000 : 0)
    + rank * 1200
    + Math.log10(deaths + 1) * 900
    + Math.min(520, ageScore / 30);
}

function markerVisual(rank: number, violenceType?: string | number | null, selected = false) {
  const type = String(violenceType || '').trim();
  if (selected) return { visualKind: 'selected' as const, shape: 'square' as const };
  if (rank >= 5) return { visualKind: 'critical' as const, shape: 'square' as const };
  if (rank >= 4 || type === '3') return { visualKind: 'warning' as const, shape: 'triangle' as const };
  return { visualKind: 'incident' as const, shape: 'circle' as const };
}

function eventLabel(item: GlobeWorkerEvent, deaths: number) {
  const country = item.country || item.locationLabel || 'UCDP';
  const actors = [item.sideA, item.sideB].filter(Boolean).join(' vs ');
  return `${country}${deaths ? ` · ${deaths} deaths` : ''}${actors ? ` · ${actors}` : ''}`;
}

function inRegion(lat: number, lng: number, region: string) {
  const bounds = REGION_BOUNDS[region] ?? GLOBAL_REGION_BOUNDS;
  return lat >= bounds.minLat && lat <= bounds.maxLat && lng >= bounds.minLng && lng <= bounds.maxLng;
}

function latLngToXYZ(lat: number, lng: number, altitude = 0.006) {
  const phi = (90 - lat) * (Math.PI / 180);
  const theta = (90 - lng) * (Math.PI / 180);
  const radius = GLOBE_RADIUS * (1 + altitude);
  const sinPhi = Math.sin(phi);
  return [
    radius * sinPhi * Math.cos(theta),
    radius * Math.cos(phi),
    radius * sinPhi * Math.sin(theta),
  ] as const;
}

function hexToRgb(hex: string) {
  const normalized = hex.replace('#', '');
  const value = Number.parseInt(normalized.length === 3
    ? normalized.split('').map((char) => `${char}${char}`).join('')
    : normalized, 16);
  if (!Number.isFinite(value)) return [1, 0.4, 0.2] as const;
  return [
    ((value >> 16) & 255) / 255,
    ((value >> 8) & 255) / 255,
    (value & 255) / 255,
  ] as const;
}

function normalizeEvents(events: GlobeWorkerEvent[], selectedId?: string | null, hoveredId?: string | null) {
  const normalized: GlobeMarkerMeta[] = [];
  for (const item of events) {
    const lat = numberValue(item.latitude);
    const lng = numberValue(item.longitude);
    if (lat == null || lng == null || lat < -90 || lat > 90 || lng < -180 || lng > 180) continue;
    const deaths = Math.max(0, numberValue(item.deathsBest) ?? 0);
    const rank = severityRank(item, deaths);
    const color = ucdpColorByRank(rank, item.violenceType);
    const id = String(item.id || `${lat}:${lng}:${item.occurredAt || ''}`);
    const selected = id === selectedId;
    const visual = markerVisual(rank, item.violenceType, selected);
    normalized.push({
      id,
      kind: 'event',
      lat,
      lng,
      color,
      size: clamp(6 + Math.log10(deaths + 1) * 3.8 + rank * 0.8, 7, 22),
      tone: ucdpTone(item),
      deaths,
      count: 1,
      label: eventLabel(item, deaths),
      country: item.country || 'Unknown',
      location: item.locationLabel || item.summary || 'Unknown location',
      occurredAt: item.occurredAt || null,
      source: item.source || 'UCDP',
      sourceUrl: item.sourceUrl || null,
      sideA: item.sideA || '',
      sideB: item.sideB || '',
      violenceType: markerViolenceLabel(item.violenceType),
      severity: rank >= 5 ? 'critical' : rank >= 4 ? 'warning' : item.severity || 'watch',
      summary: item.summary || eventLabel(item, deaths),
      ...visual,
      strongGlow: false,
      pulsing: false,
      priority: markerPriority(item, deaths, rank, selected, id === hoveredId),
    });
  }
  return normalized;
}

function gridSizeFor(zoomLevel: number, qualityLevel: GlobeQualityLevel, idle: boolean) {
  if (qualityLevel === 'low' || idle) return zoomLevel >= 3 ? 4 : 9;
  if (zoomLevel >= 4) return 1.4;
  if (zoomLevel >= 3) return 2.5;
  if (zoomLevel >= 2) return 5;
  return 10;
}

function shouldShowRaw(marker: GlobeMarkerMeta, zoomLevel: number, qualityLevel: GlobeQualityLevel, idle: boolean) {
  const critical = marker.priority >= 5 * 1200 || marker.deaths >= 50 || marker.severity === 'critical';
  if (marker.visualKind === 'selected') return true;
  if (qualityLevel === 'low') return critical;
  if (idle && marker.deaths < 10) return false;
  if (zoomLevel <= 1) return critical || marker.deaths >= 10;
  if (zoomLevel === 2) return marker.deaths >= 3 || marker.severity === 'warning' || critical;
  return true;
}

function clusterMarkers(markers: GlobeMarkerMeta[], gridSize: number) {
  const clusters = new Map<string, { items: GlobeMarkerMeta[]; max: GlobeMarkerMeta; latSum: number; lngSum: number; deaths: number }>();
  for (const marker of markers) {
    const latKey = Math.floor((marker.lat + 90) / gridSize);
    const lngKey = Math.floor((marker.lng + 180) / gridSize);
    const key = `${gridSize}:${latKey}:${lngKey}`;
    const current = clusters.get(key);
    if (!current) {
      clusters.set(key, { items: [marker], max: marker, latSum: marker.lat, lngSum: marker.lng, deaths: marker.deaths });
      continue;
    }
    current.items.push(marker);
    current.latSum += marker.lat;
    current.lngSum += marker.lng;
    current.deaths += marker.deaths;
    if (marker.priority > current.max.priority) current.max = marker;
  }

  const result: GlobeMarkerMeta[] = [];
  for (const [key, cluster] of clusters) {
    if (cluster.items.length === 1) {
      result.push(cluster.items[0]!);
      continue;
    }
    const count = cluster.items.length;
    const max = cluster.max;
    result.push({
      ...max,
      id: `cluster:${key}`,
      kind: 'cluster',
      lat: cluster.latSum / count,
      lng: cluster.lngSum / count,
      size: clamp(11 + Math.log2(count + 1) * 3.5 + Math.log10(cluster.deaths + 1), 14, 34),
      count,
      deaths: cluster.deaths,
      label: `${count} conflict events · ${max.country}`,
      location: `${count} clustered events`,
      summary: `${count} events in this cell; highest-priority source event is ${max.location}.`,
      visualKind: 'cluster',
      shape: 'cluster',
      strongGlow: false,
      pulsing: false,
      priority: max.priority + count * 12,
    });
  }
  return result;
}

function applyVisualBudgets(markers: GlobeMarkerMeta[], selectedId?: string | null) {
  let glowCount = 0;
  let pulseCount = 0;
  return markers.map((marker) => {
    const selected = marker.id === selectedId;
    const strongCandidate = selected || marker.visualKind === 'critical' || (marker.kind === 'cluster' && marker.count >= 12);
    const pulseCandidate = selected || (marker.visualKind === 'critical' && marker.deaths >= 50);
    const strongGlow = strongCandidate && glowCount < 150;
    const pulsing = pulseCandidate && pulseCount < 24;
    if (strongGlow) glowCount += 1;
    if (pulsing) pulseCount += 1;
    return {
      ...marker,
      visualKind: selected ? 'selected' : marker.visualKind,
      strongGlow,
      pulsing,
    };
  });
}

function markerFlag(marker: GlobeMarkerMeta) {
  if (marker.shape === 'cluster') return 1;
  if (marker.shape === 'square') return 2;
  if (marker.shape === 'triangle') return 3;
  return 0;
}

function writeBuffers(markers: GlobeMarkerMeta[]) {
  const positions = new Float32Array(markers.length * 3);
  const colors = new Float32Array(markers.length * 3);
  const sizes = new Float32Array(markers.length);
  const opacities = new Float32Array(markers.length);
  const flags = new Uint8Array(markers.length);

  markers.forEach((marker, index) => {
    const [x, y, z] = latLngToXYZ(marker.lat, marker.lng, marker.kind === 'cluster' ? 0.012 : 0.006);
    const [r, g, b] = hexToRgb(marker.color);
    const pos = index * 3;
    positions[pos] = x;
    positions[pos + 1] = y;
    positions[pos + 2] = z;
    colors[pos] = r;
    colors[pos + 1] = g;
    colors[pos + 2] = b;
    sizes[index] = marker.size * (marker.strongGlow ? 1.08 : 0.92);
    opacities[index] = marker.strongGlow ? 0.86 : marker.kind === 'cluster' ? 0.7 : 0.68;
    flags[index] = markerFlag(marker);
  });

  return { positions, colors, sizes, opacities, flags };
}

export function buildMarkerPayload(message: GlobeMarkerWorkerRequest): GlobeMarkerWorkerResult {
  const start = performance.now();
  const budgets = QUALITY_BUDGETS[message.qualityLevel];
  const normalized = normalizeEvents(message.events, message.selectedId, message.hoveredId);
  const regionFiltered = normalized.filter((marker) => message.region === 'global' || inRegion(marker.lat, marker.lng, message.region));
  const sorted = regionFiltered.sort((a, b) => b.priority - a.priority);

  const raw: GlobeMarkerMeta[] = [];
  const clusterCandidates: GlobeMarkerMeta[] = [];
  for (const marker of sorted) {
    if (raw.length < budgets.raw && shouldShowRaw(marker, message.zoomLevel, message.qualityLevel, message.idle)) raw.push(marker);
    else clusterCandidates.push(marker);
  }

  const clustered = clusterMarkers(clusterCandidates, gridSizeFor(message.zoomLevel, message.qualityLevel, message.idle));
  const visible = applyVisualBudgets([...raw, ...clustered]
    .sort((a, b) => b.priority - a.priority)
    .slice(0, budgets.visible), message.selectedId);
  const htmlMarkers = visible
    .filter((marker) => marker.kind !== 'cluster' && (marker.visualKind === 'selected' || marker.visualKind === 'critical' || marker.deaths >= 50))
    .slice(0, Math.min(HTML_MARKER_CAP, budgets.html));
  const buffers = writeBuffers(visible);

  return {
    type: 'MARKERS_BUILT',
    requestId: message.requestId,
    ...buffers,
    meta: visible,
    htmlMarkers,
    totalCount: normalized.length,
    visibleCount: visible.length,
    htmlCount: htmlMarkers.length,
    gpuCount: visible.length,
    clusterCount: visible.filter((marker) => marker.kind === 'cluster').length,
    workerDurationMs: performance.now() - start,
    generatedAt: Date.now(),
  };
}
