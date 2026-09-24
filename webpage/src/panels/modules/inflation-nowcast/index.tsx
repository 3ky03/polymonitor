import { Panel } from '@/components/Panel';
import { fetchRuntimeInflationNowcast } from '@/services/api';
import type { RuntimeInflationNowcastPayload, RuntimePolymarketMacroMapPayload } from '@/types';
import { formatRelative } from '../../shared/formatters';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { LinkedMarketRegistry, MarketImplicationStrip, PanelGlyph, RowGlyph, StatusBadge, linkedMacroMarkets, signalToneClass } from '../macro-intel';

function rowValue(row?: Record<string, string | undefined> | null, key?: string) {
  if (!row || !key) return '--';
  return row[key] || row[key.trim()] || '--';
}

function rowMonth(row?: Record<string, string | undefined> | null) {
  return rowValue(row, 'Month') !== '--' ? rowValue(row, 'Month') : rowValue(row, 'Date');
}

function toneFromNowcast(payload?: RuntimeInflationNowcastPayload | null) {
  const core = Number(rowValue(payload?.monthOverMonth, 'Core CPI'));
  if (Number.isFinite(core) && core >= 0.3) return 'hot';
  if (Number.isFinite(core) && core <= 0.2) return 'cool';
  return 'watch';
}

function valueTone(value?: string | null) {
  const n = Number(String(value || '').replace('%', ''));
  if (!Number.isFinite(n)) return 'neutral';
  if (n >= 0.3) return 'hot';
  if (n <= 0.2) return 'cool';
  return 'watch';
}

function InflationMetric({ label, value, icon }: { label: string; value?: string; icon: 'cpi' | 'fed' | 'growth' }) {
  const tone = valueTone(value);
  return (
    <div className={`wm-nowcast-driver-row ${tone}`}>
      <RowGlyph icon={icon} tone={tone} label={label} />
      <span>{label}</span>
      <strong>{value || '--'}</strong>
    </div>
  );
}

function InflationNowcastPanel({ payload, macroPayload }: { payload?: RuntimeInflationNowcastPayload | null; macroPayload?: RuntimePolymarketMacroMapPayload | null }) {
  const tone = toneFromNowcast(payload);
  const signal = tone === 'hot' ? 'NOWCAST CPI HOTTER' : tone === 'cool' ? 'NOWCAST DISINFLATION' : 'NOWCAST WATCH';
  const linkedMarkets = linkedMacroMarkets(macroPayload, ['cpi', 'fed']);
  const mom = payload?.monthOverMonth || {};
  const yoy = payload?.yearOverYear || {};
  return (
    <Panel
      title="INFLATION NOWCAST"
      badge={payload?.status === 'ok' ? 'FED' : 'SEED'}
      status={payload?.status === 'ok' ? 'live' : 'muted'}
      count={(payload?.quarterly || []).length + (payload?.monthOverMonth ? 1 : 0) + (payload?.yearOverYear ? 1 : 0)}
      className="wm-market-panel wm-nowcast-panel"
      dataPanelId="inflation-nowcast"
    >
      <div className={`wm-intel-signal-band ${signalToneClass(signal)}`}>
        <div className="wm-intel-signal-main">
          <PanelGlyph icon="cpi" tone={tone} />
          <div className="wm-intel-signal-copy">
            <span>Cleveland Fed Driver</span>
            <strong>{signal}</strong>
          </div>
        </div>
        <em>{payload?.source || 'Cleveland Fed Inflation Nowcasting'} / {rowMonth(mom)}</em>
      </div>
      <div className="wm-nowcast-driver-grid">
        <InflationMetric label="CPI MoM" value={rowValue(mom, 'CPI')} icon="cpi" />
        <InflationMetric label="Core CPI" value={rowValue(mom, 'Core CPI')} icon="cpi" />
        <InflationMetric label="PCE MoM" value={rowValue(mom, 'PCE')} icon="fed" />
        <InflationMetric label="Core PCE" value={rowValue(mom, 'Core PCE')} icon="fed" />
        <InflationMetric label="CPI YoY" value={rowValue(yoy, 'CPI')} icon="growth" />
        <InflationMetric label="Core PCE YoY" value={rowValue(yoy, 'Core PCE')} icon="growth" />
      </div>
      <div className="wm-nowcast-quarterly-log">
        {(payload?.quarterly || []).slice(0, 3).map((row, index) => (
          <div key={`${row['Quarter'] || row.Date || index}`}>
            <RowGlyph icon="calendar" tone="official" label="Quarterly nowcast" />
            <span>{row['Quarter'] || row.Date || `Q${index + 1}`}</span>
            <strong>{row['Core CPI'] || row.CPI || '--'} core CPI</strong>
            <StatusBadge tone={valueTone(row['Core CPI'] || row.CPI)}>{row.PCE || row['Core PCE'] || '--'}</StatusBadge>
          </div>
        ))}
      </div>
      <MarketImplicationStrip items={['CPI buckets', 'Core CPI', 'PCE', 'Fed reaction']} />
      <LinkedMarketRegistry title="PMKT CPI / Fed" items={linkedMarkets} emptyLabel="Awaiting macro map" />
      <div className="wm-nowcast-footer">
        <span>{(payload?.cacheMode || 'seed').toUpperCase()}</span>
        <span>{(payload?.status || 'warming').toUpperCase()}</span>
        <span>{formatRelative(payload?.generatedAt)}</span>
      </div>
    </Panel>
  );
}

const renderers: PanelRenderMap = {
  'inflation-nowcast': {
    render: (ctx) => <InflationNowcastPanel payload={ctx.runtimeData['inflation-nowcast'] as RuntimeInflationNowcastPayload | undefined} macroPayload={ctx.runtimeData['polymarket-macro-map'] as RuntimePolymarketMacroMapPayload | undefined} />,
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'inflation-nowcast',
  title: 'Inflation Nowcast',
  eyebrow: 'macro',
  description: 'Cleveland Fed CPI/PCE nowcasting panel.',
  defaultEnabled: false,
}, {
  tier: 'slow',
  fetchData: fetchRuntimeInflationNowcast,
});
