import { Panel } from '@/components/Panel';
import type { OracleEvent, OraclePayload, PanelRenderContext } from '@/types';
import type { PanelRenderMap } from './types';
import { AiMarketWidePanel } from './shared/ai-market-wide';
import { oracleList } from './shared/renderers';
import { shortHash } from './shared/formatters';
import { globalOracle } from './shared/selectors';
import { useI18n } from '@/services/i18n';

type OracleI18n = ReturnType<typeof useI18n>;

type OracleFeedView = {
  events: OracleEvent[];
  source: 'focused' | 'global-fallback' | 'global';
};

export function oracleFeedView(focused: OraclePayload | null, globalEvents: OracleEvent[]): OracleFeedView {
  if (focused?.timeline?.length) {
    return { events: focused.timeline, source: 'focused' };
  }
  return {
    events: globalEvents,
    source: focused ? 'global-fallback' : 'global',
  };
}

function focusedOraclePayload(ctx: PanelRenderContext): OraclePayload | null {
  if (ctx.selectedMarketId && ctx.bundle?.oracle) {
    const oracleMarketId = Number(ctx.bundle.oracle.localMarketId ?? ctx.bundle.oracle.marketId);
    if (Number.isFinite(oracleMarketId) && oracleMarketId === Number(ctx.selectedMarketId)) {
      return ctx.bundle.oracle;
    }
  }
  const selectedGroup = ctx.selectedMarketGroupDetail || ctx.bundle?.group || ctx.selectedMarketGroup;
  const selectedOutcome = (
    ctx.bundle?.selectedOutcome && ctx.selectedMarketId != null && Number(ctx.bundle.selectedOutcome.marketId) === ctx.selectedMarketId
      ? ctx.bundle.selectedOutcome
      : null
  ) || (
    (selectedGroup?.outcomes || []).find((outcome) => ctx.selectedMarketId != null && Number(outcome.marketId) === ctx.selectedMarketId)
    || (selectedGroup?.topOutcomes || []).find((outcome) => ctx.selectedMarketId != null && Number(outcome.marketId) === ctx.selectedMarketId)
    || (selectedGroup?.outcomes || []).find((outcome) => outcome.outcomeKey === ctx.selectedMarketGroupOutcomeKey)
    || (selectedGroup?.topOutcomes || []).find((outcome) => outcome.outcomeKey === ctx.selectedMarketGroupOutcomeKey)
    || null
  );
  if (!selectedOutcome && !ctx.selectedMarketId) return null;
  return {
    marketId: Number(selectedOutcome?.marketId || ctx.selectedMarketId || 0),
    localMarketId: selectedOutcome?.marketId ?? ctx.selectedMarketId ?? null,
    gammaMarketId: selectedOutcome?.gammaMarketId ?? ctx.bundle?.market?.gammaMarketId ?? ctx.selectedMarket?.gammaMarketId ?? null,
    conditionId: selectedOutcome?.conditionId ?? ctx.selectedMarket?.conditionId ?? ctx.bundle?.market?.conditionId ?? null,
    questionId: ctx.selectedMarket?.questionId ?? ctx.bundle?.market?.questionId ?? null,
    oracle: ctx.selectedMarket?.oracle ?? ctx.bundle?.market?.oracle ?? null,
    currentStatus: ctx.selectedMarket?.status ?? 'OPEN',
    completionStatus: 'OPEN',
    isTradingClosed: false,
    isResolved: false,
    isFinal: false,
    settlementOutcome: 'UNKNOWN',
    settlementSource: selectedOutcome?.marketId ? 'market' : 'gamma-event',
    timeline: [],
  };
}

function oracleIdentity(ctx: PanelRenderContext, payload: OraclePayload) {
  const identity = ctx.bundle?.identity;
  const market = ctx.bundle?.market || ctx.selectedMarket;
  return {
    questionId: payload.questionId || identity?.questionId || market?.questionId || null,
    conditionId: payload.conditionId || identity?.conditionId || market?.conditionId || null,
    oracle: payload.oracle || identity?.oracle || market?.oracle || null,
    gammaMarketId: payload.gammaMarketId || identity?.gammaMarketId || market?.gammaMarketId || null,
  };
}

function oracleIsLinked(ctx: PanelRenderContext, payload: OraclePayload) {
  const identity = oracleIdentity(ctx, payload);
  return Boolean(identity.questionId || identity.conditionId || identity.oracle);
}

