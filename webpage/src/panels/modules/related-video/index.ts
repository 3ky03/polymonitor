import { contentPanelRenderers } from '../../content-panels';
import { panelFromRenderer } from '../helpers';

export const panel = panelFromRenderer(contentPanelRenderers, {
  id: 'related-video',
  title: 'Video Feed',
  eyebrow: 'content',
  description: 'Video content linked to market.',
  defaultEnabled: false,
});
