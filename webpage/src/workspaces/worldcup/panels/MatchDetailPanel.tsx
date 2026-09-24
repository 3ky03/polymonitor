import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupDashboardPayload, WorldCupMatch, WorldCupOddsSnapshot, WorldCupPolymarketMarket } from '../types';
import { scoreText, stageLabel } from './formatters';

export function MatchDetailPanel({
  match,
  markets,
  odds,
  weather,
  city,
}: {
  match: WorldCupMatch | null;
  markets: WorldCupPolymarketMarket[];
  odds: WorldCupOddsSnapshot[];
  weather?: WorldCupDashboardPayload['weather'][number] | null;
  city?: WorldCupDashboardPayload['cities'][number] | null;
}) {
  if (!match) {
    return (
      <Panel title="MATCH DETAIL" count={0} className="wm-worldcup-panel">
        <div className="wm-worldcup-empty">No match selected.</div>
      </Panel>
    );
  }
  return (
    <Panel title="MATCH DETAIL" count={markets.length + odds.length} className="wm-worldcup-panel wm-worldcup-match-panel">
      <div className="wm-worldcup-scoreboard">
        <div><span>HOME</span><strong>{match.homeTeam}</strong></div>
        <b>{scoreText(match)}</b>
        <div><span>AWAY</span><strong>{match.awayTeam}</strong></div>
      </div>
      <div className="wm-worldcup-match-detail-strip">
        <span><b>#{match.fifaMatchNumber || '--'}</b> MATCH</span>
        <span><b>{match.group || stageLabel(match.stage)}</b> GROUP</span>
        <span><b>{markets.length}</b> MKTS</span>
        <span><b>{odds.length}</b> ODDS</span>
      </div>
      <div className="wm-worldcup-match-facts">
        <div><span>MATCH</span><strong>#{match.fifaMatchNumber || '--'} · {match.round}</strong></div>
        <div><span>GROUP</span><strong>{match.group || stageLabel(match.stage)}</strong></div>
        <div><span>BEIJING</span><strong>{match.kickoffBeijing}</strong></div>
        <div><span>LOCAL</span><strong>{match.kickoffLocal}</strong></div>
        <div><span>CITY</span><strong>{match.city}</strong></div>
        <div><span>VENUE</span><strong>{match.venue}</strong></div>
        <div><span>WEATHER</span><strong>{weather ? `${weather.current.tempC}C · ${weather.current.condition}` : 'Host weather watch'}</strong></div>
        <div><span>CAPACITY</span><strong>{city?.capacity ? `${city.capacity.toLocaleString()} seats` : 'Host venue'}</strong></div>
        <div><span>STATE</span><strong>{String(match.status || 'scheduled').toUpperCase()}{match.minute ? ` · ${match.minute}` : ''}</strong></div>
      </div>
      {!markets.length && !odds.length ? (
        <SourceRequired
          detail="Match detail renders schedule and venue data first, then enriches with linked market and odds rows only when real sources arrive."
        />
      ) : null}
    </Panel>
  );
}
