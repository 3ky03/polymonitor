import { useMemo, useState } from 'preact/hooks';
import type { QuantBacktestBenchmarkArtifact, QuantBacktestBenchmarkRow, QuantBacktestBenchmarkRun, QuantBacktestRun, QuantBacktestUniverse } from '@/types';
import type {
  BacktestResult,
  BatchBacktestRow,
  PerformanceSortKey,
  SortDirection,
  StrategyParameters,
  TesterTab,
  TradeFilter,
} from '../types';
import { EquityDrawdownChart } from './EquityDrawdownChart';
import { MetricCard } from './MetricCard';
import { PerformanceSummaryTab } from './PerformanceSummaryTab';
import { PropertiesTab } from './PropertiesTab';
import { TradesTableTab } from './TradesTableTab';

type ToolTab = 'screener' | 'editor' | 'tester' | 'replay' | 'trading';

const TOOL_TABS: Array<[ToolTab, string]> = [
  ['screener', 'Market Screener'],
  ['editor', 'Strategy Editor'],
  ['tester', 'Strategy Tester'],
  ['replay', 'Replay Trading'],
  ['trading', 'Trading Panel'],
];

const TESTER_TABS: Array<[TesterTab, string]> = [
  ['overview', 'Overview'],
  ['benchmark', 'Benchmark'],
  ['fillQuality', 'Fill Quality'],
  ['dataQuality', 'Data Quality'],
  ['regime', 'Regime'],
  ['predictionQuality', 'Prediction Quality'],
  ['parameters', 'Parameters'],
  ['performance', 'Performance'],
  ['orders', 'Orders'],
  ['trades', 'Trades'],
  ['ledger', 'Ledger'],
  ['equity', 'Equity Curve'],
  ['drawdown', 'Drawdown'],
  ['runs', 'Runs'],
  ['logs', 'Logs'],
  ['properties', 'Properties'],
];

const STRATEGY_PRESETS_KEY = 'polydata.quant.strategyPresets';

function formatPct(value: number, digits = 2) {
  if (!Number.isFinite(value)) return '--';
  return `${value.toFixed(digits)}%`;
}

function formatNumber(value: number, digits = 2) {
  if (!Number.isFinite(value)) return '--';
  return value.toLocaleString('en-US', { maximumFractionDigits: digits });
}

function formatParameterValue(value: unknown) {
  if (typeof value === 'number') return formatNumber(value, 4);
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (value === undefined || value === null || value === '') return '-';
  return String(value);
}

function statusForRisk(value: boolean) {
  return value ? 'ready' : 'review';
}

type SavedStrategyPreset = {
  name: string;
  parameters: StrategyParameters;
  savedAt: string;
};

type StrategyTesterPanelProps = {
  result: BacktestResult;
  testerTab: TesterTab;
  deepBacktest: boolean;
  selectedTradeId: string | null;
  performanceSearch: string;
  performanceSortKey: PerformanceSortKey;
  performanceSortDirection: SortDirection;
  tradeFilters: Set<TradeFilter>;
  filteredPerformanceRows: BacktestResult['performanceRows'];
  filteredTrades: BacktestResult['trades'];
  onTesterTabChange: (tab: TesterTab) => void;
  onDeepBacktestChange: () => void;
  onRefresh: () => void;
  onBatchBacktest: () => void;
  onSplitBacktest: () => void;
  onWalkForwardBacktest: () => void;
  onExport: (format: 'csv' | 'json') => void;
  onPerformanceSearchChange: (value: string) => void;
  onPerformanceSortChange: (key: PerformanceSortKey) => void;
  onTradeFilterToggle: (filter: TradeFilter) => void;
  onTradeSelect: (tradeId: string) => void;
  strategyParameters: StrategyParameters;
  onStrategyParametersChange: (parameters: StrategyParameters) => void;
  onStrategyAutoTune: () => void;
  marketTitle?: string;
  dataSource?: string;
  engine?: string;
  rowCount?: number;
  backtestStatus?: string;
  batchRows?: BatchBacktestRow[];
  batchStatus?: string;
  splitRows?: BatchBacktestRow[];
  splitStatus?: string;
  walkForwardRows?: BatchBacktestRow[];
  walkForwardStatus?: string;
  recentBacktestRuns?: QuantBacktestRun[];
  backtestRunsStatus?: string;
  onRunLoad?: (runId: number) => void;
  benchmarkRun?: QuantBacktestBenchmarkRun | null;
  benchmarkRows?: QuantBacktestBenchmarkRow[];
  benchmarkArtifacts?: QuantBacktestBenchmarkArtifact[];
  benchmarkUniverses?: QuantBacktestUniverse[];
  selectedBenchmarkUniverse?: string;
  selectedBenchmarkLimit?: number;
  benchmarkStatus?: string;
  onRunBenchmark?: () => void;
  onBenchmarkUniverseChange?: (universe: string) => void;
  onBenchmarkLimitChange?: (limit: number) => void;
};

function loadSavedPresets(): SavedStrategyPreset[] {
  if (typeof window === 'undefined') return [];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STRATEGY_PRESETS_KEY) || '[]');
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item) => item && typeof item.name === 'string' && item.parameters);
  } catch {
    return [];
  }
}

function persistSavedPresets(presets: SavedStrategyPreset[]) {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(STRATEGY_PRESETS_KEY, JSON.stringify(presets));
}

function hashText(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

function compactRunTime(value?: string | null) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function compactRows(value?: string | number | null) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '-';
  return numeric.toLocaleString('en-US');
}

function benchmarkSummaryValue(summary: Record<string, unknown> | null | undefined, key: string) {
  const direct = summary?.[key];
  if (direct !== undefined && direct !== null) return direct;
  const comparison = summary?.comparison;
  if (comparison && typeof comparison === 'object') {
    const nested = (comparison as Record<string, unknown>)[key];
    if (nested !== undefined && nested !== null) return nested;
  }
  const realistic = summary?.['accurate:realistic'];
  if (realistic && typeof realistic === 'object') {
    const nested = (realistic as Record<string, unknown>)[key];
    if (nested !== undefined && nested !== null) return nested;
  }
  return undefined;
}

function benchmarkSummaryText(summary: Record<string, unknown> | null | undefined, key: string, fallback = '-') {
  const value = benchmarkSummaryValue(summary, key);
  if (value === undefined || value === null || value === '') return fallback;
  if (typeof value === 'number') return Number.isFinite(value) ? formatNumber(value, 4) : fallback;
  return String(value);
}

function artifactPayload(artifacts: QuantBacktestBenchmarkArtifact[] | undefined, key: string) {
  return artifacts?.find((artifact) => artifact.artifactKey === key)?.payload;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function asArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === 'object') as Array<Record<string, unknown>> : [];
}

function recordText(record: Record<string, unknown>, key: string, fallback = '-') {
  const value = record[key];
  if (value === undefined || value === null || value === '') return fallback;
  if (typeof value === 'number') return formatNumber(value, 4);
  return String(value);
}

function compactMoney(value?: string | number | null) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '-';
  return `${numeric >= 0 ? '+' : ''}${numeric.toFixed(2)}`;
}

function numericFromMetric(value: string) {
  const numeric = Number(String(value).replace(/[^0-9.+-]/g, ''));
  return Number.isFinite(numeric) ? numeric : 0;
}

function runNumber(value: unknown, fallback: number) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function strategyParametersFromRun(run: QuantBacktestRun, fallback: StrategyParameters): StrategyParameters {
  return {
    entryThreshold: runNumber(run.entryThreshold, fallback.entryThreshold),
    exitThreshold: runNumber(run.exitThreshold, fallback.exitThreshold),
    stopLoss: runNumber(run.stopLoss, fallback.stopLoss),
    takeProfit: runNumber(run.takeProfit, fallback.takeProfit),
    maxHoldingBars: Math.round(runNumber(run.maxHoldingBars, fallback.maxHoldingBars)),
    initialCapital: runNumber(run.initialCapital, fallback.initialCapital),
    positionSize: runNumber(run.positionSize, fallback.positionSize),
    feeBps: runNumber(run.feeBps, fallback.feeBps),
    slippageBps: runNumber(run.slippageBps, fallback.slippageBps),
    liquidityCapPct: runNumber(run.liquidityCapPct, fallback.liquidityCapPct),
    maxPositionNotional: runNumber(run.maxPositionNotional, fallback.maxPositionNotional),
    minFillPct: runNumber(run.minFillPct, fallback.minFillPct),
    executionProfile: fallback.executionProfile,
    orderRole: fallback.orderRole,
    executionPriceMode: run.executionPriceMode === 'ORDERFILLED_LIMIT_REPLAY' || run.executionPriceMode === 'ORDERFILLED' || run.executionPriceMode === 'DEPTH' || run.executionPriceMode === 'LEGACY' ? run.executionPriceMode : fallback.executionPriceMode,
    finalValuationMode: run.finalValuationMode === 'FORCE_CLOSE' ? 'FORCE_CLOSE' : fallback.finalValuationMode,
    buyLimitPrice: run.buyLimitPrice === null || run.buyLimitPrice === undefined ? fallback.buyLimitPrice : runNumber(run.buyLimitPrice, fallback.buyLimitPrice ?? fallback.entryThreshold),
    sellLimitPrice: run.sellLimitPrice === null || run.sellLimitPrice === undefined ? fallback.sellLimitPrice : runNumber(run.sellLimitPrice, fallback.sellLimitPrice ?? 0.99),
    settlementValue: run.settlementValue === null || run.settlementValue === undefined ? fallback.settlementValue : runNumber(run.settlementValue, fallback.settlementValue ?? 0),
    latencySeconds: runNumber(run.latencySeconds, fallback.latencySeconds),
    latencyBlocks: fallback.latencyBlocks,
    maxBookStalenessSeconds: runNumber(run.maxBookStalenessSeconds, fallback.maxBookStalenessSeconds),
    adverseSlippageCents: fallback.adverseSlippageCents,
    fillProbabilityHaircutPct: fallback.fillProbabilityHaircutPct,
    allowPartialFill: typeof run.allowPartialFill === 'boolean' ? run.allowPartialFill : fallback.allowPartialFill,
    minFillSize: runNumber(run.minFillSize, fallback.minFillSize),
    rejectOnStaleBook: typeof run.rejectOnStaleBook === 'boolean' ? run.rejectOnStaleBook : fallback.rejectOnStaleBook,
  };
}

function runParameterSummary(run: QuantBacktestRun, fallback: StrategyParameters) {
  const params = strategyParametersFromRun(run, fallback);
  const spread = params.entryThreshold - params.exitThreshold;
  const costBps = (params.feeBps * 2) + (params.slippageBps * 2);
  const riskPct = params.initialCapital > 0 ? (params.positionSize / params.initialCapital) * 100 : 0;
  const meta = run.meta || {};
  const fingerprint = run.parameterFingerprint || (typeof meta.parameter_fingerprint === 'string' ? meta.parameter_fingerprint : typeof meta.parameterFingerprint === 'string' ? meta.parameterFingerprint : '');
  return {
    params,
    spread,
    costBps,
    riskPct,
    fingerprint,
    label: `${params.executionPriceMode === 'ORDERFILLED_LIMIT_REPLAY' ? 'limit replay' : `${params.entryThreshold.toFixed(3)}→${params.exitThreshold.toFixed(3)}`} · ${costBps.toFixed(1)}bps · ${riskPct.toFixed(1)}% risk`,
  };
}

function optionalFixed(value: number | undefined, digits = 6) {
  return Number.isFinite(value) ? Number(Number(value).toFixed(digits)) : undefined;
}

