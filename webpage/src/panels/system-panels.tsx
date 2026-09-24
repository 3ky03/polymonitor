import { Panel } from '@/components/Panel';
import type { PanelRenderMap } from './types';
import { formatDate, shortHash } from './shared/formatters';
import { summaryRows } from './shared/renderers';

export const systemPanelRenderers: PanelRenderMap = {
  'live-api-status': {
    render: (ctx) => (
      <Panel title="LIVE API STATUS" badge={ctx.health?.apiStatus || 'OK'} status="live">
        {summaryRows([
          { label: 'API', value: String(ctx.health?.apiStatus || 'ok').toUpperCase() },
          { label: 'REDIS', value: ctx.health?.redis ? 'ONLINE' : 'OFF' },
          { label: 'LOB', value: ctx.bundle?.lob ? 'LIVE' : (ctx.health?.lobRuntime?.status || 'READY') },
          { label: 'CONTENT', value: ctx.bundle?.content?.sourceMode || ctx.health?.contentSync?.status || 'RUNTIME' },
        ])}
      </Panel>
    ),
  },
  'system-health': {
    render: (ctx) => (
      <Panel title="SYSTEM HEALTH" badge={ctx.health?.redis ? 'REDIS' : 'READY'} status="live">
        {summaryRows([
          { label: 'DB', value: shortHash(ctx.health?.database || '--', 16, 0) },
          { label: 'MARKET', value: formatDate(ctx.health?.marketSync?.updatedAt || null) },
          { label: 'TRADE', value: formatDate(ctx.health?.tradeSync?.updatedAt || null) },
          { label: 'ORACLE', value: formatDate(ctx.health?.oracleSync?.updatedAt || null) },
          { label: 'PRICE', value: formatDate(ctx.health?.priceSync?.updatedAt || null) },
          { label: 'CONTENT', value: ctx.health?.contentSync?.status || '--' },
        ])}
      </Panel>
    ),
  },
};

