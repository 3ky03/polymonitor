import { describe, expect, it, vi } from 'vitest';
import { DeckMapRenderer } from './DeckMapRenderer';
import { SvgMapRenderer } from './SvgMapRenderer';
import type { MapRendererCallbacks } from './MapRenderer';
import { defaultWorldEventMapState } from '../state/mapState';
import type { GeoEvent } from '../domain/types';

function callbacks(): MapRendererCallbacks {
  return {
    onCameraChange: vi.fn(),
    onEventSelect: vi.fn(),
    onCountrySelect: vi.fn(),
    onCountryContextMenu: vi.fn(),
    onBasemapStateChange: vi.fn(),
    onRendererFallbackRequested: vi.fn(),
    onError: vi.fn(),
  };
}

describe('renderer hover lifecycle', () => {
  it('does not declare the local fallback ready before country geometry loads', () => {
    const onBasemapStateChange = vi.fn();
    const renderer = new DeckMapRenderer() as unknown as {
      callbacks: MapRendererCallbacks;
      fallbackApplied: boolean;
      map: { getSource: () => object; isSourceLoaded: () => boolean };
      markLocalFallbackReadyIfLoaded: () => boolean;
      destroy: () => void;
    };
    renderer.callbacks = { ...callbacks(), onBasemapStateChange };
    renderer.fallbackApplied = true;
    renderer.map = {
      getSource: () => ({}),
      isSourceLoaded: () => false,
    };

    expect(renderer.markLocalFallbackReadyIfLoaded()).toBe(false);
    expect(onBasemapStateChange).not.toHaveBeenCalledWith('local-fallback-ready');
    renderer.map = null as never;
    renderer.destroy();
  });

  it('declares the local fallback ready after country geometry loads', () => {
    const onBasemapStateChange = vi.fn();
    const renderer = new DeckMapRenderer() as unknown as {
      callbacks: MapRendererCallbacks;
      fallbackApplied: boolean;
      map: { getSource: () => object; isSourceLoaded: () => boolean };
      markLocalFallbackReadyIfLoaded: () => boolean;
      destroy: () => void;
    };
    renderer.callbacks = { ...callbacks(), onBasemapStateChange };
    renderer.fallbackApplied = true;
    renderer.map = {
      getSource: () => ({}),
      isSourceLoaded: () => true,
    };

    expect(renderer.markLocalFallbackReadyIfLoaded()).toBe(true);
    expect(onBasemapStateChange).toHaveBeenCalledWith('local-fallback-ready');
    renderer.map = null as never;
    renderer.destroy();
  });

  it('keeps a loaded primary basemap when an individual tile fails later', () => {
    const onError = vi.fn();
    const renderer = new DeckMapRenderer() as unknown as {
      callbacks: MapRendererCallbacks;
      fallbackApplied: boolean;
      handleMapError: (event: { error?: Error; message?: string }) => void;
      destroy: () => void;
    };
    renderer.callbacks = { ...callbacks(), onError };
    renderer.handleMapError({ message: 'Tile network request failed' });
    renderer.handleMapError({ message: 'Glyph fetch network request failed' });

    expect(renderer.fallbackApplied).toBe(false);
    expect(onError).toHaveBeenCalledTimes(2);
    renderer.destroy();
  });

  it('quarantines only the failed deck layer and reports an explicit degraded state', () => {
    const onLayerDegraded = vi.fn();
    const onError = vi.fn();
    const renderer = new DeckMapRenderer() as unknown as {
      callbacks: MapRendererCallbacks;
      quarantinedLayerIds: Set<string>;
      handleDeckLayerError: (error: Error, layer?: { id: string }) => void;
      destroy: () => void;
    };
    renderer.callbacks = { ...callbacks(), onLayerDegraded, onError };

    const failure = new Error('fixture shader failure');
    renderer.handleDeckLayerError(failure, { id: 'world-event-points' });
    renderer.handleDeckLayerError(failure, { id: 'world-event-points' });

    expect(renderer.quarantinedLayerIds).toEqual(new Set(['world-event-points']));
    expect(onLayerDegraded).toHaveBeenCalledOnce();
    expect(onLayerDegraded).toHaveBeenCalledWith('world-event-points', failure);
    expect(onError).toHaveBeenCalledOnce();
    renderer.destroy();
  });

  it('lets retryable PMTiles resources recover before the readiness timeout', () => {
    const onError = vi.fn();
    const renderer = new DeckMapRenderer() as unknown as {
      callbacks: MapRendererCallbacks;
      fallbackApplied: boolean;
      handleMapError: (event: { error?: Error; message?: string }) => void;
      destroy: () => void;
    };
    renderer.callbacks = { ...callbacks(), onError };
    renderer.handleMapError({ message: 'Tile network request failed' });
    renderer.handleMapError({ message: 'Glyph fetch network request failed' });

    expect(renderer.fallbackApplied).toBe(false);
    expect(onError).toHaveBeenCalledTimes(2);
    renderer.destroy();
  });

  it('clears Deck hover locally when a map drag starts', () => {
    const off = vi.fn();
    const sync = vi.fn();
    const canvas = { style: { visibility: '' } } as HTMLCanvasElement;
    const renderer = new DeckMapRenderer() as unknown as {
      callbacks: MapRendererCallbacks;
      map: {
        off: (event: string, callback: () => void) => void;
        getLayer: () => undefined;
        getCanvas: () => { classList: { toggle: () => void } };
      };
      aviationOverlay: { getCanvas: () => HTMLCanvasElement };
      aviationOverlayViewSync: () => void;
      hoveredDeckEventId: string | null;
      deckHoverActive: boolean;
      handleMoveStart: () => void;
    };
    renderer.callbacks = callbacks();
    renderer.map = {
      off,
      getLayer: () => undefined,
      getCanvas: () => ({ classList: { toggle: vi.fn() } }),
    };
    renderer.aviationOverlay = { getCanvas: () => canvas };
    renderer.aviationOverlayViewSync = sync;
    renderer.hoveredDeckEventId = 'event:1';
    renderer.deckHoverActive = true;

    renderer.handleMoveStart();

    expect(renderer.hoveredDeckEventId).toBeNull();
    expect(renderer.deckHoverActive).toBe(false);
    // The stock render listener is removed at mount; camera synchronization is
    // one-shot after moveend, so drag start must not register another frame
    // listener or trigger a redraw.
    expect(off).not.toHaveBeenCalled();
    expect(sync).not.toHaveBeenCalled();
    expect(canvas.style.visibility).toBe('hidden');
  });

  it('does not require App hover state during Deck destruction', () => {
    const renderer = new DeckMapRenderer() as unknown as {
      callbacks: MapRendererCallbacks | null;
      hoveredDeckEventId: string | null;
      destroy: () => void;
    };
    renderer.callbacks = callbacks();
    renderer.hoveredDeckEventId = 'event:1';

    renderer.destroy();

    expect(renderer.callbacks).toBeNull();
  });

  it('keeps animated aircraft hover and click without a second GPU picking pass', () => {
    const onEventSelect = vi.fn();
    const show = vi.fn();
    const flight = {
      id: 'flight:cpu-pick',
      category: 'infrastructure',
      title: 'PX 204',
      severity: 'watch',
      geometry: { type: 'LineString', coordinates: [[0, 0], [2, 2]] },
      locationPrecision: 'exact',
      sources: [{ provider: 'fixture' }],
      limitations: [],
      relatedMarketIds: [],
      properties: { mapEntity: 'air-flight', flightId: 'PX204' },
    } as GeoEvent;
    const renderer = new DeckMapRenderer() as unknown as {
      callbacks: MapRendererCallbacks;
      map: {
        project: () => { x: number; y: number };
        getCanvas: () => { classList: { toggle: () => void } };
      };
      seededAircraftPickPoints: Array<{
        id: string;
        event: GeoEvent;
        position: [number, number];
        color: [number, number, number, number];
        angle: number;
        size: number;
        count: number;
      }>;
      manualAviationTooltip: { show: typeof show; clear: () => void; destroy: () => void };
      hoveredDeckEventId: string | null;
      handleManualAviationHover: (event: { point: { x: number; y: number } }) => void;
      handleManualAviationClick: () => void;
      destroy: () => void;
    };
    renderer.callbacks = { ...callbacks(), onEventSelect };
    renderer.map = {
      project: () => ({ x: 20, y: 20 }),
      getCanvas: () => ({ classList: { toggle: vi.fn() } }),
    };
    renderer.seededAircraftPickPoints = [{
      id: 'PX204',
      event: flight,
      position: [1, 1],
      color: [92, 241, 255, 170],
      angle: 45,
      size: 14,
      count: 1,
    }];
    renderer.manualAviationTooltip = { show, clear: vi.fn(), destroy: vi.fn() };

    renderer.handleManualAviationHover({ point: { x: 24, y: 23 } });
    expect(renderer.hoveredDeckEventId).toBe(flight.id);
    expect(show).toHaveBeenCalled();
    renderer.handleManualAviationClick();
    expect(onEventSelect).toHaveBeenCalledWith(flight.id);

    renderer.map = null as never;
    renderer.destroy();
  });

  it('does not rebuild disaster and geometry layers for an aviation-only refresh', () => {
    const hazard = {
      id: 'hazard:stable',
      category: 'natural-hazard',
      severity: 'warning',
      geometry: { type: 'Point', coordinates: [10, 10] },
      properties: {},
    } as GeoEvent;
    const flight = (id: string) => ({
      id,
      category: 'infrastructure',
      title: id,
      severity: 'info',
      geometry: { type: 'LineString', coordinates: [[0, 0], [1, 1]] },
      locationPrecision: 'exact',
      sources: [{ provider: 'fixture' }],
      limitations: [],
      relatedMarketIds: [],
      properties: { mapEntity: 'air-flight' },
    }) as GeoEvent;
    const previousPointLayers = [{}];
    const previousGeometryLayers = [{}];
    const renderer = new DeckMapRenderer() as unknown as {
      events: GeoEvent[];
      pointLayers: object[];
      geometryLayers: object[];
      geometryNeedsCommit: boolean;
      aviationLayerSections: object | null;
      setEvents: (events: GeoEvent[]) => void;
      destroy: () => void;
    };
    renderer.events = [hazard, flight('flight:old')];
    renderer.pointLayers = previousPointLayers;
    renderer.geometryLayers = previousGeometryLayers;
    renderer.geometryNeedsCommit = false;
    renderer.aviationLayerSections = {};

    renderer.setEvents([hazard, flight('flight:new')]);

    expect(renderer.pointLayers).toBe(previousPointLayers);
    expect(renderer.geometryLayers).toBe(previousGeometryLayers);
    expect(renderer.geometryNeedsCommit).toBe(false);
    expect(renderer.aviationLayerSections).toBeNull();
    renderer.destroy();
  });

  it('clears and destroys the renderer-owned SVG tooltip', () => {
    const clear = vi.fn();
    const destroyTooltip = vi.fn();
    const renderer = new SvgMapRenderer() as unknown as {
      callbacks: MapRendererCallbacks | null;
      tooltip: { clear: () => void; destroy: () => void };
      pause: () => void;
      destroy: () => void;
    };
    renderer.callbacks = callbacks();
    renderer.tooltip = { clear, destroy: destroyTooltip };

    renderer.pause();
    renderer.destroy();

    expect(clear).toHaveBeenCalled();
    expect(destroyTooltip).toHaveBeenCalled();
    expect(renderer.callbacks).toBeNull();
  });

  it('reduces aviation frame rate after a delayed RAF and recovers gradually', () => {
    const renderer = new DeckMapRenderer() as unknown as {
      animationIntervalMs: number;
      animationRecoveryFrames: number;
      updateAdaptiveAnimationBudget: (frameCostMs: number) => void;
      destroy: () => void;
    };

    renderer.updateAdaptiveAnimationBudget(25);
    expect(renderer.animationIntervalMs).toBe(80);

    for (let frame = 0; frame < 599; frame += 1) renderer.updateAdaptiveAnimationBudget(16);
    expect(renderer.animationIntervalMs).toBe(80);
    expect(renderer.animationRecoveryFrames).toBe(599);

    renderer.updateAdaptiveAnimationBudget(16);
    expect(renderer.animationIntervalMs).toBe(40);
    expect(renderer.animationRecoveryFrames).toBe(0);
    renderer.destroy();
  });

  it('runs hazard pulses on a separate 500ms clock and stops it during interaction', () => {
    const setInterval = vi.fn(() => 41);
    const clearInterval = vi.fn();
    vi.stubGlobal('window', { location: { search: '' }, setInterval, clearInterval });
    const critical = {
      id: 'earthquake:critical',
      category: 'natural-hazard',
      title: 'Critical earthquake',
      severity: 'critical',
      geometry: { type: 'Point', coordinates: [10, 10] },
      locationPrecision: 'exact',
      sources: [{ provider: 'fixture' }],
      limitations: [],
      relatedMarketIds: [],
      properties: {},
      hazardKind: 'earthquake',
      lifecycle: 'active',
      coverage: { scope: 'global', label: 'fixture', isComplete: false, gaps: [] },
      severityEvidence: { provider: 'fixture', mappingVersion: 'fixture', reason: 'fixture' },
      revision: { nativeEventId: 'critical' },
      metrics: { kind: 'earthquake', magnitude: 6.2 },
    } as GeoEvent;
    const renderer = new DeckMapRenderer() as unknown as {
      overlay: object;
      state: ReturnType<typeof defaultWorldEventMapState>;
      events: GeoEvent[];
      pulseEvents: GeoEvent[];
      eventFirstSeenAt: Map<string, number>;
      hazardPulseTimer: number | null;
      interacting: boolean;
      syncHazardPulseLoop: () => void;
      destroy: () => void;
    };
    renderer.overlay = {};
    renderer.state = defaultWorldEventMapState();
    renderer.events = [critical];
    renderer.pulseEvents = [critical];
    renderer.eventFirstSeenAt = new Map();

    renderer.syncHazardPulseLoop();
    expect(setInterval).toHaveBeenCalledWith(expect.any(Function), 500);
    expect(renderer.hazardPulseTimer).toBe(41);

    renderer.interacting = true;
    renderer.syncHazardPulseLoop();
    expect(clearInterval).toHaveBeenCalledWith(41);
    expect(renderer.hazardPulseTimer).toBeNull();
    renderer.destroy();
    vi.unstubAllGlobals();
  });

  it('renders the static map without allocating an aviation overlay', () => {
    const request = vi.fn();
    const renderer = new DeckMapRenderer() as unknown as {
      overlay: object;
      aviationOverlay: object | null;
      state: ReturnType<typeof defaultWorldEventMapState>;
      renderScheduler: { request: typeof request; cancel: () => void };
      requestRender: (invalidation: { points: boolean }) => void;
      destroy: () => void;
    };
    renderer.overlay = {};
    renderer.aviationOverlay = null;
    renderer.state = defaultWorldEventMapState();
    renderer.renderScheduler = { request, cancel: vi.fn() };

    renderer.requestRender({ points: true });

    expect(request).toHaveBeenCalledWith({ points: true });
    expect(renderer.aviationOverlay).toBeNull();
    renderer.destroy();
  });

  it('commits static labels atomically so cached Deck layers are never finalized and reinserted', () => {
    const setProps = vi.fn();
    const events = Array.from({ length: 6 }, (_, index) => ({
      id: `earthquake:cluster:${index}`,
      category: 'natural-hazard',
      title: `Earthquake ${index}`,
      severity: 'critical',
      geometry: { type: 'Point', coordinates: [10 + index * 0.02, 10 + index * 0.02] },
      locationPrecision: 'exact',
      sources: [{ provider: 'fixture', nativeId: String(index) }],
      limitations: [],
      relatedMarketIds: [],
      properties: { mapEntity: 'hazard-event' },
      hazardKind: 'earthquake',
      lifecycle: 'active',
      coverage: { scope: 'global', label: 'fixture', isComplete: false, gaps: [] },
      severityEvidence: { provider: 'fixture', mappingVersion: 'fixture', reason: 'fixture' },
      revision: { nativeEventId: String(index) },
      metrics: { kind: 'earthquake', magnitude: 6.4 },
    })) as GeoEvent[];
    const renderer = new DeckMapRenderer() as unknown as {
      overlay: { setProps: typeof setProps };
      state: ReturnType<typeof defaultWorldEventMapState>;
      events: GeoEvent[];
      clusterIndex: { update: (items: GeoEvent[]) => void };
      flushRender: (invalidation: {
        points: boolean;
        aviation: boolean;
        geometry: boolean;
        dynamic: boolean;
        pulse: boolean;
        interaction: boolean;
      }) => void;
      destroy: () => void;
    };
    renderer.overlay = { setProps };
    renderer.state = {
      ...defaultWorldEventMapState(),
      activeLayerIds: ['earthquakes-volcanoes'],
    };
    renderer.events = events;
    renderer.clusterIndex.update(events);

    renderer.flushRender({
      points: true,
      aviation: false,
      geometry: false,
      dynamic: false,
      pulse: false,
      interaction: false,
    });

    expect(setProps).toHaveBeenCalledTimes(1);
    const committed = setProps.mock.calls[0]?.[0]?.layers as Array<{ id: string }>;
    expect(committed.map((layer) => layer.id)).toContain('world-event-cluster-counts');
    renderer.destroy();
  });
});