function canonicalStrategyParameters(parameters: StrategyParameters) {
  return {
    entryThreshold: Number(parameters.entryThreshold.toFixed(6)),
    exitThreshold: Number(parameters.exitThreshold.toFixed(6)),
    stopLoss: Number(parameters.stopLoss.toFixed(6)),
    takeProfit: Number(parameters.takeProfit.toFixed(6)),
    maxHoldingBars: Math.round(parameters.maxHoldingBars),
    initialCapital: Number(parameters.initialCapital.toFixed(6)),
    positionSize: Number(parameters.positionSize.toFixed(6)),
    feeBps: Number(parameters.feeBps.toFixed(6)),
    slippageBps: Number(parameters.slippageBps.toFixed(6)),
    liquidityCapPct: Number(parameters.liquidityCapPct.toFixed(6)),
    maxPositionNotional: Number(parameters.maxPositionNotional.toFixed(6)),
    minFillPct: Number(parameters.minFillPct.toFixed(6)),
    executionProfile: parameters.executionProfile,
    orderRole: parameters.orderRole,
    executionPriceMode: parameters.executionPriceMode,
    finalValuationMode: parameters.finalValuationMode,
    buyLimitPrice: optionalFixed(parameters.buyLimitPrice),
    sellLimitPrice: optionalFixed(parameters.sellLimitPrice),
    settlementValue: optionalFixed(parameters.settlementValue),
    latencySeconds: Number(parameters.latencySeconds.toFixed(6)),
    latencyBlocks: Math.round(parameters.latencyBlocks),
    maxBookStalenessSeconds: Number(parameters.maxBookStalenessSeconds.toFixed(6)),
    adverseSlippageCents: Number(parameters.adverseSlippageCents.toFixed(6)),
    fillProbabilityHaircutPct: Number(parameters.fillProbabilityHaircutPct.toFixed(6)),
    allowPartialFill: parameters.allowPartialFill,
    minFillSize: Number(parameters.minFillSize.toFixed(6)),
    rejectOnStaleBook: parameters.rejectOnStaleBook,
  };
}

function strategyParameterDiffs(current: StrategyParameters, reference: StrategyParameters) {
  const labels: Record<keyof StrategyParameters, string> = {
    entryThreshold: 'entry',
    exitThreshold: 'exit',
    stopLoss: 'stop',
    takeProfit: 'take',
    maxHoldingBars: 'hold',
    initialCapital: 'capital',
    positionSize: 'size',
    feeBps: 'fee',
    slippageBps: 'slip',
    liquidityCapPct: 'liq',
    maxPositionNotional: 'max pos',
    minFillPct: 'min fill',
    executionProfile: 'profile',
    orderRole: 'role',
    executionPriceMode: 'mode',
    finalValuationMode: 'valuation',
    buyLimitPrice: 'buy limit',
    sellLimitPrice: 'sell limit',
    settlementValue: 'settlement',
    latencySeconds: 'latency',
    latencyBlocks: 'lat blocks',
    maxBookStalenessSeconds: 'stale',
    adverseSlippageCents: 'adverse slip',
    fillProbabilityHaircutPct: 'fill haircut',
    allowPartialFill: 'partial',
    minFillSize: 'min size',
    rejectOnStaleBook: 'stale gate',
  };
  return (Object.keys(labels) as Array<keyof StrategyParameters>).map((key) => {
    const left = key === 'maxHoldingBars' ? Math.round(current[key]) : current[key];
    const right = key === 'maxHoldingBars' ? Math.round(reference[key]) : reference[key];
    const diff = typeof left === 'number' && typeof right === 'number'
      ? Math.abs(Number(left) - Number(right))
      : (String(left ?? '') === String(right ?? '') ? 0 : 1);
    return {
      key,
      label: labels[key],
      current: left,
      reference: right,
      changed: diff > (key === 'maxHoldingBars' ? 0 : 0.000001),
    };
  }).filter((row) => row.changed);
}

async function copyText(value: string) {
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  if (typeof document === 'undefined') return;
  const textarea = document.createElement('textarea');
  textarea.value = value;
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  document.execCommand('copy');
  document.body.removeChild(textarea);
}

