type SparklineProps = {
  values?: Array<number | string | null>;
  tone?: string | null;
};

export function numericValue(value?: number | string | null) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

export function toneFromValue(value?: number | string | null) {
  const numeric = numericValue(value);
  if (numeric === null) return 'neutral';
  if (numeric > 0) return 'up';
  if (numeric < 0) return 'down';
  return 'neutral';
}

export function formatMoney(value?: number | string | null, digits = 2) {
  const numeric = numericValue(value);
  if (numeric === null) return '--';
  const maximumFractionDigits = Math.abs(numeric) >= 1000 ? 2 : digits;
  return `$${new Intl.NumberFormat('en-US', { maximumFractionDigits }).format(numeric)}`;
}

export function formatCompact(value?: number | string | null, digits = 1) {
  const numeric = numericValue(value);
  if (numeric === null) return '--';
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: digits }).format(numeric);
}

export function formatPercent(value?: number | string | null, digits = 2) {
  const numeric = numericValue(value);
  if (numeric === null) return '--';
  return `${numeric > 0 ? '+' : ''}${numeric.toFixed(digits)}%`;
}

export function TinySparkline({ values, tone }: SparklineProps) {
  const points = (values || []).map((value) => numericValue(value)).filter((value): value is number => value !== null);
  if (points.length < 2) return <span className={`wm-monitor-sparkline tone-${tone || 'neutral'}`} aria-hidden="true" />;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const spread = max - min || 1;
  const path = points.map((value, index) => {
    const x = (index / Math.max(1, points.length - 1)) * 100;
    const y = 34 - ((value - min) / spread) * 30;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return (
    <svg className={`wm-monitor-sparkline tone-${tone || 'neutral'}`} viewBox="0 0 100 38" aria-hidden="true" focusable="false">
      <polyline points={path} />
    </svg>
  );
}

export function StatusDots() {
  return (
    <div className="wm-monitor-dots" aria-hidden="true">
      <span />
      <span />
      <span />
    </div>
  );
}
