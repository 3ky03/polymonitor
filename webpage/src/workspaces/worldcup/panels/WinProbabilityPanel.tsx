import { useState } from 'preact/hooks';
import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupMatch, WorldCupOddsSnapshot, WorldCupPolymarketMarket } from '../types';

type WorldCupProbabilityRow = {
  team: string;
  poly: number | null;
  book: number | null;
  edge: number | null;
  volume: number;
};

type WorldCupBookmakerProbabilityRow = {
  key: string;
  title: string;
  lastUpdate: string;
  outcomes: Array<{
    team: string;
    probability: number | null;
    decimalOdds: number | null;
  }>;
};

function percentLabel(value?: number | null, digits = 1) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--';
  return `${Number(value).toFixed(digits)}%`;
}

function finalResultOutcome(match: WorldCupMatch | null) {
  if (!match || match.status !== 'finished' || match.homeScore == null || match.awayScore == null) return null;
  if (match.homeScore > match.awayScore) return match.homeTeam;
  if (match.homeScore < match.awayScore) return match.awayTeam;
  return 'Draw';
}

function percentFromUnknown(value?: number | string | null) {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return numeric >= 0 && numeric <= 1 ? numeric * 100 : numeric;
}

function outcomeMatchesTeam(name: string | undefined, team: string) {
  const left = String(name || '').trim().toLowerCase();
  const right = String(team || '').trim().toLowerCase();
  if (!left || !right) return false;
  if (team === 'Draw') return /draw|tie|\bx\b/i.test(left);
  if (left === right) return true;
  const leftTokens = new Set(left.split(/[^a-z0-9]+/).filter(Boolean));
  const rightTokens = right.split(/[^a-z0-9]+/).filter((part) => part.length > 1);
  return rightTokens.some((token) => leftTokens.has(token));
}

function snapshotOutcomePercent(snapshot: WorldCupOddsSnapshot | undefined, team: string) {
  if (!snapshot) return null;
  const direct = (snapshot.outcomes || []).find((outcome) => outcomeMatchesTeam(outcome.name, team));
  if (direct) return percentFromUnknown(direct.impliedProbability ?? direct.price ?? null);
  const probability = (snapshot.probabilities || []).find((row) => outcomeMatchesTeam(row.outcome || row.name, team));
  return percentFromUnknown(probability?.impliedProbability ?? probability?.price ?? null);
}

function buildBookmakerProbabilityRows(snapshot: WorldCupOddsSnapshot | undefined, match: WorldCupMatch | null): WorldCupBookmakerProbabilityRow[] {
  if (!snapshot || !match) return [];
  const teams = [match.homeTeam, 'Draw', match.awayTeam];
  return (snapshot.bookmakers || [])
    .map((bookmaker, index) => {
      const outcomes = teams.map((team) => {
        const outcome = (bookmaker.outcomes || []).find((row) => outcomeMatchesTeam(row.name, team));
        return {
          team,
          probability: percentFromUnknown(outcome?.impliedProbability ?? null),
          decimalOdds: outcome?.decimalOdds == null ? null : Number(outcome.decimalOdds),
        };
      });
      return {
        key: bookmaker.key || `${bookmaker.title}-${index}`,
        title: bookmaker.title || bookmaker.key || 'Book',
        lastUpdate: bookmaker.lastUpdate || snapshot.generatedAt || '',
        outcomes,
      };
    })
    .filter((row) => row.outcomes.some((outcome) => outcome.probability != null));
}

function compactTimeLabel(value: string) {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'UTC',
    timeZoneName: 'short',
  }).format(date);
}

function oddsSnapshotKey(snapshot: WorldCupOddsSnapshot) {
  return String(snapshot.marketKey || snapshot.marketType || 'book').toLowerCase();
}

