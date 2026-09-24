import type { BacktestMetric } from '../types';

export function MetricCard({ metric }: { metric: BacktestMetric }) {
  return (
    <div className="qtv-metric" title={metric.tooltip}>
      <span>{metric.name}</span>
      <strong className={metric.status}>{metric.formattedValue}</strong>
      <em className={metric.status}>{metric.delta}</em>
    </div>
  );
}
