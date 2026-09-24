import { SignalRow, type WorldCupSignalItem } from '../components/SignalRow';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupDashboardPayload, WorldCupMatch, WorldCupOddsSnapshot, WorldCupPolymarketMarket } from '../types';
import { scoreText, stageLabel } from './formatters';

export function MatchControlPanel({
  match,
  markets,
  odds,
  weather,
  city,
  facts,
  broadcast,
}: {
  match: WorldCupMatch | null;
  markets: WorldCupPolymarketMarket[];
  odds: WorldCupOddsSnapshot[];
  weather?: WorldCupDashboardPayload['weather'][number] | null;
  city?: WorldCupDashboardPayload['cities'][number] | null;
  facts: WorldCupSignalItem[];
  broadcast: WorldCupSignalItem[];
}) {
  if (!match) {
    return (
      <Panel title="MATCH CONTROL" count={0} className="wm-worldcup-panel wm-worldcup-match-control-panel">
        <div className="wm-worldcup-empty">No match selected.</div>
      </Panel>
    );
  }
  const factCards = [
    ['MATCH', `#${match.fifaMatchNumber || '--'}`, match.round],
    ['GROUP', match.group || stageLabel(match.stage), 'table / fixtures'],
    ['BJT', match.kickoffBeijing, 'desk clock'],
    ['LOCAL', match.kickoffLocal, city?.timezone || 'venue time'],
    ['VENUE', match.venue, `${match.city} · ${city?.capacity ? city.capacity.toLocaleString() : '--'} seats`],
    ['WEATHER', weather ? `${weather.current.tempC}C · ${weather.current.condition}` : 'pending', `wind ${weather?.current.windKph ?? '--'} kph · rain ${weather?.current.precipitationProbability ?? 0}%`],
  ];
  return (
    <Panel title="MATCH CONTROL" count={facts.length + broadcast.length} className="wm-worldcup-panel wm-worldcup-match-control-panel">
      <div className="wm-worldcup-control-score">
        <span><em>HOME</em><strong>{match.homeTeam}</strong></span>
        <b>{scoreText(match)}</b>
        <span><em>AWAY</em><strong>{match.awayTeam}</strong></span>
      </div>
      <div className="wm-worldcup-control-ticker">
        <i>{String(match.status || 'scheduled').toUpperCase()}</i>
        <i>{markets.length} markets</i>
        <i>{odds.length} odds feeds</i>
        <i>{match.city}</i>
      </div>
      <div className="wm-worldcup-control-grid">
        {factCards.map(([label, value, meta]) => (
          <div key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
            <em>{meta}</em>
          </div>
        ))}
      </div>
      <div className="wm-worldcup-mini-feed">
        {[...facts.slice(0, 3), ...broadcast.slice(0, 2)].map((item) => <SignalRow item={item} key={item.id} />)}
      </div>
    </Panel>
  );
}
