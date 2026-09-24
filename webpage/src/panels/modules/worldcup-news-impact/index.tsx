import { createWorldCupHomePanel } from '../worldcup-kit';

export const panel = createWorldCupHomePanel({
  id: 'worldcup-news-impact',
  title: 'NEWS IMPACT',
  description: 'World Cup news rows tagged for market, team, venue, and risk impact.',
  question: 'Ranks visible news by source tags and topic cues; model impact scoring remains source-gated.',
  view: 'news-impact',
});
