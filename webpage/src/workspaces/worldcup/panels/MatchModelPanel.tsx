import { cleanSignalSource, type WorldCupSignalItem } from '../components/SignalRow';
import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';

export function MatchModelPanel({
  xgSignals,
  tacticalSignals,
}: {
  xgSignals: WorldCupSignalItem[];
  tacticalSignals: WorldCupSignalItem[];
}) {
  const rows = [...xgSignals, ...tacticalSignals].slice(0, 6);
  return (
    <Panel title="MATCH MODEL" count={rows.length} className="wm-worldcup-panel wm-worldcup-model-panel">
      {rows.length ? (
        <div className="wm-worldcup-model-table">
          {rows.map((item) => (
            <div key={item.id}>
              <span>{cleanSignalSource(item.source)}</span>
              <strong>{item.title}</strong>
              <em>{item.summary}</em>
            </div>
          ))}
        </div>
      ) : (
        <SourceRequired
          detail="xG, set-piece and transition metrics are disabled until a licensed statistical feed or model service supplies values."
          rows={[{ source: 'Opta / StatsBomb / FBref / SofaScore', status: 'required', detail: 'xG, xGA, shots, pressure and player events' }]}
        />
      )}
    </Panel>
  );
}
