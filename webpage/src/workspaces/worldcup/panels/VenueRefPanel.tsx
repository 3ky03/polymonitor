import { matchCity } from '../data';
import { SignalRow, type WorldCupSignalItem } from '../components/SignalRow';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupDashboardPayload, WorldCupMatch } from '../types';
import { scoreText, stageLabel } from './formatters';

export function VenueRefPanel({
  refVenue,
  risk,
  payload,
  match,
}: {
  refVenue: WorldCupSignalItem[];
  risk: WorldCupSignalItem[];
  payload: WorldCupDashboardPayload;
  match: WorldCupMatch | null;
}) {
  const city = match ? matchCity(payload.cities, match.cityId) : payload.cities[0];
  if (!city) {
    return (
      <Panel title="REF / VENUE BOARD" count={0} className="wm-worldcup-panel wm-worldcup-venue-ref-panel">
        <div className="wm-worldcup-empty">No venue selected.</div>
      </Panel>
    );
  }
  const nearbyMatches = payload.matches.filter((item) => item.cityId === city.id).slice(0, 5);
  return (
    <Panel title="REF / VENUE BOARD" count={refVenue.length + nearbyMatches.length} className="wm-worldcup-panel wm-worldcup-venue-ref-panel">
      <div className="wm-worldcup-venue-card">
        <span>{city.countryName}</span>
        <strong>{city.venue}</strong>
        <em>{city.city} · {city.capacity ? city.capacity.toLocaleString() : '--'} seats · {city.timezone}</em>
      </div>
      <div className="wm-worldcup-venue-fixtures">
        {nearbyMatches.map((item) => (
          <span key={item.id}>
            <em>#{item.fifaMatchNumber || '--'} {item.group || stageLabel(item.stage)}</em>
            <strong>{item.homeTeam} <i>{scoreText(item)}</i> {item.awayTeam}</strong>
            <b>{item.kickoffBeijing}</b>
          </span>
        ))}
      </div>
      <div className="wm-worldcup-mini-feed wm-worldcup-mini-feed-compact">
        {[...refVenue.slice(0, 3), ...risk.slice(0, 2)].map((item) => <SignalRow item={item} key={item.id} />)}
      </div>
    </Panel>
  );
}
