import { fetchRuntimeShelterRentOerPressure } from '@/services/api';
import type { RuntimeMacroDriverPayload, RuntimePolymarketMacroMapPayload } from '@/types';
import type { PanelRenderMap } from '../../types';
import { runtimePanelFromRenderer } from '../helpers';
import { MacroDriverPanel } from '../macro-driver-panel';

const renderers: PanelRenderMap = {
  'shelter-rent-oer-pressure': {
    render: (ctx) => (
      <MacroDriverPanel
        config={{
          panelId: 'shelter-rent-oer-pressure',
          title: 'SHELTER / OER',
          badge: 'FRED',
          glyph: 'home',
          driverLabel: 'Core CPI Driver',
          helpTitle: 'Shelter Rent OER Pressure',
          helpText: 'Uses official FRED/BLS rent, OER, shelter CPI, and housing-price series to expose the sticky core-CPI channel Polymarket traders watch before CPI prints.',
          emptyTitle: 'SHELTER WATCH WARMING',
          implicationItems: ['Core CPI', 'OER', 'Rent CPI', 'Fed path'],
          linkedCategories: ['cpi', 'fed'],
          linkedTitle: 'PMKT shelter / CPI',
        }}
        payload={ctx.runtimeData['shelter-rent-oer-pressure'] as RuntimeMacroDriverPayload | undefined}
        macroPayload={ctx.runtimeData['polymarket-macro-map'] as RuntimePolymarketMacroMapPayload | undefined}
      />
    ),
  },
};

export const panel = runtimePanelFromRenderer(renderers, {
  id: 'shelter-rent-oer-pressure',
  title: 'Shelter Rent OER Pressure',
  eyebrow: 'macro',
  description: 'Rent and OER pressure for core CPI markets.',
  defaultEnabled: false,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeShelterRentOerPressure(8),
});
