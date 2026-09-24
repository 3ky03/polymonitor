import type { RuntimeGeoSanctionsShockPayload } from '@/types';
import type { CountryGeometryIndex } from '../domain/countryGeometry';
import type { GeoEvent, GeoEventAdapterResult } from '../domain/types';
import { validateGeoEvents } from '../domain/validation';
import {
  finiteNumber,
  isoTimestamp,
  nonEmpty,
  normalizedSeverity,
  normalizedSourceStatus,
  stableId,
} from './shared';

export function adaptGeoShockPayload(payload?: RuntimeGeoSanctionsShockPayload | null): GeoEventAdapterResult {
  const provider = nonEmpty(payload?.conflictProvider) || nonEmpty(payload?.source) || 'ucdp';
  const sourceStatus = normalizedSourceStatus(payload?.conflictState || payload?.status);
  const candidates: GeoEvent[] = [];
  const missingId: GeoEventAdapterResult['rejected'] = [];

  (payload?.items || []).forEach((item, index) => {
    const id = stableId(provider, item.id);
    if (!id) {
      missingId.push({ index, code: 'missing-stable-id', message: 'conflict event has no provider-native id' });
      return;
    }
    const longitude = finiteNumber(item.longitude);
    const latitude = finiteNumber(item.latitude);
    const hasPoint = longitude != null
      && latitude != null
      && longitude >= -180
      && longitude <= 180
      && latitude >= -90
      && latitude <= 90;
    const country = nonEmpty(item.country);
    const title = nonEmpty(item.headline)
      || [nonEmpty(item.sideA), nonEmpty(item.sideB)].filter(Boolean).join(' vs ')
      || nonEmpty(item.locationLabel)
      || 'Conflict event';
    candidates.push({
      id,
      category: String(item.kind || '').toLowerCase().includes('unrest') ? 'unrest' : 'conflict',
      title,
      summary: nonEmpty(item.summary),
      severity: normalizedSeverity(item.severity),
      occurredAt: isoTimestamp(item.occurredAt),
      geometry: hasPoint ? { type: 'Point', coordinates: [longitude, latitude] } : undefined,
      locationPrecision: hasPoint ? 'exact' : country ? 'country' : 'unknown',
      locationLabel: nonEmpty(item.locationLabel) || country,
      sources: [{
        provider,
        url: nonEmpty(item.sourceUrl) || nonEmpty(payload?.sourceUrl),
        nativeId: nonEmpty(item.id),
        observedAt: isoTimestamp(item.occurredAt),
        status: sourceStatus,
      }],
      limitations: [
        'Fatality estimates and conflict classification may be revised by the source.',
        ...(hasPoint ? [] : ['The source did not provide renderable point geometry.']),
      ],
      relatedMarketIds: [],
      properties: {
        mapEntity: 'conflict-event',
        country,
        sideA: nonEmpty(item.sideA),
        sideB: nonEmpty(item.sideB),
        deathsBest: finiteNumber(item.deathsBest) ?? 0,
        deathsLow: finiteNumber(item.deathsLow),
        deathsHigh: finiteNumber(item.deathsHigh),
        violenceType: nonEmpty(item.violenceType),
        rawSeverity: nonEmpty(item.severity),
        tags: item.tags || [],
      },
    });
  });

  const validated = validateGeoEvents(candidates);
  return { events: validated.events, rejected: [...missingId, ...validated.rejected] };
}

function countryRiskSeverity(count: number) {
  if (count >= 25) return 'warning' as const;
  return 'watch' as const;
}

