import { describe, expect, it } from 'vitest';
import { rectIntersectsViewport } from './rendererVisibility';

const rect = (overrides: Partial<DOMRectReadOnly> = {}) => ({
  top: 100,
  right: 500,
  bottom: 500,
  left: 100,
  width: 400,
  height: 400,
  ...overrides,
});

describe('renderer visibility', () => {
  it('recognizes a map that is already visible before IntersectionObserver reports', () => {
    expect(rectIntersectsViewport(rect(), 1920, 1080)).toBe(true);
  });

  it('rejects zero-sized and fully offscreen hosts', () => {
    expect(rectIntersectsViewport(rect({ width: 0 }), 1920, 1080)).toBe(false);
    expect(rectIntersectsViewport(rect({ top: 1200, bottom: 1600 }), 1920, 1080)).toBe(false);
    expect(rectIntersectsViewport(rect({ right: -1, left: -401 }), 1920, 1080)).toBe(false);
  });

  it('accepts a partially visible map host', () => {
    expect(rectIntersectsViewport(rect({ top: -300, bottom: 100 }), 1920, 1080)).toBe(true);
  });
});