function oddsMarketShortLabel(snapshot: WorldCupOddsSnapshot | { marketKey?: string; marketType?: string }) {
  const key = String(snapshot.marketKey || snapshot.marketType || '').toLowerCase();
  if (key === 'h2h' || key === 'moneyline') return '1X2';
  if (key === 'spreads' || key === 'spread') return 'AH';
  if (key === 'totals' || key === 'total_goals') return 'O/U';
  if (key === 'btts' || key === 'both_teams_to_score') return 'BTTS';
  if (key === 'draw_no_bet') return 'DNB';
  if (key === 'double_chance') return 'DC';
  return key ? key.toUpperCase().slice(0, 8) : 'BOOK';
}

function oddsMarketTitle(snapshot: WorldCupOddsSnapshot) {
  const label = oddsMarketShortLabel(snapshot);
  if (label === 'AH') return 'Asian handicap';
  if (label === 'O/U') return 'Goals total';
  if (label === '1X2') return 'Full-time result';
  if (label === 'BTTS') return 'Both teams score';
  if (label === 'DNB') return 'Draw no bet';
  if (label === 'DC') return 'Double chance';
  return label;
}

function lineLabel(value?: number | string | null) {
  if (value === null || value === undefined || value === '') return '--';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  const text = Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
  return numeric > 0 ? `+${text}` : text;
}

function oddsPriceLabel(value?: number | string | null) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return numeric.toFixed(2);
}

function latestBookUpdate(snapshot: WorldCupOddsSnapshot) {
  const values = (snapshot.bookmakers || [])
    .map((book) => book.lastUpdate)
    .filter(Boolean)
    .sort();
  return values.length ? compactTimeLabel(values[values.length - 1] || '') : compactTimeLabel(snapshot.generatedAt || '');
}

function BookmakerMicroBars({ snapshot }: { snapshot: WorldCupOddsSnapshot }) {
  const rows = (snapshot.outcomes || []).slice(0, 6);
  if (!rows.length) return null;
  return (
    <div className="wm-worldcup-linebook-bars" aria-hidden="true">
      {rows.map((row) => (
        <span key={`${row.name}-${row.point ?? 'x'}`}>
          <i style={{ width: row.impliedProbability == null ? '0%' : `${Math.max(2, Math.min(100, Number(row.impliedProbability)))}%` }} />
        </span>
      ))}
    </div>
  );
}

