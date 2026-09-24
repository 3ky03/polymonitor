import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-lineup-board',
  title: 'LINEUP BOARD',
  description: 'Predicted XI and confirmed lineup board for the selected fixture.',
  question: 'Requires official team sheets or lineup providers; formations are not fabricated.',
  view: 'lineup-board',
});
