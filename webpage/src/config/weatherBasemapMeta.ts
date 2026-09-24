export type WeatherMapTheme = 'dark' | 'dark-matter' | 'positron';

export const OPENFREEMAP_DARK_STYLE = 'https://tiles.openfreemap.org/styles/dark';
export const OPENFREEMAP_LIGHT_STYLE = 'https://tiles.openfreemap.org/styles/positron';
export const CARTO_DARK_STYLE = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';
export const CARTO_LIGHT_STYLE = 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json';
export const WORLD_EVENT_PMTILES_URL = (import.meta.env.VITE_PMTILES_URL || '').trim();

export function getWeatherBasemapAttribution(provider: 'auto' | 'pmtiles' | 'openfreemap' | 'carto' = 'auto') {
  const resolved = provider === 'auto' ? (WORLD_EVENT_PMTILES_URL ? 'pmtiles' : 'openfreemap') : provider;
  if (resolved === 'pmtiles') return '© Protomaps · © OpenStreetMap contributors';
  if (resolved === 'carto') return '© CARTO · © OpenStreetMap contributors';
  return '© OpenFreeMap · © OpenStreetMap contributors';
}
