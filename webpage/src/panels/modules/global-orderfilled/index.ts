import { chainPanelRenderers } from '../../chain-panels';
import { panelFromRenderer } from '../helpers';

export const panel = panelFromRenderer(chainPanelRenderers, {
  id: 'global-orderfilled',
  title: 'Orderfilled Flow',
  eyebrow: 'chain',
  description: 'Cross-market latest on-chain trades.',
  defaultEnabled: true,
});
