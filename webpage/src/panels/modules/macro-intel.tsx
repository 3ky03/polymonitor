import type { RuntimePolymarketMacroMapItem, RuntimePolymarketMacroMapPayload } from '@/types';
import { useSpecialistCopy } from '@/services/specialist-i18n';

export type PanelGlyphName =
  | 'geo'
  | 'radar'
  | 'calendar'
  | 'energy'
  | 'basket'
  | 'market'
  | 'cpi'
  | 'fed'
  | 'growth'
  | 'labor'
  | 'oil'
  | 'gas'
  | 'diesel'
  | 'food'
  | 'home'
  | 'policy'
  | 'rates'
  | 'source';

function numberLabel(value?: string | number | null) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '--';
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(n);
}

function probabilityLabel(value?: string | number | null) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '--';
  return `${Math.round(n * 100)}%`;
}

function normalized(value?: string | null) {
  return String(value || '').toLowerCase();
}

export function signalToneClass(value?: string | null) {
  const text = normalized(value);
  if (/(hot|rising|hawk|alert|high|sticky|underpriced)/.test(text)) return 'hot';
  if (/(cool|cooling|dovish|disinflation|soft|low)/.test(text)) return 'cool';
  if (/(watch|mixed|event|partial|degraded|warming)/.test(text)) return 'watch';
  return 'neutral';
}

export function sourceStateTone(value?: string | null) {
  const text = normalized(value);
  if (text === 'ok' || text === 'live' || text === 'official' || text === 'seeded') return 'ok';
  if (text === 'fallback' || text === 'partial' || text === 'degraded') return 'watch';
  if (text === 'error' || text === 'stale' || text === 'warming') return 'bad';
  return 'neutral';
}

const GLYPH_META: Record<PanelGlyphName, { token: string; label: string }> = {
  geo: { token: 'GEO', label: 'Geopolitical shock' },
  radar: { token: 'MAP', label: 'Macro map' },
  calendar: { token: 'CAL', label: 'Calendar event' },
  energy: { token: 'OIL', label: 'Energy' },
  basket: { token: 'CPI', label: 'Consumer basket' },
  market: { token: 'MKT', label: 'Market signal' },
  cpi: { token: 'CPI', label: 'Inflation' },
  fed: { token: 'FED', label: 'Federal Reserve' },
  growth: { token: 'GDP', label: 'Growth' },
  labor: { token: 'JOB', label: 'Labor' },
  oil: { token: 'WTI', label: 'Oil' },
  gas: { token: 'GAS', label: 'Gasoline' },
  diesel: { token: 'DSL', label: 'Diesel' },
  food: { token: 'FD', label: 'Food' },
  home: { token: 'OER', label: 'Shelter and rent' },
  policy: { token: 'POL', label: 'Policy' },
  rates: { token: '2Y', label: 'Rates' },
  source: { token: 'SRC', label: 'Source' },
};

function glyphMeta(icon: PanelGlyphName) {
  return GLYPH_META[icon] || GLYPH_META.source;
}

export function PanelGlyph({ icon, tone = 'neutral' }: { icon: PanelGlyphName; tone?: string }) {
  const { shared } = useSpecialistCopy('macro-shared');
  const meta = glyphMeta(icon);
  const label = shared(`glyph.${icon}`, meta.label);
  return (
    <span className={`wm-intel-mark ${tone}`} aria-label={label} title={label}>
      <i />
      <em>{meta.token}</em>
    </span>
  );
}

export function RowGlyph({ icon, tone = 'neutral', label }: { icon: PanelGlyphName; tone?: string; label?: string }) {
  const { shared } = useSpecialistCopy('macro-shared');
  const meta = glyphMeta(icon);
  const localizedLabel = label || shared(`glyph.${icon}`, meta.label);
  return (
    <span className={`wm-row-marker ${tone}`} aria-label={localizedLabel} title={localizedLabel}>
      <i />
      <em>{meta.token}</em>
    </span>
  );
}

