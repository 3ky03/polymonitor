export type MapDataPhase = 'cache-read' | 'network' | 'parse' | 'publish';

export type MapDataSample = {
  phase: MapDataPhase;
  source: string;
  durationMs: number;
  eventCount: number;
  at: number;
};

declare global {
  interface Window {
    __POLYMONITOR_MAP_DATA_PERF__?: {
      snapshot: () => MapDataSample[];
      reset: () => void;
    };
  }
}

const samples: MapDataSample[] = [];

function expose() {
  if (typeof window === 'undefined' || window.__POLYMONITOR_MAP_DATA_PERF__) return;
  window.__POLYMONITOR_MAP_DATA_PERF__ = {
    snapshot: () => samples.slice(),
    reset: () => { samples.length = 0; },
  };
}

export function recordMapDataPhase(
  phase: MapDataPhase,
  source: string,
  startedAt: number,
  eventCount = 0,
) {
  if (typeof performance === 'undefined') return;
  const durationMs = Math.max(0, performance.now() - startedAt);
  samples.push({ phase, source, durationMs, eventCount, at: performance.now() });
  if (samples.length > 180) samples.splice(0, samples.length - 180);
  performance.mark(`polymonitor:map-data:${phase}:${source}`);
  if (phase === 'publish' && eventCount > 0
    && performance.getEntriesByName('polymonitor:map:first-hazard').length === 0) {
    performance.mark('polymonitor:map:first-hazard');
  }
  expose();
}
