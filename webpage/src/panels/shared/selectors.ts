import type { ContentItem, PanelRenderContext } from '@/types';

function focusedTrades(ctx: PanelRenderContext) {
  return ctx.bundle?.trades?.length
    ? ctx.bundle.trades
    : (ctx.bootstrap?.featuredMarket?.id === ctx.selectedMarketId ? ctx.bootstrap.recentTradesPreview : []);
}

function focusedOracle(ctx: PanelRenderContext) {
  return ctx.bundle?.oracle?.timeline?.length
    ? ctx.bundle.oracle.timeline
    : (ctx.bootstrap?.featuredMarket?.id === ctx.selectedMarketId ? ctx.bootstrap.oraclePreview : []);
}

function focusedContent(ctx: PanelRenderContext) {
  return ctx.bundle?.content?.items?.length
    ? ctx.bundle.content.items
    : (ctx.bootstrap?.featuredMarket?.id === ctx.selectedMarketId ? ctx.bootstrap.contentPreview : ctx.latestContent);
}

function globalMarkets(ctx: PanelRenderContext) {
  return ctx.markets.length ? ctx.markets : (ctx.bootstrap?.activeMarketsPreview || []);
}

function globalOracle(ctx: PanelRenderContext) {
  return ctx.globalOracle.length ? ctx.globalOracle : (ctx.bootstrap?.globalOraclePreview || []);
}

function inferContentType(item: ContentItem) {
  const explicit = String(item.contentType || '').trim().toLowerCase();
  if (explicit) return explicit;
  const haystack = `${item.title || ''} ${item.source || ''} ${item.url || ''}`.toLowerCase();
  if (/youtube|youtu\.be|vimeo|video|livestream|stream/.test(haystack)) return 'video';
  if (/report|brief|dossier|outlook|filing/.test(haystack)) return 'report';
  if (/arxiv\.org|ssrn\.com|nber\.org|working paper|research paper|journal/.test(haystack)) return 'research';
  return 'news';
}

function contentByType(items: ContentItem[], contentType: string) {
  return items.filter((item) => inferContentType(item) === contentType);
}

function fallbackContent(items: ContentItem[], contentType: string) {
  const filtered = contentByType(items, contentType);
  if (filtered.length) return filtered;
  return items.slice(0, 4);
}


export {
  focusedTrades,
  focusedOracle,
  focusedContent,
  globalMarkets,
  globalOracle,
  contentByType,
  fallbackContent,
};
