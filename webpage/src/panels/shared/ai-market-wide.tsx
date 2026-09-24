import { useEffect, useMemo, useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import { fetchMarketWideAiSnapshot } from '@/services/api';
import type {
  MarketGroupItem,
  MarketListItem,
  MarketWideAiInsightLens,
  MarketWideAiInsightPayload,
  MarketWideAiInsightResponse,
  PanelRenderContext,
} from '@/types';
import { formatCompact, formatCurrencyCompact } from './formatters';
import { globalMarkets } from './selectors';
import '@/styles/ai-market-panels.css';

type AiMarketWidePanelProps = {
  ctx: PanelRenderContext;
  lens: MarketWideAiInsightLens;
  title: string;
  badge: string;
};

type MarketCandidate = {
  title: string;
  category: string;
  volume24h: number;
  tradeCount24h: number;
  latestPrice: number;
  outcomeCount: number;
  source: 'market' | 'group';
};

const PANEL_COPY: Record<MarketWideAiInsightLens, {
  source: string;
  eyebrow: string;
  focusLabel: string;
  focusMeta: string;
  watchLabel: string;
  fallbackTitle: string;
}> = {
  overview: {
    source: 'WORLD BRIEF',
    eyebrow: 'Market-wide read',
    focusLabel: 'Focus',
    focusMeta: 'market structure',
    watchLabel: 'Watch next',
    fallbackTitle: 'Market universe loaded',
  },
  special: {
    source: 'TODAY RADAR',
    eyebrow: 'Unusual markets',
    focusLabel: 'Special markets',
    focusMeta: 'ranked signals',
    watchLabel: 'Why it matters',
    fallbackTitle: 'Unusual market watch',
  },
  trend: {
    source: 'MACRO READ',
    eyebrow: 'Trend radar',
    focusLabel: 'Trend thesis',
    focusMeta: 'cross-market',
    watchLabel: 'Next catalysts',
    fallbackTitle: 'Polymarket trend watch',
  },
};

function numericValue(value: string | number | null | undefined) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

function topMarkets(ctx: PanelRenderContext) {
  return globalMarkets(ctx)
    .slice()
    .sort((a, b) => numericValue(b.volume24h) - numericValue(a.volume24h))
    .slice(0, 48);
}

function topGroups(ctx: PanelRenderContext) {
  const groups = ctx.marketGroups.length ? ctx.marketGroups : (ctx.bootstrap?.activeMarketGroupsPreview || []);
  return groups
    .slice()
    .sort((a, b) => numericValue(b.volume24h) - numericValue(a.volume24h))
    .slice(0, 36);
}

function buildMarketWidePayload(ctx: PanelRenderContext, lens: MarketWideAiInsightLens): MarketWideAiInsightPayload {
  return {
    lens,
    markets: topMarkets(ctx),
    marketGroups: topGroups(ctx),
    trades: (ctx.globalTrades || []).slice(0, 24),
    oracle: (ctx.globalOracle || []).slice(0, 24),
    content: (ctx.latestContent || []).slice(0, 12),
    alphaSignals: (ctx.alphaSignals?.items || []).slice(0, 10),
    whaleSignals: (ctx.whaleTrades?.items || []).slice(0, 10),
    suspiciousSignals: (ctx.suspiciousTrades?.items || []).slice(0, 10),
  };
}

function candidatePriceFromGroup(group: MarketGroupItem) {
  const outcomes = group.topOutcomes?.length ? group.topOutcomes : group.outcomes || [];
  const edge = outcomes
    .map((outcome) => numericValue(outcome.yesPrice))
    .filter((price) => price > 0)
    .sort((a, b) => Math.abs(a - 0.5) - Math.abs(b - 0.5))[0];
  return edge || 0;
}

function marketCandidates(payload: MarketWideAiInsightPayload): MarketCandidate[] {
  const markets = payload.markets.map((market: MarketListItem) => ({
    title: market.title,
    category: market.category || 'market',
    volume24h: numericValue(market.volume24h),
    tradeCount24h: numericValue(market.tradeCount24h),
    latestPrice: numericValue(market.latestPrice),
    outcomeCount: numericValue(market.outcomeCount),
    source: 'market' as const,
  }));
  const groups = payload.marketGroups.map((group: MarketGroupItem) => ({
    title: group.title,
    category: group.category || 'market',
    volume24h: numericValue(group.volume24h),
    tradeCount24h: numericValue(group.tradeCount24h),
    latestPrice: candidatePriceFromGroup(group),
    outcomeCount: numericValue(group.outcomeCount || group.outcomes?.length || group.topOutcomes?.length),
    source: 'group' as const,
  }));
  return [...markets, ...groups];
}

function categorySummary(payload: MarketWideAiInsightPayload) {
  const counts = new Map<string, number>();
  marketCandidates(payload).forEach((candidate) => {
    const category = String(candidate.category || 'market').toLowerCase();
    counts.set(category, (counts.get(category) || 0) + 1);
  });
  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([category, count]) => `${category} ${count}`)
    .join(' / ');
}

