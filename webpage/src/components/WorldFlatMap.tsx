import { useMemo } from 'preact/hooks';
import { geoNaturalEarth1, geoPath, type GeoProjection } from 'd3-geo';
import { feature } from 'topojson-client';
import countriesAtlas from 'world-atlas/countries-110m.json';
import type { ContentItem, MarketListItem, MarketSummary, OracleEvent, TradeRow } from '@/types';

type WorldFlatMapProps = {
  markets: MarketListItem[];
  selectedMarket: MarketSummary | null;
  recentTrades: TradeRow[];
  recentOracle: OracleEvent[];
  contentItems: ContentItem[];
  region: string;
  zoomLevel: number;
  onOpenWeatherMap?: () => void;
};

type FlatPoint = {
  x: number;
  y: number;
  r: number;
  ring: number;
  fill: string;
  stroke: string;
};

type RegionWindow = {
  lat: number;
  lng: number;
  scale: number;
};

const VIEWBOX_WIDTH = 1280;
const VIEWBOX_HEIGHT = 680;

const REGION_WINDOWS: Record<string, RegionWindow> = {
  global: { lat: 18, lng: 12, scale: 1 },
  america: { lat: 22, lng: -88, scale: 1.55 },
  mena: { lat: 28, lng: 38, scale: 1.95 },
  eu: { lat: 50, lng: 10, scale: 2.2 },
  asia: { lat: 28, lng: 98, scale: 1.8 },
  latam: { lat: -15, lng: -70, scale: 1.72 },
  africa: { lat: 8, lng: 20, scale: 1.85 },
  oceania: { lat: -24, lng: 136, scale: 2.1 },
};

const GEO_HINTS: Array<{ pattern: RegExp; lat: number; lng: number }> = [
  { pattern: /\b(israel|netanyahu|gaza|jerusalem|tel aviv)\b/i, lat: 31.7683, lng: 35.2137 },
  { pattern: /\b(iran|tehran|hormuz)\b/i, lat: 35.6892, lng: 51.389 },
  { pattern: /\b(ukraine|kyiv|kiev)\b/i, lat: 50.4501, lng: 30.5234 },
  { pattern: /\b(russia|moscow)\b/i, lat: 55.7558, lng: 37.6173 },
  { pattern: /\b(china|beijing)\b/i, lat: 39.9042, lng: 116.4074 },
  { pattern: /\b(taiwan|taipei)\b/i, lat: 25.033, lng: 121.5654 },
  { pattern: /\b(india|delhi)\b/i, lat: 28.6139, lng: 77.209 },
  { pattern: /\b(europe|eu|brussels)\b/i, lat: 50.8503, lng: 4.3517 },
  { pattern: /\b(uk|britain|london)\b/i, lat: 51.5072, lng: -0.1276 },
  { pattern: /\b(france|paris)\b/i, lat: 48.8566, lng: 2.3522 },
  { pattern: /\b(germany|berlin)\b/i, lat: 52.52, lng: 13.405 },
  { pattern: /\b(us|u\\.s\\.|america|trump|kamala|president|washington)\b/i, lat: 38.9072, lng: -77.0369 },
  { pattern: /\b(california|silicon valley|san francisco)\b/i, lat: 37.7749, lng: -122.4194 },
  { pattern: /\b(new york|wall street)\b/i, lat: 40.7128, lng: -74.006 },
  { pattern: /\b(japan|tokyo)\b/i, lat: 35.6762, lng: 139.6503 },
  { pattern: /\b(korea|seoul)\b/i, lat: 37.5665, lng: 126.978 },
  { pattern: /\b(australia|sydney)\b/i, lat: -33.8688, lng: 151.2093 },
  { pattern: /\b(africa|sudan|cairo|egypt)\b/i, lat: 30.0444, lng: 31.2357 },
];

function hashString(value: string) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = ((hash << 5) - hash + value.charCodeAt(index)) | 0;
  }
  return Math.abs(hash);
}

function resolveGeo(text: string, index = 0) {
  for (const hint of GEO_HINTS) {
    if (hint.pattern.test(text)) {
      return { lat: hint.lat, lng: hint.lng };
    }
  }
  const hash = hashString(`${text}:${index}`);
  return {
    lat: ((hash % 1200) / 10) - 60,
    lng: (((Math.floor(hash / 1200) % 3600) / 10) - 180),
  };
}

function applyJitter(x: number, y: number, seed: number, magnitude = 18) {
  const angle = ((seed * 53) % 360) * (Math.PI / 180);
  const radius = ((seed % 7) / 6) * magnitude;
  return {
    x: x + Math.cos(angle) * radius,
    y: y + Math.sin(angle) * radius,
  };
}

function clampZoom(view: RegionWindow, zoomLevel: number) {
  return Math.max(1, Math.min(3.6, view.scale + ((zoomLevel - 1) * 0.12)));
}

