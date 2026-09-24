export function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

export function movingAverage(values: number[], windowSize: number) {
  return values.map((_, index) => {
    const start = Math.max(0, index - windowSize + 1);
    const slice = values.slice(start, index + 1);
    return slice.reduce((sum, value) => sum + value, 0) / slice.length;
  });
}

export function buildPath(points: Array<{ x: number; y: number }>, width: number, height: number, padding: number, yMin: number, yMax: number) {
  const xMin = Math.min(...points.map((point) => point.x), 0);
  const xMax = Math.max(...points.map((point) => point.x), 1);
  const xSpan = Math.max(1, xMax - xMin);
  const ySpan = Math.max(0.001, yMax - yMin);
  return points.map((point, index) => {
    const x = padding + ((point.x - xMin) / xSpan) * (width - padding * 2);
    const y = height - padding - ((point.y - yMin) / ySpan) * (height - padding * 2);
    return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
  }).join(' ');
}
