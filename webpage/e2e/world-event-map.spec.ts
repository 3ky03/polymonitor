import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page, type Route } from '@playwright/test';

const GENERATED_AT = '2026-08-26T03:00:00Z';
const ARTIFACT_DIR = resolve('artifacts/world-event-map-e2e');
const ALL_LAYERS = [
  'weather-alerts',
  'earthquakes-volcanoes',
  'wildfires',
  'extreme-temperature',
  'climate-anomalies',
  'air-routes',
].join(',');

type Json = Record<string, unknown>;

function source(provider: string, nativeId: string) {
  return [{ provider, nativeId, observedAt: GENERATED_AT, freshness: 'live', status: 'ok' }];
}

function hazard(overrides: Json): Json {
  const id = String(overrides.id);
  return {
    id,
    category: 'natural-hazard',
    title: id,
    summary: 'Deterministic World Event Map browser fixture.',
    severity: 'warning',
    occurredAt: GENERATED_AT,
    updatedAt: GENERATED_AT,
    geometry: { type: 'Point', coordinates: [0, 0] },
    locationPrecision: 'exact',
    locationLabel: 'Browser fixture',
    sources: source('Fixture authority', id),
    limitations: ['Deterministic browser fixture; not production data.'],
    relatedMarketIds: [],
    properties: { mapEntity: 'hazard-event', detailAvailable: true, geometryMode: 'simplified' },
    hazardKind: 'earthquake',
    lifecycle: 'active',
    coverage: { scope: 'provider-area', label: 'Deterministic browser coverage', isComplete: false, gaps: ['Fixture only.'] },
    severityEvidence: { provider: 'Fixture authority', rawLevel: 'fixture', mappingVersion: 'fixture.v1', reason: 'Deterministic browser contract.' },
    revision: { nativeEventId: id, revisionAt: GENERATED_AT, replaces: [], cancelled: false },
    metrics: { kind: 'earthquake', magnitude: 6.4, depthKm: 12 },
    ...overrides,
  };
}

