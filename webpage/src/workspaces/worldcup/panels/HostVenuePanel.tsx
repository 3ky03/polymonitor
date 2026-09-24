import { matchCity, WORLD_CUP_HOST_MATCH_COUNTS } from '../data';
import { SignalRow, type WorldCupSignalItem } from '../components/SignalRow';
import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupDashboardPayload } from '../types';
import { formatWeatherDay } from './panelUtils';

export function HostVenuePanel({
  payload,
  selectedCityId,
  onSelectCity,
  hostOps,
  risk,
  refVenue,
}: {
  payload: WorldCupDashboardPayload;
  selectedCityId: string | null;
  onSelectCity: (cityId: string) => void;
  hostOps: WorldCupSignalItem[];
  risk: WorldCupSignalItem[];
  refVenue: WorldCupSignalItem[];
}) {
  const cityWeather = payload.weather.map((weather) => ({
    weather,
    city: matchCity(payload.cities, weather.cityId),
    matchCount: Math.max(
      WORLD_CUP_HOST_MATCH_COUNTS[weather.cityId] || 0,
      payload.matches.filter((match) => match.cityId === weather.cityId).length,
    ),
  }));
  const selectedWeather = payload.weather.find((item) => item.cityId === selectedCityId) || payload.weather[0] || null;
  const opsMetrics = [
    ['WIND', selectedWeather?.current.windKph ?? 0, 'kph', 36],
    ['RAIN', selectedWeather?.current.precipitationProbability ?? 0, '%', 100],
    ['TRAVEL LOAD', Math.min(100, (payload.matches.filter((match) => match.cityId === selectedCityId).length || 1) * 9), '%', 100],
    ['DELAY RISK', /storm|rain/i.test(selectedWeather?.current.condition || '') ? 42 : 12, '%', 100],
  ] as const;
  return (
    <Panel title="HOST / VENUE OPS" count={cityWeather.length} className="wm-worldcup-panel wm-worldcup-host-venue-panel">
      {cityWeather.length ? (
        <>
          <div className="wm-worldcup-ops-metrics">
            {opsMetrics.map(([label, value, unit, max]) => (
              <span key={label}>
                <em>{label}</em>
                <strong>{value}{unit}</strong>
                <i style={{ width: `${Math.max(4, Math.min(100, (Number(value) / Number(max)) * 100))}%` }} />
              </span>
            ))}
          </div>
          <div className="wm-worldcup-city-strip">
            {cityWeather.slice(0, 16).map(({ city, weather, matchCount }) => (
              <button className={city.id === selectedCityId ? 'active' : ''} key={city.id} type="button" onClick={() => onSelectCity(city.id)}>
                <span>
                  <strong>{city.city}</strong>
                  <em>{city.country} · {matchCount} matches · {weather.current.condition}</em>
                </span>
                <b>{weather.current.tempC}C</b>
                <i>{weather.forecast.slice(0, 4).map((day) => `${formatWeatherDay(day.date)} ${day.lowC}/${day.highC}`).join(' · ')}</i>
              </button>
            ))}
          </div>
          <div className="wm-worldcup-mini-feed wm-worldcup-mini-feed-compact">
            {[...hostOps.slice(0, 2), ...risk.slice(0, 2), ...refVenue.slice(0, 2)].map((item) => <SignalRow item={item} key={item.id} />)}
          </div>
        </>
      ) : (
        <SourceRequired
          detail="Host ops uses real weather and venue metadata only. It is waiting for runtime weather before showing wind/rain/load rows."
          rows={[{ source: 'Open-Meteo runtime weather', status: 'required', detail: 'host-city current and forecast payload' }]}
        />
      )}
    </Panel>
  );
}
