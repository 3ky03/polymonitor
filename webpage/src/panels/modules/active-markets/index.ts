import { marketPanelRenderers } from '../../market-panels';
import { panelFromRenderer } from '../helpers';

export const panel = panelFromRenderer(marketPanelRenderers, {
  id: 'active-markets',
  title: 'Active Markets',
  eyebrow: 'market',
  description: 'Live active market list.',
  defaultEnabled: true,
});
