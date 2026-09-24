import type { EquityPoint } from '../types';
import { buildPath } from '../utils/backtest';

export function EquityDrawdownChart({ points }: { points: EquityPoint[] }) {
  const width = 1280;
  const height = 260;
  const padding = 26;
  const equityPoints = points.map((point) => ({ x: point.index, y: point.equity }));
  const drawdownPoints = points.map((point) => ({ x: point.index, y: point.drawdown }));
  const equityMin = Math.min(...equityPoints.map((point) => point.y));
  const equityMax = Math.max(...equityPoints.map((point) => point.y));
  const equityPath = buildPath(equityPoints, width, height, padding, equityMin, equityMax);
  const drawdownPath = buildPath(drawdownPoints, width, height, padding, -5000, 0);
  return (
    <svg className="qtv-equity-chart" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label="Equity and drawdown curve">
      {Array.from({ length: 7 }, (_, index) => <line key={`h-${index}`} x1={padding} x2={width - padding} y1={padding + index * 34} y2={padding + index * 34} />)}
      {Array.from({ length: 11 }, (_, index) => <line key={`v-${index}`} y1={padding} y2={height - padding} x1={padding + index * 122} x2={padding + index * 122} />)}
      <path className="qtv-drawdown-fill" d={`${drawdownPath} L ${width - padding} ${padding} L ${padding} ${padding} Z`} />
      <path className="qtv-equity-fill" d={`${equityPath} L ${width - padding} ${height - padding} L ${padding} ${height - padding} Z`} />
      <path className="qtv-equity-line" d={equityPath} />
      <path className="qtv-drawdown-line" d={drawdownPath} />
    </svg>
  );
}
