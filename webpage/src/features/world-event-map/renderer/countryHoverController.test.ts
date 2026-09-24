import { describe, expect, it, vi } from 'vitest';
import { createCountryHoverQueryController } from './countryHoverController';

describe('country hover query controller', () => {
  it('runs one query per frame using the latest pointer position', () => {
    const scheduled: FrameRequestCallback[] = [];
    const runQuery = vi.fn();
    const controller = createCountryHoverQueryController<{ x: number }>(
      (callback) => {
        scheduled.push(callback);
        return 7;
      },
      vi.fn(),
      runQuery,
    );
    controller.queue({ x: 1 });
    controller.queue({ x: 2 });
    expect(controller.isPending()).toBe(true);
    scheduled[0]!(16);
    expect(runQuery).toHaveBeenCalledTimes(1);
    expect(runQuery).toHaveBeenCalledWith({ x: 2 });
    expect(controller.isPending()).toBe(false);
  });

  it('cancels a pending query on pointer leave', () => {
    const cancelFrame = vi.fn();
    const controller = createCountryHoverQueryController(
      () => 11,
      cancelFrame,
      vi.fn(),
    );
    controller.queue({ x: 1 });
    controller.cancel();
    expect(cancelFrame).toHaveBeenCalledWith(11);
    expect(controller.isPending()).toBe(false);
  });
});
