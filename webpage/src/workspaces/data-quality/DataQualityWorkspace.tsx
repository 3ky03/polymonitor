import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { MobileWorkspaceNav } from '@/components/MobileWorkspaceNav';
import {
  MetricCard,
  StatusBadge,
  type OperationalTone,
} from '@/components/design-system/StatusPrimitives';
import { shortHash } from '@/panels/shared/formatters';
import { fetchMarketDataQuality } from '@/services/api';
import { useI18n } from '@/services/i18n';
import type {
  MarketDataQualityDimension,
  MarketDataQualityGap,
  MarketDataQualityLifecycleStage,
  MarketDataQualityPayload,
  MarketDataQualityWatermark,
  OracleEvent,
} from '@/types';

const REFRESH_INTERVAL_MS = 60_000;
const MAX_VISIBLE_ORACLE_EVENTS = 16;
type Translator = ReturnType<typeof useI18n>['t'];
type NumberFormatter = ReturnType<typeof useI18n>['formatNumber'];

function statusTone(status?: string | null): OperationalTone {
  const normalized = String(status || '').trim().toLowerCase();
  if (['critical', 'error', 'missing', 'stale'].includes(normalized)) return 'critical';
  if (['warning', 'degraded', 'aging', 'partial'].includes(normalized)) return 'warning';
  if (['ok', 'fresh', 'ready', 'observed'].includes(normalized)) return 'positive';
  return 'neutral';
}

function parseTimestamp(value?: string | null): number | null {
  const parsed = Date.parse(String(value || ''));
  return Number.isFinite(parsed) ? parsed : null;
}

function ageSeconds(value?: string | null): number | null {
  const parsed = parseTimestamp(value);
  return parsed == null ? null : Math.max(0, Math.round((Date.now() - parsed) / 1_000));
}

function cleanLabel(value?: string | null): string {
  return String(value || 'unknown')
    .replace(/[_-]/g, ' ')
    .replace(/\b\w/g, (character: string) => character.toUpperCase());
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
    'not-collected': 'status.notCollected',
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
  return key ? t(key) : cleanLabel(value);
}

function formatCoverage(value: number | null | undefined, formatNumber: NumberFormatter): string {
  return value == null || !Number.isFinite(value)
    ? '--'
    : `${formatNumber(value, { maximumFractionDigits: value >= 99 ? 1 : 2 })}%`;
}

function formatCompact(value: number | string | null | undefined, formatNumber: NumberFormatter): string {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? formatNumber(numeric, { notation: 'compact', maximumFractionDigits: 1 })
    : '--';
}

function formatCount(value: number | null | undefined, formatNumber: NumberFormatter, t: Translator): string {
  return value == null ? t('status.notCollected') : formatCompact(value, formatNumber);
}

function localizedDimension(item: MarketDataQualityDimension, t: Translator) {
  const known = {
    identity: ['quality.dimension.identity', 'quality.dimension.identityDetail'],
    'token-registry': ['quality.dimension.tokenRegistry', 'quality.dimension.tokenRegistryDetail'],
    'serving-price': ['quality.dimension.servingPrice', 'quality.dimension.servingPriceDetail'],
    'oracle-binding': ['quality.dimension.oracleBinding', 'quality.dimension.oracleBindingDetail'],
    resolution: ['quality.dimension.resolution', 'quality.dimension.resolutionDetail'],
    'oracle-freshness': ['quality.dimension.oracleFreshness', 'quality.dimension.oracleFreshnessDetail'],
    'trade-freshness': ['quality.dimension.tradeFreshness', 'quality.dimension.tradeFreshnessDetail'],
  } as const;
  const keys = known[item.id as keyof typeof known];
  return keys ? { label: t(keys[0]), detail: t(keys[1]) } : { label: item.label, detail: item.detail };
}

function localizedGap(gap: MarketDataQualityGap, t: Translator) {
  const known = {
    'oracle-index-stale': ['quality.gap.oracleStale', 'quality.gap.oracleStaleDetail'],
    'closed-awaiting-oracle': ['quality.gap.closedAwaiting', 'quality.gap.closedAwaitingDetail'],
    'unbound-oracle-events': ['quality.gap.unboundOracle', 'quality.gap.unboundOracleDetail'],
    'missing-question-id': ['quality.gap.missingQuestion', 'quality.gap.missingQuestionDetail'],
    'missing-normalized-token-registry': ['quality.gap.missingTokenRegistry', 'quality.gap.missingTokenRegistryDetail'],
  } as const;
  const keys = known[gap.id as keyof typeof known];
  return keys ? { label: t(keys[0]), detail: t(keys[1]) } : { label: gap.label, detail: gap.detail };
}

