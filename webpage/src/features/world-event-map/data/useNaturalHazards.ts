import { useEffect, useRef, useState } from 'preact/hooks';
import { fetchNaturalHazardMapSource } from '@/services/api';
import type {
  HazardEvent,
  HazardKind,
  HazardMapResponse,
} from '../domain/types';
import type { WorldEventSourceStatus } from './sourceStatus';
import { sourceStatusesFromHazardResponse } from './sourceStatus';
import {
  mergeCanonicalHazardEvents,
  parseNaturalHazardsResponse,
  type ParsedNaturalHazards,
} from './naturalHazards';
import {
  HAZARD_MAP_SOURCE_KEYS,
  hazardMapGeometryZoom,
  readHazardMapSnapshot,
  writeHazardMapSnapshot,
  type HazardMapSourceKey,
} from './hazardMapCache';
import { recordMapDataPhase } from './mapDataPerformance';

const RETRY_DELAYS_MS = [5_000, 10_000, 20_000, 60_000] as const;
const INITIAL_SOURCE_PRIORITY: readonly HazardMapSourceKey[] = [
  'usgs',
  'usgs-volcano-cap',
  'nhc',
  'eonet',
  'nws',
  'gdacs',
  'firms',
  'climate-anomaly',
];
const INITIAL_SOURCE_CONCURRENCY = 3;
const REFRESH_INTERVAL_MS: Record<HazardMapSourceKey, number> = {
  usgs: 60_000,
  'usgs-volcano-cap': 300_000,
  nhc: 120_000,
  eonet: 300_000,
  gdacs: 300_000,
  nws: 60_000,
  firms: 900_000,
  'climate-anomaly': 6 * 60 * 60_000,
};

type SourceRecord = {
  parsed: ParsedNaturalHazards;
  signature: string;
  origin: 'cache' | 'network';
  refreshError: string | null;
};

export type NaturalHazardsState = {
  events: HazardEvent[];
  response: HazardMapResponse | null;
  sources: WorldEventSourceStatus[];
  loading: boolean;
  error: string | null;
  rejectedCount: number;
};

function sourceSignature(parsed: ParsedNaturalHazards) {
  const source = parsed.response.sources[0];
  return JSON.stringify([
    parsed.response.meta?.geometryZoom,
    source?.key,
    source?.status,
    source?.dataUpdatedAt,
    source?.fetchedAt,
    source?.errorCode,
    parsed.events.map((event) => [
      event.id,
      event.updatedAt,
      event.revision.revisionAt,
      event.revision.cancelled,
    ]),
  ]);
}

function latestGeneratedAt(records: Map<HazardMapSourceKey, SourceRecord>) {
  const timestamps = [...records.values()]
    .map((record) => record.parsed.response.generatedAt)
    .filter(Boolean)
    .sort();
  return timestamps[timestamps.length - 1] || new Date(0).toISOString();
}

function mergeHazardEvents(records: Map<HazardMapSourceKey, SourceRecord>) {
  return mergeCanonicalHazardEvents(HAZARD_MAP_SOURCE_KEYS.flatMap(
    (source) => records.get(source)?.parsed.events || [],
  ));
}

function countsByKind(events: HazardEvent[]) {
  const result: Partial<Record<HazardKind, number>> = {};
  for (const event of events) result[event.hazardKind] = (result[event.hazardKind] || 0) + 1;
  return result;
}

function sourceStatus(
  source: HazardMapSourceKey,
  record: SourceRecord | undefined,
  attempted: boolean,
  requestError: string | undefined,
): WorldEventSourceStatus {
  const label = source === 'climate-anomaly'
    ? 'ANOMALY'
    : source === 'usgs-volcano-cap'
      ? 'USGS VOLCANO'
      : source.toUpperCase();
  if (!record) {
    return {
      key: source,
      label,
      status: attempted ? 'error' : 'loading',
      eventCount: 0,
      rejectedCount: 0,
      message: requestError || undefined,
    };
  }
  const parsedStatus = sourceStatusesFromHazardResponse(
    record.parsed.response,
    record.parsed.rejected.length,
  )[0] || {
    key: source,
    label,
    status: 'partial' as const,
    eventCount: record.parsed.events.length,
    rejectedCount: record.parsed.rejected.length,
  };
  const retainedMessage = record.origin === 'cache'
    ? 'Showing the persisted last-good map snapshot while the source refreshes.'
    : '';
  const refreshMessage = record.refreshError
    ? `Refresh failed; retaining the last-good source snapshot: ${record.refreshError}`
    : '';
  return {
    ...parsedStatus,
    status: record.refreshError || record.origin === 'cache'
      ? parsedStatus.status === 'error' ? 'error' : 'degraded'
      : parsedStatus.status,
    message: [parsedStatus.message, retainedMessage, refreshMessage].filter(Boolean).join(' · ') || undefined,
  };
}

