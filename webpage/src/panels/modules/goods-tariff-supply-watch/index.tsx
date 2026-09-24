import { fetchRuntimeGoodsTariffSupplyWatch } from '@/services/api';
import type { RuntimeMacroRegistryPayload } from '@/types';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { MacroRegistryPanel } from '../macro-registry-panel';

const renderers: PanelRenderMap = {
  'goods-tariff-supply-watch': {
    render: (ctx) => (
      <MacroRegistryPanel
        config={{
          panelId: 'goods-tariff-supply-watch',
          title: 'GOODS / TARIFF',
          badge: 'PUBLIC',
          glyph: 'policy',
          helpTitle: 'Goods Tariff Supply Watch',
          helpText: 'Tracks seeded FRED goods/upstream series and Federal Register trade-policy events as CPI goods-price inputs.',
          emptyTitle: 'GOODS WATCH WARMING',
          implicationItems: ['Goods CPI', 'Tariff events', 'Import prices', 'Supply pressure'],
        }}
        payload={ctx.runtimeData['goods-tariff-supply-watch'] as RuntimeMacroRegistryPayload | undefined}
      />
    ),
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'goods-tariff-supply-watch',
  title: 'Goods / Tariff / Supply Chain Watch',
  eyebrow: 'macro',
  description: 'Goods inflation, tariff, import-price, and supply-chain registry.',
  defaultEnabled: true,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeGoodsTariffSupplyWatch(36),
});
