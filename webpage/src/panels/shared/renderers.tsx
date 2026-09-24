import type { ChartPoint, ContentItem, L2Level, OracleEvent, RuntimeMarketTicker, RuntimeTradeSignal, TradeRow } from '@/types';
import { formatCompact, formatDate, formatPercent, formatRelative, shortHash } from './formatters';

type EmptyStateCopy = {
  label?: string;
  detail?: string;
};

type PriceLineCopy = {
  noHistory?: string;
  low?: (value: string) => string;
  high?: (value: string) => string;
  empty?: EmptyStateCopy;
};

type OracleListCopy = {
  noActivity?: string;
  finalCount?: (count: number) => string;
  proposedCount?: (count: number) => string;
  boundCount?: (count: number) => string;
  finalized?: string;
  finalizedOutcome?: (outcome: string) => string;
  disputed?: string;
  proposed?: string;
  requested?: string;
  event?: string;
  pending?: string;
  unboundEvent?: string;
  unbound?: string;
  marketNumber?: (id: number | string) => string;
  formatRelative?: (value?: string | null) => string;
  formatDate?: (value?: string | null) => string;
  empty?: EmptyStateCopy;
};

type ContentListCopy = {
  untitled?: string;
  readSource?: string;
  formatDate?: (value?: string | null) => string;
  empty?: EmptyStateCopy;
};

function emptyState(message: string, copy: EmptyStateCopy = {}) {
  return (
    <div className="wm-empty wm-empty-card">
      <span>{copy.label || 'Standby'}</span>
      <strong>{message}</strong>
      <em>{copy.detail || 'The panel will update automatically when this source has rows for the selected market.'}</em>
    </div>
  );
}

