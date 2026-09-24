import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-media-wire',
  title: 'MEDIA / INTEL',
  description: 'Global and local World Cup media intelligence wire.',
  question: 'Combines runtime World Cup signals with linked news rows for market and match context.',
  view: 'media-wire',
});
