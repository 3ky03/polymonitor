import { fetchRuntimeLaborServicesInflationMonitor } from '@/services/api';
import type { RuntimeMacroRegistryPayload } from '@/types';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { MacroRegistryPanel } from '../macro-registry-panel';

const renderers: PanelRenderMap = {
  'labor-services-inflation-monitor': {
    render: (ctx) => (
      <MacroRegistryPanel
        config={{
          panelId: 'labor-services-inflation-monitor',
          title: 'LABOR / SERVICES',
          badge: 'BLS/DOL',
          glyph: 'labor',
          helpTitle: 'Labor Services Inflation Monitor',
          helpText: 'Combines payrolls, unemployment, wages, claims, JOLTS, and services-inflation bridges into one registry for Fed/CPI reaction judgment.',
          emptyTitle: 'LABOR SERVICES WARMING',
          implicationItems: ['Services CPI', 'Wage pressure', 'Labor cooling', 'Fed reaction'],
        }}
        payload={ctx.runtimeData['labor-services-inflation-monitor'] as RuntimeMacroRegistryPayload | undefined}
      />
    ),
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'labor-services-inflation-monitor',
  title: 'Labor / Services Inflation Monitor',
  eyebrow: 'macro',
  description: 'Labor, wage, claims, and services CPI pressure registry.',
  defaultEnabled: true,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeLaborServicesInflationMonitor(36),
});
