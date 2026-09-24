import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import { buildRuntimeHlsProxyUrl, buildRuntimeYoutubeEmbedUrl, fetchRuntimeMarketTvWire } from '@/services/api';
import type { RuntimeMarketTvWireItem, RuntimeMarketTvWirePayload } from '@/types';
import { useStaggeredLoad, youtubeBridgeMessageMatches } from '../../shared/videoPlayback';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { useSpecialistCopy } from '@/services/specialist-i18n';

type PlaybackState = 'connecting' | 'playing' | 'waiting' | 'blocked' | 'external';
const YOUTUBE_VIDEO_ID_RE = /^[A-Za-z0-9_-]{11}$/;
const HLS_FAILURE_COOLDOWN_MS = 5 * 60 * 1000;

function badgeLabel(payload?: RuntimeMarketTvWirePayload | null) {
  const status = String(payload?.status || '').toLowerCase();
  const cacheMode = String(payload?.cacheMode || '').toLowerCase();
  if (cacheMode.includes('stale')) return 'STALE';
  if (status === 'warming') return 'WARM';
  if (status === 'degraded') return 'DEGRADED';
  if (status === 'empty') return 'EMPTY';
  return 'LIVE';
}

function panelStatus(payload?: RuntimeMarketTvWirePayload | null): 'live' | 'muted' {
  const status = String(payload?.status || '').toLowerCase();
  return status === 'warming' || status === 'empty' ? 'muted' : 'live';
}

function scoreLabel(value?: string | number | null) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return String(Math.round(numeric));
}

function itemTitle(item: RuntimeMarketTvWireItem) {
  return item.displayName || item.name || 'Live video source';
}

function categoryLabel(value?: string | null) {
  const category = String(value || 'other').toUpperCase();
  if (category === 'GEO') return 'GEO';
  return category;
}

function categoryToneClass(value?: string | null) {
  const category = String(value || 'all').toLowerCase().replace(/[^a-z0-9-]/g, '');
  return `tone-${category || 'all'}`;
}

function sourceTypeLabel(value?: string | null) {
  const sourceType = String(value || 'external').toLowerCase();
  if (sourceType === 'youtube') return 'YT';
  if (sourceType === 'external') return 'EXT';
  if (sourceType === 'timelapse') return 'TL';
  return 'HLS';
}

function statusClass(value?: string | null) {
  const status = String(value || 'unknown').toLowerCase();
  if (status === 'ready') return 'ready';
  if (status === 'not_24_7' || status === 'stale' || status === 'unknown') return 'stale';
  return 'blocked';
}

function statusLabel(value?: string | null) {
  const status = String(value || 'unknown').toLowerCase();
  if (status === 'not_24_7') return 'NOT 24/7';
  return status.toUpperCase();
}

function sourceLocation(item: RuntimeMarketTvWireItem) {
  return [item.region, item.country, item.language].filter(Boolean).join(' / ') || 'GLOBAL';
}

function openExternal(url?: string | null) {
  const target = String(url || '').trim();
  if (!target) return;
  window.open(target, '_blank', 'noopener,noreferrer');
}

function isHlsPreviewable(item?: RuntimeMarketTvWireItem | null) {
  const probeStatus = String(item?.hlsProbeStatus || '').toLowerCase();
  const status = String(item?.status || '').toLowerCase();
  const hasHlsUrl = Boolean(String(item?.hlsUrl || '').trim());
  if (String(item?.sourceType || '').toLowerCase() !== 'hls' || !hasHlsUrl) return false;
  if (probeStatus) return probeStatus === 'playable' || probeStatus === 'unverified';
  return status === 'ready' || status === 'not_24_7';
}

function hlsPlaybackUrl(item?: RuntimeMarketTvWireItem | null) {
  const hlsUrl = String(item?.hlsUrl || '').trim();
  if (!hlsUrl) return '';
  const strategy = String(item?.playbackStrategy || '').toLowerCase();
  if (item?.hlsProxyRequired || strategy === 'proxied-hls') return buildRuntimeHlsProxyUrl(hlsUrl);
  return hlsUrl;
}

