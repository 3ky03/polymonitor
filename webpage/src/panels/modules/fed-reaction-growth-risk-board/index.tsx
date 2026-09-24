import { fetchRuntimeFedReactionGrowthRiskBoard } from '@/services/api';
import type { RuntimeMacroRegistryPayload } from '@/types';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { MacroRegistryPanel } from '../macro-registry-panel';

const renderers: PanelRenderMap = {
  'fed-reaction-growth-risk-board': {
    render: (ctx) => (
      <MacroRegistryPanel
        config={{
          panelId: 'fed-reaction-growth-risk-board',
          title: 'FED / GROWTH RISK',
          badge: 'FED/FRED',
          glyph: 'rates',
          helpTitle: 'Fed Reaction Growth Risk Board',
          helpText: 'Merges Fed/rates and growth/demand seeds to explain the likely CPI-to-Fed reaction path without depending on Polymarket market rows.',
          emptyTitle: 'FED GROWTH WARMING',
          implicationItems: ['Fed path', '2Y/10Y reaction', 'Demand risk', 'Recession narrative'],
        }}
        payload={ctx.runtimeData['fed-reaction-growth-risk-board'] as RuntimeMacroRegistryPayload | undefined}
      />
    ),
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'fed-reaction-growth-risk-board',
  title: 'Fed Reaction / Growth Risk Board',
  eyebrow: 'macro',
  description: 'Fed, rates, growth, demand, and recession-risk registry.',
  defaultEnabled: true,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeFedReactionGrowthRiskBoard(36),
});
