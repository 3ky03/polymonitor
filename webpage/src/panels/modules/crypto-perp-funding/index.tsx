import { createFinanceWatchPanel } from '../finance-watch-kit';

export const panel = createFinanceWatchPanel({
  id: 'crypto-perp-funding',
  title: 'CRYPTO PERPS',
  description: 'Crypto perpetual funding pressure list.',
  question: 'Shows mainstream crypto perpetual contract price and funding pressure in fixed BTC, ETH, SOL, BNB order.',
  limit: 10,
});
