import type { ComponentChildren } from 'preact';
import { useCallback, useEffect, useState } from 'preact/hooks';
import { MobileWorkspaceNav } from '@/components/MobileWorkspaceNav';
import { useI18n } from '@/services/i18n';
import {
  AuthApiError,
  changePassword,
  createApiKey,
  fetchApiKeys,
  fetchAuditLog,
  fetchAuthSession,
  login,
  logout,
  revokeApiKey,
  type AuditEvent,
  type AuthSession,
  type ProductApiKey,
} from '@/services/auth';

export function errorMessage(error: unknown) {
  if (error instanceof AuthApiError) {
    return error.requestId ? `${error.message} · Request ${error.requestId}` : error.message;
  }
  return error instanceof Error ? error.message : 'The request could not be completed.';
}

export function AuthFrame({ children }: { children: ComponentChildren }) {
  const { t } = useI18n();
  return (
    <div className="auth-shell">
      <header className="auth-topbar">
        <a className="auth-home-link" href="/">◎ {t('auth.world')}</a>
        <div className="auth-brand">{t('auth.brand')} <span>{t('auth.controlPlane')}</span></div>
      </header>
      {children}
      <MobileWorkspaceNav />
    </div>
  );
}

export function SignInPanel({ next = '/account' }: { next?: string }) {
  const { t } = useI18n();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: Event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session = await login(username, password);
      const safeNext = next.startsWith('/') && !next.startsWith('//') ? next : '/account';
      window.location.assign(session.user?.forcePasswordChange ? '/account' : safeNext);
    } catch (caught) {
      setError(errorMessage(caught));
      setBusy(false);
    }
  };

  return (
    <main className="auth-main auth-login-layout">
      <section className="auth-intro">
        <span className="auth-kicker">{t('auth.loginKicker')}</span>
        <h1>{t('auth.loginTitle')}</h1>
        <p>{t('auth.loginDescription')}</p>
        <div className="auth-boundary-list" aria-label={t('auth.accessBoundary')}>
          <span><b>{t('auth.public')}</b> {t('auth.publicDetail')}</span>
          <span><b>{t('auth.admin')}</b> {t('auth.adminDetail')}</span>
          <span><b>API</b> {t('auth.apiDetail')}</span>
        </div>
      </section>
      <form className="auth-card auth-form" onSubmit={submit}>
        <div>
          <span className="auth-card-kicker">{t('auth.secureSession')}</span>
          <h2>{t('auth.signIn')}</h2>
          <p>{t('auth.sessionDetail')}</p>
        </div>
        <label>
          <span>{t('auth.username')}</span>
          <input
            autoComplete="username"
            value={username}
            onInput={(event) => setUsername(event.currentTarget.value)}
            placeholder="admin"
            required
          />
        </label>
        <label>
          <span>{t('auth.password')}</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onInput={(event) => setPassword(event.currentTarget.value)}
            required
          />
        </label>
        {error ? <div className="auth-alert" role="alert">{error}</div> : null}
        <button className="auth-primary" type="submit" disabled={busy}>
          {busy ? t('auth.verifying') : t('auth.enter')}
        </button>
        <small>{t('auth.protection')}</small>
      </form>
    </main>
  );
}

export function LoadingAccess() {
  const { t } = useI18n();
  return (
    <AuthFrame>
      <main className="auth-main auth-loading">
        <span className="auth-kicker">{t('auth.identityBoundary')}</span>
        <h1>{t('auth.checking')}</h1>
      </main>
    </AuthFrame>
  );
}

export function LoginWorkspace() {
  const query = new URLSearchParams(window.location.search);
  return <AuthFrame><SignInPanel next={query.get('next') || '/account'} /></AuthFrame>;
}

