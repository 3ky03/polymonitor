import { fetchRuntimeSuspicious } from '@/services/api';
import { chainPanelRenderers } from '../../chain-panels';
import { runtimePanelFromRenderer } from '../helpers';

export const panel = runtimePanelFromRenderer(chainPanelRenderers, {
  id: 'suspicious-flow',
  title: 'Flow Watch',
  eyebrow: 'chain',
  description: 'Oracle-adjacent and large live trade flow.',
  defaultEnabled: true,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeSuspicious(12),
});
