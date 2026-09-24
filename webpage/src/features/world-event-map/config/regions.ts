export type WorldEventRegion =
  | 'global'
  | 'america'
  | 'mena'
  | 'eu'
  | 'asia'
  | 'latam'
  | 'africa'
  | 'oceania';

export type WorldEventRegionPreset = {
  id: WorldEventRegion;
  center: { lon: number; lat: number };
  zoom: number;
};

export const WORLD_EVENT_REGION_PRESETS: readonly WorldEventRegionPreset[] = [
  // Matches the primary vector style's country-label disclosure threshold.
  { id: 'global', center: { lon: 8, lat: 18 }, zoom: 1.5 },
  { id: 'america', center: { lon: -90, lat: 25 }, zoom: 2.15 },
  { id: 'mena', center: { lon: 41, lat: 27 }, zoom: 3 },
  { id: 'eu', center: { lon: 13, lat: 51 }, zoom: 3 },
  { id: 'asia', center: { lon: 101, lat: 29 }, zoom: 2.35 },
  { id: 'latam', center: { lon: -67, lat: -15 }, zoom: 2.2 },
  { id: 'africa', center: { lon: 22, lat: 2 }, zoom: 2.25 },
  { id: 'oceania', center: { lon: 145, lat: -25 }, zoom: 2.4 },
] as const;

export function worldEventRegionPreset(region: string) {
  return WORLD_EVENT_REGION_PRESETS.find((preset) => preset.id === region)
    || WORLD_EVENT_REGION_PRESETS[0]!;
}

export function isWorldEventRegion(value: unknown): value is WorldEventRegion {
  return WORLD_EVENT_REGION_PRESETS.some((preset) => preset.id === value);
}
