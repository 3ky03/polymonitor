import { useMemo } from 'preact/hooks';
import type { GeoEvent } from '../domain/types';
import {
  AVIATION_LENS_MODES,
  AVIATION_RISK_SOURCES,
  type AviationLensMode,
  type AviationRiskSource,
  type WorldEventMapState,
} from '../state/mapState';
import { aviationLayerStatsForState } from '../renderer/layerFactories/aviationLayers';

const LENS_LABELS: Record<AviationLensMode, string> = {
  all: 'All',
  trunk: 'Trunk',
  watch: 'Watch',
};

const RISK_LABELS: Record<AviationRiskSource, string> = {
  all: 'All risk',
  weather: 'Weather',
  conflict: 'Conflict',
  corridor: 'Corridor',
};

export function AviationLens({
  events,
  state,
  onLensChange,
  onRiskSourceChange,
  onClose,
}: {
  events: GeoEvent[];
  state: Pick<WorldEventMapState, 'zoom' | 'aviationLens' | 'aviationRiskSource'>;
  onLensChange: (lens: AviationLensMode) => void;
  onRiskSourceChange: (source: AviationRiskSource) => void;
  onClose: () => void;
}) {
  const stats = useMemo(
    () => aviationLayerStatsForState(events, state),
    [events, state],
  );
  return (
    <aside className="wm-aviation-lens" aria-label="Aviation reference lens">
      <header>
        <div>
          <span>Air Lens · reference</span>
          <strong>{LENS_LABELS[state.aviationLens]} aviation</strong>
        </div>
        <button type="button" onClick={onClose} aria-label="Hide aviation layer">×</button>
      </header>
      <div className="wm-aviation-lens-stats" aria-label="Aviation reference counts">
        <span><i className="routes" /><b>{stats.visibleRoutes}</b><em>/{stats.routes} routes</em></span>
        <span><i className="hubs" /><b>{stats.visibleHubs}</b><em>/{stats.hubs} hubs</em></span>
        <span>
          <i className="flights" />
          <b>{stats.visibleFlights + stats.visibleLiveAircraft}</b>
          <em>/{stats.flights + stats.liveAircraft} aircraft</em>
        </span>
      </div>
      <div className="wm-aviation-lens-tabs" role="group" aria-label="Aviation route mode">
        {AVIATION_LENS_MODES.map((lens) => (
          <button
            type="button"
            key={lens}
            className={state.aviationLens === lens ? 'active' : ''}
            aria-pressed={state.aviationLens === lens}
            onClick={() => onLensChange(lens)}
          >
            {LENS_LABELS[lens]}
          </button>
        ))}
      </div>
      {state.aviationLens === 'watch' ? (
        <div className="wm-aviation-risk-tabs" role="group" aria-label="Aviation watch evidence">
          {AVIATION_RISK_SOURCES.map((source) => (
            <button
              type="button"
              key={source}
              className={state.aviationRiskSource === source ? 'active' : ''}
              aria-pressed={state.aviationRiskSource === source}
              onClick={() => onRiskSourceChange(source)}
            >
              <span>{RISK_LABELS[source]}</span>
              <b>{stats.riskSources[source]}</b>
            </button>
          ))}
        </div>
      ) : null}
      <p>
        {state.aviationLens === 'trunk'
          ? 'Trunk prioritizes classified corridors, then fills the bounded view with the highest-traffic routes. Route lines do not prove that flights are operating.'
          : 'Animated topology is contextual. A route line does not prove that a flight is operating.'}
      </p>
    </aside>
  );
}
