import { marketPanelRenderers } from '../../market-panels';
import { panelFromRenderer } from '../helpers';

export const panel = panelFromRenderer(marketPanelRenderers, {
  id: 'price-chart',
  title: 'Price Surface',
  eyebrow: 'price',
  description: 'Focused market probability curve.',
  size: 'wide',
  defaultEnabled: true,
});
