import { oraclePanelRenderers } from '../../oracle-panels';
import { panelFromRenderer } from '../helpers';

export const panel = panelFromRenderer(oraclePanelRenderers, {
  id: 'oracle-feed',
  title: 'Oracle Feed',
  eyebrow: 'oracle',
  description: 'Recent oracle events across markets.',
  defaultEnabled: true,
});
