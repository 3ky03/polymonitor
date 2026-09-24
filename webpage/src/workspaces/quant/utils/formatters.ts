import type { MetricStatus } from '../types';

export function toNumber(value: unknown) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

export function fmtPrice(value: unknown) {
  return toNumber(value).toFixed(3);
}

export function fmtProbabilityPercent(value: unknown, digits = 1) {
  const numeric = toNumber(value) * 100;
  if (Math.abs(numeric) < 1 && numeric !== 0) return `${numeric.toFixed(2)}%`;
  return `${numeric.toFixed(digits)}%`;
}

export function fmtPercent(value: unknown) {
  const numeric = toNumber(value);
  const sign = numeric > 0 ? '+' : '';
  return `${sign}${numeric.toFixed(2)}%`;
}

export function fmtCurrency(value: unknown) {
  const numeric = toNumber(value);
  const sign = numeric > 0 ? '+' : '';
  return `${sign}${numeric.toLocaleString('en-US', { maximumFractionDigits: 2 })} USDC`;
}

export function fmtCompact(value: unknown) {
  return toNumber(value).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

export function statusClass(value: number): MetricStatus {
  if (value > 0) return 'positive';
  if (value < 0) return 'negative';
  return 'neutral';
}

export function formatTime(timestamp: number) {
  const date = new Date(timestamp * 1000);
  return date.toISOString().slice(0, 16).replace('T', ' ');
}

export function truncateMiddle(value: string, maxLength = 28) {
  const text = value.replace(/\s+/g, ' ').trim();
  if (text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(4, maxLength - 9)).trim()}...${text.slice(-5).trim()}`;
}

function compactDateLabel(value: string) {
  const months: Record<string, string> = {
    january: 'Jan',
    february: 'Feb',
    march: 'Mar',
    april: 'Apr',
    june: 'Jun',
    july: 'Jul',
    august: 'Aug',
    september: 'Sep',
    october: 'Oct',
    november: 'Nov',
    december: 'Dec',
  };
  return value.replace(/\b(January|February|March|April|June|July|August|September|October|November|December)\b/gi, (match) => months[match.toLowerCase()] || match);
}

export function deriveEventOutcomeLabel(eventTitle?: string | null, marketQuestion?: string | null, fallback?: string | null) {
  const event = String(eventTitle || '').replace(/\?$/, '').trim();
  const question = String(marketQuestion || fallback || '').replace(/\s+/g, ' ').trim();
  const source = question || String(fallback || '').trim();
  const lower = source.toLowerCase();

  const exactSeats = source.match(/\bexactly\s+(\d+)\s+(?:republican\s+|democratic\s+)?senate seats?/i);
  if (exactSeats) return `${exactSeats[1]} seats`;
  const moreSeats = source.match(/\b(\d+)\s+or\s+more\s+(?:republican\s+|democratic\s+)?senate seats?/i);
  if (moreSeats) return `${moreSeats[1]}+ seats`;
  const fewerSeats = source.match(/\b(\d+)\s+or\s+fewer\s+(?:republican\s+|democratic\s+)?senate seats?/i);
  if (fewerSeats) return `≤${fewerSeats[1]} seats`;
  const holdSeats = source.match(/\bhold\s+(?:exactly\s+)?(\d+)\s+(?:republican\s+|democratic\s+)?senate seats?/i);
  if (holdSeats) return `${holdSeats[1]} seats`;

  const bpsMove = source.match(/\b(\d+\+?)\s+bps\s+(increase|decrease)/i)
    || source.match(/\b(increase|decrease)\s+interest rates by\s+(\d+\+?)\s+bps/i);
  if (bpsMove) {
    const first = bpsMove[1] || '';
    const second = bpsMove[2] || '';
    const size = /\d/.test(first) ? first : second;
    const direction = /\d/.test(first) ? second : first;
    return `${size} bps ${direction.toLowerCase()}`;
  }
  if (lower.includes('no change') && lower.includes('interest rates')) return 'No change';

  if (event.includes('___')) {
    const [rawPrefix = '', rawSuffix = ''] = event.split('___');
    const prefix = rawPrefix.replace(/\s+/g, ' ').trim();
    const suffix = rawSuffix.replace(/\s+/g, ' ').trim();
    let blankValue = source.replace(/\?$/, '').trim();
    if (prefix && blankValue.toLowerCase().startsWith(prefix.toLowerCase())) {
      blankValue = blankValue.slice(prefix.length).trim();
    }
    if (suffix && blankValue.toLowerCase().endsWith(suffix.toLowerCase())) {
      blankValue = blankValue.slice(0, -suffix.length).trim();
    }
    blankValue = blankValue.replace(/^[:\s-]+|[:\s-]+$/g, '');
    if (blankValue) return truncateMiddle(compactDateLabel(blankValue), 24);
  }

  if (source.includes(':')) {
    const suffix = source.split(':').pop()?.replace(/\?$/, '').trim();
    if (suffix) return truncateMiddle(suffix, 24);
  }
  if (event && source.toLowerCase().startsWith(event.toLowerCase())) {
    const suffix = source.slice(event.length).replace(/^[:\s-]+/, '').replace(/\?$/, '').trim();
    if (suffix) return truncateMiddle(suffix, 24);
  }
  const matchup = source.split(/\s+(?:vs\.?|v\.?)\s+/i).map((part) => part.trim()).filter(Boolean);
  if (matchup.length === 2) return truncateMiddle(matchup[0] || source, 20);
  return truncateMiddle(String(fallback || source || 'Outcome'), 24);
}

export function downloadText(filename: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
