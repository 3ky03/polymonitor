import { describe, expect, it } from 'vitest';
import { parseRelatedWeatherMarkets } from './relatedMarkets';

function response(overrides: Record<string, unknown> = {}) {
  const evidence = { passed: true, level: 'contextual', reason: 'fixture evidence' };
  return {
    schemaVersion: 'hazard-market-links.v1',
    generatedAt: '2026-07-29T12:00:00Z',
    eventId: 'extreme-heat:nws:test',
    linkerVersion: 'hazard-weather-market-linker.v1',
    markets: [{
      marketId: 42,
      title: 'Highest temperature in Dallas on 2026-07-30?',
      marketFamily: 'highest_temperature',
      relationship: 'contextual',
      matchScore: 0.7,
      matchReasons: {
        type: evidence,
        space: evidence,
        time: evidence,
        metric: evidence,
      },
      matchedAt: '2026-07-29T12:00:00Z',
      linkerVersion: 'hazard-weather-market-linker.v1',
      target: { city: 'Dallas', date: '2026-07-30' },
      quote: { probability: 0.45 },
      oracle: { status: 'unknown', reason: 'not joined' },
    }],
    counts: { candidates: 1, matched: 1, returned: 1, rejected: 0 },
    limitations: ['Title similarity alone never creates a link.'],
    ...overrides,
  };
}

describe('related weather market response boundary', () => {
  it('accepts only links with all four evidence dimensions', () => {
    const parsed = parseRelatedWeatherMarkets(response());
    expect(parsed.markets[0]).toMatchObject({
      marketId: 42,
      relationship: 'contextual',
      matchReasons: {
        type: { passed: true },
        space: { passed: true },
        time: { passed: true },
        metric: { passed: true },
      },
    });
  });

  it('rejects a backend link with a failed evidence gate', () => {
    const payload = response();
    const markets = payload.markets as Array<Record<string, unknown>>;
    const first = markets[0] as Record<string, unknown>;
    first.matchReasons = {
      ...(first.matchReasons as Record<string, unknown>),
      metric: { passed: false, reason: 'not comparable' },
    };
    expect(() => parseRelatedWeatherMarkets(payload)).toThrow(/Rejected market/);
  });
});

