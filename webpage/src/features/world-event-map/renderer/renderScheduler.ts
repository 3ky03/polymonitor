export type MapRenderInvalidation = {
  points: boolean;
  aviation: boolean;
  geometry: boolean;
  dynamic: boolean;
  pulse: boolean;
  interaction: boolean;
};

const EMPTY_INVALIDATION: MapRenderInvalidation = {
  points: false,
  aviation: false,
  geometry: false,
  dynamic: false,
  pulse: false,
  interaction: false,
};

/** Coalesces setter, camera and data bursts into one map commit per frame. */
export class MapRenderScheduler {
  private frame: number | null = null;
  private pending: MapRenderInvalidation = { ...EMPTY_INVALIDATION };

  constructor(
    private readonly requestFrame: (callback: FrameRequestCallback) => number,
    private readonly cancelFrame: (handle: number) => void,
    private readonly flush: (invalidation: MapRenderInvalidation) => void,
  ) {}

  request(next: Partial<MapRenderInvalidation> = {}) {
    this.pending = {
      points: this.pending.points || Boolean(next.points),
      aviation: this.pending.aviation || Boolean(next.aviation),
      geometry: this.pending.geometry || Boolean(next.geometry),
      dynamic: this.pending.dynamic || Boolean(next.dynamic),
      pulse: this.pending.pulse || Boolean(next.pulse),
      interaction: this.pending.interaction || Boolean(next.interaction),
    };
    if (this.frame != null) return;
    this.frame = this.requestFrame(() => {
      this.frame = null;
      const pending = this.pending;
      this.pending = { ...EMPTY_INVALIDATION };
      this.flush(pending);
    });
  }

  cancel() {
    if (this.frame != null) this.cancelFrame(this.frame);
    this.frame = null;
    this.pending = { ...EMPTY_INVALIDATION };
  }

  get scheduled() {
    return this.frame != null;
  }
}
