import { useI18n, type MessageKey } from '@/services/i18n';

const ITEMS: Array<{ href: string; key: MessageKey; icon: string; matches: (path: string) => boolean }> = [
  { href: '/', key: 'mobileNav.atlas', icon: '◎', matches: (path) => path === '/' },
  { href: '/data-quality', key: 'mobileNav.quality', icon: '◇', matches: (path) => path.startsWith('/data-quality') },
  { href: '/watchlist', key: 'mobileNav.watchlist', icon: '☆', matches: (path) => path.startsWith('/watchlist') },
  { href: '/briefings', key: 'mobileNav.briefings', icon: '▤', matches: (path) => path.startsWith('/briefings') },
  { href: '/developers', key: 'mobileNav.developers', icon: '⌘', matches: (path) => path.startsWith('/developers') },
];

export function MobileWorkspaceNav() {
  const { t } = useI18n();
  const pathname = typeof window === 'undefined' ? '/' : window.location.pathname;

  return (
    <nav className="mobile-workspace-nav" aria-label={t('mobileNav.label')}>
      {ITEMS.map((item) => {
        const active = item.matches(pathname);
        return (
          <a href={item.href} className={active ? 'is-active' : ''} aria-current={active ? 'page' : undefined} key={item.href}>
            <span aria-hidden="true">{item.icon}</span>
            <strong>{t(item.key)}</strong>
          </a>
        );
      })}
    </nav>
  );
}
