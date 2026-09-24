import { describe, expect, it } from 'vitest';
import type { GeoEvent, HazardEvent, HazardKind } from '../domain/types';
import {
  EVENT_LIST_ROW_HEIGHT,
  eventListRegion,
  eventTypeOptions,
  filterEventListEvents,
  virtualEventWindow,
  type EventListFilters,
} from './EventList';

const NOW = Date.parse('2026-08-06T12:00:00Z');
const DEFAULT_FILTERS: EventListFilters = {
  query: '',
  eventType: 'all',
  severity: 'all',
  time: 'all',
  region: 'all',
};

function event(
  id: string,
  severity: GeoEvent['severity'] = 'info',
  coordinates: [number, number] = [0, 0],
  updatedAt = '2026-08-06T11:30:00Z',
): GeoEvent {
  return {
    id,
    category: 'conflict',
    title: id,
    severity,
    updatedAt,
    geometry: { type: 'Point', coordinates },
    locationPrecision: 'exact',
    locationLabel: `${id} location`,
    sources: [{ provider: 'fixture', nativeId: id }],
    limitations: [],
    relatedMarketIds: [],
    properties: {},
  };
}

function hazard(
  id: string,
  hazardKind: HazardKind,
  coordinates: [number, number],
  severity: GeoEvent['severity'] = 'warning',
  updatedAt = '2026-08-06T11:30:00Z',
): HazardEvent {
  return {
    ...event(id, severity, coordinates, updatedAt),
    category: 'natural-hazard',
    hazardKind,
    lifecycle: 'active',
    coverage: { scope: 'global', label: 'Fixture', isComplete: true, gaps: [] },
    severityEvidence: { provider: 'fixture', mappingVersion: 'test', reason: 'fixture' },
    revision: { nativeEventId: id },
    metrics: hazardKind === 'earthquake'
      ? { kind: 'earthquake', magnitude: 6.1 }
      : { kind: 'volcano-or-other' },
  };
}

describe('complete virtualized event drawer', () => {
  it('keeps the complete filtered collection while rendering only the visible window', () => {
    const events = Array.from({ length: 2_000 }, (_, index) => (
      event(`event-${index}`, index < 10 ? 'critical' : 'info')
    ));
    const filtered = filterEventListEvents(events, DEFAULT_FILTERS, NOW);
    const window = virtualEventWindow(filtered, EVENT_LIST_ROW_HEIGHT * 600, 340);

    expect(filtered).toHaveLength(2_000);
    expect(window.totalHeight).toBe(2_000 * EVENT_LIST_ROW_HEIGHT);
    expect(window.items.length).toBeLessThan(30);
    expect(window.startIndex).toBeGreaterThan(0);
    expect(window.items[0]?.index).toBe(window.startIndex);
  });

  it('filters by search, disaster type, severity and time without slicing results', () => {
    const events = [
      hazard('Alaska earthquake', 'earthquake', [-150, 62], 'critical'),
      hazard('Pacific cyclone', 'tropical-cyclone', [145, 18], 'warning'),
      hazard('Old earthquake', 'earthquake', [140, 36], 'critical', '2026-07-01T00:00:00Z'),
    ];
    const filtered = filterEventListEvents(events, {
      query: 'alaska',
      eventType: 'earthquake',
      severity: 'critical',
      time: '24h',
      region: 'all',
    }, NOW);

    expect(filtered.map((item) => item.id)).toEqual(['Alaska earthquake']);
    expect(eventTypeOptions(events).map((option) => option.value)).toEqual([
      'earthquake',
      'tropical-cyclone',
    ]);
  });

  it('uses declared or geometry-derived geographic regions for regional filtering', () => {
    const northAmerica = hazard('Alaska earthquake', 'earthquake', [-150, 62]);
    const europe = hazard('France heat alert', 'extreme-heat', [2.3, 46.2]);
    const declared = { ...event('Declared region'), regionCode: 'oceania' };

    expect(eventListRegion(northAmerica)).toBe('america');
    expect(eventListRegion(europe)).toBe('eu');
    expect(eventListRegion(declared)).toBe('oceania');
    expect(filterEventListEvents([northAmerica, europe, declared], {
      ...DEFAULT_FILTERS,
      region: 'eu',
    }, NOW).map((item) => item.id)).toEqual(['France heat alert']);
  });

  it('orders the complete list by severity, freshness and title', () => {
    const ordered = filterEventListEvents([
      event('Info', 'info'),
      event('Warning old', 'warning', [0, 0], '2026-08-06T10:00:00Z'),
      event('Critical', 'critical'),
      event('Warning fresh', 'warning', [0, 0], '2026-08-06T11:00:00Z'),
    ], DEFAULT_FILTERS, NOW);

    expect(ordered.map((item) => item.id)).toEqual([
      'Critical',
      'Warning fresh',
      'Warning old',
      'Info',
    ]);
  });
});
