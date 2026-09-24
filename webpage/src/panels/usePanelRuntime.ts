import { useCallback, useEffect, useRef, useState } from 'preact/hooks';
import {
  fetchPanelRuntimeData,
  getRefreshablePanels,
  mergeRuntimeData,
} from './runtime-store';
import {
  getPanelRefreshPolicy,
  type PanelFetchContext,
  type PanelModule,
  type PanelRefreshTier,
  type PanelRuntimeData,
  type PanelRuntimeStatus,
} from './types';
import type { RuntimePanelMetadata } from '@/services/api';

const DEFAULT_STALE_AFTER_MS: Record<PanelRefreshTier, number> = {
  bootstrap: 5 * 60_000,
  fast: 60_000,
  slow: 15 * 60_000,
  manual: Number.POSITIVE_INFINITY,
};

const EMPTY_STATUS: PanelRuntimeStatus = {
  phase: 'idle',
  updatedAt: null,
  lastAttemptAt: null,
  failureCount: 0,
  error: null,
};

type RuntimeRefreshOptions = {
  panelIds?: Iterable<string>;
  reason?: PanelFetchContext['reason'];
  force?: boolean;
};

type UsePanelRuntimeOptions = {
  panels: PanelModule[];
  activePanelIds: string[];
  initialData?: PanelRuntimeData;
  suspended?: boolean;
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error || 'Panel refresh failed.');
}

function payloadTimestamp(value: unknown): number {
  if (!value || typeof value !== 'object') return Date.now();
  const payload = value as Record<string, unknown>;
  const candidate = payload.generatedAt || payload.updatedAt || payload.asOf || payload.timestamp;
  if (typeof candidate === 'number' && Number.isFinite(candidate)) {
    return candidate > 10_000_000_000 ? candidate : candidate * 1_000;
  }
  const parsed = Date.parse(String(candidate || ''));
  return Number.isFinite(parsed) ? parsed : Date.now();
}

function payloadIsDegraded(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false;
  const status = String((value as { status?: unknown }).status || '').trim().toLowerCase();
  return status === 'degraded' || status === 'error' || status === 'failed' || status === 'warming';
}

function metadataTimestamp(metadata?: RuntimePanelMetadata): number | null {
  const parsed = Date.parse(String(metadata?.freshness.observedAt || ''));
  return Number.isFinite(parsed) ? parsed : null;
}

function metadataPhase(metadata?: RuntimePanelMetadata): PanelRuntimeStatus['phase'] | null {
  const freshness = String(metadata?.freshness.state || '').trim().toLowerCase();
  if (freshness === 'stale') return 'stale';
  if (freshness === 'degraded' || freshness === 'error' || freshness === 'unavailable') return 'degraded';
  return null;
}

function retryDelay(panel: PanelModule, failureCount: number): number | null {
  const retry = getPanelRefreshPolicy(panel)?.retry;
  const attempts = Math.max(0, retry?.attempts ?? 2);
  if (failureCount > attempts) return null;
  const base = Math.max(250, retry?.baseDelayMs ?? 1_000);
  const ceiling = Math.max(base, retry?.maxDelayMs ?? 30_000);
  return Math.min(ceiling, base * (2 ** Math.max(0, failureCount - 1)));
}

