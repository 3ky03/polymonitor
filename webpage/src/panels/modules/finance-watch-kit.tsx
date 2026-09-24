import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import { fetchRuntimeFinanceWatchPanel } from '@/services/api';
import type { RuntimeFinanceWatchItem, RuntimeFinanceWatchPayload } from '@/types';
import type { PanelRenderMap } from '../types';
import { runtimePanelFromRenderer } from './helpers';
import { useSpecialistCopy } from '@/services/specialist-i18n';

type FinancePanelMode = 'rows' | 'feed' | 'grid' | 'sentiment' | 'etf' | 'research';

type FinancePanelConfig = {
  id: string;
  title: string;
  description: string;
  question: string;
  mode?: FinancePanelMode;
  limit?: number;
};

function toneClass(value?: string | null) {
  const tone = String(value || 'neutral').toLowerCase();
  if (tone === 'up') return 'tone-up';
  if (tone === 'down') return 'tone-down';
  if (tone === 'watch') return 'tone-watch';
  return 'tone-neutral';
}

function statusBadge(payload?: RuntimeFinanceWatchPayload | null) {
  const status = String(payload?.status || '').toLowerCase();
  const cacheMode = String(payload?.cacheMode || '').toLowerCase();
  if (cacheMode.includes('stale')) return 'STALE';
  if (status === 'ok') return 'LIVE';
  if (status === 'degraded' || status === 'partial') return 'PARTIAL';
  if (status === 'empty') return 'WARMING';
  return status ? status.toUpperCase() : 'SEED';
}

function itemKey(item: RuntimeFinanceWatchItem, index: number) {
  return String(item.id || item.url || item.title || item.label || index);
}

function itemSignature(item: RuntimeFinanceWatchItem) {
  return [
    item.id,
    item.url,
    item.title,
    item.label,
    item.metricLabel,
    item.secondaryLabel,
    item.changeLabel,
    item.publishedAt,
    item.summary,
    item.institution,
    item.analyst,
    item.rating,
    item.targetPriceLabel,
  ].map((value) => String(value ?? '')).join('::');
}

function itemFreshClass(item: RuntimeFinanceWatchItem, index: number, freshKeys?: Set<string>) {
  return freshKeys?.has(itemKey(item, index)) ? ' is-fresh' : '';
}

function FinanceTags({ tags }: { tags?: string[] }) {
  return (
    <>
      {(tags || []).slice(0, 3).map((tag) => <span className={`wm-finance-tag tag-${String(tag).toLowerCase().replace(/\W+/g, '-')}`} key={tag}>{tag}</span>)}
    </>
  );
}

function ValueBlock({ item }: { item: RuntimeFinanceWatchItem }) {
  return (
    <div className="wm-finance-value">
      <strong>{item.metricLabel || '--'}</strong>
      {item.changeLabel ? <em className={toneClass(item.tone)}>{item.changeLabel}</em> : null}
      {!item.changeLabel && item.secondaryLabel ? <em>{item.secondaryLabel}</em> : null}
    </div>
  );
}

function QuoteRow({ item, fresh = false }: { item: RuntimeFinanceWatchItem; fresh?: boolean }) {
  const { shared } = useSpecialistCopy('finance-shared');
  return (
    <article className={`wm-finance-row${fresh ? ' is-fresh' : ''}`}>
      <div className="wm-finance-main">
        <strong>{item.label || item.title || shared('financeItem', 'Finance item')}</strong>
        <span>{item.symbol || item.metricUnit || '--'}</span>
      </div>
      <ValueBlock item={item} />
      <div className="wm-finance-row-foot">
        <FinanceTags tags={item.tags} />
        {item.secondaryLabel ? <span>{item.secondaryLabel}</span> : null}
      </div>
    </article>
  );
}