export function adaptGeoShockCountryRiskPayload(
  payload: RuntimeGeoSanctionsShockPayload | null | undefined,
  countries: CountryGeometryIndex | null,
): GeoEventAdapterResult {
  if (!payload || !countries) return { events: [], rejected: [] };
  const provider = nonEmpty(payload.source) || 'OFAC / Federal Register / UCDP';
  const sourceStatus = normalizedSourceStatus(payload.status);
  const candidates: GeoEvent[] = [];
  const rejected: GeoEventAdapterResult['rejected'] = [];
  type Breakdown = NonNullable<RuntimeGeoSanctionsShockPayload['targetBreakdown']>[number];
  type CountryEvidence = {
    country: NonNullable<ReturnType<CountryGeometryIndex['resolve']>>;
    sanctions?: Breakdown;
    countryRisk?: Breakdown;
    legacyMixedEvidence: boolean;
  };
  const byCountry = new Map<string, CountryEvidence>();
  const hasSplitContract = Array.isArray(payload.sanctionsTargetBreakdown)
    || Array.isArray(payload.countryRiskBreakdown);
  const sanctionsBreakdown = payload.sanctionsTargetBreakdown || [];
  const countryRiskBreakdown = payload.countryRiskBreakdown
    || (hasSplitContract ? [] : payload.targetBreakdown || []);

  const addBreakdown = (
    target: Breakdown,
    kind: 'sanctions' | 'countryRisk',
    index: number,
  ) => {
    const label = nonEmpty(target.label);
    const country = countries.resolve(label);
    if (!label || !country) {
      rejected.push({
        index,
        code: 'unresolved-country-geometry',
        message: `risk target is not a verified country identity: ${label || 'unknown'}`,
      });
      return;
    }
    const count = Math.max(0, finiteNumber(target.count) ?? 0);
    if (!count) {
      rejected.push({
        index,
        code: 'empty-country-risk-evidence',
        message: `risk target has no source-backed evidence count: ${label}`,
      });
      return;
    }
    const current = byCountry.get(country.iso2) || {
      country,
      legacyMixedEvidence: !hasSplitContract,
    };
    current[kind] = target;
    byCountry.set(country.iso2, current);
  };
  sanctionsBreakdown.forEach((target, index) => addBreakdown(target, 'sanctions', index));
  countryRiskBreakdown.forEach((target, index) => addBreakdown(
    target,
    'countryRisk',
    sanctionsBreakdown.length + index,
  ));

  byCountry.forEach(({ country, sanctions, countryRisk, legacyMixedEvidence }) => {
    const sanctionsCount = Math.max(0, finiteNumber(sanctions?.count) ?? 0);
    const countryRiskCount = Math.max(0, finiteNumber(countryRisk?.count) ?? 0);
    const category = sanctions ? 'sanctions' as const : 'country-risk' as const;
    const records = [sanctions, countryRisk].filter((item): item is Breakdown => Boolean(item));
    const latest = [...records].sort((left, right) => (
      Date.parse(String(right.latestOccurredAt || '')) - Date.parse(String(left.latestOccurredAt || ''))
    ))[0];
    const sourceRecords = [
      sanctions ? {
        provider: nonEmpty(sanctions.latestSource) || 'OFAC / Federal Register',
        nativeId: `sanctions-country:${country.iso2}`,
        observedAt: isoTimestamp(sanctions.latestOccurredAt),
      } : null,
      countryRisk ? {
        provider: nonEmpty(countryRisk.latestSource) || nonEmpty(payload.conflictProvider) || 'Conflict feed',
        nativeId: `country-risk:${country.iso2}`,
        observedAt: isoTimestamp(countryRisk.latestOccurredAt),
      } : null,
    ].filter((source): source is NonNullable<typeof source> => source !== null);
    candidates.push({
      id: `geo-sanctions-shock:${country.iso2}`,
      category,
      title: sanctions && countryRisk
        ? `Sanctions & country risk: ${country.name}`
        : sanctions
          ? `Sanctions activity: ${country.name}`
        : `Country risk evidence: ${country.name}`,
      summary: records
        .map((record) => nonEmpty(record.latestHeadline))
        .filter((headline): headline is string => Boolean(headline))
        .filter((headline, index, values) => values.indexOf(headline) === index)
        .join(' · ') || nonEmpty(payload.summary?.targetSummary),
      severity: countryRiskSeverity(Math.max(sanctionsCount, countryRiskCount)),
      occurredAt: isoTimestamp(latest?.latestOccurredAt),
      updatedAt: isoTimestamp(payload.generatedAt),
      geometry: country.geometry,
      locationPrecision: 'country',
      countryCode: country.iso2,
      locationLabel: country.name,
      sources: sourceRecords.map((source) => ({
        provider: source.provider || provider,
        url: nonEmpty(payload.sourceUrl),
        nativeId: source.nativeId,
        observedAt: source.observedAt,
        status: sourceStatus,
      })),
      limitations: [
        'The polygon is country-level evidence, not an incident footprint.',
        'Sanctions evidence and conflict evidence use separate counts; the values are not added or compared as one metric.',
        'Sanctions records are source monitoring evidence and are not legal advice.',
        ...(legacyMixedEvidence
          ? ['This cached payload predates the split-source contract; its mixed target score is shown only as country-risk evidence.']
          : []),
      ],
      relatedMarketIds: [],
      properties: {
        mapEntity: 'country-risk-area',
        country: country.name,
        countryCode: country.iso2,
        evidenceCount: Math.max(sanctionsCount, countryRiskCount),
        sanctionsEvidenceCount: sanctionsCount,
        countryRiskEvidenceCount: countryRiskCount,
        latestHeadline: nonEmpty(latest?.latestHeadline),
        latestSource: nonEmpty(latest?.latestSource),
        ofacRecordCountTotal: finiteNumber(payload.ofacRecordCountTotal),
        globalNewSanctionsCount: finiteNumber(payload.summary?.newSanctionsCount),
        riskMappingVersion: 'geo-shock-country-risk.v1',
        sourceContract: hasSplitContract ? 'geo-shock-split-evidence.v1' : 'geo-shock-legacy-mixed',
      },
    });
  });

  const validated = validateGeoEvents(candidates);
  return { events: validated.events, rejected: [...rejected, ...validated.rejected] };
}