function isInPlaybackCooldown(item: RuntimeMarketTvWireItem | null | undefined, cooldowns: Record<string, number>) {
  if (!item?.id) return false;
  return Number(cooldowns[item.id] || 0) > Date.now();
}

function youtubeVideoId(item?: RuntimeMarketTvWireItem | null) {
  const liveId = String(item?.youtubeLiveVideoId || '').trim();
  if (YOUTUBE_VIDEO_ID_RE.test(liveId)) return liveId;
  const fallbackId = String(item?.fallbackVideoId || '').trim();
  return YOUTUBE_VIDEO_ID_RE.test(fallbackId) ? fallbackId : '';
}

function youtubeEmbedUrl(item?: RuntimeMarketTvWireItem | null) {
  const mode = String(item?.youtubeEmbedMode || '').toLowerCase();
  if (mode && mode !== 'live-video' && mode !== 'video') return '';
  const videoId = youtubeVideoId(item);
  if (videoId) return buildRuntimeYoutubeEmbedUrl(videoId, { autoplay: true, mute: true, quality: 'hd720' });
  const embedded = String(item?.youtubeEmbedUrl || '').trim();
  return embedded;
}

function isYoutubePreviewable(item?: RuntimeMarketTvWireItem | null) {
  return String(item?.sourceType || '').toLowerCase() === 'youtube' && Boolean(youtubeEmbedUrl(item) || youtubeVideoId(item));
}

function isEmbeddedPreviewable(item?: RuntimeMarketTvWireItem | null, cooldowns: Record<string, number> = {}) {
  if (isInPlaybackCooldown(item, cooldowns)) return false;
  return isHlsPreviewable(item) || isYoutubePreviewable(item);
}

function youtubeProbeLabel(item?: RuntimeMarketTvWireItem | null) {
  const probeStatus = String(item?.youtubeProbeStatus || '').toLowerCase();
  if (probeStatus === 'live') return 'YOUTUBE LIVE';
  if (probeStatus === 'offline') return 'YT VERIFIED';
  if (probeStatus === 'error') return 'YT FALLBACK';
  return 'YOUTUBE';
}

