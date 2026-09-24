import { marketPanelRenderers } from '../../market-panels';
import { panelFromRenderer } from '../helpers';

export const panel = panelFromRenderer(marketPanelRenderers, {
  id: 'featured-market',
  title: 'Market Context',
  eyebrow: 'focus',
  description: 'Resolution rules, tags, and oracle references for the selected market.',
  defaultEnabled: true,
});
