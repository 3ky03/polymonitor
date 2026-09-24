import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-source-audit',
  title: 'SOURCE AUDIT',
  description: 'World Cup source readiness, cache mode, and provider coverage audit.',
  question: 'Shows which World Cup sources are live, stale, missing, or source-required before trusting a panel.',
  view: 'source-audit',
});