const quake = hazard({
  id: 'earthquake:usgs:fixture',
  title: 'M6.4 Test Ridge Earthquake',
  severity: 'critical',
  geometry: { type: 'Point', coordinates: [-122.1, 37.4] },
  sources: source('USGS', 'fixture'),
});
const quakeCluster = Array.from({ length: 6 }, (_, index) => hazard({
  id: `earthquake:usgs:cluster-${index}`,
  title: `M5.${index} Cluster Ridge Earthquake`,
  severity: index >= 4 ? 'warning' : 'watch',
  geometry: { type: 'Point', coordinates: [-122.1, 37.4] },
  sources: source('USGS', `cluster-${index}`),
  metrics: { kind: 'earthquake', magnitude: 5 + index / 10, depthKm: 8 + index },
}));
const volcano = hazard({
  id: 'volcano:usgs:fixture',
  title: 'Fixture Volcano · WATCH / ORANGE',
  hazardKind: 'volcano',
  geometry: { type: 'Point', coordinates: [-155.3, 19.4] },
  sources: source('USGS Volcano Hazards Program', 'fixture-volcano'),
  metrics: { kind: 'volcano-or-other', statusLabel: 'WATCH / ORANGE' },
});
const cyclone = hazard({
  id: 'tropical-cyclone:nhc:al012026',
  title: 'HU ADA · NHC Advisory 12',
  hazardKind: 'tropical-cyclone',
  geometry: { type: 'Point', coordinates: [-70, 20] },
  sources: source('NOAA National Hurricane Center', 'AL012026'),
  properties: {
    mapEntity: 'hazard-event', detailAvailable: true, geometryMode: 'simplified',
    movementDirectionDegrees: 315, movementSpeedKnots: 12,
    geometries: {
      observedPosition: { type: 'Point', coordinates: [-70, 20] },
      observedTrack: { type: 'LineString', coordinates: [[-76, 16], [-73, 18], [-70, 20]] },
      forecastTrack: { type: 'LineString', coordinates: [[-70, 20], [-67, 23], [-64, 27]] },
      forecastCone: { type: 'Polygon', coordinates: [[[-72, 18], [-66, 19], [-62, 28], [-67, 29], [-72, 18]]] },
    },
  },
  revision: { nativeEventId: 'AL012026', advisoryId: '12', revisionAt: GENERATED_AT, replaces: [], cancelled: false },
  metrics: { kind: 'tropical-cyclone', maximumWind: { value: 100, unit: 'kt' }, pressureHpa: 960, advisoryNumber: '12', categoryLabel: 'HU' },
});
const wildfire = hazard({
  id: 'wildfire:eonet:fixture',
  title: 'Sierra Major Wildfire',
  hazardKind: 'wildfire',
  severity: 'warning',
  geometry: { type: 'Point', coordinates: [-118.2, 34.1] },
  sources: source('NASA EONET', 'fixture-fire'),
  metrics: { kind: 'wildfire', detectionCount: 84, fireRadiativePowerMw: 420, sensor: 'VIIRS', confidenceLabel: 'high' },
});
const detection = hazard({
  id: 'fire-detection:firms:fixture',
  title: 'VIIRS Detection · FRP 125 MW',
  hazardKind: 'fire-detection',
  severity: 'watch',
  geometry: { type: 'Point', coordinates: [-118.35, 34.18] },
  sources: source('NASA FIRMS', 'fixture-detection'),
  properties: { mapEntity: 'hazard-observation', detailAvailable: true, rawDetection: true, geometryMode: 'source-native' },
  coverage: { scope: 'viewport', label: 'Requested viewport', isComplete: false, gaps: ['Cloud and satellite overpass limitations apply.'] },
  metrics: { kind: 'wildfire', detectionCount: 1, fireRadiativePowerMw: 125, sensor: 'VIIRS', satellite: 'N20', confidenceLabel: 'high' },
});
const anomaly = hazard({
  id: 'temperature-anomaly:ncei:202607:42.5N,12.5E',
  title: 'Observed Temperature Anomaly +3.3 °C',
  hazardKind: 'temperature-anomaly',
  severity: 'critical',
  geometry: { type: 'Polygon', coordinates: [[[10, 40], [15, 40], [15, 45], [10, 45], [10, 40]]] },
  sources: source('NOAA NCEI Climate at a Glance', '202607:42.5N,12.5E'),
  metrics: {
    kind: 'climate-anomaly', variable: 'temperature', value: 3.3, anomaly: 3.3, unit: '°C',
    baselinePeriod: '1991-2020', calculationVersion: 'ncei-cag-global-mapping.v1',
    timeWindow: '202607', spatialResolution: '5-degree-grid', provider: 'NOAA NCEI',
  },
});

const sourceEvents: Record<string, Json[]> = {
  usgs: [quake, ...quakeCluster],
  'usgs-volcano-cap': [volcano],
  nhc: [cyclone],
  eonet: [wildfire],
  gdacs: [],
  nws: [],
  firms: [wildfire],
  'climate-anomaly': [anomaly],
};

