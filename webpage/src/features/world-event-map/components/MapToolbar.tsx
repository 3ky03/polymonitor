import {
  WORLD_EVENT_SEVERITIES,
  WORLD_EVENT_TIME_RANGES,
  WORLD_EVENT_BASEMAP_PROVIDERS,
  WORLD_EVENT_BASEMAP_THEMES,
  type WorldEventMapState,
  type WorldEventTimeRange,
  type WorldEventBasemapProvider,
  type WorldEventBasemapTheme,
} from '../state/mapState';
import type { GeoEventSeverity } from '../domain/types';

const SEVERITY_LABELS: Record<GeoEventSeverity, string> = {
  info: 'Info',
  watch: 'Watch',
  warning: 'Warning',
  critical: 'Critical',
};

export function MapToolbar({
  state,
  onTimeRangeChange,
  onSeveritiesChange,
  onBasemapProviderChange,
  onBasemapThemeChange,
  onClearCountry,
}: {
  state: Pick<WorldEventMapState, 'timeRange' | 'severities' | 'basemapProvider' | 'basemapTheme' | 'countryCode'>;
  onTimeRangeChange: (timeRange: WorldEventTimeRange) => void;
  onSeveritiesChange: (severities: GeoEventSeverity[]) => void;
  onBasemapProviderChange: (provider: WorldEventBasemapProvider) => void;
  onBasemapThemeChange: (theme: WorldEventBasemapTheme) => void;
  onClearCountry: () => void;
}) {
  const selected = new Set(state.severities);
  return (
    <div className="wm-world-event-map-toolbar" aria-label="World Event Map filters">
      <label>
        <span>Time</span>
        <select
          aria-label="Map time range"
          value={state.timeRange}
          onChange={(event) => onTimeRangeChange(
            (event.currentTarget as HTMLSelectElement).value as WorldEventTimeRange,
          )}
        >
          {WORLD_EVENT_TIME_RANGES.map((timeRange) => (
            <option value={timeRange} key={timeRange}>{timeRange === 'all' ? 'All time' : timeRange}</option>
          ))}
        </select>
      </label>
      <span className="wm-world-event-severity-label">Severity</span>
      <div className="wm-world-event-severity-filters">
        {WORLD_EVENT_SEVERITIES.map((severity) => (
          <button
            type="button"
            key={severity}
            className={`is-${severity} ${selected.has(severity) ? 'active' : ''}`}
            aria-pressed={selected.has(severity)}
            onClick={() => onSeveritiesChange(
              selected.has(severity)
                ? state.severities.filter((candidate) => candidate !== severity)
                : [...state.severities, severity],
            )}
          >
            {SEVERITY_LABELS[severity]}
          </button>
        ))}
      </div>
      <label className="wm-world-event-basemap-control">
        <span>Basemap</span>
        <select
          aria-label="Basemap provider"
          value={state.basemapProvider}
          onChange={(event) => onBasemapProviderChange(
            (event.currentTarget as HTMLSelectElement).value as WorldEventBasemapProvider,
          )}
        >
          {WORLD_EVENT_BASEMAP_PROVIDERS.map((provider) => (
            <option value={provider} key={provider}>{provider === 'auto' ? 'Auto' : provider.toUpperCase()}</option>
          ))}
        </select>
      </label>
      <label className="wm-world-event-basemap-control">
        <span>Theme</span>
        <select
          aria-label="Basemap theme"
          value={state.basemapTheme}
          onChange={(event) => onBasemapThemeChange(
            (event.currentTarget as HTMLSelectElement).value as WorldEventBasemapTheme,
          )}
        >
          {WORLD_EVENT_BASEMAP_THEMES.map((theme) => (
            <option value={theme} key={theme}>{theme === 'positron' ? 'Light' : 'Dark'}</option>
          ))}
        </select>
      </label>
      {state.countryCode ? (
        <button type="button" className="wm-world-event-country-filter" onClick={onClearCountry}>
          COUNTRY · {state.countryCode} ×
        </button>
      ) : null}
    </div>
  );
}