function priceLine(points: ChartPoint[], copy: PriceLineCopy = {}) {
  const isRawValueSeries = points.some((point) => point.value !== undefined && point.value !== null);
  const clean = points
    .map((point, index) => ({ index, value: Number(isRawValueSeries ? point.value : point.yesPrice) }))
    .filter((point) => Number.isFinite(point.value));

  if (clean.length < 2) return emptyState(copy.noHistory || 'No price history loaded yet.', copy.empty);

  const width = 520;
  const height = 180;
  const min = Math.min(...clean.map((point) => point.value));
  const max = Math.max(...clean.map((point) => point.value));
  const span = max - min || 1;
  const path = clean
    .map((point, idx) => {
      const x = (point.index / Math.max(clean.length - 1, 1)) * width;
      const y = height - ((point.value - min) / span) * height;
      return `${idx === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(' ');
  const areaPath = `${path} L ${width} ${height} L 0 ${height} Z`;
  const axisValue = (value: number) => (isRawValueSeries ? `$${formatCompact(value)}` : formatPercent(value));

  return (
    <div className="wm-line-chart">
      <svg viewBox={`0 0 ${width} ${height}`} className="wm-line-chart-svg" preserveAspectRatio="none">
        <defs>
          <linearGradient id="wmPriceLine" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="#39ff73" />
            <stop offset="55%" stopColor="#88ffbf" />
            <stop offset="100%" stopColor="#f5f7f7" />
          </linearGradient>
          <linearGradient id="wmPriceArea" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor="rgba(57, 255, 115, 0.26)" />
            <stop offset="100%" stopColor="rgba(57, 255, 115, 0.02)" />
          </linearGradient>
        </defs>
        <path d={areaPath} fill="url(#wmPriceArea)" />
        <path d={path} fill="none" stroke="url(#wmPriceLine)" strokeWidth="2.8" strokeLinecap="round" />
      </svg>
      <div className="wm-line-axis">
        <span>{copy.low?.(axisValue(min)) || `LOW ${axisValue(min)}`}</span>
        <span>{copy.high?.(axisValue(max)) || `HIGH ${axisValue(max)}`}</span>
      </div>
    </div>
  );
}

function sparkline(points: Array<{ value?: number | null }>, color = '#39ff73') {
  const clean = points
    .map((point, index) => ({ index, value: Number(point.value) }))
    .filter((point) => Number.isFinite(point.value));
  if (clean.length < 2) return null;
  const width = 140;
  const height = 40;
  const min = Math.min(...clean.map((point) => point.value));
  const max = Math.max(...clean.map((point) => point.value));
  const span = max - min || 1;
  const path = clean
    .map((point, idx) => {
      const x = (point.index / Math.max(clean.length - 1, 1)) * width;
      const y = height - ((point.value - min) / span) * height;
      return `${idx === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(' ');
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="wm-mini-spark" preserveAspectRatio="none">
      <path d={path} fill="none" stroke={color} strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}

function tradeNotional(trade: TradeRow) {
  const size = Number(trade.size);
  const price = Number(trade.price);
  if (!Number.isFinite(size) || !Number.isFinite(price)) return null;
  return size * price;
}

function tradeActor(trade: TradeRow) {
  return shortHash(trade.taker || trade.maker || trade.txHash || '', 7, 4);
}

function tradeActorFull(trade: TradeRow) {
  return trade.taker || trade.maker || trade.txHash || '--';
}

function formatDateExact(value?: string | null) {
  if (!value) return '--';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('en-US', {
    month: 'short',
    day: '2-digit',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  });
}

function tradeList(trades: TradeRow[], limit = 8) {
  if (!trades.length) return emptyState('Waiting for trade rows.');
  return (
    <div className="wm-tape-list">
      {trades.slice(0, limit).map((trade) => {
        const side = String(trade.side || '').toUpperCase();
        const tone = side === 'BUY' ? 'positive' : 'critical';
        return (
          <article className={`wm-tape-row ${tone}`} key={`${trade.txHash}-${trade.logIndex}`}>
            <div className="wm-tape-time">{formatRelative(trade.timestamp || null)}</div>
            <div className="wm-tape-party">{tradeActor(trade)}</div>
            <div className="wm-tape-main">
              <div className="wm-tape-title">{trade.marketTitle || `Market #${trade.marketId || '--'}`}</div>
              <div className="wm-tape-meta">
                <span>{shortHash(trade.txHash)}</span>
                <span>{formatDate(trade.timestamp || null)}</span>
              </div>
            </div>
            <div className="wm-tape-action">
              <span className={`wm-chip ${tone}`}>{side || 'FLOW'}</span>
              <em>{String(trade.outcome || '--').toUpperCase()}</em>
            </div>
            <div className="wm-tape-price">
              <strong>{formatPercent(trade.price)}</strong>
              <span>{tradeNotional(trade) ? `$${formatCompact(tradeNotional(trade))}` : formatCompact(trade.size)}</span>
            </div>
          </article>
        );
      })}
    </div>
  );
}

function tradePriceCents(value?: string | number | null) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return `${Math.round(numeric * 100)}c`;
}

function polyscanTxUrl(txHash?: string | null) {
  const clean = String(txHash || '').trim();
  if (!clean) return null;
  const normalized = clean.startsWith('0x') ? clean : `0x${clean}`;
  return /^0x[a-fA-F0-9]{64}$/.test(normalized) ? `https://polygonscan.com/tx/${normalized}` : null;
}

function normalizedTxHash(txHash?: string | null) {
  const clean = String(txHash || '').trim();
  if (!clean) return '';
  return clean.startsWith('0x') ? clean : `0x${clean}`;
}

function orderfilledSideLabel(rawSide?: string | null) {
  const side = String(rawSide || '').trim().toUpperCase();
  return side === 'BUY' || side === 'SELL' ? side : 'TRADE';
}

function orderfilledList(
  trades: TradeRow[],
  limit = 8,
  resolveMarketTitle?: (trade: TradeRow) => string | null | undefined,
  options: { compact?: boolean } = {},
) {
  if (!trades.length) return emptyState('Waiting for trade rows.');
  const visibleTrades = trades.slice(0, limit);
  const visibleSides = new Set(visibleTrades.map((trade) => orderfilledSideLabel(trade.side)).filter((side) => side === 'BUY' || side === 'SELL'));
  const showDirectionalSide = visibleSides.size > 1;
  return (
    <div className="wm-orderfilled-list">
      {visibleTrades.map((trade) => {
        const side = showDirectionalSide ? orderfilledSideLabel(trade.side) : 'TRADE';
        const outcome = String(trade.outcome || '--').toUpperCase();
        const tone = side === 'BUY' ? 'positive' : side === 'SELL' ? 'critical' : 'neutral';
        const actor = tradeActor(trade);
        const actorFull = tradeActorFull(trade);
        const marketTitle = resolveMarketTitle?.(trade) || trade.marketTitle || null;
        const txUrl = polyscanTxUrl(trade.txHash);
        const txHash = normalizedTxHash(trade.txHash);
        const rowKey = `${trade.txHash || 'trade'}-${trade.logIndex ?? 'x'}`;
        const notional = tradeNotional(trade);
        const rowContent = (
          <>
            <div className="wm-orderfilled-top">
              <div className="wm-orderfilled-headline">
                <span className={`wm-chip ${tone}`}>{side || 'FLOW'}</span>
                <span className={`wm-orderfilled-outcome ${outcome === 'YES' ? 'yes' : outcome === 'NO' ? 'no' : ''}`}>{outcome}</span>
                <strong>{tradePriceCents(trade.price)}</strong>
                <em>{notional ? `$${formatCompact(notional)}` : '$--'}</em>
              </div>
            </div>
            {!options.compact ? <div className="wm-orderfilled-title">{marketTitle || 'Untitled market'}</div> : null}
            <div className="wm-orderfilled-meta">
              <span>{formatRelative(trade.timestamp || null)}</span>
              <span className="wm-orderfilled-hash">{txHash ? shortHash(txHash, 8, 6) : actor}</span>
              <span className={`wm-orderfilled-link ${txUrl ? 'ready' : 'disabled'}`}>{txUrl ? 'Polyscan' : 'No tx'}</span>
            </div>
            <div className="wm-orderfilled-tooltip" role="tooltip" aria-hidden="true">
              <div className="wm-orderfilled-tooltip-title">{marketTitle || `Market #${trade.marketId || '--'}`}</div>
              <div className="wm-orderfilled-tooltip-row">
                <span>Address</span>
                <strong>{actorFull}</strong>
              </div>
              <div className="wm-orderfilled-tooltip-row">
                <span>Tx</span>
                <strong>{trade.txHash || '--'}</strong>
              </div>
              <div className="wm-orderfilled-tooltip-row">
                <span>Time</span>
                <strong>{formatDateExact(trade.timestamp)}</strong>
              </div>
              {trade.marketId ? (
                <div className="wm-orderfilled-tooltip-row">
                  <span>Market</span>
                  <strong>#{trade.marketId}</strong>
                </div>
              ) : null}
            </div>
          </>
        );
        if (txUrl) {
          return (
            <a
              className={`wm-orderfilled-row ${tone}`}
              href={txUrl}
              key={rowKey}
              target="_blank"
              rel="noreferrer"
              aria-label={`Open OrderFilled transaction ${trade.txHash} on Polyscan`}
            >
              {rowContent}
            </a>
          );
        }
        return (
          <article className={`wm-orderfilled-row ${tone}`} key={rowKey}>
            {rowContent}
          </article>
        );
      })}
    </div>
  );
}

function oracleTone(status?: string | null) {
  const normalized = String(status || '').toLowerCase();
  if (normalized.includes('sett')) return 'positive';
  if (normalized.includes('disput') || normalized.includes('reject')) return 'critical';
  if (normalized.includes('propos')) return 'warning';
  return 'muted';
}

function oracleStageLabel(event: OracleEvent, copy: OracleListCopy = {}) {
  const status = String(event.eventStatus || '').toLowerCase();
  const outcome = String(event.effectiveSettlementOutcome || event.settlementOutcome || '').toUpperCase();
  if (status.includes('settle')) return outcome && outcome !== 'UNKNOWN'
    ? copy.finalizedOutcome?.(outcome) || `Finalized ${outcome}`
    : copy.finalized || 'Finalized';
  if (status.includes('dispute')) return copy.disputed || 'Disputed';
  if (status.includes('propose')) return copy.proposed || 'Proposed';
  if (status.includes('request')) return copy.requested || 'Requested';
  return event.eventStatus || copy.event || 'Oracle event';
}

function oracleOutcomeLabel(event: OracleEvent, copy: OracleListCopy = {}) {
  const outcome = String(event.effectiveSettlementOutcome || event.settlementOutcome || '').toUpperCase();
  if (outcome && outcome !== 'UNKNOWN') return outcome;
  const price = event.settledPrice ?? event.proposedPrice;
  const numeric = Number(price);
  if (Number.isFinite(numeric)) {
    if (numeric >= 0.999 || numeric >= 999999999999999999) return 'YES';
    if (numeric <= 0.001) return 'NO';
    if (Math.abs(numeric - 0.5) < 0.001) return 'CANCELLED';
    return formatPercent(numeric);
  }
  return copy.pending || 'Pending';
}

function oracleActor(event: OracleEvent) {
  return event.proposer || event.disputer || event.requester || event.sourceOracle || event.sourceAdapter || event.txHash || '';
}

function oracleTx(event: OracleEvent) {
  return event.settlementTransaction || event.proposalTransaction || event.txHash || '';
}

function oracleList(events: OracleEvent[], limit = 8, mode: 'feed' | 'timeline' = 'feed', copy: OracleListCopy = {}) {
  if (!events.length) return emptyState(copy.noActivity || 'No oracle activity loaded.', copy.empty);
  const visible = events.slice(0, limit);
  const settledCount = visible.filter((event) => String(event.eventStatus || '').toLowerCase().includes('settle')).length;
  const proposedCount = visible.filter((event) => String(event.eventStatus || '').toLowerCase().includes('propose')).length;
  const boundCount = visible.filter((event) => event.isBound !== false && event.marketId).length;
  return (
    <div className={`wm-oracle-shell ${mode}`}>
      <div className="wm-oracle-summary-strip">
        <span>{copy.finalCount?.(settledCount) || <><strong>{settledCount}</strong> final</>}</span>
        <span>{copy.proposedCount?.(proposedCount) || <><strong>{proposedCount}</strong> proposed</>}</span>
        <span>{copy.boundCount?.(boundCount) || <><strong>{boundCount}</strong> bound</>}</span>
      </div>
      <div className="wm-oracle-list">
      {visible.map((event, index) => {
        const status = String(event.eventStatus || '').toLowerCase();
        const tone = oracleTone(event.eventStatus);
        const stage = oracleStageLabel(event, copy);
        const outcome = oracleOutcomeLabel(event, copy);
        const actor = oracleActor(event);
        const tx = oracleTx(event);
        const lifecycleClass = status.includes('settle') ? 'settle' : status.includes('dispute') ? 'dispute' : status.includes('propose') ? 'propose' : 'request';
        return (
          <article className={`wm-oracle-event-card ${tone} ${lifecycleClass}`} key={`${event.id || index}-${event.blockNumber || index}`}>
            <div className="wm-oracle-event-top">
              <div className="wm-oracle-stage">
                <span className={`wm-oracle-stage-dot ${tone}`} aria-hidden="true" />
                <strong>{stage}</strong>
              </div>
              <span className={`wm-status-pill ${tone}`}>{event.eventStatus || 'event'}</span>
            </div>
            <div className="wm-oracle-market-title">{event.marketTitle || event.marketSlug || copy.unboundEvent || 'Unbound oracle event'}</div>
            <div className="wm-oracle-result-row">
              <span className={`wm-oracle-outcome ${String(outcome).toLowerCase()}`}>{outcome}</span>
              <span>{event.completionStatus || (event.isFinal ? 'SETTLED' : 'PENDING')}</span>
              <span>{event.isBound === false || !event.marketId
                ? copy.unbound || 'UNBOUND'
                : copy.marketNumber?.(event.marketId) || `MKT #${event.marketId}`}</span>
            </div>
            <div className="wm-oracle-event-meta">
              <span>{copy.formatRelative?.(event.eventTime || null) || formatRelative(event.eventTime || null)}</span>
              <span>{copy.formatDate?.(event.eventTime || null) || formatDate(event.eventTime || null)}</span>
            </div>
            <div className="wm-oracle-proof-grid">
              <span>Oracle <strong>{shortHash(actor, 8, 5) || '--'}</strong></span>
              <span>Tx <strong>{shortHash(tx, 8, 5) || '--'}</strong></span>
              <span>QID <strong>{shortHash(event.questionId || event.conditionId || '', 8, 5) || '--'}</strong></span>
            </div>
          </article>
        );
      })}
      </div>
    </div>
  );
}

function contentTone(type?: string | null) {
  const normalized = String(type || '').toLowerCase();
  if (normalized.includes('video')) return 'video';
  if (normalized.includes('report')) return 'report';
  if (normalized.includes('research')) return 'research';
  return 'news';
}

type ContentTag = {
  label: string;
  tone: string;
};

function contentText(item: ContentItem) {
  return `${item.source || ''} ${item.category || ''} ${item.title || ''} ${item.summary || ''} ${item.contentType || ''}`.toLowerCase();
}

function firstMatchingTag(text: string): ContentTag | null {
  const checks: Array<[RegExp, ContentTag]> = [
    [/\b(election|vote|voting|poll|nominee|primary|ballot)\b/, { label: 'ELECTION', tone: 'election' }],
    [/\b(president|senate|congress|parliament|mayor|minister|trump|biden|republican|democrat|party|cabinet)\b/, { label: 'POLITICS', tone: 'politics' }],
    [/\b(nba|nfl|mlb|nhl|soccer|football|ufc|tennis|spurs|thunder|knicks|cavs|cavaliers|sabre|valorant|counter-strike|esports|premier league)\b/, { label: 'SPORTS', tone: 'sports' }],
    [/\b(bitcoin|btc|ethereum|eth|solana|xrp|crypto|dogecoin|token|stablecoin|blockchain)\b/, { label: 'CRYPTO', tone: 'crypto' }],
    [/\b(fed|inflation|cpi|rates?|tariff|gdp|jobs|unemployment|recession|economy|economic|bond|oil|gas|gold|silver|crude)\b/, { label: 'ECONOMIC', tone: 'economic' }],
    [/\b(ai|chip|semiconductor|nvidia|openai|google|microsoft|tesla|robot|cyber|data center|tech)\b/, { label: 'TECH', tone: 'tech' }],
    [/\b(ebola|virus|vaccine|health|hospital|disease|outbreak|infection|pandemic)\b/, { label: 'HEALTH', tone: 'health' }],
    [/\b(war|strike|missile|military|attack|drone|genocide|conflict|invasion|hostage)\b/, { label: 'CONFLICT', tone: 'conflict' }],
    [/\b(iran|israel|gaza|ukraine|russia|china|taiwan|sanction|ceasefire|talks|summit|diplomat|treaty)\b/, { label: 'DIPLOMATIC', tone: 'diplomatic' }],
    [/\b(weather|storm|heat|rain|hurricane|temperature|wildfire|flood)\b/, { label: 'WEATHER', tone: 'weather' }],
    [/\b(earnings|stock|shares|dow|nasdaq|s&p|finance|market|trading)\b/, { label: 'FINANCE', tone: 'finance' }],
    [/\b(movie|music|culture|celebrity|award|streaming)\b/, { label: 'CULTURE', tone: 'culture' }],
  ];
  return checks.find(([pattern]) => pattern.test(text))?.[1] || null;
}

function contentAlertTags(text: string): ContentTag[] {
  if (/\b(breaking|urgent|emergency|critical|killed|death toll|deadly|explosion|invasion|missile|strike|attack|outbreak|genocide|default|crash)\b/.test(text)) {
    return [{ label: 'ALERT', tone: 'alert' }];
  }
  if (/\b(warns?|warning|risk|could|may|threat|probe|lawsuit|charged|ban|delay|volatility|re-escalation)\b/.test(text)) {
    return [{ label: 'CAUTION', tone: 'caution' }];
  }
  if (/\b(ongoing|live|continues?|developing|talks|ceasefire|trial|campaign)\b/.test(text)) {
    return [{ label: 'ONGOING', tone: 'ongoing' }];
  }
  return [];
}

function contentTags(item: ContentItem, tone: string): ContentTag[] {
  const text = contentText(item);
  const tags = contentAlertTags(text);
  const theme = firstMatchingTag(text);
  if (theme) tags.push(theme);
  if (tone !== 'news') tags.push({ label: tone.toUpperCase(), tone });
  if (!tags.length) tags.push({ label: 'NEWS', tone: 'news' });
  return tags.slice(0, 3);
}

function contentPriority(tags: ContentTag[]) {
  if (tags.some((tag) => tag.tone === 'alert')) return 'alert';
  if (tags.some((tag) => tag.tone === 'caution')) return 'caution';
  if (tags.some((tag) => tag.tone === 'ongoing')) return 'ongoing';
  return tags[0]?.tone || 'news';
}

function cleanIntelSummary(value?: string | null) {
  const text = String(value || '').trim();
  if (!text || /^(null|undefined|none)$/i.test(text)) return '';
  return text;
}

function cleanIntelSource(value?: string | null, fallback?: string | null) {
  const raw = String(value || fallback || 'intel').trim();
  if (!raw) return 'intel';
  const withoutProvider = raw
    .replace(/^(tavily|brave search|serpapi|gdelt doc|topic search|market search)\s*:\s*/i, '')
    .replace(/^google news\s*:\s*/i, '');
  const parts = withoutProvider.split(':').map((part) => part.trim()).filter(Boolean);
  return parts[parts.length - 1] || withoutProvider || raw;
}

function contentList(items: ContentItem[], emptyMessage: string, maxItems = 20, copy: ContentListCopy = {}) {
  if (!items.length) return emptyState(emptyMessage, copy.empty);
  return (
    <div className="wm-intel-list">
      {items.slice(0, maxItems).map((item, index) => {
        const tone = contentTone(item.contentType);
        const tags = contentTags(item, tone);
        const priority = contentPriority(tags);
        const summary = cleanIntelSummary(item.summary);
        const source = cleanIntelSource(item.source, item.contentType);
        return (
          <a className={`wm-intel-card ${tone} priority-${priority}`} href={item.url || '#'} target="_blank" rel="noreferrer" key={`${item.url}-${index}`}>
            <span className="wm-intel-rank">{String(index + 1).padStart(2, '0')}</span>
            <div className="wm-intel-topline">
              <div className="wm-intel-meta">
                <span className="wm-intel-dot" aria-hidden="true" />
                <span className="wm-news-source">{source}</span>
                {tags.map((tag) => (
                  <span className={`wm-intel-tag ${tag.tone}`} key={`${item.url || item.title}-${tag.label}`}>{tag.label}</span>
                ))}
              </div>
            </div>
            <div className="wm-news-title">{item.title || copy.untitled || 'Untitled item'}</div>
            {summary ? <p className="wm-intel-summary">{summary}</p> : null}
            <div className="wm-news-meta">
              <span>{copy.formatDate?.(item.publishedAt || null) || formatDate(item.publishedAt || null)}</span>
              <b>{copy.readSource || 'Read source'}</b>
            </div>
          </a>
        );
      })}
    </div>
  );
}

function summaryTone(value: string) {
  const normalized = String(value).toLowerCase();
  if (normalized.includes('ok') || normalized.includes('online') || normalized.includes('live') || normalized.includes('active')) return 'positive';
  if (normalized.includes('off') || normalized.includes('down') || normalized.includes('error')) return 'critical';
  if (normalized.includes('warn') || normalized.includes('delay') || normalized.includes('pending')) return 'warning';
  return 'muted';
}

function summaryRows(rows: Array<{ label: string; value: string }>) {
  return (
    <div className="wm-summary-grid">
      {rows.map((row) => (
        <div className={`wm-summary-row ${summaryTone(row.value)}`} key={row.label}>
          <span>{row.label}</span>
          <strong>{row.value}</strong>
        </div>
      ))}
    </div>
  );
}

function marketTickerGrid(items: RuntimeMarketTicker[], emptyMessage: string) {
  if (!items.length) return emptyState(emptyMessage);
  return (
    <div className="wm-market-grid">
      {items.map((item) => {
        const isUp = (item.changePercent || 0) >= 0;
        return (
          <article className="wm-market-ticker-card" key={item.symbol}>
            <div className="wm-market-ticker-top">
              <span>{item.label}</span>
              <strong>{item.price == null ? '--' : Number(item.price).toLocaleString('en-US', { maximumFractionDigits: 2 })}</strong>
            </div>
            <div className="wm-market-spark-wrap">{sparkline(item.points || [], isUp ? '#39ff73' : '#ff6464')}</div>
            <div className={`wm-market-change ${isUp ? 'up' : 'down'}`}>
              {item.changePercent == null ? '--' : `${item.changePercent > 0 ? '+' : ''}${item.changePercent.toFixed(2)}%`}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function marketTickerList(items: RuntimeMarketTicker[], emptyMessage: string) {
  if (!items.length) return emptyState(emptyMessage);
  return (
    <div className="wm-ticker-list">
      {items.map((item) => {
        const isUp = (item.changePercent || 0) >= 0;
        return (
          <article className="wm-ticker-row" key={item.symbol}>
            <div className="wm-ticker-meta">
              <strong>{item.label}</strong>
              <span>{item.symbol}</span>
            </div>
            <div className="wm-ticker-spark">{sparkline(item.points || [], isUp ? '#39ff73' : '#ff6464')}</div>
            <div className="wm-ticker-value">
              <strong>{item.price == null ? '--' : Number(item.price).toLocaleString('en-US', { maximumFractionDigits: 2 })}</strong>
              <span className={isUp ? 'up' : 'down'}>
                {item.changePercent == null ? '--' : `${item.changePercent > 0 ? '+' : ''}${item.changePercent.toFixed(2)}%`}
              </span>
            </div>
          </article>
        );
      })}
    </div>
  );
}


function signalBias(item: RuntimeTradeSignal) {
  const explicit = String(item.bias || '').toLowerCase();
  if (explicit === 'bearish' || explicit === 'bullish') return explicit;
  return String(item.side || '').toUpperCase() === 'SELL' ? 'bearish' : 'bullish';
}

function signalAction(item: RuntimeTradeSignal) {
  const side = item.action?.label || (String(item.side || '').toUpperCase() === 'SELL' ? 'Sell' : 'Buy');
  const outcome = item.action?.outcome || (String(item.outcome || '').toUpperCase() === 'NO' ? 'No' : 'Yes');
  return { side, outcome };
}

function alphaSignalList(items: RuntimeTradeSignal[], emptyMessage: string, onMarketSelect?: (marketId: number) => void) {
  if (!items.length) return emptyState(emptyMessage);
  return (
    <div style={{ fontFamily: 'var(--font-mono), monospace', width: '100%' }}>
      {items.map((item, index) => {
        const isCluster = (item.addresses?.length || 0) > 1 || String(item.sourceLabel || '').toLowerCase().includes('cluster');
        const icon = isCluster ? '👥' : '🐳';
        const sourceName = item.sourceLabel ? item.sourceLabel.toUpperCase() : (isCluster ? 'CLUSTER' : 'WHALE');
        const bias = signalBias(item);
        const isBull = bias === 'bullish';
        
        // Exact PolyWorld colors
        const dirColor = isBull ? '#22c55e' : '#ef4444';
        const dirArrow = isBull ? '▲' : '▼';
        
        // Assume all signals are 'STR' for now as in the original mock if not provided
        const sColor = '#ff4444';
        const sBg = 'rgba(255,68,68,0.12)';
        const strengthLabel = 'STR';
        
        const action = signalAction(item);

        let timeStr = formatRelative(item.timestamp || null);
        timeStr = timeStr.replace(' minutes ago', 'm').replace(' minutes', 'm').replace(' hours ago', 'h').replace(' hours', 'h').replace(' seconds ago', 's').replace(' seconds', 's');
        if (timeStr.includes('just now')) timeStr = '1m';

        const metrics = item.metrics || null;
        const volume = metrics?.totalNotional || item.notional || 0;
        const count = metrics?.tradeCount || 1;
        const wallets = metrics?.accountCount || item.addresses?.length || 1;
        const prob = formatPercent(metrics?.currentProbability || item.price || 0);

        return (
          <div 
            key={`${item.title || item.marketTitle || 'signal'}-${index}`}
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: '6px',
              padding: '6px',
              borderBottom: '1px solid rgba(255,255,255,0.05)',
              cursor: 'pointer',
              background: 'rgba(255,68,68,0.03)'
            }}
            onClick={() => item.marketId && onMarketSelect?.(item.marketId)}
          >
            {/* Left Column: Icon & Strength Badge */}
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0, width: '28px', paddingTop: '2px' }}>
              <span style={{ fontSize: '13px', lineHeight: 1 }}>{icon}</span>
              <span style={{
                fontSize: '8px',
                fontWeight: 'bold',
                borderRadius: '2px',
                padding: '0 2px',
                marginTop: '2px',
                lineHeight: '14px',
                background: sBg,
                color: sColor
              }}>
                {strengthLabel}
              </span>
            </div>

            {/* Right Column */}
            <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: '2px' }}>
              
              {/* Row 1: Header */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px', marginBottom: '2px' }}>
                <span style={{ fontFamily: 'var(--font-mono), monospace', fontSize: '10px', fontWeight: 900, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'rgba(255,255,255,0.54)' }}>
                  {sourceName}
                </span>
                <span style={{ fontFamily: 'var(--font-mono), monospace', fontSize: '10px', fontWeight: 900, color: dirColor }}>
                  {dirArrow} {bias.toUpperCase()}
                </span>
                <span style={{ fontFamily: 'var(--font-mono), monospace', fontSize: '10px', fontWeight: 800, color: 'rgba(255,255,255,0.36)', marginLeft: 'auto', flexShrink: 0 }}>
                  {timeStr}
                </span>
              </div>

              {/* Row 2: Summary */}
              <div style={{ fontFamily: 'var(--font-mono), monospace', fontSize: '11px', fontWeight: 800, color: 'rgba(255,255,255,0.84)', lineHeight: 1.28, display: '-webkit-box', WebkitBoxOrient: 'vertical', WebkitLineClamp: 3, overflow: 'hidden' }}>
                {item.headline || item.summary || item.title || 'Signal activity detected'}
              </div>

              {/* Row 3: Market/Outcome Box */}
              {(item.marketTitle || (action.outcome && action.outcome !== 'Yes' && action.outcome !== 'No')) && (
                <div style={{
                  fontFamily: 'var(--font-mono), monospace',
                  fontSize: '10px',
                  fontWeight: 900,
                  marginTop: '2px',
                  padding: '2px 4px',
                  display: 'inline-block',
                  borderRadius: '2px',
                  width: 'fit-content',
                  background: isBull ? 'rgba(34,197,94,0.1)' : 'rgba(255,68,68,0.1)',
                  color: dirColor,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  maxWidth: '100%'
                }}>
                  {dirArrow} {item.marketTitle || 'Market'} {action.outcome && action.outcome !== 'Yes' && action.outcome !== 'No' ? ` · ${action.outcome}` : ''}
                </div>
              )}

              {/* Row 4: Metrics & Action */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '2px', flexWrap: 'wrap' }}>
                {Number(volume) > 0 && (
                  <div style={{ display: 'flex', flexDirection: 'column', fontSize: '10px', color: 'rgba(255,255,255,0.5)', whiteSpace: 'nowrap' }}>
                    <span style={{ color: 'rgba(255,255,255,0.8)', fontWeight: 900 }}>${formatCompact(volume)}</span>
                    <span style={{ marginTop: '-2px' }}>vol</span>
                  </div>
                )}
                {Number(count) > 0 && (
                  <div style={{ display: 'flex', flexDirection: 'column', fontSize: '10px', color: 'rgba(255,255,255,0.5)', whiteSpace: 'nowrap' }}>
                    <span style={{ color: 'rgba(255,255,255,0.8)', fontWeight: 900 }}>{count}</span>
                    <span style={{ marginTop: '-2px' }}>trades</span>
                  </div>
                )}
                {Number(wallets) > 0 && (
                  <div style={{ display: 'flex', flexDirection: 'column', fontSize: '10px', color: 'rgba(255,255,255,0.5)', whiteSpace: 'nowrap' }}>
                    <span style={{ color: 'rgba(255,255,255,0.8)', fontWeight: 900 }}>{wallets}</span>
                    <span style={{ marginTop: '-2px' }}>wallet(s)</span>
                  </div>
                )}
                
                <span style={{ fontSize: '10px', color: 'rgba(255,255,255,0.5)', alignSelf: 'center', margin: 'auto 0' }}>
                  @{prob}
                </span>

                <button style={{
                  marginLeft: 'auto',
                  fontSize: '9px',
                  fontFamily: 'var(--font-mono), monospace',
                  fontWeight: 900,
                  padding: '4px 6px',
                  borderRadius: '2px',
                  cursor: 'pointer',
                  border: 'none',
                  whiteSpace: 'nowrap',
                  background: isBull ? 'rgba(34,197,94,0.15)' : 'rgba(255,68,68,0.15)',
                  color: dirColor
                }}>
                  {action.side === 'buy' ? 'Buy' : 'Sell'} {action.outcome || (isBull ? 'YES' : 'NO')}
                </button>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function whaleTrackerList(items: RuntimeTradeSignal[], emptyMessage: string, onMarketSelect?: (marketId: number) => void) {
  if (!items.length) return emptyState(emptyMessage);
  return (
    <div className="wm-whale-feed">
      <div className="wm-whale-tabs">
        <span className="active">Trades</span>
        <span>Flow</span>
        <span>Signals</span>
      </div>

      <div className="wm-whale-list">
        {items.map((item, index) => {
           let timeStr = formatRelative(item.timestamp || null);
           // clean up time format to look like '14m ago'
           timeStr = timeStr.replace(' minutes ago', 'm ago').replace(' minutes', 'm ago')
                            .replace(' hours ago', 'h ago').replace(' hours', 'h ago')
                            .replace(' seconds ago', 's ago').replace(' seconds', 's ago');
           if (timeStr.includes('just now')) timeStr = '1m ago';
           if (!timeStr.includes('ago')) timeStr += ' ago'; // safe fallback
           
           const side = String(item.side || 'BUY').toUpperCase();
           const isBuy = side === 'BUY';
           const addressFull = item.txHash || item.addresses?.[0]?.address || 'unknown';
           const address = shortHash(addressFull, 5, 0).replace('...', '');
           
           return (
             <div
               key={`${item.txHash || 'trade'}-${index}`}
               className={`wm-whale-row ${isBuy ? 'buy' : 'sell'}`}
               onClick={() => item.marketId && onMarketSelect?.(item.marketId)}
             >
               <div className="wm-whale-meta">
                 <span className="wm-whale-dot" />
                 <span>{address}</span>
                 <i>·</i>
                 <span>{timeStr}</span>
                 <i>·</i>
                 <strong>{side}</strong>
                 <b>${formatCompact(item.notional || 0)}</b>
               </div>
               <strong className="wm-whale-title">
                 {item.marketTitle || 'Unknown Market'}
               </strong>
             </div>
           );
        })}
      </div>
    </div>
  );
}

function tradeSignalList(items: RuntimeTradeSignal[], emptyMessage: string) {
  if (!items.length) return emptyState(emptyMessage);
  return (
    <div className="wm-signal-list">
      {items.map((item, index) => {
        const severity = String(item.severity || 'watch').toLowerCase();
        const side = String(item.side || '--').toUpperCase();
        const outcome = String(item.outcome || '--').toUpperCase();
        return (
          <article className={`wm-trade-signal-card ${severity}`} key={`${item.txHash || item.title || 'trade'}-${index}`}>
            <div className="wm-trade-signal-rail" />
            <div className="wm-trade-signal-content">
              <div className="wm-trade-signal-head">
                <div className="wm-trade-chip-row">
                  <span className={`wm-chip ${side === 'BUY' ? 'positive' : side === 'SELL' ? 'critical' : ''}`}>{side}</span>
                  <span className="wm-trade-outcome">{outcome}</span>
                </div>
                <span className={`wm-signal-severity ${severity}`}>{severity.toUpperCase()}</span>
              </div>
              <div className="wm-trade-signal-body">
                <strong className="wm-trade-signal-notional">{formatPercent(item.price)}</strong>
                <span className="wm-trade-signal-market">{item.marketTitle || 'Market signal'}</span>
              </div>
              <div className="wm-trade-signal-meta">
                <span>{shortHash(item.txHash || '', 12, 6)}</span>
                <span>{item.notional ? `$${formatCompact(item.notional)}` : '--'}</span>
                <span>{formatDate(item.timestamp || null)}</span>
              </div>
              {item.summary ? <div className="wm-trade-signal-summary">{item.summary}</div> : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function levelTotal(levels: L2Level[]) {
  return levels.reduce((sum, level) => {
    const size = Number(level.size);
    return Number.isFinite(size) ? sum + size : sum;
  }, 0);
}

export {
  emptyState,
  levelTotal,
  orderfilledList,
  priceLine,
  sparkline,
  tradeList,
  oracleList,
  contentList,
  summaryRows,
  marketTickerGrid,
  marketTickerList,
  alphaSignalList,
  tradeSignalList,
  whaleTrackerList,
};
