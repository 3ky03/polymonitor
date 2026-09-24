import { fetchRuntimeF1 } from '@/services/api';
import { f1PanelRenderers } from '../../f1-panels';
import { runtimePanelFromRenderer } from '../helpers';

export const panel = runtimePanelFromRenderer(f1PanelRenderers, {
  id: 'f1-trackside',
  title: 'BWE News',
  eyebrow: 'news',
  description: 'BWENews runtime RSS flashes with cached auto-refresh.',
  defaultEnabled: true,
}, {
  tier: 'fast',
  fetchData: () => fetchRuntimeF1(10),
});