function buildProjection(region: string, zoomLevel: number) {
  const projection = geoNaturalEarth1();
  const world = feature(countriesAtlas as any, (countriesAtlas as any).objects.countries) as any;
  projection.fitExtent([[36, 24], [VIEWBOX_WIDTH - 36, VIEWBOX_HEIGHT - 26]], world);

  const focus = REGION_WINDOWS[region] ?? REGION_WINDOWS.global ?? { lat: 18, lng: 12, scale: 1 };
  const zoom = clampZoom(focus, zoomLevel);
  const center = projection([focus.lng, focus.lat]) || [VIEWBOX_WIDTH / 2, VIEWBOX_HEIGHT / 2];
  const translateX = (VIEWBOX_WIDTH / 2) - (center[0] * zoom);
  const translateY = (VIEWBOX_HEIGHT / 2) - (center[1] * zoom);

  return {
    projection,
    transform: `translate(${translateX.toFixed(2)} ${translateY.toFixed(2)}) scale(${zoom.toFixed(3)})`,
    world,
  };
}

function projectPoint(projection: GeoProjection, lat: number, lng: number, seed: number, magnitude = 14) {
  const projected = projection([lng, lat]);
  if (!projected) return null;
  return applyJitter(projected[0], projected[1], seed, magnitude);
}

export function WorldFlatMap({ markets, selectedMarket, recentTrades, recentOracle, contentItems, region, zoomLevel, onOpenWeatherMap }: WorldFlatMapProps) {
  const { projection, transform, world } = useMemo(
    () => buildProjection(region, zoomLevel),
    [region, zoomLevel],
  );

  const path = useMemo(() => geoPath(projection), [projection]);

  const points = useMemo<FlatPoint[]>(() => {
    const all: FlatPoint[] = [];

    markets.slice(0, 80).forEach((market, index) => {
      const geo = resolveGeo(`${market.title} ${market.category || ''} ${(market.tags || []).join(' ')}`, index + 1);
      const projected = projectPoint(projection, geo.lat, geo.lng, index + 1, market.id === selectedMarket?.id ? 8 : 16);
      if (!projected) return;
      all.push({
        x: projected.x,
        y: projected.y,
        r: market.id === selectedMarket?.id ? 7 : 3.3,
        ring: market.id === selectedMarket?.id ? 18 : 7,
        fill: market.id === selectedMarket?.id ? '#ffd14f' : '#66a8ff',
        stroke: market.id === selectedMarket?.id ? 'rgba(255, 209, 79, 0.38)' : 'rgba(102, 168, 255, 0.28)',
      });
    });

    recentTrades.slice(0, 24).forEach((trade, index) => {
      const geo = resolveGeo(`${trade.marketTitle || trade.marketId || ''} ${trade.side || ''}`, index + 120);
      const projected = projectPoint(projection, geo.lat, geo.lng, index + 120, 12);
      if (!projected) return;
      all.push({
        x: projected.x,
        y: projected.y,
        r: 2.8,
        ring: 5,
        fill: String(trade.side).toLowerCase() === 'buy' ? '#ff9c3a' : '#ffd166',
        stroke: 'rgba(255, 192, 66, 0.2)',
      });
    });

    recentOracle.slice(0, 18).forEach((event, index) => {
      const geo = resolveGeo(`${event.marketTitle || ''} ${event.questionId || ''}`, index + 240);
      const projected = projectPoint(projection, geo.lat, geo.lng, index + 240, 10);
      if (!projected) return;
      all.push({
        x: projected.x,
        y: projected.y,
        r: 3.6,
        ring: 7,
        fill: '#ff5c5c',
        stroke: 'rgba(255, 92, 92, 0.26)',
      });
    });

    contentItems.slice(0, 12).forEach((item, index) => {
      const geo = resolveGeo(`${item.title || ''} ${item.source || ''}`, index + 360);
      const projected = projectPoint(projection, geo.lat, geo.lng, index + 360, 9);
      if (!projected) return;
      all.push({
        x: projected.x,
        y: projected.y,
        r: 2.5,
        ring: 5,
        fill: '#39ff73',
        stroke: 'rgba(57, 255, 115, 0.24)',
      });
    });

    return all;
  }, [contentItems, markets, projection, recentOracle, recentTrades, selectedMarket?.id]);

  return (
    <div
      className={`wm-flatmap ${onOpenWeatherMap ? 'clickable' : ''}`}
      role={onOpenWeatherMap ? 'button' : undefined}
      tabIndex={onOpenWeatherMap ? 0 : undefined}
      onClick={onOpenWeatherMap}
      onKeyDown={(event) => {
        if (!onOpenWeatherMap) return;
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onOpenWeatherMap();
        }
      }}
      aria-label={onOpenWeatherMap ? 'Open global weather map' : undefined}
    >
      <svg className="wm-flatmap-svg" viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
        <defs>
          <linearGradient id="wmFlatSea" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#06142f" />
            <stop offset="100%" stopColor="#020612" />
          </linearGradient>
        </defs>
        <rect x="0" y="0" width={VIEWBOX_WIDTH} height={VIEWBOX_HEIGHT} fill="url(#wmFlatSea)" />
        <g transform={transform}>
          <path className="wm-flatmap-landmass" d={path(world) || ''} />
          {points.map((point, index) => (
            <g key={`flat-point-${index}`}>
              <circle className="wm-flatmap-ring" cx={point.x} cy={point.y} r={point.ring} />
              <circle cx={point.x} cy={point.y} r={point.r} fill={point.fill} stroke={point.stroke} strokeWidth="1.4" />
            </g>
          ))}
        </g>
      </svg>
      {onOpenWeatherMap ? <span className="wm-flatmap-open-hint">Weather Map</span> : null}
    </div>
  );
}
