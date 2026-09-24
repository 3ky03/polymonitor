import { fetchRuntimeFedRatesPolymarketGap } from '@/services/api';
import type { RuntimeMacroDriverPayload, RuntimePolymarketMacroMapPayload } from '@/types';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { MacroDriverPanel } from '../macro-driver-panel';

const renderers: PanelRenderMap = {
  'fed-rates-polymarket-gap': {
    render: (ctx) => (
      <MacroDriverPanel
        config={{
          panelId: 'fed-rates-polymarket-gap',
          title: 'FED / RATES GAP',
          badge: 'FRED',
          glyph: 'rates',
          driverLabel: 'Policy Market Driver',
          helpTitle: 'Fed Rates Polymarket Gap',
          helpText: 'Uses public Fed funds, SOFR, Treasury yields, and curve data to frame whether Polymarket Fed/rates markets are fighting the rates tape.',
          emptyTitle: 'FED GAP WARMING',
          implicationItems: ['FOMC', 'Rate cuts', '2Y yield', 'CPI reaction'],
          linkedCategories: ['fed', 'cpi', 'growth'],
          linkedTitle: 'PMKT Fed markets',
        }}
        payload={ctx.runtimeData['fed-rates-polymarket-gap'] as RuntimeMacroDriverPayload | undefined}
        macroPayload={ctx.runtimeData['polymarket-macro-map'] as RuntimePolymarketMacroMapPayload | undefined}
      />
    ),
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'fed-rates-polymarket-gap',
  title: 'Fed Rates Polymarket Gap',
  eyebrow: 'macro',
  description: 'Rates tape and Polymarket Fed-market context.',
  defaultEnabled: false,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeFedRatesPolymarketGap(8),
});
