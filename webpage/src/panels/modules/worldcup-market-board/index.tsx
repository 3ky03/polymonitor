import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-market-board',
  title: 'WC MARKET BOARD',
  description: 'Verified World Cup Polymarket links and outcome price board.',
  question: 'Shows only local DB or Gamma-matched World Cup markets; it does not fabricate missing prices.',
  view: 'market-board',
});
