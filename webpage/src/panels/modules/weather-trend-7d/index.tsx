import { Panel } from '@/components/Panel';
import type { RuntimeGlobalWeatherMapPayload } from '@/types';
import type { PanelRenderMap } from '../../types';
import { panelFromRenderer } from '../helpers';
import { panelStatus, selectedWeatherCity, statusBadge } from '../weather-detail-utils';
import { sevenDayPoints, TrendChart } from '../weather-trend-detail';
import { useSpecialistCopy } from '@/services/specialist-i18n';

function WeatherTrend7dPanel({
  payload,
  selectedCityId,
}: {
  payload?: RuntimeGlobalWeatherMapPayload | null;
  selectedCityId?: string | null;
}) {
  const { copy } = useSpecialistCopy('weather-trend-7d');
  const city = selectedWeatherCity(payload, selectedCityId);
  return (
    <Panel
      title={copy('title', 'WU 7 DAY')}
      badge={statusBadge(payload?.status)}
      status={panelStatus(payload?.status)}
      className="wm-market-panel wm-weather-trend-detail-panel wm-weather-trend-single-panel"
      dataPanelId="weather-trend-7d"
    >
      {city ? (
        <TrendChart title={copy('chartTitle', 'WU 7 Day')} city={city} points={sevenDayPoints(city)} />
      ) : (
        <div className="wm-weather-detail-empty">{copy('empty', 'Select a city to inspect 7 day temperature trend.')}</div>
      )}
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'weather-trend-7d': {
    render: (ctx) => (
      <WeatherTrend7dPanel
        payload={ctx.runtimeData['global-temperature-monitor'] as RuntimeGlobalWeatherMapPayload | undefined}
        selectedCityId={ctx.selectedWeatherCityId}
      />
    ),
  },
};

export const panel = panelFromRenderer(renderers, {
  id: 'weather-trend-7d',
  title: 'WU 7 Day',
  eyebrow: 'weather',
  description: 'Selected city 7 day temperature trend chart.',
  defaultEnabled: true,
});
