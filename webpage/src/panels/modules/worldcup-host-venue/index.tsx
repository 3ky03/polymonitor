import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-host-venue',
  title: 'HOST / VENUE',
  description: 'Host-city weather, venue, and match-load operations board.',
  question: 'Tracks host cities, match load, venue metadata, and weather conditions.',
  view: 'host-venue',
});
