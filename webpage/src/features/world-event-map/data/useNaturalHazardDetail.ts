import { useEffect, useState } from 'preact/hooks';
import { fetchNaturalHazardDetail } from '@/services/api';
import type { HazardEvent } from '../domain/types';
import { parseNaturalHazardDetail } from './naturalHazards';
import { recordMapDataPhase } from './mapDataPerformance';

type HazardDetailState = {
  event: HazardEvent | null;
  loading: boolean;
  error: string | null;
};

const detailCache = new Map<string, HazardEvent>();

export function useNaturalHazardDetail(mapEvent: HazardEvent | null): HazardDetailState {
  const shouldFetch = Boolean(mapEvent?.properties.detailAvailable);
  const cached = mapEvent ? detailCache.get(mapEvent.id) || null : null;
  const [state, setState] = useState<HazardDetailState>({
    event: cached,
    loading: Boolean(mapEvent && shouldFetch && !cached),
    error: null,
  });

  useEffect(() => {
    if (!mapEvent || !shouldFetch) {
      setState({ event: null, loading: false, error: null });
      return;
    }
    const cachedEvent = detailCache.get(mapEvent.id);
    if (cachedEvent) {
      setState({ event: cachedEvent, loading: false, error: null });
      return;
    }
    const controller = new AbortController();
    setState({ event: null, loading: true, error: null });
    const startedAt = performance.now();
    void fetchNaturalHazardDetail(mapEvent.id, controller.signal)
      .then((payload) => {
        recordMapDataPhase('network', `detail:${mapEvent.id}`, startedAt, 1);
        const parseStartedAt = performance.now();
        const parsed = parseNaturalHazardDetail(payload);
        recordMapDataPhase('parse', `detail:${mapEvent.id}`, parseStartedAt, 1);
        if (controller.signal.aborted) return;
        detailCache.set(mapEvent.id, parsed.event);
        setState({ event: parsed.event, loading: false, error: null });
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        setState({
          event: null,
          loading: false,
          error: error instanceof Error ? error.message : String(error),
        });
      });
    return () => controller.abort();
  }, [mapEvent?.id, shouldFetch]);

  return state.event && state.event.id !== mapEvent?.id
    ? { event: null, loading: shouldFetch, error: null }
    : state;
}
