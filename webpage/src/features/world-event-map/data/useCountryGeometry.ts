import { useEffect, useState } from 'preact/hooks';
import type { FeatureCollection } from 'geojson';
import {
  buildCountryGeometryIndex,
  type CountryGeometryIndex,
} from '../domain/countryGeometry';

const COUNTRY_GEOMETRY_URL = '/map-data/world-countries.geojson';
const COUNTRY_GEOMETRY_TIMEOUT_MS = 6_000;

type CountryGeometryState = {
  index: CountryGeometryIndex | null;
  loading: boolean;
  error: string | null;
};

let cachedIndex: CountryGeometryIndex | null = null;

export function useCountryGeometry(enabled: boolean): CountryGeometryState {
  const [state, setState] = useState<CountryGeometryState>(() => ({
    index: cachedIndex,
    loading: enabled && cachedIndex == null,
    error: null,
  }));

  useEffect(() => {
    if (!enabled) {
      setState((current) => ({ ...current, loading: false }));
      return;
    }
    if (cachedIndex) {
      setState({ index: cachedIndex, loading: false, error: null });
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), COUNTRY_GEOMETRY_TIMEOUT_MS);
    setState({ index: null, loading: true, error: null });
    void fetch(COUNTRY_GEOMETRY_URL, {
      headers: { Accept: 'application/geo+json, application/json' },
      signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok) throw new Error(`Country geometry returned HTTP ${response.status}.`);
      const payload = await response.json() as FeatureCollection;
      if (payload?.type !== 'FeatureCollection' || !Array.isArray(payload.features)) {
        throw new Error('Country geometry is not a GeoJSON FeatureCollection.');
      }
      const index = buildCountryGeometryIndex(payload);
      if (!index.countries.length) throw new Error('Country geometry contains no valid countries.');
      cachedIndex = index;
      setState({ index, loading: false, error: null });
    }).catch((error) => {
      if (controller.signal.aborted) {
        setState({
          index: null,
          loading: false,
          error: `Country geometry timed out after ${COUNTRY_GEOMETRY_TIMEOUT_MS / 1_000}s.`,
        });
        return;
      }
      setState({
        index: null,
        loading: false,
        error: error instanceof Error ? error.message : String(error),
      });
    }).finally(() => window.clearTimeout(timer));
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [enabled]);

  return state;
}
