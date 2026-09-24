import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import type { PanelRenderContext, RuntimeMarketGroup, RuntimeMarketTicker } from '@/types';
import type { PanelRenderMap } from './types';
import { emptyState } from './shared/renderers';
import { formatCompact } from './shared/formatters';
import { useSpecialistCopy } from '@/services/specialist-i18n';

type CommoditiesTab = 'commodities' | 'fx';

const COMMODITY_SYMBOL_ORDER = [
  '^VIX',
  'GC=F',
  'SI=F',
  'HG=F',
  'PL=F',
  'PA=F',
  'ALI=F',
  'CL=F',
  'BZ=F',
  'NG=F',
  'TTF=F',
  'RB=F',
  'HO=F',
  'URA',
  'LIT',
  'MTF=F',
  'ZW=F',
  'ZC=F',
  'ZS=F',
  'ZR=F',
  'KC=F',
  'SB=F',
  'CC=F',
  'CT=F',
] as const;

const FX_SYMBOL_ORDER = [
  'EURUSD=X',
  'GBPUSD=X',
  'USDJPY=X',
  'USDCNY=X',
  'USDINR=X',
  'AUDUSD=X',
  'USDCHF=X',
  'USDCAD=X',
  'USDTRY=X',
] as const;

const COMMODITY_SORT_INDEX = new Map(COMMODITY_SYMBOL_ORDER.map((symbol, index) => [symbol, index]));
const FX_SORT_INDEX = new Map(FX_SYMBOL_ORDER.map((symbol, index) => [symbol, index]));
const CRYPTO_SYMBOL_ORDER = [
  'BTC-USD',
  'ETH-USD',
  'SOL-USD',
  'BNB-USD',
  'XRP-USD',
  'DOGE-USD',
  'ADA-USD',
  'AVAX-USD',
  'LINK-USD',
  'LTC-USD',
  'DOT-USD',
  'TRX-USD',
  'BCH-USD',
] as const;

const CRYPTO_SORT_INDEX = new Map(CRYPTO_SYMBOL_ORDER.map((symbol, index) => [symbol, index]));
type CryptoTickDirection = 'tick-up' | 'tick-down';

function tickerTone(item: RuntimeMarketTicker) {
  const changePercent = Number(item.changePercent);
  if (!Number.isFinite(changePercent) || changePercent === 0) return 'flat';
  return changePercent > 0 ? 'up' : 'down';
}

function tickerMoveTag(item: RuntimeMarketTicker, mode: 'crypto' | 'macro') {
  const absChange = Math.abs(Number(item.changePercent));
  if (!Number.isFinite(absChange)) return mode === 'crypto' ? 'SPOT' : 'QUOTE';
  if (absChange >= (mode === 'crypto' ? 4 : 1.5)) return 'ALERT';
  if (absChange >= (mode === 'crypto' ? 1.5 : 0.6)) return 'MOVE';
  return mode === 'crypto' ? 'SPOT' : 'WATCH';
}

function tagKey(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-');
}

function averageChange(items: RuntimeMarketTicker[]) {
  const changes = items.map((item) => Number(item.changePercent)).filter((value) => Number.isFinite(value));
  if (!changes.length) return null;
  return changes.reduce((sum, value) => sum + value, 0) / changes.length;
}

function topMover(items: RuntimeMarketTicker[]) {
  return items.reduce<RuntimeMarketTicker | null>((top, item) => {
    const move = Math.abs(Number(item.changePercent));
    if (!Number.isFinite(move)) return top;
    if (!top) return item;
    return move > Math.abs(Number(top.changePercent)) ? item : top;
  }, null);
}

