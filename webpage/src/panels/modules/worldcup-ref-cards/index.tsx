import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-ref-cards',
  title: 'REF / CARDS',
  description: 'Referee, disciplinary, and cards-risk signal board.',
  question: 'Shows referee and cards signals only when official or statistical provider rows exist.',
  view: 'ref-cards',
});
