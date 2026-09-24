import { useCallback, useEffect, useMemo, useState } from 'preact/hooks';
import { fetchAuthSession, type AuthSession } from '@/services/auth';
import { useI18n, type MessageKey } from '@/services/i18n';
import {
  addWatchlistMarket,
  createAlertRule,
  deleteAlertRule,
  fetchAlerts,
  fetchNotificationPreferences,
  fetchWebPushStatus,
  fetchWatchlist,
  markAlertRead,
  markAllAlertsRead,
  removeWatchlistMarket,
  updateNotificationPreferences,
  type AlertEvent,
  type NotificationPreferences,
  type WebPushStatus,
  type Watchlist,
} from '@/services/product';
import {
  disableBrowserPush,
  enableBrowserPush,
  getBrowserPushState,
  type BrowserPushState,
} from '@/services/webPush';
import { AuthFrame, LoadingAccess, SignInPanel, errorMessage } from '@/workspaces/auth/AuthWorkspace';

const PRICE_KINDS = new Set(['price_above', 'price_below']);
const LABEL_KEYS: Record<string, MessageKey> = {
  price_above: 'watchlist.rule.priceAbove',
  price_below: 'watchlist.rule.priceBelow',
  oracle_gap: 'watchlist.rule.oracleGap',
  oracle_proposed: 'watchlist.rule.oracleProposed',
  oracle_disputed: 'watchlist.rule.oracleDisputed',
  oracle_resolved: 'watchlist.rule.oracleResolved',
  market_closed: 'watchlist.rule.marketClosed',
};

const timeValue = (minute: number | null) =>
  minute == null ? '' : `${String(Math.floor(minute / 60)).padStart(2, '0')}:${String(minute % 60).padStart(2, '0')}`;
