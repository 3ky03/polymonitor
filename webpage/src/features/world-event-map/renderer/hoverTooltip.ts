import type { GeoEvent } from '../domain/types';
import type { EventCluster } from './layerFactories';
import { isHazardEvent } from './layerFactories/shared';

export type WorldEventPickedObject =
  | GeoEvent
  | EventCluster
  | { event?: GeoEvent; properties?: { event?: GeoEvent } };

export type WorldEventTooltipModel = {
  kicker: string;
  title: string;
  details: string[];
};

export function pickedWorldEvent(object?: WorldEventPickedObject | null): GeoEvent | null {
  if (!object) return null;
  if ('kind' in object && object.kind === 'event-cluster') return null;
  const wrappedEvent = (object as { event?: GeoEvent }).event;
  if (wrappedEvent?.id) return wrappedEvent;
  const featureEvent = (object as { properties?: { event?: GeoEvent } }).properties?.event;
  if (featureEvent?.id) return featureEvent;
  return 'id' in object && 'category' in object ? object as GeoEvent : null;
}

export function pickedWorldEventCluster(object?: WorldEventPickedObject | null): EventCluster | null {
  return object && 'kind' in object && object.kind === 'event-cluster' ? object : null;
}

function escapeHtml(value: unknown) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function humanize(value: string) {
  return value.replace(/-/g, ' ').replace(/\b\w/g, (letter: string) => letter.toUpperCase());
}

function textProperty(event: GeoEvent, key: string) {
  return String(event.properties[key] ?? '').trim();
}

function numberProperty(event: GeoEvent, key: string): number | null {
  const value = Number(event.properties[key]);
  return Number.isFinite(value) ? value : null;
}

function compact(values: Array<string | null | undefined | false>) {
  return values.filter((value): value is string => Boolean(value));
}

function sourceProvider(event: GeoEvent) {
  return event.sources.find((source) => source.provider)?.provider || '';
}

function baseDetails(event: GeoEvent) {
  return compact([
    event.locationLabel,
    sourceProvider(event),
  ]);
}

function hazardDetails(event: GeoEvent) {
  if (!isHazardEvent(event)) return baseDetails(event);
  const metrics = event.metrics;
  if (metrics.kind === 'earthquake') {
    return compact([
      `Magnitude ${metrics.magnitude.toFixed(1)}${metrics.depthKm == null ? '' : ` · Depth ${metrics.depthKm.toFixed(1)} km`}`,
      metrics.pagerAlert ? `PAGER ${metrics.pagerAlert.toUpperCase()}` : null,
      ...baseDetails(event),
    ]);
  }
  if (metrics.kind === 'tropical-cyclone') {
    return compact([
      compact([
        metrics.categoryLabel,
        metrics.maximumWind ? `Wind ${metrics.maximumWind.value} ${metrics.maximumWind.unit}` : null,
        metrics.pressureHpa == null ? null : `${metrics.pressureHpa} hPa`,
      ]).join(' · '),
      ...baseDetails(event),
    ]);
  }
  if (metrics.kind === 'weather-alert') {
    return compact([
      compact([metrics.providerSeverity, metrics.urgency, metrics.certainty]).join(' · '),
      ...baseDetails(event),
    ]);
  }
  if (metrics.kind === 'wildfire') {
    return compact([
      compact([
        metrics.detectionCount == null ? null : `${metrics.detectionCount} detections`,
        metrics.fireRadiativePowerMw == null ? null : `${metrics.fireRadiativePowerMw} MW FRP`,
        metrics.confidenceLabel,
      ]).join(' · '),
      ...baseDetails(event),
    ]);
  }
  if (metrics.kind === 'climate-anomaly') {
    return compact([
      `${metrics.variable} ${metrics.value} ${metrics.unit} · anomaly ${metrics.anomaly >= 0 ? '+' : ''}${metrics.anomaly} ${metrics.unit}`,
      `Baseline ${metrics.baselinePeriod}`,
      ...baseDetails(event),
    ]);
  }
  return compact([metrics.statusLabel, ...baseDetails(event)]);
}

function aviationRouteModel(event: GeoEvent, seeded = false): WorldEventTooltipModel {
  const from = textProperty(event, 'fromCode');
  const to = textProperty(event, 'toCode');
  const route = from && to ? `${from} → ${to}` : event.title;
  const callsign = textProperty(event, 'callsign');
  const riskSources = Array.isArray(event.properties.riskSources)
    ? event.properties.riskSources.map(String).filter(Boolean).join(', ')
    : '';
  const trafficScore = numberProperty(event, 'trafficScore');
  const riskScore = numberProperty(event, 'riskScore');
  return {
    kicker: seeded ? 'Animated Reference Aircraft' : 'Air Route',
    title: callsign || route,
    details: compact([
      seeded && callsign && callsign !== route ? route : null,
      compact([
        textProperty(event, 'layer') && `${humanize(textProperty(event, 'layer'))} corridor`,
        trafficScore == null ? null : `Traffic ${trafficScore}`,
        riskScore == null ? null : `Risk ${riskScore}`,
      ]).join(' · '),
      riskSources && `Exposure: ${riskSources}`,
      textProperty(event, 'airline') || sourceProvider(event),
    ]),
  };
}

