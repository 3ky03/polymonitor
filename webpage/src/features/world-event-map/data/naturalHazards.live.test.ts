import { describe, expect, it } from 'vitest';
import { parseNaturalHazardsResponse } from './naturalHazards';

const liveUrl = process.env.POLYDATA_NATURAL_HAZARDS_TEST_URL;

describe('natural hazards live contract', () => {
  it.skipIf(!liveUrl)('accepts the deployed same-origin response', async () => {
    const response = await fetch(String(liveUrl), {
      headers: { Accept: 'application/json' },
    });
    expect(response.ok).toBe(true);
    const parsed = parseNaturalHazardsResponse(await response.json());
    expect(parsed.events.length).toBeGreaterThan(0);
    expect(parsed.response.sources.some((source) => source.status === 'ok')).toBe(true);
  });
});
