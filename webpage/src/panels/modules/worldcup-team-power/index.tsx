import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-team-power',
  title: 'TEAM POWER',
  description: 'Team roster availability and player pool strength surface.',
  question: 'Uses official roster rows when available; no team strength is estimated without a source.',
  view: 'team-power',
});