function specialMarketsFromPayload(payload: MarketWideAiInsightPayload) {
  return marketCandidates(payload)
    .slice()
    .sort((a, b) => candidateScore(b) - candidateScore(a))
    .slice(0, 4)
    .map((candidate) => {
      const closeOdds = candidate.latestPrice >= 0.42 && candidate.latestPrice <= 0.58;
      const active = candidate.volume24h > 0 || candidate.tradeCount24h > 0;
      const complex = candidate.outcomeCount >= 6;
      const trend = closeOdds ? 'Knife-edge odds' : active ? 'Liquidity focus' : complex ? 'Outcome spread' : 'Narrative watch';
      const evidence = closeOdds
        ? `${Math.round(candidate.latestPrice * 100)}%`
        : active
          ? `${formatCurrencyCompact(candidate.volume24h)} 24h`
          : `${formatCompact(candidate.outcomeCount)} outcomes`;
      return {
        title: candidate.title,
        why: closeOdds
          ? 'Odds are balanced enough that new information can quickly reprice the market.'
          : active
            ? 'Visible volume or trade activity makes this a useful read on current attention.'
            : 'This market helps explain where broad narrative interest is forming.',
        trend,
        severity: closeOdds || active ? 'warning' : 'neutral',
        evidence,
      };
    });
}

function candidateScore(candidate: MarketCandidate) {
  const closeOdds = candidate.latestPrice >= 0.42 && candidate.latestPrice <= 0.58;
  return (candidate.volume24h * 3)
    + (candidate.tradeCount24h * 250)
    + (closeOdds ? 3500 : 0)
    + (candidate.outcomeCount * 120);
}

