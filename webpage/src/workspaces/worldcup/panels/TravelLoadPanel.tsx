import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupDashboardPayload, WorldCupMatch } from '../types';
import { clampNumber } from './panelUtils';

function daysBetween(a?: string, b?: string) {
  const left = new Date(a || '').getTime();
  const right = new Date(b || '').getTime();
  if (!Number.isFinite(left) || !Number.isFinite(right)) return null;
  return Math.round(Math.abs(left - right) / 86_400_000);
}

export function TravelLoadPanel({ payload, match }: { payload: WorldCupDashboardPayload; match: WorldCupMatch | null }) {
  const teams = match ? [match.homeTeam, match.awayTeam] : payload.rosters.slice(0, 2).map((roster) => roster.team);
  const selected = match || payload.matches[0] || null;
  const weather = selected ? payload.weather.find((item) => item.cityId === selected.cityId) : null;
  const cityMatches = selected ? payload.matches.filter((item) => item.cityId === selected.cityId) : [];
  const teamRows = selected ? teams.map((team) => {
    const teamMatches = payload.matches
      .filter((item) => item.homeTeam === team || item.awayTeam === team)
      .sort((a, b) => new Date(a.kickoffUtc).getTime() - new Date(b.kickoffUtc).getTime());
    const index = teamMatches.findIndex((item) => item.id === selected.id);
    const previous = index > 0 ? teamMatches[index - 1] : null;
    const restDays = previous ? daysBetween(previous.kickoffUtc, selected.kickoffUtc) : null;
    const load = clampNumber(
      (restDays === null ? 22 : restDays <= 3 ? 72 : restDays <= 4 ? 54 : 30)
      + (weather?.current.windKph && weather.current.windKph >= 24 ? 12 : 0)
      + (weather?.current.precipitationProbability && weather.current.precipitationProbability >= 45 ? 12 : 0),
      8,
      96,
    );
    return { team, previous, restDays, load };
  }) : [];
  return (
    <Panel title="TRAVEL LOAD" count={teamRows.length || 0} className="wm-worldcup-panel wm-worldcup-travel-load-panel">
      {selected ? (
        <>
          <div className="wm-worldcup-travel-table">
            <header><span>TEAM</span><span>REST</span><span>LOAD</span><span>CITY</span><span>CONTEXT</span></header>
            {teamRows.map((row) => (
              <div key={row.team}>
                <strong>{row.team}</strong>
                <em>{row.restDays === null ? 'open' : `${row.restDays}d`}</em>
                <span><b>{row.load}</b><i style={{ width: `${row.load}%` }} /></span>
                <em>{cityMatches.length}</em>
                <b>{row.previous ? `${row.previous.city} prev` : 'first visible match'}</b>
              </div>
            ))}
          </div>
          <SourceRequired
            title="LOGISTICS SOURCE REQUIRED"
            detail={`${selected.venue} schedule load is computed from real fixture/weather rows. Team-base travel distance still requires official logistics data.`}
            rows={[
              { source: 'FIFA fixture history', status: payload.matches.length ? 'partial' : 'required', detail: 'previous fixture and recovery window' },
              { source: 'Official team base / federation logistics', status: 'required', detail: 'camp location, flights and travel dates' },
            ]}
          />
        </>
      ) : (
        <SourceRequired
          detail={`Travel load for ${teams.join(' / ') || 'selected teams'} requires actual team-base, previous-match and travel itinerary data. Distances and rest windows are not synthesized.`}
          rows={[
            { source: 'Official team base / federation logistics', status: 'required', detail: 'team camp location and travel dates' },
            { source: 'FIFA fixture history', status: payload.matches.length ? 'partial schedule only' : 'required', detail: 'previous fixture and recovery window' },
          ]}
        />
      )}
    </Panel>
  );
}
