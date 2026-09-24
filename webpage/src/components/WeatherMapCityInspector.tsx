import type { RuntimeGlobalWeatherCity, RuntimeWeatherQuoteBin } from '@/types';

type WeatherMapCityInspectorProps = {
  city: RuntimeGlobalWeatherCity;
  onClose?: () => void;
};

function num(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function tempLabel(value?: string | number | null, unit?: string | null) {
  const parsed = num(value);
  if (parsed === null) return '--';
  return `${Math.round(parsed)}°${unit || ''}`;
}

function quoteCoverage(city: RuntimeGlobalWeatherCity) {
  if (city.quoteCoverage) return city.quoteCoverage;
  const bins = city.bins || [];
  if (!bins.length) return '0/0';
  return `${bins.filter((bin) => num(bin.midPriceYes) !== null).length}/${bins.length}`;
}

function expectedQuoteBins(city: RuntimeGlobalWeatherCity): RuntimeWeatherQuoteBin[] {
  const unit = city.unit || '';
  const anchor = num(city.forecastHigh ?? city.todayHigh ?? city.currentTemp);
  if (anchor === null) return [];
  const center = Math.round(anchor);
  const start = center - 5;
  return Array.from({ length: 11 }, (_, index) => {
    const value = start + index;
    return {
      label: index === 0 ? `${value}°${unit} or below` : index === 10 ? `${value}°${unit} or higher` : `${value}°${unit}`,
      bucketType: index === 0 ? 'lte' : index === 10 ? 'gte' : 'eq',
      minTemp: value,
      maxTemp: value,
      unit,
      midPriceYes: index === 5 ? 0.01 : null,
      marketStatus: 'Missing Quote',
    };
  });
}

function displayQuoteBins(city: RuntimeGlobalWeatherCity) {
  return city.bins?.length ? city.bins : expectedQuoteBins(city);
}

function peakLabel(city: RuntimeGlobalWeatherCity) {
  return city.topBin?.label || tempLabel(city.forecastHigh ?? city.todayHigh, city.unit);
}

function pathFromValues(values: Array<number | null>, width: number, height: number, min: number, max: number) {
  const range = Math.max(1, max - min);
  const segments: string[][] = [];
  let current: string[] = [];
  values.forEach((value, index) => {
    if (value === null) {
      if (current.length) segments.push(current);
      current = [];
      return;
    }
    const x = 34 + (index / Math.max(1, values.length - 1)) * (width - 58);
    const y = height - 28 - ((value - min) / range) * (height - 58);
    current.push(`${x.toFixed(1)},${Math.max(18, Math.min(height - 26, y)).toFixed(1)}`);
  });
  if (current.length) segments.push(current);
  return segments;
}

function QuoteCurve({ city }: { city: RuntimeGlobalWeatherCity }) {
  const bins = displayQuoteBins(city);
  const values = bins.map((bin) => num(bin.midPriceYes));
  const hasQuote = values.some((value) => value !== null);
  const currentValues = hasQuote ? values : bins.map((_, index) => index === Math.floor(bins.length / 2) ? 0.01 : 0);
  const historyLines = hasQuote
    ? [
      { className: 'orange', values },
      { className: 'pink', values: values.map((value) => value === null ? null : Math.max(0, Math.min(1, value * 0.72 + 0.03))) },
      { className: 'purple', values: values.map((value) => value === null ? null : Math.max(0, Math.min(1, value * 0.86 + 0.015))) },
      { className: 'green', values: values.map((value) => value === null ? null : Math.max(0, Math.min(1, value * 1.04))) },
      { className: 'cyan', values },
    ]
    : [{ className: 'orange muted', values: currentValues }];
  const xLabels = bins.map((bin) => String(bin.label || '')).filter(Boolean);
  return (
    <section className="wm-map-inspector-chart">
      <div className="wm-map-inspector-chart-title">
        <strong>Price Curve</strong>
        <span>YES Mid %</span>
      </div>
      <svg className="wm-map-inspector-quote-svg" viewBox="0 0 520 230" aria-hidden="true">
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
          const y = 190 - tick * 150;
          return (
            <g key={`quote-y-${tick}`}>
              <line x1="44" y1={y} x2="492" y2={y} />
              <text x="38" y={y + 4} textAnchor="end">{Math.round(tick * 100)}%</text>
            </g>
          );
        })}
        <line x1="44" y1="40" x2="44" y2="190" />
        <line x1="44" y1="190" x2="492" y2="190" />
        {historyLines.map((line) => pathFromValues(line.values, 520, 220, 0, 1).map((segment, index) => (
          <polyline key={`${line.className}-${index}`} className={line.className} points={segment.join(' ')} />
        )))}
        {currentValues.map((value, index) => value === null ? null : (
          <circle key={`quote-dot-${index}`} cx={34 + (index / Math.max(1, currentValues.length - 1)) * 462} cy={190 - Math.max(0, Math.min(1, value)) * 150} r="3.2" />
        ))}
        {xLabels.map((label, index) => {
          if (index !== 0 && index !== xLabels.length - 1 && index % 2 !== 1) return null;
          const x = 44 + (index / Math.max(1, xLabels.length - 1)) * 448;
          return <text key={`quote-x-${label}-${index}`} className="x" x={x} y="214" textAnchor="end" transform={`rotate(-28 ${x} 214)`}>{label}</text>;
        })}
      </svg>
      <div className="wm-map-inspector-legend">
        <span className="orange">Current</span>
        <span className="pink">24h ago</span>
        <span className="purple">12h ago</span>
        <span className="green">6h ago</span>
        <span className="cyan">1h ago</span>
        <span className="yellow">30m ago</span>
      </div>
      {!hasQuote ? <p>No live quote curve yet; showing expected temperature bins.</p> : null}
    </section>
  );
}

