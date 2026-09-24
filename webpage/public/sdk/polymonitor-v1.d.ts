export type JsonObject = Record<string, unknown>;

export type PolyMonitorClientOptions = {
  baseUrl?: string;
  apiKey?: string;
  fetch?: typeof globalThis.fetch;
};

export type MarketSearchOptions = {
  q?: string;
  status?: string;
  page?: number;
  pageSize?: number;
};

export declare class PolyMonitorClient {
  readonly baseUrl: string;
  readonly apiKey: string;
  constructor(options?: PolyMonitorClientOptions);
  request(path: string, init?: RequestInit): Promise<JsonObject>;
  searchMarkets(options?: MarketSearchOptions): Promise<JsonObject>;
  getMarket(marketId: number): Promise<JsonObject>;
  getMarketWorkspace(marketId: number): Promise<JsonObject>;
  getOracleLifecycle(marketId: number): Promise<JsonObject>;
  getDataQuality(): Promise<JsonObject>;
  getPublicBriefing(publicId: string): Promise<JsonObject>;
  getRuntimePanels(ids?: string[]): Promise<JsonObject>;
  callMcpTool(name: string, args?: JsonObject): Promise<JsonObject>;
}

export declare const MCP_PROTOCOL_VERSION: '2025-06-18';
