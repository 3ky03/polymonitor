import { fetchRuntimeNbaIntel } from '@/services/api';
import { sportsPanelRenderers } from '../../sports-panels';
import { runtimePanelFromRenderer } from '../helpers';

export const panel = runtimePanelFromRenderer(sportsPanelRenderers, {
  id: 'nba-intel',
  title: 'NBA Intel',
  eyebrow: 'sports',
  description: 'ESPN news, starting lineups, and pregame rumors.',
  size: 'wide',
  defaultEnabled: true,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeNbaIntel(12),
});