function FeedRow({ item, fresh = false }: { item: RuntimeFinanceWatchItem; fresh?: boolean }) {
  const { shared, formatRelativeTime } = useSpecialistCopy('finance-shared');
  const content = (
    <>
      <div className="wm-finance-feed-meta">
        <span className="wm-finance-dot" />
        <b>{item.label || item.source || shared('source', 'SOURCE')}</b>
        <span>{item.source || item.symbol || 'RSS'}</span>
        <FinanceTags tags={item.tags} />
      </div>
      <strong>{item.title || item.label || shared('headlinePending', 'Headline pending')}</strong>
      <em>{item.summary || item.title || shared('summaryPending', 'Summary pending')}</em>
      <div className="wm-finance-feed-foot">
        <span>{formatRelativeTime(item.publishedAt || undefined)}</span>
        {item.url ? <b>{shared('readSource', 'Read source')}</b> : null}
      </div>
    </>
  );
  if (item.url) {
    return <a className={`wm-finance-feed-row${fresh ? ' is-fresh' : ''}`} href={item.url} target="_blank" rel="noreferrer">{content}</a>;
  }
  return <article className={`wm-finance-feed-row${fresh ? ' is-fresh' : ''}`}>{content}</article>;
}

function ResearchRow({ item, fresh = false }: { item: RuntimeFinanceWatchItem; fresh?: boolean }) {
  const { shared, formatRelativeTime } = useSpecialistCopy('finance-shared');
  const fields = [
    { label: shared('code', 'CODE'), value: item.label },
    { label: shared('rating', 'RATING'), value: item.rating || item.metricLabel },
    { label: shared('target', 'TARGET'), value: item.targetPriceLabel || item.secondaryLabel },
    { label: shared('firm', 'FIRM'), value: item.institution || item.source },
    { label: shared('analyst', 'ANALYST'), value: item.analyst },
    { label: shared('time', 'TIME'), value: formatRelativeTime(item.publishedAt || undefined) },
  ].filter((field) => field.value);
  const content = (
    <>
      <div className="wm-finance-research-main">
        <div className="wm-finance-feed-meta">
          <span className="wm-finance-dot" />
          <b>{item.label || 'TICKER'}</b>
          <span>{item.institution || item.source || item.symbol || 'BROKER'}</span>
          <FinanceTags tags={item.tags} />
        </div>
        <strong>{item.title || item.summary || shared('researchPending', 'Research note pending')}</strong>
        <div className="wm-finance-research-fields">
          {fields.slice(0, 6).map((field) => (
            <span key={field.label}>
              <b>{field.label}</b>
              <em>{field.value}</em>
            </span>
          ))}
        </div>
        <em>{item.summary || item.company || shared('researchMetadataPending', 'Original research metadata pending')}</em>
        <div className="wm-finance-feed-foot">
          <span>{formatRelativeTime(item.publishedAt || undefined)}</span>
          {item.url ? <b>{item.reportPageLabel || shared('readReport', 'Read report')}</b> : null}
        </div>
      </div>
      <div className="wm-finance-research-metric">
        <strong className={toneClass(item.tone)}>{item.rating || item.metricLabel || 'REPORT'}</strong>
        <span>{item.targetPriceLabel || item.secondaryLabel || item.metricUnit || 'PT --'}</span>
        {item.changeLabel ? <em className={toneClass(item.tone)}>{item.changeLabel}</em> : null}
      </div>
    </>
  );
  if (item.url) {
    return <a className={`wm-finance-research-row${fresh ? ' is-fresh' : ''}`} href={item.url} target="_blank" rel="noreferrer">{content}</a>;
  }
  return <article className={`wm-finance-research-row${fresh ? ' is-fresh' : ''}`}>{content}</article>;
}

function GridTile({ item, fresh = false }: { item: RuntimeFinanceWatchItem; fresh?: boolean }) {
  return (
    <article className={`wm-finance-grid-tile ${toneClass(item.tone)}${fresh ? ' is-fresh' : ''}`}>
      <span>{item.symbol || item.metricUnit || '--'}</span>
      <strong>{item.label || '--'}</strong>
      <div>
        <b>{item.metricLabel || '--'}</b>
        <em className={toneClass(item.tone)}>{item.changeLabel || '--'}</em>
      </div>
    </article>
  );
}

