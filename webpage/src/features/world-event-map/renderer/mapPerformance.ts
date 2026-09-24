export type MapPerformancePhase = 'js-build' | 'deck-commit' | 'dynamic-build' | 'dynamic-commit';

type Sample = { phase: MapPerformancePhase; duration: number; at: number };

export type MapPerformanceSnapshot = {
  enabled: boolean;
  longTasks: { count: number; totalMs: number; maxMs: number };
  phases: Record<MapPerformancePhase, { count: number; averageMs: number; p95Ms: number; maxMs: number }>;
};

declare global {
  interface Window {
    __POLYMONITOR_MAP_PERF__?: {
      snapshot: () => MapPerformanceSnapshot;
      reset: () => void;
      resetLongTasks: () => void;
    };
  }
}

const PHASES: MapPerformancePhase[] = ['js-build', 'deck-commit', 'dynamic-build', 'dynamic-commit'];

function percentile(values: number[], quantile: number) {
  if (!values.length) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * quantile))] || 0;
}

export class MapPerformanceMonitor {
  private samples: Sample[] = [];
  private longTasks: number[] = [];
  private observer: PerformanceObserver | null = null;
  private exposedApi: Window['__POLYMONITOR_MAP_PERF__'] = undefined;
  readonly enabled: boolean;

  constructor() {
    const params = typeof window === 'undefined' ? null : new URLSearchParams(window.location.search);
    this.enabled = import.meta.env.DEV || params?.get('mapPerf') === '1';
    if (!this.enabled || typeof window === 'undefined') return;
    if (typeof PerformanceObserver !== 'undefined'
      && PerformanceObserver.supportedEntryTypes?.includes('longtask')) {
      this.observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) this.longTasks.push(entry.duration);
      });
      this.observer.observe({ type: 'longtask', buffered: true });
    }
    this.exposedApi = {
      snapshot: () => this.snapshot(),
      reset: () => this.reset(),
      resetLongTasks: () => this.resetLongTasks(),
    };
    window.__POLYMONITOR_MAP_PERF__ = this.exposedApi;
  }

  measure<T>(phase: MapPerformancePhase, run: () => T): T {
    if (!this.enabled) return run();
    const startedAt = performance.now();
    try {
      return run();
    } finally {
      this.record(phase, performance.now() - startedAt);
    }
  }

  record(phase: MapPerformancePhase, duration: number) {
    if (!this.enabled) return;
    this.samples.push({ phase, duration, at: performance.now() });
    if (this.samples.length > 600) this.samples.splice(0, this.samples.length - 600);
  }

  snapshot(): MapPerformanceSnapshot {
    const phases = Object.fromEntries(PHASES.map((phase) => {
      const values = this.samples.filter((sample) => sample.phase === phase).map((sample) => sample.duration);
      const total = values.reduce((sum, value) => sum + value, 0);
      return [phase, {
        count: values.length,
        averageMs: values.length ? total / values.length : 0,
        p95Ms: percentile(values, 0.95),
        maxMs: values.length ? Math.max(...values) : 0,
      }];
    })) as MapPerformanceSnapshot['phases'];
    const longTaskTotal = this.longTasks.reduce((sum, value) => sum + value, 0);
    return {
      enabled: this.enabled,
      longTasks: {
        count: this.longTasks.length,
        totalMs: longTaskTotal,
        maxMs: this.longTasks.length ? Math.max(...this.longTasks) : 0,
      },
      phases,
    };
  }

  reset() {
    this.samples = [];
    this.longTasks = [];
  }

  resetLongTasks() {
    this.longTasks = [];
  }

  destroy() {
    this.observer?.disconnect();
    this.observer = null;
    if (typeof window !== 'undefined' && window.__POLYMONITOR_MAP_PERF__ === this.exposedApi) {
      delete window.__POLYMONITOR_MAP_PERF__;
    }
    this.exposedApi = undefined;
  }
}
