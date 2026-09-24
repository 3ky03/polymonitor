import { describe, expect, it } from 'vitest';
import {
  WORLD_EVENT_LAYER_REGISTRY,
  eventMatchesWorldEventLayers,
  executableWorldEventLayers,
  selectableWorldEventLayers,
  worldEventLayerIdForEvent,
  worldEventLayerById,
} from './layerRegistry';
import type { HazardEvent } from '../domain/types';
import { MAP_SYMBOL_DEFINITIONS } from './mapSymbols';

describe('World Event Map layer registry', () => {
  it('is the single source of selectable product layers', () => {
    expect(selectableWorldEventLayers().map((layer) => layer.id)).toEqual([
      'weather-alerts',
      'earthquakes-volcanoes',
      'wildfires',
      'extreme-temperature',
      'climate-anomalies',
      'air-routes',
      'intel-hotspots',
      'ucdp',
      'sanctions-country-risk',
    ]);
    expect(selectableWorldEventLayers().filter((layer) => layer.defaultEnabled).map((layer) => layer.id)).toEqual([
      'weather-alerts',
      'earthquakes-volcanoes',
      'wildfires',
      'extreme-temperature',
      'climate-anomalies',
      'air-routes',
      'ucdp',
      'sanctions-country-risk',
    ]);
  });

  it('exposes evidence-gated country layers as real optional controls', () => {
    expect(worldEventLayerById('intel-hotspots')?.selectable).toBe(true);
    expect(worldEventLayerById('sanctions-country-risk')?.selectable).toBe(true);
    expect(worldEventLayerById('intel-hotspots')?.defaultEnabled).toBe(false);
    expect(worldEventLayerById('sanctions-country-risk')?.defaultEnabled).toBe(true);
    expect(worldEventLayerById('air-routes')?.defaultEnabled).toBe(true);
  });

  it('gates execution by renderer and required runtime sources', () => {
    const authoritativeSources = new Set(['usgs', 'climate-anomaly', 'global-transport-shipping']);
    expect(executableWorldEventLayers({ renderer: 'webgl', availableSources: authoritativeSources })
      .map((layer) => layer.id)).toEqual(expect.arrayContaining([
        'earthquakes-volcanoes',
        'climate-anomalies',
        'air-routes',
      ]));
    expect(executableWorldEventLayers({ renderer: 'webgl', availableSources: authoritativeSources })
      .map((layer) => layer.id)).not.toContain('intel-hotspots');
    expect(executableWorldEventLayers({ renderer: 'svg', availableSources: new Set() })
      .map((layer) => layer.id)).not.toContain('earthquakes-volcanoes');
  });

  it('declares source, legend and limitations for every layer', () => {
    for (const layer of WORLD_EVENT_LAYER_REGISTRY) {
      expect(layer.sourceKeys.length).toBeGreaterThan(0);
      expect(layer.explanation.sources.length).toBeGreaterThan(0);
      expect(layer.explanation.limitations.length).toBeGreaterThan(0);
      expect(MAP_SYMBOL_DEFINITIONS[layer.icon]).toBeDefined();
      expect(layer.legend.length).toBeGreaterThan(0);
      if (layer.cluster) expect(layer.clusterMinPoints).toBeGreaterThanOrEqual(5);
      else expect(layer.clusterMinPoints).toBe(0);
      for (const item of layer.legend) expect(MAP_SYMBOL_DEFINITIONS[item.symbol]).toBeDefined();
    }
  });

  it('gives every selectable layer a localized control label', () => {
    for (const layer of selectableWorldEventLayers()) {
      expect(layer.messageKey).toMatch(/^atlas\.layer\./);
    }
  });

  it('gives every selectable layer a distinct native emoji for the control surface', () => {
    const layers = selectableWorldEventLayers();
    expect(layers.every((layer) => /\p{Extended_Pictographic}/u.test(layer.panelEmoji))).toBe(true);
    expect(new Set(layers.map((layer) => layer.panelEmoji)).size).toBe(layers.length);
  });

  it('keeps every selectable layer label English-only for the map control', () => {
    for (const layer of selectableWorldEventLayers()) {
      expect(layer.label).toMatch(/^[\x20-\x7e]+$/);
    }
  });

  it('maps hazards by hazard kind instead of the broad natural-hazard category', () => {
    const event = {
      id: 'earthquake:usgs:test',
      category: 'natural-hazard',
      title: 'M5.0 earthquake',
      severity: 'warning',
      locationPrecision: 'exact',
      sources: [{ provider: 'USGS' }],
      limitations: [],
      relatedMarketIds: [],
      properties: {},
      hazardKind: 'earthquake',
      lifecycle: 'observed',
      coverage: { scope: 'global', label: 'USGS', isComplete: false, gaps: [] },
      severityEvidence: { provider: 'USGS', mappingVersion: 'v1', reason: 'magnitude' },
      revision: { nativeEventId: 'test' },
      metrics: { kind: 'earthquake', magnitude: 5 },
    } satisfies HazardEvent;
    expect(worldEventLayerIdForEvent(event)).toBe('earthquakes-volcanoes');
    expect(eventMatchesWorldEventLayers(event, ['earthquakes-volcanoes'])).toBe(true);
    expect(eventMatchesWorldEventLayers(event, ['weather-alerts'])).toBe(false);
  });
});