const transportPayload = {
  generatedAt: GENERATED_AT,
  status: 'ok',
  source: 'OpenFlights fixture',
  freshness: 'live',
  items: [],
  aviation: {
    generatedAt: GENERATED_AT,
    routes: [{
      id: 'JFK-LHR', fromCode: 'JFK', toCode: 'LHR', fromLon: -73.78, fromLat: 40.64,
      toLon: -0.45, toLat: 51.47, corridor: 'North Atlantic trunk', trafficScore: 92,
      riskScore: 55, status: 'watch', layer: 'trunk', phase: 0.2, speed: 0.00002,
      riskSources: ['weather'], source: 'OpenFlights fixture',
    }],
    hubs: [
      { code: 'JFK', name: 'John F Kennedy', city: 'New York', country: 'US', lon: -73.78, lat: 40.64, routeCount: 92, status: 'watch' },
      { code: 'LHR', name: 'Heathrow', city: 'London', country: 'GB', lon: -0.45, lat: 51.47, routeCount: 88, status: 'ok' },
    ],
    flights: [{
      id: 'fixture-seeded', callsign: 'PX101', fromCode: 'JFK', toCode: 'LHR',
      fromLon: -73.78, fromLat: 40.64, toLon: -0.45, toLat: 51.47,
      phase: 0.37, speed: 0.00002, status: 'watch', layer: 'trunk', riskScore: 55,
    }],
  },
};

const minimalStyle = {
  version: 8,
  sources: { countries: { type: 'geojson', data: '/map-data/world-countries.geojson' } },
  layers: [
    { id: 'background', type: 'background', paint: { 'background-color': '#070a0c' } },
    { id: 'countries', type: 'fill', source: 'countries', paint: { 'fill-color': '#12181c', 'fill-opacity': 1 } },
    { id: 'country-lines', type: 'line', source: 'countries', paint: { 'line-color': '#526068', 'line-opacity': 0.7, 'line-width': 0.7 } },
  ],
};

function mapResponse(key: string, events: Json[], status = 'ok') {
  return {
    schemaVersion: 'natural-hazards-map.v1', generatedAt: GENERATED_AT, events,
    sources: [{
      key, status,
      coverage: { scope: 'provider-area', label: `${key} deterministic fixture coverage`, isComplete: false, gaps: ['Fixture only.'] },
      fetchedAt: GENERATED_AT, dataUpdatedAt: GENERATED_AT, staleAfter: null,
      lastSuccessAt: status === 'ok' ? GENERATED_AT : null,
      errorCode: status === 'ok' ? null : 'fixture-unavailable',
    }],
    isPartial: status !== 'ok', errors: status === 'ok' ? [] : [{ source: key, code: 'fixture-unavailable' }],
    counts: { events: events.length, byHazardKind: {} },
    meta: { source: key, geometryMode: 'simplified', geometryZoom: 3, detailEndpoint: '/runtime/world/natural-hazards/events/{eventId}' },
  };
}

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

async function installFixtures(page: Page, climateUnavailable = false) {
  await page.route('https://tiles.openfreemap.org/styles/**', (route) => fulfillJson(route, minimalStyle));
  await page.route('https://basemaps.cartocdn.com/gl/**', (route) => fulfillJson(route, minimalStyle));
  await page.route('**/wm-api/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/wm-api/, '');
    if (path === '/bootstrap') {
      await fulfillJson(route, {
        generatedAt: GENERATED_AT,
        defaultWorkspace: { name: 'World Event Map fixture', panels: ['global-transport-shipping'] },
        featuredMarket: null, activeMarketsPreview: [], activeMarketGroupsPreview: [],
        globalTradesPreview: [], globalOraclePreview: [], latestContentPreview: [], recentTradesPreview: [],
        oraclePreview: [], contentPreview: [], pricePreview: null, systemHealth: { apiStatus: 'ok', database: 'fixture' },
      });
      return;
    }
    if (path === '/runtime/world/natural-hazards/map') {
      const key = url.searchParams.get('source') || '';
      const zoom = Number(url.searchParams.get('zoom') || 2);
      if (key === 'climate-anomaly' && climateUnavailable) {
        await fulfillJson(route, mapResponse(key, [], 'error'));
        return;
      }
      const events = key === 'firms' && zoom >= 5 ? [detection] : sourceEvents[key] || [];
      await fulfillJson(route, mapResponse(key, events));
      return;
    }
    if (path.startsWith('/runtime/world/natural-hazards/events/')) {
      const id = decodeURIComponent(path.split('/').pop() || '');
      const event = Object.values(sourceEvents).flat().find((candidate) => candidate.id === id) || detection;
      await fulfillJson(route, { schemaVersion: 'natural-hazard-detail.v1', generatedAt: GENERATED_AT, event });
      return;
    }
    if (path === '/runtime/transport/global-shipping') {
      await fulfillJson(route, transportPayload);
      return;
    }
    if (path === '/runtime/transport/aviation-viewport') {
      await fulfillJson(route, {
        schemaVersion: 'aviation-viewport.v1', generatedAt: GENERATED_AT, status: 'ok',
        bbox: (url.searchParams.get('bbox') || '-90,30,-60,55').split(',').map(Number), zoom: Number(url.searchParams.get('zoom') || 3),
        aircraft: [{ id: 'abc123', icao24: 'abc123', callsign: 'PX202', lon: -70, lat: 43, baroAltitude: 10300, velocity: 240, heading: 72, status: 'watch', riskScore: 64, source: 'OpenSky fixture', updatedAt: GENERATED_AT }],
        aircraftCount: 1, availableAircraftCount: 1, source: 'OpenSky fixture', limitations: ['Deterministic browser fixture.'],
      });
      return;
    }
    if (path === '/markets' || path === '/market-groups') {
      await fulfillJson(route, { items: [], pagination: { page: 1, pageSize: 80, total: 0, totalPages: 0, hasMore: false } });
      return;
    }
    await fulfillJson(route, { generatedAt: GENERATED_AT, status: 'ok', items: [], panels: {} });
  });
}

