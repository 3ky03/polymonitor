import { contentPanelRenderers } from '../../content-panels';
import { panelFromRenderer } from '../helpers';

export const panel = panelFromRenderer(contentPanelRenderers, {
  id: 'report-feed',
  title: 'Report Feed',
  eyebrow: 'content',
  description: 'Long-form reports and writeups.',
  defaultEnabled: false,
});
