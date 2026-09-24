import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { fetchAviationViewport } from '@/services/api';
import type { AviationViewportPayload } from '@/types';

const CACHE_TTL_MS = 30_000;
const viewportCache = new Map<string, { storedAt: number; payload: AviationViewportPayload }>();

function viewportForCamera(center: [number, number], zoom: number): [number, number, number, number] | null {
  if (zoom < 2) return null;
  const width = Math.max(4, 360 / (2 ** zoom) * 1.65);
  const height = Math.max(3, width * 0.58);
  const threshold = Math.max(0.5, width * 0.18);
  const lon = Math.round(center[0] / threshold) * threshold;
  const lat = Math.round(center[1] / threshold) * threshold;
  const west = Math.max(-180, lon - width / 2);
  const east = Math.min(180, lon + width / 2);
  const south = Math.max(-85, lat - height / 2);
  const north = Math.min(85, lat + height / 2);
  if (west >= east || south >= north) return null;
  return [west, south, east, north].map((value) => Number(value.toFixed(3))) as [number, number, number, number];
}

export function useAviationViewport(
  enabled: boolean,
  center: [number, number],
  zoom: number,
) {
  const bbox = useMemo(() => viewportForCamera(center, zoom), [center[0], center[1], zoom]);
  const key = bbox ? `${Math.floor(zoom)}:${bbox.join(',')}` : '';
  const [payload, setPayload] = useState<AviationViewportPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const generationRef = useRef(0);

  useEffect(() => {
    const generation = ++generationRef.current;
    if (!enabled || !bbox) {
      setPayload(null);
      setError(null);
      return undefined;
    }
    const cached = viewportCache.get(key);
    if (cached && Date.now() - cached.storedAt <= CACHE_TTL_MS) setPayload(cached.payload);
    const controller = new AbortController();
    void fetchAviationViewport(bbox, zoom, controller.signal).then((next) => {
      if (controller.signal.aborted || generation !== generationRef.current) return;
      viewportCache.set(key, { storedAt: Date.now(), payload: next });
      setPayload(next);
      setError(null);
    }).catch((reason) => {
      if (controller.signal.aborted || generation !== generationRef.current) return;
      setError(reason instanceof Error ? reason.message : String(reason));
    });
    return () => controller.abort();
  }, [enabled, key]);

  return { payload, error, bbox, loading: enabled && Boolean(bbox) && !payload && !error };
}
