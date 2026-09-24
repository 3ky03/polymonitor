import { useCallback, useEffect, useMemo, useState } from 'preact/hooks';
import { MobileWorkspaceNav } from '@/components/MobileWorkspaceNav';
import { fetchAuthSession, type AuthSession } from '@/services/auth';
import { useI18n } from '@/services/i18n';
import {
  createBriefing,
  fetchBriefings,
  fetchPublicBriefing,
  revokeBriefing,
  type BriefingMarket,
  type BriefingRegistryItem,
  type PublicBriefing,
} from '@/services/product';
import { AuthFrame, LoadingAccess, SignInPanel, errorMessage } from '@/workspaces/auth/AuthWorkspace';

function briefingPublicId() {
  if (typeof window === 'undefined') return null;
  const match = window.location.pathname.match(/^\/briefings\/([A-Za-z0-9_-]{32})(?:\/|$)/);
  return match?.[1] || null;
}

function MarketRows({ items, empty }: { items: BriefingMarket[]; empty: string }) {
  const { t, formatNumber, formatPercent } = useI18n();
  if (!items.length) return <div className="brief-empty">{empty}</div>;
  return (
    <div className="brief-market-list">
      {items.map((market) => (
        <a href={`/markets/${market.marketId}`} key={market.marketId}>
          <div>
            <span className={`brief-stage is-${market.oracleStage}`}>{market.oracleStage}</span>
            <strong>{market.title}</strong>
            <small>#{market.marketId} · {market.category || t('briefing.uncategorized')} · {market.completionStatus}</small>
          </div>
          <div className="brief-price">
            <strong>{market.latestPrice == null ? '—' : formatPercent(market.latestPrice)}</strong>
            <span className={(market.change24h || 0) >= 0 ? 'is-positive' : 'is-negative'}>
              {market.change24h == null ? t('briefing.noBaseline') : `${market.change24h >= 0 ? '+' : ''}${formatPercent(market.change24h)}`}
            </span>
            <small>${formatNumber(market.volume24h || 0, { notation: 'compact', maximumFractionDigits: 1 })} {t('briefing.volume')}</small>
          </div>
        </a>
      ))}
    </div>
  );
}

export function PublicBriefingWorkspace() {
  const { t, formatDateTime } = useI18n();
  const publicId = briefingPublicId();
  const [briefing, setBriefing] = useState<PublicBriefing | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!publicId) {
      setError(t('briefing.invalidUrl'));
      return;
    }
    fetchPublicBriefing(publicId).then(setBriefing).catch((caught) => setError(errorMessage(caught)));
  }, [publicId, t]);

  if (!briefing && !error) {
    return <div className="brief-shell"><main className="brief-loading"><span>{t('briefing.canonicalSnapshot')}</span><h1>{t('briefing.opening')}</h1></main></div>;
  }
  if (!briefing) {
    return <div className="brief-shell"><main className="brief-loading"><span>{t('briefing.unavailable')}</span><h1>{error}</h1><a href="/">{t('briefing.returnAtlas')}</a></main></div>;
  }
  const snapshot = briefing.snapshot;
  return (
    <div className="brief-shell">
      <header className="brief-topbar">
        <a href="/">◎ Atlas</a>
        <div>{t('briefing.brand')} <span>{t('briefing.readOnly')}</span></div>
        <button type="button" onClick={() => void navigator.clipboard.writeText(window.location.href)}>{t('briefing.copyLink')}</button>
      </header>
      <main className="brief-main">
        <section className="brief-hero">
          <div>
            <span>{t('briefing.generated', { date: formatDateTime(snapshot.generatedAt) })}</span>
            <h1>{briefing.title}</h1>
            <p>{t('briefing.description')}</p>
          </div>
          <div className="brief-validity"><span>{t('briefing.validUntil')}</span><strong>{formatDateTime(briefing.expiresAt)}</strong><small>{t('briefing.revocable')}</small></div>
        </section>
        <section className="brief-metrics">
          <div><span>{t('briefing.tracked')}</span><strong>{snapshot.summary.trackedMarkets}</strong></div>
          <div><span>{t('briefing.liquidMarkets')}</span><strong>{snapshot.summary.topMarkets}</strong></div>
          <div><span>{t('briefing.oracleAttention')}</span><strong>{snapshot.summary.oracleAttention}</strong></div>
          <div><span>{t('briefing.workspaceRev')}</span><strong>{snapshot.workspaceLens.revision || '—'}</strong></div>
        </section>
        <div className="brief-grid">
          <section className="brief-card brief-card-wide">
            <header><div><span>{t('briefing.personalLens')}</span><h2>{t('briefing.trackedMarkets')}</h2></div><em>{t('briefing.observed', { count: snapshot.trackedMarkets.length })}</em></header>
            <MarketRows items={snapshot.trackedMarkets} empty={t('briefing.noTracked')} />
          </section>
          <section className="brief-card">
            <header><div><span>{t('briefing.oracleLifecycle')}</span><h2>{t('briefing.attentionLedger')}</h2></div><em>{t('briefing.unresolved')}</em></header>
            <MarketRows items={snapshot.oracleAttention} empty={t('briefing.noOracleAttention')} />
          </section>
          <section className="brief-card">
            <header><div><span>{t('briefing.marketActivity')}</span><h2>{t('briefing.highestVolume')}</h2></div><em>{t('briefing.canonicalServing')}</em></header>
            <MarketRows items={snapshot.topMarkets} empty={t('briefing.noVolume')} />
          </section>
        </div>
        <section className="brief-provenance">
          <div><span>{t('briefing.sourceContract')}</span><strong>{snapshot.source.kind}</strong></div>
          <p>{snapshot.source.markets}<br />{snapshot.source.oracle}</p>
          <p>{snapshot.source.warning}</p>
        </section>
      </main>
      <MobileWorkspaceNav />
    </div>
  );
}