function focusedOracleStatus(ctx: PanelRenderContext, payload: OraclePayload, i18n: OracleI18n) {
  const { t } = i18n;
  const marketTitle = ctx.bundle?.market?.title || ctx.selectedMarket?.title || ctx.bundle?.group?.title || ctx.selectedMarketGroupDetail?.title || ctx.selectedMarketGroup?.title || t('atlasOracle.selectedMarket');
  const identity = oracleIdentity(ctx, payload);
  const linked = oracleIsLinked(ctx, payload);
  const currentStatus = String(payload.currentStatus || payload.completionStatus || '').toLowerCase();
  const tradingClosed = Boolean(payload.isTradingClosed || payload.isResolved || payload.isFinal || currentStatus.includes('closed') || currentStatus.includes('ended'));
  const resolved = Boolean(payload.isResolved || payload.isFinal);
  const outcome = payload.settlementOutcome && payload.settlementOutcome !== 'UNKNOWN' ? payload.settlementOutcome : t('atlasOracle.pendingOutcome');
  const createdAt = ctx.bundle?.market?.createdAt || ctx.selectedMarket?.createdAt || ctx.bundle?.group?.createdAt || ctx.selectedMarketGroupDetail?.createdAt || ctx.selectedMarketGroup?.createdAt || null;
  const closedAt = ctx.bundle?.market?.endDate || ctx.selectedMarket?.endDate || ctx.bundle?.group?.endDate || ctx.selectedMarketGroupDetail?.endDate || ctx.selectedMarketGroup?.endDate || null;
  const timeline = payload.timeline || [];
  const latestEvent = timeline.length ? timeline[timeline.length - 1] : null;
  const updatedAt = latestEvent?.eventTime || ctx.bundle?.generatedAt || ctx.bundle?.servingUpdatedAt || null;
  const proposedEvent = (payload.timeline || []).find((event) => String(event.eventStatus || '').toLowerCase().includes('propose'));
  const disputedEvent = (payload.timeline || []).find((event) => String(event.eventStatus || '').toLowerCase().includes('dispute'));
  const finalizedEvent = (payload.timeline || []).find((event) => String(event.eventStatus || '').toLowerCase().includes('settle') || event.isFinal);
  const stage = resolved
    ? { label: t('atlasOracle.stageFinalized'), detail: t('atlasOracle.finalizedHint', { outcome }), tone: 'positive' }
    : disputedEvent
      ? { label: t('atlasOracle.stageDisputed'), detail: t('atlasOracle.disputedHint'), tone: 'warning' }
      : proposedEvent
        ? { label: t('atlasOracle.stageProposed'), detail: t('atlasOracle.proposedHint'), tone: 'warning' }
        : tradingClosed
          ? { label: t('atlasOracle.stageAwaiting'), detail: t('atlasOracle.awaitingHint'), tone: 'warning' }
          : {
              label: t('atlasOracle.stageTrading'),
              detail: closedAt
                ? t('atlasOracle.tradingHintWithClose', { time: i18n.formatRelativeTime(closedAt) })
                : t('atlasOracle.tradingHint'),
              tone: 'positive',
            };
  const lifecycle = [
    { label: t('atlasOracle.created'), time: createdAt, active: false, done: Boolean(createdAt) },
    { label: t('atlasOracle.tradingOpen'), time: createdAt, active: !tradingClosed, done: Boolean(createdAt) },
    { label: tradingClosed ? t('atlasOracle.closed') : t('atlasOracle.closes'), time: closedAt, active: tradingClosed && !proposedEvent && !resolved, done: tradingClosed },
    { label: proposedEvent ? t('atlasOracle.submitted') : t('atlasOracle.proposed'), time: proposedEvent?.eventTime || null, active: Boolean(proposedEvent && !disputedEvent && !resolved), done: Boolean(proposedEvent) },
    { label: t('atlasOracle.finalized'), time: finalizedEvent?.eventTime || null, active: resolved, done: resolved },
  ];
  return (
    <div className="wm-oracle-shell focused">
      <section className={`wm-oracle-stage-card ${stage.tone}`} aria-label={t('atlasOracle.currentStage')}>
        <div className="wm-oracle-stage-heading">
          <span>{t('atlasOracle.currentStage')}</span>
          <em>{linked ? t('atlasOracle.linked') : t('atlasOracle.unlinked')}</em>
        </div>
        <strong>{stage.label}</strong>
        <p>{stage.detail}</p>
      </section>
      <div className="wm-oracle-focused-status-list" aria-label={t('atlasOracle.statusTitle')}>
        <div><span>{t('atlasOracle.resolution')}</span><strong>{resolved ? outcome : t('atlasOracle.pendingOutcome')}</strong></div>
        <div><span>{t('atlasOracle.events')}</span><strong>{i18n.formatNumber(payload.timeline?.length || 0)} <small>{payload.timeline?.length ? t('atlasOracle.onchain') : t('atlasOracle.noneYet')}</small></strong></div>
        <div className="wide"><span>{t('atlasOracle.dataUpdated')}</span><strong>{updatedAt ? i18n.formatRelativeTime(updatedAt) : t('atlasOracle.notAvailable')}</strong></div>
      </div>
      <div className="wm-oracle-section-heading"><span>{t('atlasOracle.lifecycle')}</span></div>
      <div className="wm-oracle-lifecycle" aria-label={t('atlasOracle.lifecycle')}>
        {lifecycle.map((step) => (
          <div className={`wm-oracle-lifecycle-step${step.done ? ' done' : ''}${step.active ? ' active' : ''}`} key={step.label}>
            <span aria-hidden="true" />
            <strong>{step.label}</strong>
            <em>{step.time ? i18n.formatRelativeTime(step.time) : t('atlasOracle.pending')}</em>
          </div>
        ))}
      </div>
      <article className="wm-oracle-focused-card">
        <div className="wm-oracle-focused-heading">
          <span className={`wm-oracle-stage-dot ${linked ? 'positive' : 'warning'}`} aria-hidden="true" />
          <strong>{t('atlasOracle.resolutionConnection')}</strong>
          <em className={`wm-oracle-awaiting-badge ${linked ? 'linked' : ''}`}>{linked ? t('atlasOracle.linked') : t('atlasOracle.unlinked')}</em>
        </div>
        <div className="wm-oracle-market-title">{marketTitle}</div>
        <div className="wm-oracle-focused-table" aria-label={t('atlasOracle.details')}>
          <div><span>QID</span><strong title={identity.questionId || undefined}>{shortHash(identity.questionId || '', 8, 5) || t('atlasOracle.notAvailable')}</strong></div>
          <div><span>{t('atlasOracle.condition')}</span><strong title={identity.conditionId || undefined}>{shortHash(identity.conditionId || '', 8, 5) || t('atlasOracle.notAvailable')}</strong></div>
          <div><span>Oracle</span><strong title={identity.oracle || undefined}>{shortHash(identity.oracle || '', 8, 5) || t('atlasOracle.notAvailable')}</strong></div>
          <div><span>{t('atlasOracle.source')}</span><strong>{payload.settlementSource || t('atlasOracle.marketRegistry')}</strong></div>
          <div><span>{t('atlasOracle.marketId')}</span><strong>{payload.localMarketId || payload.marketId ? `#${payload.localMarketId || payload.marketId}` : t('atlasOracle.notAvailable')}</strong></div>
        </div>
      </article>
    </div>
  );
}

