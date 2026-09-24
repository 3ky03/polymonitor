import { fetchRuntimeNbaMatchupPredictor } from '@/services/api';
import { sportsPanelRenderers } from '../../sports-panels';
import { runtimePanelFromRenderer } from '../helpers';

export const panel = runtimePanelFromRenderer(sportsPanelRenderers, {
  id: 'espn-matchup-predictor',
  title: 'ESPN Matchup Predictor',
  eyebrow: 'sports',
  description: 'ESPN BPI win probability, matchup quality, projected margin, and expected score.',
  size: 'wide',
  defaultEnabled: true,
}, {
  tier: 'slow',
  fetchData: () => fetchRuntimeNbaMatchupPredictor(8),
});
