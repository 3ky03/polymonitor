import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { MobileWorkspaceNav } from '@/components/MobileWorkspaceNav';
import {
  MetricCard,
  operationalTone,
  StatusBadge,
  type OperationalTone,
} from '@/components/design-system/StatusPrimitives';
import { shortHash, signedClass } from '@/panels/shared/formatters';
import { fetchMarketLobByToken, fetchWorkspaceBundle } from '@/services/api';
import { fetchAuthSession } from '@/services/auth';
import { useI18n } from '@/services/i18n';
import { addWatchlistMarket } from '@/services/product';
import type {
  ChartPoint,
  ContentItem,
  L2Level,
  LobPayload,
  MarketEvidenceClaim,
  MarketGroupOutcome,
  OracleEvent,
  TradeRow,
  WorkspaceBundle,
} from '@/types';

const REFRESH_INTERVAL_MS = 30_000;
const MAX_VISIBLE_TRADES = 16;
const MAX_VISIBLE_CONTENT = 8;
const MAX_BOOK_LEVELS = 8;

type BookSide = 'yes' | 'no';
type Translator = ReturnType<typeof useI18n>['t'];
type NumberFormatter = ReturnType<typeof useI18n>['formatNumber'];
type PercentFormatter = ReturnType<typeof useI18n>['formatPercent'];

function readMarketId(): number | null {
  if (typeof window === 'undefined') return null;
  const match = window.location.pathname.match(/^\/markets\/(\d+)(?:\/|$)/);
  const marketId = Number(match?.[1]);
  return Number.isSafeInteger(marketId) && marketId > 0 ? marketId : null;
}

function parseTimestamp(value?: string | null): number | null {
  const parsed = Date.parse(String(value || ''));
  return Number.isFinite(parsed) ? parsed : null;
}

function ageSeconds(value?: string | null): number | null {
  const parsed = parseTimestamp(value);
  return parsed == null ? null : Math.max(0, Math.round((Date.now() - parsed) / 1_000));
}

function freshnessFromTimestamp(value?: string | null, staleAfterSeconds = 300): string {
  const age = ageSeconds(value);
  if (age == null) return 'unknown';
  if (age <= staleAfterSeconds) return 'fresh';
  if (age <= staleAfterSeconds * 6) return 'aging';
  return 'stale';
}

function statusTone(status?: string | null): OperationalTone {
  const normalized = String(status || '').toLowerCase();
  if (normalized.includes('mismatch') || normalized.includes('missing') || normalized === 'error') return 'critical';
  if (
    normalized.includes('partial')
    || normalized.includes('stale')
    || normalized.includes('aging')
    || normalized.includes('snapshot')
    || normalized.includes('open-no-events')
    || normalized.includes('single-market')
    || normalized.includes('not-loaded')
  ) return 'warning';
  return operationalTone(normalized);
}

function formatProbabilityCents(value?: string | number | null): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return `${Math.round(numeric * 100)}¢`;
}

function statusLabel(value: string | null | undefined, t: Translator): string {
  const normalized = String(value || 'unknown').trim().toLowerCase().replace(/_/g, '-');
  const known = {
    loading: 'status.loading',
    unknown: 'status.unknown',
    fresh: 'status.fresh',
    aging: 'status.aging',
    stale: 'status.stale',
    ok: 'status.ok',
    missing: 'status.missing',
    partial: 'status.partial',
    critical: 'status.critical',
    degraded: 'status.degraded',
    warning: 'status.warning',
    ready: 'status.ready',
    observed: 'status.observed',
    bound: 'status.bound',
    unbound: 'status.unbound',
    pending: 'status.pending',
    open: 'status.open',
    closed: 'status.closed',
    proposed: 'status.proposed',
    disputed: 'status.disputed',
    resolved: 'status.resolved',
    error: 'status.error',
    snapshot: 'status.snapshot',
    'single-market': 'status.singleMarket',
    'open-no-events': 'status.openNoEvents',
    'not-loaded': 'status.notLoaded',
    'ended-awaiting-oracle': 'status.endedAwaitingOracle',
  } as const;
  const key = known[normalized as keyof typeof known];
  return key ? t(key) : cleanSource(value);
}

function formatShares(value: string | number | null | undefined, formatNumber: NumberFormatter): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return formatNumber(numeric, {
    maximumFractionDigits: numeric >= 100 ? 0 : 2,
  });
}

function formatNotional(
  price: string | number | null | undefined,
  size: string | number | null | undefined,
  formatNumber: NumberFormatter,
): string {
  const numericPrice = Number(price);
  const numericSize = Number(size);
  if (!Number.isFinite(numericPrice) || !Number.isFinite(numericSize)) return '--';
  return formatNumber(numericPrice * numericSize, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: numericPrice * numericSize >= 100 ? 0 : 2,
  });
}

