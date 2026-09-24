import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupMatch } from '../types';
import { buildGroupNames, buildGroupStandings } from './panelUtils';

export function GroupAdvancePanel({
  matches,
  group,
  onGroupChange,
}: {
  matches: WorldCupMatch[];
  group: string;
  onGroupChange: (group: string) => void;
}) {
  const groups = buildGroupNames(matches);
  const rows = buildGroupStandings(matches, group);
  const groupFixtures = matches.filter((match) => match.group === group);
  return (
    <Panel title="GROUP ADVANCE" count={rows.length} className="wm-worldcup-panel wm-worldcup-group-advance-panel">
      <div className="wm-worldcup-mini-tabs">
        {groups.slice(0, 8).map((item) => (
          <button className={item === group ? 'active' : ''} key={item} type="button" onClick={() => onGroupChange(item)}>
            {String(item || 'Group').replace('Group ', '')}
          </button>
        ))}
      </div>
      <div className="wm-worldcup-advance-table">
        <header><span>TEAM</span><span>P</span><span>PTS</span><span>GD</span><span>FIX</span></header>
        {rows.map((row, index) => (
          <div key={row.team}>
            <strong>{index + 1}. {row.team}</strong>
            <b>{row.played}</b>
            <b>{row.pts}</b>
            <b>{row.gf - row.ga}</b>
            <span><em>{groupFixtures.filter((match) => match.homeTeam === row.team || match.awayTeam === row.team).length}</em><i style={{ width: `${Math.max(4, row.played * 33)}%` }} /></span>
          </div>
        ))}
      </div>
      {!rows.length ? (
        <SourceRequired
          detail="No verified group rows are available. Advance and win-group probabilities are not generated without a real standings/probability source."
          rows={[{ source: 'FIFA Match Centre / official standings', status: 'required', detail: 'played, points, goals and qualification state' }]}
        />
      ) : null}
    </Panel>
  );
}
