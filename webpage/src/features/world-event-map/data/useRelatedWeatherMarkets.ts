import { useCallback, useEffect, useRef, useState } from 'preact/hooks';
import { fetchNaturalHazardRelatedMarkets } from '@/services/api';
import type { HazardMarketLinksResponse } from '../domain/types';
import { parseRelatedWeatherMarkets } from './relatedMarkets';

type RelatedWeatherMarketsData = {
  response: HazardMarketLinksResponse | null;
  loading: boolean;
  error: string | null;
};

export type RelatedWeatherMarketsState = RelatedWeatherMarketsData & {
  retry: () => void;
};

export function useRelatedWeatherMarkets(eventId: string | null): RelatedWeatherMarketsState {
  const [state, setState] = useState<RelatedWeatherMarketsData>({
    response: null,
    loading: false,
    error: null,
  });
  const [retrySequence, setRetrySequence] = useState(0);
  const requestIdRef = useRef(0);
  const retry = useCallback(() => setRetrySequence((value) => value + 1), []);

  useEffect(() => {
    const requestId = ++requestIdRef.current;
    if (!eventId) {
      setState({ response: null, loading: false, error: null });
      return undefined;
    }
    const controller = new AbortController();
    setState({ response: null, loading: true, error: null });
    void fetchNaturalHazardRelatedMarkets(eventId, controller.signal)
      .then(parseRelatedWeatherMarkets)
      .then((response) => {
        if (requestId !== requestIdRef.current) return;
        setState({ response, loading: false, error: null });
      })
      .catch((error: unknown) => {
        if (requestId !== requestIdRef.current || controller.signal.aborted) return;
        setState({
          response: null,
          loading: false,
          error: error instanceof Error ? error.message : String(error),
        });
      });
    return () => {
      requestIdRef.current += 1;
      controller.abort();
    };
  }, [eventId, retrySequence]);

  return { ...state, retry };
}
