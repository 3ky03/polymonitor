const DEFAULT_PROTOCOL_VERSION = '2025-06-18';

function normalizedBaseUrl(value) {
  const fallback = typeof window === 'undefined'
    ? 'https://polymonitor.club/wm-api'
    : `${window.location.origin}/wm-api`;
  return String(value || fallback).replace(/\/+$/, '');
}

function positiveInteger(value, name) {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 1) {
    throw new TypeError(`${name} must be a positive integer.`);
  }
  return parsed;
}

export class PolyMonitorClient {
  constructor(options = {}) {
    this.baseUrl = normalizedBaseUrl(options.baseUrl);
    this.apiKey = String(options.apiKey || '').trim();
    this.fetch = options.fetch || globalThis.fetch;
    if (typeof this.fetch !== 'function') throw new TypeError('A Fetch-compatible function is required.');
  }

  async request(path, init = {}) {
    const headers = new Headers(init.headers || {});
    headers.set('Accept', 'application/json');
    const response = await this.fetch(`${this.baseUrl}${path}`, { ...init, headers });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const error = new Error(payload?.error || payload?.message || `PolyMonitor request failed with HTTP ${response.status}.`);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  searchMarkets(options = {}) {
    const query = new URLSearchParams();
    if (options.q) query.set('q', String(options.q));
    if (options.status) query.set('status', String(options.status));
    if (options.page != null) query.set('page', String(positiveInteger(options.page, 'page')));
    if (options.pageSize != null) query.set('pageSize', String(positiveInteger(options.pageSize, 'pageSize')));
    return this.request(`/markets${query.size ? `?${query}` : ''}`);
  }

  getMarket(marketId) {
    return this.request(`/markets/${positiveInteger(marketId, 'marketId')}`);
  }

  getMarketWorkspace(marketId) {
    return this.request(`/markets/${positiveInteger(marketId, 'marketId')}/workspace`);
  }

  getOracleLifecycle(marketId) {
    return this.request(`/markets/${positiveInteger(marketId, 'marketId')}/oracle`);
  }

  getDataQuality() {
    return this.request('/data-quality/markets');
  }

  getPublicBriefing(publicId) {
    const normalized = String(publicId || '').trim();
    if (!/^[A-Za-z0-9_-]{32}$/.test(normalized)) throw new TypeError('publicId must be a 32-character capability identifier.');
    return this.request(`/briefings/${encodeURIComponent(normalized)}`);
  }

  getRuntimePanels(ids = []) {
    const query = new URLSearchParams();
    if (ids.length) query.set('ids', ids.join(','));
    return this.request(`/v1/runtime/panels${query.size ? `?${query}` : ''}`);
  }

  callMcpTool(name, args = {}) {
    if (!this.apiKey) throw new TypeError('callMcpTool requires an mcp:read API key.');
    return this.request('/mcp', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${this.apiKey}`,
        'Content-Type': 'application/json',
        'MCP-Protocol-Version': DEFAULT_PROTOCOL_VERSION,
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: globalThis.crypto?.randomUUID?.() || String(Date.now()),
        method: 'tools/call',
        params: { name: String(name), arguments: args },
      }),
    });
  }
}

export const MCP_PROTOCOL_VERSION = DEFAULT_PROTOCOL_VERSION;
