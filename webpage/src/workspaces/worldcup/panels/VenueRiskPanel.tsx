import { matchCity, WORLD_CUP_HOST_MATCH_COUNTS } from '../data';
import { SourceRequired } from '../components/SourceRequired';
import { WorldCupPanel as Panel } from '../components/WorldCupPanel';
import type { WorldCupDashboardPayload, WorldCupMatch } from '../types';
import { clampNumber } from './panelUtils';

type WeatherWithSource = WorldCupDashboardPayload['weather'][number] & { source?: string };

function venueRiskScore(temp: number, precipitation: number, wind: number, matchCount: number) {
  return clampNumber(Math.round((temp > 27 ? 18 : 6) + precipitation * 0.35 + wind * 0.7 + matchCount * 1.4), 5, 96);
}

function riskBand(score: number) {
  if (score >= 68) return { key: 'high', label: 'HIGH STRESS' };
  if (score >= 42) return { key: 'watch', label: 'WATCH STRESS' };
  return { key: 'low', label: 'LOW STRESS' };
}

function formatUpdatedAt(value?: string) {
  if (!value) return 'updated --';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return 'updated --';
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Shanghai',
  }).format(date);
}

export function VenueRiskPanel({ payload, match, weather }: { payload: WorldCupDashboardPayload; match: WorldCupMatch | null; weather?: WorldCupDashboardPayload['weather'][number] | null }) {
  const city = match ? matchCity(payload.cities, match.cityId) : payload.cities[0];
  const matchCount = city ? Math.max(WORLD_CUP_HOST_MATCH_COUNTS[city.id] || 0, payload.matches.filter((item) => item.cityId === city.id).length) : 0;
  if (!weather) {
    return (
      <Panel title="VENUE RISK" count={0} className="wm-worldcup-panel wm-worldcup-venue-risk-panel">
        <SourceRequired
          detail="Venue risk is calculated only from live weather plus real host-city match count. No default temperature or rain values are used."
          rows={[{ source: 'Open-Meteo runtime weather', status: 'required', detail: 'temperature, wind and precipitation probability by host city' }]}
        />
      </Panel>
    );
  }
  const temp = weather.current.tempC;
  const precipitation = weather.current.precipitationProbability || 0;
  const wind = weather.current.windKph || 0;
  const risk = venueRiskScore(temp, precipitation, wind, matchCount);
  const band = riskBand(risk);
  const source = (weather as WeatherWithSource).source || payload.intelligence?.source?.split('/').pop()?.trim() || 'Weather feed';
  const metrics = [
    { label: 'TEMP', value: temp, unit: 'C', max: 36, tone: 'gold', note: 'ambient' },
    { label: 'PRECIP', value: precipitation, unit: 'mm', max: 12, tone: 'blue', note: 'current' },
    { label: 'WIND', value: wind, unit: 'kph', max: 40, tone: 'purple', note: '10m speed' },
    { label: 'LOAD', value: matchCount * 6, unit: '%', max: 100, tone: 'green', note: `${matchCount} matches` },
  ] as const;
  return (
    <Panel title="VENUE RISK" count={risk} className="wm-worldcup-panel wm-worldcup-venue-risk-panel">
      <div className="wm-worldcup-venue-risk-stack">
        <section className={`wm-worldcup-risk-hero risk-${band.key}`}>
          <div className="wm-worldcup-risk-ring" style={{ '--risk-score': `${risk}%` }}>
            <span>{risk}</span>
          </div>
          <div>
            <em>{city?.city || 'Host city'}</em>
            <strong>{band.label}</strong>
            <b>{weather?.current.condition || 'venue watch'}</b>
          </div>
        </section>
        <div className="wm-worldcup-risk-meta">
          <span><em>SOURCE</em><strong>{source}</strong></span>
          <span><em>UPDATED</em><strong>{formatUpdatedAt(weather.generatedAt)}</strong></span>
        </div>
        <div className="wm-worldcup-risk-metric-grid">
          {metrics.map((metric) => (
            <span className={metric.tone} key={metric.label}>
              <em>{metric.label}</em>
              <strong>{metric.value}{metric.unit}</strong>
              <small>{metric.note}</small>
              <i style={{ width: `${clampNumber((Number(metric.value) / Number(metric.max)) * 100, 4, 100)}%` }} />
            </span>
          ))}
        </div>
      </div>
    </Panel>
  );
}
