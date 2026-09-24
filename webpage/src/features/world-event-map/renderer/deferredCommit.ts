type Cancel = () => void;

export function scheduleAfterMainThreadYield(run: () => void): Cancel {
  let cancelled = false;
  const scheduler = (globalThis as unknown as { scheduler?: { yield?: () => Promise<void> } }).scheduler;
  const yielded = typeof scheduler?.yield === 'function'
    ? scheduler.yield()
    : new Promise<void>((resolve) => setTimeout(resolve, 0));
  void yielded.then(() => {
    if (!cancelled) run();
  });
  return () => { cancelled = true; };
}

/** Keeps only the latest heavy-layer request and commits it after yielding. */
export class DeferredLatestCommit<T> {
  private pending: T | null = null;
  private cancelScheduled: Cancel | null = null;

  constructor(
    private readonly schedule: (run: () => void) => Cancel,
    private readonly commit: (value: T) => void,
  ) {}

  stage(value: T) {
    this.pending = value;
    this.cancelScheduled?.();
    this.cancelScheduled = this.schedule(() => {
      this.cancelScheduled = null;
      const pending = this.pending;
      this.pending = null;
      if (pending != null) this.commit(pending);
    });
  }

  cancel() {
    this.cancelScheduled?.();
    this.cancelScheduled = null;
    this.pending = null;
  }
}
