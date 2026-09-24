import { useMemo } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import type { RuntimeGlobalWeatherMapPayload, RuntimeWeatherQuoteBin } from '@/types';
import type { PanelRenderMap } from '../../types';
import { panelFromRenderer } from '../helpers';
import { bookMidPrice, panelStatus, selectedWeatherCity, statusBadge, useLiveWeatherQuoteBins } from '../weather-detail-utils';
import { numericTime, WeatherLiveChart, type WeatherLiveChartSeries } from '../weather-live-chart';
import { useSpecialistCopy } from '@/services/specialist-i18n';

function percentAxisLabel(value: number) {
  return `${Math.round(value * 10) / 10}%`;
}

function QuoteCurve({ bins, cityName }: { bins: RuntimeWeatherQuoteBin[]; cityName?: string | null }) {
  const { copy, shared } = useSpecialistCopy('weather-quote-detail');
  const values = bins.map((bin) => bookMidPrice(bin));
  const hasBookQuote = values.some((value) => value !== null);
  const hasLastOnly = bins.some((bin) => bookMidPrice(bin) === null && bin.midPriceYes !== null);
  const chartSeries = useMemo<WeatherLiveChartSeries[]>(() => [{
    id: 'book-mid',
    type: 'area',
    color: '#ff9900',
    topColor: 'rgba(255, 153, 0, 0.36)',
    bottomColor: 'rgba(255, 153, 0, 0.02)',
    data: values
      .map((value, index) => value === null ? null : ({
        time: numericTime(index + 1),
        value: Math.max(0, Math.min(100, value * 100)),
      }))
      .filter((point): point is { time: ReturnType<typeof numericTime>; value: number } => Boolean(point)),
  }], [values]);
  return (
    <div className="wm-weather-quote-curve-panel">
      <div className="wm-weather-chart-title">
        <strong>{copy('curveTitle', '{city} Book Price Curve', { city: cityName || shared('selectedCity', 'Selected city') })}</strong>
        <span>{shared('yesBidAskMid', 'YES Bid/Ask Mid %')}</span>
      </div>
      {hasBookQuote ? (
        <WeatherLiveChart
          className="wm-weather-quote-curve-large"
          series={chartSeries}
          showTimeScale={false}
          valueFormatter={percentAxisLabel}
        />
      ) : (
        <div className="wm-weather-detail-empty-line wm-weather-quote-curve-large">{copy('noBookMid', 'No two-sided CLOB book mid for this market.')}</div>
      )}
      <div className="wm-weather-quote-history-strip">
        <button type="button">{copy('playHistory', 'Play History')}</button>
        <span className="muted">{shared('hoursAgo', '{count}h ago', { count: 24 })}</span>
        <span className="purple">{shared('hoursAgo', '{count}h ago', { count: 12 })}</span>
        <span className="green">{shared('hoursAgo', '{count}h ago', { count: 6 })}</span>
        <span className="cyan">{shared('hoursAgo', '{count}h ago', { count: 1 })}</span>
        <span className="yellow">{shared('minutesAgo', '{count}m ago', { count: 30 })}</span>
      </div>
      {hasLastOnly ? <p>{copy('lastOnlyNote', 'LAST and one-sided book quotes stay in the table but are not plotted as live bid/ask mid.')}</p> : null}
    </div>
  );
}

function WeatherQuoteDetailPanel({
  payload,
  selectedCityId,
}: {
  payload?: RuntimeGlobalWeatherMapPayload | null;
  selectedCityId?: string | null;
}) {
  const { copy } = useSpecialistCopy('weather-quote-detail');
  const city = selectedWeatherCity(payload, selectedCityId);
  const { bins, loading } = useLiveWeatherQuoteBins(city);
  return (
    <Panel
      title={copy('title', 'WEATHER QUOTE CURVE')}
      badge={loading ? 'BOOK' : statusBadge(payload?.status)}
      status={panelStatus(payload?.status)}
      className="wm-market-panel wm-weather-quote-detail-panel wm-weather-quote-curve-only-panel"
      dataPanelId="weather-quote-detail"
    >
      {city ? (
        <QuoteCurve bins={bins} cityName={city.city} />
      ) : (
        <div className="wm-weather-detail-empty">{copy('empty', 'Select a city to inspect quote bins.')}</div>
      )}
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'weather-quote-detail': {
    render: (ctx) => (
      <WeatherQuoteDetailPanel
        payload={ctx.runtimeData['global-temperature-monitor'] as RuntimeGlobalWeatherMapPayload | undefined}
        selectedCityId={ctx.selectedWeatherCityId}
      />
    ),
  },
};

export const panel = panelFromRenderer(renderers, {
  id: 'weather-quote-detail',
  title: 'Weather Quote Curve',
  eyebrow: 'weather',
  description: 'Selected city Polymarket temperature bin mid price curve.',
  defaultEnabled: true,
});