function aviationHubModel(event: GeoEvent): WorldEventTooltipModel {
  const routeCount = numberProperty(event, 'routeCount');
  const riskScore = numberProperty(event, 'riskScore');
  return {
    kicker: 'Air Hub',
    title: textProperty(event, 'code') || event.title,
    details: compact([
      compact([textProperty(event, 'city'), textProperty(event, 'country')]).join(' · '),
      compact([
        routeCount == null ? null : `${routeCount} connected routes`,
        riskScore == null ? null : `Risk ${riskScore}`,
        textProperty(event, 'status'),
      ]).join(' · '),
    ]),
  };
}

function liveAircraftModel(event: GeoEvent): WorldEventTooltipModel {
  const altitude = numberProperty(event, 'baroAltitude');
  const velocity = numberProperty(event, 'velocity');
  const heading = numberProperty(event, 'heading');
  return {
    kicker: 'Live Aircraft',
    title: textProperty(event, 'callsign') || textProperty(event, 'icao24') || event.title,
    details: compact([
      compact([
        textProperty(event, 'icao24') && `ICAO24 ${textProperty(event, 'icao24')}`,
        textProperty(event, 'originCountry'),
      ]).join(' · '),
      compact([
        altitude == null ? null : `Altitude ${Math.round(altitude).toLocaleString('en-US')} m / ${Math.round(altitude * 3.28084).toLocaleString('en-US')} ft`,
        velocity == null ? null : `${Math.round(velocity * 1.94384)} kt`,
        heading == null ? null : `${Math.round(heading)}°`,
      ]).join(' · '),
    ]),
  };
}

function countryRiskModel(event: GeoEvent): WorldEventTooltipModel {
  const evidence = numberProperty(event, 'evidenceCount');
  const sanctions = numberProperty(event, 'sanctionsEvidenceCount');
  const countryRisk = numberProperty(event, 'countryRiskEvidenceCount');
  return {
    kicker: `${event.severity} Country Risk`,
    title: event.title,
    details: compact([
      compact([
        evidence == null ? null : `${evidence} evidence records`,
        sanctions == null ? null : `${sanctions} sanctions`,
        countryRisk == null ? null : `${countryRisk} risk signals`,
      ]).join(' · '),
      textProperty(event, 'latestSource') || sourceProvider(event),
    ]),
  };
}

function inferredLayerId(event: GeoEvent) {
  const entity = textProperty(event, 'mapEntity');
  if (entity === 'air-route') return 'aviation-route-core';
  if (entity === 'air-flight') return 'aviation-seeded-aircraft';
  if (entity === 'air-hub') return 'aviation-hubs';
  if (entity === 'live-aircraft') return 'aviation-live-aircraft';
  if (entity === 'country-risk-area') return 'world-event-country-risk';
  return isHazardEvent(event) ? 'world-event-points' : '';
}

/**
 * WorldMonitor-style layer dispatch: hover stays lightweight while click opens
 * the full evidence inspector. Only real adapter fields are displayed.
 */
export function worldEventTooltipModel(
  object?: WorldEventPickedObject | null,
  rawLayerId = '',
): WorldEventTooltipModel | null {
  const cluster = pickedWorldEventCluster(object);
  if (cluster) {
    return {
      kicker: `${cluster.severity} Cluster`,
      title: `${cluster.count.toLocaleString('en-US')} ${cluster.label || 'mapped events'}`,
      details: ['Click to expand this area'],
    };
  }

  const event = pickedWorldEvent(object);
  if (!event) return null;
  const layerId = rawLayerId.endsWith('-ghost') ? rawLayerId.slice(0, -6) : rawLayerId || inferredLayerId(event);
  switch (layerId) {
    case 'aviation-route-core':
      return aviationRouteModel(event);
    case 'aviation-seeded-aircraft':
      return aviationRouteModel(event, true);
    case 'aviation-hubs':
      return aviationHubModel(event);
    case 'aviation-live-aircraft':
      return liveAircraftModel(event);
    case 'world-event-country-risk':
      return countryRiskModel(event);
    case 'world-event-hazard-areas':
    case 'world-event-points':
      if (isHazardEvent(event)) {
        return { kicker: humanize(event.hazardKind), title: event.title, details: hazardDetails(event) };
      }
      break;
    default:
      break;
  }
  const entity = textProperty(event, 'mapEntity');
  return {
    kicker: `${event.severity} ${humanize(entity || event.category)}`,
    title: event.title,
    details: baseDetails(event),
  };
}

export function worldEventTooltipHtml(
  object?: WorldEventPickedObject | null,
  layerId = '',
): string | null {
  const model = worldEventTooltipModel(object, layerId);
  if (!model) return null;
  return [
    '<div class="wm-world-event-tooltip">',
    `<span class="wm-world-event-tooltip-kicker">${escapeHtml(model.kicker)}</span>`,
    `<strong>${escapeHtml(model.title)}</strong>`,
    ...model.details.map((detail) => `<small>${escapeHtml(detail)}</small>`),
    '</div>',
  ].join('');
}
