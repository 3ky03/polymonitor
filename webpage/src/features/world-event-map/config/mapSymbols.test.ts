import { describe, expect, it } from 'vitest';
import type { GeoEvent, HazardEvent } from '../domain/types';
import {
  MAP_SYMBOL_ATLAS,
  MAP_SYMBOL_DEFINITIONS,
  MAP_SYMBOL_ICON_MAPPING,
  MAP_SYMBOL_MASK_ATLAS,
  MAP_SYMBOL_MASK_ICON_MAPPING,
  MAP_SYMBOL_PALETTES,
  MAP_SYMBOL_SIZE,
  mapSymbolForEvent,
} from './mapSymbols';

function hazard(hazardKind: HazardEvent['hazardKind']): HazardEvent {
  return {
    id: `fixture:${hazardKind}`,
    category: 'natural-hazard',
    title: hazardKind,
    severity: 'warning',
    geometry: { type: 'Point', coordinates: [0, 0] },
    locationPrecision: 'exact',
    sources: [{ provider: 'fixture' }],
    limitations: [],
    relatedMarketIds: [],
    properties: {},
    hazardKind,
    lifecycle: 'active',
    coverage: { scope: 'global', label: 'fixture', isComplete: true, gaps: [] },
    severityEvidence: { provider: 'fixture', mappingVersion: 'fixture', reason: 'fixture' },
    revision: { nativeEventId: hazardKind },
    metrics: hazardKind === 'earthquake'
      ? { kind: 'earthquake', magnitude: 6 }
      : { kind: 'volcano-or-other' },
  } as HazardEvent;
}

describe('Polymonitor map symbol atlas', () => {
  it('packs every semantic symbol into a colored atlas plus an aviation mask atlas', () => {
    const keys = Object.keys(MAP_SYMBOL_DEFINITIONS);
    expect(MAP_SYMBOL_ATLAS.startsWith('data:image/svg+xml;charset=utf-8,')).toBe(true);
    expect(MAP_SYMBOL_MASK_ATLAS.startsWith('data:image/svg+xml;charset=utf-8,')).toBe(true);
    expect(Object.keys(MAP_SYMBOL_ICON_MAPPING)).toEqual(keys);
    expect(Object.keys(MAP_SYMBOL_MASK_ICON_MAPPING)).toEqual(keys);
    expect(Object.keys(MAP_SYMBOL_PALETTES)).toEqual(keys);
    expect(new Set(keys.map((key) => MAP_SYMBOL_ICON_MAPPING[key as keyof typeof MAP_SYMBOL_ICON_MAPPING].x)).size)
      .toBe(keys.length);
    for (const [key, mapping] of Object.entries(MAP_SYMBOL_ICON_MAPPING)) {
      expect(mapping.width).toBe(MAP_SYMBOL_SIZE);
      expect(mapping.height).toBe(MAP_SYMBOL_SIZE);
      expect(mapping.mask).toBe(false);
      expect(MAP_SYMBOL_MASK_ICON_MAPPING[key as keyof typeof MAP_SYMBOL_MASK_ICON_MAPPING].mask).toBe(true);
      expect(MAP_SYMBOL_PALETTES[key as keyof typeof MAP_SYMBOL_PALETTES].primary).toMatch(/^#[0-9a-f]{6}$/i);
      expect(MAP_SYMBOL_DEFINITIONS[key as keyof typeof MAP_SYMBOL_DEFINITIONS].paths.length).toBeGreaterThan(0);
    }
  });

  it('maps hazard and conflict semantics to shapes instead of font glyphs', () => {
    expect(mapSymbolForEvent(hazard('earthquake'))).toBe('earthquake');
    expect(mapSymbolForEvent(hazard('wildfire'))).toBe('wildfire');
    const conflict = {
      ...hazard('volcano'),
      category: 'conflict',
      properties: { violenceType: '2' },
    } as unknown as GeoEvent;
    expect(mapSymbolForEvent(conflict)).toBe('conflict-nonstate');
    expect(Object.values(MAP_SYMBOL_DEFINITIONS).flatMap((definition) => definition.paths).join(''))
      .not.toMatch(/[▲◆◇✦◉≈]/);
  });
});
