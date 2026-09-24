function formatPercent(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return '--';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return `${(numeric * 100).toFixed(1)}%`;
}

function formatCompact(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return '--';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(numeric);
}

function formatCurrencyCompact(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return '--';
  return `$${formatCompact(value)}`;
}

function formatSignedPercent(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return '--';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  const sign = numeric > 0 ? '+' : '';
  return `${sign}${(numeric * 100).toFixed(1)}%`;
}

function signedClass(value?: string | number | null) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric === 0) return 'flat';
  return numeric > 0 ? 'up' : 'down';
}

function formatDate(value?: string | null) {
  if (!value) return '--';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('en-US', {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatRelative(value?: string | null) {
  if (!value) return '--';
  const parsed = new Date(value);
  const time = parsed.getTime();
  if (Number.isNaN(time)) return '--';
  const diffMs = time - Date.now();
  const absMs = Math.abs(diffMs);
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;
  const suffix = diffMs >= 0 ? '' : ' ago';
  if (absMs < hour) return `${Math.max(1, Math.round(absMs / minute))}m${suffix}`;
  if (absMs < day) return `${Math.round(absMs / hour)}h${suffix}`;
  return `${Math.round(absMs / day)}d${suffix}`;
}

function shortHash(value?: string | null, leading = 10, trailing = 6) {
  if (!value) return '--';
  if (trailing <= 0) {
    return value.length <= leading ? value : `${value.slice(0, leading)}...`;
  }
  if (value.length <= leading + trailing + 3) return value;
  return `${value.slice(0, leading)}...${value.slice(-trailing)}`;
}


export {
  formatPercent,
  formatCompact,
  formatCurrencyCompact,
  formatSignedPercent,
  signedClass,
  formatDate,
  formatRelative,
  shortHash,
};

