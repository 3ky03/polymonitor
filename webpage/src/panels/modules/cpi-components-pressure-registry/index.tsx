import { fetchRuntimeCpiComponentsPressureRegistry } from '@/services/api';
import type { RuntimeMacroRegistryPayload } from '@/types';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { MacroRegistryPanel } from '../macro-registry-panel';

const renderers: PanelRenderMap = {
  'cpi-components-pressure-registry': {
    render: (ctx) => (
      <MacroRegistryPanel
        config={{
          panelId: 'cpi-components-pressure-registry',
          title: 'CPI COMPONENTS',
          badge: 'BLS/EIA',
          glyph: 'basket',
          helpTitle: 'CPI Components Pressure Registry',
          helpText: 'Merges energy, food, and shelter/OER seeds into one dense component-pressure registry for headline and core CPI judgment.',
          emptyTitle: 'COMPONENTS WARMING',
          implicationItems: ['Headline CPI', 'Core sticky', 'Energy impulse', 'Food/shelter pressure'],
        }}
        payload={ctx.runtimeData['cpi-components-pressure-registry'] as RuntimeMacroRegistryPayload | undefined}
      />
    ),
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'cpi-components-pressure-registry',
  title: 'CPI Components Pressure Registry',
  eyebrow: 'macro',
  description: 'Energy, food, shelter, and CPI component pressure registry.',
  defaultEnabled: true,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeCpiComponentsPressureRegistry(48),
});
