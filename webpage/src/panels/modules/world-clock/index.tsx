import { useEffect, useMemo, useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import type { RuntimeGlobalWeatherMapPayload } from '@/types';
import { buildWorldClockRows, CORE_WORLD_CLOCKS, normalizeTimezone, type WorldClockLocation } from '@/utils/worldClock';
import type { PanelRenderMap } from '../../types';
import { panelFromRenderer } from '../helpers';
import { useSpecialistCopy } from '@/services/specialist-i18n';

function selectedClockLocation(payload?: RuntimeGlobalWeatherMapPayload | null, selectedCityId?: string | null, fallback = 'Selected city'): WorldClockLocation | null {
  const city = (payload?.items || []).find((item) => item.cityId === selectedCityId);
  const timezone = normalizeTimezone(city?.timezone);
  if (!city || !timezone) return null;
  return {
    id: `selected-${city.cityId || city.city || timezone}`,
    city: String(city.city || fallback),
    venue: 'LOCAL',
    timezone,
    market: 'generic',
  };
}

function WorldClockPanel({
  payload,
  selectedCityId,
}: {
  payload?: RuntimeGlobalWeatherMapPayload | null;
  selectedCityId?: string | null;
}) {
  const { copy, shared } = useSpecialistCopy('world-clock');
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const locations = useMemo(() => {
    const selected = selectedClockLocation(payload, selectedCityId, shared('selectedCity', 'Selected city'));
    if (!selected || CORE_WORLD_CLOCKS.some((row) => row.timezone === selected.timezone)) return CORE_WORLD_CLOCKS;
    return [selected, ...CORE_WORLD_CLOCKS].slice(0, 6);
  }, [payload, selectedCityId, shared]);
  const rows = buildWorldClockRows(now, locations);
  return (
    <Panel
      title={copy('title', 'WORLD CLOCK')}
      badge="LIVE"
      status="live"
      count={rows.length}
      className="wm-market-panel wm-world-clock-panel"
      dataPanelId="world-clock"
    >
      <div className="wm-world-clock-list">
        {rows.map((row, index) => (
          <article key={row.id} className={`wm-world-clock-row ${row.home || index === 0 ? 'primary' : ''}`}>
            <div className="wm-world-clock-drag" aria-hidden="true">⋮</div>
            <div className="wm-world-clock-city">
              <strong>{row.id.startsWith('selected-') ? row.city : copy(`city.${row.id}`, row.city)}</strong>
              <span>{row.venue} <i className={row.open ? 'open' : ''} /> {row.open ? shared('open', 'OPEN') : shared('closedShort', 'CLSD')}</span>
            </div>
            <div className="wm-world-clock-time">
              <b>{row.time}</b>
              <span><em style={{ width: `${Math.round(row.progress * 100)}%` }} /> {copy(`weekday.${row.dayLabel.toLowerCase()}`, row.dayLabel)} {row.gmtLabel}</span>
            </div>
          </article>
        ))}
      </div>
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'world-clock': {
    render: (ctx) => (
      <WorldClockPanel
        payload={ctx.runtimeData['global-temperature-monitor'] as RuntimeGlobalWeatherMapPayload | undefined}
        selectedCityId={ctx.selectedWeatherCityId}
      />
    ),
  },
};

export const panel = panelFromRenderer(renderers, {
  id: 'world-clock',
  title: 'World Clock',
  eyebrow: 'time',
  description: 'Live market clocks for Shanghai, New York, London, and the selected weather city.',
  defaultEnabled: true,
});
