import { createFinanceWatchPanel } from '../finance-watch-kit';

export const panel = createFinanceWatchPanel({
  id: 'blockchain-policy-news',
  title: 'CHAIN POLICY',
  description: 'Blockchain policy, regulation, and enforcement feed.',
  question: 'Seeded policy headlines for crypto bills, SEC/CFTC actions, stablecoin legislation, and court events.',
  mode: 'feed',
  limit: 12,
});
