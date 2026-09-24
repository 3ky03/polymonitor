import { RefObject } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';

export const VIDEO_IDLE_PAUSE_MS = 6 * 60 * 1000;

export function useElementInView<T extends Element>(rootMargin = '120px'): [RefObject<T>, boolean] {
  const ref = useRef<T>(null);
  const [inView, setInView] = useState(true);

  useEffect(() => {
    const element = ref.current;
    if (!element || typeof IntersectionObserver === 'undefined') {
      setInView(true);
      return undefined;
    }
    const observer = new IntersectionObserver(
      ([entry]) => setInView(Boolean(entry?.isIntersecting)),
      { root: null, rootMargin, threshold: 0.01 },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [rootMargin]);

  return [ref, inView];
}

export function useIdlePause(timeoutMs = VIDEO_IDLE_PAUSE_MS) {
  const [idle, setIdle] = useState(false);

  useEffect(() => {
    let timer = 0;
    const reset = () => {
      setIdle(false);
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setIdle(true), timeoutMs);
    };
    const events: Array<keyof WindowEventMap> = ['mousedown', 'mousemove', 'keydown', 'scroll', 'touchstart', 'pointerdown'];
    events.forEach((eventName) => window.addEventListener(eventName, reset, { passive: true }));
    reset();
    return () => {
      window.clearTimeout(timer);
      events.forEach((eventName) => window.removeEventListener(eventName, reset));
    };
  }, [timeoutMs]);

  return idle;
}

export function useStaggeredLoad(enabled: boolean, delayMs = 0) {
  const [ready, setReady] = useState(delayMs <= 0);

  useEffect(() => {
    if (!enabled) {
      setReady(false);
      return undefined;
    }
    if (delayMs <= 0) {
      setReady(true);
      return undefined;
    }
    const timer = window.setTimeout(() => setReady(true), delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs, enabled]);

  return enabled && ready;
}

export function youtubeBridgeMessageMatches(event: MessageEvent, iframe: HTMLIFrameElement | null, videoId: string) {
  if (!iframe || event.source !== iframe.contentWindow) return false;
  const payload = event.data as { type?: string; videoId?: string } | null;
  if (!payload || typeof payload !== 'object') return false;
  if (!String(payload.type || '').startsWith('yt-')) return false;
  return !payload.videoId || payload.videoId === videoId;
}
