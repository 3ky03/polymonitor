import { chainPanelRenderers } from '../../chain-panels';
import { panelFromRenderer } from '../helpers';

export const panel = panelFromRenderer(chainPanelRenderers, {
  id: 'bbo-monitor',
  title: 'BBO Monitor',
  eyebrow: 'lob',
  description: 'Runtime best bid/ask snapshot.',
  defaultEnabled: true,
});
