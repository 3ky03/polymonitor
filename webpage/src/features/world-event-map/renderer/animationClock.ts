/** A deliberate 25 fps budget for map motion, independent of display refresh. */
export const MAP_ANIMATION_FRAME_INTERVAL_MS = 40;
export const MAP_ANIMATION_MAX_DELTA_MS = 80;

export function boundedAnimationDelta(previousTimestamp: number | null, timestamp: number) {
  if (previousTimestamp == null) return 0;
  return Math.min(
    MAP_ANIMATION_MAX_DELTA_MS,
    Math.max(0, timestamp - previousTimestamp),
  );
}

export function advanceAnimationTime(currentSeconds: number, elapsedMs: number) {
  return Math.max(0, currentSeconds + Math.max(0, elapsedMs) / 1_000);
}
