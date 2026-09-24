import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-calendar',
  title: 'WC CALENDAR',
  description: 'FIFA World Cup 2026 fixture calendar with host-city context.',
  question: 'Shows upcoming World Cup fixtures, match number, group, Beijing time, city, and venue.',
  view: 'calendar',
  size: 'tall',
});
