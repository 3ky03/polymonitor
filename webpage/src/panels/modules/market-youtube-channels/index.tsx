import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import { buildRuntimeYoutubeEmbedUrl, fetchRuntimeMarketYoutubeChannels } from '@/services/api';
import type { RuntimeMarketTvWireItem, RuntimeMarketYoutubeChannelsPayload } from '@/types';
import { useStaggeredLoad, youtubeBridgeMessageMatches } from '../../shared/videoPlayback';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { useSpecialistCopy } from '@/services/specialist-i18n';

const YOUTUBE_VIDEO_ID_RE = /^[A-Za-z0-9_-]{11}$/;
const YOUTUBE_PANEL_LIMIT = 80;
const YOUTUBE_LIVE_ROTATE_MS = 10 * 60 * 1000;
const YOUTUBE_VIDEO_ROTATE_MS = 7 * 60 * 1000;

function badgeLabel(payload?: RuntimeMarketYoutubeChannelsPayload | null) {
  const status = String(payload?.status || '').toLowerCase();
  const cacheMode = String(payload?.cacheMode || '').toLowerCase();
  if (cacheMode.includes('stale')) return 'STALE';
  if (status === 'warming') return 'WARM';
  if (status === 'empty') return 'EMPTY';
  if (status === 'degraded') return 'MIXED';
  return 'LIVE';
}

function panelStatus(payload?: RuntimeMarketYoutubeChannelsPayload | null): 'live' | 'muted' {
  const status = String(payload?.status || '').toLowerCase();
  return status === 'warming' || status === 'empty' ? 'muted' : 'live';
}

function itemTitle(item: RuntimeMarketTvWireItem) {
  return item.youtubeLiveTitle || item.displayName || item.name || 'YouTube source';
}

function channelName(item: RuntimeMarketTvWireItem) {
  return item.youtubeChannelName || item.displayName || item.sourceName || 'YouTube';
}

function categoryLabel(value?: string | null) {
  const category = String(value || 'other').toUpperCase();
  return category === 'GEO' ? 'GEO' : category;
}

function categoryToneClass(value?: string | null) {
  const category = String(value || 'all').toLowerCase().replace(/[^a-z0-9-]/g, '');
  return `tone-${category || 'all'}`;
}

function sourceLocation(item: RuntimeMarketTvWireItem) {
  return [item.region, item.country, item.language].filter(Boolean).join(' / ') || 'GLOBAL';
}

function youtubeVideoId(item?: RuntimeMarketTvWireItem | null) {
  const liveId = String(item?.youtubeLiveVideoId || '').trim();
  if (YOUTUBE_VIDEO_ID_RE.test(liveId)) return liveId;
  const fallbackId = String(item?.fallbackVideoId || '').trim();
  return YOUTUBE_VIDEO_ID_RE.test(fallbackId) ? fallbackId : '';
}

function hasPlayableYoutubeEmbed(item?: RuntimeMarketTvWireItem | null) {
  const mode = String(item?.youtubeEmbedMode || '').toLowerCase();
  return mode === 'live-video' || mode === 'video' || Boolean(youtubeVideoId(item));
}

function youtubeEmbedUrl(item?: RuntimeMarketTvWireItem | null) {
  if (!hasPlayableYoutubeEmbed(item)) return '';
  const videoId = youtubeVideoId(item);
  if (videoId) return buildRuntimeYoutubeEmbedUrl(videoId, { autoplay: true, mute: true, quality: 'hd720' });
  return String(item?.youtubeEmbedUrl || '').trim();
}

function probeLabel(item?: RuntimeMarketTvWireItem | null) {
  const status = String(item?.youtubeProbeStatus || '').toLowerCase();
  if (status === 'live') return 'LIVE';
  if (status === 'offline') return 'VIDEO';
  if (status === 'error') return 'FALLBACK';
  return 'OPEN';
}

function scoreLabel(value?: string | number | null) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? String(Math.round(numeric)) : '--';
}

function openExternal(url?: string | null) {
  const target = String(url || '').trim();
  if (!target) return;
  window.open(target, '_blank', 'noopener,noreferrer');
}

