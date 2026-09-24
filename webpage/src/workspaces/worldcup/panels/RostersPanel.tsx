import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupDashboardPayload, WorldCupMatch } from '../types';

export function RostersPanel({ payload, match }: { payload: WorldCupDashboardPayload; match: WorldCupMatch | null }) {
  const teams = match ? [match.homeTeam, match.awayTeam] : [];
  const rosters = payload.rosters.filter((roster) => teams.includes(roster.team));
  return (
    <Panel title="SQUADS" count={rosters.length} className="wm-worldcup-panel wm-worldcup-rosters-panel">
      {rosters.length ? rosters.map((roster) => (
        <section className="wm-worldcup-roster-block" key={roster.team}>
          <div className="wm-worldcup-roster-head">
            <strong>{roster.team}</strong>
            <span>FED</span>
          </div>
          {roster.players.map((player) => (
            <div className="wm-worldcup-player-row" key={`${roster.team}-${player.name}`}>
              <span>{player.name}</span>
              <em>{player.position || '--'} · {player.club || player.status || 'pending'}</em>
            </div>
          ))}
        </section>
      )) : (
        <SourceRequired
          detail="Official federation squad feeds are not connected. No placeholder player rows are rendered."
          rows={[
            { source: 'FIFA / federation squad pages', status: 'required', detail: 'confirmed roster and shirt-number data' },
            { source: 'ESPN injury tracker / club notes', status: 'required', detail: 'availability and injury status' },
          ]}
        />
      )}
    </Panel>
  );
}
