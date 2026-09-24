import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-match-control',
  title: 'MATCH CONTROL',
  description: 'Selected fixture control card with market, venue, time, and weather context.',
  question: 'Keeps the selected fixture identity, kickoff clocks, venue, market links, and weather in one scan path.',
  view: 'match-control',
});
