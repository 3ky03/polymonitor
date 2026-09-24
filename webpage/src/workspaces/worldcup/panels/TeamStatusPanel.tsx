import { SignalTags, cleanSignalSource, type WorldCupSignalItem } from '../components/SignalRow';
import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupDashboardPayload, WorldCupMatch } from '../types';

export function TeamStatusPanel({
  payload,
  match,
  injuries,
  players,
}: {
  payload: WorldCupDashboardPayload;
  match: WorldCupMatch | null;
  injuries: WorldCupSignalItem[];
  players: WorldCupSignalItem[];
}) {
  const teams = match ? [match.homeTeam, match.awayTeam] : payload.rosters.slice(0, 2).map((roster) => roster.team);
  const rosters = payload.rosters.filter((roster) => teams.includes(roster.team));
  const rosterRows = rosters.slice(0, 2);
  return (
    <Panel title="TEAM STATUS" count={injuries.length + players.length} className="wm-worldcup-panel wm-worldcup-team-status-panel">
      {rosterRows.length ? (
        <div className="wm-worldcup-team-grid">
          {rosterRows.map((roster) => {
            const confirmed = roster.players.filter((player) => player.status === 'confirmed').length;
            const injured = roster.players.filter((player) => player.status === 'injured').length;
            const ready = roster.players.length ? Math.round((confirmed / roster.players.length) * 100) : 0;
            return (
              <section key={roster.team}>
                <header><strong>{roster.team}</strong><span>{roster.players.length} players</span></header>
                <div className="wm-worldcup-team-meter"><i style={{ width: `${Math.max(2, ready)}%` }} /><b>{ready}% ready</b></div>
                <p>{injured ? `${injured} injury flags` : 'No injury flag in connected roster feed'}</p>
                {roster.players.slice(0, 4).map((player) => (
                  <div className="wm-worldcup-team-row" key={`${roster.team}-${player.name}`}>
                    <span>{player.position || 'ALL'}</span>
                    <strong>{player.name}</strong>
                    <em>{player.status || 'watch'}</em>
                  </div>
                ))}
              </section>
            );
          })}
        </div>
      ) : (
        <SourceRequired
          detail="Team status is hidden until official roster and injury status feeds are connected."
          rows={[{ source: 'Federation squads / ESPN injury tracker', status: 'required', detail: 'player-level availability' }]}
        />
      )}
      <div className="wm-worldcup-status-table">
        {[...injuries.slice(0, 3), ...players.slice(0, 3)].map((item) => (
          <div key={item.id}>
            <span>{cleanSignalSource(item.source)}</span>
            <strong>{item.title}</strong>
            <SignalTags tags={item.tags} />
          </div>
        ))}
      </div>
    </Panel>
  );
}
