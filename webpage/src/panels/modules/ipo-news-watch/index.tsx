import { createFinanceWatchPanel } from '../finance-watch-kit';

export const panel = createFinanceWatchPanel({
  id: 'ipo-news-watch',
  title: 'IPO NEWS',
  description: 'IPO, listing, and filing catalyst feed.',
  question: 'Seeded IPO and listing headlines, including S-1/F-1 filing language and listing rumors.',
  mode: 'feed',
  limit: 12,
});
