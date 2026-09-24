import { Panel } from '@/components/Panel';
import type { ContentItem } from '@/types';
import { useMemo, useState } from 'preact/hooks';
import type { PanelRenderMap, PanelRuntimeContext } from './types';
import { contentList } from './shared/renderers';
import { contentByType, focusedContent } from './shared/selectors';
import { useI18n, type MessageKey } from '@/services/i18n';

type IntelTab = {
  id: 'news' | 'video' | 'report' | 'research';
  labelKey: MessageKey;
};

const INTEL_TABS: IntelTab[] = [
  { id: 'news', labelKey: 'atlasIntel.news' },
  { id: 'video', labelKey: 'atlasIntel.video' },
  { id: 'report', labelKey: 'atlasIntel.reports' },
  { id: 'research', labelKey: 'atlasIntel.research' },
];

function explicitContentType(item: ContentItem) {
  return String(item.contentType || '').trim().toLowerCase();
}

function inferredContentType(item: ContentItem): IntelTab['id'] {
  const explicit = explicitContentType(item);
  if (explicit === 'video' || explicit === 'report' || explicit === 'research') return explicit;
  const source = String(item.source || '').toLowerCase();
  const url = String(item.url || '').toLowerCase();
  const title = String(item.title || '').toLowerCase();
  const haystack = `${source} ${url} ${title}`;
  if (/youtube|youtu\.be|vimeo|twitch\.tv/.test(haystack)) return 'video';
  if (/\.pdf($|[?#])|annual-report|whitepaper|research-report|special-report/.test(haystack)) return 'report';
  if (/arxiv\.org|ssrn\.com|nber\.org|working paper|research paper|journal/.test(haystack)) return 'research';
  return 'news';
}

function smartContentByType(items: ContentItem[], tab: IntelTab['id']) {
  return items.filter((item) => inferredContentType(item) === tab);
}

function RelatedIntelPanel({ ctx }: { ctx: PanelRuntimeContext }) {
  const i18n = useI18n();
  const { t } = i18n;
  const [activeTab, setActiveTab] = useState<IntelTab['id']>('news');
  const items = focusedContent(ctx);
  const tabItems = useMemo(() => Object.fromEntries(
    INTEL_TABS.map((tab) => [tab.id, smartContentByType(items, tab.id)]),
  ) as Record<IntelTab['id'], ReturnType<typeof contentByType>>, [items]);
  const visibleItems = tabItems[activeTab] || [];
  const activeLabel = t(INTEL_TABS.find((tab) => tab.id === activeTab)?.labelKey || 'atlasIntel.intel');
  const emptyMessage = activeTab === 'news'
    ? t('atlasIntel.noNews')
    : t('atlasIntel.noType', { type: activeLabel });

  return (
    <Panel
      title={t('atlasIntel.title')}
      status="live"
      count={items.length}
      className="wm-market-panel wm-content-feed-panel wm-related-news-panel wm-related-intel-panel"
    >
      <div className="wm-intel-filter-tabs" role="tablist" aria-label={t('atlasIntel.types')}>
        {INTEL_TABS.map((tab) => (
          <button
            aria-selected={activeTab === tab.id}
            className={activeTab === tab.id ? 'active' : ''}
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            role="tab"
            type="button"
          >
            <span>{t(tab.labelKey)}</span>
            <b>{i18n.formatNumber(tabItems[tab.id]?.length || 0)}</b>
          </button>
        ))}
      </div>
      {contentList(visibleItems, emptyMessage, 20, {
        untitled: t('atlasIntel.untitled'),
        readSource: t('atlasIntel.readSource'),
        formatDate: (value) => value ? i18n.formatDateTime(value) : '--',
        empty: {
          label: t('atlasShared.standby'),
          detail: t('atlasShared.emptyDetail'),
        },
      })}
    </Panel>
  );
}

export const contentPanelRenderers: PanelRenderMap = {
  'related-news': {
    render: (ctx) => <RelatedIntelPanel ctx={ctx} />,
  },
  'related-video': {
    render: (ctx) => (
      <Panel title="VIDEO FEED" badge="VIDEO" status="muted" count={contentByType(focusedContent(ctx), 'video').length} className="wm-market-panel wm-content-feed-panel wm-related-video-panel">
        {contentList(contentByType(focusedContent(ctx), 'video'), 'No linked videos yet.')}
      </Panel>
    ),
  },
  'report-feed': {
    render: (ctx) => (
      <Panel title="REPORT FEED" badge="REPORT" status="muted" count={contentByType(ctx.latestContent, 'report').length} className="wm-market-panel wm-content-feed-panel wm-report-feed-panel">
        {contentList(contentByType(ctx.latestContent, 'report'), 'No linked reports yet.')}
      </Panel>
    ),
  },
  'research-feed': {
    render: (ctx) => (
      <Panel title="RESEARCH FEED" badge="RESEARCH" status="muted" count={contentByType(ctx.latestContent, 'research').length} className="wm-market-panel wm-content-feed-panel wm-research-feed-panel">
        {contentList(contentByType(ctx.latestContent, 'research'), 'No linked research yet.')}
      </Panel>
    ),
  },
};
