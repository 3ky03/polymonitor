import { createFinanceWatchPanel } from '../finance-watch-kit';

export const panel = createFinanceWatchPanel({
  id: 'tradfi-perp-radar',
  title: 'TRADFI PERPS',
  description: 'Tokenized stock, index, and commodity perp radar.',
  question: 'Tracks traditional market perpetual contracts and reference perps such as index, gold, oil, and ETF-linked swaps.',
  limit: 16,
});