async function gotoMap(page: Page, search = '') {
  await page.goto(`/?view=2d&mapPerf=1&time=all&severity=info,watch,warning,critical&${search}`);
  await expect(page.locator('[data-map-renderer-ready]')).toHaveAttribute('data-map-renderer-ready', /webgl|svg/);
  await expect(page.getByRole('button', { name: /ALL EVENTS/ })).toContainText(/[1-9]/);
  await waitForMapPaint(page);
}

async function waitForMapPaint(page: Page) {
  const renderer = await page.locator('[data-map-renderer-ready]').getAttribute('data-map-renderer-ready');
  if (renderer === 'webgl') {
    await expect.poll(async () => page.evaluate(() => (
      window.__POLYMONITOR_MAP_PERF__?.snapshot().phases['deck-commit'].count || 0
    ))).toBeGreaterThan(0);
  }
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  }));
  // Icon atlases and MapLibre glyphs decode after the first deck commit. The
  // visual baseline must capture the completed frame, not only accepted data.
  await page.waitForTimeout(450);
}

function screenshot(page: Page, name: string) {
  mkdirSync(ARTIFACT_DIR, { recursive: true });
  return page.screenshot({ path: resolve(ARTIFACT_DIR, name), fullPage: false });
}

async function mapCanvasCenter(page: Page) {
  const canvas = page.locator('.maplibregl-canvas').first();
  const box = await canvas.boundingBox();
  if (!box) throw new Error('MapLibre canvas has no rendered bounding box.');
  return { canvas, x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function projectedMapPoint(page: Page, lon: number, lat: number) {
  const host = page.locator('[data-map-renderer-ready="webgl"]');
  const box = await host.boundingBox();
  if (!box) throw new Error('WebGL map host has no rendered bounding box.');
  const relative = await host.evaluate((element, coordinates) => {
    const project = (element as HTMLElement & {
      __polymonitorProjectGeoPoint?: (projectLon: number, projectLat: number) => { x: number; y: number };
    }).__polymonitorProjectGeoPoint;
    if (!project) throw new Error('Map performance projection harness is unavailable.');
    return project(coordinates.lon, coordinates.lat);
  }, { lon, lat });
  return { x: box.x + relative.x, y: box.y + relative.y };
}

test.beforeEach(async ({ page }) => {
  await installFixtures(page);
});

test('WebGL map covers layered hazards, details, URL state, provider reload and aviation', async ({ page }) => {
  await gotoMap(page, `center=-25,27&zoom=2.2&layers=${ALL_LAYERS}`);
  await expect(page.locator('[data-map-renderer-ready]')).toHaveAttribute('data-map-renderer-ready', 'webgl');
  await expect(page.getByText('OBSERVED', { exact: true })).toBeVisible();
  await expect(page.getByText('FORECAST', { exact: true })).toBeVisible();
  await screenshot(page, '01-global-default.png');

  await page.getByRole('button', { name: /ALL EVENTS/ }).click();
  await expect(page.getByRole('region', { name: 'All mapped events' })).toBeVisible();
  await page.locator('#wm-event-list-search').fill('earthquake');
  await page.getByRole('button', { name: /M6.4 Test Ridge Earthquake/ }).click();
  await expect(page.locator('.wm-event-inspector[data-event-id="earthquake:usgs:fixture"]')).toBeVisible();
  await expect(page).toHaveURL(/event=earthquake%3Ausgs%3Afixture/);

  await page.getByLabel('Basemap theme').selectOption('positron');
  await page.getByLabel('Basemap provider').selectOption('carto');
  await expect(page).toHaveURL(/theme=positron/);
  await expect(page).toHaveURL(/basemap=carto/);
  await expect(page.locator('[data-map-renderer-ready]')).toHaveAttribute('data-map-renderer-ready', 'webgl');

  await page.goto(`/?view=2d&mapPerf=1&time=all&center=-98,39&zoom=3.5&layers=${ALL_LAYERS}&country=US&basemap=openfreemap&theme=dark`);
  await expect(page.getByRole('button', { name: /COUNTRY · US/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /ALL EVENTS/ })).toContainText(/[1-9]/);
  await waitForMapPaint(page);
  await screenshot(page, '07-country-filter.png');

  await page.goto(`/?view=2d&mapPerf=1&time=all&center=-73,42&zoom=3.2&layers=air-routes&air=all`);
  await expect(page.getByText('ALL AVIATION')).toBeVisible();
  await expect.poll(async () => page.evaluate(() => (
    window.__POLYMONITOR_MAP_PERF__?.snapshot().phases['dynamic-commit'].count || 0
  ))).toBeGreaterThan(0);
  await expect(page.locator('.wm-weather-deck-basemap canvas')).toHaveCount(2);
  await waitForMapPaint(page);
  await screenshot(page, '06-aviation-trunk-watch.png');
});

test('dense hazards, NHC geometry and FIRMS drill-down remain visually distinct', async ({ page }) => {
  await gotoMap(page, 'center=-118,35&zoom=4.6&layers=earthquakes-volcanoes,wildfires,weather-alerts');
  await screenshot(page, '02-high-density-hazards.png');

  await page.goto('/?view=2d&mapPerf=1&time=all&center=-69,22&zoom=4.4&layers=weather-alerts');
  await expect(page.getByText('OBSERVED', { exact: true })).toBeVisible();
  await expect(page.getByText('FORECAST', { exact: true })).toBeVisible();
  await waitForMapPaint(page);
  await screenshot(page, '03-hurricane-observed-forecast-cone.png');

  await page.goto('/?view=2d&mapPerf=1&time=all&center=-118.25,34.15&zoom=6&layers=wildfires');
  await expect(page.getByRole('button', { name: /ALL EVENTS/ })).toContainText('1');
  await waitForMapPaint(page);
  await screenshot(page, '04-firms-drill-down.png');

});

test('climate anomaly includes reproducible geometry and visual evidence', async ({ page }) => {
  await gotoMap(page, 'center=12.5,42.5&zoom=4.5&layers=climate-anomalies');
  await expect(page.getByRole('button', { name: /ALL EVENTS/ })).toContainText('1');
  await screenshot(page, '05-climate-anomaly.png');
});

test('WebGL event and cluster picking form complete interaction loops', async ({ page }) => {
  await gotoMap(page, 'center=-122.1,37.4&zoom=8&layers=earthquakes-volcanoes');
  let center = await mapCanvasCenter(page);
  await page.mouse.move(center.x, center.y);
  await expect(page.locator('.deck-tooltip:visible')).toContainText(/Cluster Ridge Earthquake|M6.4 Test Ridge Earthquake/);
  await page.mouse.click(center.x, center.y);
  await expect(page.locator('.wm-event-inspector')).toBeVisible();
  await expect(page.locator('.wm-event-inspector')).toContainText(/Disaster report/i);
  await page.getByRole('button', { name: 'Close event details' }).click();

  await page.goto('/?view=2d&mapPerf=1&time=all&center=-122.1,37.4&zoom=2.2&layers=earthquakes-volcanoes');
  await expect(page.getByRole('button', { name: /ALL EVENTS/ })).toContainText('8');
  await waitForMapPaint(page);
  const clusterPoint = await projectedMapPoint(page, -122.1, 37.4);
  await page.mouse.move(clusterPoint.x, clusterPoint.y);
  await expect(page.locator('.deck-tooltip:visible')).toContainText(/Cluster.*7 earthquake/i);
  const clusterUrl = page.url();
  await page.mouse.click(clusterPoint.x, clusterPoint.y);
  await expect.poll(() => page.url()).not.toBe(clusterUrl);
});

test('WebGL country hover, click, fit, context menu and filter remain connected', async ({ page }) => {
  await gotoMap(page, 'center=-98,39&zoom=3.5&layers=earthquakes-volcanoes,wildfires');
  let center = await mapCanvasCenter(page);
  await page.mouse.move(center.x, center.y);
  await expect(center.canvas).toHaveClass(/wm-map-hover-target/);
  await page.mouse.click(center.x, center.y);
  const countryDialog = page.getByRole('dialog', { name: /United States.* map actions/ });
  await expect(countryDialog).toBeVisible();
  const beforeFit = page.url();
  await countryDialog.getByRole('button', { name: 'Fit country' }).click();
  await expect.poll(() => page.url()).not.toBe(beforeFit);
  await waitForMapPaint(page);
  center = await mapCanvasCenter(page);
  await page.mouse.click(center.x, center.y, { button: 'right' });
  await expect(page.locator('.wm-country-context-card.is-context')).toBeVisible();
  await page.getByRole('button', { name: 'Filter events' }).click();
  await expect(page).toHaveURL(/country=US/);
});

test('live aircraft supports viewport loading, hover, click and inspector details', async ({ page }) => {
  await gotoMap(page, 'center=-70,43&zoom=5&layers=air-routes&air=all');
  await expect(page.getByText('ALL AVIATION')).toBeVisible();
  const center = await mapCanvasCenter(page);
  await page.mouse.move(center.x, center.y);
  await expect(page.locator('.deck-tooltip:visible')).toContainText('PX202');
  await page.mouse.click(center.x, center.y);
  await expect(page.locator('.wm-event-inspector')).toContainText('ICAO24');
});

test('SVG fallback and reduced-motion mobile preserve events, interaction entry points and cleanup', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 390, height: 844 });
  await gotoMap(page, 'center=-70,22&zoom=3&layers=weather-alerts,earthquakes-volcanoes,wildfires,extreme-temperature,climate-anomalies');
  await expect(page.locator('[data-map-renderer-ready]')).toHaveAttribute('data-map-renderer-ready', 'svg');
  await expect(page.locator('.wm-world-event-svg-map')).toBeVisible();
  await expect(page.locator('.wm-world-event-svg-cyclone-geometry.is-forecast')).toHaveCount(1);
  await expect(page.locator('.wm-world-event-svg-emphasis circle')).toHaveCount(0);
  const cycloneTarget = page.locator('.wm-world-event-svg-cyclone-geometry.is-observed[data-event-id="tropical-cyclone:nhc:al012026"]');
  await expect(cycloneTarget).toBeVisible();
  // Track, cone and point intentionally overlap and all own the same event
  // handlers. Dispatching at the selected track target makes this parity check
  // deterministic instead of depending on a one-pixel SVG stroke hit test.
  await cycloneTarget.dispatchEvent('pointerenter', { clientX: 210, clientY: 240 });
  await expect(page.locator('.wm-world-event-renderer-tooltip:not([hidden])')).toContainText('HU ADA');
  await cycloneTarget.dispatchEvent('click');
  await expect(page.locator('.wm-event-inspector[data-event-id="tropical-cyclone:nhc:al012026"]')).toContainText(/Disaster report/i);
  await page.getByRole('button', { name: 'Close event details' }).click();
  await page.getByRole('button', { name: /ALL EVENTS/ }).click();
  await expect(page.getByRole('region', { name: 'All mapped events' })).toBeVisible();
  await screenshot(page, '09-mobile.png');
});