function localMarketWideFallback(payload: MarketWideAiInsightPayload): MarketWideAiInsightResponse {
  const candidates = marketCandidates(payload);
  const volume = candidates.reduce((sum, item) => sum + item.volume24h, 0);
  const totalTrades = payload.trades.length || candidates.reduce((sum, item) => sum + item.tradeCount24h, 0);
  const specialMarkets = specialMarketsFromPayload(payload);
  if (payload.lens === 'special') {
    return {
      status: 'fallback',
      lens: payload.lens,
      model: 'local-market-wide-fallback',
      brief: specialMarkets.length
        ? `Today's unusual-market radar is led by ${specialMarkets[0]?.title || 'the top loaded market'}. Look for close odds, visible volume, and broad outcome sets.`
        : `No single standout market is dominating the loaded dashboard yet.`,
      specialMarkets,
      themes: [
        { label: 'SPECIAL', title: 'Unusual-market radar', summary: 'Markets are ranked by volume, close probabilities, and outcome complexity.', severity: 'neutral', evidence: `${formatCompact(candidates.length)} scanned` },
        { label: 'ATTENTION', title: 'Attention clusters', summary: categorySummary(payload) || 'Category breadth is still loading.', severity: 'neutral', evidence: 'categories' },
      ],
      watchlist: specialMarkets.slice(0, 2).map((item) => ({ title: item.title, reason: item.why, horizon: 'today', severity: item.severity })),
      focus: [
        { label: 'SPECIAL', title: specialMarkets[0]?.title || 'No standout market yet', summary: specialMarkets[0]?.why || 'Waiting for market concentration to appear.', severity: specialMarkets[0]?.severity || 'neutral', evidence: specialMarkets[0]?.evidence || '--' },
        { label: 'BREADTH', title: 'Market set coverage', summary: `${formatCompact(candidates.length)} markets and events are available for anomaly scanning.`, severity: 'neutral', evidence: `${formatCompact(candidates.length)} scanned` },
      ],
      evidence: [`${formatCompact(candidates.length)} scanned`, `${formatCompact(totalTrades)} trade signals`, formatCurrencyCompact(volume), categorySummary(payload) || 'categories loading'],
    };
  }
  if (payload.lens === 'trend') {
    return {
      status: 'fallback',
      lens: payload.lens,
      model: 'local-market-wide-fallback',
      brief: `Polymarket attention is clustering around ${categorySummary(payload) || 'the loaded event set'}. Watch whether isolated markets become category-wide narratives.`,
      specialMarkets,
      themes: [
        { label: 'TREND', title: 'Narrative concentration', summary: categorySummary(payload) || 'Category breadth is still loading.', severity: 'neutral', evidence: `${formatCompact(candidates.length)} scanned` },
        { label: 'CATALYSTS', title: 'News-to-market bridge', summary: `${formatCompact(payload.content.length)} content items and ${formatCompact(payload.alphaSignals?.length || 0)} alpha signals can explain why users rotate attention.`, severity: payload.content.length ? 'positive' : 'warning', evidence: `${formatCompact(payload.content.length)} items` },
        { label: 'SPECIAL', title: 'Standout market pressure', summary: specialMarkets[0]?.title || 'No clear standout market yet.', severity: specialMarkets[0]?.severity || 'neutral', evidence: specialMarkets[0]?.evidence || '--' },
      ],
      watchlist: [
        { title: 'Narrative rotation', reason: 'Watch whether one unusual market pulls volume into adjacent markets.', horizon: '24h', severity: 'neutral' },
        { title: 'Close-probability events', reason: 'Markets near 50/50 tend to react quickly to fresh catalysts.', horizon: 'today', severity: 'warning' },
      ],
      focus: [
        { label: 'TREND', title: 'Narrative concentration', summary: categorySummary(payload) || 'Category breadth is still loading.', severity: 'neutral', evidence: 'categories' },
        { label: 'CATALYSTS', title: 'Catalyst bridge', summary: `${formatCompact(payload.content.length)} content items are loaded for context.`, severity: payload.content.length ? 'positive' : 'warning', evidence: `${formatCompact(payload.content.length)} items` },
      ],
      evidence: [categorySummary(payload) || 'categories loading', `${formatCompact(payload.content.length)} content`, `${formatCompact(totalTrades)} trade signals`, formatCurrencyCompact(volume)],
    };
  }
  return {
    status: 'fallback',
    lens: payload.lens,
    model: 'local-market-wide-fallback',
    brief: `Market-wide dashboard covers ${formatCompact(candidates.length)} markets and events. Top category breadth: ${categorySummary(payload) || 'loading'}.`,
    specialMarkets,
    themes: [
      { label: 'BREADTH', title: 'Market universe', summary: `${formatCompact(candidates.length)} markets/events are loaded.`, severity: 'positive', evidence: `${formatCompact(candidates.length)} scanned` },
      { label: 'CONVERGENCE', title: 'Attention map', summary: categorySummary(payload) || 'Category breadth is still loading.', severity: 'neutral', evidence: 'categories' },
      { label: 'SPECIAL', title: 'Standout market pressure', summary: specialMarkets[0]?.title || 'No clear standout market yet.', severity: specialMarkets[0]?.severity || 'neutral', evidence: specialMarkets[0]?.evidence || '--' },
    ],
    watchlist: specialMarkets.slice(0, 2).map((item) => ({ title: item.title, reason: item.why, horizon: 'today', severity: item.severity })),
    focus: [
      { label: 'BREADTH', title: 'Market universe', summary: `${formatCompact(payload.markets.length)} active markets and ${formatCompact(payload.marketGroups.length)} grouped markets are loaded.`, severity: 'positive', evidence: `${formatCompact(candidates.length)} covered` },
      { label: 'CATALYSTS', title: 'Context feed', summary: `${formatCompact(payload.content.length)} content items and ${formatCompact(payload.alphaSignals?.length || 0)} alpha signals are available.`, severity: payload.content.length ? 'positive' : 'warning', evidence: `${formatCompact(payload.content.length)} items` },
      { label: 'CONVERGENCE', title: 'Category concentration', summary: categorySummary(payload) || 'Category breadth is still loading.', severity: 'neutral', evidence: 'categories' },
    ],
    evidence: [`${formatCompact(payload.markets.length)} markets`, `${formatCompact(payload.marketGroups.length)} groups`, `${formatCompact(totalTrades)} trade signals`, formatCurrencyCompact(volume)],
  };
}

