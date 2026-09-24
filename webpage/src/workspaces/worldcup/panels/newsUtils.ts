import type { ContentItem } from '@/types';
import type { WorldCupNewsItem } from '../types';

export function newsTags(item: WorldCupNewsItem) {
  const text = `${item.title} ${item.summary || ''}`.toLowerCase();
  const tags: Array<{ label: string; tone: string }> = [];
  tags.push({ label: 'ONGOING', tone: 'gray' });
  if (/(alert|risk|delay|storm|injur|security|crisis)/.test(text)) tags.push({ label: 'ALERT', tone: 'red' });
  if (/(strike|attack|war|quake|killed|evacuat|ceasefire|conflict|disaster)/.test(text)) tags.push({ label: 'ALERT', tone: 'red' });
  if (/(market|odds|price|trading|polymarket)/.test(text)) tags.push({ label: 'MARKET', tone: 'purple' });
  if (/(weather|storm|heat|rain|travel)/.test(text)) tags.push({ label: 'WEATHER', tone: 'blue' });
  if (/(squad|team|player|coach|roster)/.test(text)) tags.push({ label: 'TEAM', tone: 'gold' });
  return tags.slice(0, 2);
}

export function latestContentNewsFallback(content: ContentItem[]): WorldCupNewsItem[] {
  return content.slice(0, 24).map((item, index) => ({
    id: String(item.id || item.url || `latest-${index}`),
    title: item.title || 'Global monitor item',
    source: item.source || 'GLOBAL WIRE',
    url: item.url || '#',
    publishedAt: item.publishedAt || new Date().toISOString(),
    summary: item.summary || '',
  }));
}
