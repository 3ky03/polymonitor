import { createFinanceWatchPanel } from '../finance-watch-kit';

export const panel = createFinanceWatchPanel({
  id: 'crypto-etf-flow',
  title: 'CRYPTO ETF',
  description: 'BTC and ETH ETF flow proxy board.',
  question: 'Uses seeded ETF quote and volume proxies to show whether ETF demand supports crypto price action.',
  mode: 'etf',
  limit: 8,
});
