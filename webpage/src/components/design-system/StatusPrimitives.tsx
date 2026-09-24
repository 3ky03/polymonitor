import type { ComponentChildren } from 'preact';
import type { PanelRuntimeStatus } from '@/panels/types';

export type OperationalTone = 'positive' | 'warning' | 'critical' | 'info' | 'neutral';

type StatusBadgeProps = {
  label: string;
  tone?: OperationalTone;
  detail?: string | null;
  compact?: boolean;
  className?: string;
};

type FreshnessBadgeProps = {
  freshness?: string | null;
  ageSeconds?: number | null;
  compact?: boolean;
};

type RuntimeStatusBadgeProps = {
  status: PanelRuntimeStatus;
  compact?: boolean;
};

type MetricCardProps = {
  eyebrow: string;
  value: ComponentChildren;
  detail?: ComponentChildren;
  tone?: OperationalTone;
};

const POSITIVE_STATES = new Set(['ok', 'ready', 'live', 'fresh', 'online', 'active', 'healthy', 'synced']);
const WARNING_STATES = new Set(['warming', 'aging', 'stale', 'degraded', 'partial', 'preserved', 'suspended']);
const CRITICAL_STATES = new Set(['error', 'failed', 'missing', 'offline', 'off', 'unavailable', 'critical']);

export function operationalTone(value?: string | null): OperationalTone {
  const normalized = String(value || '').trim().toLowerCase();
  if (POSITIVE_STATES.has(normalized)) return 'positive';
  if (WARNING_STATES.has(normalized)) return 'warning';
  if (CRITICAL_STATES.has(normalized)) return 'critical';
  if (normalized === 'loading' || normalized === 'observed' || normalized === 'network') return 'info';
  return 'neutral';
}

export function formatAge(ageSeconds?: number | null): string {
  if (ageSeconds == null || !Number.isFinite(ageSeconds) || ageSeconds < 0) return '--';
  if (ageSeconds < 5) return 'now';
  if (ageSeconds < 60) return `${Math.round(ageSeconds)}s`;
  if (ageSeconds < 3_600) return `${Math.round(ageSeconds / 60)}m`;
  if (ageSeconds < 86_400) return `${Math.round(ageSeconds / 3_600)}h`;
  return `${Math.round(ageSeconds / 86_400)}d`;
}

export function StatusBadge({
  label,
  tone = operationalTone(label),
  detail,
  compact = false,
  className = '',
}: StatusBadgeProps) {
  return (
    <span
      className={`ds-status-badge is-${tone}${compact ? ' is-compact' : ''}${className ? ` ${className}` : ''}`}
      title={detail || undefined}
    >
      <span className="ds-status-dot" aria-hidden="true" />
      <span>{label}</span>
      {detail && !compact ? <em>{detail}</em> : null}
    </span>
  );
}

export function FreshnessBadge({ freshness, ageSeconds, compact = false }: FreshnessBadgeProps) {
  const normalized = String(freshness || 'unknown').trim().toLowerCase();
  const age = formatAge(ageSeconds);
  const label = age === '--' ? normalized : `${normalized} · ${age}`;
  return (
    <StatusBadge
      compact={compact}
      label={label.toUpperCase()}
      tone={operationalTone(normalized)}
      detail={age === '--' ? 'No observation timestamp available' : `Observed ${age} ago`}
    />
  );
}

export function RuntimeStatusBadge({ status, compact = false }: RuntimeStatusBadgeProps) {
  const phase = status.phase || 'idle';
  const freshness = String(status.freshness || '').trim().toLowerCase();
  const label = freshness && phase === 'ready' ? freshness : phase;
  const age = status.ageSeconds ?? (
    status.updatedAt ? Math.max(0, Math.round((Date.now() - status.updatedAt) / 1_000)) : null
  );
  const detail = [
    status.cacheMode ? `cache ${status.cacheMode}` : null,
    age == null ? null : `age ${formatAge(age)}`,
    status.error || null,
  ].filter(Boolean).join(' · ');
  return (
    <StatusBadge
      compact={compact}
      label={label.toUpperCase()}
      tone={operationalTone(phase === 'ready' ? freshness || 'ready' : phase)}
      detail={detail}
    />
  );
}

export function MetricCard({
  eyebrow,
  value,
  detail,
  tone = 'neutral',
}: MetricCardProps) {
  return (
    <article className={`ds-metric-card is-${tone}`}>
      <span className="ds-metric-card-eyebrow">{eyebrow}</span>
      <strong>{value}</strong>
      {detail ? <div className="ds-metric-card-detail">{detail}</div> : null}
    </article>
  );
}
