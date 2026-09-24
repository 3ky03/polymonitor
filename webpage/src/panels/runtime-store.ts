import type {
  PanelFetchContext,
  PanelModule,
  PanelRefreshTier,
  PanelRuntimeData,
} from './types';
import { getPanelRefreshPolicy } from './types';
import {
  fetchRuntimePanels,
  type RuntimePanelMetadata,
} from '@/services/api';

const PANEL_RUNTIME_LIMITS: Record<string, number> = {
  'alpha-signal': 8,
  'polybeats-feed': 8,
  'cpi-components-pressure-registry': 48,
  'cpi-release-calendar': 8,
  'cpi-release-command-center': 36,
  'blockchain-policy-news': 12,
  'broker-research-watch': 12,
  'crypto-etf-flow': 8,
  'crypto-fear-greed': 6,
  'crypto-funding-watch': 18,
  'crypto-perp-funding': 10,
  'defi-security-watch': 12,
  'defi-token-watch': 10,
  'defi-yield-monitor': 10,
  'energy-gasoline-shock': 6,
  'espn-matchup-predictor': 8,
  'esports-intel': 3,
  'fed-reaction-growth-risk-board': 36,
  'food-retail-basket-pressure': 8,
  'geo-sanctions-shock': 2000,
  'global-index-monitor': 12,
  'global-temperature-monitor': 60,
  'global-transport-shipping': 14,
  'weather-market-browser': 60,
  'goods-tariff-supply-watch': 36,
  'ipo-news-watch': 12,
  'jin10-flash': 24,
  'labor-services-inflation-monitor': 36,
  'nba-intel': 12,
  'nba-scoreboard': 10,
  'new-market-signals': 12,
  'polymarket-macro-map': 12,
  'sports-odds': 8,
  'stablecoin-monitor': 8,
  'suspicious-flow': 12,
  'tradfi-perp-radar': 16,
  'weather-news': 24,
  'whale-tracker': 14,
};

export function buildRuntimeDataPatch(panelId: string, value: unknown): PanelRuntimeData {
  return { [panelId]: value };
}

export function getRefreshablePanels(panels: PanelModule[], tier: PanelRefreshTier): PanelModule[] {
  return panels.filter((panel) => getPanelRefreshPolicy(panel)?.tier === tier && typeof panel.fetchData === 'function');
}

export type PanelRuntimeFetchOptions = {
  signal: AbortSignal;
  reason: PanelFetchContext['reason'];
  maxBatchSize?: number;
  onPanelData?: (panelId: string, value: unknown, metadata?: RuntimePanelMetadata) => void;
  onPanelError?: (panelId: string, error: Error) => void;
  onPanelSettled?: (panelId: string) => void;
};

export type PanelRuntimeFetchResult = {
  data: PanelRuntimeData;
  errors: Record<string, Error>;
  metadata: Record<string, RuntimePanelMetadata>;
};

export async function fetchPanelRuntimeData(
  panels: PanelModule[],
  options: PanelRuntimeFetchOptions,
): Promise<PanelRuntimeFetchResult> {
  const entries = panels.filter((panel) => typeof panel.fetchData === 'function');
  const data: PanelRuntimeData = {};
  const errors: Record<string, Error> = {};
  const metadata: Record<string, RuntimePanelMetadata> = {};
  const maxBatchSize = Math.max(1, options.maxBatchSize || 12);

  const recordData = (panelId: string, value: unknown, panelMetadata?: RuntimePanelMetadata) => {
    data[panelId] = value;
    if (panelMetadata) metadata[panelId] = panelMetadata;
    options.onPanelData?.(panelId, value, panelMetadata);
  };
  const recordError = (panelId: string, error: unknown) => {
    const normalized = error instanceof Error ? error : new Error(String(error || 'Panel refresh failed.'));
    errors[panelId] = normalized;
    options.onPanelError?.(panelId, normalized);
  };
  const fetchIndividually = async (individualEntries: PanelModule[]) => {
    await Promise.all(individualEntries.map(async (panel) => {
      try {
        const value = await panel.fetchData!({ signal: options.signal, reason: options.reason });
        if (value !== undefined) recordData(panel.id, value);
        else recordError(panel.id, new Error(`Panel ${panel.id} returned no data.`));
      } catch (error) {
        recordError(panel.id, error);
      } finally {
        options.onPanelSettled?.(panel.id);
      }
    }));
  };

  for (let offset = 0; offset < entries.length; offset += maxBatchSize) {
    const batch = entries.slice(offset, offset + maxBatchSize);
    if (batch.length <= 1) {
      await fetchIndividually(batch);
      continue;
    }
    try {
      const ids = batch.map((panel) => panel.id);
      const payload = await fetchRuntimePanels(ids, PANEL_RUNTIME_LIMITS, options.signal);
      const values = payload.panels || {};
      const batchErrors = payload.errors || {};
      const batchMetadata = payload.metadata || {};
      batch.forEach((panel) => {
        const value = values[panel.id];
        if (value !== undefined) {
          recordData(panel.id, value, batchMetadata[panel.id]);
        } else {
          recordError(panel.id, new Error(batchErrors[panel.id] || `Batch response omitted panel ${panel.id}.`));
        }
        options.onPanelSettled?.(panel.id);
      });
    } catch (error) {
      if (options.signal.aborted) {
        batch.forEach((panel) => {
          recordError(panel.id, error);
          options.onPanelSettled?.(panel.id);
        });
        continue;
      }
      // Fall back to individual panel requests if the batch route is unavailable.
      await fetchIndividually(batch);
    }
  }
  return { data, errors, metadata };
}

export function mergeRuntimeData(current: PanelRuntimeData, patch: PanelRuntimeData): PanelRuntimeData {
  if (!Object.keys(patch).length) return current;
  const next = { ...current };
  for (const [panelId, value] of Object.entries(patch)) {
    const previous = current[panelId];
    if (hasItems(previous) && isEmptyWarming(value)) {
      continue;
    }
    next[panelId] = value;
  }
  return next;
}

function hasItems(value: unknown): boolean {
  return Boolean(
    value &&
    typeof value === 'object' &&
    Array.isArray((value as { items?: unknown[] }).items) &&
    ((value as { items?: unknown[] }).items?.length || 0) > 0,
  );
}

function isEmptyWarming(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false;
  const payload = value as { items?: unknown[]; status?: unknown };
  return (!Array.isArray(payload.items) || payload.items.length === 0) && String(payload.status || '').toLowerCase() === 'warming';
}
