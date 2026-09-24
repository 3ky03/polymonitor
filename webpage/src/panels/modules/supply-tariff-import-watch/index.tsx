import { fetchRuntimeSupplyTariffImportWatch } from '@/services/api';
import type { RuntimeMacroDriverPayload, RuntimePolymarketMacroMapPayload } from '@/types';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { MacroDriverPanel } from '../macro-driver-panel';

const renderers: PanelRenderMap = {
  'supply-tariff-import-watch': {
    render: (ctx) => (
      <MacroDriverPanel
        config={{
          panelId: 'supply-tariff-import-watch',
          title: 'SUPPLY / TARIFF',
          badge: 'PUBLIC',
          glyph: 'policy',
          driverLabel: 'Goods CPI Driver',
          helpTitle: 'Supply Tariff Import Watch',
          helpText: 'Tracks free FRED supply/import series and Federal Register trade-policy notices as CPI goods-price inputs for Polymarket inflation and Fed markets.',
          emptyTitle: 'SUPPLY WATCH WARMING',
          implicationItems: ['Goods CPI', 'Tariff headlines', 'Import prices', 'Fed reaction'],
          linkedCategories: ['cpi', 'fed', 'growth'],
          linkedTitle: 'PMKT CPI / trade',
        }}
        payload={ctx.runtimeData['supply-tariff-import-watch'] as RuntimeMacroDriverPayload | undefined}
        macroPayload={ctx.runtimeData['polymarket-macro-map'] as RuntimePolymarketMacroMapPayload | undefined}
      />
    ),
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'supply-tariff-import-watch',
  title: 'Supply Tariff Import Watch',
  eyebrow: 'macro',
  description: 'Supply-chain and tariff watch for CPI goods pressure.',
  defaultEnabled: false,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeSupplyTariffImportWatch(8),
});
