import { chainPanelRenderers } from '../../chain-panels';
import { panelFromRenderer } from '../helpers';

export const panel = panelFromRenderer(chainPanelRenderers, {
  id: 'sample-chain-trades',
  title: 'AI Special Markets',
  eyebrow: 'agent',
  description: 'Market-wide AI radar for unusual, high-attention, and fast-moving markets.',
  defaultEnabled: false,
});
