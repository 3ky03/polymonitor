import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-group-advance',
  title: 'GROUP ADVANCE',
  description: 'Group-stage standings and fixture pressure view.',
  question: 'Shows group rows, played count, goal difference, points, and fixture load from schedule/results data.',
  view: 'group-advance',
});
