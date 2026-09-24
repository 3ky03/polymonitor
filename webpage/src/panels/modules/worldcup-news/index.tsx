import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-news',
  title: 'WC NEWS',
  description: 'World Cup-linked news feed from runtime intel and latest content.',
  question: 'Shows source-backed World Cup news and market context without generated headlines.',
  view: 'news',
});
