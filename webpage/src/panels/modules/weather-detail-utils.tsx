import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { fetchMarketLobByToken } from '@/services/api';
import type { LobPayload, RuntimeGlobalWeatherCity, RuntimeGlobalWeatherMapPayload, RuntimeWeatherQuoteBin } from '@/types';
import { formatRelative } from '../shared/formatters';

export function num(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function tempLabel(value?: string | number | null, unit?: string | null) {
  const parsed = num(value);
  if (parsed === null) return '--';
  return `${Math.round(parsed)}°${unit || ''}`;
}

export function currentWeatherTemp(city?: RuntimeGlobalWeatherCity | null) {
  return city?.currentTemp ?? city?.metarTemp ?? city?.todayHigh ?? null;
}

export function highWeatherTemp(city?: RuntimeGlobalWeatherCity | null) {
  return city?.forecastHigh ?? city?.todayHigh ?? city?.currentTemp ?? city?.metarTemp ?? null;
}

export function priceLabel(value?: string | number | null) {
  const parsed = num(value);
  if (parsed === null) return '--';
  return `${Math.round(parsed * 1000) / 10}%`;
}

export function bookPrice(bin?: RuntimeWeatherQuoteBin | null) {
  const bid = num(bin?.bestBidYes);
  const ask = num(bin?.bestAskYes);
  if (bid !== null && ask !== null) return (bid + ask) / 2;
  return bid ?? ask ?? null;
}

export function bookMidPrice(bin?: RuntimeWeatherQuoteBin | null) {
  const bid = num(bin?.bestBidYes);
  const ask = num(bin?.bestAskYes);
  if (bid === null || ask === null) return null;
  return (bid + ask) / 2;
}

type LiveWeatherQuote = Pick<RuntimeWeatherQuoteBin, 'bestBidYes' | 'bestAskYes' | 'bookStatus' | 'priceSource'>;

const LIVE_WEATHER_BOOK_TTL_MS = 10_000;
const LIVE_WEATHER_BOOK_CACHE = new Map<string, { expiresAt: number; quote?: LiveWeatherQuote; promise?: Promise<LiveWeatherQuote> }>();

function bestLevelValue(levels: Array<{ price?: string | number | null }> | undefined, mode: 'bid' | 'ask') {
  const values = (levels || []).map((level) => num(level.price)).filter((value): value is number => value !== null);
  if (!values.length) return null;
  return mode === 'bid' ? Math.max(...values) : Math.min(...values);
}

function quoteFromLob(lob: LobPayload | null): LiveWeatherQuote {
  const yes = lob?.yes || {};
  const bid = num(yes.bestBid) ?? bestLevelValue(yes.bids, 'bid');
  const ask = num(yes.bestAsk) ?? bestLevelValue(yes.asks, 'ask');
  return {
    bestBidYes: bid,
    bestAskYes: ask,
    bookStatus: bid !== null || ask !== null ? 'ok' : (lob?.bookStatus || 'no-book'),
    priceSource: bid !== null || ask !== null ? 'clob-book' : undefined,
  };
}

function liveBookPromise(tokenId: string, title: string) {
  const now = Date.now();
  const cached = LIVE_WEATHER_BOOK_CACHE.get(tokenId);
  if (cached?.quote && cached.expiresAt > now) return Promise.resolve(cached.quote);
  if (cached?.promise && cached.expiresAt > now) return cached.promise;
  const promise = fetchMarketLobByToken(tokenId, title, '', 1800)
    .then((lob) => quoteFromLob(lob))
    .catch((): LiveWeatherQuote => ({ bestBidYes: null, bestAskYes: null, bookStatus: 'error', priceSource: undefined }))
    .then((quote) => {
      LIVE_WEATHER_BOOK_CACHE.set(tokenId, { quote, expiresAt: Date.now() + LIVE_WEATHER_BOOK_TTL_MS });
      return quote;
    });
  LIVE_WEATHER_BOOK_CACHE.set(tokenId, { promise, expiresAt: now + LIVE_WEATHER_BOOK_TTL_MS });
  return promise;
}

function mergeLiveQuote(bin: RuntimeWeatherQuoteBin, quote?: LiveWeatherQuote): RuntimeWeatherQuoteBin {
  if (!quote) return bin;
  const bid = num(quote.bestBidYes);
  const ask = num(quote.bestAskYes);
  const next: RuntimeWeatherQuoteBin = {
    ...bin,
    bookStatus: quote.bookStatus || bin.bookStatus,
  };
  if (bid !== null || ask !== null) {
    next.bestBidYes = bid;
    next.bestAskYes = ask;
    next.priceSource = 'clob-book';
    if (bid !== null && ask !== null) next.midPriceYes = Math.round(((bid + ask) / 2) * 10_000) / 10_000;
  }
  return next;
}

export function useLiveWeatherQuoteBins(city?: RuntimeGlobalWeatherCity | null) {
  const seedBins = useMemo(() => displayQuoteBins(city), [city]);
  const tokenKey = seedBins.map((bin) => String(bin.yesTokenId || '')).filter(Boolean).join('|');
  const [state, setState] = useState<{ key: string; quotes: Record<string, LiveWeatherQuote>; loading: boolean }>({
    key: '',
    quotes: {},
    loading: false,
  });

  useEffect(() => {
    if (!tokenKey) {
      setState({ key: '', quotes: {}, loading: false });
      return;
    }
    let cancelled = false;
    const now = Date.now();
    const immediate: Record<string, LiveWeatherQuote> = {};
    const requests: Array<Promise<[string, LiveWeatherQuote]>> = [];
    for (const bin of seedBins) {
      const tokenId = String(bin.yesTokenId || '').trim();
      if (!tokenId) continue;
      const cached = LIVE_WEATHER_BOOK_CACHE.get(tokenId);
      if (cached?.quote && cached.expiresAt > now) {
        immediate[tokenId] = cached.quote;
        continue;
      }
      requests.push(liveBookPromise(tokenId, String(bin.label || '')).then((quote) => [tokenId, quote]));
    }
    setState({ key: tokenKey, quotes: immediate, loading: requests.length > 0 });
    if (!requests.length) return;
    Promise.all(requests).then((entries) => {
      if (cancelled) return;
      setState({
        key: tokenKey,
        quotes: entries.reduce<Record<string, LiveWeatherQuote>>((acc, [tokenId, quote]) => {
          acc[tokenId] = quote;
          return acc;
        }, { ...immediate }),
        loading: false,
      });
    });
    return () => {
      cancelled = true;
    };
  }, [seedBins, tokenKey]);

  const quotes = state.key === tokenKey ? state.quotes : {};
  return {
    bins: seedBins.map((bin) => mergeLiveQuote(bin, quotes[String(bin.yesTokenId || '')])),
    loading: state.key === tokenKey && state.loading,
  };
}

export function selectedWeatherCity(payload?: RuntimeGlobalWeatherMapPayload | null, selectedCityId?: string | null) {
  const items = payload?.items || [];
  if (!items.length) return null;
  return items.find((item) => String(item.cityId || '') === String(selectedCityId || '')) || items[0] || null;
}

export function bestQuoteBin(city?: RuntimeGlobalWeatherCity | null): RuntimeWeatherQuoteBin | null {
  if (!city) return null;
  if (city.topBin) return city.topBin;
  let best: RuntimeWeatherQuoteBin | null = null;
  for (const bin of city.bins || []) {
    if ((num(bin.midPriceYes) ?? -1) > (num(best?.midPriceYes) ?? -1)) best = bin;
  }
  return best;
}

export function bestBookQuoteBin(city?: RuntimeGlobalWeatherCity | null): RuntimeWeatherQuoteBin | null {
  let best: RuntimeWeatherQuoteBin | null = null;
  for (const bin of city?.bins || []) {
    const value = bookMidPrice(bin);
    if (value !== null && value > (bookMidPrice(best) ?? -1)) best = bin;
  }
  return best;
}

export function bookCoverage(city?: RuntimeGlobalWeatherCity | null) {
  const bins = city?.bins || [];
  if (!bins.length) return '0/0';
  return `${bins.filter((bin) => num(bin.bestBidYes) !== null || num(bin.bestAskYes) !== null).length}/${bins.length}`;
}

export function bookMidCoverage(city?: RuntimeGlobalWeatherCity | null) {
  const bins = city?.bins || [];
  if (!bins.length) return '0/0';
  return `${bins.filter((bin) => bookMidPrice(bin) !== null).length}/${bins.length}`;
}

export function midCoverage(city?: RuntimeGlobalWeatherCity | null) {
  const bins = city?.bins || [];
  if (!bins.length) return '0/0';
  return `${bins.filter((bin) => num(bin.midPriceYes) !== null).length}/${bins.length}`;
}

export function expectedQuoteBins(city?: RuntimeGlobalWeatherCity | null): RuntimeWeatherQuoteBin[] {
  if (!city) return [];
  const unit = city.unit || '';
  const anchor = num(city.forecastHigh ?? city.todayHigh ?? city.currentTemp ?? city.metarTemp);
  if (anchor === null) return [];
  const center = Math.round(anchor);
  const start = center - 5;
  return Array.from({ length: 11 }, (_, index) => {
    const value = start + index;
    const label = index === 0
      ? `${value}°${unit} or below`
      : index === 10
        ? `${value}°${unit} or higher`
        : `${value}°${unit}`;
    return {
      label,
      bucketType: index === 0 ? 'lte' : index === 10 ? 'gte' : 'eq',
      minTemp: value,
      maxTemp: value,
      unit,
      bestBidYes: null,
      bestAskYes: null,
      midPriceYes: null,
      marketStatus: 'Missing Quote',
    };
  });
}

export function displayQuoteBins(city?: RuntimeGlobalWeatherCity | null): RuntimeWeatherQuoteBin[] {
  const family = String(city?.marketFamily || city?.metricType || '').toLowerCase();
  if (city?.bins?.length) return city.bins;
  if (family && !family.includes('temperature')) return [];
  return expectedQuoteBins(city);
}

export function quoteCoverage(city?: RuntimeGlobalWeatherCity | null) {
  if (!city) return '0/0';
  if (city.quoteCoverage) return city.quoteCoverage;
  const bins = city.bins || [];
  if (!bins.length) return '0/0';
  return `${bins.filter((bin) => num(bin.midPriceYes) !== null).length}/${bins.length}`;
}

export function statusBadge(status?: string | null) {
  const text = String(status || '').toLowerCase();
  if (text === 'ok') return 'LIVE';
  if (text === 'degraded') return 'PARTIAL';
  if (text === 'warming') return 'WARMING';
  return text ? text.toUpperCase() : 'SEED';
}

export function panelStatus(status?: string | null): 'live' | 'muted' {
  return String(status || '').toLowerCase() === 'ok' ? 'live' : 'muted';
}

export function sourceStatus(city?: RuntimeGlobalWeatherCity | null) {
  const sourceStates = city?.sourceStates || {};
  const bad = Object.entries(sourceStates).find(([, value]) => !['ok', 'empty'].includes(String(value).toLowerCase()));
  if (bad) return `${bad[0]} ${bad[1]}`;
  if (sourceStates.polymarket === 'ok') return 'market linked';
  if (sourceStates.openMeteo === 'ok') return 'weather live';
  if (sourceStates.metar === 'ok') return 'metar live';
  return 'seed';
}

export function marketSourceLabel(city?: RuntimeGlobalWeatherCity | null) {
  const source = String(city?.marketSource || '').toLowerCase();
  if (source === 'psql-db') return 'PSQL DB';
  if (source === 'gamma-api') return 'GAMMA API';
  if (source) return source.toUpperCase();
  return 'NO MARKET';
}

export function weatherSourceLabel(city?: RuntimeGlobalWeatherCity | null, payload?: RuntimeGlobalWeatherMapPayload | null) {
  const states = city?.sourceStates || {};
  const openMeteo = String(states.openMeteo || payload?.sources?.openMeteo || '').toLowerCase();
  const wttr = String(states.wttr || payload?.sources?.wttr || '').toLowerCase();
  const metar = String(states.metar || states.aviationWeather || payload?.sources?.aviationWeather || '').toLowerCase();
  if (wttr === 'ok') return 'WTTR LIVE';
  if ((city?.weatherCarryForward || openMeteo === 'stale') && metar === 'ok') return 'METAR LIVE';
  if (city?.weatherCarryForward || openMeteo === 'stale') return 'WX STALE';
  if (openMeteo === 'ok') return 'OPEN-METEO';
  if (metar === 'ok') return 'METAR OK';
  if (openMeteo === 'error') return 'WX ERROR';
  return 'WX SEED';
}

export function forecastSourceLabel(city?: RuntimeGlobalWeatherCity | null, payload?: RuntimeGlobalWeatherMapPayload | null) {
  const states = city?.sourceStates || {};
  const openMeteo = String(states.openMeteo || payload?.sources?.openMeteo || '').toLowerCase();
  const wttr = String(states.wttr || payload?.sources?.wttr || '').toLowerCase();
  if (wttr === 'ok') return 'WTTR LIVE';
  if (city?.weatherCarryForward || openMeteo === 'stale') return 'WX STALE';
  if (openMeteo === 'ok') return 'OPEN-METEO';
  if (openMeteo === 'error') return 'WX ERROR';
  return 'WX SEED';
}

export function updatedLabel(city?: RuntimeGlobalWeatherCity | null, fallback?: string | null) {
  return formatRelative(city?.updatedAt || city?.hourly?.[0]?.time || fallback || null);
}

export function WeatherCanvasSparkline({
  values,
  className = '',
}: {
  values: number[];
  className?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || values.length < 2) return;
    const rect = canvas.getBoundingClientRect();
    const pixelRatio = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.floor(rect.width));
    const height = Math.max(1, Math.floor(rect.height));
    canvas.width = Math.floor(width * pixelRatio);
    canvas.height = Math.floor(height * pixelRatio);
    const context = canvas.getContext('2d');
    if (!context) return;
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    context.clearRect(0, 0, width, height);

    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = Math.max(1, max - min);
    const xFor = (index: number) => (index / Math.max(1, values.length - 1)) * (width - 4) + 2;
    const yFor = (value: number) => height - 3 - ((value - min) / range) * (height - 6);

    context.strokeStyle = 'rgba(255,255,255,0.08)';
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(0, height - 4);
    context.lineTo(width, height - 4);
    context.moveTo(0, Math.round(height * 0.5));
    context.lineTo(width, Math.round(height * 0.5));
    context.stroke();

    context.strokeStyle = getComputedStyle(canvas).color || '#7edcff';
    context.lineWidth = 2;
    context.lineJoin = 'round';
    context.lineCap = 'round';
    context.beginPath();
    values.forEach((value, index) => {
      const x = xFor(index);
      const y = yFor(value);
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.stroke();

    context.fillStyle = context.strokeStyle;
    values.forEach((value, index) => {
      const x = xFor(index);
      const y = yFor(value);
      context.beginPath();
      context.arc(x, y, 1.6, 0, Math.PI * 2);
      context.fill();
    });
  }, [values]);

  return <canvas ref={canvasRef} className={className} aria-hidden="true" />;
}

export function WeatherMiniLine({
  city,
  className = '',
  limit = 24,
}: {
  city?: RuntimeGlobalWeatherCity | null;
  className?: string;
  limit?: number;
}) {
  const points = (city?.hourly || []).filter((point) => num(point.temp) !== null).slice(0, limit);
  if (points.length < 2) return <div className={`wm-weather-detail-empty-line ${className}`.trim()}>No hourly curve</div>;
  const values = points.map((point) => num(point.temp) || 0);
  return <WeatherCanvasSparkline values={values} className={`wm-weather-detail-line ${className}`.trim()} />;
}
