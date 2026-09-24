import { createTechPanel } from '../tech-watch-kit';

export const panel = createTechPanel({
  id: 'big-tech-market-cap',
  title: 'BIG TECH CAP',
  description: 'Mega-cap tech quote and market-cap rank board.',
  question: 'Ranks major technology companies by current market cap and shows price moves that can drive largest-company markets.',
  mode: 'market-cap',
  limit: 16,
});
