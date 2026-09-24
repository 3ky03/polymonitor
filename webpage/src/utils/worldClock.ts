export type WorldClockLocation = {
  id: string;
  city: string;
  venue: string;
  timezone: string;
  market?: 'sse' | 'nyse' | 'lse' | 'tse' | 'asx' | 'generic';
  home?: boolean;
};

export type WorldClockRow = WorldClockLocation & {
  time: string;
  dayLabel: string;
  gmtLabel: string;
  open: boolean;
  progress: number;
};

export const CORE_WORLD_CLOCKS: WorldClockLocation[] = [
  { id: 'shanghai', city: 'Shanghai', venue: 'SSE', timezone: 'Asia/Shanghai', market: 'sse', home: true },
  { id: 'new-york', city: 'New York', venue: 'NYSE', timezone: 'America/New_York', market: 'nyse' },
  { id: 'london', city: 'London', venue: 'LSE', timezone: 'Europe/London', market: 'lse' },
  { id: 'tokyo', city: 'Tokyo', venue: 'TSE', timezone: 'Asia/Tokyo', market: 'tse' },
  { id: 'sydney', city: 'Sydney', venue: 'ASX', timezone: 'Australia/Sydney', market: 'asx' },
];

const WEEKDAYS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];

function partsFor(date: Date, timezone: string) {
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZoneName: 'shortOffset',
  });
  const parts = Object.fromEntries(formatter.formatToParts(date).map((part) => [part.type, part.value]));
  const dayIndex = WEEKDAYS.indexOf(String(parts.weekday || '').toUpperCase());
  const hour = Number(parts.hour === '24' ? '0' : parts.hour);
  const minute = Number(parts.minute || 0);
  const second = Number(parts.second || 0);
  return {
    time: `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:${String(second).padStart(2, '0')}`,
    weekday: dayIndex >= 0 ? dayIndex : 0,
    hour,
    minute,
    second,
    gmtLabel: String(parts.timeZoneName || 'GMT'),
  };
}

function marketOpen(location: WorldClockLocation, weekday: number, hour: number, minute: number) {
  if (weekday === 0 || weekday === 6) return false;
  const minutes = hour * 60 + minute;
  if (location.market === 'sse') return (minutes >= 570 && minutes < 690) || (minutes >= 780 && minutes < 900);
  if (location.market === 'nyse') return minutes >= 570 && minutes < 960;
  if (location.market === 'lse') return minutes >= 480 && minutes < 990;
  if (location.market === 'tse') return (minutes >= 540 && minutes < 690) || (minutes >= 750 && minutes < 900);
  if (location.market === 'asx') return minutes >= 600 && minutes < 960;
  return true;
}

export function buildWorldClockRows(date: Date, locations: WorldClockLocation[] = CORE_WORLD_CLOCKS): WorldClockRow[] {
  return locations.map((location) => {
    const local = partsFor(date, location.timezone);
    const seconds = local.hour * 3600 + local.minute * 60 + local.second;
    return {
      ...location,
      time: local.time,
      dayLabel: WEEKDAYS[local.weekday] || '---',
      gmtLabel: local.gmtLabel,
      open: marketOpen(location, local.weekday, local.hour, local.minute),
      progress: Math.max(0, Math.min(1, seconds / 86400)),
    };
  });
}

export function normalizeTimezone(value?: string | null) {
  const timezone = String(value || '').trim();
  if (!timezone) return null;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: timezone }).format(new Date());
    return timezone;
  } catch {
    return null;
  }
}