function MarketTvPreview({
  item,
  onPlaybackBlocked,
}: {
  item: RuntimeMarketTvWireItem;
  onPlaybackBlocked?: (item: RuntimeMarketTvWireItem) => void;
}) {
  const { copy, shared } = useSpecialistCopy('market-tv-wire');
  const videoRef = useRef<HTMLVideoElement>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const sourceType = String(item.sourceType || '').toLowerCase();
  const rawHlsUrl = String(item.hlsUrl || '').trim();
  const initialHlsUrl = hlsPlaybackUrl(item);
  const youtubeId = youtubeVideoId(item);
  const youtubeUrl = youtubeEmbedUrl(item);
  const canProxyFallback = Boolean(rawHlsUrl && initialHlsUrl === rawHlsUrl && item.playbackTier === 'trusted-hls');
  const blockedReportedRef = useRef(false);
  const [activeHlsUrl, setActiveHlsUrl] = useState(initialHlsUrl);
  const [playbackState, setPlaybackState] = useState<PlaybackState>(initialHlsUrl ? 'connecting' : 'external');
  const [youtubeFrameState, setYoutubeFrameState] = useState<PlaybackState>(youtubeUrl ? 'connecting' : 'external');
  const shouldLoad = useStaggeredLoad(Boolean(initialHlsUrl || youtubeUrl), 200);

  useEffect(() => {
    setActiveHlsUrl(initialHlsUrl);
  }, [initialHlsUrl, item.id]);

  useEffect(() => {
    const video = videoRef.current;
    let cancelled = false;
    let destroyHls: (() => void) | null = null;
    let recoveries = 0;

    blockedReportedRef.current = false;
    setPlaybackState(activeHlsUrl ? (shouldLoad ? 'connecting' : 'waiting') : 'external');
    if (!video || !activeHlsUrl || !shouldLoad) {
      return undefined;
    }

    const markPlaying = () => setPlaybackState('playing');
    const markWaiting = () => setPlaybackState('waiting');
    const reportBlocked = () => {
      if (blockedReportedRef.current) return;
      blockedReportedRef.current = true;
      onPlaybackBlocked?.(item);
    };
    const tryProxyFallback = () => {
      if (!canProxyFallback || activeHlsUrl !== rawHlsUrl) return false;
      setPlaybackState('waiting');
      setActiveHlsUrl(buildRuntimeHlsProxyUrl(rawHlsUrl));
      return true;
    };
    const markBlocked = () => {
      if (recoveries < 2) {
        recoveries += 1;
        setPlaybackState('waiting');
        window.setTimeout(() => {
          if (!cancelled) video.play().catch(() => setPlaybackState('waiting'));
        }, 800);
        return;
      }
      if (tryProxyFallback()) return;
      setPlaybackState('blocked');
      reportBlocked();
    };
    video.addEventListener('playing', markPlaying);
    video.addEventListener('waiting', markWaiting);
    video.addEventListener('error', markBlocked);

    const startNative = () => {
      video.src = activeHlsUrl;
      video.load();
      video.play().catch(() => setPlaybackState('waiting'));
    };

    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      startNative();
    } else {
      import('hls.js')
        .then(({ default: Hls }) => {
          if (cancelled) return;
          if (!Hls.isSupported()) {
            setPlaybackState('blocked');
            return;
          }
          const hls = new Hls({
            enableWorker: true,
            lowLatencyMode: true,
            liveSyncDurationCount: 3,
          });
          destroyHls = () => hls.destroy();
          hls.loadSource(activeHlsUrl);
          hls.attachMedia(video);
          hls.on(Hls.Events.MANIFEST_PARSED, () => {
            video.play().catch(() => setPlaybackState('waiting'));
          });
          hls.on(Hls.Events.ERROR, (_event, data) => {
            if (!data?.fatal) return;
            if (recoveries < 2 && data.type === Hls.ErrorTypes.NETWORK_ERROR) {
              recoveries += 1;
              setPlaybackState('waiting');
              hls.startLoad();
              return;
            }
            if (recoveries < 2 && data.type === Hls.ErrorTypes.MEDIA_ERROR) {
              recoveries += 1;
              setPlaybackState('waiting');
              hls.recoverMediaError();
              return;
            }
            if (tryProxyFallback()) return;
            setPlaybackState('blocked');
            reportBlocked();
          });
        })
        .catch(() => {
          setPlaybackState('blocked');
          reportBlocked();
        });
    }

    return () => {
      cancelled = true;
      video.removeEventListener('playing', markPlaying);
      video.removeEventListener('waiting', markWaiting);
      video.removeEventListener('error', markBlocked);
      destroyHls?.();
      video.pause();
      video.removeAttribute('src');
      video.load();
    };
  }, [activeHlsUrl, canProxyFallback, item.id, rawHlsUrl, shouldLoad]);

  useEffect(() => {
    if (!youtubeUrl || !shouldLoad) {
      setYoutubeFrameState('external');
      return undefined;
    }
    setYoutubeFrameState('connecting');
    const timer = window.setTimeout(() => setYoutubeFrameState((state) => (state === 'playing' || state === 'blocked' ? state : 'waiting')), 12000);
    return () => window.clearTimeout(timer);
  }, [youtubeUrl, shouldLoad]);

  useEffect(() => {
    const iframe = iframeRef.current;
    if (!youtubeUrl || !iframe) return undefined;
    const handleMessage = (event: MessageEvent) => {
      if (!youtubeBridgeMessageMatches(event, iframe, youtubeId)) return;
      const data = event.data as { type?: string; state?: number | string };
      const type = String(data.type || '');
      if (type === 'yt-ready') setYoutubeFrameState('waiting');
      if (type === 'yt-state') {
        const state = Number(data.state);
        if (state === 1 || state === 3) setYoutubeFrameState('playing');
        else if (state === 2 || state === 0 || state === 5) setYoutubeFrameState('waiting');
      }
      if (type === 'yt-error') setYoutubeFrameState('blocked');
      if (type === 'yt-timeout' || type === 'yt-autoplay-failed') setYoutubeFrameState('waiting');
    };
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [youtubeUrl, youtubeId]);

  useEffect(() => {
    const iframe = iframeRef.current;
    if (!iframe?.contentWindow || !youtubeUrl || !shouldLoad) return;
    iframe.contentWindow.postMessage({ type: 'play' }, '*');
  }, [shouldLoad, youtubeUrl]);

  if (youtubeUrl && (sourceType === 'youtube' || !rawHlsUrl)) {
    return (
      <div className="wm-market-tv-preview youtube">
        <div className="wm-market-tv-preview-head">
          <div>
            <strong>{item.youtubeLiveTitle || itemTitle(item)}</strong>
            <span>{categoryLabel(item.category)} / {sourceLocation(item)} / {youtubeProbeLabel(item)}</span>
          </div>
          <em className={`wm-market-tv-playback ${youtubeFrameState === 'blocked' ? 'blocked' : 'playing'}`}>
            {youtubeFrameState === 'blocked' ? shared('blocked', 'BLOCKED') : youtubeProbeLabel(item)}
          </em>
          <button type="button" onClick={() => openExternal(item.externalUrl || item.sourceUrl || `https://www.youtube.com/watch?v=${youtubeId}`)}>
            {shared('open', 'OPEN')}
          </button>
        </div>
        <div className="wm-market-tv-stage">
          {shouldLoad ? (
            <iframe
              ref={iframeRef}
              className="wm-market-tv-youtube-frame"
              src={youtubeUrl}
              title={item.youtubeLiveTitle || itemTitle(item)}
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
              allowFullScreen
              referrerPolicy="strict-origin-when-cross-origin"
              onLoad={() => setYoutubeFrameState('waiting')}
            />
          ) : (
            <div className="wm-market-tv-fallback passive">{copy('streamStandby', 'STREAM PAUSED · STANDBY')}</div>
          )}
          {youtubeFrameState === 'blocked' ? (
            <button
              type="button"
              className="wm-market-tv-fallback"
              onClick={() => openExternal(item.externalUrl || item.sourceUrl || `https://www.youtube.com/watch?v=${youtubeId}`)}
            >
              {copy('youtubeBlockedOpen', 'YOUTUBE EMBED BLOCKED · OPEN')}
            </button>
          ) : null}
        </div>
      </div>
    );
  }

  if (!activeHlsUrl) {
    return (
      <div className="wm-market-tv-preview external">
        <div>
          <span>{sourceTypeLabel(item.sourceType)}</span>
          <strong>{itemTitle(item)}</strong>
          <em>{item.marketUseCase || item.sourceName || copy('openOfficialSource', 'Open official live source.')}</em>
        </div>
        <button type="button" onClick={() => openExternal(item.externalUrl || item.sourceUrl)}>
          {shared('open', 'OPEN')}
        </button>
      </div>
    );
  }

  return (
    <div className="wm-market-tv-preview">
      <div className="wm-market-tv-preview-head">
        <div>
          <strong>{itemTitle(item)}</strong>
          <span>{categoryLabel(item.category)} / {sourceLocation(item)} / {sourceTypeLabel(item.sourceType)}</span>
        </div>
        <em className={`wm-market-tv-playback ${playbackState}`}>{playbackState.toUpperCase()}</em>
        <button type="button" onClick={() => openExternal(item.externalUrl || item.sourceUrl || rawHlsUrl)}>
          {shared('open', 'OPEN')}
        </button>
      </div>
      <div className="wm-market-tv-stage">
        <video ref={videoRef} className="wm-market-tv-video" controls muted playsInline />
        {playbackState === 'blocked' ? (
          <button
            type="button"
            className="wm-market-tv-fallback"
            onClick={() => openExternal(item.externalUrl || item.sourceUrl || rawHlsUrl)}
          >
            {copy('streamBlockedOpen', 'STREAM BLOCKED · OPEN SOURCE')}
          </button>
        ) : null}
      </div>
    </div>
  );
}

