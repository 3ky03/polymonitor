import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupDashboardPayload, WorldCupMatch, WorldCupOddsSnapshot } from '../types';
import { clampNumber } from './panelUtils';

function probabilityValue(value?: number | string | null) {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return number > 1 ? number / 100 : number;
}

function findMoneyline(payload: WorldCupDashboardPayload, match: WorldCupMatch | null): WorldCupOddsSnapshot | null {
  if (!match) return null;
  return payload.odds.find((item) => (
    item.matchId === match.id
    && item.providerType !== 'prediction_market'
    && ['h2h', 'moneyline', 'h2h_3_way'].includes(String(item.marketKey || item.marketType))
  )) || null;
}

export function TeamPowerPanel({ payload, match }: { payload: WorldCupDashboardPayload; match: WorldCupMatch | null }) {
  const teams = match ? [match.homeTeam, match.awayTeam] : payload.rosters.slice(0, 2).map((roster) => roster.team);
  const rows = teams.map((team) => {
    const roster = payload.rosters.find((item) => item.team === team);
    const confirmed = roster?.players.filter((player) => player.status === 'confirmed').length ?? 0;
    const injured = roster?.players.filter((player) => player.status === 'injured').length ?? 0;
    return { team, rosterCount: roster?.players.length ?? 0, confirmed, injured };
  });
  const moneyline = findMoneyline(payload, match);
  const marketRows = teams.map((team) => {
    const outcome = moneyline?.outcomes.find((item) => String(item.name || '').toLowerCase() === team.toLowerCase());
    const probability = probabilityValue(outcome?.impliedProbability);
    const decimalOdds = outcome?.bestDecimalOdds ?? outcome?.decimalOdds;
    return {
      team,
      probability,
      decimalOdds,
      bookCount: outcome?.bookCount ?? moneyline?.bookmakerCount ?? moneyline?.bookmakers?.length ?? 0,
    };
  });
  return (
    <Panel title="TEAM POWER" count={rows.length} className="wm-worldcup-panel wm-worldcup-team-power-panel">
      {payload.rosters.length ? (
        <div className="wm-worldcup-power-grid">
          {rows.map((row) => (
            <section key={row.team}>
              <header><strong>{row.team}</strong><span>{row.rosterCount} players</span></header>
              {[
                ['CONFIRMED', row.confirmed, Math.max(1, row.rosterCount)],
                ['INJURY FLAGS', row.injured, Math.max(1, row.rosterCount)],
                ['ROSTER ROWS', row.rosterCount, 26],
              ].map(([label, value, max]) => (
                <div key={label}>
                  <span>{label}</span>
                  <b>{value}</b>
                  <i style={{ width: `${clampNumber((Number(value) / Number(max)) * 100, 4, 100)}%` }} />
                </div>
              ))}
            </section>
          ))}
        </div>
      ) : moneyline && marketRows.some((row) => row.probability !== null) ? (
        <>
          <div className="wm-worldcup-power-grid wm-worldcup-market-power-grid">
            {marketRows.map((row) => (
              <section key={row.team}>
                <header>
                  <strong>{row.team}</strong>
                  <span>BOOK POWER</span>
                </header>
                <div>
                  <span>IMPLIED</span>
                  <b>{row.probability === null ? '--' : `${(row.probability * 100).toFixed(1)}%`}</b>
                  <i style={{ width: `${clampNumber((row.probability || 0) * 100, 3, 100)}%` }} />
                </div>
                <div>
                  <span>DECIMAL</span>
                  <b>{Number.isFinite(Number(row.decimalOdds)) ? Number(row.decimalOdds).toFixed(2) : '--'}</b>
                </div>
                <div>
                  <span>BOOKS</span>
                  <b>{row.bookCount || '--'}</b>
                </div>
              </section>
            ))}
          </div>
          <SourceRequired
            title="ROSTER SOURCE REQUIRED"
            detail="Showing bookmaker-implied team strength from real odds. Player power still waits for official squads and injury status."
            rows={[
              { source: 'The Odds API h2h', status: 'live', detail: `${moneyline.provider || 'bookmaker'} consensus` },
              { source: 'Official squads', status: 'required', detail: 'player-level strength and availability' },
            ]}
          />
        </>
      ) : (
        <SourceRequired
          detail="Team power cannot be computed without a real squad/rating provider. Elo, form and market value are not estimated in the browser."
          rows={[
            { source: 'FIFA ranking / Elo provider', status: 'required', detail: 'team rating and form inputs' },
            { source: 'Official squads', status: 'required', detail: 'confirmed player pool and availability' },
          ]}
        />
      )}
    </Panel>
  );
}
