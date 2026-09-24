import { createFinanceWatchPanel } from '../finance-watch-kit';

export const panel = createFinanceWatchPanel({
  id: 'global-index-monitor',
  title: 'GLOBAL INDICES',
  description: 'Global index quote board.',
  question: 'Shows whether major global equity indices are broadly risk-on or risk-off.',
  mode: 'grid',
  limit: 12,
});
