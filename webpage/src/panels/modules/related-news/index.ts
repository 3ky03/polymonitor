import { contentPanelRenderers } from '../../content-panels';
import { panelFromRenderer } from '../helpers';

export const panel = panelFromRenderer(contentPanelRenderers, {
  id: 'related-news',
  title: 'Related News',
  eyebrow: 'intel',
  description: 'News linked to focused market.',
  defaultEnabled: true,
});