function MarketTvRow({
  item,
  active,
  onPreview,
  cooldowns,
}: {
  item: RuntimeMarketTvWireItem;
  active: boolean;
  onPreview: () => void;
  cooldowns?: Record<string, number>;
}) {
  const { copy, shared, formatRelativeTime } = useSpecialistCopy('market-tv-wire');
  const previewReady = isEmbeddedPreviewable(item, cooldowns || {});
  const tags = (item.matchedTerms?.length ? item.matchedTerms : item.marketTags || []).slice(0, 3);
  return (
    <article className={`wm-market-tv-row ${active ? 'active' : ''}`}>
      <button type="button" className="wm-market-tv-row-main" onClick={onPreview} title={item.marketUseCase || itemTitle(item)}>
        <span className={`wm-market-tv-dot ${statusClass(item.status)}`} />
        <div className="wm-market-tv-copy">
          <div className="wm-market-tv-meta">
            <span>{categoryLabel(item.category)}</span>
            <span>{sourceTypeLabel(item.sourceType)}</span>
            <span className={statusClass(item.status)}>{statusLabel(item.status)}</span>
            <span>{sourceLocation(item)}</span>
          </div>
          <strong>{itemTitle(item)}</strong>
          <p>{item.marketUseCase || item.sourceName || copy('liveContext', 'Live source for market context.')}</p>
          {tags.length ? (
            <div className="wm-market-tv-tags">
              {tags.map((tag) => <span key={`${item.id}-${tag}`}>{String(tag).toUpperCase()}</span>)}
            </div>
          ) : null}
        </div>
        <div className="wm-market-tv-score">
          <strong>{scoreLabel(item.relevanceScore)}</strong>
          <span>{previewReady ? shared('watch', 'WATCH') : shared('open', 'OPEN')}</span>
          <em>{formatRelativeTime(item.lastCheckedAt)}</em>
        </div>
      </button>
    </article>
  );
}

