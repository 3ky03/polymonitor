import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-injury-load',
  title: 'INJURY LOAD',
  description: 'Injury and availability load monitor for the selected fixture.',
  question: 'Shows injury pressure only from connected roster or injury feeds.',
  view: 'injury-load',
});
