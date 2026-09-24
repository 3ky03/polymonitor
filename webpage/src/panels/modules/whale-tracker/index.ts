import { fetchRuntimeWhales } from '@/services/api';
import { chainPanelRenderers } from '../../chain-panels';
import { runtimePanelFromRenderer } from '../helpers';

export const panel = runtimePanelFromRenderer(chainPanelRenderers, {
  id: 'whale-tracker',
  title: 'Whale Tracker',
  eyebrow: 'chain',
  description: 'Largest recent on-chain trades.',
  defaultEnabled: true,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeWhales(14),
});