function formatMarketPercent(value: string | number | null | undefined, formatPercent: PercentFormatter): string {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? formatPercent(numeric) : '--';
}

function formatMarketSignedPercent(value: string | number | null | undefined, formatPercent: PercentFormatter): string {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? formatPercent(numeric, { signDisplay: numeric === 0 ? 'auto' : 'always' })
    : '--';
}

function formatMarketCompact(value: string | number | null | undefined, formatNumber: NumberFormatter): string {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? formatNumber(numeric, { notation: 'compact', maximumFractionDigits: 1 })
    : '--';
}

function formatMarketCurrency(value: string | number | null | undefined, formatNumber: NumberFormatter): string {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? formatNumber(numeric, {
      notation: 'compact',
      style: 'currency',
      currency: 'USD',
      maximumFractionDigits: 1,
    })
    : '--';
}

function cleanSource(value?: string | null): string {
  return String(value || 'unknown')
    .replace(/[_-]/g, ' ')
    .replace(/\b\w/g, (character: string) => character.toUpperCase());
}

function selectedOutcome(bundle: WorkspaceBundle | null): MarketGroupOutcome | null {
  if (!bundle) return null;
  if (bundle.selectedOutcome) return bundle.selectedOutcome;
  const marketId = bundle.market?.id || bundle.identity?.localMarketId;
  return (bundle.group?.outcomes || []).find((outcome) => Number(outcome.marketId) === Number(marketId)) || null;
}

function probabilityPoint(point: ChartPoint): number | null {
  const value = Number(point.yesPrice ?? point.value);
  return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : null;
}

