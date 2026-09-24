import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-match-model',
  title: 'MATCH MODEL',
  description: 'xG, pressure, and tactical model surface for the selected fixture.',
  question: 'Shows statistical model rows only from trusted data providers or server-side models.',
  view: 'match-model',
});
