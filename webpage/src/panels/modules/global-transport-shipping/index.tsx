import { useMemo, useState } from 'preact/hooks';
import { Panel } from '@/components/Panel';
import { fetchRuntimeGlobalTransportShipping } from '@/services/api';
import type { RuntimeGlobalTransportShippingItem, RuntimeGlobalTransportShippingPayload } from '@/types';
import { formatCompact } from '../../shared/formatters';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { useSpecialistCopy } from '@/services/specialist-i18n';

type TransportTab = 'ops' | 'flights' | 'airlines' | 'track' | 'news';
type AviationIconKind = 'airport' | 'flight' | 'airline' | 'track' | 'news';
type AviationPayload = NonNullable<RuntimeGlobalTransportShippingPayload['aviation']>;
type AviationRoute = NonNullable<AviationPayload['routes']>[number];

function statusBadge(payload?: RuntimeGlobalTransportShippingPayload | null) {
  const mode = String(payload?.cacheMode || '').toLowerCase();
  const status = String(payload?.status || '').toLowerCase();
  if (mode.includes('stale') || mode.includes('preserved')) return 'STALE';
  if (mode.includes('seed')) return status === 'degraded' ? 'PARTIAL' : 'SEED';
  if (status === 'empty' || status === 'warming') return 'WARM';
  return status === 'degraded' ? 'PARTIAL' : 'LIVE';
}

function severityClass(item: RuntimeGlobalTransportShippingItem) {
  const severity = String(item.severity || '').toLowerCase();
  if (severity === 'alert') return 'alert';
  if (severity === 'watch') return 'watch';
  return 'normal';
}

function numeric(value?: number | string | null) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function clampPercent(value?: number | string | null) {
  return Math.max(0, Math.min(100, Math.round(numeric(value))));
}

function riskStyle(value?: number | string | null) {
  return { '--risk': `${clampPercent(value)}%` } as Record<string, string>;
}

function trendValues(value: unknown, fallbackSeed: string) {
  if (Array.isArray(value)) {
    const parsed = value.map((item) => numeric(item as string | number | null)).filter((item) => item > 0);
    if (parsed.length) return parsed.slice(0, 10);
  }
  const seed = fallbackSeed.split('').reduce((sum, char) => sum + char.charCodeAt(0), 0);
  return Array.from({ length: 8 }, (_, index) => 20 + ((seed + index * 13) % 58));
}

