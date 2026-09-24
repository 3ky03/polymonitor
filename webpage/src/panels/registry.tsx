import { PANEL_MODULES } from './modules';
import type { PanelModule, RegistryEntry } from './types';
import { getPanelRefreshPolicy } from './types';

export type { PanelModule, RegistryEntry } from './types';
export { PANEL_MODULES } from './modules';

const NORMALIZED_PANEL_MODULES = PANEL_MODULES.map((panel): PanelModule => {
  const refreshPolicy = getPanelRefreshPolicy(panel);
  return {
    ...panel,
    workspaces: panel.workspaces || ['world'],
    dataSources: panel.dataSources || (panel.fetchData ? [{ id: 'runtime-panels', transport: 'batch' }] : []),
    permissions: panel.permissions || [],
    maxBatchSize: panel.maxBatchSize || 12,
    refreshPolicy,
  };
});

export const PANEL_LIBRARY = NORMALIZED_PANEL_MODULES.map(({
  render,
  fetchData,
  refresh,
  refreshPolicy,
  dataSources,
  permissions,
  maxBatchSize,
  ...definition
}) => definition);

function assertUniquePanelIds(panels: PanelModule[]) {
  const seen = new Set<string>();
  for (const panel of panels) {
    if (seen.has(panel.id)) {
      throw new Error(`Duplicate panel module id: ${panel.id}`);
    }
    seen.add(panel.id);
    if (panel.fetchData && !getPanelRefreshPolicy(panel)?.tier) {
      throw new Error(`Runtime panel ${panel.id} must declare refresh.tier`);
    }
  }
}

assertUniquePanelIds(NORMALIZED_PANEL_MODULES);

export const DEFAULT_PANEL_IDS = NORMALIZED_PANEL_MODULES
  .filter((panel) => panel.defaultEnabled !== false)
  .map((panel) => panel.id);

export const RUNTIME_PANEL_MODULES = NORMALIZED_PANEL_MODULES.filter((panel) => typeof panel.fetchData === 'function');

export const PANEL_REGISTRY: Record<string, RegistryEntry> = Object.fromEntries(
  NORMALIZED_PANEL_MODULES.map((panel) => [panel.id, panel]),
);
