import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupMatch } from '../types';
import { kickoffDay, kickoffTime, sameBeijingDate, scoreText, stageLabel } from './formatters';
import type { MatchFilter } from './registry';

export function CalendarPanel({
  matches,
  selectedMatchId,
  filter,
  now,
  onFilter,
  onSelectMatch,
}: {
  matches: WorldCupMatch[];
  selectedMatchId: string | null;
  filter: MatchFilter;
  now: Date;
  onFilter: (filter: MatchFilter) => void;
  onSelectMatch: (match: WorldCupMatch) => void;
}) {
  const filtered = matches.filter((match) => {
    if (filter === 'today') return sameBeijingDate(match, now);
    if (filter === 'future') return new Date(match.kickoffUtc) > now;
    if (filter === 'finished') return match.status === 'finished';
    if (filter === 'market') return Boolean(match.marketLinked);
    return true;
  });
  const visibleMatches = filtered.length ? filtered : matches;
  return (
    <Panel
      title="CALENDAR"
      count={visibleMatches.length}
      className="wm-worldcup-panel wm-worldcup-schedule-panel"
    >
      <div className="wm-worldcup-filter-strip">
        {(['all', 'today', 'future'] as MatchFilter[]).map((item) => (
          <button className={filter === item ? 'active' : ''} key={item} type="button" onClick={() => onFilter(item)}>{item}</button>
        ))}
      </div>
      <div className="wm-worldcup-match-list">
        {visibleMatches.slice(0, 104).map((match) => (
          <button
            className={`wm-worldcup-match-row ${match.id === selectedMatchId ? 'active' : ''}`}
            key={match.id}
            type="button"
            onClick={() => onSelectMatch(match)}
          >
            <span className="wm-worldcup-match-time">
              <strong>{kickoffTime(match)}</strong>
              <em>{kickoffDay(match)}</em>
            </span>
            <span className="wm-worldcup-match-main">
              <span className="wm-worldcup-match-kicker">#{match.fifaMatchNumber || '--'} · {match.group || stageLabel(match.stage)} · {match.round}</span>
              <strong>{match.homeTeam} <i>{scoreText(match)}</i> {match.awayTeam}</strong>
              <em>{match.kickoffBeijing} · {match.city} · {match.venue}</em>
            </span>
          </button>
        ))}
      </div>
    </Panel>
  );
}