type FngMetric = { label?: string; value?: string; tone?: string };
type FngCategory = { id?: string; label?: string; score?: number; weight?: number; contribution?: number; tone?: string; degraded?: boolean };

function summaryList<T>(payload: RuntimeFinanceWatchPayload | null | undefined, key: string): T[] {
  const value = payload?.summary?.[key];
  return Array.isArray(value) ? value as T[] : [];
}

function SentimentView({ payload, freshKeys }: { payload?: RuntimeFinanceWatchPayload | null; freshKeys?: Set<string> }) {
  const { shared } = useSpecialistCopy('finance-shared');
  const headline = payload?.headline || {};
  const score = headline.score ?? '--';
  const delta = headline.delta;
  const deltaLabel = typeof delta === 'number'
    ? shared('versusPrevious', '{value} vs prev', { value: `${delta >= 0 ? '+' : ''}${delta.toFixed(1)}` })
    : null;
  const deltaTone = typeof delta === 'number' && delta >= 0 ? 'up' : 'down';
  const scoreNumber = Number(score);
  const angle = Number.isFinite(scoreNumber) ? Math.max(0, Math.min(100, scoreNumber)) * 1.8 - 90 : 0;
  const metrics = summaryList<FngMetric>(payload, 'headerMetrics');
  const categories = summaryList<FngCategory>(payload, 'categories');
  return (
    <div className="wm-finance-sentiment">
      <section className={`wm-finance-sentiment-gauge ${toneClass(headline.tone)}`}>
        <b>{headline.regime || shared('neutral', 'NEUTRAL')}</b>
        <div className="wm-finance-gauge-arc">
          <span className="zone z1" />
          <span className="zone z2" />
          <span className="zone z3" />
          <span className="zone z4" />
          <span className="zone z5" />
          <i style={{ transform: `rotate(${angle}deg)` }} />
          <strong>{score}</strong>
          <em>{headline.label || shared('neutral', 'NEUTRAL')}</em>
        </div>
        {deltaLabel ? <small className={toneClass(deltaTone)}>{deltaLabel}</small> : null}
      </section>
      {metrics.length ? (
        <div className="wm-finance-fng-metrics">
          {metrics.map((metric) => (
            <span key={metric.label}>
              <b className={toneClass(metric.tone)}>{metric.value || '--'}</b>
              <em>{metric.label}</em>
            </span>
          ))}
        </div>
      ) : null}
      {categories.length ? (
        <div className="wm-finance-fng-categories">
          {categories.map((category) => {
            const scoreValue = Number(category.score || 0);
            return (
              <article key={category.id || category.label}>
                <div>
                  <strong>{category.label}</strong>
                  <b className={toneClass(category.tone)}>{category.score}</b>
                </div>
                <span><i className={toneClass(category.tone)} style={{ width: `${Math.max(0, Math.min(100, scoreValue))}%` }} /></span>
                <em>{shared('weightContribution', '{weight}% weight · +{points} pts', {
                  weight: Math.round(Number(category.weight || 0) * 100),
                  points: category.contribution || 0,
                })}{category.degraded ? ` · ${shared('degraded', 'degraded')}` : ''}</em>
              </article>
            );
          })}
        </div>
      ) : null}
      <div className="wm-finance-list compact">
        {(payload?.items || []).map((item, index) => <QuoteRow item={item} fresh={freshKeys?.has(itemKey(item, index))} key={itemKey(item, index)} />)}
      </div>
    </div>
  );
}

function compactMoney(value: unknown) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return '--';
  const sign = number < 0 ? '-' : '';
  const abs = Math.abs(number);
  if (abs >= 1_000_000_000) return `${sign}$${(abs / 1_000_000_000).toFixed(1)}B`;
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(0)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