function commodityClass(item: RuntimeMarketTicker) {
  const symbol = item.symbol.toUpperCase();
  if (isFxTicker(item)) return 'FX';
  if (['GC=F', 'SI=F', 'HG=F', 'PL=F', 'PA=F', 'ALI=F'].includes(symbol)) return 'METALS';
  if (['CL=F', 'BZ=F', 'NG=F', 'TTF=F', 'RB=F', 'HO=F', 'URA', 'LIT'].includes(symbol)) return 'ENERGY';
  if (symbol === '^VIX') return 'RISK';
  return 'AGRI';
}

function commodityAuxMeta(item: RuntimeMarketTicker) {
  const volume = Number(item.volume24h);
  if (Number.isFinite(volume) && volume > 0) return `VOL ${formatCompact(volume)}`;
  const marketCap = Number(item.marketCap);
  if (Number.isFinite(marketCap) && marketCap > 0) return `MCAP ${formatCompact(marketCap)}`;
  return null;
}

function isFxTicker(item: RuntimeMarketTicker) {
  return item.symbol.endsWith('=X');
}

function sortTickers(items: RuntimeMarketTicker[], sortIndex: Map<string, number>) {
  return [...items].sort((left, right) => {
    const leftIndex = sortIndex.get(left.symbol) ?? Number.MAX_SAFE_INTEGER;
    const rightIndex = sortIndex.get(right.symbol) ?? Number.MAX_SAFE_INTEGER;
    if (leftIndex !== rightIndex) return leftIndex - rightIndex;
    return left.label.localeCompare(right.label);
  });
}

