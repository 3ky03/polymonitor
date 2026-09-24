import { createFinanceWatchPanel } from '../finance-watch-kit';

export const panel = createFinanceWatchPanel({
  id: 'stablecoin-monitor',
  title: 'STABLECOINS',
  description: 'Stablecoin peg and supply monitor.',
  question: 'Shows stablecoin peg deviation, supply size, and 7d supply change from seeded DeFiLlama data.',
  limit: 8,
});
