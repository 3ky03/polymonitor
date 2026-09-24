import { createFinanceWatchPanel } from '../finance-watch-kit';

export const panel = createFinanceWatchPanel({
  id: 'defi-yield-monitor',
  title: 'DEFI YIELDS',
  description: 'Protocol APY and TVL watch board.',
  question: 'Shows seeded DeFiLlama pools ranked by reliable protocol coverage, TVL depth, and sustainable APY.',
  limit: 10,
});
