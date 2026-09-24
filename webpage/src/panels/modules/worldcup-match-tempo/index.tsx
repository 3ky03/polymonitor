import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-match-tempo',
  title: 'MATCH TEMPO',
  description: 'Tempo, xG, and tactical signal board for the selected fixture.',
  question: 'Displays model/statistical tempo signals when trusted provider rows are connected.',
  view: 'match-tempo',
});