export function usePanelRuntime({
  panels,
  activePanelIds,
  initialData = {},
  suspended = false,
}: UsePanelRuntimeOptions) {
  const [runtimeData, setRuntimeData] = useState<PanelRuntimeData>(() => initialData);
  const [statuses, setStatuses] = useState<Record<string, PanelRuntimeStatus>>({});
  const [documentHidden, setDocumentHidden] = useState(() => typeof document !== 'undefined' && document.hidden);
  const dataRef = useRef(runtimeData);
  const statusesRef = useRef(statuses);
  const activePanelIdsRef = useRef(new Set(activePanelIds));
  const inflightRef = useRef(new Map<string, Promise<void>>());
  const controllersRef = useRef(new Map<string, AbortController>());
  const retryTimersRef = useRef(new Map<string, number>());

  const runtimeSuspended = suspended || documentHidden;

  useEffect(() => {
    dataRef.current = runtimeData;
  }, [runtimeData]);

  useEffect(() => {
    statusesRef.current = statuses;
  }, [statuses]);

  useEffect(() => {
    activePanelIdsRef.current = new Set(activePanelIds);
  }, [activePanelIds]);

  const updateStatuses = useCallback((panelIds: string[], updater: (current: PanelRuntimeStatus, panelId: string) => PanelRuntimeStatus) => {
    if (!panelIds.length) return;
    setStatuses((current) => {
      const next = { ...current };
      panelIds.forEach((panelId) => {
        next[panelId] = updater(current[panelId] || EMPTY_STATUS, panelId);
      });
      return next;
    });
  }, []);

  const refreshPanels = useCallback(async (
    requestedPanels: PanelModule[],
    options: RuntimeRefreshOptions = {},
  ): Promise<PanelRuntimeData> => {
    if (runtimeSuspended && !options.force) return {};
    const requestedIds = options.panelIds ? new Set(options.panelIds) : activePanelIdsRef.current;
    const eligibilityNow = Date.now();
    const eligible = requestedPanels.filter((panel) => {
      if (
        typeof panel.fetchData !== 'function'
        || !requestedIds.has(panel.id)
        || inflightRef.current.has(panel.id)
      ) return false;
      const policy = getPanelRefreshPolicy(panel);
      const status = statusesRef.current[panel.id];
      if (!options.force && options.reason === 'refresh' && policy?.tier === 'slow' && status?.updatedAt) {
        const staleAfterMs = policy.staleAfterMs ?? DEFAULT_STALE_AFTER_MS.slow;
        if (eligibilityNow - status.updatedAt < staleAfterMs) return false;
      }
      return true;
    });
    if (!eligible.length) return {};

    const controller = new AbortController();
    const panelIds = eligible.map((panel) => panel.id);
    const reason = options.reason || 'refresh';
    const now = Date.now();
    panelIds.forEach((panelId) => controllersRef.current.set(panelId, controller));
    updateStatuses(panelIds, (current, panelId) => ({
      ...current,
      phase: dataRef.current[panelId] === undefined ? 'loading' : current.phase,
      lastAttemptAt: now,
      error: null,
    }));

    const request = fetchPanelRuntimeData(eligible, {
      signal: controller.signal,
      reason,
      maxBatchSize: Math.min(12, ...eligible.map((panel) => Math.max(1, panel.maxBatchSize || 12))),
      onPanelData: (panelId, value, metadata) => {
        const panel = eligible.find((entry) => entry.id === panelId);
        const policy = panel ? getPanelRefreshPolicy(panel) : undefined;
        const updatedAt = metadataTimestamp(metadata) ?? payloadTimestamp(value);
        const staleAfterMs = policy ? (policy.staleAfterMs ?? DEFAULT_STALE_AFTER_MS[policy.tier]) : Number.POSITIVE_INFINITY;
        const phase = metadataPhase(metadata)
          ?? (payloadIsDegraded(value)
            ? 'degraded'
            : (Number.isFinite(staleAfterMs) && Date.now() - updatedAt > staleAfterMs ? 'stale' : 'ready'));
        const retryTimer = retryTimersRef.current.get(panelId);
        if (retryTimer) {
          window.clearTimeout(retryTimer);
          retryTimersRef.current.delete(panelId);
        }
        setRuntimeData((current) => mergeRuntimeData(current, { [panelId]: value }));
        updateStatuses([panelId], () => ({
          phase,
          updatedAt,
          lastAttemptAt: now,
          failureCount: 0,
          error: null,
          cacheMode: metadata?.cache.mode || null,
          freshness: metadata?.freshness.state || null,
          ageSeconds: metadata?.freshness.ageSeconds ?? null,
        }));
      },
      onPanelError: (panelId, error) => {
        if (controller.signal.aborted) return;
        updateStatuses([panelId], (current) => ({
          ...current,
          phase: dataRef.current[panelId] === undefined ? 'error' : 'degraded',
          lastAttemptAt: now,
          failureCount: current.failureCount + 1,
          error: errorMessage(error),
        }));
      },
    }).then(({ data }) => data).finally(() => {
      panelIds.forEach((panelId) => {
        controllersRef.current.delete(panelId);
        inflightRef.current.delete(panelId);
      });
    });

    const tracked = request.then(() => undefined);
    panelIds.forEach((panelId) => inflightRef.current.set(panelId, tracked));
    return request;
  }, [runtimeSuspended, updateStatuses]);

  const refreshTier = useCallback((
    tier: PanelRefreshTier,
    options: RuntimeRefreshOptions = {},
  ) => refreshPanels(getRefreshablePanels(panels, tier), options), [panels, refreshPanels]);

  useEffect(() => {
    const onVisibilityChange = () => setDocumentHidden(document.hidden);
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => document.removeEventListener('visibilitychange', onVisibilityChange);
  }, []);

  useEffect(() => {
    if (!runtimeSuspended) return;
    controllersRef.current.forEach((controller) => controller.abort());
    retryTimersRef.current.forEach((timer) => window.clearTimeout(timer));
    retryTimersRef.current.clear();
    updateStatuses(
      [...inflightRef.current.keys()],
      (current, panelId) => ({ ...current, phase: dataRef.current[panelId] === undefined ? 'suspended' : current.phase }),
    );
  }, [runtimeSuspended, updateStatuses]);

  useEffect(() => {
    if (runtimeSuspended) return;
    const timers: number[] = [];
    panels.forEach((panel) => {
      const policy = getPanelRefreshPolicy(panel);
      const intervalMs = Number(policy?.intervalMs || 0);
      if (!policy || intervalMs <= 0 || !activePanelIdsRef.current.has(panel.id)) return;
      void refreshPanels([panel], { reason: 'interval' });
      timers.push(window.setInterval(() => {
        if (activePanelIdsRef.current.has(panel.id)) {
          void refreshPanels([panel], { reason: 'interval' });
        }
      }, intervalMs));
    });
    return () => timers.forEach((timer) => window.clearInterval(timer));
  }, [activePanelIds, panels, refreshPanels, runtimeSuspended]);

  useEffect(() => {
    if (runtimeSuspended) return;
    const timer = window.setInterval(() => {
      const now = Date.now();
      setStatuses((current) => {
        let changed = false;
        const next = { ...current };
        panels.forEach((panel) => {
          const status = current[panel.id];
          const policy = getPanelRefreshPolicy(panel);
          if (!status?.updatedAt || !policy || status.phase !== 'ready') return;
          const staleAfterMs = policy.staleAfterMs ?? DEFAULT_STALE_AFTER_MS[policy.tier];
          if (Number.isFinite(staleAfterMs) && now - status.updatedAt > staleAfterMs) {
            next[panel.id] = { ...status, phase: 'stale' };
            changed = true;
          }
        });
        return changed ? next : current;
      });
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [panels, runtimeSuspended]);

  useEffect(() => {
    if (runtimeSuspended) return;
    statusesRef.current = statuses;
    panels.forEach((panel) => {
      const status = statuses[panel.id];
      if (!status?.error || (status.phase !== 'error' && status.phase !== 'degraded')) return;
      if (retryTimersRef.current.has(panel.id) || !activePanelIdsRef.current.has(panel.id)) return;
      const delay = retryDelay(panel, status.failureCount);
      if (delay == null) return;
      const timer = window.setTimeout(() => {
        retryTimersRef.current.delete(panel.id);
        void refreshPanels([panel], { reason: 'retry' });
      }, delay);
      retryTimersRef.current.set(panel.id, timer);
    });
  }, [panels, refreshPanels, runtimeSuspended, statuses]);

  useEffect(() => () => {
    controllersRef.current.forEach((controller) => controller.abort());
    retryTimersRef.current.forEach((timer) => window.clearTimeout(timer));
  }, []);

  const getStatus = useCallback((panelId: string): PanelRuntimeStatus => statuses[panelId] || EMPTY_STATUS, [statuses]);

  return {
    runtimeData,
    setRuntimeData,
    statuses,
    getStatus,
    refreshPanels,
    refreshTier,
    suspended: runtimeSuspended,
  };
}
