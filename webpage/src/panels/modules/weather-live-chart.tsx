import { useEffect, useRef } from 'preact/hooks';
import {
  AreaSeries,
  ColorType,
  createChart,
  LineSeries,
  type AreaData,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type Time,
} from 'lightweight-charts';

type AreaPoint = AreaData<Time>;
type LinePoint = LineData<Time>;

export type WeatherLiveChartSeries =
  | {
    id: string;
    type: 'area';
    color: string;
    topColor: string;
    bottomColor: string;
    data: AreaPoint[];
  }
  | {
    id: string;
    type: 'line';
    color: string;
    data: LinePoint[];
  };

type SeriesApi = ISeriesApi<'Area'> | ISeriesApi<'Line'>;

export function numericTime(value: number): Time {
  return value as Time;
}

export function WeatherLiveChart({
  className = '',
  series,
  showTimeScale = true,
  valueFormatter,
}: {
  className?: string;
  series: WeatherLiveChartSeries[];
  showTimeScale?: boolean;
  valueFormatter?: (value: number) => string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRefs = useRef<Map<string, SeriesApi>>(new Map());
  const formatterRef = useRef(valueFormatter || ((value: number) => String(Math.round(value))));

  useEffect(() => {
    formatterRef.current = valueFormatter || ((value: number) => String(Math.round(value)));
    chartRef.current?.applyOptions({
      localization: {
        priceFormatter: (value: number) => formatterRef.current(value),
      },
    });
  }, [valueFormatter]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;

    const chart = createChart(container, {
      autoSize: false,
      height: Math.max(120, container.clientHeight || 160),
      width: Math.max(240, container.clientWidth || 320),
      layout: {
        attributionLogo: false,
        background: { type: ColorType.Solid, color: '#050606' },
        textColor: '#a9b4b9',
        fontFamily: 'var(--font-mono)',
        fontSize: 10,
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.035)' },
        horzLines: { color: 'rgba(255,255,255,0.085)' },
      },
      rightPriceScale: {
        borderColor: 'rgba(255,255,255,0.10)',
        scaleMargins: { top: 0.12, bottom: 0.14 },
      },
      timeScale: {
        borderColor: 'rgba(255,255,255,0.10)',
        visible: showTimeScale,
        fixLeftEdge: true,
        fixRightEdge: true,
        secondsVisible: false,
        timeVisible: true,
        rightOffset: 2,
        barSpacing: 10,
      },
      crosshair: {
        horzLine: { color: 'rgba(126,220,255,0.35)', labelBackgroundColor: '#0b7389' },
        vertLine: { color: 'rgba(126,220,255,0.22)', labelBackgroundColor: '#0b7389' },
      },
      handleScroll: false,
      handleScale: false,
      localization: {
        priceFormatter: (value: number) => formatterRef.current(value),
      },
    });

    chartRef.current = chart;
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return;
      const width = Math.max(240, Math.floor(entry.contentRect.width));
      const height = Math.max(120, Math.floor(entry.contentRect.height));
      chart.resize(width, height);
      chart.timeScale().fitContent();
    });
    observer.observe(container);

    return () => {
      observer.disconnect();
      seriesRefs.current.clear();
      chart.remove();
      chartRef.current = null;
    };
  }, [showTimeScale]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const activeIds = new Set(series.map((item) => item.id));
    for (const [id, api] of seriesRefs.current.entries()) {
      if (!activeIds.has(id)) {
        chart.removeSeries(api);
        seriesRefs.current.delete(id);
      }
    }

    for (const item of series) {
      let api = seriesRefs.current.get(item.id);
      if (!api) {
        api = item.type === 'area'
          ? chart.addSeries(AreaSeries, {
            lineColor: item.color,
            topColor: item.topColor,
            bottomColor: item.bottomColor,
            lineWidth: 2,
            priceLineVisible: true,
            lastValueVisible: true,
          })
          : chart.addSeries(LineSeries, {
            color: item.color,
            lineWidth: 2,
            priceLineVisible: true,
            lastValueVisible: true,
          });
        seriesRefs.current.set(item.id, api);
      }

      if (item.type === 'area') {
        api.setData(item.data);
      } else {
        api.setData(item.data);
      }
    }

    chart.timeScale().fitContent();
  }, [series]);

  return <div ref={containerRef} className={`wm-weather-live-chart ${className}`.trim()} />;
}
