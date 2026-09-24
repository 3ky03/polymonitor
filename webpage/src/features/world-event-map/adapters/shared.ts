import type {
  GeoEventFreshness,
  GeoEventSeverity,
  GeoEventSourceStatus,
} from '../domain/types';

export function finiteNumber(value: unknown): number | null {
  if (value == null || value === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

export function normalizedConfidence(value: unknown): number | undefined {
  const numeric = finiteNumber(value);
  if (numeric == null) return undefined;
  const normalized = numeric > 1 ? numeric / 100 : numeric;
  return Math.max(0, Math.min(1, normalized));
}

export function normalizedSeverity(value: unknown): GeoEventSeverity {
  const normalized = String(value || '').trim().toLowerCase();
  if (normalized === 'critical' || normalized === 'emergency' || normalized === 'extreme') return 'critical';
  if (normalized === 'alert' || normalized === 'warning' || normalized === 'high' || normalized === 'severe') return 'warning';
  if (normalized === 'watch' || normalized === 'elevated' || normalized === 'medium') return 'watch';
  return 'info';
}

export function normalizedSourceStatus(value: unknown): GeoEventSourceStatus {
  const normalized = String(value || '').trim().toLowerCase();
  if (normalized === 'error' || normalized === 'failed' || normalized === 'unavailable') return 'error';
  if (normalized === 'degraded' || normalized === 'stale') return 'degraded';
  if (normalized === 'partial' || normalized === 'warming') return 'partial';
  return 'ok';
}

export function normalizedFreshness(value: unknown): GeoEventFreshness {
  const normalized = String(value || '').trim().toLowerCase();
  if (normalized === 'live') return 'live';
  if (normalized === 'fresh' || normalized === 'ok') return 'fresh';
  if (normalized.includes('stale')) return 'stale';
  return 'unknown';
}

export function isoTimestamp(value: unknown): string | undefined {
  if (typeof value !== 'string' || !value.trim() || !Number.isFinite(Date.parse(value))) return undefined;
  return new Date(value).toISOString();
}

export function nonEmpty(value: unknown): string | undefined {
  const normalized = String(value ?? '').trim();
  return normalized || undefined;
}

export function stableId(provider: string, nativeId: unknown): string | null {
  const id = nonEmpty(nativeId);
  return id ? `${provider}:${id}` : null;
}
