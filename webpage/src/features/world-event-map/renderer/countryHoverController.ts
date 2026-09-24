export interface CountryHoverQueryController<TPoint> {
  queue(point: TPoint): void;
  cancel(): void;
  isPending(): boolean;
}

/** Coalesces synchronous feature queries to the newest pointer position per frame. */
export function createCountryHoverQueryController<TPoint>(
  requestFrame: (callback: FrameRequestCallback) => number,
  cancelFrame: (handle: number) => void,
  runQuery: (point: TPoint) => void,
): CountryHoverQueryController<TPoint> {
  let frame: number | null = null;
  let pendingPoint: TPoint | null = null;

  const flush = () => {
    frame = null;
    const point = pendingPoint;
    pendingPoint = null;
    if (point !== null) runQuery(point);
  };

  return {
    queue(point) {
      pendingPoint = point;
      if (frame == null) frame = requestFrame(flush);
    },
    cancel() {
      pendingPoint = null;
      if (frame != null) cancelFrame(frame);
      frame = null;
    },
    isPending() {
      return frame != null;
    },
  };
}
