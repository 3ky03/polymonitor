import { fetchRuntimeNba } from '@/services/api';
import { sportsPanelRenderers } from '../../sports-panels';
import { runtimePanelFromRenderer } from '../helpers';

export const panel = runtimePanelFromRenderer(sportsPanelRenderers, {
  id: 'nba-scoreboard',
  title: 'NBA Scoreboard',
  eyebrow: 'sports',
  description: 'Upcoming and live NBA games.',
  defaultEnabled: true,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeNba(10),
});