function EtfView({ payload, freshKeys }: { payload?: RuntimeFinanceWatchPayload | null; freshKeys?: Set<string> }) {
  const { shared } = useSpecialistCopy('finance-shared');
  const items = payload?.items || [];
  const summary = payload?.summary || {};
  const net = Number(summary.netFlowProxyUsd || 0);
  const dirClass = toneClass(net > 0 ? 'up' : net < 0 ? 'down' : 'neutral');
  return (
    <div className="wm-finance-etf">
      <div className="wm-finance-etf-summary">
        <span>{shared('netFlow', 'NET FLOW')}<b className={dirClass}>{net > 0 ? shared('inflow', 'INFLOW') : net < 0 ? shared('outflow', 'OUTFLOW') : shared('neutral', 'NEUTRAL')}</b></span>
        <span>{shared('estimatedFlow', 'EST FLOW')}<b>{compactMoney(summary.netFlowProxyUsd)}</b></span>
        <span>{shared('totalVolume', 'TOTAL VOL')}<b>{compactMoney(summary.totalVolume)}</b></span>
        <span>ETFS<b>{summary.inflowCount || 0}↑ {summary.outflowCount || 0}↓</b></span>
      </div>
      <div className="wm-finance-etf-table">
        <div className="wm-finance-etf-head">
          <span>{shared('code', 'CODE')}</span><span>{shared('issuer', 'ISSUER')}</span><span>{shared('estimatedFlow', 'EST FLOW')}</span><span>{shared('volume', 'VOLUME')}</span><span>{shared('change', 'CHG')}</span>
        </div>
        {items.map((item, index) => (
          <article className={`wm-finance-etf-row${itemFreshClass(item, index, freshKeys)}`} key={itemKey(item, index)}>
            <b>{item.label}</b>
            <span>{item.symbol}</span>
            <em className={toneClass(item.tone)}>{item.metricLabel || '--'}</em>
            <span>{item.secondaryLabel || '--'}</span>
            <em className={toneClass(item.tone)}>{item.changeLabel || '--'}</em>
          </article>
        ))}
      </div>
    </div>
  );
}

function FinanceWatchView({ payload, mode, freshKeys }: { payload?: RuntimeFinanceWatchPayload | null; mode: FinancePanelMode; freshKeys?: Set<string> }) {
  const { shared } = useSpecialistCopy('finance-shared');
  const items = payload?.items || [];
  if (mode === 'sentiment') return <SentimentView payload={payload} freshKeys={freshKeys} />;
  if (mode === 'etf') return <EtfView payload={payload} freshKeys={freshKeys} />;
  if (!items.length) {
    return (
      <div className="wm-finance-empty">
        <span>{shared('standby', 'STANDBY')}</span>
        <strong>{shared('panelWarmingTitle', '{title} warming', { title: payload?.title || shared('financePanel', 'Finance panel') })}</strong>
      </div>
    );
  }
  if (mode === 'feed') {
    return <div className="wm-finance-feed-list">{items.map((item, index) => <FeedRow item={item} fresh={freshKeys?.has(itemKey(item, index))} key={itemKey(item, index)} />)}</div>;
  }
  if (mode === 'research') {
    return <div className="wm-finance-research-list">{items.map((item, index) => <ResearchRow item={item} fresh={freshKeys?.has(itemKey(item, index))} key={itemKey(item, index)} />)}</div>;
  }
  if (mode === 'grid') {
    return <div className="wm-finance-grid-list">{items.map((item, index) => <GridTile item={item} fresh={freshKeys?.has(itemKey(item, index))} key={itemKey(item, index)} />)}</div>;
  }
  return <div className="wm-finance-list">{items.map((item, index) => <QuoteRow item={item} fresh={freshKeys?.has(itemKey(item, index))} key={itemKey(item, index)} />)}</div>;
}

