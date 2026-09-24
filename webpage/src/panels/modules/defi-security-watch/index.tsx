import { createFinanceWatchPanel } from '../finance-watch-kit';

export const panel = createFinanceWatchPanel({
  id: 'defi-security-watch',
  title: 'DEFI SECURITY',
  description: 'DeFi exploit, vulnerability, and protocol risk feed.',
  question: 'Seeded security headlines for hacks, exploits, vulnerabilities, audits, and protocol risk events.',
  mode: 'feed',
  limit: 12,
});
