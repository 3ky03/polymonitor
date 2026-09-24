export type ViewportRect = Pick<DOMRectReadOnly, 'top' | 'right' | 'bottom' | 'left' | 'width' | 'height'>;

export function rectIntersectsViewport(
  rect: ViewportRect,
  viewportWidth: number,
  viewportHeight: number,
) {
  if (rect.width <= 0 || rect.height <= 0 || viewportWidth <= 0 || viewportHeight <= 0) return false;
  return rect.bottom > 0
    && rect.right > 0
    && rect.top < viewportHeight
    && rect.left < viewportWidth;
}
