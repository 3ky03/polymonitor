import { SignalRow, type WorldCupSignalItem } from '../components/SignalRow';
import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';

export function LineupBoardPanel({
  lineups,
  squadSignals,
}: {
  lineups: WorldCupSignalItem[];
  squadSignals: WorldCupSignalItem[];
}) {
  return (
    <Panel title="LINEUP BOARD" count={lineups.length} className="wm-worldcup-panel wm-worldcup-lineup-board-panel">
      {lineups.length || squadSignals.length ? (
        <div className="wm-worldcup-mini-feed wm-worldcup-mini-feed-compact">
          {[...lineups.slice(0, 4), ...squadSignals.slice(0, 2)].map((item) => <SignalRow item={item} key={item.id} />)}
        </div>
      ) : (
        <SourceRequired
          detail="Predicted XI and confirmed lineup cards require Flashscore/SofaScore/FotMob or official team feeds. Formation boards are not fabricated."
          rows={[
            { source: 'Official team sheets', status: 'required', detail: 'T-60 confirmed XI' },
            { source: 'Flashscore / SofaScore / FotMob', status: 'required', detail: 'predicted XI and formation feed' },
          ]}
        />
      )}
    </Panel>
  );
}
