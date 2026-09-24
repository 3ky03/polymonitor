import { useMemo } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import type { RuntimeGlobalWeatherCity, RuntimeGlobalWeatherMapPayload } from '@/types';
import type { PanelRenderMap } from '../../types';
import { panelFromRenderer } from '../helpers';
import { forecastSourceLabel, num, panelStatus, selectedWeatherCity, statusBadge, tempLabel } from '../weather-detail-utils';
import { numericTime, WeatherLiveChart, type WeatherLiveChartSeries } from '../weather-live-chart';
import { useSpecialistCopy } from '@/services/specialist-i18n';

type TrendPoint = {
  label: string;
  time: number;
  avg: number;
  high: number;
};

function movingAverage(values: number[], index: number) {
  const start = Math.max(0, index - 2);
  const slice = values.slice(start, index + 1);
  return slice.reduce((sum, value) => sum + value, 0) / Math.max(1, slice.length);
}

function oneDayPoints(city?: RuntimeGlobalWeatherCity | null): TrendPoint[] {
  const hourly = (city?.hourly || [])
    .filter((point) => num(point.temp) !== null)
    .slice(0, 24);
  const values = hourly.map((point) => num(point.temp) || 0);
  return hourly.map((point, index) => {
    const value = num(point.temp) || 0;
    const date = String(point.time || '');
    const parsed = Date.parse(date);
    return {
      label: date.slice(11, 16) || date.slice(5, 10) || '--',
      time: Number.isFinite(parsed) ? Math.floor(parsed / 1000) : index + 1,
      avg: movingAverage(values, index),
      high: value,
    };
  }).sort((left, right) => left.time - right.time);
}

function sevenDayPoints(city?: RuntimeGlobalWeatherCity | null): TrendPoint[] {
  const days = (city?.daily || [])
    .filter((point) => num(point.high) !== null || num(point.low) !== null)
    .slice(0, 7);
  return days.map((day, index) => {
    const high = num(day.high) ?? num(day.low) ?? 0;
    const low = num(day.low) ?? high;
    const avg = (high + low) / 2;
    const label = String(day.date || '').slice(5) || '--';
    const parsed = Date.parse(`${day.date}T00:00:00Z`);
    return { label, time: Number.isFinite(parsed) ? Math.floor(parsed / 1000) : index + 1, avg, high };
  }).sort((left, right) => left.time - right.time);
}

function TrendChart({
  title,
  city,
  points,
}: {
  title: string;
  city?: RuntimeGlobalWeatherCity | null;
  points: TrendPoint[];
}) {
  const { shared } = useSpecialistCopy('weather-trend-detail');
  const unit = city?.unit || '';
  const chartSeries = useMemo<WeatherLiveChartSeries[]>(() => {
    return [
      {
        id: `${title}-avg`,
        type: 'line',
        color: '#ff9900',
        data: points.map((point, index) => ({ time: numericTime(point.time || index + 1), value: point.avg })),
      },
      {
        id: `${title}-high`,
        type: 'line',
        color: '#7edcff',
        data: points.map((point, index) => ({ time: numericTime(point.time || index + 1), value: point.high })),
      },
    ];
  }, [points, title]);
  if (points.length < 2) {
    return (
      <section className="wm-weather-trend-card">
        <div className="wm-weather-trend-title"><strong>{title}</strong><span>{shared('average', 'Avg')}</span><span>{shared('high', 'High')}</span></div>
        <div className="wm-weather-detail-empty-line">{shared('noTrendData', 'No trend data')}</div>
      </section>
    );
  }
  return (
    <section className="wm-weather-trend-card">
      <div className="wm-weather-trend-title">
        <strong>{title}</strong>
        <span className="source">{forecastSourceLabel(city)}</span>
        <span className="avg">{shared('average', 'Avg')}</span>
        <span className="high">{shared('high', 'High')}</span>
      </div>
      <WeatherLiveChart
        className="wm-weather-trend-chart"
        series={chartSeries}
        valueFormatter={(value) => tempLabel(value, unit)}
      />
    </section>
  );
}

function WeatherTrendDetailPanel({
  payload,
  selectedCityId,
}: {
  payload?: RuntimeGlobalWeatherMapPayload | null;
  selectedCityId?: string | null;
}) {
  const { copy } = useSpecialistCopy('weather-trend-detail');
  const city = selectedWeatherCity(payload, selectedCityId);
  return (
    <Panel
      title={copy('title', 'WU 1 DAY')}
      badge={statusBadge(payload?.status)}
      status={panelStatus(payload?.status)}
      className="wm-market-panel wm-weather-trend-detail-panel wm-weather-trend-single-panel"
      dataPanelId="weather-trend-detail"
    >
      {city ? (
        <TrendChart title={copy('chartTitle', 'WU 1 Day')} city={city} points={oneDayPoints(city)} />
      ) : (
        <div className="wm-weather-detail-empty">{copy('empty', 'Select a city to inspect temperature trend.')}</div>
      )}
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'weather-trend-detail': {
    render: (ctx) => (
      <WeatherTrendDetailPanel
        payload={ctx.runtimeData['global-temperature-monitor'] as RuntimeGlobalWeatherMapPayload | undefined}
        selectedCityId={ctx.selectedWeatherCityId}
      />
    ),
  },
};

export const panel = panelFromRenderer(renderers, {
  id: 'weather-trend-detail',
  title: 'WU 1 Day',
  eyebrow: 'weather',
  description: 'Selected city 1D temperature trend chart.',
  defaultEnabled: true,
});

export { sevenDayPoints, TrendChart };
