import { fetchRuntimeCommodities } from '@/services/api';
import { macroPanelRenderers } from '../../macro-panels';
import { runtimePanelFromRenderer } from '../helpers';

export const panel = runtimePanelFromRenderer(macroPanelRenderers, {
  id: 'commodities-watch',
  title: 'Commodities Watch',
  eyebrow: 'macro',
  description: 'Commodity price boards and sparklines.',
  defaultEnabled: true,
}, {
  tier: 'fast',
  fetchData: fetchRuntimeCommodities,
});
