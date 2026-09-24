import { matchCity } from '../data';
import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupDashboardPayload } from '../types';
import { formatWeatherDay, weatherIcon, weatherTone } from './panelUtils';

export function WeatherPanel({
  payload,
  selectedCityId,
  onSelectCity,
}: {
  payload: WorldCupDashboardPayload;
  selectedCityId: string | null;
  onSelectCity: (cityId: string) => void;
}) {
  const cityWeather = payload.weather.map((weather) => ({
    weather,
    city: matchCity(payload.cities, weather.cityId),
    matchCount: payload.matches.filter((match) => match.cityId === weather.cityId).length,
  }));
  return (
    <Panel title="WEATHER" count={cityWeather.length} className="wm-worldcup-panel wm-worldcup-weather-panel">
      <div className="wm-worldcup-weather-list">
        {cityWeather.map(({ city, weather, matchCount }) => (
          <button className={`wm-worldcup-weather-row ${city.id === selectedCityId ? 'active' : ''} ${weatherTone(weather.current.condition, weather.current.tempC)}`} key={city.id} type="button" onClick={() => onSelectCity(city.id)}>
            <span className="wm-worldcup-weather-main">
              <strong><i aria-hidden="true">{weatherIcon(weather.current.condition)}</i>{city.city}</strong>
              <em>{city.country} · {matchCount} matches · wind {weather.current.windKph ?? '--'} kph · rain {weather.current.precipitationProbability ?? 0}%</em>
            </span>
            <b>{weather.current.tempC}°C</b>
            <span className="wm-worldcup-weather-condition">{weather.current.condition}</span>
            <span className="wm-worldcup-weather-forecast">
              {weather.forecast.slice(0, 5).map((day) => (
                <i key={`${city.id}-${day.date}`}>
                  <small>{formatWeatherDay(day.date)}</small>
                  <strong>{day.lowC}°/{day.highC}°</strong>
                  <em>{day.precipitationProbability ?? 0}%</em>
                </i>
              ))}
            </span>
          </button>
        ))}
        {!cityWeather.length ? (
          <SourceRequired
            detail="Weather panel requires runtime Open-Meteo/wttr host-city forecasts. No browser-generated temperatures are displayed."
            rows={[
              { source: 'Open-Meteo', status: payload.intelligence?.providerStates?.openMeteo || 'required', detail: 'current and 5-day forecast by host city' },
              { source: 'wttr.in secondary', status: payload.intelligence?.providerStates?.wttr || 'optional', detail: 'used only when Open-Meteo does not return a city' },
            ]}
          />
        ) : null}
      </div>
    </Panel>
  );
}
