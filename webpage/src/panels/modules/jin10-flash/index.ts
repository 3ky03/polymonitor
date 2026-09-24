import { fetchRuntimeJin10 } from '@/services/api';
import { jin10PanelRenderers } from '../../jin10-panels';
import { runtimePanelFromRenderer } from '../helpers';

export const panel = runtimePanelFromRenderer(jin10PanelRenderers, {
  id: 'jin10-flash',
  title: 'Jin10 Flash',
  eyebrow: 'macro',
  description: 'Jin10 macro flash feed in market-card form.',
  defaultEnabled: true,
}, {
  tier: 'fast',
  fetchData: () => fetchRuntimeJin10(24),
});
