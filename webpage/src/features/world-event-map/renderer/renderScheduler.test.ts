import { describe, expect, it, vi } from 'vitest';
import { MapRenderScheduler } from './renderScheduler';

describe('MapRenderScheduler', () => {
  it('coalesces a burst into one frame and merges invalidations', () => {
    let callback: FrameRequestCallback | null = null;
    const requestFrame = vi.fn((next: FrameRequestCallback) => {
      callback = next;
      return 7;
    });
    const flush = vi.fn();
    const scheduler = new MapRenderScheduler(requestFrame, vi.fn(), flush);

    scheduler.request({ points: true });
    scheduler.request({ aviation: true });
    scheduler.request({ dynamic: true });
    expect(requestFrame).toHaveBeenCalledTimes(1);

    (callback as FrameRequestCallback | null)?.(16);
    expect(flush).toHaveBeenCalledWith({
      points: true,
      aviation: true,
      geometry: false,
      dynamic: true,
      pulse: false,
      interaction: false,
    });
  });

  it('cancels pending work during pause or teardown', () => {
    const cancelFrame = vi.fn();
    const scheduler = new MapRenderScheduler(() => 11, cancelFrame, vi.fn());
    scheduler.request({ geometry: true });
    scheduler.cancel();
    expect(cancelFrame).toHaveBeenCalledWith(11);
    expect(scheduler.scheduled).toBe(false);
  });
});
