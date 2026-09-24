import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-venue-ref',
  title: 'REF / VENUE',
  description: 'Selected venue card with nearby fixtures and referee-source readiness.',
  question: 'Pairs selected venue metadata with fixture rows and referee/venue source state.',
  view: 'venue-ref',
});
