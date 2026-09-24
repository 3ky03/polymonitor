import { systemPanelRenderers } from '../../system-panels';
import { panelFromRenderer } from '../helpers';

export const panel = panelFromRenderer(systemPanelRenderers, {
  id: 'system-health',
  title: 'System Health',
  eyebrow: 'system',
  description: 'Infra and sync readiness.',
  defaultEnabled: true,
});