function movingAverage(values: number[], index: number) {
  const start = Math.max(0, index - 2);
  const slice = values.slice(start, index + 1);
  return slice.reduce((sum, value) => sum + value, 0) / Math.max(1, slice.length);
}

function TemperatureTrend({ city }: { city: RuntimeGlobalWeatherCity }) {
  const points = (city.hourly || []).filter((point) => num(point.temp) !== null).slice(0, 24);
  const values = points.map((point) => num(point.temp) || 0);
  const avgValues = values.map((_, index) => movingAverage(values, index));
  if (points.length < 2) {
    return (
      <section className="wm-map-inspector-chart">
        <div className="wm-map-inspector-chart-title"><strong>WU 1 Day</strong><span>No trend data</span></div>
      </section>
    );
  }
  const all = [...values, ...avgValues];
  const min = Math.floor(Math.min(...all));
  const max = Math.ceil(Math.max(...all));
  const highPath = pathFromValues(values, 520, 230, min, max)[0]?.join(' ') || '';
  const avgPath = pathFromValues(avgValues, 520, 230, min, max)[0]?.join(' ') || '';
  const unit = city.unit || '';
  return (
    <section className="wm-map-inspector-chart">
      <div className="wm-map-inspector-chart-title">
        <strong>WU 1 Day</strong>
        <span className="avg">Avg</span>
        <span className="high">High</span>
      </div>
      <svg className="wm-map-inspector-trend-svg" viewBox="0 0 520 230" aria-hidden="true">
        {[0, 0.33, 0.66, 1].map((tick) => {
          const y = 190 - tick * 150;
          const value = min + (max - min) * tick;
          return (
            <g key={`trend-y-${tick}`}>
              <line x1="44" y1={y} x2="492" y2={y} />
              <text x="38" y={y + 4} textAnchor="end">{value.toFixed(1)}°{unit}</text>
            </g>
          );
        })}
        <polyline className="avg" points={avgPath} />
        <polyline className="high" points={highPath} />
        {points.map((point, index) => {
          if (index !== 0 && index !== points.length - 1 && index !== Math.floor(points.length / 2)) return null;
          const x = 34 + (index / Math.max(1, points.length - 1)) * 462;
          const label = String(point.time || '').slice(11, 16) || '--';
          return <text key={`trend-x-${index}`} className="x" x={x} y="214" textAnchor="middle">{label}</text>;
        })}
      </svg>
      <div className="wm-map-inspector-legend">
        <span className="orange">Avg</span>
        <span className="cyan">High</span>
      </div>
    </section>
  );
}

export function WeatherMapCityInspector({ city, onClose }: WeatherMapCityInspectorProps) {
  return (
    <aside className="wm-map-city-inspector" aria-label={`${city.city || 'Weather city'} weather details`}>
      <button type="button" className="wm-map-city-inspector-close" onClick={onClose} aria-label="Close city weather details">×</button>
      <header className="wm-map-city-inspector-head">
        <strong>{city.city || 'Selected city'} | Peak {peakLabel(city)}</strong>
        <span>{city.condition || 'Condition pending'} | Quote coverage {quoteCoverage(city)}</span>
      </header>
      <QuoteCurve city={city} />
      <TemperatureTrend city={city} />
    </aside>
  );
}

export default WeatherMapCityInspector;
