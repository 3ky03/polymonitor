import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-travel-load',
  title: 'TRAVEL LOAD',
  description: 'Travel and recovery load context for World Cup teams and venues.',
  question: 'Requires team-base and itinerary data; it does not synthesize distances or rest windows.',
  view: 'travel-load',
});
