import { fetchRuntimeLaborWageServicesPressure } from '@/services/api';
import type { RuntimeMacroDriverPayload, RuntimePolymarketMacroMapPayload } from '@/types';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { MacroDriverPanel } from '../macro-driver-panel';

const renderers: PanelRenderMap = {
  'labor-wage-services-pressure': {
    render: (ctx) => (
      <MacroDriverPanel
        config={{
          panelId: 'labor-wage-services-pressure',
          title: 'LABOR / WAGES',
          badge: 'BLS',
          glyph: 'labor',
          driverLabel: 'Services CPI Driver',
          helpTitle: 'Labor Wage Services Pressure',
          helpText: 'Combines public payrolls, unemployment, wages, claims, and JOLTS to show whether services inflation and Fed odds are being pulled hotter or cooler.',
          emptyTitle: 'LABOR WATCH WARMING',
          implicationItems: ['NFP', 'Wages', 'Services CPI', 'Fed decision'],
          linkedCategories: ['labor', 'cpi', 'fed'],
          linkedTitle: 'PMKT labor / Fed',
        }}
        payload={ctx.runtimeData['labor-wage-services-pressure'] as RuntimeMacroDriverPayload | undefined}
        macroPayload={ctx.runtimeData['polymarket-macro-map'] as RuntimePolymarketMacroMapPayload | undefined}
      />
    ),
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'labor-wage-services-pressure',
  title: 'Labor Wage Services Pressure',
  eyebrow: 'macro',
  description: 'Labor-market and wage pressure for services CPI and Fed markets.',
  defaultEnabled: false,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeLaborWageServicesPressure(8),
});
