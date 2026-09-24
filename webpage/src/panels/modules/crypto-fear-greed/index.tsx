import { createFinanceWatchPanel } from '../finance-watch-kit';

export const panel = createFinanceWatchPanel({
  id: 'crypto-fear-greed',
  title: 'FEAR & GREED',
  description: 'Crypto sentiment gauge with price drivers.',
  question: 'Shows a gauge-style crypto sentiment regime with previous-score delta and major crypto momentum drivers.',
  mode: 'sentiment',
  limit: 6,
});
