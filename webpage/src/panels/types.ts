import type { VNode } from 'preact';
import type { PanelDefinition, PanelRenderContext } from '@/types';

export type PanelRuntimeData = Record<string, unknown>;
export type PanelWorkspace = 'world' | 'worldcup' | 'quant';
export type PanelRuntimePhase = 'idle' | 'loading' | 'ready' | 'stale' | 'degraded' | 'error' | 'suspended';

export type PanelRuntimeContext = PanelRenderContext & {
  runtimeData: PanelRuntimeData;
};

export type PanelRenderer = (ctx: PanelRuntimeContext) => VNode;

export type RegistryEntry = PanelDefinition & {
  render: PanelRenderer;
  defaultEnabled?: boolean;
  refresh?: PanelRefreshConfig;
  fetchData?: PanelFetchData;
};

export type PanelEntryFragment = {
  render: PanelRenderer;
  size?: PanelDefinition['size'];
};

export type PanelRenderMap = Record<string, PanelEntryFragment>;

export type PanelRefreshTier = 'bootstrap' | 'fast' | 'slow' | 'manual';

export type PanelRefreshConfig = {
  tier: PanelRefreshTier;
  intervalMs?: number;
  staleAfterMs?: number;
  retry?: {
    attempts?: number;
    baseDelayMs?: number;
    maxDelayMs?: number;
  };
};

export type PanelDataSource = {
  id: string;
  transport: 'batch' | 'single' | 'local';
  limit?: number;
};

export type PanelFetchContext = {
  signal: AbortSignal;
  reason: 'bootstrap' | 'refresh' | 'interval' | 'retry' | 'manual';
};

export type PanelFetchData = (context?: PanelFetchContext) => Promise<unknown>;

export type PanelRuntimeStatus = {
  phase: PanelRuntimePhase;
  updatedAt: number | null;
  lastAttemptAt: number | null;
  failureCount: number;
  error: string | null;
  cacheMode?: string | null;
  freshness?: string | null;
  ageSeconds?: number | null;
};

export type PanelModule = PanelDefinition & {
  defaultEnabled?: boolean;
  workspaces?: PanelWorkspace[];
  dataSources?: PanelDataSource[];
  permissions?: string[];
  maxBatchSize?: number;
  refreshPolicy?: PanelRefreshConfig;
  /** @deprecated Use refreshPolicy. Retained while panel modules migrate. */
  refresh?: PanelRefreshConfig;
  fetchData?: PanelFetchData;
  render: PanelRenderer;
};

export function getPanelRefreshPolicy(panel: PanelModule): PanelRefreshConfig | undefined {
  return panel.refreshPolicy || panel.refresh;
}
