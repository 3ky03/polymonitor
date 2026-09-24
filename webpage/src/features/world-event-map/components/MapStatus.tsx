import type { WorldEventSourceStatus } from '../data/sourceStatus';

export function MapStatus({ sources }: { sources: WorldEventSourceStatus[] }) {
  return (
    <span className="wm-map-source-statuses" aria-label="Map source status">
      {sources.map((source) => (
        <span
          className={`wm-map-source-status is-${source.status}`}
          key={source.key}
          title={source.message || `${source.label}: ${source.status}`}
        >
          <b>{source.label}</b>
          <em>{source.status.toUpperCase()}</em>
          {source.status !== 'loading' ? <small>{source.eventCount}</small> : null}
        </span>
      ))}
    </span>
  );
}