function yieldMainThread() {
  const scheduler = globalThis as typeof globalThis & { scheduler?: { yield?: () => Promise<void> } };
  return typeof scheduler.scheduler?.yield === 'function'
    ? scheduler.scheduler.yield()
    : new Promise<void>((resolve) => window.setTimeout(resolve, 0));
}

function viewportForCamera(center: [number, number], zoom: number): [number, number, number, number] | undefined {
  if (zoom < 5) return undefined;
  const longitudinalSpan = Math.max(1.5, 360 / (2 ** zoom) * 1.5);
  const latitudinalSpan = Math.max(1, longitudinalSpan * 0.56);
  const quantum = Math.max(0.25, longitudinalSpan * 0.2);
  const snappedLon = Math.round(center[0] / quantum) * quantum;
  const snappedLat = Math.round(center[1] / quantum) * quantum;
  return [
    Math.max(-180, snappedLon - longitudinalSpan / 2),
    Math.max(-85, snappedLat - latitudinalSpan / 2),
    Math.min(180, snappedLon + longitudinalSpan / 2),
    Math.min(85, snappedLat + latitudinalSpan / 2),
  ].map((value) => Number(value.toFixed(3))) as [number, number, number, number];
}

export function useNaturalHazards(zoom = 2, center: [number, number] = [0, 18]): NaturalHazardsState {
  const geometryZoom = hazardMapGeometryZoom(zoom);
  const firmsViewport = viewportForCamera(center, zoom);
  const firmsViewportKey = firmsViewport?.join(',') || '';
  const [state, setState] = useState<NaturalHazardsState>({
    events: [],
    response: null,
    sources: HAZARD_MAP_SOURCE_KEYS.map((key) => sourceStatus(key, undefined, false, undefined)),
    loading: true,
    error: null,
    rejectedCount: 0,
  });
  const requestGenerationRef = useRef(0);

  useEffect(() => {
    let disposed = false;
    let publishFrame: number | null = null;
    const generation = ++requestGenerationRef.current;
    const records = new Map<HazardMapSourceKey, SourceRecord>();
    const attempts = new Set<HazardMapSourceKey>();
    const requestErrors = new Map<HazardMapSourceKey, string>();
    const failureCounts = new Map<HazardMapSourceKey, number>();
    const controllers = new Map<HazardMapSourceKey, AbortController>();
    const timers = new Map<HazardMapSourceKey, number>();

    const publish = () => {
      if (disposed || publishFrame != null) return;
      publishFrame = window.requestAnimationFrame(() => {
        publishFrame = null;
        if (disposed || generation !== requestGenerationRef.current) return;
        const startedAt = performance.now();
        const events = mergeHazardEvents(records);
        const sources = HAZARD_MAP_SOURCE_KEYS.map((source) => sourceStatus(
          source,
          records.get(source),
          attempts.has(source),
          requestErrors.get(source),
        ));
        const responseSources = [...records.values()].flatMap((record) => record.parsed.response.sources);
        const errors = [...records.values()].flatMap((record) => record.parsed.response.errors);
        for (const [source, message] of requestErrors) {
          if (!records.has(source)) errors.push({ source, code: message });
        }
        const rejectedCount = [...records.values()].reduce(
          (total, record) => total + record.parsed.rejected.length,
          0,
        );
        const response: HazardMapResponse | null = records.size ? {
          schemaVersion: 'natural-hazards-map.v1',
          generatedAt: latestGeneratedAt(records),
          events,
          sources: responseSources,
          isPartial: sources.some((source) => source.status !== 'ok') || rejectedCount > 0,
          errors,
          counts: { events: events.length, byHazardKind: countsByKind(events) },
        } : null;
        const loading = sources.some((source) => source.status === 'loading');
        setState({
          events,
          response,
          sources,
          loading,
          error: !events.length && !loading && requestErrors.size
            ? [...requestErrors.values()].join(' · ')
            : null,
          rejectedCount,
        });
        recordMapDataPhase('publish', 'all', startedAt, events.length);
      });
    };

    const commit = (
      source: HazardMapSourceKey,
      parsed: ParsedNaturalHazards,
      origin: SourceRecord['origin'],
    ) => {
      const existing = records.get(source);
      if (origin === 'cache' && existing?.origin === 'network') return;
      const signature = sourceSignature(parsed);
      records.set(source, existing?.signature === signature
        ? { ...existing, origin, refreshError: null }
        : { parsed, signature, origin, refreshError: null });
      requestErrors.delete(source);
      publish();
    };

    const schedule = (source: HazardMapSourceKey, failed: boolean) => {
      if (disposed) return;
      const failures = failureCounts.get(source) || 0;
      const delay = failed
        ? RETRY_DELAYS_MS[Math.min(RETRY_DELAYS_MS.length - 1, Math.max(0, failures - 1))]!
        : REFRESH_INTERVAL_MS[source];
      const jitter = failed ? Math.floor(Math.random() * 1_000) : Math.floor(Math.random() * 3_000);
      timers.set(source, window.setTimeout(() => void loadSource(source), delay + jitter));
    };

    const loadSource = async (source: HazardMapSourceKey) => {
      controllers.get(source)?.abort();
      const controller = new AbortController();
      controllers.set(source, controller);
      attempts.add(source);
      const networkStartedAt = performance.now();
      try {
        const payload = await fetchNaturalHazardMapSource(
          source,
          geometryZoom,
          source === 'firms' ? firmsViewport : undefined,
          controller.signal,
        );
        recordMapDataPhase('network', source, networkStartedAt, payload.events?.length || 0);
        const parseStartedAt = performance.now();
        const parsed = parseNaturalHazardsResponse(payload);
        recordMapDataPhase('parse', source, parseStartedAt, parsed.events.length);
        if (disposed || controller.signal.aborted || generation !== requestGenerationRef.current) return;
        commit(source, parsed, 'network');
        failureCounts.set(source, 0);
        void writeHazardMapSnapshot(
          source,
          geometryZoom,
          parsed.response,
          source === 'firms' ? firmsViewportKey : '',
        );
        schedule(source, false);
      } catch (error) {
        if (disposed || controller.signal.aborted || generation !== requestGenerationRef.current) return;
        const message = error instanceof Error ? error.message : String(error);
        const failures = (failureCounts.get(source) || 0) + 1;
        failureCounts.set(source, failures);
        requestErrors.set(source, message);
        const existing = records.get(source);
        if (existing) records.set(source, { ...existing, refreshError: message });
        publish();
        schedule(source, true);
      }
    };

    for (const source of HAZARD_MAP_SOURCE_KEYS) {
      const cacheStartedAt = performance.now();
      void readHazardMapSnapshot(
        source,
        geometryZoom,
        source === 'firms' ? firmsViewportKey : '',
      ).then((cached) => {
        recordMapDataPhase('cache-read', source, cacheStartedAt, cached?.payload.events?.length || 0);
        if (!cached || disposed || generation !== requestGenerationRef.current) return;
        try {
          commit(source, parseNaturalHazardsResponse(cached.payload), 'cache');
        } catch {
          // Invalid or old schema cache is ignored and replaced by the network response.
        }
      });
    }

    // Keep the first-paint network fan-out bounded. Fast canonical sources
    // immediately free a slot for the next provider, so a slow GDACS request
    // cannot head-of-line block USGS/EONET/NWS or the persisted cache path.
    let initialSourceIndex = 0;
    const hydrateWorker = async () => {
      while (!disposed && generation === requestGenerationRef.current) {
        const source = INITIAL_SOURCE_PRIORITY[initialSourceIndex];
        initialSourceIndex += 1;
        if (!source) return;
        await loadSource(source);
        await yieldMainThread();
      }
    };
    for (let worker = 0; worker < INITIAL_SOURCE_CONCURRENCY; worker += 1) {
      void hydrateWorker();
    }

    return () => {
      disposed = true;
      requestGenerationRef.current += 1;
      if (publishFrame != null) window.cancelAnimationFrame(publishFrame);
      for (const controller of controllers.values()) controller.abort();
      for (const timer of timers.values()) window.clearTimeout(timer);
    };
  }, [geometryZoom, firmsViewportKey]);

  return state;
}