function FinanceWatchPanel({ config, payload }: { config: FinancePanelConfig; payload?: RuntimeFinanceWatchPayload | null }) {
  const { copy, shared } = useSpecialistCopy(config.id);
  const [showHelp, setShowHelp] = useState(false);
  const [freshKeys, setFreshKeys] = useState<Set<string>>(new Set());
  const [updatePulse, setUpdatePulse] = useState(0);
  const previousSignaturesRef = useRef<Record<string, string>>({});
  const previousGeneratedAtRef = useRef<string | undefined>(undefined);
  const mode = config.mode || 'rows';
  const count = useMemo(() => payload?.items?.length || 0, [payload?.items]);
  const items = payload?.items || [];
  const itemStamp = useMemo(() => items.map((item, index) => `${itemKey(item, index)}=${itemSignature(item)}`).join('|'), [items]);
  const title = copy('title', config.title);

  useEffect(() => {
    if (!payload) return;
    const nextSignatures: Record<string, string> = {};
    items.forEach((item, index) => {
      nextSignatures[itemKey(item, index)] = itemSignature(item);
    });
    const previous = previousSignaturesRef.current;
    const previousGeneratedAt = previousGeneratedAtRef.current;
    const hasPrevious = Object.keys(previous).length > 0;
    const changed = hasPrevious ? Object.keys(nextSignatures).filter((key) => previous[key] !== nextSignatures[key]) : [];
    previousSignaturesRef.current = nextSignatures;
    previousGeneratedAtRef.current = payload.generatedAt;
    const generatedAtChanged = Boolean(previousGeneratedAt && payload.generatedAt && previousGeneratedAt !== payload.generatedAt);
    if (!changed.length) {
      setFreshKeys((current) => current.size ? new Set() : current);
      if (generatedAtChanged) setUpdatePulse((value) => value + 1);
      return;
    }
    setFreshKeys(new Set(changed));
    setUpdatePulse((value) => value + 1);
  }, [itemStamp, payload?.generatedAt]);

  useEffect(() => {
    if (!freshKeys.size) return undefined;
    const timer = window.setTimeout(() => setFreshKeys(new Set()), 14000);
    return () => window.clearTimeout(timer);
  }, [freshKeys]);

  return (
    <Panel
      title={title}
      titleControls={<button type="button" className="wm-panel-help-button" aria-label={shared('explainPanel', 'Explain {title}', { title })} aria-expanded={showHelp} onClick={() => setShowHelp((current) => !current)}>?</button>}
      badge={statusBadge(payload)}
      status={payload?.status === 'ok' ? 'live' : 'muted'}
      count={count}
      headerOverlay={showHelp ? (
        <div className="wm-panel-help-popover">
          <strong>{title}</strong>
          <p>{copy('question', config.question)}</p>
        </div>
      ) : null}
      className={`wm-market-panel wm-finance-watch-panel mode-${mode}${updatePulse > 0 ? ` is-dynamic refresh-pulse-${updatePulse % 2}` : ''}`}
      dataPanelId={config.id}
    >
      <FinanceWatchView payload={payload} mode={mode} freshKeys={freshKeys} />
    </Panel>
  );
}

export function createFinanceWatchPanel(config: FinancePanelConfig) {
  const limit = config.limit || 10;
  const renderers: PanelRenderMap = {
    [config.id]: {
      render: (ctx) => <FinanceWatchPanel config={config} payload={ctx.runtimeData[config.id] as RuntimeFinanceWatchPayload | undefined} />,
    },
  };
  return runtimePanelFromRenderer(renderers, {
    id: config.id,
    title: config.title,
    eyebrow: 'finance',
    description: config.description,
    defaultEnabled: true,
  }, {
    tier: 'slow',
    intervalMs: 300000,
    fetchData: () => fetchRuntimeFinanceWatchPanel(config.id, limit),
  });
}