function stableHash(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function playableYoutubeItems(items: RuntimeMarketTvWireItem[]) {
  return items.filter((item) => youtubeEmbedUrl(item));
}

function rotatingDefaultYoutubeItem(items: RuntimeMarketTvWireItem[], category: string, generatedAt?: string | null) {
  const playable = playableYoutubeItems(items);
  if (!playable.length) return items[0] || null;
  const nonNasa = playable.filter((item) => !/nasa/i.test(`${item.id} ${item.displayName || ''} ${item.youtubeChannelName || ''}`));
  const pool = nonNasa.length > 1 ? nonNasa : playable;
  const sixHourBucket = Math.floor(Date.now() / (6 * 60 * 60 * 1000));
  const seed = `${category}:${String(generatedAt || '').slice(0, 13)}:${sixHourBucket}:${pool.length}`;
  return pool[stableHash(seed) % pool.length] || pool[0] || null;
}

function nextYoutubeItem(items: RuntimeMarketTvWireItem[], currentId?: string | null) {
  const playable = playableYoutubeItems(items);
  const pool = playable.length ? playable : items;
  if (!pool.length) return null;
  const currentIndex = pool.findIndex((item) => item.id === currentId);
  return pool[(currentIndex + 1 + pool.length) % pool.length] || pool[0] || null;
}

function MarketYoutubePlayer({ item, onEnded }: { item: RuntimeMarketTvWireItem; onEnded?: () => void }) {
  const { copy, shared } = useSpecialistCopy('market-youtube-channels');
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const endedRef = useRef(false);
  const embedUrl = youtubeEmbedUrl(item);
  const videoId = youtubeVideoId(item);
  const externalUrl = item.externalUrl || item.sourceUrl || (videoId ? `https://www.youtube.com/watch?v=${videoId}` : null);
  const [frameState, setFrameState] = useState<'connecting' | 'playing' | 'waiting' | 'blocked' | 'external'>(embedUrl ? 'connecting' : 'external');
  const shouldLoad = useStaggeredLoad(Boolean(embedUrl), 900);

  useEffect(() => {
    endedRef.current = false;
    if (!embedUrl) {
      setFrameState('external');
      return undefined;
    }
    if (!shouldLoad) {
      setFrameState('waiting');
      return undefined;
    }
    setFrameState('connecting');
    const timer = window.setTimeout(() => setFrameState((state) => (state === 'playing' || state === 'blocked' ? state : 'waiting')), 12000);
    return () => window.clearTimeout(timer);
  }, [embedUrl, shouldLoad]);

  useEffect(() => {
    const iframe = iframeRef.current;
    if (!embedUrl || !iframe) return undefined;
    const handleMessage = (event: MessageEvent) => {
      if (!youtubeBridgeMessageMatches(event, iframe, videoId)) return;
      const data = event.data as { type?: string; state?: number | string };
      const type = String(data.type || '');
      if (type === 'yt-ready') setFrameState('waiting');
      if (type === 'yt-state') {
        const state = Number(data.state);
        if (state === 1 || state === 3) setFrameState('playing');
        else if (state === 2 || state === 5) setFrameState('waiting');
        else if (state === 0) {
          setFrameState('waiting');
          if (!endedRef.current) {
            endedRef.current = true;
            window.setTimeout(() => onEnded?.(), 800);
          }
        }
      }
      if (type === 'yt-error') {
        setFrameState('blocked');
        if (!endedRef.current) {
          endedRef.current = true;
          window.setTimeout(() => onEnded?.(), 1800);
        }
      }
      if (type === 'yt-timeout' || type === 'yt-autoplay-failed') setFrameState('waiting');
    };
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [embedUrl, onEnded, videoId]);

  useEffect(() => {
    const iframe = iframeRef.current;
    if (!iframe?.contentWindow || !embedUrl || !shouldLoad) return;
    iframe.contentWindow.postMessage({ type: 'play' }, '*');
  }, [embedUrl, shouldLoad]);

  return (
    <div className={`wm-market-youtube-player ${frameState === 'blocked' ? 'blocked' : ''}`}>
      <header>
        <div>
          <span>{categoryLabel(item.category)} / {sourceLocation(item)} / {probeLabel(item)}</span>
          <strong>{itemTitle(item)}</strong>
        </div>
        <button type="button" onClick={() => openExternal(externalUrl)}>
          {shared('open', 'OPEN')}
        </button>
      </header>
      {embedUrl && shouldLoad ? (
        <iframe
          ref={iframeRef}
          className="wm-market-youtube-frame"
          src={embedUrl}
          title={itemTitle(item)}
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
          allowFullScreen
          referrerPolicy="strict-origin-when-cross-origin"
          onLoad={() => setFrameState('waiting')}
        />
      ) : embedUrl ? (
        <div className="wm-market-youtube-fallback">
          <strong>{channelName(item)}</strong>
          <em>{copy('preparingPlayback', 'Preparing embedded playback.')}</em>
          <button type="button" onClick={() => openExternal(externalUrl)}>
            {shared('open', 'OPEN')}
          </button>
        </div>
      ) : (
        <div className="wm-market-youtube-fallback">
          <strong>{channelName(item)}</strong>
          <em>{item.marketUseCase || copy('openExternal', 'Open the YouTube channel externally. Current live embed is not verified.')}</em>
          <button type="button" onClick={() => openExternal(externalUrl)}>
            {shared('open', 'OPEN')}
          </button>
        </div>
      )}
      {embedUrl && frameState === 'blocked' ? (
        <div className="wm-market-youtube-blocked">
          <strong>{copy('embedBlocked', 'YOUTUBE EMBED BLOCKED')}</strong>
          <em>{copy('embedBlockedText', 'This channel or video does not allow embedded playback. Open the source page.')}</em>
          <button type="button" onClick={() => openExternal(externalUrl)}>
            {shared('open', 'OPEN')}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function MarketYoutubeRow({
  item,
  active,
  onSelect,
}: {
  item: RuntimeMarketTvWireItem;
  active: boolean;
  onSelect: () => void;
}) {
  const { copy } = useSpecialistCopy('market-youtube-channels');
  const tags = (item.matchedTerms?.length ? item.matchedTerms : item.marketTags || []).slice(0, 3);
  return (
    <button type="button" className={`wm-market-youtube-row ${active ? 'active' : ''}`} onClick={onSelect}>
      <span className={`wm-market-youtube-dot ${youtubeEmbedUrl(item) ? 'live' : 'ready'}`} />
      <div>
        <small>{categoryLabel(item.category)} / {probeLabel(item)}</small>
        <strong>{channelName(item)}</strong>
        <em>{item.marketUseCase || item.sourceName || copy('liveContext', 'Live video context.')}</em>
        {tags.length ? (
          <span className="wm-market-youtube-tags">
            {tags.map((tag) => <i key={`${item.id}-${tag}`}>{String(tag).toUpperCase()}</i>)}
          </span>
        ) : null}
      </div>
      <b>{scoreLabel(item.relevanceScore)}</b>
    </button>
  );
}

function MarketYoutubeChannelsPanel({ payload }: { payload?: RuntimeMarketYoutubeChannelsPayload | null }) {
  const { copy, shared, formatRelativeTime } = useSpecialistCopy('market-youtube-channels');
  const [activeCategory, setActiveCategory] = useState('all');
  const [activeId, setActiveId] = useState<string | null>(null);
  const [categoryPayload, setCategoryPayload] = useState<{ category: string; payload: RuntimeMarketYoutubeChannelsPayload } | null>(null);
  const [categoryLoading, setCategoryLoading] = useState(false);
  const [categoryError, setCategoryError] = useState<string | null>(null);
  const [autoRotate, setAutoRotate] = useState(true);
  const selectedPayload = activeCategory === 'all'
    ? payload
    : (categoryPayload?.category === activeCategory ? categoryPayload.payload : null);
  const items = selectedPayload?.items || [];
  const summary = payload?.summary || selectedPayload?.summary || {};
  const categories = payload?.categories || selectedPayload?.categories || [];
  const totalCount = Number(summary.total ?? payload?.selection?.total ?? items.length);
  const visibleItems = useMemo(() => items, [items]);
  const activeItem = visibleItems.find((item) => item.id === activeId) || null;
  const defaultItem = useMemo(
    () => rotatingDefaultYoutubeItem(visibleItems, activeCategory, selectedPayload?.generatedAt || payload?.generatedAt),
    [activeCategory, payload?.generatedAt, selectedPayload?.generatedAt, visibleItems],
  );
  const playerItem = activeItem || defaultItem || null;

  const advanceToNextItem = () => {
    const next = nextYoutubeItem(visibleItems, playerItem?.id || activeId);
    if (next) setActiveId(next.id);
  };

  useEffect(() => {
    let cancelled = false;
    setActiveId(null);
    setCategoryError(null);
    if (activeCategory === 'all') {
      setCategoryPayload(null);
      setCategoryLoading(false);
      return undefined;
    }
    setCategoryLoading(true);
    fetchRuntimeMarketYoutubeChannels(YOUTUBE_PANEL_LIMIT, activeCategory)
      .then((nextPayload) => {
        if (cancelled) return;
        setCategoryPayload({ category: activeCategory, payload: nextPayload });
      })
      .catch((error) => {
        if (cancelled) return;
        setCategoryError(error instanceof Error ? error.message : copy('loadCategoryFailed', 'Failed to load YouTube category'));
      })
      .finally(() => {
        if (!cancelled) setCategoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeCategory]);

  useEffect(() => {
    if (!visibleItems.length) {
      setActiveId(null);
      return;
    }
    if (!activeId || !visibleItems.some((item) => item.id === activeId)) {
      setActiveId(defaultItem?.id || null);
    }
  }, [activeId, defaultItem, visibleItems]);

  useEffect(() => {
    if (!autoRotate || !playerItem || visibleItems.length <= 1) return undefined;
    const isLive = String(playerItem.youtubeEmbedMode || '').toLowerCase() === 'live-video' || String(playerItem.youtubeProbeStatus || '').toLowerCase() === 'live';
    const timer = window.setTimeout(advanceToNextItem, isLive ? YOUTUBE_LIVE_ROTATE_MS : YOUTUBE_VIDEO_ROTATE_MS);
    return () => window.clearTimeout(timer);
  }, [activeId, autoRotate, playerItem?.id, visibleItems]);

  return (
    <Panel
      title={copy('title', 'YOUTUBE MARKET TV')}
      badge={badgeLabel(payload)}
      status={panelStatus(payload)}
      count={items.length || undefined}
      className="wm-market-panel wm-market-youtube-panel"
      dataPanelId="market-youtube-channels"
    >
      <div className="wm-market-youtube-layout">
        <aside className="wm-market-youtube-sidebar">
          <div className="wm-market-youtube-tabs">
            <button
              type="button"
              className={`${categoryToneClass('all')} ${activeCategory === 'all' ? 'active' : ''}`}
              onClick={() => setActiveCategory('all')}
            >
              {shared('all', 'ALL')} <span>{Number.isFinite(totalCount) ? totalCount : items.length}</span>
            </button>
            {categories.map((category) => (
              <button
                key={category.id}
                type="button"
                className={`${categoryToneClass(category.id)} ${activeCategory === category.id ? 'active' : ''}`}
                onClick={() => setActiveCategory(category.id)}
              >
                {category.label} <span>{category.count}</span>
              </button>
            ))}
          </div>
          <div className="wm-market-youtube-summary">
            <span><strong>{summary.liveReady ?? 0}</strong> {shared('live', 'LIVE')}</span>
            <span><strong>{summary.embedReady ?? 0}</strong> {shared('embed', 'EMBED')}</span>
            <button
              type="button"
              className={autoRotate ? 'active' : ''}
              onClick={() => setAutoRotate((value) => !value)}
            >
              {shared('auto', 'AUTO')}
            </button>
          </div>
          <div className="wm-market-youtube-list">
            {visibleItems.map((item) => (
              <MarketYoutubeRow
                key={item.id}
                item={item}
                active={(activeId || playerItem?.id) === item.id}
                onSelect={() => setActiveId(item.id)}
              />
            ))}
          </div>
        </aside>
        {playerItem ? <MarketYoutubePlayer item={playerItem} onEnded={autoRotate ? advanceToNextItem : undefined} /> : (
          <div className="wm-empty-state">
            <strong>{categoryLoading ? copy('loading', 'YouTube TV loading.') : copy('warming', 'YouTube TV warming.')}</strong>
            <em>{categoryError || copy('notReady', 'GCP seeded YouTube channel snapshot is not ready yet.')}</em>
          </div>
        )}
      </div>
      <footer className="wm-market-youtube-foot">
        <span>{formatRelativeTime(selectedPayload?.generatedAt || payload?.generatedAt)}</span>
        <em>{selectedPayload?.cacheMode || payload?.cacheMode || 'seeded'}</em>
      </footer>
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'market-youtube-channels': {
    render: (ctx) => {
      const payload = ctx.runtimeData['market-youtube-channels'] as RuntimeMarketYoutubeChannelsPayload | undefined;
      return <MarketYoutubeChannelsPanel payload={payload} />;
    },
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'market-youtube-channels',
  title: 'YouTube Market TV',
  eyebrow: 'content',
  description: 'Curated YouTube live and video channels for market context.',
  size: 'wide',
  defaultEnabled: true,
}, {
  tier: 'slow',
  intervalMs: 180000,
  fetchData: () => fetchRuntimeMarketYoutubeChannels(YOUTUBE_PANEL_LIMIT),
});
