import { Panel } from '@/components/Panel';
import type { RuntimeGlobalWeatherMapPayload } from '@/types';
import type { PanelRenderMap } from '../../types';
import { panelFromRenderer } from '../helpers';
import {
  bestQuoteBin,
  bookCoverage,
  currentWeatherTemp,
  highWeatherTemp,
  marketSourceLabel,
  panelStatus,
  selectedWeatherCity,
  sourceStatus,
  statusBadge,
  tempLabel,
  updatedLabel,
  weatherSourceLabel,
  WeatherMiniLine,
} from '../weather-detail-utils';
import { useSpecialistCopy } from '@/services/specialist-i18n';

function WeatherCitySnapshotPanel({
  payload,
  selectedCityId,
}: {
  payload?: RuntimeGlobalWeatherMapPayload | null;
  selectedCityId?: string | null;
}) {
  const { copy, shared } = useSpecialistCopy('weather-city-snapshot');
  const city = selectedWeatherCity(payload, selectedCityId);
  const topBin = bestQuoteBin(city);
  const unit = city?.unit || topBin?.unit || '';
  const daily = (city?.daily || []).slice(0, 5);
  return (
    <Panel
      title={copy('title', 'WEATHER CITY SNAPSHOT')}
      badge={statusBadge(payload?.status)}
      status={panelStatus(payload?.status)}
      className="wm-market-panel wm-weather-city-snapshot-panel"
      dataPanelId="weather-city-snapshot"
    >
      {city ? (
        <div className="wm-weather-detail-stack">
          <section className="wm-weather-city-hero">
            <div>
              <span>{city.region || city.country || shared('weatherCity', 'Weather city')}</span>
              <strong>{city.city || '--'}</strong>
              <em>{city.condition || shared('conditionPending', 'Condition pending')}</em>
            </div>
            <b>{tempLabel(currentWeatherTemp(city), unit)}</b>
          </section>
          <div className="wm-weather-city-stats">
            <span><i>{shared('low', 'Low')}</i><strong>{tempLabel(city.todayLow ?? city.daily?.[0]?.low, unit)}</strong></span>
            <span><i>{shared('high', 'High')}</i><strong>{tempLabel(highWeatherTemp(city), unit)}</strong></span>
            <span><i>{shared('book', 'Book')}</i><strong>{bookCoverage(city)}</strong></span>
            <span><i>{shared('updated', 'Updated')}</i><strong>{updatedLabel(city, payload?.generatedAt)}</strong></span>
          </div>
          <WeatherMiniLine city={city} className="wm-weather-city-line" />
          <div className="wm-weather-city-daily">
            {daily.length ? daily.map((day) => (
              <span key={String(day.date)}>
                <i>{String(day.date || '').slice(5) || '--'}</i>
                <strong>{tempLabel(day.high, unit)}</strong>
                <em>{tempLabel(day.low, unit)}</em>
              </span>
            )) : <span><i>{shared('daily', 'Daily')}</i><strong>--</strong><em>{shared('pending', 'pending')}</em></span>}
          </div>
          <div className="wm-weather-city-marketline">
            <span>{weatherSourceLabel(city, payload)} · {marketSourceLabel(city)} · {sourceStatus(city)}</span>
            {city.marketUrl ? <a href={city.marketUrl} target="_blank" rel="noreferrer">{shared('openMarket', 'Open market')}</a> : <em>{shared('noMarketLink', 'No live market link')}</em>}
          </div>
        </div>
      ) : (
        <div className="wm-weather-detail-empty">{copy('empty', 'Select a city on the 2D weather map or temperature monitor.')}</div>
      )}
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'weather-city-snapshot': {
    render: (ctx) => (
      <WeatherCitySnapshotPanel
        payload={ctx.runtimeData['global-temperature-monitor'] as RuntimeGlobalWeatherMapPayload | undefined}
        selectedCityId={ctx.selectedWeatherCityId}
      />
    ),
  },
};

export const panel = panelFromRenderer(renderers, {
  id: 'weather-city-snapshot',
  title: 'Weather City Snapshot',
  eyebrow: 'weather',
  description: 'Selected city temperature, condition, daily range, and market coverage.',
  defaultEnabled: true,
});
