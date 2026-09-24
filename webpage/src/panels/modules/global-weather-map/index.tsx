import { Panel } from '@/components/Panel';
import { fetchRuntimeGlobalTemperatureMonitor } from '@/services/api';
import type { RuntimeGlobalWeatherCity, RuntimeGlobalWeatherMapPayload, RuntimeWeatherQuoteBin } from '@/types';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { bookCoverage as liveBookCoverage, WeatherCanvasSparkline, weatherSourceLabel } from '../weather-detail-utils';
import { useSpecialistCopy } from '@/services/specialist-i18n';

function statusBadge(status?: string | null) {
  const text = String(status || '').toLowerCase();
  if (text === 'ok') return 'LIVE';
  if (text === 'degraded') return 'PARTIAL';
  if (text === 'warming') return 'WARMING';
  return text ? text.toUpperCase() : 'SEED';
}

function panelStatus(status?: string | null): 'live' | 'muted' {
  return String(status || '').toLowerCase() === 'ok' ? 'live' : 'muted';
}

function num(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function tempLabel(value?: string | number | null, unit?: string | null) {
  const parsed = num(value);
  if (parsed === null) return '--';
  return `${Math.round(parsed)}°${unit || ''}`;
}

function priceLabel(value?: string | number | null) {
  const parsed = num(value);
  if (parsed === null) return '--';
  return `${Math.round(parsed * 100)}%`;
}

function currentTempValue(city: RuntimeGlobalWeatherCity) {
  return city.currentTemp ?? city.metarTemp ?? city.todayHigh ?? null;
}

function highTempValue(city: RuntimeGlobalWeatherCity) {
  return city.forecastHigh ?? city.todayHigh ?? city.currentTemp ?? city.metarTemp ?? null;
}

function citySortValue(city: RuntimeGlobalWeatherCity) {
  return num(highTempValue(city)) ?? -999;
}

function cityTone(city: RuntimeGlobalWeatherCity) {
  const high = num(highTempValue(city));
  if (high === null) return 'neutral';
  if (String(city.unit || '').toUpperCase() === 'F') {
    if (high >= 90) return 'hot';
    if (high <= 45) return 'cool';
  } else {
    if (high >= 32) return 'hot';
    if (high <= 7) return 'cool';
  }
  return 'neutral';
}

function bestBin(city: RuntimeGlobalWeatherCity): RuntimeWeatherQuoteBin | null {
  if (city.topBin) return city.topBin;
  const bins = city.bins || [];
  let best: RuntimeWeatherQuoteBin | null = null;
  for (const bin of bins) {
    if ((num(bin.midPriceYes) ?? -1) > (num(best?.midPriceYes) ?? -1)) best = bin;
  }
  return best;
}

function MiniSpark({ city }: { city: RuntimeGlobalWeatherCity }) {
  const hourly = (city.hourly || []).filter((point) => num(point.temp) !== null).slice(0, 12);
  const points = hourly.length >= 2 ? hourly : (city.bins || []).filter((bin) => num(bin.midPriceYes) !== null).slice(0, 12).map((bin, index) => ({ time: String(index), temp: num(bin.midPriceYes)! * 100 }));
  if (points.length < 2) return <span className="wm-weather-table-mini-empty">--</span>;
  const values = points.map((point) => num(point.temp) ?? 0);
  return <WeatherCanvasSparkline values={values} className="wm-weather-table-mini" />;
}

function TemperatureCard({
  city,
  selected,
  onSelectCity,
}: {
  city: RuntimeGlobalWeatherCity;
  selected: boolean;
  onSelectCity: (cityId: string) => void;
}) {
  const { shared, formatRelativeTime } = useSpecialistCopy('weather-shared');
  const top = bestBin(city);
  const unit = city.unit || top?.unit || '';
  const coverage = liveBookCoverage(city);
  const hasMarket = Boolean(city.marketUrl || top);
  const cityId = String(city.cityId || '');
  const selectCity = () => {
    if (cityId) onSelectCity(cityId);
  };
  return (
    <article
      className={`wm-temp-city-card ${cityTone(city)} ${selected ? 'selected' : ''}`.trim()}
      role="button"
      tabIndex={0}
      onClick={selectCity}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          selectCity();
        }
      }}
    >
      <div className="wm-temp-city-main">
        <div>
          <strong>{city.city || '--'}</strong>
          <span>{city.condition || shared('weatherUpdate', 'Weather update')} · {weatherSourceLabel(city)}</span>
        </div>
        <b>{tempLabel(currentTempValue(city), unit)}</b>
      </div>
      <MiniSpark city={city} />
      <div className="wm-temp-city-stats">
        <span><i>{shared('high', 'High')}</i>{tempLabel(highTempValue(city), unit)}</span>
        <span><i>{shared('low', 'Low')}</i>{tempLabel(city.todayLow ?? city.daily?.[0]?.low, unit)}</span>
        <span><i>{shared('updated', 'Updated')}</i>{formatRelativeTime(city.updatedAt || city.hourly?.[0]?.time || null)}</span>
      </div>
      {hasMarket ? (
        <div className="wm-temp-city-market">
          {city.marketUrl ? <a href={city.marketUrl} target="_blank" rel="noreferrer">Polymarket</a> : <span>{shared('market', 'Market')}</span>}
        <span>{top?.label || shared('quoteBins', 'Quote bins')}</span>
        <b>{priceLabel(top?.midPriceYes)}</b>
        <em>{coverage}</em>
      </div>
    ) : null}
  </article>
);
}

function TemperatureMonitorPanel({
  payload,
  selectedWeatherCityId,
  onSelectCity,
}: {
  payload?: RuntimeGlobalWeatherMapPayload | null;
  selectedWeatherCityId?: string | null;
  onSelectCity: (cityId: string | null) => void;
}) {
  const { copy } = useSpecialistCopy('global-temperature-monitor');
  const items = [...(payload?.items || [])].sort((a, b) => {
    return citySortValue(b) - citySortValue(a);
  });
  const selectedId = selectedWeatherCityId || items[0]?.cityId || null;
  return (
    <Panel
      title={copy('title', 'GLOBAL TEMP MONITOR')}
      badge={statusBadge(payload?.status)}
      status={panelStatus(payload?.status)}
      className="wm-market-panel wm-global-temperature-monitor-panel"
      dataPanelId="global-temperature-monitor"
    >
      <div className="wm-temp-city-list">
        {items.length ? items.map((city) => (
          <TemperatureCard
            key={String(city.cityId || city.city)}
            city={city}
            selected={String(city.cityId || '') === String(selectedId || '')}
            onSelectCity={onSelectCity}
          />
        )) : (
          <div className="wm-weather-table-empty">{copy('empty', 'Weather seed warming. Live city temperatures will appear automatically.')}</div>
        )}
      </div>
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'global-temperature-monitor': {
    render: (ctx) => (
      <TemperatureMonitorPanel
        payload={ctx.runtimeData['global-temperature-monitor'] as RuntimeGlobalWeatherMapPayload | undefined}
        selectedWeatherCityId={ctx.selectedWeatherCityId}
        onSelectCity={ctx.setSelectedWeatherCityId}
      />
    ),
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'global-temperature-monitor',
  title: 'Global Temp Monitor',
  eyebrow: 'weather',
  description: 'Live global city temperatures, forecast highs, and Polymarket quote coverage in a monitor table.',
  defaultEnabled: true,
}, {
  tier: 'slow',
  intervalMs: 60000,
  fetchData: () => fetchRuntimeGlobalTemperatureMonitor(60),
});
