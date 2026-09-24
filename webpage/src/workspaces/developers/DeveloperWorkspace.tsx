import { useState } from 'preact/hooks';
import { MobileWorkspaceNav } from '@/components/MobileWorkspaceNav';
import { useI18n, type MessageKey } from '@/services/i18n';

const TOOL_KEYS: MessageKey[] = [
  'developer.tool.search',
  'developer.tool.market',
  'developer.tool.oracle',
  'developer.tool.liquidity',
  'developer.tool.quality',
  'developer.tool.briefing',
  'developer.tool.watchlist',
];

export function DeveloperWorkspace() {
  const { locale, setLocale, t } = useI18n();
  const [copied, setCopied] = useState(false);
  const origin = typeof window === 'undefined' ? 'https://polymonitor.club' : window.location.origin;
  const endpoint = `${origin}/wm-api/mcp`;
  const copyEndpoint = async () => {
    await navigator.clipboard.writeText(endpoint);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };

  return (
    <div className="developer-shell">
      <header className="developer-topbar">
        <a href="/">{t('developer.home')}</a>
        <div>{t('developer.brand')} <span>{t('developer.controlPlane')}</span></div>
        <select aria-label={t('settings.language')} value={locale} onChange={(event) => setLocale(event.currentTarget.value === 'zh' ? 'zh' : 'en')}>
          <option value="en">EN</option>
          <option value="zh">中文</option>
        </select>
      </header>

      <main className="developer-main">
        <section className="developer-hero">
          <span>{t('developer.kicker')}</span>
          <h1>{t('developer.title')}</h1>
          <p>{t('developer.description')}</p>
          <div className="developer-endpoint">
            <small>{t('developer.endpoint')}</small>
            <code>{endpoint}</code>
            <button type="button" onClick={() => void copyEndpoint()}>{copied ? t('developer.copied') : t('developer.copy')}</button>
          </div>
        </section>

        <section className="developer-metrics" aria-label="MCP contract">
          <article><span>{t('developer.protocol')}</span><strong>{t('developer.protocolValue')}</strong></article>
          <article><span>{t('developer.tools')}</span><strong>{t('developer.toolsValue')}</strong></article>
          <article><span>{t('developer.auth')}</span><strong>{t('developer.authValue')}</strong></article>
        </section>

        <section className="developer-grid">
          <article className="developer-panel developer-boundary">
            <span>PRIVACY BY CONSTRUCTION</span>
            <h2>{t('developer.boundaryTitle')}</h2>
            <p>{t('developer.boundaryBody')}</p>
            <div className="developer-actions">
              <a href="/account">{t('developer.issueKey')}</a>
              <a href="/.well-known/mcp/server-card.json">{t('developer.discovery')}</a>
              <a href="/wm-api/openapi.json">{t('developer.openapi')}</a>
              <a href="/sdk/polymonitor-v1.mjs">{t('developer.sdk')}</a>
            </div>
          </article>
          <article className="developer-panel">
            <span>TOOLS / LIST</span>
            <h2>{t('developer.toolTitle')}</h2>
            <ol className="developer-tool-list">
              {TOOL_KEYS.map((key, index) => <li key={key}><b>{String(index + 1).padStart(2, '0')}</b>{t(key)}</li>)}
            </ol>
          </article>
        </section>

        <section className="developer-grid">
          <article className="developer-panel developer-quickstart">
            <span>STREAMABLE HTTP</span>
            <h2>{t('developer.quickstart')}</h2>
            <p>{t('developer.quickstartDetail')}</p>
            <pre><code>{`{
  "mcpServers": {
    "polymonitor": {
      "url": "${endpoint}",
      "headers": {
        "Authorization": "Bearer pm_live_…"
      }
    }
  }
}`}</code></pre>
          </article>
          <article className="developer-panel">
            <span>{t('developer.security')}</span>
            <ul className="developer-security-list">
              <li>{t('developer.securityOne')}</li>
              <li>{t('developer.securityTwo')}</li>
              <li>{t('developer.securityThree')}</li>
              <li>{t('developer.securityFour')}</li>
            </ul>
          </article>
        </section>

        <section className="developer-panel developer-sdk">
          <span>{t('developer.sdkKicker')}</span>
          <h2>{t('developer.sdkTitle')}</h2>
          <p>{t('developer.sdkDetail')}</p>
          <pre><code>{`import { PolyMonitorClient } from '${origin}/sdk/polymonitor-v1.mjs';

const client = new PolyMonitorClient();
const markets = await client.searchMarkets({ q: 'election', pageSize: 10 });
const quality = await client.getDataQuality();`}</code></pre>
          <div className="developer-actions">
            <a href="/sdk/polymonitor-v1.d.ts">{t('developer.types')}</a>
            <a href="/wm-api/v1/openapi.json">{t('developer.openapiV1')}</a>
          </div>
        </section>
      </main>
      <MobileWorkspaceNav />
    </div>
  );
}