function Sparkline({ values }: { values: number[] }) {
  const max = Math.max(1, ...values);
  const points = values.map((value, index) => {
    const x = values.length <= 1 ? 0 : (index / (values.length - 1)) * 54;
    const y = 24 - (Math.max(0, value) / max) * 20;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return (
    <svg className="wm-aviation-spark" viewBox="0 0 56 26" focusable="false" aria-hidden="true">
      <polyline points={points} />
    </svg>
  );
}

function RiskMeter({ value, className = '' }: { value?: number | string | null; className?: string }) {
  return <span className={`wm-aviation-risk-meter ${className}`} style={riskStyle(value)} aria-hidden="true"><i /></span>;
}

function compactUnknown(value: unknown) {
  if (typeof value === 'number' || typeof value === 'string' || value === null || value === undefined) return formatCompact(value);
  return formatCompact(Number(value) || 0);
}

function glyph(item: RuntimeGlobalTransportShippingItem) {
  const type = String(item.evidenceType || item.topic || '').toUpperCase();
  if (type.includes('AIS')) return 'AIS';
  if (type.includes('TRANSIT') || type.includes('GTFS')) return 'GT';
  return 'AIR';
}

function openSource(url?: string | null) {
  const target = String(url || '').trim();
  if (target) window.open(target, '_blank', 'noopener,noreferrer');
}

function statusClass(status?: unknown) {
  const text = String(status || '').toLowerCase();
  if (text.includes('alert') || text.includes('error') || text.includes('auth')) return 'alert';
  if (text.includes('watch') || text.includes('minor') || text.includes('degraded') || text.includes('stale') || text.includes('missing')) return 'watch';
  return 'normal';
}

function statusLabel(status?: unknown) {
  const text = String(status || '').trim();
  if (!text) return 'NORMAL';
  return text.replace(/[_-]+/g, ' ').toUpperCase();
}

function riskSourcesLabel(value: unknown) {
  if (!Array.isArray(value) || !value.length) return 'BASELINE';
  return value.slice(0, 3).map((item) => String(item || '').toUpperCase()).join(' / ');
}

function summaryNumber(value: unknown) {
  const parsed = numeric(value as number | string | null);
  return Number.isFinite(parsed) ? parsed : 0;
}

function aviationRoutes(payload?: RuntimeGlobalTransportShippingPayload | null) {
  return payload?.aviation?.routes || [];
}

function aviationLiveFlights(payload?: RuntimeGlobalTransportShippingPayload | null) {
  return payload?.aviation?.liveFlights || [];
}

function aviationOps(payload?: RuntimeGlobalTransportShippingPayload | null) {
  return payload?.aviation?.ops || [];
}

function aviationAirlines(payload?: RuntimeGlobalTransportShippingPayload | null) {
  return payload?.aviation?.airlines || [];
}

function aviationNews(payload?: RuntimeGlobalTransportShippingPayload | null) {
  return payload?.aviation?.news || [];
}

function AviationIcon({ kind }: { kind: AviationIconKind }) {
  const paths: Record<AviationIconKind, string> = {
    airport: 'M21 16.2 13.9 14.4 10.4 21l-2-.6 1.1-6.2-5-1.8-2.2 2.1-1.3-.9 2.8-4.4 5.7.8 4.6-8.3c.5-.9 1.5-1.3 2.4-1 .9.4 1.3 1.4.9 2.4l-3.1 7 6.1.6.6 1.5Z',
    flight: 'M21 16.2 13.9 14.4 10.4 21l-2-.6 1.1-6.2-5-1.8-2.2 2.1-1.3-.9 2.8-4.4 5.7.8 4.6-8.3c.5-.9 1.5-1.3 2.4-1 .9.4 1.3 1.4.9 2.4l-3.1 7 6.1.6.6 1.5Z',
    airline: 'M4 15.8 20 7l.7 1.5-16 8.8L4 15.8Zm3.4 2.5 9.6-3.1.5 1.6-8.7 4.2-1.4-2.7Zm-1-7.4 9.1-4.4.7 1.5-8.8 5.1-1-2.2Z',
    track: 'M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18Zm0 2a7 7 0 1 0 0 14 7 7 0 0 0 0-14Zm0 2.6a4.4 4.4 0 0 1 4.4 4.4h-2a2.4 2.4 0 0 0-2.4-2.4v-2Zm0 4.4 5.6-5.6 1.4 1.4-6.2 6.2H8v-2h4Z',
    news: 'M5 4h14a1 1 0 0 1 1 1v14l-3-2H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Zm2 4v2h10V8H7Zm0 4v2h7v-2H7Z',
  };
  return (
    <span className={`wm-aviation-icon ${kind}`} aria-hidden="true">
      <svg viewBox="0 0 24 24" focusable="false">
        <path d={paths[kind]} />
      </svg>
    </span>
  );
}

function OpsRow({ row }: { row: Record<string, unknown> }) {
  const { shared } = useSpecialistCopy('global-transport-shipping');
  const status = statusClass(row.status);
  const risk = clampPercent((row.riskScore ?? row.delayScore) as number | string | null);
  return (
    <div className="wm-aviation-intel-row ops">
      <span className="wm-aviation-code"><AviationIcon kind="airport" /><b>{String(row.code || '--')}</b></span>
      <span><strong>{String(row.name || shared('airport', 'Airport'))}</strong><em>{String(row.city || row.country || shared('global', 'Global'))}</em></span>
      <Sparkline values={trendValues(row.trend, String(row.code || row.name || 'hub'))} />
      <i className={status}>{statusLabel(row.status)}</i>
      <small>{risk}</small>
    </div>
  );
}

function FlightRow({ route }: { route: AviationRoute }) {
  const { shared } = useSpecialistCopy('global-transport-shipping');
  const risk = numeric(route.riskScore);
  const status = statusClass(route.status);
  return (
    <div className="wm-aviation-intel-row flight">
      <span className="wm-aviation-code"><AviationIcon kind="flight" /><b>{route.fromCode || '--'}</b></span>
      <span>
        <strong>{route.fromCode} &gt; {route.toCode}</strong>
        <em>{riskSourcesLabel(route.riskSources)} · {route.corridor || route.airline || shared('airCorridor', 'air corridor')}</em>
      </span>
      <RiskMeter value={risk} className={status} />
      <i className={status}>{risk || statusLabel(route.status)}</i>
      <small>{String(route.layer || 'air').toUpperCase()}</small>
    </div>
  );
}

function AirlineRow({ row }: { row: { name?: string | null; routeCount?: number | string | null; status?: string | null; exposureScore?: number | string | null; trend?: Array<number | string | null> } }) {
  const { copy, shared } = useSpecialistCopy('global-transport-shipping');
  const exposure = row.exposureScore ?? row.routeCount;
  const status = statusClass(row.status);
  return (
    <div className="wm-aviation-intel-row airline">
      <span className="wm-aviation-code"><AviationIcon kind="airline" /><b>AL</b></span>
      <span><strong>{row.name || shared('airline', 'Airline')}</strong><em>{copy('routeCoverage', 'OpenFlights route coverage')}</em></span>
      <Sparkline values={trendValues(row.trend, row.name || 'airline')} />
      <i className={status}>{statusLabel(row.status)}</i>
      <small>{formatCompact(exposure)}</small>
    </div>
  );
}

function FlightSampleRow({ row }: { row: Record<string, unknown> }) {
  const status = statusClass(row.status);
  return (
    <div className="wm-aviation-intel-row flight">
      <span className="wm-aviation-code"><AviationIcon kind="flight" /><b>{String(row.callsign || row.fromCode || 'AIR')}</b></span>
      <span><strong>{String(row.fromCode || '--')} &gt; {String(row.toCode || '--')}</strong><em>{riskSourcesLabel(row.riskSources)} · {String(row.layer || 'route')}</em></span>
      <RiskMeter value={(row.riskScore ?? row.trafficScore) as number | string | null} className={status} />
      <i className={status}>{compactUnknown(row.riskScore)}</i>
      <small>{compactUnknown(row.routeCount)}</small>
    </div>
  );
}

function LiveAircraftRow({ row }: { row: NonNullable<AviationPayload['liveFlights']>[number] }) {
  const { shared } = useSpecialistCopy('global-transport-shipping');
  const status = statusClass(row.status);
  const speed = numeric(row.velocity);
  const altitude = numeric(row.baroAltitude);
  return (
    <div className="wm-aviation-intel-row flight live">
      <span className="wm-aviation-code"><AviationIcon kind="flight" /><b>{String(row.callsign || row.icao24 || 'OPEN')}</b></span>
      <span><strong>{row.regionLabel || row.region || 'OpenSky'}</strong><em>{row.originCountry || shared('unknown', 'Unknown')} · {row.onGround ? shared('ground', 'GROUND') : shared('airborne', 'AIRBORNE')}</em></span>
      <RiskMeter value={row.riskScore || speed / 3} className={status} />
      <i className={status}>{Math.round(speed)}M/S</i>
      <small>{altitude ? `${Math.round(altitude / 100) / 10}K` : '--'}</small>
    </div>
  );
}

function TrackRow({ item }: { item: RuntimeGlobalTransportShippingItem }) {
  const status = severityClass(item);
  return (
    <button type="button" className="wm-aviation-intel-row source" onClick={() => openSource(item.sourceUrl)}>
      <span className="wm-aviation-code"><AviationIcon kind="track" /><b>{glyph(item)}</b></span>
      <span><strong>{item.entity || item.evidenceType}</strong><em>{item.summary || item.title}</em></span>
      <RiskMeter value={Number(item.confidence || 0) * 100 || item.metric} className={status} />
      <i className={status}>{status.toUpperCase()}</i>
      <small>{formatCompact(item.metric)}</small>
    </button>
  );
}

function NewsRow({ row }: { row: Record<string, unknown> }) {
  const { copy } = useSpecialistCopy('global-transport-shipping');
  const status = statusClass(row.status);
  return (
    <div className="wm-aviation-intel-row news">
      <span className="wm-aviation-code"><AviationIcon kind="news" /><b>NW</b></span>
      <span><strong>{String(row.title || copy('aviationSignal', 'Aviation signal'))}</strong><em>{riskSourcesLabel(row.riskSources)} · {String(row.riskReason || row.corridor || row.source || copy('routeIntelligence', 'route intelligence'))}</em></span>
      <RiskMeter value={row.riskScore as number | string | null} className={status} />
      <i className={status}>{compactUnknown(row.riskScore)}</i>
      <small>{String(row.source || 'seed')}</small>
    </div>
  );
}

function AviationStatusDeck({ payload }: { payload?: RuntimeGlobalTransportShippingPayload | null }) {
  const { copy } = useSpecialistCopy('global-transport-shipping');
  const summary = payload?.summary || {};
  const liveFlights = aviationLiveFlights(payload);
  const liveCount = summaryNumber(summary.liveFlightSamples) || liveFlights.length;
  const routeCount = summaryNumber(summary.routes);
  const visibleRoutes = summaryNumber(summary.visibleRoutes);
  const topHub = String(summary.topHub || payload?.aviation?.hubs?.[0]?.code || '--');
  const source = String(summary.liveFlightSource || (liveFlights.length ? liveFlights[0]?.source : '') || 'ROUTE GRAPH');
  const health = statusClass(summary.adsbStatus || summary.openSkyStatus || payload?.status);
  const blips = liveFlights.length ? liveFlights.slice(0, 6) : (payload?.aviation?.hubs || []).slice(0, 6);
  return (
    <div className="wm-aviation-status-deck">
      <div className={`wm-aviation-radar-scope ${health}`}>
        <span className="wm-aviation-radar-sweep" />
        <svg viewBox="0 0 156 104" focusable="false" aria-hidden="true">
          <path className="grid" d="M14 80 C36 18 115 18 142 80M28 80 C46 38 105 38 128 80M48 80 C60 58 93 58 108 80M78 10V94M18 80H140" />
          <path className="route" d="M20 76 C52 34 86 38 136 22" />
          <path className="route ghost" d="M26 30 C58 58 100 62 138 48" />
          {blips.map((row, index) => {
            const record = row as Record<string, unknown>;
            const seed = String(record.callsign || record.code || record.icao24 || record.id || index);
            const hash = seed.split('').reduce((sum, char) => sum + char.charCodeAt(0), 0);
            const x = 26 + ((hash + index * 19) % 102);
            const y = 24 + ((hash + index * 13) % 56);
            return <circle key={`${seed}-${index}`} className={index < 2 ? 'hot' : ''} cx={x} cy={y} r={index < 2 ? 4.6 : 3.4} />;
          })}
        </svg>
        <div className="wm-aviation-radar-readout">
          <b>{formatCompact(liveCount)}</b>
          <span>{source}</span>
        </div>
      </div>
      <div className="wm-aviation-status-copy">
        <span>{copy('liveAirPicture', 'LIVE AIR PICTURE')}</span>
        <strong>{copy('terminalNet', '{hub} terminal net', { hub: topHub })}</strong>
        <em>{copy('corridorSummary', '{visible} visible corridors / {routes} OpenFlights edges', { visible: formatCompact(visibleRoutes), routes: formatCompact(routeCount) })}</em>
        <div className="wm-aviation-stat-row">
          <i><b>{formatCompact(liveCount)}</b><small>{copy('liveAircraft', 'live aircraft')}</small></i>
          <i><b>{statusLabel(summary.adsbStatus)}</b><small>{copy('adsbFallback', 'adsb fallback')}</small></i>
          <i><b>{statusLabel(summary.openSkyStatus)}</b><small>{copy('openSky', 'opensky')}</small></i>
        </div>
      </div>
    </div>
  );
}

function SourceHealthStrip({ payload }: { payload?: RuntimeGlobalTransportShippingPayload | null }) {
  const { copy } = useSpecialistCopy('global-transport-shipping');
  const health = payload?.sourceHealth || {};
  const rows: Array<[string, unknown]> = [
    ['OpenFlights', health.openflights || payload?.sources?.openflights],
    ['OpenSky', health.opensky || payload?.summary?.openSkyStatus],
    ['ADSB', health.adsb || payload?.summary?.adsbStatus],
    ['Transitland', health.transitland || payload?.sources?.transitland],
    ['AIS', health.aisstream || payload?.summary?.aisStatus],
  ];
  return (
    <div className="wm-aviation-source-strip" aria-label={copy('sourceHealthAria', 'Aviation evidence source health')}>
      {rows.map(([label, value]) => {
        const text = typeof value === 'string' ? value : (value ? 'ok' : 'warming');
        return <span key={label} className={statusClass(text)}><b>{label}</b><em>{String(text).toUpperCase()}</em></span>;
      })}
    </div>
  );
}

function GlobalTransportShippingPanel({ payload }: { payload?: RuntimeGlobalTransportShippingPayload | null }) {
  const { copy, formatRelativeTime } = useSpecialistCopy('global-transport-shipping');
  const [showHelp, setShowHelp] = useState(false);
  const [tab, setTab] = useState<TransportTab>('ops');
  const items = payload?.items || [];
  const tabs: Array<{ id: TransportTab; label: string; count: number; icon: AviationIconKind }> = [
    { id: 'ops', label: copy('tabOps', 'Ops'), count: aviationOps(payload).length, icon: 'airport' },
    { id: 'flights', label: copy('tabFlights', 'Flights'), count: aviationLiveFlights(payload).length || aviationRoutes(payload).length, icon: 'flight' },
    { id: 'airlines', label: copy('tabAirlines', 'Airlines'), count: aviationAirlines(payload).length, icon: 'airline' },
    { id: 'track', label: copy('tabTrack', 'Track'), count: items.length, icon: 'track' },
    { id: 'news', label: copy('tabNews', 'News'), count: aviationNews(payload).length, icon: 'news' },
  ];
  const rows = useMemo(() => {
    if (tab === 'ops') return aviationOps(payload).slice(0, 8).map((row, index) => <OpsRow key={`${row.code || index}`} row={row} />);
    if (tab === 'flights') {
      const liveFlights = aviationLiveFlights(payload);
      if (liveFlights.length) return liveFlights.slice(0, 8).map((row, index) => <LiveAircraftRow key={`${row.id || row.icao24 || index}`} row={row} />);
      const flights = payload?.aviation?.flights || [];
      if (flights.length) return flights.slice(0, 8).map((row, index) => <FlightSampleRow key={`${row.id || index}`} row={row} />);
      return aviationRoutes(payload).slice(0, 8).map((route, index) => <FlightRow key={`${route.id || index}`} route={route} />);
    }
    if (tab === 'airlines') return aviationAirlines(payload).slice(0, 8).map((row, index) => <AirlineRow key={`${row.name || index}`} row={row} />);
    if (tab === 'news') return aviationNews(payload).slice(0, 8).map((row, index) => <NewsRow key={`${row.title || index}`} row={row} />);
    return items.slice(0, 8).map((item) => <TrackRow key={item.id || `${item.evidenceType}-${item.entity}-${item.title}`} item={item} />);
  }, [items, payload, tab]);
  return (
    <Panel
      title={copy('title', 'AIRLINE INTELLIGENCE')}
      titleControls={<button type="button" className="wm-panel-help-button" aria-label={copy('explainAria', 'Explain transport ops')} aria-expanded={showHelp} onClick={() => setShowHelp((value) => !value)}>?</button>}
      controls={<button type="button" className="wm-evidence-sort-button wm-aviation-refresh-button" aria-label={copy('openSourceAria', 'Open aviation source')} onClick={() => openSource(payload?.sourceUrl)}>↻</button>}
      badge={statusBadge(payload)}
      status={payload?.status === 'ok' ? 'live' : 'muted'}
      count={items.length}
      headerOverlay={showHelp ? (
        <div className="wm-panel-help-popover">
          <strong>{copy('helpTitle', 'Airline Intelligence')}</strong>
          <p>{copy('helpText', 'Air evidence radar: OpenFlights route graph, Transitland feed coverage, AIS sample state, and runtime weather/conflict route joins.')}</p>
        </div>
      ) : null}
      className="wm-market-panel wm-evidence-panel wm-global-transport-panel"
      dataPanelId="global-transport-shipping"
    >
      <AviationStatusDeck payload={payload} />
      <SourceHealthStrip payload={payload} />
      <div className="wm-aviation-tabs" role="tablist" aria-label={copy('viewsAria', 'Airline intel views')}>
        {tabs.map((item) => (
          <button key={item.id} type="button" className={tab === item.id ? 'active' : ''} onClick={() => setTab(item.id)}>
            <AviationIcon kind={item.icon} /><strong>{item.label}</strong><span>{item.count}</span>
          </button>
        ))}
      </div>
      <div className="wm-aviation-intel-list">
        {rows.length ? rows : (
          <div className="wm-registry-empty"><strong>{copy('warming', 'Transport evidence seed warming')}</strong><span>{formatRelativeTime(payload?.generatedAt)}</span></div>
        )}
      </div>
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'global-transport-shipping': {
    render: (ctx) => <GlobalTransportShippingPanel payload={ctx.runtimeData['global-transport-shipping'] as RuntimeGlobalTransportShippingPayload | undefined} />,
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'global-transport-shipping',
  title: 'Global Transport / Shipping',
  eyebrow: 'transport',
  description: 'OpenFlights, Transitland Atlas, and AISStream transport evidence linked to markets.',
  size: 'wide',
  defaultEnabled: true,
}, {
  tier: 'slow',
  intervalMs: 900000,
  fetchData: () => fetchRuntimeGlobalTransportShipping(14),
});
