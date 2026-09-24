import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-odds-liquidity',
  title: 'ODDS / LIQUIDITY',
  description: 'Bookmaker odds and Polymarket liquidity monitor for a World Cup match.',
  question: 'Surfaces real odds snapshots, linked market count, and Polymarket volume for the selected fixture.',
  view: 'odds-liquidity',
});
