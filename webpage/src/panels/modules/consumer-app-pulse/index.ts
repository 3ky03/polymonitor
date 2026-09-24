import { createTechPanel } from '../tech-watch-kit';

export const panel = createTechPanel({
  id: 'consumer-app-pulse',
  title: 'APP PULSE',
  description: 'App Store, Google Play, TikTok, and consumer app ranking and regulation signals.',
  question: 'Combines app-store rank rows with consumer-app news to monitor download, rank, ban, lawsuit, and product-update markets.',
  mode: 'app-pulse',
  limit: 40,
});
