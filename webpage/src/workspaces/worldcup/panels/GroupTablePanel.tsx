import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupMatch } from '../types';
import { scoreText } from './formatters';
import { buildGroupNames, buildGroupStandings } from './panelUtils';

export function GroupTablePanel({
  matches,
  group,
  onGroupChange,
}: {
  matches: WorldCupMatch[];
  group: string;
  onGroupChange: (group: string) => void;
}) {
  const groups = buildGroupNames(matches);
  const groupMatches = matches.filter((match) => (match.group || '') === group);
  const standings = buildGroupStandings(matches, group);
  return (
    <Panel title="GROUP TABLE" count={groupMatches.length} className="wm-worldcup-panel wm-worldcup-group-table-panel">
      <div className="wm-worldcup-group-tabs">
        {groups.slice(0, 12).map((item) => (
          <button className={item === group ? 'active' : ''} key={item} type="button" onClick={() => onGroupChange(item)}>
            {String(item || 'Group').replace('Group ', '')}
          </button>
        ))}
      </div>
      <div className="wm-worldcup-standings">
        <div className="wm-worldcup-standings-head">
          <span>TEAM</span><span>P</span><span>GD</span><span>PTS</span>
        </div>
        {standings.map((row, index) => (
          <div className="wm-worldcup-standings-row" key={row.team}>
            <strong>{index + 1}. {row.team}</strong>
            <span>{row.played}</span>
            <span>{row.gf - row.ga}</span>
            <b>{row.pts}</b>
          </div>
        ))}
      </div>
      <div className="wm-worldcup-group-match-feed">
        {groupMatches.slice(0, 8).map((match) => (
          <button key={match.id} type="button" className="wm-worldcup-group-match-row">
            <span>#{match.fifaMatchNumber || '--'} · {match.round}</span>
            <strong>{match.homeTeam} <i>{scoreText(match)}</i> {match.awayTeam}</strong>
            <em>{match.kickoffBeijing} · {match.city}</em>
          </button>
        ))}
      </div>
    </Panel>
  );
}
