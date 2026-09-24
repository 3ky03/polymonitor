import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-group-table',
  title: 'GROUP TABLE',
  description: 'Group table and fixture feed for FIFA World Cup 2026.',
  question: 'Shows group standings and fixtures from schedule/results data.',
  view: 'group-table',
});
