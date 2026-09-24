import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-team-status',
  title: 'TEAM STATUS',
  description: 'Team roster and player availability status board.',
  question: 'Displays player-level availability once official squad or injury sources are connected.',
  view: 'team-status',
});
