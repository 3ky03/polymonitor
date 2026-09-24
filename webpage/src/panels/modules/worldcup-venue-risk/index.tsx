import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-venue-risk',
  title: 'VENUE RISK',
  description: 'Weather and host-city load risk monitor for the selected World Cup venue.',
  question: 'Scores venue risk from live weather, wind, rain, temperature, and host-city match load.',
  view: 'venue-risk',
});