function localizedLifecycle(stage: MarketDataQualityLifecycleStage, t: Translator) {
  const known = {
    discovered: ['quality.lifecycle.discovered', 'quality.lifecycle.discoveredDetail'],
    tradeable: ['quality.lifecycle.tradeable', 'quality.lifecycle.tradeableDetail'],
    active: ['quality.lifecycle.active', 'quality.lifecycle.activeDetail'],
    closed: ['quality.lifecycle.closed', 'quality.lifecycle.closedDetail'],
    proposed: ['quality.lifecycle.proposed', 'quality.lifecycle.proposedDetail'],
    disputed: ['quality.lifecycle.disputed', 'quality.lifecycle.disputedDetail'],
    resolved: ['quality.lifecycle.resolved', 'quality.lifecycle.resolvedDetail'],
    redeemed: ['quality.lifecycle.redeemed', 'quality.lifecycle.redeemedDetail'],
  } as const;
  const keys = known[stage.id as keyof typeof known];
  return keys ? { label: t(keys[0]), detail: t(keys[1]) } : { label: stage.label, detail: stage.detail };
}

function DimensionCard({ item }: { item: MarketDataQualityDimension }) {
  const { t, formatDateTime, formatDuration, formatNumber, formatRelativeTime } = useI18n();
  const value = Number(item.coveragePct);
  const hasCoverage = Number.isFinite(value);
  const localized = localizedDimension(item, t);
  return (
    <article className={`quality-dimension is-${statusTone(item.status)}`}>
      <div className="quality-dimension-heading">
        <div>
          <span>{item.source}</span>
          <strong>{localized.label}</strong>
        </div>
        <StatusBadge label={statusLabel(item.status, t)} tone={statusTone(item.status)} compact />
      </div>
      <div className="quality-dimension-value">
        <strong>{formatCoverage(item.coveragePct, formatNumber)}</strong>
        {item.ageSeconds != null ? <span>{t('quality.old', { age: formatDuration(item.ageSeconds) })}</span> : null}
      </div>
      <progress max={100} value={hasCoverage ? Math.max(0, Math.min(100, value)) : 0}>
        {formatCoverage(item.coveragePct, formatNumber)}
      </progress>
      <p>{localized.detail || t('quality.noDefinition')}</p>
      <div className="quality-dimension-foot">
        {item.denominator ? (
          <span>{t('quality.records', {
            numerator: formatCompact(item.numerator || 0, formatNumber),
            denominator: formatCompact(item.denominator, formatNumber),
          })}</span>
        ) : (
          <span>{item.observedAt
            ? t('quality.observedAgo', { time: formatRelativeTime(item.observedAt) })
            : t('quality.noObservationTimestamp')}</span>
        )}
        <time>{item.observedAt ? formatDateTime(item.observedAt) : '—'}</time>
      </div>
    </article>
  );
}

