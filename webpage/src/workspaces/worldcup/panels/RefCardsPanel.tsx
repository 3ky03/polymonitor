import { SignalRow, type WorldCupSignalItem } from '../components/SignalRow';
import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupCityWeather, WorldCupMatch } from '../types';

export function RefCardsPanel({ refVenue, match, weather }: { refVenue: WorldCupSignalItem[]; match: WorldCupMatch | null; weather?: WorldCupCityWeather | null }) {
  const fallbackRows: WorldCupSignalItem[] = match ? [
    {
      id: 'ref-appointment-required',
      source: 'FIFA REF FEED',
      title: `${match.homeTeam} vs ${match.awayTeam}: referee appointment not connected`,
      summary: 'Cards, fouls, penalties and VAR tendencies remain disabled until an official appointment/history source is connected.',
      age: '',
      tags: [{ label: 'REF', tone: 'red' }, { label: 'REQUIRED', tone: 'gold' }],
      accent: 'gold',
    },
    {
      id: 'cards-weather-context',
      source: 'MATCH CONTEXT',
      title: `${match.city}: ${weather?.current.condition || 'weather pending'} card-context watch`,
      summary: `Temp ${weather?.current.tempC ?? '--'}C · wind ${weather?.current.windKph ?? '--'} kph · rain ${weather?.current.precipitationProbability ?? 0}%. This is context only, not a referee card model.`,
      age: weather?.generatedAt || '',
      tags: [{ label: 'CONTEXT', tone: 'blue' }, { label: 'NO MODEL', tone: 'gray' }],
      accent: 'blue',
    },
  ] : [];
  const rows = refVenue.length ? refVenue : fallbackRows;
  return (
    <Panel title="REF / CARDS" count={rows.length} className="wm-worldcup-panel wm-worldcup-ref-cards-panel">
      {rows.length ? (
        <div className="wm-worldcup-ref-list">
          {rows.slice(0, 5).map((item) => <SignalRow item={item} key={item.id} />)}
        </div>
      ) : (
        <SourceRequired
          detail="Card profile requires assigned referee history and match-official data. No yellow/red/foul values are generated."
          rows={[{ source: 'FIFA referee appointments / historical referee stats', status: 'required', detail: 'cards, fouls, penalties and VAR tendency' }]}
        />
      )}
    </Panel>
  );
}