function severityClass(severity?: string | null) {
  const normalized = String(severity || '').toLowerCase();
  if (/critical|risk|high|bear/.test(normalized)) return 'critical';
  if (/warning|watch|medium/.test(normalized)) return 'warning';
  if (/positive|bull|strong/.test(normalized)) return 'positive';
  return 'neutral';
}

function sourceStatus(insight: MarketWideAiInsightResponse) {
  if (insight.source === 'agent-snapshot' && insight.cacheStatus === 'stale-snapshot') return 'AI SNAPSHOT';
  if (insight.source === 'agent-snapshot' || insight.cacheStatus === 'snapshot') return 'AI SNAPSHOT';
  if (insight.viaGateway) return 'AI LIVE';
  if (insight.cacheStatus === 'hit') return 'CACHED';
  return 'LOCAL';
}

export function AiMarketWidePanel({ ctx, lens, title, badge }: AiMarketWidePanelProps) {
  const payload = useMemo(() => buildMarketWidePayload(ctx, lens), [
    ctx.alphaSignals,
    ctx.bootstrap,
    ctx.globalOracle,
    ctx.globalTrades,
    ctx.latestContent,
    ctx.marketGroups,
    ctx.markets,
    ctx.suspiciousTrades,
    ctx.whaleTrades,
    lens,
  ]);
  const fallback = useMemo(() => localMarketWideFallback(payload), [payload]);
  const [snapshotInsight, setSnapshotInsight] = useState<MarketWideAiInsightResponse | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setSnapshotInsight(null);
    fetchMarketWideAiSnapshot(lens)
      .then((response) => {
        if (cancelled) return;
        setSnapshotInsight(response);
      })
      .catch(() => {
        if (!cancelled) setSnapshotInsight(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [lens]);

  const insight = snapshotInsight || fallback;
  const focus = insight.focus?.length ? insight.focus : fallback.focus;
  const specialMarkets = insight.specialMarkets?.length ? insight.specialMarkets : (fallback.specialMarkets || []);
  const themes = insight.themes?.length ? insight.themes : (fallback.themes || focus);
  const watchlist = insight.watchlist?.length ? insight.watchlist : (fallback.watchlist || []);
  const evidence = insight.evidence?.length ? insight.evidence : (fallback.evidence || []);
  const live = insight.status === 'live';
  const snapshot = insight.source === 'agent-snapshot' || insight.cacheStatus === 'snapshot' || insight.cacheStatus === 'stale-snapshot';
  const warming = insight.cacheStatus === 'warming' || insight.cacheStatus === 'warming-in-progress' || insight.status === 'cache-warming';
  const copy = PANEL_COPY[lens];
  const themeLimit = lens === 'trend' ? 4 : 3;
  const specialLimit = lens === 'special' ? 4 : 2;
  const panelCount = Math.max(specialMarkets.length, themes.length, focus.length);
  const primaryCards = lens === 'special' ? specialMarkets.slice(0, specialLimit) : [];
  const narrativeCards = lens === 'overview' ? focus : themes;
  const focusCards = lens === 'special' ? themes.slice(0, 2) : narrativeCards.slice(0, themeLimit);

  return (
    <Panel
      title={title}
      badge={warming ? 'WARMING' : (loading && !snapshot ? 'LOADING' : (snapshot ? badge : (live ? badge : 'LOCAL')))}
      status={snapshot || live ? 'live' : 'muted'}
      count={panelCount}
      className={`wm-market-panel wm-ai-market-panel wm-ai-market-wide-panel wm-ai-${lens}`}
    >
      <div className="wm-ai-insights">
        <section className="wm-ai-insight-hero">
          <div className="wm-ai-insight-source">
            <span><i aria-hidden="true" />{copy.source}</span>
            <b>{sourceStatus(insight)}</b>
          </div>
          <em>{copy.eyebrow}</em>
          <p>{insight.brief || fallback.brief}</p>
        </section>

        {primaryCards.length ? (
          <section className="wm-ai-insight-list wm-ai-special-list" aria-label={`${title} special markets`}>
            <div className="wm-ai-insight-section-head">
              <span>{copy.focusLabel}</span>
              <em>{specialMarkets.length} picked</em>
            </div>
            {primaryCards.map((item, index) => (
              <article className={`wm-ai-insight-market-card ${severityClass(item.severity)}`} key={`${item.title}-${index}`}>
                <div>
                  <span>{item.trend || 'Watch'}</span>
                  <strong>{item.title}</strong>
                  <p>{item.why}</p>
                </div>
                <b>{item.evidence || '--'}</b>
              </article>
            ))}
          </section>
        ) : null}

        <section className="wm-ai-insight-list wm-ai-market-focus" aria-label={`${title} trend signals`}>
          <div className="wm-ai-insight-section-head">
            <span>{lens === 'special' ? copy.watchLabel : copy.focusLabel}</span>
            <em>{insight.viaGateway ? 'gateway' : (insight.model || 'local')}</em>
          </div>
          {focusCards.map((item, index) => (
            <article className={`wm-ai-insight-card ${severityClass(item.severity)}`} key={`${lens}-${item.label}-${index}`}>
              <div className="wm-ai-insight-card-head">
                <span>{item.label}</span>
                <b>{item.evidence || copy.fallbackTitle}</b>
              </div>
              <strong>{item.title || copy.fallbackTitle}</strong>
              <p>{item.summary}</p>
            </article>
          ))}
        </section>

        {watchlist.length ? (
          <section className="wm-ai-insight-list wm-ai-watchlist" aria-label={`${title} watchlist`}>
            <div className="wm-ai-insight-section-head">
              <span>{copy.watchLabel}</span>
              <em>{watchlist.length} items</em>
            </div>
            {watchlist.slice(0, 3).map((item, index) => (
              <article className={`wm-ai-insight-watch ${severityClass(item.severity)}`} key={`${item.title}-${index}`}>
                <span>{item.horizon || 'today'}</span>
                <strong>{item.title}</strong>
                <p>{item.reason}</p>
              </article>
            ))}
          </section>
        ) : null}

        <section className="wm-ai-insight-evidence" aria-label={`${title} evidence`}>
          {evidence.slice(0, 4).map((item) => <span key={item}>{item}</span>)}
        </section>
      </div>
    </Panel>
  );
}