export function BriefingManagerWorkspace() {
  const { t, formatDateTime } = useI18n();
  const [session, setSession] = useState<AuthSession | null>(null);
  const [items, setItems] = useState<BriefingRegistryItem[]>([]);
  const [title, setTitle] = useState('');
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const nextSession = await fetchAuthSession();
    setSession(nextSession);
    if (nextSession.authenticated && !nextSession.user?.forcePasswordChange) {
      setItems((await fetchBriefings()).items || []);
    }
    setReady(true);
  }, []);

  useEffect(() => {
    refresh().catch((caught) => {
      setError(errorMessage(caught));
      setReady(true);
    });
  }, [refresh]);

  const activeCount = useMemo(() => items.filter((item) => item.active).length, [items]);
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

  if (!ready) return <LoadingAccess />;
  if (!session?.authenticated) return <AuthFrame><SignInPanel next="/briefings" /></AuthFrame>;
  if (session.user?.forcePasswordChange) return <AuthFrame><main className="brief-manager-main brief-gate"><h1>{t('briefing.replacePassword')}</h1><a href="/account">{t('briefing.openAccess')}</a></main></AuthFrame>;

  return (
    <AuthFrame>
      <main className="brief-manager-main">
        <section className="brief-manager-hero">
          <div><span>{t('briefing.managerKicker')}</span><h1>{t('briefing.registry')}</h1><p>{t('briefing.managerDescription')}</p></div>
          <div><strong>{activeCount}/20</strong><span>{t('briefing.activeLinks')}</span></div>
        </section>
        {error ? <div className="brief-alert is-error">{error}</div> : null}
        {notice ? <div className="brief-alert">{notice}</div> : null}
        <form className="brief-create" onSubmit={(event) => {
          event.preventDefault();
          void run(async () => {
            const item = (await createBriefing(title)).item;
            const url = `${window.location.origin}/briefings/${item.publicId}`;
            await navigator.clipboard.writeText(url);
            setNotice(t('briefing.created'));
            setTitle('');
          });
        }}>
          <label><span>{t('briefing.titleLabel')}</span><input value={title} maxLength={120} onInput={(event) => setTitle(event.currentTarget.value)} placeholder={t('briefing.titlePlaceholder')} /></label>
          <button type="submit" disabled={busy || activeCount >= 20}>{busy ? t('briefing.generating') : t('briefing.generate')}</button>
        </form>
        <section className="brief-registry">
          <header><div><span>{t('briefing.capabilityLinks')}</span><h2>{t('briefing.recent')}</h2></div><em>{t('briefing.newestFirst')}</em></header>
          {!items.length ? <div className="brief-empty">{t('briefing.noneYet')}</div> :
            <div className="brief-registry-list">{items.map((item) => {
              const url = `${window.location.origin}/briefings/${item.publicId}`;
              return <article key={item.id} className={item.active ? '' : 'is-inactive'}>
                <div><span>{item.active ? `● ${t('briefing.active')}` : item.revokedAt ? `○ ${t('briefing.revoked')}` : `○ ${t('briefing.expired')}`}</span><strong>{item.title}</strong><small>{t('briefing.createdExpires', { created: formatDateTime(item.createdAt), expires: formatDateTime(item.expiresAt) })}</small></div>
                <div>{item.active ? <><a href={url} target="_blank" rel="noreferrer">{t('briefing.open')}</a><button type="button" onClick={() => void navigator.clipboard.writeText(url).then(() => setNotice(t('briefing.copied')))}>{t('briefing.copy')}</button><button type="button" onClick={() => void run(() => revokeBriefing(item.id))}>{t('briefing.revoke')}</button></> : null}</div>
              </article>;
            })}</div>}
        </section>
      </main>
    </AuthFrame>
  );
}