function BookmakerLineBook({ snapshot }: { snapshot: WorldCupOddsSnapshot }) {
  const bookmakerRows = (snapshot.bookmakers || []).slice(0, 8);
  return (
    <div className="wm-worldcup-linebook">
      <div className="wm-worldcup-linebook-hero">
        <span><em>{oddsMarketShortLabel(snapshot)}</em><strong>{oddsMarketTitle(snapshot)}</strong></span>
        <span><em>BOOKS</em><strong>{snapshot.bookmakerCount || bookmakerRows.length || '--'}</strong></span>
        <span><em>UPDATE</em><strong>{latestBookUpdate(snapshot)}</strong></span>
      </div>
      <BookmakerMicroBars snapshot={snapshot} />
      <div className="wm-worldcup-linebook-grid">
        {(snapshot.outcomes || []).slice(0, 8).map((outcome) => (
          <article key={`${outcome.name}-${outcome.point ?? 'line'}`}>
            <div>
              <span>{outcome.name}</span>
              <strong>{outcome.point == null ? oddsPriceLabel(outcome.decimalOdds) : lineLabel(outcome.point)}</strong>
            </div>
            <b>{outcome.point == null ? percentLabel(percentFromUnknown(outcome.impliedProbability)) : oddsPriceLabel(outcome.decimalOdds)}</b>
            <em>{outcome.bookCount || 0} books</em>
            <i style={{ width: outcome.impliedProbability == null ? '0%' : `${Math.max(2, Math.min(100, Number(outcome.impliedProbability)))}%` }} />
          </article>
        ))}
      </div>
      <div className="wm-worldcup-linebook-books">
        {bookmakerRows.map((book) => (
          <article key={`${snapshot.matchId}-${oddsSnapshotKey(snapshot)}-${book.key || book.title}`}>
            <span>{book.title}</span>
            <div>
              {(book.outcomes || []).slice(0, 3).map((outcome) => (
                <b key={`${book.title}-${outcome.name}-${outcome.point ?? 'p'}`}>
                  {outcome.name}{outcome.point == null ? '' : ` ${lineLabel(outcome.point)}`} <strong>{oddsPriceLabel(outcome.decimalOdds)}</strong>
                </b>
              ))}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

function buildWinProbabilityRows(markets: WorldCupPolymarketMarket[], odds: WorldCupOddsSnapshot[], match: WorldCupMatch | null): WorldCupProbabilityRow[] {
  if (!match) return [];
  const market = markets[0];
  const bookmakerSnapshot = odds.find((snapshot) => snapshot.providerType !== 'prediction_market');
  const predictionSnapshot = odds.find((snapshot) => snapshot.providerType === 'prediction_market' || /polymarket/i.test(snapshot.provider || snapshot.source || ''));
  const teams = [match.homeTeam, 'Draw', match.awayTeam];
  return teams.map((team) => {
    const marketOutcome = market?.outcomes.find((outcome) => outcome.name.toLowerCase() === team.toLowerCase() || (team === 'Draw' && /draw/i.test(outcome.name)));
    const poly = marketOutcome?.yesPrice == null
      ? snapshotOutcomePercent(predictionSnapshot, team)
      : percentFromUnknown(marketOutcome.yesPrice);
    const book = snapshotOutcomePercent(bookmakerSnapshot, team);
    if (poly == null && book == null) return null;
    return {
      team,
      poly,
      book,
      edge: poly != null && book != null ? poly - book : null,
      volume: market?.volume24h || 0,
    };
  }).filter((row): row is WorldCupProbabilityRow => Boolean(row));
}

export function WinProbabilityPanel({
  markets,
  odds,
  match,
}: {
  markets: WorldCupPolymarketMarket[];
  odds: WorldCupOddsSnapshot[];
  match: WorldCupMatch | null;
}) {
  const [activeTab, setActiveTab] = useState('edge');
  const settledOutcome = finalResultOutcome(match);
  if (settledOutcome && match) {
    const teams = [match.homeTeam, 'Draw', match.awayTeam];
    const scoreStatus = String((match as WorldCupMatch & { scoreStatus?: string }).scoreStatus || 'FT');
    const source = String((match as WorldCupMatch & { scoreSource?: string }).scoreSource || 'ESPN scoreboard');
    return (
      <Panel title="WIN PROBABILITY" count={3} className="wm-worldcup-panel wm-worldcup-win-probability-panel">
        <div className="wm-worldcup-odds-mode-tabs">
          <button className="active" type="button">FINAL</button>
        </div>
        <div className="wm-worldcup-prob-headline">
          <span><em>FINAL RESULT</em><strong>{match.homeScore}-{match.awayScore}</strong><b>{scoreStatus}</b></span>
          <span><em>SETTLED OUTCOME</em><strong className="green">{settledOutcome}</strong><b>{source}</b></span>
        </div>
        <div className="wm-worldcup-prob-table wm-worldcup-final-result-table">
          <header><span>OUTCOME</span><span>SCORE</span><span>RESULT</span><span>SOURCE</span></header>
          {teams.map((team) => {
            const score = team === match.homeTeam ? match.homeScore : team === match.awayTeam ? match.awayScore : `${match.homeScore}-${match.awayScore}`;
            const won = team === settledOutcome;
            return (
              <div className={won ? 'settled-winner' : 'source-missing'} key={team}>
                <strong>{team}</strong>
                <span><b>{score}</b><i style={{ width: won ? '100%' : '0%' }} /></span>
                <span><b>{won ? 'WIN' : '--'}</b><i style={{ width: won ? '100%' : '0%' }} /></span>
                <em className={won ? 'green' : ''}>{won ? '100%' : '--'}</em>
              </div>
            );
          })}
        </div>
        <div className="wm-worldcup-home-source-note">
          <b>BOOK SOURCE</b>
          <span>finished match; pre-match bookmaker odds are closed by source, result verified from {source}</span>
        </div>
      </Panel>
    );
  }
  const rows = buildWinProbabilityRows(markets, odds, match);
  const bookmakerSnapshots = odds.filter((snapshot) => snapshot.providerType !== 'prediction_market');
  const bookmakerSnapshot = bookmakerSnapshots.find((snapshot) => oddsSnapshotKey(snapshot) === 'h2h' || snapshot.marketType === 'moneyline') || bookmakerSnapshots[0];
  const bookmakerRows = buildBookmakerProbabilityRows(bookmakerSnapshot, match);
  const pricedRows = rows.filter((row) => row.poly != null);
  const probabilityRows = pricedRows.length ? pricedRows : rows.filter((row) => row.book != null);
  const leader = probabilityRows.length
    ? probabilityRows.reduce((best, row) => ((row.poly ?? row.book) || 0) > ((best.poly ?? best.book) || 0) ? row : best, probabilityRows[0]!)
    : null;
  const hasPolymarket = rows.some((row) => row.poly != null);
  const hasBook = rows.some((row) => row.book != null);
  return (
    <Panel title="WIN PROBABILITY" count={bookmakerSnapshot?.bookmakerCount || bookmakerRows.length || rows.length} className="wm-worldcup-panel wm-worldcup-win-probability-panel">
      {rows.length ? (
        <>
          <div className="wm-worldcup-odds-mode-tabs">
            <button className={activeTab === 'edge' ? 'active' : ''} type="button" onClick={() => setActiveTab('edge')}>EDGE</button>
            {bookmakerSnapshots.slice(0, 6).map((snapshot) => {
              const key = oddsSnapshotKey(snapshot);
              return (
                <button className={activeTab === key ? 'active' : ''} key={`${snapshot.matchId}-${key}`} type="button" onClick={() => setActiveTab(key)}>
                  {oddsMarketShortLabel(snapshot)}
                  <span>{snapshot.bookmakerCount || 0}</span>
                </button>
              );
            })}
          </div>
          {activeTab === 'edge' ? (
            <>
              <div className="wm-worldcup-prob-headline">
                <span><em>{hasPolymarket ? 'MARKET LEADER' : 'BOOK LEADER'}</em><strong>{leader?.team || '--'}</strong><b>{percentLabel(leader?.poly ?? leader?.book)}</b></span>
                <span><em>{hasPolymarket && hasBook ? 'EDGE' : 'SOURCE'}</em><strong className={(leader?.edge || 0) >= 0 ? 'green' : 'red'}>{leader?.edge == null ? (hasBook ? 'BOOK' : 'POLY') : `${leader.edge >= 0 ? '+' : ''}${percentLabel(leader.edge)}`}</strong><b>{hasPolymarket && hasBook ? 'poly-book' : 'real odds'}</b></span>
              </div>
              <div className="wm-worldcup-prob-table">
                <header><span>OUTCOME</span><span>POLY</span><span>BOOK</span><span>EDGE</span></header>
                {rows.map((row) => (
                  <div key={row.team}>
                    <strong>{row.team}</strong>
                    <span><b>{percentLabel(row.poly)}</b><i style={{ width: row.poly == null ? '0%' : `${row.poly}%` }} /></span>
                    <span><b>{percentLabel(row.book)}</b><i style={{ width: row.book == null ? '0%' : `${row.book}%` }} /></span>
                    <em className={(row.edge || 0) >= 0 ? 'green' : 'red'}>{row.edge == null ? '--' : `${row.edge >= 0 ? '+' : ''}${percentLabel(row.edge)}`}</em>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <BookmakerLineBook snapshot={bookmakerSnapshots.find((snapshot) => oddsSnapshotKey(snapshot) === activeTab) || bookmakerSnapshot!} />
          )}
        </>
      ) : (
        <SourceRequired
          detail="Win probabilities require real Polymarket outcome prices, bookmaker probabilities, or both. No hash/model fallback is rendered."
          rows={[
            { source: 'Polymarket local DB / Gamma', status: markets.length ? 'linked without prices' : 'not matched', detail: 'yesPrice per outcome' },
            { source: 'Bookmaker odds API', status: odds.length ? 'available' : 'not connected', detail: 'impliedProbability per outcome' },
          ]}
        />
      )}
    </Panel>
  );
}
