import { describe, expect, it } from 'vitest';
import {
  MAP_CLUSTER_COUNT_ATLAS,
  MAP_CLUSTER_COUNT_ICON_MAPPING,
  mapClusterCountIcon,
} from './mapClusterCountAtlas';

describe('map cluster count atlas', () => {
  it('keeps common cluster counts exact and bounds large counts', () => {
    expect(mapClusterCountIcon(2)).toBe('2');
    expect(mapClusterCountIcon(42)).toBe('42');
    expect(mapClusterCountIcon(100)).toBe('100+');
    expect(mapClusterCountIcon(8_000)).toBe('100+');
  });

  it('provides a fixed GPU icon mapping without a TextLayer font atlas', () => {
    expect(MAP_CLUSTER_COUNT_ATLAS).toContain('data:image/svg+xml');
    expect(MAP_CLUSTER_COUNT_ICON_MAPPING['2']).toMatchObject({ mask: false });
    expect(MAP_CLUSTER_COUNT_ICON_MAPPING['100+']).toMatchObject({ mask: false });
  });
});