test('SVG country context and filtering remain keyboard-accessible', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 390, height: 844 });
  await gotoMap(page, 'center=-98,39&zoom=3&layers=earthquakes-volcanoes,wildfires');
  await expect(page.locator('[data-map-renderer-ready]')).toHaveAttribute('data-map-renderer-ready', 'svg');
  const usCountry = page.locator('[aria-label^="United States"][aria-label$="map area"]');
  await expect(usCountry).toBeVisible();
  await usCountry.click({ button: 'right' });
  await expect(page.locator('.wm-country-context-card.is-context')).toBeVisible();
  await page.getByRole('button', { name: 'Filter events' }).click();
  await expect(page).toHaveURL(/country=US/);
});

test('WebGL context failure switches to SVG and destroys stale deck canvases', async ({ page }) => {
  await gotoMap(page, 'center=-70,22&zoom=3&layers=weather-alerts,earthquakes-volcanoes');
  await expect(page.locator('[data-map-renderer-ready]')).toHaveAttribute('data-map-renderer-ready', 'webgl');
  // The mapPerf-only harness invokes the production renderer-level failure
  // callback. It is deterministic across native GPUs and SwiftShader while
  // exercising the real WebGL destroy -> state handoff -> SVG mount path.
  await page.locator('[data-map-renderer-ready]').dispatchEvent('polymonitor:map-renderer-failure');
  await expect(page.locator('[data-map-renderer-ready]')).toHaveAttribute('data-map-renderer-ready', 'svg', { timeout: 8_000 });
  await expect(page.locator('.wm-world-event-svg-map')).toBeVisible();
  await expect(page.locator('.wm-weather-deck-basemap canvas')).toHaveCount(0);
  await expect(page.locator('.deck-tooltip')).toHaveCount(0);
  await screenshot(page, '08-svg-fallback.png');
});

test('required source failure makes the affected layer unavailable instead of a working empty toggle', async ({ page }) => {
  await page.unrouteAll({ behavior: 'wait' });
  await installFixtures(page, true);
  await gotoMap(page, 'layers=weather-alerts,earthquakes-volcanoes,wildfires,climate-anomalies');
  const openLayers = page.getByRole('button', { name: 'Open layers panel' });
  if (await openLayers.isVisible()) await openLayers.click();
  const anomalyRow = page.locator('.wm-layer-row').filter({ hasText: 'Major Weather Anomalies' });
  await expect(anomalyRow).toHaveClass(/is-unavailable/);
  await expect(anomalyRow.locator('input[type="checkbox"]')).toBeDisabled();
  await expect(anomalyRow.locator('input[type="checkbox"]')).not.toBeChecked();
  await expect(page.locator('.wm-sidebar-footer')).toHaveText('3/8 LAYERS ACTIVE');
});
