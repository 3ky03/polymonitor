import { marketPanelRenderers } from '../../market-panels';
import { panelFromRenderer } from '../helpers';

export const panel = panelFromRenderer(marketPanelRenderers, {
  id: 'price-implications',
  title: 'AI Market Brief',
  eyebrow: 'agent',
  description: 'Market-wide AI brief, focal points, and convergence signals.',
  defaultEnabled: false,
});
