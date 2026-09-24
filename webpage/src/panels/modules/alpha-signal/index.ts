import { fetchRuntimeAlpha } from '@/services/api';
import { signalPanelRenderers } from '../../signal-panels';
import { runtimePanelFromRenderer } from '../helpers';

export const panel = runtimePanelFromRenderer(signalPanelRenderers, {
  id: 'alpha-signal',
  title: 'Alpha Signal',
  eyebrow: 'signal',
  description: 'Cross-source heuristic signal stack.',
  defaultEnabled: true,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeAlpha(8),
});
