import type {
  HazardMarketEvidence,
  HazardMarketLinksResponse,
  RelatedWeatherMarket,
} from '../domain/types';

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function parseEvidence(value: unknown, key: string): HazardMarketEvidence {
  if (!isRecord(value) || typeof value.passed !== 'boolean' || typeof value.reason !== 'string') {
    throw new Error(`Related market ${key} evidence is invalid`);
  }
  if (value.level != null && value.level !== 'direct' && value.level !== 'contextual') {
    throw new Error(`Related market ${key} evidence level is invalid`);
  }
  return value as unknown as HazardMarketEvidence;
}

function parseMarket(value: unknown): RelatedWeatherMarket {
  if (!isRecord(value)) throw new Error('Related market must be an object');
  if (typeof value.title !== 'string' || !value.title.trim()) {
    throw new Error('Related market title is required');
  }
  if (value.relationship !== 'direct' && value.relationship !== 'contextual') {
    throw new Error('Related market relationship is invalid');
  }
  if (typeof value.matchScore !== 'number' || value.matchScore < 0 || value.matchScore > 1) {
    throw new Error('Related market match score is invalid');
  }
  if (!isRecord(value.matchReasons)) throw new Error('Related market evidence is missing');
  const matchReasons = {
    type: parseEvidence(value.matchReasons.type, 'type'),
    space: parseEvidence(value.matchReasons.space, 'space'),
    time: parseEvidence(value.matchReasons.time, 'time'),
    metric: parseEvidence(value.matchReasons.metric, 'metric'),
  };
  if (!Object.values(matchReasons).every((evidence) => evidence.passed)) {
    throw new Error('Rejected market cannot cross the related-market response boundary');
  }
  if (!isRecord(value.target) || !isRecord(value.quote) || !isRecord(value.oracle)) {
    throw new Error('Related market target, quote or oracle state is invalid');
  }
  return {
    ...(value as unknown as RelatedWeatherMarket),
    matchReasons,
  };
}

export function parseRelatedWeatherMarkets(value: unknown): HazardMarketLinksResponse {
  if (!isRecord(value) || value.schemaVersion !== 'hazard-market-links.v1') {
    throw new Error('Unsupported related weather market schema');
  }
  if (typeof value.eventId !== 'string' || typeof value.linkerVersion !== 'string') {
    throw new Error('Related market response identity is invalid');
  }
  if (!Array.isArray(value.markets)) throw new Error('Related market response is missing markets');
  if (!isRecord(value.counts) || !Array.isArray(value.limitations)) {
    throw new Error('Related market response metadata is invalid');
  }
  return {
    ...(value as unknown as HazardMarketLinksResponse),
    markets: value.markets.map(parseMarket),
  };
}