function chartGeometry(points: ChartPoint[]) {
  const clean = points
    .map((point, index) => ({ index, value: probabilityPoint(point) }))
    .filter((point): point is { index: number; value: number } => point.value != null);
  if (!clean.length) return null;
  const width = 760;
  const height = 250;
  const left = 14;
  const right = 14;
  const top = 18;
  const bottom = 24;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const project = (point: { index: number; value: number }) => {
    const x = left + (point.index / Math.max(points.length - 1, 1)) * plotWidth;
    const y = top + (1 - point.value) * plotHeight;
    return { x, y };
  };
  const projected = clean.map(project);
  const linePath = projected.map((point, index) => `${index ? 'L' : 'M'}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(' ');
  const first = projected[0];
  const last = projected[projected.length - 1];
  const areaPath = first && last
    ? `${linePath} L${last.x.toFixed(2)},${(height - bottom).toFixed(2)} L${first.x.toFixed(2)},${(height - bottom).toFixed(2)} Z`
    : '';
  return {
    width,
    height,
    linePath,
    areaPath,
    last,
    latest: clean[clean.length - 1]?.value ?? null,
  };
}

function ProbabilityChart({ bundle }: { bundle: WorkspaceBundle }) {
  const { t, formatDateTime, formatPercent } = useI18n();
  const points = bundle.chart?.points || [];
  const geometry = chartGeometry(points);
  if (!geometry || points.length < 2) {
    return (
      <div className="market-empty-state">
        <strong>{t('market.noProbabilityCurve')}</strong>
        <span>{t('market.noProbabilityCurveDetail')}</span>
      </div>
    );
  }
  const firstTimestamp = points.find((point) => point.timestamp)?.timestamp || null;
  const lastTimestamp = [...points].reverse().find((point) => point.timestamp)?.timestamp || null;
  return (
    <div className="market-chart-shell">
      <div className="market-chart-meta">
        <span>{cleanSource(bundle.chart?.priceSource || 'market history')}</span>
        <span>{t('market.observations', { count: points.length })}</span>
        <span>{bundle.chart?.range || '--'} / {bundle.chart?.interval || '--'}</span>
      </div>
      <svg
        className="market-probability-chart"
        viewBox={`0 0 ${geometry.width} ${geometry.height}`}
        role="img"
        aria-label={t('market.chartAria', { count: points.length })}
      >
        <defs>
          <linearGradient id="marketProbabilityArea" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stop-color="rgba(57, 255, 115, 0.28)" />
            <stop offset="100%" stop-color="rgba(57, 255, 115, 0)" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75].map((value) => {
          const y = 18 + (1 - value) * (geometry.height - 42);
          return (
            <g key={value}>
              <line x1="14" x2={geometry.width - 14} y1={y} y2={y} className="market-chart-gridline" />
              <text x={geometry.width - 18} y={y - 5} className="market-chart-grid-label">{Math.round(value * 100)}%</text>
            </g>
          );
        })}
        <path d={geometry.areaPath} fill="url(#marketProbabilityArea)" />
        <path d={geometry.linePath} className="market-chart-line" />
        {geometry.last ? <circle cx={geometry.last.x} cy={geometry.last.y} r="5" className="market-chart-endpoint" /> : null}
      </svg>
      <div className="market-chart-axis">
        <span>{firstTimestamp ? formatDateTime(firstTimestamp) : '—'}</span>
        <strong>{geometry.latest == null ? '--' : formatPercent(geometry.latest)} YES</strong>
        <span>{lastTimestamp ? formatDateTime(lastTimestamp) : '—'}</span>
      </div>
    </div>
  );
}

function topLevels(levels?: L2Level[], side: 'bid' | 'ask' = 'bid') {
  return [...(levels || [])]
    .sort((left, right) => (
      side === 'bid'
        ? Number(right.price || 0) - Number(left.price || 0)
        : Number(left.price || 0) - Number(right.price || 0)
    ))
    .slice(0, MAX_BOOK_LEVELS);
}

function BookTable({ title, levels, side }: { title: string; levels?: L2Level[]; side: 'bid' | 'ask' }) {
  const { t, formatNumber } = useI18n();
  const visible = topLevels(levels, side);
  return (
    <div className={`market-book-side is-${side}`}>
      <div className="market-book-side-title">
        <strong>{title}</strong>
        <span>{t('market.levels', { count: visible.length })}</span>
      </div>
      <div className="market-table market-book-table" role="table">
        <div className="market-table-head" role="row">
          <span>{t('market.price')}</span><span>{t('market.shares')}</span><span>{t('market.notional')}</span>
        </div>
        {visible.map((level, index) => (
          <div className="market-table-row" role="row" key={`${side}-${index}-${level.price}-${level.size}`}>
            <strong>{formatProbabilityCents(level.price)}</strong>
            <span>{formatShares(level.size, formatNumber)}</span>
            <span>{formatNotional(level.price, level.size, formatNumber)}</span>
          </div>
        ))}
        {!visible.length ? <div className="market-table-empty">{t('market.noBookLevels', { side: side === 'bid' ? t('market.bidSide') : t('market.askSide') })}</div> : null}
      </div>
    </div>
  );
}

function OrderBook({ lob }: { lob?: LobPayload | null }) {
  const { t, formatRelativeTime } = useI18n();
  const [side, setSide] = useState<BookSide>('yes');
  const active = side === 'yes' ? lob?.yes : lob?.no;
  return (
    <section className="market-card market-orderbook-card">
      <div className="market-section-heading">
        <div>
          <span>{t('market.executionEvidence')}</span>
          <h2>{t('market.liveOrderBook')}</h2>
        </div>
        <div className="market-segmented-control" role="tablist" aria-label={t('market.orderBookOutcome')}>
          <button type="button" className={side === 'yes' ? 'active' : ''} onClick={() => setSide('yes')}>YES</button>
          <button type="button" className={side === 'no' ? 'active' : ''} onClick={() => setSide('no')}>NO</button>
        </div>
      </div>
      <div className="market-quote-strip">
        <span><em>{t('market.bestBid')}</em><strong>{formatProbabilityCents(active?.bestBid)}</strong></span>
        <span><em>{t('market.bestAsk')}</em><strong>{formatProbabilityCents(active?.bestAsk)}</strong></span>
        <span><em>{t('market.spread')}</em><strong>{formatProbabilityCents(active?.spread)}</strong></span>
        <span><em>{t('market.observed')}</em><strong>{formatRelativeTime(lob?.fetchedAt)}</strong></span>
      </div>
      <div className="market-book-grid">
        <BookTable title={t('market.bids', { outcome: side.toUpperCase() })} levels={active?.bids} side="bid" />
        <BookTable title={t('market.asks', { outcome: side.toUpperCase() })} levels={active?.asks} side="ask" />
      </div>
      {!lob ? (
        <div className="market-inline-notice">
          {t('market.noClob')}
        </div>
      ) : null}
    </section>
  );
}

function evidenceClaims(bundle: WorkspaceBundle): MarketEvidenceClaim[] {
  const claims = [...(bundle.evidence?.claims || [])];
  const contentItems = bundle.content?.items || [];
  const latestContent = contentItems
    .map((item) => item.publishedAt)
    .filter((value): value is string => Boolean(value))
    .sort()
    .reverse()[0] || null;
  claims.push({
    id: 'lob',
    label: 'CLOB liquidity',
    status: bundle.lob?.bookStatus || (bundle.lob ? 'ok' : 'missing'),
    source: bundle.lob?.source || 'clob-book',
    observedAt: bundle.lob?.fetchedAt || null,
    recordCount: (bundle.lob?.yes?.bids?.length || 0)
      + (bundle.lob?.yes?.asks?.length || 0)
      + (bundle.lob?.no?.bids?.length || 0)
      + (bundle.lob?.no?.asks?.length || 0),
    detail: 'Best bid, best ask and visible depth levels',
  });
  claims.push({
    id: 'content',
    label: 'Linked intelligence',
    status: contentItems.length ? 'ok' : 'missing',
    source: bundle.content?.sourceMode || 'content-index',
    observedAt: latestContent,
    recordCount: contentItems.length,
    detail: 'Market-linked reporting and primary-source context',
  });
  return claims;
}

function localizedClaim(claim: MarketEvidenceClaim, t: Translator): Pick<MarketEvidenceClaim, 'label' | 'detail'> {
  switch (claim.id) {
    case 'identity':
      return { label: t('market.claim.identity'), detail: t('market.claim.identityDetail') };
    case 'price':
      return { label: t('market.claim.price'), detail: t('market.claim.priceDetail') };
    case 'history':
      return { label: t('market.claim.history'), detail: claim.detail };
    case 'trades':
      return { label: t('market.claim.trades'), detail: t('market.claim.tradesDetail') };
    case 'oracle':
      return { label: t('market.claim.oracle'), detail: t('market.claim.oracleDetail') };
    case 'group':
      return { label: t('market.claim.group'), detail: t('market.claim.groupDetail') };
    case 'lob':
      return { label: t('market.claim.lob'), detail: t('market.claim.lobDetail') };
    case 'content':
      return { label: t('market.claim.content'), detail: t('market.claim.contentDetail') };
    default:
      return { label: claim.label, detail: claim.detail };
  }
}

function EvidenceChain({ bundle }: { bundle: WorkspaceBundle }) {
  const { t, formatNumber, formatRelativeTime } = useI18n();
  const claims = evidenceClaims(bundle);
  return (
    <section className="market-card market-evidence-card">
      <div className="market-section-heading">
        <div>
          <span>{t('market.auditTrail')}</span>
          <h2>{t('market.evidenceChain')}</h2>
        </div>
        <StatusBadge
          label={bundle.evidence?.contractVersion || t('market.clientDerived')}
          tone={bundle.evidence ? 'positive' : 'warning'}
          compact
        />
      </div>
      <p className="market-section-intro">
        {t('market.evidenceIntro')}
      </p>
      <div className="market-evidence-list">
        {claims.map((claim, index) => {
          const localized = localizedClaim(claim, t);
          return (
            <article className="market-evidence-row" key={`${claim.id}-${index}`}>
              <span className="market-evidence-step">{String(index + 1).padStart(2, '0')}</span>
              <div className="market-evidence-copy">
                <strong>{localized.label}</strong>
                <span>{localized.detail || t('market.noSourceDetail')}</span>
                <em>{cleanSource(claim.source)} · {formatRelativeTime(claim.observedAt)}</em>
              </div>
              <div className="market-evidence-result">
                <StatusBadge label={statusLabel(claim.status, t)} tone={statusTone(claim.status)} compact />
                <strong>{claim.recordCount == null ? '--' : formatNumber(claim.recordCount)}</strong>
                <span>{t('market.records')}</span>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function IdentifierRow({ label, value }: { label: string; value?: string | number | null }) {
  const { t } = useI18n();
  const stringValue = String(value || '');
  return (
    <div className="market-identifier-row">
      <span>{label}</span>
      <code title={stringValue || undefined}>{stringValue ? shortHash(stringValue, 14, 8) : '--'}</code>
      {stringValue ? (
        <button
          type="button"
          onClick={() => void navigator.clipboard.writeText(stringValue)}
          aria-label={t('market.copyIdentifier', { label })}
        >
          {t('market.copy')}
        </button>
      ) : null}
    </div>
  );
}

function ResolutionContract({ bundle }: { bundle: WorkspaceBundle }) {
  const { t } = useI18n();
  const market = bundle.market;
  const oracleSummary = bundle.oracle?.summary;
  return (
    <section className="market-card market-resolution-card">
      <div className="market-section-heading">
        <div>
          <span>{t('market.resolutionContract')}</span>
          <h2>{t('market.whatDecides')}</h2>
        </div>
        <StatusBadge
          label={statusLabel(oracleSummary?.completionStatus || bundle.oracle?.completionStatus || market?.status, t)}
          tone={statusTone(oracleSummary?.completionStatus || bundle.oracle?.completionStatus || market?.status)}
        />
      </div>
      <div className="market-resolution-copy">
        {market?.description || t('market.noResolutionCriteria')}
      </div>
      <div className="market-identifier-grid">
        <IdentifierRow label={t('market.localMarket')} value={bundle.identity?.localMarketId || market?.id} />
        <IdentifierRow label={t('market.gammaMarket')} value={bundle.identity?.gammaMarketId || market?.gammaMarketId} />
        <IdentifierRow label={t('market.conditionId')} value={bundle.identity?.conditionId || market?.conditionId} />
        <IdentifierRow label={t('market.questionId')} value={bundle.identity?.questionId || market?.questionId} />
        <IdentifierRow label={t('market.yesToken')} value={bundle.identity?.yesTokenId || market?.yesTokenId} />
        <IdentifierRow label={t('market.noToken')} value={bundle.identity?.noTokenId || market?.noTokenId} />
      </div>
    </section>
  );
}

function TradeTable({ trades }: { trades: TradeRow[] }) {
  const { t, formatDateTime, formatNumber, formatRelativeTime } = useI18n();
  return (
    <section className="market-card market-trades-card">
      <div className="market-section-heading">
        <div>
          <span>{t('market.onChainExecution')}</span>
          <h2>{t('market.orderFilledTape')}</h2>
        </div>
        <StatusBadge label={t('market.rows', { count: trades.length })} tone={trades.length ? 'positive' : 'warning'} compact />
      </div>
      <div className="market-data-table-wrap">
        <table className="market-data-table">
          <thead>
            <tr>
              <th>{t('market.observed')}</th>
              <th>{t('market.sideOutcome')}</th>
              <th>{t('market.price')}</th>
              <th>{t('market.size')}</th>
              <th>{t('market.makerTaker')}</th>
              <th>{t('market.eventKey')}</th>
            </tr>
          </thead>
          <tbody>
            {trades.slice(0, MAX_VISIBLE_TRADES).map((trade, index) => (
              <tr key={`${trade.txHash || 'trade'}-${trade.logIndex ?? index}`}>
                <td><time>{trade.timestamp ? formatDateTime(trade.timestamp) : '—'}</time><small>{formatRelativeTime(trade.timestamp)}</small></td>
                <td><strong className={String(trade.side || '').toLowerCase()}>{trade.side || '--'}</strong><small>{trade.outcome || t('market.outcomeUnknown')}</small></td>
                <td>{formatProbabilityCents(trade.price)}</td>
                <td>{formatShares(trade.size, formatNumber)}</td>
                <td><code>{shortHash(trade.maker, 7, 4)}</code><small>→ {shortHash(trade.taker, 7, 4)}</small></td>
                <td><code>{shortHash(trade.txHash, 8, 5)}</code><small>{t('market.logIndex', { index: trade.logIndex ?? '--' })}</small></td>
              </tr>
            ))}
          </tbody>
        </table>
        {!trades.length ? <div className="market-table-empty">{t('market.noOrderFilled')}</div> : null}
      </div>
    </section>
  );
}

function OracleTimeline({ events, bundle }: { events: OracleEvent[]; bundle: WorkspaceBundle }) {
  const { t, formatDateTime, formatRelativeTime } = useI18n();
  const current = bundle.oracle?.summary?.completionStatus || bundle.oracle?.completionStatus || 'OPEN';
  const oracleStages = [
    { id: 'request', label: t('market.stage.request'), matcher: /request/i },
    { id: 'propose', label: t('market.stage.propose'), matcher: /propos/i },
    { id: 'dispute', label: t('market.stage.dispute'), matcher: /disput/i },
    { id: 'settle', label: t('market.stage.settle'), matcher: /settle|resolve/i },
  ].map((stage) => ({
    ...stage,
    event: events.find((event) => stage.matcher.test(String(event.eventStatus || event.completionStatus || ''))),
  }));
  return (
    <section className="market-card market-oracle-card">
      <div className="market-section-heading">
        <div>
          <span>{t('market.resolutionLifecycle')}</span>
          <h2>{t('market.oracleTimeline')}</h2>
        </div>
        <StatusBadge label={statusLabel(current, t)} tone={statusTone(current)} />
      </div>
      <div className="market-oracle-phase-rail" aria-label={t('market.oraclePhases')}>
        {oracleStages.map((stage, index) => (
          <article className={stage.event ? 'is-observed' : 'is-pending'} key={stage.id}>
            <span>{String(index + 1).padStart(2, '0')}</span>
            <div>
              <strong>{stage.label}</strong>
              <em>{stage.event ? formatRelativeTime(stage.event.eventTime) : t('market.notObserved')}</em>
            </div>
          </article>
        ))}
      </div>
      <div className="market-timeline">
        {events.map((event, index) => (
          <article className="market-timeline-row" key={`${event.txHash || event.id || 'oracle'}-${event.logIndex ?? index}`}>
            <span className="market-timeline-node" />
            <div>
              <strong>{event.eventStatus || event.completionStatus || t('market.oracleObservation')}</strong>
              <span>{event.settlementOutcome || event.proposedPrice || event.settledPrice || t('market.noOutcomeValue')}</span>
              <em>{event.eventTime ? formatDateTime(event.eventTime) : '—'} · {cleanSource(event.sourceAdapter || event.sourceOracle || 'oracle')}</em>
            </div>
            <code>{shortHash(event.txHash, 9, 5)}<small>{t('market.logIndex', { index: event.logIndex ?? '--' })}</small></code>
          </article>
        ))}
        {!events.length ? (
          <div className="market-empty-state is-compact">
            <strong>{t('market.noOracleEvent')}</strong>
            <span>{t('market.noOracleEventDetail')}</span>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function ContentCard({ item }: { item: ContentItem }) {
  const { t, formatRelativeTime } = useI18n();
  const body = (
    <>
      <div className="market-content-meta">
        <span>{cleanSource(item.provider || item.source || item.contentType)}</span>
        <time>{formatRelativeTime(item.publishedAt)}</time>
      </div>
      <strong>{item.title || t('market.untitledIntelligence')}</strong>
      {item.summary ? <p>{item.summary}</p> : null}
      <div className="market-content-footer">
        <span>{item.category || t('market.marketContext')}</span>
        {item.relevanceScore != null ? <em>{t('market.relevance', { score: Math.round(Number(item.relevanceScore) * 100) })}</em> : null}
      </div>
    </>
  );
  return item.url ? (
    <a className="market-content-item" href={item.url} target="_blank" rel="noopener noreferrer">{body}</a>
  ) : (
    <article className="market-content-item">{body}</article>
  );
}

function LinkedIntelligence({ items }: { items: ContentItem[] }) {
  const { t } = useI18n();
  return (
    <section className="market-card market-content-card">
      <div className="market-section-heading">
        <div>
          <span>{t('market.realityLayer')}</span>
          <h2>{t('market.linkedIntelligence')}</h2>
        </div>
        <StatusBadge label={t('market.items', { count: items.length })} tone={items.length ? 'info' : 'warning'} compact />
      </div>
      <div className="market-content-grid">
        {items.slice(0, MAX_VISIBLE_CONTENT).map((item, index) => (
          <ContentCard item={item} key={item.id || `${item.url || item.title}-${index}`} />
        ))}
      </div>
      {!items.length ? (
        <div className="market-empty-state is-compact">
          <strong>{t('market.noLinkedReporting')}</strong>
          <span>{t('market.noLinkedReportingDetail')}</span>
        </div>
      ) : null}
    </section>
  );
}

function OutcomeRail({
  outcomes,
  activeMarketId,
  activeYesPrice,
}: {
  outcomes: MarketGroupOutcome[];
  activeMarketId?: number | null;
  activeYesPrice?: string | number | null;
}) {
  const { t, formatNumber, formatPercent } = useI18n();
  if (!outcomes.length) return null;
  return (
    <section className="market-outcome-rail" aria-label={t('market.eventOutcomes')}>
      <div className="market-outcome-rail-heading">
        <span>{t('market.eventOutcomes')}</span>
        <strong>{t('market.contracts', { count: outcomes.length })}</strong>
      </div>
      <div className="market-outcome-list">
        {outcomes.map((outcome, index) => {
          const marketId = Number(outcome.marketId);
          const active = Number.isFinite(marketId) && marketId === Number(activeMarketId);
          const displayedPrice = active && activeYesPrice != null ? activeYesPrice : outcome.yesPrice;
          const content = (
            <>
              <span>{outcome.label || outcome.title || t('market.outcome', { index: index + 1 })}</span>
              <strong>{formatMarketPercent(displayedPrice, formatPercent)}</strong>
              <em>{formatMarketCurrency(outcome.volume24h, formatNumber)} · {t('market.transactions', { count: formatMarketCompact(outcome.tradeCount24h, formatNumber) })}</em>
            </>
          );
          return Number.isFinite(marketId) && marketId > 0 ? (
            <a
              className={active ? 'active' : ''}
              href={`/markets/${marketId}`}
              aria-current={active ? 'page' : undefined}
              key={outcome.outcomeKey || marketId}
            >
              {content}
            </a>
          ) : (
            <div className="pending" key={outcome.outcomeKey || index}>{content}</div>
          );
        })}
      </div>
    </section>
  );
}

export function MarketWorkspace() {
  const {
    t,
    formatDateTime,
    formatNumber,
    formatPercent,
    formatRelativeTime,
  } = useI18n();
  const marketId = readMarketId();
  const [bundle, setBundle] = useState<WorkspaceBundle | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefreshedAt, setLastRefreshedAt] = useState<number | null>(null);
  const [watchState, setWatchState] = useState<'idle' | 'busy' | 'watched'>('idle');
  const requestRef = useRef(0);

  const refresh = useCallback(async () => {
    if (!marketId) {
      setError(t('market.invalidUrlDetail'));
      setLoading(false);
      return;
    }
    const requestId = ++requestRef.current;
    setLoading(true);
    try {
      const next = await fetchWorkspaceBundle(marketId, { includeContent: true, includeLob: true });
      if (!next.market) {
        throw new Error(t('market.noIdentity', { id: marketId }));
      }
      const outcome = selectedOutcome(next);
      const tokenId = String(outcome?.yesTokenId || next.identity?.yesTokenId || next.market?.yesTokenId || '').trim();
      const noTokenId = String(outcome?.noTokenId || next.identity?.noTokenId || next.market?.noTokenId || '').trim();
      if (tokenId) {
        try {
          next.lob = await fetchMarketLobByToken(tokenId, outcome?.label || next.market?.title || '', noTokenId, 3500);
        } catch {
          // The market dossier remains usable when the live book is unavailable.
        }
      }
      if (requestId !== requestRef.current) return;
      setBundle(next);
      setError(null);
      setLastRefreshedAt(Date.now());
    } catch (loadError) {
      if (requestId !== requestRef.current) return;
      setError(loadError instanceof Error ? loadError.message : t('market.loadError'));
    } finally {
      if (requestId === requestRef.current) setLoading(false);
    }
  }, [marketId, t]);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => {
      if (document.visibilityState === 'hidden') return;
      void refresh();
    }, REFRESH_INTERVAL_MS);
    return () => {
      window.clearInterval(interval);
      requestRef.current += 1;
    };
  }, [refresh]);

  const outcome = useMemo(() => selectedOutcome(bundle), [bundle]);
  const market = bundle?.market;

  const watchMarket = async () => {
    if (!marketId) return;
    setWatchState('busy');
    try {
      const session = await fetchAuthSession();
      if (!session.authenticated) {
        window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname)}`);
        return;
      }
      if (session.user?.forcePasswordChange) {
        window.location.assign('/account');
        return;
      }
      await addWatchlistMarket(marketId);
      setWatchState('watched');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('market.watchError'));
      setWatchState('idle');
    }
  };
  const price = bundle?.price;
  const yesPrice = price?.latestYesPrice ?? price?.latestPrice ?? outcome?.yesPrice ?? market?.latestYesPrice ?? market?.latestPrice;
  const noPrice = price?.latestNoPrice ?? outcome?.noPrice ?? market?.latestNoPrice;
  const volume = price?.volume24h ?? outcome?.volume24h;
  const trades24h = price?.tradeCount24h ?? outcome?.tradeCount24h;
  const observedAt = price?.updatedAt || bundle?.generatedAt || null;
  const freshness = freshnessFromTimestamp(observedAt, 300);
  const healthLevel = String(bundle?.health?.level || bundle?.diagnostics?.level || (error ? 'critical' : 'unknown'));
  const sourceClaims = bundle ? evidenceClaims(bundle) : [];
  const readyClaims = sourceClaims.filter((claim) => statusTone(claim.status) === 'positive').length;
  const issueCount = bundle?.evidence?.issues?.length || bundle?.health?.issues?.length || 0;
  const tags = useMemo(
    () => [...new Set([market?.category, ...(market?.tags || [])].filter((tag): tag is string => Boolean(tag)))].slice(0, 7),
    [market?.category, market?.tags],
  );

  useEffect(() => {
    if (!market?.title) return;
    const previousTitle = document.title;
    document.title = `${market.title} · ${t('market.documentSuffix')}`;
    return () => {
      document.title = previousTitle;
    };
  }, [market?.title, t]);

  if (!marketId) {
    return (
      <div className="market-shell">
        <main className="market-fatal-state">
          <StatusBadge label={t('market.invalidUrl')} tone="critical" />
          <h1>{t('market.unavailable')}</h1>
          <p>{t('market.invalidUrlHelp')} <code>/markets/2784982</code></p>
          <a href="/">{t('market.returnAtlas')}</a>
        </main>
      </div>
    );
  }

  return (
    <div className="market-shell">
      <header className="market-topbar">
        <div className="market-brand-cluster">
          <a className="market-home-link" href="/">◎ {t('mobileNav.atlas')}</a>
          <a className="market-home-link" href="/data-quality">{t('mobileNav.quality')}</a>
          <div className="market-brand">{t('market.brand')} <span>{t('market.brandDetail')}</span></div>
        </div>
        <div className="market-actions">
          <span className="market-refresh-note">
            {lastRefreshedAt ? t('market.observedAt', { date: formatDateTime(lastRefreshedAt) }) : t('market.awaitingObservation')}
          </span>
          <button type="button" disabled={watchState !== 'idle'} onClick={() => void watchMarket()}>
            {watchState === 'busy' ? t('market.adding') : watchState === 'watched' ? t('market.watching') : t('market.watch')}
          </button>
          <button type="button" onClick={() => void navigator.clipboard.writeText(window.location.href)}>{t('market.copyLink')}</button>
          <button type="button" className="primary" disabled={loading} onClick={() => void refresh()}>
            {loading ? t('market.refreshing') : t('market.refresh')}
          </button>
        </div>
      </header>

      <main className="market-main">
        <section className="market-hero">
          <div className="market-hero-copy">
            <div className="market-kicker-row">
              <span>{t('market.marketId', { id: marketId })}</span>
              <StatusBadge
                label={statusLabel(market?.status || (loading ? 'loading' : 'unknown'), t)}
                tone={statusTone(market?.status || (loading ? 'loading' : 'unknown'))}
                compact
              />
              <StatusBadge label={statusLabel(freshness, t)} tone={statusTone(freshness)} compact />
            </div>
            <h1>{market?.title || (loading ? t('market.loading') : t('market.marketNumber', { id: marketId }))}</h1>
            <p>
              {outcome?.label && outcome.label !== market?.title
                ? t('market.focusedOutcome', { outcome: outcome.label })
                : t('market.description')}
            </p>
            <div className="market-hero-tags">
              {tags.map((tag) => (
                <span key={tag}>{tag}</span>
              ))}
            </div>
          </div>
          <div className="market-probability-lockup">
            <span>{t('market.currentYes')}</span>
            <strong>{formatMarketPercent(yesPrice, formatPercent)}</strong>
            <em className={signedClass(price?.change24h)}>{formatMarketSignedPercent(price?.change24h, formatPercent)} / 24h</em>
            <div>
              <span>{t('market.no')} <b>{formatMarketPercent(noPrice, formatPercent)}</b></span>
              <span>{t('market.closes')} <b>{formatRelativeTime(market?.endDate)}</b></span>
            </div>
          </div>
        </section>

        {error ? (
          <div className="market-error-banner" role="alert">
            <span>{error}</span>
            <em>{bundle ? t('market.lastGood') : t('market.retryApi')}</em>
          </div>
        ) : null}

        {bundle ? (
          <>
            <section className="market-metric-grid" aria-label={t('market.summary')}>
              <MetricCard
                eyebrow={t('market.yesProbability')}
                value={formatMarketPercent(yesPrice, formatPercent)}
                detail={`${cleanSource(price?.priceSource || bundle.servingSource)} · ${formatRelativeTime(observedAt)}`}
                tone={yesPrice == null ? 'warning' : 'positive'}
              />
              <MetricCard
                eyebrow={t('market.volume24h')}
                value={formatMarketCurrency(volume, formatNumber)}
                detail={t('market.localTradesDetail', {
                  local: formatNumber(bundle.trades.length),
                  reported: formatMarketCompact(trades24h, formatNumber),
                })}
                tone={Number(volume || 0) > 0 ? 'info' : 'neutral'}
              />
              <MetricCard
                eyebrow={t('market.evidenceReadiness')}
                value={`${readyClaims}/${sourceClaims.length}`}
                detail={t('market.activeIssuesDetail', { count: formatNumber(issueCount) })}
                tone={issueCount ? 'warning' : (readyClaims ? 'positive' : 'neutral')}
              />
              <MetricCard
                eyebrow={t('market.resolution')}
                value={statusLabel(bundle.oracle?.summary?.completionStatus || bundle.oracle?.completionStatus || market?.status, t)}
                detail={t('market.oracleEventsDetail', { count: formatNumber(bundle.oracle?.timeline?.length || 0) })}
                tone={statusTone(bundle.oracle?.summary?.completionStatus || bundle.oracle?.completionStatus || market?.status)}
              />
              <MetricCard
                eyebrow={t('market.workspaceHealth')}
                value={statusLabel(healthLevel, t)}
                detail={t('market.servingDetail', { source: bundle.servingSource || 'fallback' })}
                tone={statusTone(healthLevel)}
              />
            </section>

            <OutcomeRail
              outcomes={bundle.group?.outcomes || []}
              activeMarketId={market?.id || bundle.identity?.localMarketId}
              activeYesPrice={yesPrice}
            />

            <div className="market-primary-grid">
              <section className="market-card market-chart-card">
                <div className="market-section-heading">
                  <div>
                    <span>{t('market.probabilityEvidence')}</span>
                    <h2>{t('market.history')}</h2>
                  </div>
                  <StatusBadge
                    label={statusLabel(bundle.health?.chartStatus || bundle.chart?.historyStatus, t)}
                    tone={statusTone(bundle.health?.chartStatus || bundle.chart?.historyStatus)}
                  />
                </div>
                <ProbabilityChart bundle={bundle} />
              </section>
              <EvidenceChain bundle={bundle} />
            </div>

            <div className="market-secondary-grid">
              <ResolutionContract bundle={bundle} />
              <OrderBook lob={bundle.lob} />
            </div>

            <TradeTable trades={bundle.trades || []} />

            <div className="market-secondary-grid">
              <OracleTimeline events={bundle.oracle?.timeline || []} bundle={bundle} />
              <LinkedIntelligence items={bundle.content?.items || []} />
            </div>
          </>
        ) : (
          <section className="market-loading-grid" aria-label={t('market.loading')}>
            {Array.from({ length: 8 }, (_, index) => <div className="market-skeleton" key={index} />)}
          </section>
        )}
      </main>
      <MobileWorkspaceNav />
    </div>
  );
}

export default MarketWorkspace;
