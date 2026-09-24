import { cleanSignalSource, type WorldCupSignalItem } from '../components/SignalRow';
import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupDashboardPayload, WorldCupMatch } from '../types';
import { clampNumber } from './panelUtils';

export function InjuryLoadPanel({ payload, match, injuries }: { payload: WorldCupDashboardPayload; match: WorldCupMatch | null; injuries: WorldCupSignalItem[] }) {
  const teams = match ? [match.homeTeam, match.awayTeam] : payload.rosters.slice(0, 2).map((roster) => roster.team);
  const rows = teams.map((team) => {
    const roster = payload.rosters.find((item) => item.team === team);
    const injured = roster?.players.filter((player) => player.status === 'injured').length ?? 0;
    const load = roster?.players.length ? clampNumber(injured * 26, 0, 96) : 0;
    return { team, injured, load, hasRoster: Boolean(roster?.players.length) };
  });
  const teamNewsCounts = teams.map((team) => ({
    team,
    count: payload.news.filter((item) => `${item.title} ${item.summary || ''}`.toLowerCase().includes(team.toLowerCase())).length,
  }));
  return (
    <Panel title="INJURY LOAD" count={injuries.length || teamNewsCounts.length} className="wm-worldcup-panel wm-worldcup-injury-load-panel">
      {rows.some((row) => row.hasRoster) ? (
        <div className="wm-worldcup-load-grid">
          {rows.map((row) => (
            <section key={row.team}>
              <header><strong>{row.team}</strong><span className={row.load > 55 ? 'red' : 'green'}>{row.load}/100</span></header>
              <div><span>INJURED</span><b>{row.injured}</b></div>
              <i style={{ width: `${row.load}%` }} />
            </section>
          ))}
        </div>
      ) : null}
      {injuries.length ? (
        <div className="wm-worldcup-load-list">
          {injuries.slice(0, 5).map((item) => (
            <span key={item.id}><b>{cleanSignalSource(item.source)}</b><strong>{item.title}</strong></span>
          ))}
        </div>
      ) : (
        <>
          <div className="wm-worldcup-load-grid wm-worldcup-availability-grid">
            {teamNewsCounts.map((row) => (
              <section key={row.team}>
                <header>
                  <strong>{row.team}</strong>
                  <span className={row.count ? 'green' : 'red'}>{row.count ? 'NEWS' : 'NO FEED'}</span>
                </header>
                <div><span>TEAM NEWS</span><b>{row.count}</b></div>
                <div><span>PLAYER STATUS</span><b>pending</b></div>
                <i style={{ width: `${row.count ? 42 : 8}%` }} />
              </section>
            ))}
          </div>
          <SourceRequired
            title="INJURY SOURCE REQUIRED"
            detail="No verified injury feed is available for this match. Team-news coverage is shown, but doubtful/suspended counts are not guessed."
            rows={[
              { source: 'Google News team watch', status: teamNewsCounts.some((row) => row.count) ? 'partial' : 'empty', detail: 'news mention count only' },
              { source: 'ESPN injury tracker / official team medical notes', status: 'required', detail: 'player-level status with timestamp' },
            ]}
          />
        </>
      )}
    </Panel>
  );
}
