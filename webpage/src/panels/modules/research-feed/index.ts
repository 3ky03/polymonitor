import { contentPanelRenderers } from '../../content-panels';
import { panelFromRenderer } from '../helpers';

export const panel = panelFromRenderer(contentPanelRenderers, {
  id: 'research-feed',
  title: 'Research Feed',
  eyebrow: 'content',
  description: 'Research and analysis artifacts.',
  defaultEnabled: false,
});