export function AccountWorkspace() {
  const { t, formatDateTime } = useI18n();
  const [session, setSession] = useState<AuthSession | null>(null);
  const [keys, setKeys] = useState<ProductApiKey[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [newKey, setNewKey] = useState<ProductApiKey | null>(null);
  const [keyName, setKeyName] = useState('operations-read');
  const [keyScope, setKeyScope] = useState('operations:read');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  const refresh = useCallback(async () => {
    const nextSession = await fetchAuthSession();
    setSession(nextSession);
    if (nextSession.authenticated && nextSession.user?.role === 'admin') {
      const [nextKeys, nextAudit] = await Promise.all([fetchApiKeys(), fetchAuditLog()]);
      setKeys(nextKeys);
      setAudit(nextAudit);
    }
    setReady(true);
  }, []);

  useEffect(() => {
    refresh().catch((caught) => {
      setError(errorMessage(caught));
      setReady(true);
    });
  }, [refresh]);

  if (!ready) return <LoadingAccess />;
  if (!session?.authenticated) return <AuthFrame><SignInPanel /></AuthFrame>;

  const updatePassword = async (event: Event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await changePassword(currentPassword, newPassword);
      setCurrentPassword('');
      setNewPassword('');
      setMessage(t('auth.passwordUpdated'));
      await refresh();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  const issueKey = async (event: Event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const item = await createApiKey(keyName, [keyScope]);
      setNewKey(item);
      setMessage(t('auth.keyIssued'));
      setKeys(await fetchApiKeys());
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  const signOut = async () => {
    setBusy(true);
    try {
      await logout();
      window.location.assign('/');
    } catch (caught) {
      setError(errorMessage(caught));
      setBusy(false);
    }
  };

  return (
    <AuthFrame>
      <main className="auth-main auth-account">
        <section className="auth-account-hero">
          <div>
            <span className="auth-kicker">{t('auth.accountKicker')}</span>
            <h1>{t('auth.accessControl')}</h1>
            <p>{t('auth.signedInAs')} <b>{session.user?.username}</b> · <b>{session.user?.role}</b></p>
          </div>
          <div className="auth-account-actions">
            <a href="/watchlist">{t('auth.openWatchlist')}</a>
            <a href="/briefings">{t('auth.openBriefings')}</a>
            <button type="button" onClick={signOut} disabled={busy}>{t('auth.signOut')}</button>
          </div>
        </section>

        {session.user?.forcePasswordChange ? (
          <div className="auth-alert is-warning">{t('auth.bootstrapWarning')}</div>
        ) : null}
        {message ? <div className="auth-alert is-success">{message}</div> : null}
        {error ? <div className="auth-alert" role="alert">{error}</div> : null}

        <div className="auth-account-grid">
          <form className="auth-card auth-form" onSubmit={updatePassword}>
            <div><span className="auth-card-kicker">{t('auth.sessionSecurity')}</span><h2>{t('auth.changePassword')}</h2></div>
            <label><span>{t('auth.currentPassword')}</span><input type="password" autoComplete="current-password" value={currentPassword} onInput={(event) => setCurrentPassword(event.currentTarget.value)} required /></label>
            <label><span>{t('auth.newPassword')}</span><input type="password" minLength={12} maxLength={256} autoComplete="new-password" value={newPassword} onInput={(event) => setNewPassword(event.currentTarget.value)} required /></label>
            <button className="auth-primary" type="submit" disabled={busy}>{t('auth.updatePassword')}</button>
          </form>

          {session.user?.role === 'admin' ? (
            <form className="auth-card auth-form" onSubmit={issueKey}>
              <div><span className="auth-card-kicker">{t('auth.machineAccess')}</span><h2>{t('auth.issueKey')}</h2><p>{t('auth.issueKeyDetail')}</p></div>
              <label><span>{t('auth.credentialName')}</span><input value={keyName} maxLength={80} onInput={(event) => setKeyName(event.currentTarget.value)} required /></label>
              <label>
                <span>{t('auth.readOnlyScope')}</span>
                <select value={keyScope} onChange={(event) => setKeyScope(event.currentTarget.value)}>
                  <option value="operations:read">operations:read — {t('auth.operationsScope')}</option>
                  <option value="mcp:read">mcp:read — {t('auth.mcpScope')}</option>
                </select>
              </label>
              <button className="auth-primary" type="submit" disabled={busy}>{t('auth.createKey')}</button>
              {newKey?.key ? <output className="auth-secret"><span>{t('auth.copyOnce')}</span><code>{newKey.key}</code></output> : null}
            </form>
          ) : null}
        </div>

        {session.user?.role === 'admin' ? (
          <>
            <section className="auth-card auth-table-card">
              <div className="auth-section-head"><div><span className="auth-card-kicker">{t('auth.credentials')}</span><h2>{t('auth.keyRegistry')}</h2></div><span>{t('auth.total', { count: keys.length })}</span></div>
              <div className="auth-table-wrap">
                <table><thead><tr><th>{t('auth.name')}</th><th>{t('auth.prefix')}</th><th>{t('auth.scope')}</th><th>{t('auth.lastUsed')}</th><th>{t('auth.status')}</th><th /></tr></thead>
                  <tbody>{keys.map((key) => <tr key={key.id}><td>{key.name}</td><td><code>{key.prefix}…</code></td><td>{key.scopes.join(', ')}</td><td>{key.lastUsedAt ? formatDateTime(key.lastUsedAt) : t('auth.never')}</td><td>{key.revokedAt ? t('auth.revoked') : t('auth.active')}</td><td>{!key.revokedAt ? <button type="button" onClick={() => revokeApiKey(key.id).then(refresh).catch((caught) => setError(errorMessage(caught)))}>{t('auth.revoke')}</button> : null}</td></tr>)}</tbody>
                </table>
              </div>
            </section>
            <section className="auth-card auth-table-card">
              <div className="auth-section-head"><div><span className="auth-card-kicker">{t('auth.securityEvents')}</span><h2>{t('auth.auditTrail')}</h2></div><span>{t('auth.latest', { count: audit.length })}</span></div>
              <div className="auth-table-wrap">
                <table><thead><tr><th>{t('auth.observed')}</th><th>{t('auth.actor')}</th><th>{t('auth.action')}</th><th>{t('auth.target')}</th><th>{t('auth.result')}</th></tr></thead>
                  <tbody>{audit.map((event) => <tr key={event.id}><td>{formatDateTime(event.occurredAt)}</td><td>{event.username || event.actorKind}</td><td>{event.action}</td><td>{event.targetType || '—'}</td><td>{event.result}</td></tr>)}</tbody>
                </table>
              </div>
            </section>
          </>
        ) : null}
      </main>
    </AuthFrame>
  );
}
