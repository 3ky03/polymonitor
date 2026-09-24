import { fetchRuntimeCrypto } from '@/services/api';
import { macroPanelRenderers } from '../../macro-panels';
import { runtimePanelFromRenderer } from '../helpers';

export const panel = runtimePanelFromRenderer(macroPanelRenderers, {
  id: 'crypto-watch',
  title: 'Crypto Watch',
  eyebrow: 'macro',
  description: 'Crypto spot trends and sparklines.',
  defaultEnabled: true,
}, {
  tier: 'slow',
  intervalMs: 5000,
  fetchData: fetchRuntimeCrypto,
});
