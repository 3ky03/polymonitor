import { systemPanelRenderers } from '../../system-panels';
import { panelFromRenderer } from '../helpers';

export const panel = panelFromRenderer(systemPanelRenderers, {
  id: 'live-api-status',
  title: 'Live API Status',
  eyebrow: 'system',
  description: 'Runtime health and sync status.',
  defaultEnabled: true,
});