function OracleFeedPanel({ ctx }: { ctx: PanelRenderContext }) {
  const i18n = useI18n();
  const { t } = i18n;
  const focused = focusedOraclePayload(ctx);
  const view = oracleFeedView(focused, globalOracle(ctx));
  const copy = {
    noActivity: t('atlasOracle.noActivity'),
    finalCount: (count: number) => t('atlasOracle.finalCount', { count: i18n.formatNumber(count) }),
    proposedCount: (count: number) => t('atlasOracle.proposedCount', { count: i18n.formatNumber(count) }),
    boundCount: (count: number) => t('atlasOracle.boundCount', { count: i18n.formatNumber(count) }),
    finalized: t('atlasOracle.finalized'),
    finalizedOutcome: (outcome: string) => t('atlasOracle.finalizedOutcome', { outcome }),
    disputed: t('atlasOracle.disputed'),
    proposed: t('atlasOracle.proposed'),
    requested: t('atlasOracle.requested'),
    event: t('atlasOracle.event'),
    pending: t('atlasOracle.pendingOutcome'),
    unboundEvent: t('atlasOracle.unboundEvent'),
    unbound: t('atlasOracle.unbound'),
    marketNumber: (id: number | string) => t('atlasOracle.marketNumber', { id }),
    formatRelative: (value?: string | null) => i18n.formatRelativeTime(value),
    formatDate: (value?: string | null) => value ? i18n.formatDateTime(value) : '--',
    empty: {
      label: t('atlasShared.standby'),
      detail: t('atlasShared.emptyDetail'),
    },
  };
  return (
    <Panel
      title={focused ? t('atlasOracle.statusTitle') : t('atlasOracle.feedTitle')}
      badge={focused ? (oracleIsLinked(ctx, focused) ? t('atlasOracle.linked') : t('atlasOracle.unlinked')) : t('atlasOracle.live')}
      status="live"
      count={view.events.length}
      className="wm-oracle-feed-panel"
    >
      {focused
        ? (
            <>
              {focusedOracleStatus(ctx, focused, i18n)}
              {view.events.length ? (
                <section className="wm-oracle-recent-events">
                  <div className="wm-oracle-section-heading">
                    <span>{view.source === 'focused' ? t('atlasOracle.recentEvents') : t('atlasOracle.feedTitle')}</span>
                    <em>{i18n.formatNumber(view.events.length)}</em>
                  </div>
                  {oracleList(view.events, 6, view.source === 'focused' ? 'timeline' : 'feed', copy)}
                </section>
              ) : null}
            </>
          )
        : oracleList(view.events, 10, 'feed', copy)}
    </Panel>
  );
}

export const oraclePanelRenderers: PanelRenderMap = {
  'oracle-feed': {
    render: (ctx) => <OracleFeedPanel ctx={ctx} />,
  },
  'oracle-timeline': {
    render: (ctx) => (
      <AiMarketWidePanel ctx={ctx} lens="trend" title="TREND WATCH" badge="TREND" />
    ),
  },
};
