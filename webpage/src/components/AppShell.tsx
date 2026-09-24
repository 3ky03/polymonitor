import type { ComponentChildren } from 'preact';
import { MobileWorkspaceNav } from '@/components/MobileWorkspaceNav';
import { PwaControl } from '@/components/PwaControl';
import { ResetWorkspaceIcon } from '@/components/icons/ShellIcons';
import { useI18n, type MessageKey } from '@/services/i18n';

const APP_VERSION = 'v0.2.1';
const PRIMARY_NAV_LINKS: Array<{ key: MessageKey; href: string }> = [
  { key: 'nav.markets', href: '/' },
  { key: 'nav.quality', href: '/data-quality' },
  { key: 'nav.watchlist', href: '/watchlist' },
  { key: 'nav.briefings', href: '/briefings' },
];

const RESOURCE_NAV_LINKS: Array<{ key: MessageKey; href: string; external?: boolean }> = [
  { key: 'nav.blog', href: '/blog/' },
  { key: 'nav.docs', href: '/docs/documentation/' },
  { key: 'nav.github', href: 'https://github.com/3ky03/polymonitor', external: true },
  { key: 'nav.developers', href: '/developers' },
  { key: 'nav.quant', href: '/quant' },
];

type RegionOption = {
  value: string;
  label: string;
};

type AppShellProps = {
  children: ComponentChildren;
  regionValue: string;
  regionOptions: RegionOption[];
  orderFilledCount: number;
  onRegionChange: (region: string) => void;
  onResetWorkspace: () => void;
  onOpenCommandPalette: () => void;
  onTogglePanelLibrary: () => void;
  onOpenSettings: () => void;
  onCopyLink: () => void;
};

export function AppShell({
  children,
  regionValue,
  regionOptions,
  orderFilledCount,
  onRegionChange,
  onResetWorkspace,
  onOpenCommandPalette,
  onTogglePanelLibrary,
  onOpenSettings,
  onCopyLink,
}: AppShellProps) {
  const { locale, setLocale, t } = useI18n();
  const pathname = typeof window === 'undefined' ? '/' : window.location.pathname;
  const isActivePath = (href: string) => href === '/'
    ? pathname === '/'
    : pathname === href || pathname.startsWith(`${href}/`);
  return (
    <div className="wm-shell">
      <div className="wm-promo">
        <span className="wm-pro-badge">{t('shell.proBadge')}</span>
        <span className="wm-promo-copy">
          <strong>{t('shell.promoHeadline')}</strong>
          <span> — {t('shell.promoTagline')}</span>
        </span>
      </div>

      <header className="wm-toolbar">
        <div className="wm-toolbar-left">
          <button
            className="wm-workspace-mark"
            type="button"
            onClick={onResetWorkspace}
            aria-label={t('shell.home')}
            title={t('shell.home')}
          >
            <ResetWorkspaceIcon />
          </button>
          <a className="wm-brand" href="/" aria-label="PolyData Monitor">
            POLYDATA MONITOR <span>{APP_VERSION}</span>
          </a>
          <div className="wm-live-dot" role="status">{t('shell.live')}</div>
          <label className="wm-region-select">
            <span className="sr-only">{t('shell.region')}</span>
            <select
              value={regionValue}
              onChange={(event) => onRegionChange(event.currentTarget.value)}
              aria-label={t('shell.region')}
            >
              {regionOptions.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
        </div>
        <nav className="wm-site-nav" aria-label={t('shell.resources')}>
          {PRIMARY_NAV_LINKS.map((link) => (
            <a
              key={link.key}
              className={isActivePath(link.href) ? 'active' : undefined}
              href={link.href}
              aria-current={isActivePath(link.href) ? 'page' : undefined}
            >
              {t(link.key)}
            </a>
          ))}
          <details className="wm-more-nav">
            <summary aria-haspopup="menu">{t('shell.more')}</summary>
            <div className="wm-more-nav-menu" role="menu">
              {RESOURCE_NAV_LINKS.map((link) => (
                <a
                  key={link.key}
                  className={link.key === 'nav.quant' ? 'wm-site-nav-quant' : undefined}
                  href={link.href}
                  target={link.external ? '_blank' : undefined}
                  rel={link.external ? 'noopener noreferrer' : undefined}
                  role="menuitem"
                  onClick={(event) => event.currentTarget.closest('details')?.removeAttribute('open')}
                >
                  {t(link.key)}
                </a>
              ))}
              <div className="wm-more-actions" role="none">
                <a href="/account" role="menuitem">{t('nav.access')}</a>
                <button
                  type="button"
                  role="menuitem"
                  onClick={(event) => {
                    onCopyLink();
                    event.currentTarget.closest('details')?.removeAttribute('open');
                  }}
                >
                  {t('shell.copyLink')}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={(event) => {
                    onTogglePanelLibrary();
                    event.currentTarget.closest('details')?.removeAttribute('open');
                  }}
                >
                  {t('shell.panels')}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={(event) => {
                    onOpenSettings();
                    event.currentTarget.closest('details')?.removeAttribute('open');
                  }}
                >
                  {t('shell.settingsShort')}
                </button>
              </div>
            </div>
          </details>
        </nav>
        <div className="wm-toolbar-right">
          <PwaControl />
          <span className="wm-fill-status" role="status" title={t('shell.recentFillsTitle', { count: orderFilledCount })}>
            <strong>{orderFilledCount}</strong>
            <span>{t('shell.recentFills')}</span>
          </span>
          <button className="wm-tool-button" type="button" onClick={onOpenCommandPalette}>{t('shell.search')}</button>
          <label className="wm-language-switch">
            <span className="sr-only">{t('settings.language')}</span>
            <select value={locale} onChange={(event) => setLocale(event.currentTarget.value === 'zh' ? 'zh' : 'en')}>
              <option value="en">EN</option>
              <option value="zh">中文</option>
            </select>
          </label>
        </div>
      </header>
      {children}
      <MobileWorkspaceNav />
    </div>
  );
}
