import { Panel } from '@/components/Panel';
import type { PanelRenderContext } from '@/types';
import type { PanelRenderMap } from './types';
import { emptyState } from './shared/renderers';
import { useSpecialistCopy } from '@/services/specialist-i18n';

function formatProbability(value?: number | null) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--';
  return `${Number(value).toFixed(1)}%`;
}

function clampPercent(value?: number | null) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return 0;
  return Math.min(100, Math.max(0, Number(value)));
}

function formatNumber(value?: number | null, digits = 1) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--';
  return Number(value).toFixed(digits);
}

function formatMargin(value?: number | null) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--';
  const numeric = Number(value);
  const sign = numeric > 0 ? '+' : '';
  return `${sign}${numeric.toFixed(1)}`;
}

function nbaIntelPanel(ctx: PanelRenderContext, copy: ReturnType<typeof useSpecialistCopy>['copy'], shared: ReturnType<typeof useSpecialistCopy>['shared'], formatDateTime: ReturnType<typeof useSpecialistCopy>['formatDateTime']) {
  const intel = ctx.nbaIntel;
  if (!intel || (!intel.items.length && !intel.lineups.length)) {
    return emptyState(copy('empty', 'No NBA intel loaded.'));
  }
  return (
    <div className="wm-panel-stack">
      {!!intel.lineups.length && (
        <section className="wm-subpanel">
          <div className="wm-subpanel-title">{copy('lineups', 'LINEUPS')}</div>
          <div className="wm-panel-list">
            {intel.lineups.slice(0, 3).map((game, index) => (
              <article className="wm-lineup-card" key={`${game.gameId || game.label}-${index}`}>
                <div className="wm-lineup-head">
                  <strong>{game.label || copy('matchup', 'NBA matchup')}</strong>
                  <span>{game.status || '--'}</span>
                </div>
                <div className="wm-lineup-columns">
                  {['HOME', 'AWAY'].map((side) => (
                    <div className="wm-lineup-team" key={side}>
                      <div className="wm-lineup-team-label">{side === 'HOME' ? shared('home', 'HOME') : shared('away', 'AWAY')}</div>
                      <div className="wm-lineup-players">
                        {(game.starters || []).filter((player) => player.side === side).slice(0, 5).map((player, playerIndex) => (
                          <div className="wm-lineup-player" key={`${side}-${player.playerName}-${playerIndex}`}>
                            <span>{player.playerName || '--'}</span>
                            <em>{player.position || player.lineupStatus || ''}</em>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </article>
            ))}
          </div>
        </section>
      )}
      {!!intel.items.length && (
        <section className="wm-subpanel">
          <div className="wm-subpanel-title">{copy('beatIntel', 'ESPN / BEAT INTEL')}</div>
          <div className="wm-panel-list">
            {intel.items.slice(0, 8).map((item, index) => (
              <a className="wm-news-card" href={item.url || '#'} target="_blank" rel="noreferrer" key={`${item.url || item.headline}-${index}`}>
                <div className="wm-news-source">{item.source || 'ESPN'}</div>
                <div className="wm-news-title">{item.headline || copy('intelItem', 'NBA intel item')}</div>
                <div className="wm-news-meta">{formatDateTime(item.publishedAt || '')}</div>
              </a>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}


function nbaMatchupPredictorPanel(ctx: PanelRenderContext, copy: ReturnType<typeof useSpecialistCopy>['copy'], shared: ReturnType<typeof useSpecialistCopy>['shared'], formatDateTime: ReturnType<typeof useSpecialistCopy>['formatDateTime']) {
  const items = ctx.nbaMatchupPredictor?.items || [];
  if (!items.length) return emptyState(copy('empty', 'No ESPN Matchup Predictor data loaded.'));
  return (
    <div className="wm-matchup-predictor-list">
      {items.map((game, index) => {
        const awayWidth = clampPercent(game.awayWinProbability);
        const homeWidth = clampPercent(game.homeWinProbability);
        return (
          <article className="wm-score-card wm-matchup-card" key={`${game.eventId || game.shortName}-${index}`}>
            <div className="wm-score-card-head">
              <strong>{game.shortName || `${game.awayTeam || shared('awayTitle', 'Away')} @ ${game.homeTeam || shared('homeTitle', 'Home')}`}</strong>
              <span>{game.state || game.status || 'pre'}</span>
            </div>
            <div className="wm-score-card-meta">
              <span>{formatDateTime(game.tipoff || '')}</span>
              <span>{game.status || 'ESPN BPI'}</span>
            </div>
            <div className="wm-matchup-prob-stack">
              <div className="wm-matchup-prob-row">
                <div className="wm-matchup-prob-label">
                  <span>{game.awayTeam || shared('awayTitle', 'Away')}</span>
                  <strong>{formatProbability(game.awayWinProbability)}</strong>
                </div>
                <div className="wm-matchup-prob-track">
                  <div className="wm-matchup-prob-fill away" style={{ width: `${awayWidth}%` }} />
                </div>
              </div>
              <div className="wm-matchup-prob-row">
                <div className="wm-matchup-prob-label">
                  <span>{game.homeTeam || shared('homeTitle', 'Home')}</span>
                  <strong>{formatProbability(game.homeWinProbability)}</strong>
                </div>
                <div className="wm-matchup-prob-track">
                  <div className="wm-matchup-prob-fill home" style={{ width: `${homeWidth}%` }} />
                </div>
              </div>
            </div>
            <div className="wm-matchup-metrics">
              <div>
                <span>{copy('quality', 'QUALITY')}</span>
                <strong>{formatNumber(game.matchupQuality, 1)}</strong>
              </div>
              <div>
                <span>{copy('awayMargin', 'AWAY MARGIN')}</span>
                <strong>{formatMargin(game.projectedMargin)}</strong>
              </div>
              <div>
                <span>{copy('expected', 'EXPECTED')}</span>
                <strong>{formatNumber(game.awayExpectedPoints, 1)} - {formatNumber(game.homeExpectedPoints, 1)}</strong>
              </div>
            </div>
          </article>
        );
      })}
    </div>
  );
}


function nbaGames(items: NonNullable<PanelRenderContext['nba']>['items'], copy: ReturnType<typeof useSpecialistCopy>['copy'], shared: ReturnType<typeof useSpecialistCopy>['shared'], formatDateTime: ReturnType<typeof useSpecialistCopy>['formatDateTime']) {
  if (!items.length) return emptyState(copy('empty', 'No NBA games loaded.'));
  return (
    <div className="wm-scoreboard-list">
      {items.map((game) => (
        <article className="wm-score-card" key={game.id || game.name}>
          <div className="wm-score-card-head">
            <strong>{game.awayTeam} @ {game.homeTeam}</strong>
            <span>{game.state || 'pre'}</span>
          </div>
          <div className="wm-score-card-body">
            <div className="wm-score-team-row">
              <span>{game.awayTeam || shared('awayTitle', 'Away')}</span>
              <strong>{game.awayScore ?? '-'}</strong>
            </div>
            <div className="wm-score-team-row">
              <span>{game.homeTeam || shared('homeTitle', 'Home')}</span>
              <strong>{game.homeScore ?? '-'}</strong>
            </div>
          </div>
          <div className="wm-score-card-meta">
            <span>{formatDateTime(game.tipoff || '')}</span>
            <span>{game.status || game.broadcast || '--'}</span>
          </div>
        </article>
      ))}
    </div>
  );
}

function NbaScoreboardPanel({ ctx }: { ctx: PanelRenderContext }) {
  const { copy, shared, formatDateTime } = useSpecialistCopy('nba-scoreboard');
  return <Panel title={copy('title', 'NBA SCOREBOARD')} badge="SPORTS" status="live" count={ctx.nba?.items.length || 0} className="wm-market-panel wm-nba-scoreboard-panel" dataPanelId="nba-scoreboard">{nbaGames(ctx.nba?.items || [], copy, shared, formatDateTime)}</Panel>;
}

function NbaIntelPanel({ ctx }: { ctx: PanelRenderContext }) {
  const { copy, shared, formatDateTime } = useSpecialistCopy('nba-intel');
  return <Panel title={copy('title', 'NBA INTEL')} badge="ESPN" status="live" count={ctx.nbaIntel?.items.length || 0} className="wm-market-panel wm-nba-intel-panel" dataPanelId="nba-intel">{nbaIntelPanel(ctx, copy, shared, formatDateTime)}</Panel>;
}

function EspnPredictorPanel({ ctx }: { ctx: PanelRenderContext }) {
  const { copy, shared, formatDateTime } = useSpecialistCopy('espn-matchup-predictor');
  return <Panel title={copy('title', 'ESPN MATCHUP PREDICTOR')} badge="BPI" status="live" count={ctx.nbaMatchupPredictor?.items.length || 0} className="wm-market-panel wm-matchup-predictor-panel" dataPanelId="espn-matchup-predictor">{nbaMatchupPredictorPanel(ctx, copy, shared, formatDateTime)}</Panel>;
}


export const sportsPanelRenderers: PanelRenderMap = {
  'nba-scoreboard': {
    render: (ctx) => <NbaScoreboardPanel ctx={ctx} />,
  },
  'nba-intel': {
    size: 'wide',
    render: (ctx) => <NbaIntelPanel ctx={ctx} />,
  },
  'espn-matchup-predictor': {
    size: 'wide',
    render: (ctx) => <EspnPredictorPanel ctx={ctx} />,
  },
};