export function StrategyTesterPanel({
  result,
  testerTab,
  deepBacktest,
  selectedTradeId,
  performanceSearch,
  performanceSortKey,
  performanceSortDirection,
  tradeFilters,
  filteredPerformanceRows,
  filteredTrades,
  onTesterTabChange,
  onDeepBacktestChange,
  onRefresh,
  onBatchBacktest,
  onSplitBacktest,
  onWalkForwardBacktest,
  onExport,
  onPerformanceSearchChange,
  onPerformanceSortChange,
  onTradeFilterToggle,
  onTradeSelect,
  strategyParameters,
  onStrategyParametersChange,
  onStrategyAutoTune,
  marketTitle = 'No market selected',
  dataSource = '-',
  engine = '-',
  rowCount = 0,
  backtestStatus = 'idle',
  batchRows = [],
  batchStatus = 'idle',
  splitRows = [],
  splitStatus = 'idle',
  walkForwardRows = [],
  walkForwardStatus = 'idle',
  recentBacktestRuns = [],
  backtestRunsStatus = 'idle',
  onRunLoad,
  benchmarkRun = null,
  benchmarkRows = [],
  benchmarkArtifacts = [],
  benchmarkUniverses = [],
  selectedBenchmarkUniverse = 'nba_2024_25_moneyline',
  selectedBenchmarkLimit = 50,
  benchmarkStatus = 'idle',
  onRunBenchmark,
  onBenchmarkUniverseChange,
  onBenchmarkLimitChange,
}: StrategyTesterPanelProps) {
  const [toolTab, setToolTab] = useState<ToolTab>('tester');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [parameterPreset, setParameterPreset] = useState('Custom live config');
  const [savedPresets, setSavedPresets] = useState<SavedStrategyPreset[]>(loadSavedPresets);
  const [presetDraftName, setPresetDraftName] = useState('Momentum live');
  const [copyNotice, setCopyNotice] = useState('');
  const hasCompletedRun = result.runId > 0 && result.metrics.length > 0;
  const isBenchmarkRunning = benchmarkStatus === 'running' || benchmarkStatus === 'loading';
  const summaryRows = useMemo(() => result.metrics.slice(0, 6), [result.metrics]);
  const latestTrade = result.trades[result.trades.length - 1];
  const latestOrder = result.orders[result.orders.length - 1];
  const latestLedger = result.ledger[result.ledger.length - 1];
  const selectedTrade = result.trades.find((trade) => trade.id === selectedTradeId) || latestTrade;
  const propertyValue = (label: string) => {
    const target = label.toLowerCase();
    return result.propertyGroups
      .flatMap((group) => group.rows)
      .find((row) => row.label.toLowerCase() === target)?.value || '-';
  };
  const runPayload = useMemo(() => ({
    runId: result.runId || null,
    market: marketTitle,
    source: dataSource,
    engine,
    rows: rowCount,
    status: backtestStatus,
    generatedAt: result.generatedAt,
    strategy: strategyParameters,
    lastRun: {
      marketSlug: propertyValue('market slug'),
      tokenSide: propertyValue('token side'),
      outcome: propertyValue('outcome'),
      fromBlock: propertyValue('from block'),
      toBlock: propertyValue('to block'),
      rowsProcessed: propertyValue('rows processed'),
    },
  }), [backtestStatus, dataSource, engine, marketTitle, result.generatedAt, result.runId, rowCount, strategyParameters]);
  const runPayloadText = useMemo(() => JSON.stringify(runPayload, null, 2), [runPayload]);
  const localRunFingerprint = useMemo(() => hashText(runPayloadText), [runPayloadText]);
  const serverRunFingerprint = propertyValue('fingerprint');
  const runFingerprint = serverRunFingerprint && serverRunFingerprint !== '-' ? serverRunFingerprint : localRunFingerprint;
  const liveParameterFingerprint = useMemo(
    () => hashText(JSON.stringify(canonicalStrategyParameters(strategyParameters))),
    [strategyParameters],
  );
  const parameterDiagnostics = useMemo(() => {
    const spread = strategyParameters.entryThreshold - strategyParameters.exitThreshold;
    const roundTripCostBps = (strategyParameters.feeBps * 2) + (strategyParameters.slippageBps * 2);
    const roundTripCost = roundTripCostBps / 10000;
    const breakEvenMove = Math.max(0, roundTripCost);
    const exposurePct = strategyParameters.initialCapital > 0
      ? (strategyParameters.positionSize / strategyParameters.initialCapital) * 100
      : 0;
    const capacity = (strategyParameters.positionSize * strategyParameters.liquidityCapPct) / 100;
    const warnings = [
      spread <= 0 ? 'Entry threshold must stay above exit threshold.' : '',
      spread <= breakEvenMove ? 'Entry/exit spread is tighter than estimated round-trip cost.' : '',
      exposurePct > 20 ? 'Position size uses more than 20% of initial capital.' : '',
      strategyParameters.maxPositionNotional > 0 && strategyParameters.maxPositionNotional < strategyParameters.positionSize ? 'Max position will cap every requested fill.' : '',
      strategyParameters.minFillPct > 0 && strategyParameters.liquidityCapPct < strategyParameters.minFillPct ? 'Minimum fill is above the liquidity cap assumption.' : '',
      strategyParameters.liquidityCapPct < 5 ? 'Liquidity cap is very low; fills may be sparse.' : '',
      rowCount < 100 ? 'Loaded rows are thin for a stable backtest.' : '',
    ].filter(Boolean);
    const health = warnings.length ? 'review' : 'ready';
    return {
      spread,
      roundTripCostBps,
      breakEvenMove,
      exposurePct,
      capacity,
      health,
      warnings,
    };
  }, [rowCount, strategyParameters]);
  const executionAssumptionRows = useMemo(() => {
    const base = strategyParameters;
    const scenarios = [
      { key: 'current', label: 'Current', profile: base.executionProfile, role: base.orderRole, feeBps: base.feeBps, slippageBps: base.slippageBps, liquidityCapPct: base.liquidityCapPct, minFillPct: base.minFillPct, latencyBlocks: base.latencyBlocks, adverseSlippageCents: base.adverseSlippageCents, fillProbabilityHaircutPct: base.fillProbabilityHaircutPct },
      { key: 'live', label: 'Live CLOB', profile: 'realistic' as const, role: 'taker' as const, feeBps: Math.max(base.feeBps, 0), slippageBps: Math.max(base.slippageBps, 2), liquidityCapPct: Math.min(base.liquidityCapPct, 25), minFillPct: Math.max(base.minFillPct, 20), latencyBlocks: Math.max(base.latencyBlocks, 1), adverseSlippageCents: Math.max(base.adverseSlippageCents, 0.005), fillProbabilityHaircutPct: Math.max(base.fillProbabilityHaircutPct, 20) },
      { key: 'stress', label: 'Stress', profile: 'stress' as const, role: 'taker' as const, feeBps: Math.max(base.feeBps, 5), slippageBps: Math.max(base.slippageBps, 10), liquidityCapPct: Math.min(base.liquidityCapPct, 10), minFillPct: Math.max(base.minFillPct, 50), latencyBlocks: Math.max(base.latencyBlocks, 3), adverseSlippageCents: Math.max(base.adverseSlippageCents, 0.02), fillProbabilityHaircutPct: Math.max(base.fillProbabilityHaircutPct, 45) },
      { key: 'zero', label: 'Zero cost', profile: 'optimistic' as const, role: 'maker' as const, feeBps: 0, slippageBps: 0, liquidityCapPct: 100, minFillPct: 0, latencyBlocks: 0, adverseSlippageCents: 0, fillProbabilityHaircutPct: 0 },
    ];
    return scenarios.map((scenario) => {
      const roundTripBps = (scenario.feeBps * 2) + (scenario.slippageBps * 2);
      const costUsdc = strategyParameters.positionSize * (roundTripBps / 10000);
      const capacityUsdc = strategyParameters.positionSize * (scenario.liquidityCapPct / 100);
      return {
        ...scenario,
        capacityUsdc,
        costUsdc,
        roundTripBps,
      };
    });
  }, [strategyParameters]);
  const copied = (message: string) => {
    setCopyNotice(message);
    window.setTimeout(() => setCopyNotice(''), 1800);
  };
  const copyRunPayload = async () => {
    await copyText(runPayloadText);
    copied('Run payload copied');
  };
  const copyStrategyParameters = async () => {
    await copyText(JSON.stringify(strategyParameters, null, 2));
    copied('Strategy parameters copied');
  };
  const savePreset = () => {
    const name = presetDraftName.trim() || `Preset ${savedPresets.length + 1}`;
    const nextPreset = { name, parameters: strategyParameters, savedAt: new Date().toISOString() };
    const nextPresets = [nextPreset, ...savedPresets.filter((preset) => preset.name !== name)].slice(0, 12);
    setSavedPresets(nextPresets);
    persistSavedPresets(nextPresets);
    setParameterPreset(`saved:${name}`);
    copied(`Saved ${name}`);
  };
  const deletePreset = (name: string) => {
    const nextPresets = savedPresets.filter((preset) => preset.name !== name);
    setSavedPresets(nextPresets);
    persistSavedPresets(nextPresets);
    if (parameterPreset === `saved:${name}`) setParameterPreset('Custom live config');
  };
  const updateParameter = (key: keyof StrategyParameters, value: string) => {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      if (key === 'settlementValue' && value.trim() === '') {
        onStrategyParametersChange({ ...strategyParameters, settlementValue: undefined });
      }
      return;
    }
    onStrategyParametersChange({
      ...strategyParameters,
      [key]: key === 'maxHoldingBars' || key === 'latencyBlocks' ? Math.round(numeric) : numeric,
    });
  };
  const applyExecutionAssumption = (key: string) => {
    const scenario = executionAssumptionRows.find((row) => row.key === key);
    if (!scenario) return;
    onStrategyParametersChange({
      ...strategyParameters,
      feeBps: scenario.feeBps,
      slippageBps: scenario.slippageBps,
      liquidityCapPct: scenario.liquidityCapPct,
      minFillPct: scenario.minFillPct,
      executionProfile: scenario.profile,
      orderRole: scenario.role,
      latencyBlocks: scenario.latencyBlocks,
      adverseSlippageCents: scenario.adverseSlippageCents,
      fillProbabilityHaircutPct: scenario.fillProbabilityHaircutPct,
    });
    copied(`Applied ${scenario.label} execution`);
  };
  const applyRunParameters = (run: QuantBacktestRun) => {
    const next = strategyParametersFromRun(run, strategyParameters);
    onStrategyParametersChange(next);
    setParameterPreset('Custom live config');
    setPresetDraftName(`Run ${run.runId} replay`);
    copied(`Applied params from #${run.runId}`);
    onTesterTabChange('parameters');
  };
  const applyPreset = (preset: string) => {
    setParameterPreset(preset);
    if (preset.startsWith('saved:')) {
      const name = preset.slice('saved:'.length);
      const saved = savedPresets.find((item) => item.name === name);
      if (saved) {
        onStrategyParametersChange(saved.parameters);
        setPresetDraftName(saved.name);
      }
      return;
    }
    if (preset === 'Conservative') {
      onStrategyParametersChange({
        ...strategyParameters,
        entryThreshold: 0.62,
        exitThreshold: 0.48,
        stopLoss: 0.055,
        takeProfit: 0.12,
        maxHoldingBars: 72,
        initialCapital: strategyParameters.initialCapital,
        positionSize: Math.max(1, Math.round(strategyParameters.positionSize * 0.75)),
        feeBps: strategyParameters.feeBps,
        slippageBps: Math.max(strategyParameters.slippageBps, 2),
        liquidityCapPct: Math.min(strategyParameters.liquidityCapPct, 25),
        maxPositionNotional: strategyParameters.maxPositionNotional,
        minFillPct: Math.max(strategyParameters.minFillPct, 20),
        executionProfile: 'conservative',
        orderRole: 'taker',
        executionPriceMode: 'ORDERFILLED_LIMIT_REPLAY',
        finalValuationMode: 'SETTLEMENT',
        buyLimitPrice: Math.min(strategyParameters.buyLimitPrice ?? 0.5, 0.48),
        sellLimitPrice: Math.max(strategyParameters.sellLimitPrice ?? 0.99, 0.99),
        latencyBlocks: Math.max(strategyParameters.latencyBlocks, 1),
        adverseSlippageCents: Math.max(strategyParameters.adverseSlippageCents, 0.01),
        fillProbabilityHaircutPct: Math.max(strategyParameters.fillProbabilityHaircutPct, 25),
      });
      return;
    }
    if (preset === 'Aggressive') {
      onStrategyParametersChange({
        ...strategyParameters,
        entryThreshold: 0.54,
        exitThreshold: 0.4,
        stopLoss: 0.11,
        takeProfit: 0.24,
        maxHoldingBars: 160,
        initialCapital: strategyParameters.initialCapital,
        positionSize: Math.max(1, Math.round(strategyParameters.positionSize * 1.25)),
        feeBps: strategyParameters.feeBps,
        slippageBps: strategyParameters.slippageBps,
        liquidityCapPct: Math.min(strategyParameters.liquidityCapPct, 60),
        maxPositionNotional: strategyParameters.maxPositionNotional,
        minFillPct: strategyParameters.minFillPct,
        executionProfile: 'realistic',
        orderRole: 'taker',
        executionPriceMode: 'ORDERFILLED_LIMIT_REPLAY',
        finalValuationMode: 'SETTLEMENT',
        buyLimitPrice: Math.max(strategyParameters.buyLimitPrice ?? 0.5, 0.54),
        sellLimitPrice: Math.min(strategyParameters.sellLimitPrice ?? 0.8, 0.8),
        latencyBlocks: strategyParameters.latencyBlocks,
        adverseSlippageCents: strategyParameters.adverseSlippageCents,
        fillProbabilityHaircutPct: strategyParameters.fillProbabilityHaircutPct,
      });
      return;
    }
    if (preset === 'Backend defaults') {
      onStrategyParametersChange({
        entryThreshold: 0.58,
        exitThreshold: 0.44,
        stopLoss: 0.075,
        takeProfit: 0.16,
        maxHoldingBars: 96,
        initialCapital: 100000,
        positionSize: 100,
        feeBps: 0,
        slippageBps: 0,
        liquidityCapPct: 100,
        maxPositionNotional: 0,
        minFillPct: 0,
        executionProfile: 'realistic',
        orderRole: 'taker',
        executionPriceMode: 'ORDERFILLED_LIMIT_REPLAY',
        finalValuationMode: 'SETTLEMENT',
        buyLimitPrice: 0.5,
        sellLimitPrice: 0.99,
        settlementValue: undefined,
        latencySeconds: 0,
        latencyBlocks: 0,
        maxBookStalenessSeconds: 900,
        adverseSlippageCents: 0.005,
        fillProbabilityHaircutPct: 20,
        allowPartialFill: true,
        minFillSize: 0,
        rejectOnStaleBook: true,
      });
    }
  };
  const runSnapshotRows = [
    ['entry threshold', propertyValue('entry threshold')],
    ['exit threshold', propertyValue('exit threshold')],
    ['stop loss', propertyValue('stop loss')],
    ['take profit', propertyValue('take profit')],
    ['max hold', propertyValue('max hold')],
    ['position size', propertyValue('position size')],
    ['initial capital', propertyValue('initial capital')],
    ['fee bps', propertyValue('fee bps')],
    ['slippage bps', propertyValue('slippage bps')],
    ['liquidity cap', propertyValue('liquidity cap')],
    ['max position', propertyValue('max position')],
    ['min fill', propertyValue('min fill')],
  ];
  const runProvenanceRows = [
    ['Run', result.runId ? `#${result.runId}` : '-'],
    ['Fingerprint', runFingerprint],
    ['Engine', engine],
    ['Source', dataSource],
    ['Rows', rowCount.toLocaleString('en-US')],
    ['Fallback', 'none'],
    ['Generated', compactRunTime(result.generatedAt)],
  ];
  const recentRunsForDisplay = useMemo(() => {
    const currentRunId = result.runId || 0;
    return recentBacktestRuns.slice(0, 10).map((run) => ({
      ...run,
      isCurrent: Boolean(currentRunId && run.runId === currentRunId),
    }));
  }, [recentBacktestRuns, result.runId]);
  const parameterDrift = useMemo(() => {
    const referenceRun = recentBacktestRuns.find((run) => result.runId && run.runId === result.runId)
      || recentBacktestRuns.find((run) => run.status === 'succeeded')
      || null;
    if (!referenceRun) {
      return {
        status: 'idle',
        referenceRun: null,
        referenceParams: null,
        diffs: [] as ReturnType<typeof strategyParameterDiffs>,
        title: 'No reference run',
        detail: 'Run the current parameters once to anchor reproducibility.',
      };
    }
    const referenceParams = strategyParametersFromRun(referenceRun, strategyParameters);
    const diffs = strategyParameterDiffs(strategyParameters, referenceParams);
    const sameContext = (!referenceRun.backtestEngine || referenceRun.backtestEngine === engine)
      && (!referenceRun.priceSource || referenceRun.priceSource === dataSource || dataSource.includes(referenceRun.priceSource));
    const status = diffs.length || !sameContext ? 'review' : 'ready';
    return {
      status,
      referenceRun,
      referenceParams,
      diffs,
      title: status === 'ready' ? 'Current parameters match the loaded run' : 'Current parameters differ from the reference run',
      detail: status === 'ready'
        ? `#${referenceRun.runId} · ${compactRunTime(referenceRun.createdAt)} · ${runParameterSummary(referenceRun, strategyParameters).label}`
        : `#${referenceRun.runId} · ${diffs.length.toLocaleString('en-US')} parameter changes${sameContext ? '' : ' · context changed'}`,
    };
  }, [dataSource, engine, recentBacktestRuns, result.runId, strategyParameters]);
  const batchLeaderboard = useMemo(() => (
    [...batchRows, ...splitRows, ...walkForwardRows]
      .filter((row) => row.status === 'succeeded' || row.status === 'failed' || row.runId)
      .sort((left, right) => numericFromMetric(right.netProfit) - numericFromMetric(left.netProfit))
      .slice(0, 8)
  ), [batchRows, splitRows, walkForwardRows]);
  const benchmarkSummaryRows = useMemo(() => {
    const summary = benchmarkRun?.summary || {};
    return [
      ['Markets', benchmarkRun?.marketCount ?? benchmarkSummaryValue(summary, 'market_count') ?? '-'],
      ['Fast raw rows', benchmarkSummaryText(summary, 'fast_raw_rows')],
      ['Accurate raw rows', benchmarkSummaryText(summary, 'accurate_raw_rows')],
      ['PnL diff', benchmarkSummaryText(summary, 'total_pnl_diff')],
      ['Status mismatch', benchmarkSummaryText(summary, 'status_mismatches')],
      ['Runtime', `${benchmarkSummaryText(summary, 'total_runtime_sec')}s`],
    ] as Array<[string, unknown]>;
  }, [benchmarkRun]);
  const benchmarkArtifactRows = useMemo(() => {
    const fillQuality = asRecord(artifactPayload(benchmarkArtifacts, 'fill_quality') || benchmarkRun?.summary?.fill_quality);
    const dataQuality = asRecord(artifactPayload(benchmarkArtifacts, 'data_quality') || benchmarkRun?.summary?.data_quality);
    const regimeBuckets = asRecord(artifactPayload(benchmarkArtifacts, 'regime_buckets'));
    const predictionQuality = asRecord(artifactPayload(benchmarkArtifacts, 'prediction_quality') || benchmarkRun?.summary?.prediction_quality);
    const profiles = asArray(artifactPayload(benchmarkArtifacts, 'profiles') || benchmarkRun?.summary?.profiles);
    return { fillQuality, dataQuality, regimeBuckets, predictionQuality, profiles };
  }, [benchmarkArtifacts, benchmarkRun]);
  const runControlRows = useMemo(() => ([
    {
      key: 'single',
      label: 'Single Outcome',
      status: hasCompletedRun ? backtestStatus || 'loaded' : backtestStatus || 'idle',
      detail: hasCompletedRun ? `#${result.runId} · ${result.orders.length.toLocaleString('en-US')} orders · ${result.trades.length.toLocaleString('en-US')} trades` : 'current selected outcome',
      rows: rowCount,
      actionLabel: hasCompletedRun ? 'Rerun' : 'Run',
      action: onRefresh,
      disabled: rowCount <= 0 || backtestStatus === 'running',
    },
    {
      key: 'split',
      label: 'Train/Test Split',
      status: splitStatus,
      detail: splitRows.length ? `${splitRows.length.toLocaleString('en-US')} segments · ${splitRows.filter((row) => row.status === 'succeeded').length.toLocaleString('en-US')} complete` : '70/30 block split',
      rows: splitRows.reduce((sum, row) => sum + row.rows, 0),
      actionLabel: splitStatus === 'running' ? 'Running' : 'Run 70/30',
      action: onSplitBacktest,
      disabled: rowCount <= 0 || splitStatus === 'running',
    },
    {
      key: 'batch',
      label: 'Batch Outcomes',
      status: batchStatus,
      detail: batchRows.length ? `${batchRows.length.toLocaleString('en-US')} outcomes · ${batchRows.filter((row) => row.status === 'succeeded').length.toLocaleString('en-US')} complete` : 'top 5 visible outcomes',
      rows: batchRows.reduce((sum, row) => sum + row.rows, 0),
      actionLabel: batchStatus === 'running' ? 'Running' : 'Run Top 5',
      action: onBatchBacktest,
      disabled: rowCount <= 0 || batchStatus === 'running',
    },
    {
      key: 'benchmark',
      label: 'Universe Benchmark',
      status: benchmarkStatus,
      detail: benchmarkRun ? `#${benchmarkRun.benchmarkId} · ${benchmarkRows.length.toLocaleString('en-US')} market rows` : 'fast/accurate bundle',
      rows: Number(benchmarkRun?.marketCount || 0),
      actionLabel: isBenchmarkRunning ? 'Running' : `Run ${selectedBenchmarkLimit}`,
      action: onRunBenchmark || (() => undefined),
      disabled: !onRunBenchmark || isBenchmarkRunning,
    },
    {
      key: 'walk-forward',
      label: 'Walk-forward',
      status: walkForwardStatus,
      detail: walkForwardRows.length ? `${walkForwardRows.length.toLocaleString('en-US')} rolling segments · ${walkForwardRows.filter((row) => row.status === 'succeeded').length.toLocaleString('en-US')} complete` : 'rolling train/test windows',
      rows: walkForwardRows.reduce((sum, row) => sum + row.rows, 0),
      actionLabel: walkForwardStatus === 'running' ? 'Running' : 'Run WF',
      action: onWalkForwardBacktest,
      disabled: rowCount <= 0 || walkForwardStatus === 'running',
    },
  ]), [backtestStatus, batchRows, batchStatus, benchmarkRows.length, benchmarkRun, benchmarkStatus, hasCompletedRun, isBenchmarkRunning, onBatchBacktest, onRefresh, onRunBenchmark, onSplitBacktest, onWalkForwardBacktest, result.orders.length, result.runId, result.trades.length, rowCount, selectedBenchmarkLimit, splitRows, splitStatus, walkForwardRows, walkForwardStatus]);

  return (
    <section className="qtv-bottom-panel">
      <nav className="qtv-tool-tabs" aria-label="Backtest tools">
        {TOOL_TABS.map(([id, label]) => (
          <button key={id} className={toolTab === id ? 'active' : ''} type="button" onClick={() => setToolTab(id)}>{label}</button>
        ))}
        <span className="qtv-powered">PolyData</span>
      </nav>

      <div className="qtv-tester-head">
        <div className="qtv-strategy-title">
          <strong>Momentum Probability Strategy</strong>
          <button
            className={settingsOpen ? 'active' : ''}
            type="button"
            title="Strategy settings"
            onClick={() => setSettingsOpen((current) => !current)}
          >
            Settings
          </button>
          <button type="button" onClick={onRefresh} title="Refresh strategy data">Refresh</button>
        </div>
        <div className="qtv-tester-actions">
          <label><input type="checkbox" checked readOnly /> Backtest mode</label>
          <label><input type="checkbox" checked={deepBacktest} onChange={onDeepBacktestChange} /> Deep Backtest</label>
          <button type="button" onClick={onSplitBacktest}>{splitStatus === 'running' ? 'Split running' : 'Split 70/30'}</button>
          <button type="button" onClick={onWalkForwardBacktest}>{walkForwardStatus === 'running' ? 'WF running' : 'Walk-forward'}</button>
          <button type="button" onClick={onBatchBacktest}>{batchStatus === 'running' ? 'Batch running' : 'Batch Top 5'}</button>
          <select
            value={selectedBenchmarkUniverse}
            title="Benchmark universe"
            onChange={(event) => onBenchmarkUniverseChange?.(event.currentTarget.value)}
          >
            {(benchmarkUniverses.length ? benchmarkUniverses : [{ universeName: 'nba_2024_25_moneyline', label: 'NBA 2024/25 Moneyline', universeType: 'preset' }]).map((universe) => (
              <option key={universe.universeName} value={universe.universeName}>{universe.label || universe.universeName}</option>
            ))}
          </select>
          <select
            value={String(selectedBenchmarkLimit)}
            title="Benchmark market limit"
            onChange={(event) => onBenchmarkLimitChange?.(Number(event.currentTarget.value))}
          >
            <option value="50">50</option>
            <option value="100">100</option>
            <option value="500">500</option>
          </select>
          <button type="button" disabled={!onRunBenchmark || isBenchmarkRunning} onClick={onRunBenchmark}>{isBenchmarkRunning ? 'Benchmark running' : 'Benchmark'}</button>
          <button type="button" onClick={() => onExport('json')}>Export JSON</button>
        </div>
      </div>

      {toolTab === 'tester' ? (
        <nav className="qtv-subtabs" aria-label="Strategy tester tabs">
          {TESTER_TABS.map(([id, label]) => (
            <button key={id} className={testerTab === id ? 'active' : ''} type="button" onClick={() => onTesterTabChange(id)}>{label}</button>
          ))}
        </nav>
      ) : (
        <div className="qtv-subtabs qtv-subtabs-static">
          <span>{TOOL_TABS.find(([id]) => id === toolTab)?.[1]}</span>
        </div>
      )}

      {settingsOpen ? (
        <div className="qtv-settings-strip">
          <span>entry <b>{strategyParameters.entryThreshold.toFixed(4)}</b></span>
          <span>exit <b>{strategyParameters.exitThreshold.toFixed(4)}</b></span>
          <span>stop <b>{strategyParameters.stopLoss.toFixed(4)}</b></span>
          <span>take <b>{strategyParameters.takeProfit.toFixed(4)}</b></span>
          <span>hold <b>{strategyParameters.maxHoldingBars} bars</b></span>
          <span>size <b>{strategyParameters.positionSize.toLocaleString('en-US')}</b></span>
          <span>fee <b>{strategyParameters.feeBps} bps</b></span>
          <span>slip <b>{strategyParameters.slippageBps} bps</b></span>
          <span>liq <b>{strategyParameters.liquidityCapPct}%</b></span>
          <span>profile <b>{strategyParameters.executionProfile}</b></span>
          <span>role <b>{strategyParameters.orderRole}</b></span>
          <span>lat <b>{strategyParameters.latencyBlocks} blocks</b></span>
          <span>max pos <b>{strategyParameters.maxPositionNotional ? strategyParameters.maxPositionNotional.toLocaleString('en-US') : 'off'}</b></span>
          <span>min fill <b>{strategyParameters.minFillPct}%</b></span>
          <span>framework <b>{propertyValue('engine')}</b></span>
          <span>run <b>{result.runId ? `#${result.runId}` : '-'}</b></span>
        </div>
      ) : null}

      {toolTab === 'screener' ? (
        <div className="qtv-tool-panel">
          <div className="qtv-screener-workbench">
            <section className="qtv-run-control-board" aria-label="Backtest run controls">
              {runControlRows.map((row) => (
                <article key={row.key} className={`qtv-run-lane ${row.status}`}>
                  <header>
                    <span>{row.label}</span>
                    <b>{row.status}</b>
                  </header>
                  <strong>{row.detail}</strong>
                  <em>{row.rows ? `${row.rows.toLocaleString('en-US')} rows` : rowCount ? `${rowCount.toLocaleString('en-US')} source rows` : 'waiting for rows'}</em>
                  <button type="button" disabled={row.disabled} onClick={row.action}>{row.actionLabel}</button>
                </article>
              ))}
            </section>
            {batchLeaderboard.length ? (
              <section className="qtv-screener-leaderboard">
                <header>
                  <strong>Backtest leaderboard</strong>
                  <span>{batchLeaderboard.length.toLocaleString('en-US')} recent batch/split rows</span>
                </header>
                {batchLeaderboard.map((row) => (
                  <button key={`leader-${row.key}-${row.runId || row.status}`} type="button" title={row.marketSlug}>
                    <span>{row.outcome}</span>
                    <b className={numericFromMetric(row.netProfit) >= 0 ? 'positive' : 'negative'}>{row.netProfit}</b>
                    <em>{row.totalReturn} · {row.trades.toLocaleString('en-US')} trades</em>
                    <small>{row.status}{row.runId ? ` · #${row.runId}` : ''}</small>
                  </button>
                ))}
              </section>
            ) : null}
            <div className="qtv-tool-grid">
              {summaryRows.length ? summaryRows.map((metric) => <MetricCard key={`screener-${metric.name}`} metric={metric} />) : (
                <div className="qtv-tool-empty">
                  <strong>No screened backtest</strong>
                  <span>Run a real backtest to populate market screening metrics.</span>
                </div>
              )}
            </div>
          </div>
          <div className="qtv-mini-table qtv-tool-mini">
            <strong>Market Screener</strong>
            {result.performanceRows.slice(0, 8).map((row) => (
              <div key={`screener-${row.metric}`}>
                <span>{row.metric}</span>
                <b>{row.all}</b>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {toolTab === 'editor' ? (
        <div className="qtv-tool-panel qtv-tool-panel-single">
          <PropertiesTab groups={result.propertyGroups} />
        </div>
      ) : null}

      {toolTab === 'replay' ? (
        <div className="qtv-tool-panel">
          <div className="qtv-table-wrap">
            <table className="qtv-table">
              <thead>
                <tr>
                  <th>Trade</th>
                  <th>Entry Block</th>
                  <th>Exit Block</th>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>PnL</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {result.trades.map((trade) => (
                  <tr key={`replay-${trade.id}`} className={trade.id === selectedTradeId ? 'selected' : ''} onClick={() => onTradeSelect(trade.id)}>
                    <td>{trade.id}</td>
                    <td>{trade.entryX?.toLocaleString('en-US') || trade.entryTime}</td>
                    <td>{trade.exitX?.toLocaleString('en-US') || trade.exitTime}</td>
                    <td>{trade.entryPrice.toFixed(3)}</td>
                    <td>{trade.exitPrice.toFixed(3)}</td>
                    <td className={trade.pnl >= 0 ? 'positive' : 'negative'}>{trade.pnl.toFixed(2)}</td>
                    <td>{trade.exitReason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!result.trades.length ? <div className="qtv-tool-empty"><strong>No replay trades</strong><span>Run a completed backtest first.</span></div> : null}
          </div>
          <div className="qtv-mini-table qtv-tool-mini">
            <strong>Replay Cursor</strong>
            <div><span>Selected Trade</span><b>{selectedTrade?.id || '-'}</b></div>
            <div><span>Entry</span><b>{selectedTrade?.entryX?.toLocaleString('en-US') || selectedTrade?.entryTime || '-'}</b></div>
            <div><span>Exit</span><b>{selectedTrade?.exitX?.toLocaleString('en-US') || selectedTrade?.exitTime || '-'}</b></div>
            <div><span>Holding</span><b>{selectedTrade ? `${selectedTrade.holdingBars} bars` : '-'}</b></div>
          </div>
        </div>
      ) : null}

      {toolTab === 'trading' ? (
        <div className="qtv-tool-panel qtv-tool-panel-single">
          <div className="qtv-trading-workbench">
          <div className="qtv-properties">
            <section>
              <h3>Trading Panel</h3>
              <div><span>Mode</span><strong>Backtest simulation</strong></div>
              <div><span>Selected Trade</span><strong>{selectedTrade?.id || '-'}</strong></div>
              <div><span>Outcome</span><strong>{selectedTrade?.outcome || 'YES'}</strong></div>
              <div><span>Size</span><strong>{selectedTrade ? selectedTrade.size.toLocaleString('en-US') : '100'}</strong></div>
              <div><span>Notional</span><strong>{selectedTrade ? `${selectedTrade.notional.toFixed(2)} USDC` : '-'}</strong></div>
              <div><span>Last PnL</span><strong className={selectedTrade && selectedTrade.pnl >= 0 ? 'positive' : 'negative'}>{selectedTrade ? `${selectedTrade.pnl.toFixed(2)} USDC` : '-'}</strong></div>
            </section>
          </div>
          <section className="qtv-execution-model-card">
            <header>
              <strong>Execution Model</strong>
              <span>{engine} · {dataSource}</span>
            </header>
            <div>
              <span>Fee</span><b>{strategyParameters.feeBps} bps</b>
              <span>Slippage</span><b>{strategyParameters.slippageBps} bps</b>
              <span>Liquidity cap</span><b>{strategyParameters.liquidityCapPct}%</b>
              <span>Execution profile</span><b>{strategyParameters.executionProfile}</b>
              <span>Order role</span><b>{strategyParameters.orderRole}</b>
              <span>Position size</span><b>{strategyParameters.positionSize.toLocaleString('en-US')} USDC</b>
              <span>Max position</span><b>{strategyParameters.maxPositionNotional ? `${strategyParameters.maxPositionNotional.toLocaleString('en-US')} USDC` : 'off'}</b>
              <span>Min fill</span><b>{strategyParameters.minFillPct}%</b>
              <span>Latency</span><b>{strategyParameters.latencyBlocks} blocks</b>
              <span>Adverse slip</span><b>{formatNumber(strategyParameters.adverseSlippageCents, 4)} cents</b>
              <span>Round trip</span><b>{formatNumber(parameterDiagnostics.roundTripCostBps, 2)} bps</b>
              <span>Capacity</span><b>{formatNumber(parameterDiagnostics.capacity, 0)} USDC</b>
            </div>
            <footer>
              <button type="button" onClick={() => setToolTab('tester')}>Open tester</button>
              <button type="button" onClick={() => setSettingsOpen(true)}>Edit settings</button>
              <button type="button" onClick={onRefresh}>Run simulation</button>
            </footer>
          </section>
          </div>
        </div>
      ) : null}

      {toolTab === 'tester' && testerTab === 'overview' ? (
        hasCompletedRun ? (
          <div className="qtv-overview">
            <section className="qtv-run-provenance-strip" aria-label="Backtest run reproducibility snapshot">
              <div>
                <strong>Reproducibility</strong>
                <span>{marketTitle} · real API rows only</span>
              </div>
              {runProvenanceRows.map(([label, value]) => (
                <span key={label}>
                  <em>{label}</em>
                  <b>{value}</b>
                </span>
              ))}
              <button type="button" onClick={copyRunPayload}>{copyNotice || 'Copy payload'}</button>
            </section>
            <div className="qtv-metrics-row">
              {result.metrics.map((metric) => <MetricCard key={metric.name} metric={metric} />)}
            </div>
            <div className="qtv-result-grid">
              <div className="qtv-equity-wrap">
                <div className="qtv-prediction-metrics">
                  {result.predictionMetrics.map((metric) => <MetricCard key={metric.name} metric={metric} />)}
                </div>
                <EquityDrawdownChart points={result.equity} />
              </div>
              <div className="qtv-mini-table">
                <strong>Strategy Report</strong>
                {result.performanceRows.slice(0, 8).map((row) => (
                  <div key={row.metric}>
                    <span>{row.metric}</span>
                    <b>{row.all}</b>
                  </div>
                ))}
                <button type="button" onClick={() => onExport('csv')}>Export CSV</button>
              </div>
            </div>
            {benchmarkRun ? (
              <section className="qtv-benchmark-card">
                <header>
                  <div>
                    <strong>Fast / Accurate Benchmark</strong>
                    <span>#{benchmarkRun.benchmarkId} · {benchmarkRun.universeName || 'nba_2024_25_moneyline'} · {benchmarkRun.status}</span>
                  </div>
                  <button type="button" disabled={!onRunBenchmark || isBenchmarkRunning} onClick={onRunBenchmark}>
                    {isBenchmarkRunning ? 'Running' : 'Rerun'}
                  </button>
                </header>
                <div className="qtv-benchmark-metrics">
                  {benchmarkSummaryRows.map(([label, value]) => (
                    <div key={label}>
                      <span>{label}</span>
                      <b>{String(value)}</b>
                    </div>
                  ))}
                </div>
                {benchmarkRows.length ? (
                  <div className="qtv-benchmark-table">
                    <div className="head">
                      <span>Market</span>
                      <span>Fast</span>
                      <span>Accurate</span>
                      <span>PnL diff</span>
                      <span>Quality</span>
                    </div>
                    {benchmarkRows.slice(0, 8).map((row) => (
                      <div key={`${row.benchmarkId}-${row.rowIndex}`}>
                        <span title={row.marketSlug || row.title || ''}>{row.title || row.marketSlug || '-'}</span>
                        <span>{row.fastStatus || '-'}</span>
                        <span>{row.accurateStatus || '-'}</span>
                        <span className={Number(row.pnlDiff) >= 0 ? 'ready' : 'review'}>{compactMoney(row.pnlDiff)}</span>
                        <span>{row.dataQuality || '-'}</span>
                      </div>
                    ))}
                  </div>
                ) : null}
              </section>
            ) : null}
          </div>
        ) : (
          <div className="qtv-tester-empty">
            <div className="qtv-empty-terminal">
              <div>
                <strong>Strategy Tester idle</strong>
                <span>Momentum Probability Strategy</span>
              </div>
              <dl>
                <div><dt>Market</dt><dd>{marketTitle}</dd></div>
                <div><dt>Engine</dt><dd>{engine}</dd></div>
                <div><dt>Source</dt><dd>{dataSource}</dd></div>
                <div><dt>Rows</dt><dd>{rowCount.toLocaleString('en-US')}</dd></div>
                <div><dt>Status</dt><dd>{backtestStatus}</dd></div>
              </dl>
              <ul>
                <li className={marketTitle !== 'No market selected' ? 'ready' : ''}>Market selected</li>
                <li className={rowCount > 0 ? 'ready' : ''}>Price rows loaded</li>
                <li className={engine !== '-' ? 'ready' : ''}>Backtest engine ready</li>
              </ul>
              <div className="qtv-empty-actions">
                <button className="primary" type="button" onClick={onRefresh}>Run Backtest</button>
                <button type="button" onClick={onSplitBacktest}>Split</button>
                <button type="button" onClick={onBatchBacktest}>Batch Top 5</button>
                <button type="button" disabled={!onRunBenchmark || isBenchmarkRunning} onClick={onRunBenchmark}>Benchmark</button>
                <button type="button" onClick={() => setSettingsOpen(true)}>Settings</button>
                <button type="button" onClick={onRefresh}>Refresh</button>
              </div>
            </div>
          </div>
        )
      ) : null}

      {toolTab === 'tester' && testerTab === 'parameters' ? (
        <div className="qtv-parameters-panel">
          <section>
            <header>
              <strong>Strategy Parameters</strong>
              <span className={`qtv-parameter-health ${parameterDiagnostics.health}`}>
                {parameterDiagnostics.health === 'ready' ? 'Ready to run' : 'Review parameters'}
              </span>
              <select value={parameterPreset} onChange={(event) => applyPreset(event.currentTarget.value)}>
                <option>Custom live config</option>
                <option>Backend defaults</option>
                <option>Conservative</option>
                <option>Aggressive</option>
                {savedPresets.length ? (
                  <optgroup label="Saved presets">
                    {savedPresets.map((preset) => (
                      <option key={preset.name} value={`saved:${preset.name}`}>{preset.name}</option>
                    ))}
                  </optgroup>
                ) : null}
              </select>
            </header>
            <div className="qtv-parameter-summary">
              <div>
                <span>Signal spread</span>
                <strong>{parameterDiagnostics.spread.toFixed(4)}</strong>
                <em>{strategyParameters.entryThreshold.toFixed(3)} entry / {strategyParameters.exitThreshold.toFixed(3)} exit</em>
              </div>
              <div>
                <span>Round-trip cost</span>
                <strong>{formatNumber(parameterDiagnostics.roundTripCostBps, 2)} bps</strong>
                <em>fee + slippage model</em>
              </div>
              <div>
                <span>Capital at risk</span>
                <strong>{formatPct(parameterDiagnostics.exposurePct, 2)}</strong>
                <em>{formatNumber(strategyParameters.positionSize, 0)} / {formatNumber(strategyParameters.initialCapital, 0)} USDC</em>
              </div>
              <div>
                <span>Fill capacity</span>
                <strong>{formatNumber(parameterDiagnostics.capacity, 0)} USDC</strong>
                <em>{strategyParameters.liquidityCapPct}% liquidity cap</em>
              </div>
              <div>
                <span>Run fingerprint</span>
                <strong>{runFingerprint}</strong>
                <em>{engine} · {dataSource}</em>
              </div>
            </div>
            <div className={`qtv-parameter-drift ${parameterDrift.status}`}>
              <div>
                <span>Parameter drift</span>
                <strong>{parameterDrift.title}</strong>
                <em>{parameterDrift.detail}</em>
              </div>
              <b>{parameterDrift.status === 'ready' ? 'MATCH' : parameterDrift.status === 'review' ? 'DRIFT' : 'NEW'}</b>
              <dl>
                <div><dt>Live signature</dt><dd>{liveParameterFingerprint}</dd></div>
                <div><dt>Reference run</dt><dd>{parameterDrift.referenceRun ? `#${parameterDrift.referenceRun.runId}` : '-'}</dd></div>
                <div><dt>Reference status</dt><dd>{parameterDrift.referenceRun?.status || '-'}</dd></div>
                <div><dt>Rows</dt><dd>{parameterDrift.referenceRun ? compactRows(parameterDrift.referenceRun.rowsProcessed) : '-'}</dd></div>
              </dl>
              {parameterDrift.diffs.length ? (
                <ul>
                  {parameterDrift.diffs.slice(0, 6).map((row) => (
                    <li key={row.key}>
                      <span>{row.label}</span>
                      <b>{formatParameterValue(row.reference)}</b>
                      <em>→</em>
                      <strong>{formatParameterValue(row.current)}</strong>
                    </li>
                  ))}
                </ul>
              ) : null}
              <footer>
                <button type="button" disabled={!parameterDrift.referenceRun} onClick={() => parameterDrift.referenceRun && applyRunParameters(parameterDrift.referenceRun)}>Apply reference params</button>
                <button type="button" onClick={onRefresh}>Run current params</button>
                <button type="button" onClick={copyStrategyParameters}>Copy live params</button>
              </footer>
            </div>
            {parameterDiagnostics.warnings.length ? (
              <div className="qtv-parameter-warnings">
                {parameterDiagnostics.warnings.map((warning) => <span key={warning}>{warning}</span>)}
              </div>
            ) : null}
            <div className="qtv-execution-assumption-grid" aria-label="Execution assumption scenarios">
              {executionAssumptionRows.map((scenario) => (
                <button
                  key={scenario.key}
                  className={scenario.key === 'current' ? 'active' : ''}
                  type="button"
                  onClick={() => applyExecutionAssumption(scenario.key)}
                >
                  <span>{scenario.label}</span>
                  <strong>{formatNumber(scenario.roundTripBps, 2)} bps</strong>
                  <em>{formatNumber(scenario.costUsdc, 2)} USDC cost · {formatNumber(scenario.capacityUsdc, 0)} cap</em>
                  <small>{scenario.feeBps} fee / {scenario.slippageBps} slip / {scenario.liquidityCapPct}% liq</small>
                </button>
              ))}
            </div>
            <div className="qtv-parameter-grid">
              <label><span>Entry threshold</span><input type="number" min="0.001" max="0.999" step="0.001" value={strategyParameters.entryThreshold} onInput={(event) => updateParameter('entryThreshold', event.currentTarget.value)} /></label>
              <label><span>Exit threshold</span><input type="number" min="0.001" max="0.999" step="0.001" value={strategyParameters.exitThreshold} onInput={(event) => updateParameter('exitThreshold', event.currentTarget.value)} /></label>
              <label><span>Stop loss</span><input type="number" min="0.001" max="0.95" step="0.001" value={strategyParameters.stopLoss} onInput={(event) => updateParameter('stopLoss', event.currentTarget.value)} /></label>
              <label><span>Take profit</span><input type="number" min="0.001" max="5" step="0.001" value={strategyParameters.takeProfit} onInput={(event) => updateParameter('takeProfit', event.currentTarget.value)} /></label>
              <label><span>Max holding bars</span><input type="number" min="1" max="10000" step="1" value={strategyParameters.maxHoldingBars} onInput={(event) => updateParameter('maxHoldingBars', event.currentTarget.value)} /></label>
              <label><span>Position size</span><input type="number" min="1" step="1" value={strategyParameters.positionSize} onInput={(event) => updateParameter('positionSize', event.currentTarget.value)} /></label>
              <label><span>Initial capital</span><input type="number" min="1" step="100" value={strategyParameters.initialCapital} onInput={(event) => updateParameter('initialCapital', event.currentTarget.value)} /></label>
              <label><span>Fee bps</span><input type="number" min="0" max="1000" step="0.1" value={strategyParameters.feeBps} onInput={(event) => updateParameter('feeBps', event.currentTarget.value)} /></label>
              <label><span>Slippage bps</span><input type="number" min="0" max="1000" step="0.1" value={strategyParameters.slippageBps} onInput={(event) => updateParameter('slippageBps', event.currentTarget.value)} /></label>
              <label><span>Liquidity cap %</span><input type="number" min="0" max="100" step="1" value={strategyParameters.liquidityCapPct} onInput={(event) => updateParameter('liquidityCapPct', event.currentTarget.value)} /></label>
              <label><span>Max position</span><input type="number" min="0" step="1" value={strategyParameters.maxPositionNotional} onInput={(event) => updateParameter('maxPositionNotional', event.currentTarget.value)} /></label>
              <label><span>Min fill %</span><input type="number" min="0" max="100" step="1" value={strategyParameters.minFillPct} onInput={(event) => updateParameter('minFillPct', event.currentTarget.value)} /></label>
              <label><span>Execution profile</span><select value={strategyParameters.executionProfile} onChange={(event) => onStrategyParametersChange({ ...strategyParameters, executionProfile: event.currentTarget.value === 'optimistic' || event.currentTarget.value === 'conservative' || event.currentTarget.value === 'stress' ? event.currentTarget.value : 'realistic' })}><option value="optimistic">Optimistic</option><option value="realistic">Realistic</option><option value="conservative">Conservative</option><option value="stress">Stress</option></select></label>
              <label><span>Order role</span><select value={strategyParameters.orderRole} onChange={(event) => onStrategyParametersChange({ ...strategyParameters, orderRole: event.currentTarget.value === 'maker' ? 'maker' : 'taker' })}><option value="taker">Taker</option><option value="maker">Maker</option></select></label>
              <label><span>Execution mode</span><select value={strategyParameters.executionPriceMode} onChange={(event) => onStrategyParametersChange({ ...strategyParameters, executionPriceMode: event.currentTarget.value === 'ORDERFILLED' || event.currentTarget.value === 'DEPTH' || event.currentTarget.value === 'LEGACY' ? event.currentTarget.value : 'ORDERFILLED_LIMIT_REPLAY' })}><option value="ORDERFILLED_LIMIT_REPLAY">Limit replay</option><option value="ORDERFILLED">OrderFilled probability</option><option value="DEPTH">CLOB depth snapshot</option><option value="LEGACY">Legacy volume cap</option></select></label>
              <label><span>Final valuation</span><select value={strategyParameters.finalValuationMode} onChange={(event) => onStrategyParametersChange({ ...strategyParameters, finalValuationMode: event.currentTarget.value === 'FORCE_CLOSE' ? 'FORCE_CLOSE' : 'SETTLEMENT' })}><option value="SETTLEMENT">Settlement 0/1</option><option value="FORCE_CLOSE">Legacy force close</option></select></label>
              <label><span>Buy limit</span><input type="number" min="0.001" max="0.999" step="0.001" value={strategyParameters.buyLimitPrice ?? ''} onInput={(event) => updateParameter('buyLimitPrice', event.currentTarget.value)} /></label>
              <label><span>Sell limit</span><input type="number" min="0.001" max="0.999" step="0.001" value={strategyParameters.sellLimitPrice ?? ''} onInput={(event) => updateParameter('sellLimitPrice', event.currentTarget.value)} /></label>
              <label><span>Settlement value</span><input type="number" min="0" max="1" step="1" value={strategyParameters.settlementValue ?? ''} onInput={(event) => updateParameter('settlementValue', event.currentTarget.value)} /></label>
              <label><span>Latency seconds</span><input type="number" min="0" max="3600" step="1" value={strategyParameters.latencySeconds} onInput={(event) => updateParameter('latencySeconds', event.currentTarget.value)} /></label>
              <label><span>Latency blocks</span><input type="number" min="0" max="100000" step="1" value={strategyParameters.latencyBlocks} onInput={(event) => updateParameter('latencyBlocks', event.currentTarget.value)} /></label>
              <label><span>Max book stale sec</span><input type="number" min="0" max="86400" step="1" value={strategyParameters.maxBookStalenessSeconds} onInput={(event) => updateParameter('maxBookStalenessSeconds', event.currentTarget.value)} /></label>
              <label><span>Adverse slip cents</span><input type="number" min="0" max="1" step="0.001" value={strategyParameters.adverseSlippageCents} onInput={(event) => updateParameter('adverseSlippageCents', event.currentTarget.value)} /></label>
              <label><span>Fill haircut %</span><input type="number" min="0" max="100" step="1" value={strategyParameters.fillProbabilityHaircutPct} onInput={(event) => updateParameter('fillProbabilityHaircutPct', event.currentTarget.value)} /></label>
              <label><span>Min fill size</span><input type="number" min="0" step="1" value={strategyParameters.minFillSize} onInput={(event) => updateParameter('minFillSize', event.currentTarget.value)} /></label>
              <label><span>Allow partial</span><select value={strategyParameters.allowPartialFill ? 'yes' : 'no'} onChange={(event) => onStrategyParametersChange({ ...strategyParameters, allowPartialFill: event.currentTarget.value === 'yes' })}><option value="yes">Yes</option><option value="no">No</option></select></label>
              <label><span>Reject stale book</span><select value={strategyParameters.rejectOnStaleBook ? 'yes' : 'no'} onChange={(event) => onStrategyParametersChange({ ...strategyParameters, rejectOnStaleBook: event.currentTarget.value === 'yes' })}><option value="yes">Yes</option><option value="no">No</option></select></label>
              <label><span>Engine</span><input readOnly value={engine} /></label>
              <label><span>Source rows</span><input readOnly value={rowCount.toLocaleString('en-US')} /></label>
              <label><span>Market</span><input readOnly value={marketTitle} /></label>
              <label><span>Status</span><input readOnly value={backtestStatus} /></label>
            </div>
            <div className="qtv-parameter-actions">
              <button type="button" onClick={onStrategyAutoTune}>Auto tune from loaded prices</button>
              <button type="button" onClick={() => applyPreset('Backend defaults')}>Reset defaults</button>
              <button type="button" onClick={copyStrategyParameters}>Copy params</button>
              <button type="button" onClick={onSplitBacktest}>Split 70/30</button>
              <button type="button" onClick={onWalkForwardBacktest}>Walk-forward</button>
              <button type="button" onClick={onBatchBacktest}>Batch Top 5</button>
              <button className="primary" type="button" onClick={onRefresh}>Run Backtest</button>
            </div>
            <div className="qtv-preset-save-row">
              <input
                value={presetDraftName}
                aria-label="Preset name"
                onInput={(event) => setPresetDraftName(event.currentTarget.value)}
              />
              <button type="button" onClick={savePreset}>Save preset</button>
              <button type="button" onClick={copyRunPayload}>Copy run payload</button>
              {copyNotice ? <span>{copyNotice}</span> : null}
            </div>
            <p>These controls are bound to the real backtest request. Fee bps, slippage bps, liquidity cap, train/test, batch, and walk-forward runs all submit reproducible API jobs with run ids.</p>
          </section>
          <aside className="qtv-run-health">
            <strong>Readiness</strong>
            <span className={marketTitle !== 'No market selected' ? 'ready' : ''}>Market selected</span>
            <span className={rowCount > 0 ? 'ready' : ''}>Price rows loaded</span>
            <span className={engine !== '-' ? 'ready' : ''}>Engine configured</span>
            <span className={strategyParameters.entryThreshold > strategyParameters.exitThreshold ? 'ready' : 'review'}>Entry above exit</span>
            <span className={statusForRisk(parameterDiagnostics.spread > parameterDiagnostics.breakEvenMove)}>Spread covers cost</span>
            <span className={statusForRisk(parameterDiagnostics.exposurePct <= 20)}>Capital risk under 20%</span>
            <div className="qtv-run-snapshot">
              <strong>Latest run snapshot</strong>
              {runSnapshotRows.map(([label, value]) => (
                <div key={label}><span>{label}</span><b>{value}</b></div>
              ))}
            </div>
            <div className="qtv-saved-presets">
              <strong>Saved Presets</strong>
              {savedPresets.length ? savedPresets.slice(0, 5).map((preset) => (
                <div key={`saved-${preset.name}`}>
                  <button type="button" onClick={() => applyPreset(`saved:${preset.name}`)}>{preset.name}</button>
                  <span>{new Date(preset.savedAt).toLocaleDateString()}</span>
                  <button type="button" title={`Delete ${preset.name}`} onClick={() => deletePreset(preset.name)}>Delete</button>
                </div>
              )) : <em>No saved parameter sets yet</em>}
            </div>
            <button className="primary" type="button" onClick={onRefresh}>Run Backtest</button>
          </aside>
        </div>
      ) : null}

      {toolTab === 'tester' && testerTab === 'performance' ? (
        <PerformanceSummaryTab
          rows={filteredPerformanceRows}
          search={performanceSearch}
          sortKey={performanceSortKey}
          sortDirection={performanceSortDirection}
          onSearchChange={onPerformanceSearchChange}
          onSortChange={onPerformanceSortChange}
        />
      ) : null}

      {toolTab === 'tester' && testerTab === 'orders' ? (
        <div className="qtv-table-wrap qtv-orders-table-wrap">
          <div className="qtv-filter-strip">
            <span>{result.orders.length.toLocaleString('en-US')} orders</span>
            <span>{result.orders.filter((order) => order.status === 'FILLED').length.toLocaleString('en-US')} filled</span>
            <span>{result.orders.filter((order) => order.status === 'PARTIAL_FILLED').length.toLocaleString('en-US')} partial</span>
            <span>{result.orders.filter((order) => order.status === 'NO_FILL').length.toLocaleString('en-US')} no fill</span>
            <span>{latestOrder ? `latest ${latestOrder.status}` : 'no order lifecycle yet'}</span>
          </div>
          <table className="qtv-table">
            <thead>
              <tr>
                <th>Order</th>
                <th>Status</th>
                <th>Side</th>
                <th>Role</th>
                <th>Signal Block</th>
                <th>Submit Block</th>
                <th>Decision</th>
                <th>Fill Price</th>
                <th>Requested</th>
                <th>Filled</th>
                <th>Fill Prob</th>
                <th>Fill %</th>
                <th>Block Vol</th>
                <th>Trades</th>
                <th>Fee</th>
                <th>Slip</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {result.orders.map((order) => (
                <tr key={order.id} className={order.status.toLowerCase().replace(/_/g, '-')}>
                  <td>{order.id}</td>
                  <td>{order.status}</td>
                  <td>{order.side}</td>
                  <td>{order.role}</td>
                  <td>{Number.isFinite(order.signalX) ? order.signalX.toLocaleString('en-US') : '-'}</td>
                  <td>{Number.isFinite(order.submitX) ? order.submitX.toLocaleString('en-US') : '-'}</td>
                  <td>{order.decisionPrice.toFixed(3)}</td>
                  <td>{order.avgFillPrice.toFixed(3)}</td>
                  <td>{formatNumber(order.requestedNotional, 2)}</td>
                  <td>{formatNumber(order.filledNotional, 2)}</td>
                  <td>{formatPct(order.fillProbability, 2)}</td>
                  <td>{formatPct(order.fillPct, 2)}</td>
                  <td>{formatNumber(order.blockVolume, 2)}</td>
                  <td>{formatNumber(order.tradeCount, 0)}</td>
                  <td>{formatNumber(order.feeCost, 4)}</td>
                  <td>{formatNumber(order.slippageCost, 4)}</td>
                  <td>{order.noFillReason || order.executionSource}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!result.orders.length ? <div className="qtv-tool-empty"><strong>No order lifecycle</strong><span>Run a Phase 1 backtest to load submitted, filled, partial, and no-fill orders.</span></div> : null}
        </div>
      ) : null}

      {toolTab === 'tester' && testerTab === 'trades' ? (
        <TradesTableTab
          trades={filteredTrades}
          filters={tradeFilters}
          selectedTradeId={selectedTradeId}
          onToggleFilter={onTradeFilterToggle}
          onSelectTrade={onTradeSelect}
        />
      ) : null}

      {toolTab === 'tester' && testerTab === 'ledger' ? (
        <div className="qtv-table-wrap qtv-ledger-table-wrap">
          <div className="qtv-filter-strip">
            <span>{result.ledger.length.toLocaleString('en-US')} ledger rows</span>
            <span>{latestLedger ? `cash ${formatNumber(latestLedger.cashAfter, 2)} USDC` : 'cash --'}</span>
            <span>{latestLedger ? `position ${formatNumber(latestLedger.positionAfter, 4)}` : 'position --'}</span>
            <span>{latestLedger ? `realized ${formatNumber(latestLedger.realizedPnl, 2)}` : 'realized --'}</span>
          </div>
          <table className="qtv-table">
            <thead>
              <tr>
                <th>Ledger</th>
                <th>Event</th>
                <th>Block</th>
                <th>Order</th>
                <th>Trade</th>
                <th>Shares Δ</th>
                <th>Cash Δ</th>
                <th>Fee</th>
                <th>Slip</th>
                <th>Realized PnL</th>
                <th>Position</th>
                <th>Cash</th>
                <th>Price</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {result.ledger.map((row) => (
                <tr key={row.id} className={row.eventType.toLowerCase()}>
                  <td>{row.id}</td>
                  <td>{row.eventType}</td>
                  <td>{Number.isFinite(row.xValue) ? row.xValue.toLocaleString('en-US') : '-'}</td>
                  <td>{row.orderId || '-'}</td>
                  <td>{row.tradeId || '-'}</td>
                  <td className={row.sharesDelta >= 0 ? 'positive' : 'negative'}>{formatNumber(row.sharesDelta, 4)}</td>
                  <td className={row.cashDelta >= 0 ? 'positive' : 'negative'}>{formatNumber(row.cashDelta, 4)}</td>
                  <td>{formatNumber(row.fee, 4)}</td>
                  <td>{formatNumber(row.slippageCost, 4)}</td>
                  <td className={row.realizedPnl >= 0 ? 'positive' : 'negative'}>{formatNumber(row.realizedPnl, 4)}</td>
                  <td>{formatNumber(row.positionAfter, 4)}</td>
                  <td>{formatNumber(row.cashAfter, 4)}</td>
                  <td>{row.price.toFixed(3)}</td>
                  <td>{row.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!result.ledger.length ? <div className="qtv-tool-empty"><strong>No ledger rows</strong><span>Run a completed backtest to see BUY/SELL cashflow, fees, slippage, position, and cash balance.</span></div> : null}
        </div>
      ) : null}

      {toolTab === 'tester' && testerTab === 'equity' ? (
        <div className="qtv-chart-tab">
          <header><strong>Equity Curve</strong><span>{hasCompletedRun ? `Run #${result.runId}` : 'Run a backtest to populate equity.'}</span></header>
          {result.equity.length ? <EquityDrawdownChart points={result.equity} /> : <div className="qtv-tool-empty"><strong>No equity points</strong><span>Completed backtest equity is not available yet.</span></div>}
        </div>
      ) : null}

      {toolTab === 'tester' && testerTab === 'drawdown' ? (
        <div className="qtv-chart-tab">
          <header><strong>Drawdown</strong><span>{propertyValue('max drawdown') || 'Real drawdown appears after a completed run.'}</span></header>
          {result.equity.length ? <EquityDrawdownChart points={result.equity} /> : <div className="qtv-tool-empty"><strong>No drawdown curve</strong><span>Run a backtest with equity output first.</span></div>}
        </div>
      ) : null}

      {toolTab === 'tester' && testerTab === 'benchmark' ? (
        <div className="qtv-runs-panel">
          <section className="qtv-benchmark-card">
            <header>
              <div>
                <strong>OrderFilled Benchmark</strong>
                <span>{benchmarkRun ? `#${benchmarkRun.benchmarkId} · ${benchmarkRun.universeName || selectedBenchmarkUniverse} · ${benchmarkRun.status}` : `${selectedBenchmarkUniverse} · favorite_hold_v1`}</span>
              </div>
              <button type="button" disabled={!onRunBenchmark || isBenchmarkRunning} onClick={onRunBenchmark}>
                {isBenchmarkRunning ? 'Running' : `Run ${selectedBenchmarkLimit}`}
              </button>
            </header>
            <div className="qtv-benchmark-metrics">
              {benchmarkSummaryRows.map(([label, value]) => (
                <div key={`bench-tab-${label}`}>
                  <span>{label}</span>
                  <b>{String(value)}</b>
                </div>
              ))}
            </div>
            <div className="qtv-benchmark-table">
              <div className="head">
                <span>Market</span>
                <span>Fast</span>
                <span>Accurate</span>
                <span>PnL diff</span>
                <span>Quality</span>
              </div>
              {benchmarkRows.slice(0, 24).map((row) => (
                <div key={`bench-tab-${row.benchmarkId}-${row.rowIndex}`}>
                  <span title={row.marketSlug || row.title || ''}>{row.title || row.marketSlug || '-'}</span>
                  <span>{row.fastStatus || '-'}</span>
                  <span>{row.accurateStatus || '-'}</span>
                  <span className={Number(row.pnlDiff) >= 0 ? 'ready' : 'review'}>{compactMoney(row.pnlDiff)}</span>
                  <span>{row.dataQuality || '-'}</span>
                </div>
              ))}
            </div>
            {!benchmarkRun ? <div className="qtv-tool-empty"><strong>No benchmark run loaded</strong><span>Select a universe and run the fast/accurate bundle.</span></div> : null}
          </section>
          <section className="qtv-benchmark-card">
            <header><div><strong>Profiles</strong><span>fast/accurate execution bundle</span></div></header>
            <div className="qtv-benchmark-table">
              <div className="head">
                <span>Profile</span>
                <span>Signals</span>
                <span>Trades</span>
                <span>No fill</span>
                <span>PnL</span>
              </div>
              {benchmarkArtifactRows.profiles.slice(0, 8).map((row) => (
                <div key={`profile-${recordText(row, 'key')}`}>
                  <span>{recordText(row, 'key')}</span>
                  <span>{recordText(row, 'signal_count')}</span>
                  <span>{recordText(row, 'trades')}</span>
                  <span>{recordText(row, 'no_fills')}</span>
                  <span className={Number(row.total_pnl) >= 0 ? 'ready' : 'review'}>{recordText(row, 'total_pnl')}</span>
                </div>
              ))}
            </div>
          </section>
        </div>
      ) : null}

      {toolTab === 'tester' && testerTab === 'fillQuality' ? (
        <div className="qtv-runs-panel">
          <section className="qtv-benchmark-card">
            <header><div><strong>Fill Quality</strong><span>signals, fills, slippage, latency, no-fill reasons</span></div></header>
            <div className="qtv-benchmark-metrics">
              {[
                ['Signals', recordText(benchmarkArtifactRows.fillQuality, 'signal_count')],
                ['Submitted', recordText(benchmarkArtifactRows.fillQuality, 'submitted_count')],
                ['Filled', recordText(benchmarkArtifactRows.fillQuality, 'filled_count')],
                ['Fill rate', recordText(benchmarkArtifactRows.fillQuality, 'fill_rate')],
                ['Partial', recordText(benchmarkArtifactRows.fillQuality, 'partial_fill_rate')],
                ['No fill', recordText(benchmarkArtifactRows.fillQuality, 'no_fill_rate')],
                ['Avg fill', recordText(benchmarkArtifactRows.fillQuality, 'avg_fill_price')],
                ['Latency blocks', recordText(benchmarkArtifactRows.fillQuality, 'avg_latency_blocks')],
              ].map(([label, value]) => <div key={label}><span>{label}</span><b>{value}</b></div>)}
            </div>
            <pre>{JSON.stringify(benchmarkArtifactRows.fillQuality.no_fill_reasons || {}, null, 2)}</pre>
          </section>
        </div>
      ) : null}

      {toolTab === 'tester' && testerTab === 'dataQuality' ? (
        <div className="qtv-runs-panel">
          <section className="qtv-benchmark-card">
            <header><div><strong>Data Quality</strong><span>coverage, replay rows, fast/accurate mismatch</span></div></header>
            <div className="qtv-benchmark-metrics">
              {[
                ['Source', recordText(benchmarkArtifactRows.dataQuality, 'source_table')],
                ['Raw markets', recordText(benchmarkArtifactRows.dataQuality, 'raw_market_count')],
                ['Raw rows', recordText(benchmarkArtifactRows.dataQuality, 'raw_rows')],
                ['Status mismatch', recordText(benchmarkArtifactRows.dataQuality, 'status_mismatch_count')],
                ['PnL drift', recordText(benchmarkArtifactRows.dataQuality, 'pnl_drift_count')],
                ['Gap', recordText(benchmarkArtifactRows.dataQuality, 'gap_status')],
                ['Stale', recordText(benchmarkArtifactRows.dataQuality, 'stale_status')],
              ].map(([label, value]) => <div key={label}><span>{label}</span><b>{value}</b></div>)}
            </div>
            <pre>{JSON.stringify(benchmarkArtifactRows.dataQuality.coverage || {}, null, 2)}</pre>
          </section>
        </div>
      ) : null}

      {toolTab === 'tester' && testerTab === 'regime' ? (
        <div className="qtv-runs-panel">
          {['price_bucket', 'liquidity_bucket', 'drift_bucket', 'settlement_bucket'].map((key) => (
            <section key={key} className="qtv-benchmark-card">
              <header><div><strong>{key.replace(/_/g, ' ')}</strong><span>bucketed PnL and sample count</span></div></header>
              <div className="qtv-benchmark-table">
                <div className="head"><span>Bucket</span><span>Count</span><span>PnL</span><span></span><span></span></div>
                {asArray(benchmarkArtifactRows.regimeBuckets[key]).map((row) => (
                  <div key={`${key}-${recordText(row, 'bucket')}`}>
                    <span>{recordText(row, 'bucket')}</span>
                    <span>{recordText(row, 'count')}</span>
                    <span className={Number(row.pnl) >= 0 ? 'ready' : 'review'}>{recordText(row, 'pnl')}</span>
                    <span></span>
                    <span></span>
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      ) : null}

      {toolTab === 'tester' && testerTab === 'predictionQuality' ? (
        <div className="qtv-runs-panel">
          <section className="qtv-benchmark-card">
            <header><div><strong>Prediction Quality</strong><span>Brier, calibration, close-line drift</span></div></header>
            <div className="qtv-benchmark-metrics">
              {[
                ['Samples', recordText(benchmarkArtifactRows.predictionQuality, 'sample_count')],
                ['Brier', recordText(benchmarkArtifactRows.predictionQuality, 'brier_score')],
                ['Market Brier', recordText(benchmarkArtifactRows.predictionQuality, 'market_brier_score')],
                ['Advantage', recordText(benchmarkArtifactRows.predictionQuality, 'brier_advantage')],
                ['Snapshot drift', recordText(benchmarkArtifactRows.predictionQuality, 'avg_snapshot_drift')],
                ['Close-line drift', recordText(benchmarkArtifactRows.predictionQuality, 'avg_close_line_drift')],
              ].map(([label, value]) => <div key={label}><span>{label}</span><b>{value}</b></div>)}
            </div>
            <div className="qtv-benchmark-table">
              <div className="head"><span>Bucket</span><span>Count</span><span>Predicted</span><span>Actual</span><span>Brier</span></div>
              {asArray(benchmarkArtifactRows.predictionQuality.calibration_buckets).map((row) => (
                <div key={`cal-${recordText(row, 'bucket')}`}>
                  <span>{recordText(row, 'bucket')}</span>
                  <span>{recordText(row, 'count')}</span>
                  <span>{recordText(row, 'avg_predicted')}</span>
                  <span>{recordText(row, 'actual_rate')}</span>
                  <span>{recordText(row, 'brier_score')}</span>
                </div>
              ))}
            </div>
          </section>
        </div>
      ) : null}

      {toolTab === 'tester' && testerTab === 'runs' ? (
        <div className="qtv-runs-panel">
          <section>
            <strong>Current Run</strong>
            <dl>
              <div><dt>Run</dt><dd>{result.runId ? `#${result.runId}` : '-'}</dd></div>
              <div><dt>Fingerprint</dt><dd>{runFingerprint}</dd></div>
              <div><dt>Status</dt><dd>{backtestStatus}</dd></div>
              <div><dt>Engine</dt><dd>{engine}</dd></div>
              <div><dt>Rows</dt><dd>{rowCount.toLocaleString('en-US')}</dd></div>
              <div><dt>Orders</dt><dd>{result.orders.length.toLocaleString('en-US')}</dd></div>
              <div><dt>Trades</dt><dd>{result.trades.length.toLocaleString('en-US')}</dd></div>
              <div><dt>Ledger</dt><dd>{result.ledger.length.toLocaleString('en-US')}</dd></div>
              <div><dt>Metrics</dt><dd>{result.metrics.length.toLocaleString('en-US')}</dd></div>
            </dl>
          </section>
          <section className="qtv-benchmark-card">
            <header>
              <div>
                <strong>Benchmark Run</strong>
                <span>{benchmarkRun ? `#${benchmarkRun.benchmarkId} · ${benchmarkRun.status}` : benchmarkStatus}</span>
              </div>
              <button type="button" disabled={!onRunBenchmark || isBenchmarkRunning} onClick={onRunBenchmark}>
                {isBenchmarkRunning ? 'Running' : `Run ${selectedBenchmarkLimit}`}
              </button>
            </header>
            {benchmarkRun ? (
              <>
                <div className="qtv-benchmark-metrics">
                  {benchmarkSummaryRows.map(([label, value]) => (
                    <div key={`run-${label}`}>
                      <span>{label}</span>
                      <b>{String(value)}</b>
                    </div>
                  ))}
                </div>
                <div className="qtv-benchmark-table">
                  <div className="head">
                    <span>Market</span>
                    <span>Fast</span>
                    <span>Accurate</span>
                    <span>PnL diff</span>
                    <span>Quality</span>
                  </div>
                  {benchmarkRows.slice(0, 12).map((row) => (
                    <div key={`runs-bench-${row.benchmarkId}-${row.rowIndex}`}>
                      <span title={row.marketSlug || row.title || ''}>{row.title || row.marketSlug || '-'}</span>
                      <span>{row.fastStatus || '-'}</span>
                      <span>{row.accurateStatus || '-'}</span>
                      <span className={Number(row.pnlDiff) >= 0 ? 'ready' : 'review'}>{compactMoney(row.pnlDiff)}</span>
                      <span>{row.dataQuality || '-'}</span>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <div className="qtv-tool-empty">
                <strong>No benchmark run loaded</strong>
                <span>Run a universe benchmark to persist fast/accurate timing, ledger summary, coverage state, and per-market diffs.</span>
              </div>
            )}
          </section>
          <section className="qtv-run-history-card">
            <div className="qtv-run-config-head">
              <strong>Recent Real Runs</strong>
              <span>{backtestRunsStatus}</span>
            </div>
            {recentRunsForDisplay.length ? (
              <div className="qtv-run-history-table">
                <div className="head">
                  <span>Run</span>
                  <span>Status</span>
                  <span>Market</span>
                  <span>Engine</span>
                  <span>Rows</span>
                  <span>Fingerprint</span>
                  <span>Params</span>
                  <span>Created</span>
                  <span>Action</span>
                </div>
                {recentRunsForDisplay.map((run) => {
                  const paramSummary = runParameterSummary(run, strategyParameters);
                  return (
                    <div key={`history-${run.runId}`} className={`${run.status} ${run.isCurrent ? 'current' : ''}`} title={run.error || `${run.marketSlug}\n${paramSummary.label}`}>
                      <span>#{run.runId}{run.isCurrent ? ' current' : ''}</span>
                      <span>{run.status}</span>
                      <span>{run.marketSlug}</span>
                      <span>{run.backtestEngine || '-'}</span>
                      <span>{compactRows(run.rowsProcessed)}</span>
                      <span>{paramSummary.fingerprint || '-'}</span>
                      <span className={paramSummary.spread <= paramSummary.costBps / 10000 ? 'review' : 'ready'}>{paramSummary.label}</span>
                      <span>{compactRunTime(run.createdAt)}</span>
                      <span className="actions">
                        <button type="button" onClick={() => onRunLoad?.(run.runId)}>{run.isCurrent ? 'Reload' : 'Load'}</button>
                        <button type="button" onClick={() => applyRunParameters(run)}>Params</button>
                      </span>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="qtv-tool-empty">
                <strong>No run history loaded</strong>
                <span>Run a backtest or refresh once the API returns recent quant_backtest_runs.</span>
              </div>
            )}
          </section>
          <section>
            <strong>Latest Execution</strong>
            <dl>
              <div><dt>Order</dt><dd>{latestOrder?.id || '-'}</dd></div>
              <div><dt>Status</dt><dd>{latestOrder?.status || '-'}</dd></div>
              <div><dt>Fill</dt><dd>{latestOrder ? `${formatPct(latestOrder.fillPct, 2)} · p=${formatPct(latestOrder.fillProbability, 2)}` : '-'}</dd></div>
              <div><dt>PnL</dt><dd className={selectedTrade && selectedTrade.pnl >= 0 ? 'positive' : 'negative'}>{selectedTrade ? `${selectedTrade.pnl.toFixed(2)} USDC` : '-'}</dd></div>
              <div><dt>Cash</dt><dd>{latestLedger ? `${formatNumber(latestLedger.cashAfter, 2)} USDC` : '-'}</dd></div>
            </dl>
          </section>
          <section className="qtv-batch-runs-card">
            <div className="qtv-run-config-head">
              <strong>Train/Test Split</strong>
              <button type="button" onClick={onSplitBacktest}>{splitStatus === 'running' ? 'Running...' : 'Run 70/30'}</button>
            </div>
            {splitRows.length ? (
              <div className="qtv-batch-table">
                <div className="head">
                  <span>Segment</span>
                  <span>Run</span>
                  <span>Status</span>
                  <span>Rows</span>
                  <span>Trades</span>
                  <span>Net</span>
                  <span>Return</span>
                  <span>Drawdown</span>
                </div>
                {splitRows.map((row) => (
                  <div key={`split-${row.key}`} className={row.status === 'failed' ? 'failed' : row.status === 'succeeded' ? 'succeeded' : ''} title={row.error || row.marketSlug}>
                    <span>{row.outcome}</span>
                    <span>{row.runId ? `#${row.runId}` : '-'}</span>
                    <span>{row.status}</span>
                    <span>{row.rows.toLocaleString('en-US')}</span>
                    <span>{row.trades.toLocaleString('en-US')}</span>
                    <span>{row.netProfit}</span>
                    <span>{row.totalReturn}</span>
                    <span>{row.maxDrawdown}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="qtv-tool-empty">
                <strong>No split run</strong>
                <span>Run 70/30 to submit real train and test backtests over the selected outcome block range.</span>
              </div>
            )}
          </section>
          <section className="qtv-batch-runs-card">
            <div className="qtv-run-config-head">
              <strong>Batch Backtests</strong>
              <button type="button" onClick={onBatchBacktest}>{batchStatus === 'running' ? 'Running...' : 'Run Top 5'}</button>
            </div>
            {batchRows.length ? (
              <div className="qtv-batch-table">
                <div className="head">
                  <span>Outcome</span>
                  <span>Run</span>
                  <span>Status</span>
                  <span>Rows</span>
                  <span>Trades</span>
                  <span>Net</span>
                  <span>Return</span>
                  <span>Drawdown</span>
                </div>
                {batchRows.map((row) => (
                  <div key={row.key} className={row.status === 'failed' ? 'failed' : row.status === 'succeeded' ? 'succeeded' : ''} title={row.error || row.marketSlug}>
                    <span>{row.outcome}</span>
                    <span>{row.runId ? `#${row.runId}` : '-'}</span>
                    <span>{row.status}</span>
                    <span>{row.rows.toLocaleString('en-US')}</span>
                    <span>{row.trades.toLocaleString('en-US')}</span>
                    <span>{row.netProfit}</span>
                    <span>{row.totalReturn}</span>
                    <span>{row.maxDrawdown}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="qtv-tool-empty">
                <strong>No batch runs</strong>
                <span>Run Top 5 to backtest multiple event outcomes with the current strategy parameters.</span>
              </div>
            )}
          </section>
          <section className="qtv-batch-runs-card">
            <div className="qtv-run-config-head">
              <strong>Walk-forward</strong>
              <button type="button" onClick={onWalkForwardBacktest}>{walkForwardStatus === 'running' ? 'Running...' : 'Run WF'}</button>
            </div>
            {walkForwardRows.length ? (
              <div className="qtv-batch-table">
                <div className="head">
                  <span>Window</span>
                  <span>Run</span>
                  <span>Status</span>
                  <span>Rows</span>
                  <span>Trades</span>
                  <span>Net</span>
                  <span>Return</span>
                  <span>Drawdown</span>
                </div>
                {walkForwardRows.map((row) => (
                  <div key={`walk-${row.key}`} className={row.status === 'failed' ? 'failed' : row.status === 'succeeded' ? 'succeeded' : ''} title={row.error || row.marketSlug}>
                    <span>{row.outcome}</span>
                    <span>{row.runId ? `#${row.runId}` : '-'}</span>
                    <span>{row.status}</span>
                    <span>{row.rows.toLocaleString('en-US')}</span>
                    <span>{row.trades.toLocaleString('en-US')}</span>
                    <span>{row.netProfit}</span>
                    <span>{row.totalReturn}</span>
                    <span>{row.maxDrawdown}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="qtv-tool-empty">
                <strong>No walk-forward run</strong>
                <span>Run WF to submit rolling train/test windows across the selected outcome block range.</span>
              </div>
            )}
          </section>
          <section className="qtv-run-config-card">
            <div className="qtv-run-config-head">
              <strong>Reproducibility Snapshot</strong>
              <button type="button" onClick={copyRunPayload}>{copyNotice || 'Copy JSON'}</button>
            </div>
            <dl>
              <div><dt>Market slug</dt><dd>{propertyValue('market slug')}</dd></div>
              <div><dt>Outcome</dt><dd>{propertyValue('outcome')}</dd></div>
              <div><dt>Block range</dt><dd>{propertyValue('from block')} → {propertyValue('to block')}</dd></div>
              <div><dt>Rows processed</dt><dd>{propertyValue('rows processed')}</dd></div>
              <div><dt>Generated</dt><dd>{result.generatedAt || '-'}</dd></div>
            </dl>
            <pre>{runPayloadText}</pre>
          </section>
        </div>
      ) : null}

      {toolTab === 'tester' && testerTab === 'logs' ? (
        <div className="qtv-logs-panel">
          <strong>Run Log</strong>
          <span>{new Date().toLocaleTimeString()} · status {backtestStatus}</span>
          <span>source {dataSource} · engine {engine} · rows {rowCount.toLocaleString('en-US')}</span>
          <span>{hasCompletedRun ? `loaded ${result.metrics.length} metrics, ${result.trades.length} trades, ${result.equity.length} equity points` : 'no completed real backtest in this panel yet'}</span>
        </div>
      ) : null}

      {toolTab === 'tester' && testerTab === 'properties' ? <PropertiesTab groups={result.propertyGroups} /> : null}
    </section>
  );
}