function formatCryptoPrice(item: RuntimeMarketTicker) {
  if (item.price == null || !Number.isFinite(Number(item.price))) return '--';
  const numeric = Number(item.price);
  if (Math.abs(numeric) >= 1000) {
    return `$${Math.round(numeric).toLocaleString('en-US')}`;
  }
  const digits = numeric >= 100 ? 2 : numeric >= 1 ? 2 : 4;
  return `$${numeric.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function cryptoBoard(
  items: RuntimeMarketTicker[],
  emptyMessage: string,
  tickDirections: Record<string, CryptoTickDirection | undefined>,
  labels: { topMove: string; average24h: string; green: string; volume24h: string; marketCap: string; flow: string },
) {
  if (!items.length) return emptyState(emptyMessage);
  const leader = topMover(items);
  const avg = averageChange(items);
  const upCount = items.filter((item) => tickerTone(item) === 'up').length;
  return (
    <div className="wm-crypto-watch-shell">
      <div className="wm-market-radar-strip">
        <span><b>{leader?.label || '--'}</b><em>{labels.topMove}</em></span>
        <span><b>{avg == null ? '--' : formatCommodityChange(avg)}</b><em>{labels.average24h}</em></span>
        <span><b>{upCount}/{items.length}</b><em>{labels.green}</em></span>
      </div>
      <div className="wm-crypto-watch-list">
      {items.map((item) => {
        const tone = tickerTone(item);
        const sparkColor = tone === 'down' ? '#ff5d5d' : tone === 'up' ? '#39ff73' : '#8f8f8c';
        const tickDirection = tickDirections[item.symbol];
        return (
          <article className={`wm-crypto-market-row ${tone}${tickDirection ? ` ${tickDirection}` : ''}`} key={item.symbol}>
            <div className="wm-crypto-market-asset">
              <strong>{item.label}</strong>
              <span>{item.symbol.replace('-USD', '')}</span>
            </div>
            <div className="wm-crypto-market-spark">{commoditySparkline(item.points, sparkColor)}</div>
            <div className="wm-crypto-market-value">
              <strong>{formatCryptoPrice(item)}</strong>
              <span className={tone}>{formatCommodityChange(item.changePercent)}</span>
            </div>
            <div className="wm-crypto-market-flow">
              <b>{item.volume24h ? `$${formatCompact(item.volume24h)}` : item.marketCap ? `$${formatCompact(item.marketCap)}` : '--'}</b>
              <em>{item.volume24h ? labels.volume24h : item.marketCap ? labels.marketCap : labels.flow}</em>
            </div>
            <span className={`wm-market-signal-chip ${tone}`}>{tickerMoveTag(item, 'crypto')}</span>
          </article>
        );
      })}
      </div>
    </div>
  );
}

function CryptoWatchPanel({ crypto }: { crypto?: RuntimeMarketGroup | null }) {
  const { copy, shared } = useSpecialistCopy('crypto-watch');
  const items = sortTickers(crypto?.items || [], CRYPTO_SORT_INDEX);
  const previousPricesRef = useRef<Record<string, number>>({});
  const clearTickTimerRef = useRef<number | null>(null);
  const [tickDirections, setTickDirections] = useState<Record<string, CryptoTickDirection | undefined>>({});

  useEffect(() => {
    const nextPrices: Record<string, number> = {};
    const nextDirections: Record<string, CryptoTickDirection | undefined> = {};

    items.forEach((item) => {
      const numeric = Number(item.price);
      if (!Number.isFinite(numeric)) return;
      nextPrices[item.symbol] = numeric;
      const previous = previousPricesRef.current[item.symbol];
      if (typeof previous === 'number' && Number.isFinite(previous) && previous !== numeric) {
        nextDirections[item.symbol] = numeric > previous ? 'tick-up' : 'tick-down';
      }
    });

    previousPricesRef.current = nextPrices;

    if (!Object.keys(nextDirections).length) return;
    setTickDirections(nextDirections);

    if (clearTickTimerRef.current !== null) {
      window.clearTimeout(clearTickTimerRef.current);
    }
    clearTickTimerRef.current = window.setTimeout(() => {
      setTickDirections({});
      clearTickTimerRef.current = null;
    }, 1200);

    return undefined;
  }, [items]);

  useEffect(() => () => {
    if (clearTickTimerRef.current !== null) {
      window.clearTimeout(clearTickTimerRef.current);
    }
  }, []);

  return (
    <Panel title={copy('title', 'CRYPTO')} badge={shared('live', 'LIVE')} status="live" count={items.length} className="wm-market-panel wm-crypto-market-panel">
      {cryptoBoard(items, copy('empty', 'No crypto prices loaded yet.'), tickDirections, {
        topMove: shared('topMove', 'top move'),
        average24h: shared('average24h', 'avg 24h'),
        green: shared('green', 'green'),
        volume24h: shared('volume24h', '24h vol'),
        marketCap: shared('marketCap', 'mcap'),
        flow: shared('flow', 'flow'),
      })}
    </Panel>
  );
}

function commoditySparkline(points: RuntimeMarketTicker['points'], color: string) {
  const clean = (points || [])
    .map((point, index) => ({ index, value: Number(point.value) }))
    .filter((point) => Number.isFinite(point.value));
  if (clean.length < 2) return null;

  const width = 60;
  const height = 18;
  const min = Math.min(...clean.map((point) => point.value));
  const max = Math.max(...clean.map((point) => point.value));
  const span = max - min || 1;
  const path = clean
    .map((point, index) => {
      const x = (point.index / Math.max(clean.length - 1, 1)) * width;
      const y = height - ((point.value - min) / span) * height;
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(' ');

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="mini-sparkline" preserveAspectRatio="none" aria-hidden="true">
      <path d={path} fill="none" stroke={color} strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function formatCommodityPrice(item: RuntimeMarketTicker) {
  if (item.price == null || !Number.isFinite(Number(item.price))) return '--';
  const numeric = Number(item.price);
  if (isFxTicker(item)) {
    return numeric.toFixed(4);
  }
  if (Math.abs(numeric) >= 1000) {
    return `$${formatCompact(numeric)}`;
  }
  return `$${numeric.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatCommodityChange(changePercent?: number | null) {
  if (changePercent == null || !Number.isFinite(Number(changePercent))) return '--';
  const numeric = Number(changePercent);
  return `${numeric > 0 ? '+' : ''}${numeric.toFixed(2)}%`;
}

function commodityBoard(
  items: RuntimeMarketTicker[],
  emptyMessage: string,
  labels: { topMove: string; averageMove: string; alerts: string },
) {
  if (!items.length) {
    return (
      <div className="wm-commodity-empty">
        <span>{emptyMessage}</span>
      </div>
    );
  }
  const leader = topMover(items);
  const avg = averageChange(items);
  const alertCount = items.filter((item) => tickerMoveTag(item, 'macro') === 'ALERT').length;
  return (
    <div className="wm-commodity-board">
      <div className="wm-market-radar-strip">
        <span><b>{leader?.label || '--'}</b><em>{labels.topMove}</em></span>
        <span><b>{avg == null ? '--' : formatCommodityChange(avg)}</b><em>{labels.averageMove}</em></span>
        <span><b>{alertCount}</b><em>{labels.alerts}</em></span>
      </div>
      <div className="commodities-grid">
      {items.map((item) => {
        const tone = tickerTone(item);
        const sparkColor = tone === 'down' ? '#ff6464' : '#39ff73';
        const assetClass = commodityClass(item);
        const signalTag = tickerMoveTag(item, 'macro');
        const auxMeta = commodityAuxMeta(item);
        return (
          <div className={`commodity-item ${tone}`} key={item.symbol}>
            <div className="commodity-head">
              <span className="commodity-name">{item.label}</span>
              <b className={`commodity-class-tag ${tagKey(assetClass)}`}>{assetClass}</b>
            </div>
            <div className="commodity-spark">{commoditySparkline(item.points, sparkColor)}</div>
            <div className="commodity-foot">
              <strong className="commodity-price">{formatCommodityPrice(item)}</strong>
              <span className={`commodity-change ${tone}`}>{formatCommodityChange(item.changePercent)}</span>
            </div>
            <div className="commodity-meta">
              <span className={`commodity-signal-tag ${tagKey(signalTag)}`}>{signalTag}</span>
              {auxMeta ? <em>{auxMeta}</em> : null}
            </div>
          </div>
        );
      })}
      </div>
    </div>
  );
}

function CommoditiesWatchPanel({ commodities }: { commodities?: RuntimeMarketGroup | null }) {
  const { copy, shared, formatNumber } = useSpecialistCopy('commodities-watch');
  const [tab, setTab] = useState<CommoditiesTab>('commodities');

  const tabItems = useMemo(() => {
    const items = commodities?.items || [];
    const commodityItems = sortTickers(items.filter((item) => !isFxTicker(item)), COMMODITY_SORT_INDEX);
    const fxItems = sortTickers(items.filter((item) => isFxTicker(item)), FX_SORT_INDEX);
    return { commodities: commodityItems, fx: fxItems };
  }, [commodities]);

  const hasFx = tabItems.fx.length > 0;
  const safeTab = tab === 'fx' && !hasFx ? 'commodities' : tab;
  const visibleItems = safeTab === 'fx' ? tabItems.fx : tabItems.commodities;

  return (
    <Panel
      title={copy('title', 'COMMODITIES')}
      badge={shared('macro', 'MACRO')}
      status="live"
      count={visibleItems.length}
      className="wm-market-panel wm-commodities-panel"
    >
      <div className="wm-commodity-panel-stack">
        <div className="wm-commodity-tabbar" role="tablist" aria-label={copy('views', 'Commodity market views')}>
          <button
            type="button"
            className={`panel-tab${safeTab === 'commodities' ? ' active' : ''}`}
            onClick={() => setTab('commodities')}
            role="tab"
            aria-selected={safeTab === 'commodities'}
          >
            {copy('commodities', 'Commodities')} <b>{formatNumber(tabItems.commodities.length)}</b>
          </button>
          {hasFx ? (
            <button
              type="button"
              className={`panel-tab${safeTab === 'fx' ? ' active' : ''}`}
              onClick={() => setTab('fx')}
              role="tab"
              aria-selected={safeTab === 'fx'}
            >
              FX <b>{formatNumber(tabItems.fx.length)}</b>
            </button>
          ) : null}
        </div>
        {commodityBoard(
          visibleItems,
          safeTab === 'fx' ? copy('noFx', 'No FX quotes loaded yet.') : copy('empty', 'No commodity quotes loaded yet.'),
          {
            topMove: shared('topMove', 'top move'),
            averageMove: shared('averageMove', 'avg move'),
            alerts: shared('alerts', 'alerts'),
          },
        )}
      </div>
    </Panel>
  );
}

function InflationNowcastPanel({ ctx }: { ctx: PanelRenderContext }) {
  const { copy } = useSpecialistCopy('inflation-nowcast');
  const nowcast = ctx.inflationNowcast;
  if (!nowcast) return emptyState(copy('empty', 'No inflation nowcast loaded.'));
  const mom = nowcast.monthOverMonth || {};
  const yoy = nowcast.yearOverYear || {};
  const monthlyLabel = mom['Month'] || yoy['Month'] || '--';
  return (
    <div className="wm-panel-stack">
      <section className="wm-nowcast-grid">
        {[
          { label: copy('month', 'MONTH'), value: monthlyLabel },
          { label: 'CPI MOM', value: mom['CPI'] || '--' },
          { label: copy('coreCpi', 'CORE CPI'), value: mom['Core CPI'] || '--' },
          { label: 'PCE MOM', value: mom['PCE'] || '--' },
          { label: 'CPI YOY', value: yoy['CPI'] || '--' },
          { label: copy('corePce', 'CORE PCE'), value: yoy['Core PCE'] || '--' },
        ].map((row) => (
          <article className="wm-nowcast-card" key={row.label}>
            <span>{row.label}</span>
            <strong>{row.value}</strong>
          </article>
        ))}
      </section>
      {!!nowcast.quarterly?.length && (
        <section className="wm-subpanel">
          <div className="wm-subpanel-title">{copy('quarterlyAnnualized', 'QUARTERLY ANNUALIZED')}</div>
          <div className="wm-panel-list">
            {nowcast.quarterly.slice(0, 3).map((row, index) => (
              <article className="wm-oracle-card" key={`${row['Quarter'] || row['Quarter '] || row['Date'] || index}`}>
                <div className="wm-oracle-header">
                  <strong>{row['Quarter'] || row['Date'] || `Q${index + 1}`}</strong>
                  <span>{row['Updated'] || row['Updated '] || 'fed'}</span>
                </div>
                <div className="wm-summary-grid">
                  <div className="wm-summary-row"><span>CPI</span><strong>{row['CPI'] || '--'}</strong></div>
                  <div className="wm-summary-row"><span>{copy('coreCpi', 'CORE CPI')}</span><strong>{row['Core CPI'] || '--'}</strong></div>
                  <div className="wm-summary-row"><span>PCE</span><strong>{row['PCE'] || '--'}</strong></div>
                  <div className="wm-summary-row"><span>{copy('corePce', 'CORE PCE')}</span><strong>{row['Core PCE'] || '--'}</strong></div>
                </div>
              </article>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function InflationNowcastWorkspacePanel({ ctx }: { ctx: PanelRenderContext }) {
  const { copy } = useSpecialistCopy('inflation-nowcast');
  return (
    <Panel title={copy('title', 'INFLATION NOWCAST')} badge="FED" status="live">
      <InflationNowcastPanel ctx={ctx} />
    </Panel>
  );
}

export const macroPanelRenderers: PanelRenderMap = {
  'commodities-watch': {
    render: (ctx) => <CommoditiesWatchPanel commodities={ctx.commodities} />,
  },
  'crypto-watch': {
    render: (ctx) => <CryptoWatchPanel crypto={ctx.crypto} />,
  },
  'inflation-nowcast': {
    render: (ctx) => <InflationNowcastWorkspacePanel ctx={ctx} />,
  },
};
