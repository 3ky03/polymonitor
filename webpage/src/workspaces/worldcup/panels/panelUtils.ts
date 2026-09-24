import type { WorldCupMatch } from '../types';

export function formatWeatherDay(date: string) {
  const normalized = /^\d{2}-\d{2}$/.test(date) ? `2026-${date}` : date;
  const parsed = new Date(`${normalized}T00:00:00Z`);
  if (!Number.isFinite(parsed.getTime())) return date;
  const weekday = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', weekday: 'short' }).format(parsed);
  const monthDay = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', month: 'short', day: '2-digit' }).format(parsed);
  return `${weekday} ${monthDay}`;
}

export function weatherIcon(condition = '') {
  if (/storm|thunder/i.test(condition)) return '⚡';
  if (/rain|mist|shower/i.test(condition)) return '☔';
  if (/humid|warm|heat/i.test(condition)) return '◐';
  if (/cloud/i.test(condition)) return '☁';
  return '☀';
}

export function weatherTone(condition = '', tempC = 0) {
  if (/storm|thunder|rain|mist|shower/i.test(condition)) return 'weather-rain';
  if (/humid/i.test(condition)) return 'weather-humid';
  if (tempC >= 27 || /warm|heat/i.test(condition)) return 'weather-warm';
  if (/cloud/i.test(condition)) return 'weather-cloud';
  return 'weather-clear';
}

export function clampNumber(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

export function groupName(match: WorldCupMatch | null) {
  return match?.group || 'Group A';
}

export function buildGroupNames(matches: WorldCupMatch[]) {
  const groups = Array.from(new Set(matches.map((match) => match.group).filter(Boolean))) as string[];
  return groups.length ? groups.sort((a, b) => a.localeCompare(b)) : ['Group A'];
}

export function buildGroupStandings(matches: WorldCupMatch[], group: string) {
  const table = new Map<string, { team: string; played: number; gf: number; ga: number; pts: number }>();
  const ensure = (team: string) => {
    if (!table.has(team)) table.set(team, { team, played: 0, gf: 0, ga: 0, pts: 0 });
    return table.get(team)!;
  };
  matches.filter((match) => (match.group || '') === group).forEach((match) => {
    const home = ensure(match.homeTeam);
    const away = ensure(match.awayTeam);
    if (match.homeScore === undefined || match.awayScore === undefined) return;
    home.played += 1;
    away.played += 1;
    home.gf += match.homeScore;
    home.ga += match.awayScore;
    away.gf += match.awayScore;
    away.ga += match.homeScore;
    if (match.homeScore > match.awayScore) home.pts += 3;
    else if (match.homeScore < match.awayScore) away.pts += 3;
    else {
      home.pts += 1;
      away.pts += 1;
    }
  });
  return Array.from(table.values()).sort((a, b) => b.pts - a.pts || (b.gf - b.ga) - (a.gf - a.ga) || a.team.localeCompare(b.team));
}
