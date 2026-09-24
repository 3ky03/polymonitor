import { authRequest } from '@/services/auth';

export type AlertRule = {
  id: string;
  marketId: number;
  kind: string;
  threshold: number | null;
  enabled: boolean;
  cooldownSeconds: number;
  conditionActive: boolean;
  lastTriggeredAt: string | null;
};

export type WatchlistItem = {
  marketId: number;
  title: string;
  slug?: string | null;
  category?: string | null;
  latestPrice: number | null;
  change24h: number | null;
  volume24h: number;
  tradeCount24h: number;
  priceUpdatedAt?: string | null;
  completionStatus: string;
  oracleStage: string;
  rules: AlertRule[];
};

export type Watchlist = {
  id: string | null;
  name: string;
  items: WatchlistItem[];
  summary: { markets: number; activeRules: number; oracleGaps: number; unreadAlerts: number };
  alertKinds: string[];
};

export type AlertEvent = {
  id: string;
  marketId: number;
  marketTitle?: string | null;
  kind: string;
  severity: string;
  title: string;
  detail: string;
  observedPrice: number | null;
  oracleStatus?: string | null;
  sourceObservedAt?: string | null;
  occurredAt: string;
  readAt: string | null;
};

export type NotificationPreferences = {
  inAppEnabled: boolean;
  webPushEnabled: boolean;
  digestMode: 'realtime' | 'hourly' | 'daily' | 'off';
  quietStartMinute: number | null;
  quietEndMinute: number | null;
  timezone: string;
  updatedAt?: string | null;
  channels: Record<string, { available: boolean; connected: boolean; detail?: string }>;
};

export type WebPushStatus = {
  available: boolean;
  publicKey: string | null;
  enabled: boolean;
  connected: boolean;
  subscriptionCount: number;
};

export type WorkspaceLayout = {
  exists: boolean;
  revision: number;
  activePanelIds: string[];
  panelLayout: Record<string, { rowSpan?: number; colSpan?: number }>;
  preferences: {
    region?: string;
    viewMode?: string;
    mapZoom?: number;
    showPanelLibrary?: boolean;
    marketGroupSort?: string;
  };
  clientUpdatedAt: string | null;
  updatedAt: string | null;
};

export type BriefingRegistryItem = {
  id: string;
  publicId: string;
  title: string;
  createdAt: string;
  expiresAt: string;
  revokedAt: string | null;
  active: boolean;
};

export type BriefingMarket = {
  marketId: number;
  title: string;
  category?: string | null;
  latestPrice: number | null;
  change24h: number | null;
  volume24h: number;
  tradeCount24h: number;
  completionStatus: string;
  oracleStage: string;
  observedAt?: string | null;
};

export type PublicBriefing = {
  title: string;
  createdAt: string;
  expiresAt: string;
  snapshot: {
    schema: string;
    generatedAt: string;
    summary: { trackedMarkets: number; topMarkets: number; oracleAttention: number };
    trackedMarkets: BriefingMarket[];
    topMarkets: BriefingMarket[];
    oracleAttention: BriefingMarket[];
    workspaceLens: { revision: number; activePanelIds: string[]; updatedAt: string | null };
    source: { kind: string; markets: string; oracle: string; warning: string };
  };
};

export const fetchWatchlist = () => authRequest<Watchlist>('/product/watchlist');
export const addWatchlistMarket = (marketId: number, note = '') =>
  authRequest<{ item: unknown }>('/product/watchlist/markets', {
    method: 'POST',
    body: JSON.stringify({ marketId, note }),
  }, { csrf: true });
export const removeWatchlistMarket = (marketId: number) =>
  authRequest<{ status: string }>(`/product/watchlist/markets/${marketId}`, { method: 'DELETE' }, { csrf: true });
export const createAlertRule = (marketId: number, kind: string, threshold: number | null) =>
  authRequest<{ item: AlertRule }>('/product/alert-rules', {
    method: 'POST',
    body: JSON.stringify({ marketId, kind, threshold, cooldownSeconds: 3600 }),
  }, { csrf: true });
export const deleteAlertRule = (id: string) =>
  authRequest<{ status: string }>(`/product/alert-rules/${encodeURIComponent(id)}`, { method: 'DELETE' }, { csrf: true });
export const fetchAlerts = () => authRequest<{ items: AlertEvent[]; unreadCount: number }>('/product/alerts?limit=100');
export const markAlertRead = (id: string) =>
  authRequest<{ status: string }>(`/product/alerts/${encodeURIComponent(id)}/read`, { method: 'POST', body: '{}' }, { csrf: true });
export const markAllAlertsRead = () =>
  authRequest<{ status: string; count: number }>('/product/alerts/read-all', { method: 'POST', body: '{}' }, { csrf: true });
export const fetchNotificationPreferences = () =>
  authRequest<NotificationPreferences>('/product/notification-preferences');
export const updateNotificationPreferences = (value: Omit<NotificationPreferences, 'channels' | 'updatedAt'>) =>
  authRequest<NotificationPreferences>('/product/notification-preferences', {
    method: 'PUT',
    body: JSON.stringify(value),
  }, { csrf: true });
export const fetchWebPushStatus = () => authRequest<WebPushStatus>('/product/web-push');
export const registerWebPushSubscription = (subscription: PushSubscriptionJSON) =>
  authRequest<WebPushStatus>('/product/web-push/subscriptions', {
    method: 'POST',
    body: JSON.stringify(subscription),
  }, { csrf: true });
export const revokeWebPushSubscription = (endpoint: string) =>
  authRequest<WebPushStatus>('/product/web-push/subscriptions', {
    method: 'DELETE',
    body: JSON.stringify({ endpoint }),
  }, { csrf: true });

export const fetchWorkspaceLayout = () => authRequest<WorkspaceLayout>('/product/workspace-layout');
export const saveWorkspaceLayout = (value: Omit<WorkspaceLayout, 'exists' | 'updatedAt'>) =>
  authRequest<WorkspaceLayout>('/product/workspace-layout', {
    method: 'PUT',
    body: JSON.stringify(value),
  }, { csrf: true });
export const fetchBriefings = () =>
  authRequest<{ items: BriefingRegistryItem[] }>('/product/briefings');
export const createBriefing = (title: string) =>
  authRequest<{ item: BriefingRegistryItem }>('/product/briefings', {
    method: 'POST',
    body: JSON.stringify({ title }),
  }, { csrf: true });
export const revokeBriefing = (id: string) =>
  authRequest<{ status: string }>(`/product/briefings/${encodeURIComponent(id)}`, { method: 'DELETE' }, { csrf: true });
export const fetchPublicBriefing = (publicId: string) =>
  authRequest<PublicBriefing>(`/briefings/${encodeURIComponent(publicId)}`);