function MarketTvWirePanel({ payload }: { payload?: RuntimeMarketTvWirePayload | null }) {
  const { copy, shared } = useSpecialistCopy('market-tv-wire');
  const [showHelp, setShowHelp] = useState(false);
  const [activeCategory, setActiveCategory] = useState('all');
  const [activeId, setActiveId] = useState<string | null>(null);
  const [categoryPayload, setCategoryPayload] = useState<{ category: string; payload: RuntimeMarketTvWirePayload } | null>(null);
  const [categoryLoading, setCategoryLoading] = useState(false);
  const [categoryError, setCategoryError] = useState<string | null>(null);
  const [hlsCooldowns, setHlsCooldowns] = useState<Record<string, number>>({});
  const selectedPayload = activeCategory === 'all'
    ? payload
    : (categoryPayload?.category === activeCategory ? categoryPayload.payload : null);
  const items = selectedPayload?.items || [];
  const categories = payload?.categories || selectedPayload?.categories || [];
  const summary = payload?.summary || selectedPayload?.summary || {};
  const totalCount = Number(summary.total ?? payload?.selection?.total ?? items.length);
  const visibleItems = useMemo(() => items, [items]);
  const activeItem = visibleItems.find((item) => item.id === activeId) || null;
  const usableActiveItem = activeItem && !isInPlaybackCooldown(activeItem, hlsCooldowns) ? activeItem : null;
  const previewItem = usableActiveItem || visibleItems.find((item) => isEmbeddedPreviewable(item, hlsCooldowns)) || visibleItems[0] || null;

  useEffect(() => {
    const now = Date.now();
    if (!Object.values(hlsCooldowns).some((expiresAt) => expiresAt <= now)) return undefined;
    setHlsCooldowns((current) => Object.fromEntries(Object.entries(current).filter(([, expiresAt]) => expiresAt > now)));
    return undefined;
  }, [hlsCooldowns]);

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
    fetchRuntimeMarketTvWire(60, activeCategory)
      .then((nextPayload) => {
        if (cancelled) return;
        setCategoryPayload({ category: activeCategory, payload: nextPayload });
      })
      .catch((error) => {
        if (cancelled) return;
        setCategoryError(error instanceof Error ? error.message : copy('loadCategoryFailed', 'Failed to load category'));
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
      setActiveId((visibleItems.find((item) => isEmbeddedPreviewable(item, hlsCooldowns)) || visibleItems[0])?.id || null);
    }
  }, [activeId, visibleItems, hlsCooldowns]);

  const handlePlaybackBlocked = (item: RuntimeMarketTvWireItem) => {
    setHlsCooldowns((current) => ({
      ...current,
      [item.id]: Date.now() + HLS_FAILURE_COOLDOWN_MS,
    }));
    setActiveId((current) => (current === item.id ? null : current));
  };

  return (
    <Panel
      title={copy('title', 'MARKET TV WIRE')}
      titleControls={(
        <button
          type="button"
          className="wm-panel-help-button"
          aria-label={copy('explainAria', 'Explain Market TV Wire panel')}
          aria-expanded={showHelp}
          onClick={() => setShowHelp((value) => !value)}
        >
          ?
        </button>
      )}
      badge={badgeLabel(payload)}
      status={panelStatus(payload)}
      count={items.length || undefined}
      headerOverlay={showHelp ? (
        <div className="wm-panel-help-popover">
          <strong>{copy('helpTitle', 'Market TV Wire')}</strong>
          <p>{copy('helpText', 'GCP-seeded live video sources ranked for Polymarket context. Stale snapshots stay visible when source refresh fails.')}</p>
        </div>
      ) : null}
      className="wm-market-panel wm-market-tv-panel"
      dataPanelId="market-tv-wire"
    >
      <div className="wm-market-tv-layout">
        <div className="wm-market-tv-control-rail">
          <div className="wm-market-tv-tabs">
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
          <div className="wm-market-tv-summary">
            <span><strong>{summary.liveReady ?? 0}</strong> {shared('ready', 'READY')}</span>
            <span><strong>{summary.marketMatched ?? 0}</strong> {shared('matched', 'MATCHED')}</span>
            <span><strong>{summary.regions ?? 0}</strong> {shared('regions', 'REGIONS')}</span>
            <span><strong>{summary.staleCount ?? 0}</strong> {shared('stale', 'STALE')}</span>
          </div>
          {visibleItems.length ? (
            <div className="wm-market-tv-list">
              {visibleItems.map((item) => (
                <MarketTvRow
                  key={item.id}
                  item={item}
                  active={(activeId || previewItem?.id) === item.id}
                  cooldowns={hlsCooldowns}
                  onPreview={() => setActiveId(item.id)}
                />
              ))}
            </div>
          ) : null}
        </div>
        {previewItem ? <MarketTvPreview item={previewItem} onPlaybackBlocked={handlePlaybackBlocked} /> : (
          <div className="wm-empty-state">
            <strong>{categoryLoading ? copy('loading', 'Market TV Wire loading.') : copy('warming', 'Market TV Wire warming.')}</strong>
            <em>{categoryError || copy('notReady', 'GCP seed snapshot or selected channel category is not ready yet.')}</em>
          </div>
        )}
      </div>
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'market-tv-wire': {
    render: (ctx) => {
      const payload = ctx.runtimeData['market-tv-wire'] as RuntimeMarketTvWirePayload | undefined;
      return <MarketTvWirePanel payload={payload} />;
    },
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'market-tv-wire',
  title: 'Market TV Wire',
  eyebrow: 'content',
  description: 'GCP-seeded live video sources ranked for market context.',
  size: 'wide',
  defaultEnabled: true,
}, {
  tier: 'slow',
  intervalMs: 180000,
  fetchData: () => fetchRuntimeMarketTvWire(60),
});
