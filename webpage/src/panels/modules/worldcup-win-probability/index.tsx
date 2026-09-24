import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-win-probability',
  title: 'WIN PROBABILITY',
  description: 'Selected match Polymarket and bookmaker probability board.',
  question: 'Compares real Polymarket outcome prices with bookmaker probabilities when trusted rows are available.',
  view: 'win-probability',
});
