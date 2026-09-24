import { describe, expect, it, vi } from 'vitest';
import { DeferredLatestCommit } from './deferredCommit';

describe('DeferredLatestCommit', () => {
  it('coalesces heavy geometry and commits only the latest value', () => {
    const tasks: Array<{ active: boolean; run: () => void }> = [];
    const commit = vi.fn();
    const deferred = new DeferredLatestCommit<number>((run) => {
      const task = { active: true, run };
      tasks.push(task);
      return () => { task.active = false; };
    }, commit);

    deferred.stage(1);
    deferred.stage(2);
    for (const task of tasks) if (task.active) task.run();

    expect(commit).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenCalledWith(2);
  });

  it('drops staged work after cancellation', () => {
    let active = true;
    let run: (() => void) | null = null;
    const commit = vi.fn();
    const deferred = new DeferredLatestCommit<number>((callback) => {
      run = callback;
      return () => { active = false; };
    }, commit);
    deferred.stage(1);
    deferred.cancel();
    if (active) (run as (() => void) | null)?.();
    expect(commit).not.toHaveBeenCalled();
  });
});
