import { chainPanelRenderers } from '../../chain-panels';
import { panelFromRenderer } from '../helpers';

export const panel = panelFromRenderer(chainPanelRenderers, {
  id: 'lob-depth',
  title: 'LOB Depth',
  eyebrow: 'lob',
  description: 'Focused market book depth.',
  size: 'wide',
  defaultEnabled: true,
});
