import { cleanSignalSource, type WorldCupSignalItem } from '../components/SignalRow';
import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';

export function MatchTempoPanel({ xgSignals, tacticalSignals }: { xgSignals: WorldCupSignalItem[]; tacticalSignals: WorldCupSignalItem[] }) {
  const rows = [...xgSignals, ...tacticalSignals].slice(0, 6);
  return (
    <Panel title="MATCH TEMPO" count={rows.length} className="wm-worldcup-panel wm-worldcup-tempo-panel">
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
          detail="Tempo metrics need real event/team data. xG, shots, corners, cards, pace and press are not estimated from fixture names."
          rows={[{ source: 'Opta / StatsBomb / FBref / SofaScore', status: 'required', detail: 'xG, shots, corners, cards and pressure metrics' }]}
        />
      )}
    </Panel>
  );
}
