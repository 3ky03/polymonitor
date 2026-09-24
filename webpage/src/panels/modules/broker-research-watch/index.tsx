import { createFinanceWatchPanel } from '../finance-watch-kit';

export const panel = createFinanceWatchPanel({
  id: 'broker-research-watch',
  title: 'BROKER RESEARCH',
  description: 'Analyst rating and target-price change radar.',
  question: 'Tracks public broker notes, upgrades, downgrades, initiations, and target-price changes across high-signal equities, ETFs, and crypto-linked stocks.',
  mode: 'research',
  limit: 12,
});
