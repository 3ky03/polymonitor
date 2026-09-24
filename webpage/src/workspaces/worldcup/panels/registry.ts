export type MatchFilter = 'all' | 'today' | 'future' | 'finished' | 'market';

export type WorldCupPanelId =
  | 'calendar'
  | 'match-control'
  | 'news'
  | 'win-probability'
  | 'odds-source-coverage'
  | 'line-movement'
  | 'venue-risk'
  | 'market-board'
  | 'group-advance'
  | 'team-power'
  | 'injury-load'
  | 'match-tempo'
  | 'ref-cards'
  | 'travel-load'
  | 'news-impact'
  | 'host-venue'
  | 'team-status'
  | 'lineup-board'
  | 'match-model'
  | 'group-table'
  | 'media-wire'
  | 'odds-liquidity'
  | 'venue-ref'
  | 'source-audit';

export const WORLD_CUP_PANEL_ORDER_STORAGE_KEY = 'polydata:worldcup-panel-order:v7';
export const WORLD_CUP_PANEL_DRAG_THRESHOLD = 5;

export const WORLD_CUP_PANEL_ORDER: WorldCupPanelId[] = [
  'calendar',
  'match-control',
  'win-probability',
  'line-movement',
  'odds-source-coverage',
  'venue-risk',
  'market-board',
  'group-advance',
  'team-power',
  'injury-load',
  'match-tempo',
  'odds-liquidity',
  'ref-cards',
  'travel-load',
  'news-impact',
  'news',
  'team-status',
  'lineup-board',
  'match-model',
  'group-table',
  'media-wire',
  'host-venue',
  'venue-ref',
  'source-audit',
];

export function readWorldCupPanelOrder(): WorldCupPanelId[] {
  if (typeof window === 'undefined') return [...WORLD_CUP_PANEL_ORDER];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(WORLD_CUP_PANEL_ORDER_STORAGE_KEY) || '[]');
    if (!Array.isArray(parsed)) return [...WORLD_CUP_PANEL_ORDER];
    const known = new Set<WorldCupPanelId>(WORLD_CUP_PANEL_ORDER);
    const ordered = parsed.filter((id): id is WorldCupPanelId => known.has(id));
    return [...ordered, ...WORLD_CUP_PANEL_ORDER.filter((id) => !ordered.includes(id))];
  } catch {
    return [...WORLD_CUP_PANEL_ORDER];
  }
}

export function reorderWorldCupPanels(
  panelIds: WorldCupPanelId[],
  draggedId: WorldCupPanelId,
  targetId: WorldCupPanelId,
  insertAfter: boolean,
) {
  if (draggedId === targetId) return panelIds;
  const next = panelIds.filter((id) => id !== draggedId);
  const targetIndex = next.indexOf(targetId);
  if (targetIndex < 0) return panelIds;
  next.splice(targetIndex + (insertAfter ? 1 : 0), 0, draggedId);
  return next;
}
