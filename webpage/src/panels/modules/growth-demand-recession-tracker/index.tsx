import { fetchRuntimeGrowthDemandRecessionTracker } from '@/services/api';
import type { RuntimeMacroDriverPayload, RuntimePolymarketMacroMapPayload } from '@/types';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { MacroDriverPanel } from '../macro-driver-panel';

const renderers: PanelRenderMap = {
  'growth-demand-recession-tracker': {
    render: (ctx) => (
      <MacroDriverPanel
        config={{
          panelId: 'growth-demand-recession-tracker',
          title: 'GROWTH / DEMAND',
          badge: 'FRED',
          glyph: 'growth',
          driverLabel: 'Macro Demand Driver',
          helpTitle: 'Growth Demand Recession Tracker',
          helpText: 'Tracks retail sales, PCE, industrial production, GDP, and the yield curve to separate demand-driven inflation risk from recession-market risk.',
          emptyTitle: 'GROWTH WATCH WARMING',
          implicationItems: ['Recession', 'GDP', 'Retail sales', 'Fed cuts'],
          linkedCategories: ['growth', 'fed', 'cpi'],
          linkedTitle: 'PMKT growth / recession',
        }}
        payload={ctx.runtimeData['growth-demand-recession-tracker'] as RuntimeMacroDriverPayload | undefined}
        macroPayload={ctx.runtimeData['polymarket-macro-map'] as RuntimePolymarketMacroMapPayload | undefined}
      />
    ),
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'growth-demand-recession-tracker',
  title: 'Growth Demand Recession Tracker',
  eyebrow: 'macro',
  description: 'Demand and recession indicators for macro market positioning.',
  defaultEnabled: false,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeGrowthDemandRecessionTracker(8),
});
