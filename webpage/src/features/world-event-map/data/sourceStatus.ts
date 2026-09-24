import type {
  GeoEventAdapterResult,
  GeoEventSourceStatus,
  HazardMapResponse,
} from '../domain/types';

export type WorldEventSourceStatus = {
  key: string;
  label: string;
  status: 'loading' | GeoEventSourceStatus;
  eventCount: number;
  rejectedCount: number;
  generatedAt?: string;
  message?: string;
};

export function sourceStatusFromAdapter({
  key,
  label,
  payloadStatus,
  generatedAt,
  result,
  loaded,
}: {
  key: string;
  label: string;
  payloadStatus?: unknown;
  generatedAt?: string;
  result: GeoEventAdapterResult;
  loaded: boolean;
}): WorldEventSourceStatus {
  if (!loaded) {
    return {
      key,
      label,
      status: 'loading',
      eventCount: 0,
      rejectedCount: 0,
    };
  }
  const rawStatus = String(payloadStatus || '').trim().toLowerCase();
  let status: GeoEventSourceStatus = rawStatus === 'error' || rawStatus === 'failed'
    ? 'error'
    : rawStatus === 'degraded' || rawStatus.includes('stale')
      ? 'degraded'
      : rawStatus === 'partial' || result.rejected.length > 0
        ? 'partial'
        : 'ok';
  if (!result.events.length && status === 'ok' && result.rejected.length) status = 'partial';
  return {
    key,
    label,
    status,
    eventCount: result.events.length,
    rejectedCount: result.rejected.length,
    generatedAt,
    message: result.rejected.length
      ? `${result.rejected.length} record${result.rejected.length === 1 ? '' : 's'} rejected by the map contract`
      : undefined,
  };
}

const HAZARD_SOURCE_LABELS: Record<string, string> = {
  usgs: 'USGS',
  'usgs-volcano-cap': 'USGS VOLCANO',
  nhc: 'NHC',
  eonet: 'EONET',
  gdacs: 'GDACS',
  nws: 'NWS',
  firms: 'FIRMS',
  'climate-anomaly': 'ANOMALY',
};

export function sourceStatusesFromHazardResponse(
  response: HazardMapResponse | null,
  rejectedCount = 0,
  loading = false,
): WorldEventSourceStatus[] {
  if (!response) {
    return loading
      ? ['usgs', 'usgs-volcano-cap', 'nhc', 'eonet', 'gdacs', 'nws', 'firms', 'climate-anomaly'].map((key) => ({
          key,
          label: HAZARD_SOURCE_LABELS[key] || key.toUpperCase(),
          status: 'loading' as const,
          eventCount: 0,
          rejectedCount: 0,
        }))
      : [];
  }
  return response.sources.map((source) => {
    const providerKey = source.key.toLowerCase();
    const eventCount = response.events.filter((event) => event.sources.some(
      (item) => item.provider.toLowerCase().includes(providerKey),
    )).length;
    const details = [
      source.coverage.label,
      ...source.coverage.gaps,
      source.errorCode ? `Source condition: ${source.errorCode}` : '',
    ].filter(Boolean);
    const status = source.status === 'ok' && rejectedCount > 0 ? 'partial' : source.status;
    return {
      key: source.key,
      label: HAZARD_SOURCE_LABELS[source.key] || source.key.toUpperCase(),
      status,
      eventCount,
      rejectedCount: source.status === 'ok' ? rejectedCount : 0,
      generatedAt: source.dataUpdatedAt || source.fetchedAt || response.generatedAt,
      message: details.join(' · '),
    };
  });
}

export function sourceStatusesAfterHazardRefreshFailure(
  current: WorldEventSourceStatus[],
  message: string,
  hasSnapshot: boolean,
): WorldEventSourceStatus[] {
  if (!hasSnapshot) {
    if (current.length) {
      return current.map((source) => ({
        ...source,
        status: 'error',
        eventCount: 0,
        message: `Initial source load failed: ${message}`,
      }));
    }
    return [{
      key: 'natural-hazards',
      label: 'HAZARDS',
      status: 'error',
      eventCount: 0,
      rejectedCount: 0,
      message,
    }];
  }
  return current.map((source) => ({
    ...source,
    status: source.status === 'error' ? 'error' : 'degraded',
    message: `${source.message ? `${source.message} · ` : ''}Refresh failed; retaining the last successful snapshot: ${message}`,
  }));
}