function GapLedger({ gaps }: { gaps: MarketDataQualityGap[] }) {
  const { t, formatNumber, formatRelativeTime } = useI18n();
  return (
    <section className="quality-card quality-gap-card">
      <div className="quality-section-heading">
        <div>
          <span>{t('quality.failOpenForbidden')}</span>
          <h2>{t('quality.activeGapLedger')}</h2>
        </div>
        <StatusBadge
          label={t('quality.gapCount', { count: formatNumber(gaps.length) })}
          tone={gaps.some((gap) => gap.severity === 'critical') ? 'critical' : (gaps.length ? 'warning' : 'positive')}
        />
      </div>
      <div className="quality-gap-list">
        {gaps.map((gap, index) => {
          const localized = localizedGap(gap, t);
          return (
            <article className={`quality-gap-row is-${statusTone(gap.severity)}`} key={gap.id}>
              <span className="quality-gap-index">{String(index + 1).padStart(2, '0')}</span>
              <div>
                <strong>{localized.label}</strong>
                <p>{localized.detail}</p>
                <em>{gap.source}{gap.observedAt ? ` · ${formatRelativeTime(gap.observedAt)}` : ''}</em>
              </div>
              <div className="quality-gap-count">
                <StatusBadge label={statusLabel(gap.severity, t)} tone={statusTone(gap.severity)} compact />
                <strong>{formatCompact(gap.count, formatNumber)}</strong>
              </div>
            </article>
          );
        })}
        {!gaps.length ? (
          <div className="quality-empty-state">
            <strong>{t('quality.noActiveGaps')}</strong>
            <span>{t('quality.noActiveGapsDetail')}</span>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function LifecycleRail({ stages }: { stages: MarketDataQualityLifecycleStage[] }) {
  const { t, formatNumber } = useI18n();
  return (
    <section className="quality-card quality-lifecycle-card">
      <div className="quality-section-heading">
        <div>
          <span>{t('quality.marketStateModel')}</span>
          <h2>{t('quality.lifecycleCoverage')}</h2>
        </div>
        <p>{t('quality.lifecycleBoundary')}</p>
      </div>
      <div className="quality-lifecycle-rail">
        {stages.map((stage, index) => {
          const localized = localizedLifecycle(stage, t);
          return (
            <article className={stage.status === 'not-collected' ? 'is-missing' : ''} key={stage.id}>
              <span className="quality-lifecycle-index">{String(index + 1).padStart(2, '0')}</span>
              <div>
                <strong>{localized.label}</strong>
                <b>{formatCount(stage.count, formatNumber, t)}</b>
                <p>{localized.detail}</p>
                <em>{stage.source}</em>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function OracleStageRail({ payload }: { payload: MarketDataQualityPayload }) {
  const { t, formatNumber, formatRelativeTime } = useI18n();
  const stages = payload.oracleLifecycle.stages;
  const maximum = Math.max(...stages.map((stage) => stage.count), 1);
  return (
    <section className="quality-card quality-oracle-card">
      <div className="quality-section-heading">
        <div>
          <span>{t('quality.oracleObservations')}</span>
          <h2>{t('quality.oracleLifecycle')}</h2>
        </div>
        <StatusBadge
          label={statusLabel(payload.dimensions.find((item) => item.id === 'oracle-freshness')?.status, t)}
          tone={statusTone(payload.dimensions.find((item) => item.id === 'oracle-freshness')?.status)}
        />
      </div>
      <div className="quality-oracle-stage-rail">
        {stages.map((stage, index) => (
          <article key={stage.id}>
            <span>{String(index + 1).padStart(2, '0')}</span>
            <div>
              <strong>{stage.id === 'request'
                ? t('quality.stage.request')
                : stage.id === 'propose'
                  ? t('quality.stage.propose')
                  : stage.id === 'dispute'
                    ? t('quality.stage.dispute')
                    : stage.id === 'settle'
                      ? t('quality.stage.settle')
                      : stage.label}</strong>
              <b>{formatCompact(stage.count, formatNumber)}</b>
              <progress max={maximum} value={stage.count}>{stage.count}</progress>
            </div>
          </article>
        ))}
      </div>
      <div className="quality-oracle-contract">
        <span>{t('quality.durableIdentity')} <b>tx_hash + log_index</b></span>
        <span>{t('quality.canonicalOrder')} <b>block_number + log_index</b></span>
        <span>{t('quality.latestBlock')} <b>{formatCompact(Number(payload.oracleLifecycle.latestBlock || 0), formatNumber)}</b></span>
        <span>{t('quality.latestEvent')} <b>{formatRelativeTime(payload.oracleLifecycle.latestEventAt)}</b></span>
      </div>
    </section>
  );
}

function WatermarkBoard({ watermarks }: { watermarks: MarketDataQualityWatermark[] }) {
  const { t, formatDateTime, formatDuration, formatNumber } = useI18n();
  const stateDetail = (watermark: MarketDataQualityWatermark) => {
    if (!watermark.state || typeof watermark.state !== 'object') return String(watermark.state || t('quality.noStructuredState'));
    const state = watermark.state as Record<string, unknown>;
    return [state.status, state.phase, state.error].filter(Boolean).join(' · ') || t('quality.structuredCheckpoint');
  };
  return (
    <section className="quality-card quality-watermark-card">
      <div className="quality-section-heading">
        <div>
          <span>{t('quality.applicationCheckpoints')}</span>
          <h2>{t('quality.syncWatermarks')}</h2>
        </div>
      </div>
      <div className="quality-watermark-list">
        {watermarks.map((watermark) => {
          const freshness = ageSeconds(watermark.updatedAt);
          const stale = freshness == null || freshness > 7 * 86_400;
          return (
            <article key={watermark.id}>
              <div>
                <span>{cleanLabel(watermark.id)}</span>
                <strong>{t('quality.block', { block: formatCompact(Number(watermark.lastBlock || 0), formatNumber) })}</strong>
              </div>
              <StatusBadge
                label={t('quality.freshnessBadge', {
                  status: stale ? t('status.stale') : t('status.observed'),
                  age: formatDuration(freshness),
                })}
                tone={stale ? 'critical' : 'positive'}
                compact
              />
              <p>{stateDetail(watermark)}</p>
              <time>{watermark.updatedAt ? formatDateTime(watermark.updatedAt) : '—'}</time>
            </article>
          );
        })}
        {!watermarks.length ? <div className="quality-empty-state"><strong>{t('quality.noSyncCheckpoints')}</strong></div> : null}
      </div>
    </section>
  );
}

function RecentOracleTable({ events }: { events: OracleEvent[] }) {
  const { t, formatDateTime, formatNumber, formatRelativeTime } = useI18n();
  const visible = events.slice(0, MAX_VISIBLE_ORACLE_EVENTS);
  return (
    <section className="quality-card quality-events-card">
      <div className="quality-section-heading">
        <div>
          <span>{t('quality.latestIndexed')}</span>
          <h2>{t('quality.oracleEventTape')}</h2>
        </div>
        <span>{t('quality.visible', { visible: formatNumber(visible.length), total: formatNumber(events.length) })}</span>
      </div>
      <div className="quality-table-wrap">
        <table className="quality-table">
          <thead>
            <tr>
              <th>{t('quality.observed')}</th>
              <th>{t('quality.stage')}</th>
              <th>{t('quality.market')}</th>
              <th>{t('quality.binding')}</th>
              <th>{t('quality.blockColumn')}</th>
              <th>{t('quality.durableKey')}</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((event, index) => (
              <tr key={`${event.txHash || 'oracle'}-${event.logIndex ?? index}`}>
                <td><time>{event.eventTime ? formatDateTime(event.eventTime) : '—'}</time><small>{formatRelativeTime(event.eventTime)}</small></td>
                <td><StatusBadge label={statusLabel(event.eventStatus, t)} tone="info" compact /></td>
                <td>
                  {event.localMarketId ? (
                    <a href={`/markets/${event.localMarketId}`}>{event.marketTitle || t('quality.marketNumber', { id: event.localMarketId })}</a>
                  ) : (
                    <strong>{event.marketTitle || t('quality.unboundOracleEvent')}</strong>
                  )}
                  <small>{event.marketCategory || event.completionStatus || t('quality.unknownCategory')}</small>
                </td>
                <td>
                  <StatusBadge label={event.isBound ? t('status.bound') : t('status.unbound')} tone={event.isBound ? 'positive' : 'warning'} compact />
                  <small>{cleanLabel(event.matchedBy)}</small>
                </td>
                <td><code>{formatCompact(Number(event.blockNumber || 0), formatNumber)}</code></td>
                <td><code>{shortHash(event.txHash, 8, 5)}</code><small>{t('market.logIndex', { index: event.logIndex ?? '--' })}</small></td>
              </tr>
            ))}
          </tbody>
        </table>
        {!visible.length ? <div className="quality-empty-state"><strong>{t('quality.noOracleEvents')}</strong></div> : null}
      </div>
    </section>
  );
}

function GapMarkets({ payload }: { payload: MarketDataQualityPayload }) {
  const { t, formatNumber, formatRelativeTime } = useI18n();
  return (
    <section className="quality-card quality-gap-markets-card">
      <div className="quality-section-heading">
        <div>
          <span>{t('quality.representativeRecords')}</span>
          <h2>{t('quality.awaitingOracle')}</h2>
        </div>
        <span>{t('quality.sampled', { count: formatNumber(payload.gapMarkets.length) })}</span>
      </div>
      <div className="quality-gap-market-list">
        {payload.gapMarkets.map((market) => (
          <a href={`/markets/${market.marketId}`} key={market.marketId}>
            <div>
              <span>{t('quality.marketId', { id: market.marketId })}</span>
              <strong>{market.title}</strong>
            </div>
            <div>
              <StatusBadge label={statusLabel(market.completionStatus, t)} tone="warning" compact />
              <em>{market.category || t('quality.uncategorized')} · {formatRelativeTime(market.observedAt)}</em>
            </div>
          </a>
        ))}
      </div>
    </section>
  );
}

export function DataQualityWorkspace() {
  const {
    locale,
    t,
    formatDateTime,
    formatDuration,
    formatNumber,
    formatRelativeTime,
  } = useI18n();
  const [payload, setPayload] = useState<MarketDataQualityPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestRef = useRef(0);

  const refresh = useCallback(async () => {
    const requestId = ++requestRef.current;
    setLoading(true);
    const controller = new AbortController();
    try {
      const next = await fetchMarketDataQuality(controller.signal);
      if (requestId !== requestRef.current) return;
      setPayload(next);
      setError(null);
    } catch (caught) {
      if (requestId !== requestRef.current) return;
      setError(caught instanceof Error ? caught.message : t('quality.loadError'));
    } finally {
      if (requestId === requestRef.current) setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => void refresh(), REFRESH_INTERVAL_MS);
    return () => {
      window.clearInterval(interval);
      requestRef.current += 1;
    };
  }, [refresh]);

  useEffect(() => {
    const previousTitle = document.title;
    document.title = t('quality.documentTitle');
    return () => {
      document.title = previousTitle;
    };
  }, [locale, t]);

  const dimensions = payload?.dimensions || [];
  const criticalCount = useMemo(
    () => dimensions.filter((item) => statusTone(item.status) === 'critical').length,
    [dimensions],
  );
  const oracleAge = ageSeconds(payload?.summary.latestOracleAt);
  const tradeAge = ageSeconds(payload?.summary.latestTradeAt);

  return (
    <div className="quality-shell">
      <header className="quality-topbar">
        <div className="quality-brand-cluster">
          <a className="quality-home-link" href="/">◎ {t('mobileNav.atlas')}</a>
          <div className="quality-brand">{t('quality.brand')} <span>{t('quality.brandDetail')}</span></div>
        </div>
        <div className="quality-actions">
          <a href="/docs/documentation/">{t('quality.docs')}</a>
          <button type="button" disabled={loading} onClick={() => void refresh()}>
            {loading ? t('quality.refreshing') : t('quality.refresh')}
          </button>
        </div>
      </header>

      <main className="quality-main">
        <section className="quality-hero">
          <div className="quality-hero-copy">
            <div className="quality-kicker-row">
              <span>{t('quality.kicker')}</span>
              <StatusBadge label={statusLabel(payload?.status || 'loading', t)} tone={statusTone(payload?.status || 'loading')} />
            </div>
            <h1>{t('quality.titleLineOne')}<br />{t('quality.titleLineTwo')}</h1>
            <p>{t('quality.description')}</p>
            <div className="quality-hero-contract">
              <span>{t('quality.contract')} <b>{payload?.contractVersion || 'prediction-market-data-quality.v1'}</b></span>
              <span>{t('quality.generated')} <b>{payload ? formatRelativeTime(payload.generatedAt) : t('quality.waiting')}</b></span>
            </div>
          </div>
          <div className="quality-score-lockup">
            <span>{t('quality.weightedReadiness')}</span>
            <strong>{payload ? payload.score.toFixed(1) : '--'}</strong>
            <em>/ 100</em>
            <progress max={100} value={payload?.score || 0}>{payload?.score || 0}</progress>
            <div>
              <span>{t('quality.critical')} <b>{criticalCount}</b></span>
              <span>{t('quality.gaps')} <b>{payload?.summary.activeGapCount ?? '--'}</b></span>
            </div>
          </div>
        </section>

        {error ? (
          <div className="quality-error-banner" role="alert">
            <span>{error}</span>
            <em>{payload ? t('quality.lastGood') : t('quality.retryApi')}</em>
          </div>
        ) : null}

        {payload ? (
          <>
            <section className="quality-metric-grid" aria-label={t('quality.summary')}>
              <MetricCard
                eyebrow={t('quality.canonicalMarkets')}
                value={formatCompact(payload.summary.marketCount, formatNumber)}
                detail={t('quality.servingUniverseDetail', {
                  count: formatCompact(payload.summary.servingMarketCount, formatNumber),
                })}
                tone="info"
              />
              <MetricCard
                eyebrow={t('quality.oracleEvents')}
                value={formatCompact(payload.summary.oracleEventCount, formatNumber)}
                detail={t('quality.boundMarketsDetail', {
                  count: formatCompact(payload.summary.oracleBoundMarketCount, formatNumber),
                })}
                tone="info"
              />
              <MetricCard
                eyebrow={t('quality.recentlyTraded')}
                value={formatCompact(payload.summary.recentlyTradedMarketCount, formatNumber)}
                detail={t('quality.latestTradeDetail', { age: formatDuration(tradeAge) })}
                tone={tradeAge != null && tradeAge <= 900 ? 'positive' : 'warning'}
              />
              <MetricCard
                eyebrow={t('quality.oracleWatermark')}
                value={formatDuration(oracleAge)}
                detail={t('quality.latestEventDetail', {
                  date: payload.summary.latestOracleAt ? formatDateTime(payload.summary.latestOracleAt) : '—',
                })}
                tone={oracleAge != null && oracleAge <= 7 * 86_400 ? 'warning' : 'critical'}
              />
              <MetricCard
                eyebrow={t('quality.publishedGaps')}
                value={String(payload.summary.activeGapCount)}
                detail={t('quality.dimensionCountsDetail', {
                  critical: formatNumber(payload.summary.criticalDimensionCount),
                  warning: formatNumber(payload.summary.warningDimensionCount),
                })}
                tone={payload.summary.criticalDimensionCount ? 'critical' : (payload.summary.warningDimensionCount ? 'warning' : 'positive')}
              />
            </section>

            <div className="quality-primary-grid">
              <section className="quality-card quality-dimensions-card">
                <div className="quality-section-heading">
                  <div>
                    <span>{t('quality.coverageContract')}</span>
                    <h2>{t('quality.dimensions')}</h2>
                  </div>
                  <span>{t('quality.declaredChecks', { count: dimensions.length })}</span>
                </div>
                <div className="quality-dimension-grid">
                  {dimensions.map((item) => <DimensionCard item={item} key={item.id} />)}
                </div>
              </section>
              <GapLedger gaps={payload.gaps || []} />
            </div>

            <LifecycleRail stages={payload.lifecycle || []} />

            <div className="quality-secondary-grid">
              <OracleStageRail payload={payload} />
              <WatermarkBoard watermarks={payload.watermarks || []} />
            </div>

            <RecentOracleTable events={payload.oracleLifecycle.recentEvents || []} />
            <GapMarkets payload={payload} />

            <section className="quality-card quality-semantics-card">
              <div className="quality-section-heading">
                <div>
                  <span>{t('quality.interpretationBoundary')}</span>
                  <h2>{t('quality.auditSemantics')}</h2>
                </div>
              </div>
              <div className="quality-semantics-grid">
                <div><span>{t('quality.eventIdentity')}</span><code>{payload.semantics?.eventIdentity || 'tx_hash + log_index'}</code></div>
                <div><span>{t('quality.canonicalOrder')}</span><code>{payload.semantics?.canonicalOrder || 'block_number + log_index'}</code></div>
                <div><span>{t('quality.marketBridge')}</span><code>{payload.semantics?.marketBridge || 'market + condition + question + token'}</code></div>
                <p>{t('quality.semanticScore')}</p>
              </div>
            </section>
          </>
        ) : (
          <section className="quality-loading-grid" aria-label={t('quality.loading')}>
            {Array.from({ length: 10 }, (_, index) => <div className="quality-skeleton" key={index} />)}
          </section>
        )}
      </main>
      <MobileWorkspaceNav />
    </div>
  );
}

export default DataQualityWorkspace;
