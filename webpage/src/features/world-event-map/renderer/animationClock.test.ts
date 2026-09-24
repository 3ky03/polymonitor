import { describe, expect, it } from 'vitest';
import {
  advanceAnimationTime,
  boundedAnimationDelta,
  MAP_ANIMATION_MAX_DELTA_MS,
} from './animationClock';

describe('map animation clock', () => {
  it('does not advance on the first frame after resume', () => {
    expect(boundedAnimationDelta(null, 1_000)).toBe(0);
  });

  it('caps an inactive-tab gap before advancing motion', () => {
    const elapsed = boundedAnimationDelta(100, 10_000);
    expect(elapsed).toBe(MAP_ANIMATION_MAX_DELTA_MS);
    expect(advanceAnimationTime(2.5, elapsed)).toBeCloseTo(2.58);
  });
});