export function StatusBadge({ children, tone = 'neutral' }: { children: string | number; tone?: string }) {
  return <span className={`wm-status-badge ${tone}`}>{children}</span>;
}

export function MacroAlertStrip({
  hot = 0,
  cool = 0,
  watch = 0,
}: {
  hot?: string | number | null;
  cool?: string | number | null;
  watch?: string | number | null;
}) {
  const { shared } = useSpecialistCopy('macro-shared');
  return (
    <div className="wm-macro-alert-strip">
      <span className="wm-macro-alert-chip alert"><strong>{shared('alert', 'ALERT')}</strong><em>{Number(hot) || 0}</em></span>
      <span className="wm-macro-alert-chip cool"><strong>{shared('cool', 'COOL')}</strong><em>{Number(cool) || 0}</em></span>
      <span className="wm-macro-alert-chip watch"><strong>{shared('watch', 'WATCH')}</strong><em>{Number(watch) || 0}</em></span>
    </div>
  );
}

export function SourceStack({ sources, labels }: { sources?: Record<string, string>; labels?: Record<string, string> }) {
  const entries = Object.entries(sources || {});
  if (!entries.length) return null;
  return (
    <div className="wm-macro-source-stack">
      {entries.slice(0, 6).map(([key, value]) => (
        <span key={key} className={`wm-source-pill ${sourceStateTone(value)}`}>
          <strong>{labels?.[key] || key}</strong>
          <em>{String(value || 'unknown').toUpperCase()}</em>
        </span>
      ))}
    </div>
  );
}

export function MarketImplicationStrip({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="wm-market-implication-strip">
      {items.slice(0, 4).map((item, index) => (
        <span key={`${item}-${index}`}>{item}</span>
      ))}
    </div>
  );
}

export function linkedMacroMarkets(payload?: RuntimePolymarketMacroMapPayload | null, categoryIds: string[] = []): RuntimePolymarketMacroMapItem[] {
  const wanted = new Set(categoryIds.map((item) => item.toLowerCase()));
  return (payload?.items || []).filter((item) => {
    const ids = (item.categoryIds || []).map((id) => String(id || '').toLowerCase());
    return ids.some((id) => wanted.has(id));
  });
}

export function LinkedMarketRegistry({
  title = 'Linked PMKT',
  items,
  emptyLabel = 'No linked market seeded',
}: {
  title?: string;
  items: RuntimePolymarketMacroMapItem[];
  emptyLabel?: string;
}) {
  const { shared, formatRelativeTime } = useSpecialistCopy('macro-shared');
  const resolvedTitle = title === 'Linked PMKT' ? shared('linkedMarkets', title) : title;
  const resolvedEmpty = emptyLabel === 'No linked market seeded' ? shared('noLinkedMarkets', emptyLabel) : emptyLabel;
  return (
    <div className="wm-linked-market-registry">
      <div className="wm-linked-market-header">
        <span>{resolvedTitle}</span>
        <em>{items.length ? shared('marketCount', '{count} markets', { count: items.length }) : resolvedEmpty}</em>
      </div>
      {items.slice(0, 3).map((item, index) => {
        const top = item.topOutcomes?.[0];
        return (
          <div className="wm-linked-market-row" key={`${item.eventId || item.slug || 'market'}-${index}`}>
            <div>
              <span>{(item.categoryLabels?.[0] || 'PMKT').toUpperCase()} / {item.endDate ? formatRelativeTime(item.endDate) : shared('open', 'OPEN')}</span>
              <strong>{item.title || shared('macroMarket', 'Macro market')}</strong>
            </div>
            <em>{probabilityLabel(top?.yesPrice)}</em>
          </div>
        );
      })}
      {items.length ? (
        <div className="wm-linked-market-volume">
          <span>{shared('volume', 'VOL')}</span>
          <strong>{numberLabel(items.reduce((sum, item) => sum + (Number(item.volume24h) || 0), 0))}</strong>
        </div>
      ) : null}
    </div>
  );
}
