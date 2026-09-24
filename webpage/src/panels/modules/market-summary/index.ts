import { marketPanelRenderers } from '../../market-panels';
import { panelFromRenderer } from '../helpers';

export const panel = panelFromRenderer(marketPanelRenderers, {
  id: 'market-summary',
  title: 'Market Summary',
  eyebrow: 'market',
  description: 'Identifiers, category, timing, and pricing.',
  defaultEnabled: true,
});