const minuteValue = (value: string) => value ? Number(value.slice(0, 2)) * 60 + Number(value.slice(3, 5)) : null;
export function WatchlistWorkspace() {
  const { t, formatDateTime, formatPercent } = useI18n();
  const [session, setSession] = useState<AuthSession | null>(null);
  const [watchlist, setWatchlist] = useState<Watchlist | null>(null);
  const [alerts, setAlerts] = useState<AlertEvent[]>([]);
  const [preferences, setPreferences] = useState<NotificationPreferences | null>(null);
  const [webPush, setWebPush] = useState<WebPushStatus | null>(null);
  const [browserPush, setBrowserPush] = useState<BrowserPushState | null>(null);
  const [marketId, setMarketId] = useState('');
  const [selectedMarketId, setSelectedMarketId] = useState<number | null>(null);
  const [kind, setKind] = useState('price_above');
  const [threshold, setThreshold] = useState('0.60');
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const nextSession = await fetchAuthSession();
    setSession(nextSession);
    if (nextSession.authenticated && !nextSession.user?.forcePasswordChange) {
      const [nextWatchlist, nextAlerts, nextPreferences, nextWebPush, nextBrowserPush] = await Promise.all([
        fetchWatchlist(),
        fetchAlerts(),
        fetchNotificationPreferences(),
        fetchWebPushStatus(),
        getBrowserPushState(),
      ]);
      setWatchlist(nextWatchlist);
      setAlerts(nextAlerts.items);
      setPreferences(nextPreferences);
      setWebPush(nextWebPush);
      setBrowserPush(nextBrowserPush);
      setSelectedMarketId((current) => current || nextWatchlist.items[0]?.marketId || null);
    }
    setReady(true);
  }, []);

  useEffect(() => {
    refresh().catch((caught) => {
      setError(errorMessage(caught));
      setReady(true);
    });
  }, [refresh]);

  const selected = useMemo(
    () => watchlist?.items.find((item) => item.marketId === selectedMarketId) || null,
    [watchlist, selectedMarketId],
  );
  const label = (value: string) => LABEL_KEYS[value] ? t(LABEL_KEYS[value]) : value;
  const probability = (value: number | null) => value == null ? '—' : formatPercent(value);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      await refresh();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  const toggleWebPush = async () => {
    setBusy(true);
    setError(null);
    try {
      if (!webPush) return;
      if (browserPush?.subscribed) {
        await disableBrowserPush();
      } else {
        await enableBrowserPush(webPush);
      }
      await refresh();
    } catch (caught) {
      const code = caught instanceof Error ? caught.message : '';
      const translated: Partial<Record<string, MessageKey>> = {
        WEB_PUSH_UNSUPPORTED: 'watchlist.pushUnsupported',
        WEB_PUSH_UNAVAILABLE: 'watchlist.pushUnavailable',
        WEB_PUSH_PERMISSION_DENIED: 'watchlist.pushPermissionDenied',
      };
      setError(translated[code] ? t(translated[code]) : errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  if (!ready) return <LoadingAccess />;
  if (!session?.authenticated) return <AuthFrame><SignInPanel next="/watchlist" /></AuthFrame>;
  if (session.user?.forcePasswordChange) {
    return <AuthFrame><main className="watchlist-main watchlist-gate"><span>{t('watchlist.accountAction')}</span><h1>{t('watchlist.replacePassword')}</h1><a href="/account">{t('watchlist.openAccess')}</a></main></AuthFrame>;
  }

  return (
    <AuthFrame>
      <main className="watchlist-main">
        <section className="watchlist-hero">
          <div>
            <span className="watchlist-kicker">{t('watchlist.kicker')}</span>
            <h1>{t('watchlist.title')}</h1>
            <p>{t('watchlist.description')}</p>
          </div>
          <div className="watchlist-hero-actions">
            <a href="/">{t('watchlist.openAtlas')}</a>
            <button type="button" disabled={busy} onClick={() => void refresh()}>{busy ? t('watchlist.working') : t('watchlist.refresh')}</button>
          </div>
        </section>

        {error ? <div className="watchlist-error" role="alert">{error}</div> : null}

        <section className="watchlist-metrics" aria-label={t('watchlist.status')}>
          <div><span>{t('watchlist.markets')}</span><strong>{watchlist?.summary.markets || 0}</strong><small>{t('watchlist.canonicalIds')}</small></div>
          <div><span>{t('watchlist.activeRules')}</span><strong>{watchlist?.summary.activeRules || 0}</strong><small>{t('watchlist.crossings')}</small></div>
          <div><span>{t('watchlist.oracleGaps')}</span><strong>{watchlist?.summary.oracleGaps || 0}</strong><small>{t('watchlist.endedNotFinal')}</small></div>
          <div><span>{t('watchlist.unread')}</span><strong>{watchlist?.summary.unreadAlerts || 0}</strong><small>{t('watchlist.inAppEvents')}</small></div>
        </section>

        <div className="watchlist-grid">
          <section className="watchlist-panel watchlist-markets">
            <header><div><span>{t('watchlist.registry')}</span><h2>{t('watchlist.trackedMarkets')}</h2></div><em>{watchlist?.items.length || 0} / 200</em></header>
            <form className="watchlist-add" onSubmit={(event) => {
              event.preventDefault();
              const parsed = Number(marketId);
              if (Number.isSafeInteger(parsed) && parsed > 0) void run(async () => {
                await addWatchlistMarket(parsed);
                setMarketId('');
                setSelectedMarketId(parsed);
              });
            }}>
              <label><span>{t('watchlist.localMarketId')}</span><input inputMode="numeric" value={marketId} onInput={(event) => setMarketId(event.currentTarget.value)} placeholder="2784982" required /></label>
              <button type="submit" disabled={busy}>{t('watchlist.addMarket')}</button>
            </form>
            {!watchlist?.items.length ? (
              <div className="watchlist-empty"><strong>{t('watchlist.noMarkets')}</strong><p>{t('watchlist.noMarketsDetail')}</p></div>
            ) : (
              <div className="watchlist-market-list">
                {watchlist.items.map((item) => (
                  <article key={item.marketId} className={item.marketId === selectedMarketId ? 'is-selected' : ''}>
                    <button className="watchlist-market-select" type="button" onClick={() => setSelectedMarketId(item.marketId)}>
                      <span className={`watchlist-stage is-${item.oracleStage}`}>{item.oracleStage}</span>
                      <strong>{item.title}</strong>
                      <small>#{item.marketId} · {item.category || t('watchlist.uncategorized')}</small>
                    </button>
                    <div className="watchlist-market-price"><strong>{probability(item.latestPrice)}</strong><small>{item.change24h == null ? t('watchlist.noBaseline') : `${item.change24h >= 0 ? '+' : ''}${probability(item.change24h)} / 24h`}</small></div>
                    <div className="watchlist-market-links"><a href={`/markets/${item.marketId}`}>{t('watchlist.dossier')} ↗</a><button type="button" onClick={() => void run(() => removeWatchlistMarket(item.marketId))}>{t('watchlist.remove')}</button></div>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className="watchlist-panel watchlist-rules">
            <header><div><span>{t('watchlist.ruleEngine')}</span><h2>{t('watchlist.alertReadiness')}</h2></div><em>{selected ? `#${selected.marketId}` : t('watchlist.selectMarket')}</em></header>
            {selected ? (
              <>
                <h3>{selected.title}</h3>
                <form className="watchlist-rule-form" onSubmit={(event) => {
                  event.preventDefault();
                  void run(() => createAlertRule(selected.marketId, kind, PRICE_KINDS.has(kind) ? Number(threshold) : null));
                }}>
                  <label><span>{t('watchlist.condition')}</span><select value={kind} onChange={(event) => setKind(event.currentTarget.value)}>{(watchlist?.alertKinds || []).map((value) => <option value={value} key={value}>{label(value)}</option>)}</select></label>
                  {PRICE_KINDS.has(kind) ? <label><span>{t('watchlist.probability')}</span><input type="number" min="0.01" max="0.99" step="0.01" value={threshold} onInput={(event) => setThreshold(event.currentTarget.value)} required /></label> : null}
                  <button type="submit" disabled={busy}>{t('watchlist.armRule')}</button>
                </form>
                <div className="watchlist-rule-list">
                  {selected.rules.map((rule) => (
                    <div key={rule.id}><span className={rule.conditionActive ? 'is-live' : ''}>{rule.conditionActive ? `● ${t('watchlist.active')}` : `○ ${t('watchlist.armed')}`}</span><strong>{label(rule.kind)}{rule.threshold == null ? '' : ` · ${probability(rule.threshold)}`}</strong><button type="button" onClick={() => void run(() => deleteAlertRule(rule.id))}>{t('watchlist.delete')}</button></div>
                  ))}
                </div>
              </>
            ) : <div className="watchlist-empty"><strong>{t('watchlist.chooseMarket')}</strong><p>{t('watchlist.autoRules')}</p></div>}
          </section>
        </div>

        <section className="watchlist-panel watchlist-inbox">
          <header><div><span>{t('watchlist.eventInbox')}</span><h2>{t('watchlist.observedTransitions')}</h2></div><button type="button" disabled={busy || !alerts.some((item) => !item.readAt)} onClick={() => void run(markAllAlertsRead)}>{t('watchlist.markAllRead')}</button></header>
          {!alerts.length ? <div className="watchlist-empty"><strong>{t('watchlist.noAlerts')}</strong><p>{t('watchlist.noAlertsDetail')}</p></div> :
            <div className="watchlist-alert-list">{alerts.map((item) => <article key={item.id} className={`${item.readAt ? 'is-read' : ''} is-${item.severity}`}><span>{item.severity}</span><div><strong>{item.title}</strong><p>{item.detail}</p><small>{item.marketTitle || `${t('watchlist.market')} #${item.marketId}`} · {formatDateTime(item.occurredAt)}</small></div>{item.readAt ? <em>{t('watchlist.read')}</em> : <button type="button" onClick={() => void run(() => markAlertRead(item.id))}>{t('watchlist.markRead')}</button>}</article>)}</div>}
        </section>

        {preferences ? <section className="watchlist-panel watchlist-preferences">
          <header><div><span>{t('watchlist.deliveryPolicy')}</span><h2>{t('watchlist.notificationPreferences')}</h2></div><em>{t('watchlist.inAppAndPush')}</em></header>
          <form onSubmit={(event) => {
            event.preventDefault();
            void run(() => updateNotificationPreferences({
              inAppEnabled: preferences.inAppEnabled,
              webPushEnabled: preferences.webPushEnabled,
              digestMode: preferences.digestMode,
              quietStartMinute: preferences.quietStartMinute,
              quietEndMinute: preferences.quietEndMinute,
              timezone: preferences.timezone,
            }));
          }}>
            <label className="watchlist-switch"><input type="checkbox" checked={preferences.inAppEnabled} onChange={(event) => setPreferences({ ...preferences, inAppEnabled: event.currentTarget.checked })} /><span>{t('watchlist.enableInApp')}</span></label>
            <label><span>{t('watchlist.cadence')}</span><select value={preferences.digestMode} onChange={(event) => setPreferences({ ...preferences, digestMode: event.currentTarget.value as NotificationPreferences['digestMode'] })}><option value="realtime">{t('watchlist.realtime')}</option><option value="hourly">{t('watchlist.hourly')}</option><option value="daily">{t('watchlist.daily')}</option><option value="off">{t('watchlist.off')}</option></select></label>
            <label><span>{t('watchlist.quietFrom')}</span><input type="time" value={timeValue(preferences.quietStartMinute)} onInput={(event) => setPreferences({ ...preferences, quietStartMinute: minuteValue(event.currentTarget.value) })} /></label>
            <label><span>{t('watchlist.quietUntil')}</span><input type="time" value={timeValue(preferences.quietEndMinute)} onInput={(event) => setPreferences({ ...preferences, quietEndMinute: minuteValue(event.currentTarget.value) })} /></label>
            <label><span>{t('watchlist.timezone')}</span><input value={preferences.timezone} onInput={(event) => setPreferences({ ...preferences, timezone: event.currentTarget.value })} /></label>
            <button type="submit" disabled={busy}>{t('watchlist.savePolicy')}</button>
          </form>
          <div className="watchlist-push">
            <div>
              <strong>{t('watchlist.browserPush')}</strong>
              <p>{!browserPush?.supported
                ? t('watchlist.pushUnsupported')
                : browserPush.permission === 'denied'
                  ? t('watchlist.pushBlocked')
                  : webPush?.connected && browserPush.subscribed
                    ? t('watchlist.pushConnected', { count: webPush.subscriptionCount })
                    : t('watchlist.pushReady')}</p>
            </div>
            <button
              type="button"
              disabled={busy || !webPush?.available || !browserPush?.supported || browserPush.permission === 'denied'}
              onClick={() => void toggleWebPush()}
            >
              {browserPush?.subscribed ? t('watchlist.disablePush') : t('watchlist.enablePush')}
            </button>
          </div>
          <p>{t('watchlist.deliveryBoundary')}</p>
        </section> : null}
      </main>
    </AuthFrame>
  );
}

export default WatchlistWorkspace;
