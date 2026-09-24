const CLUSTER_COUNT_TILE_WIDTH = 36;
const CLUSTER_COUNT_TILE_HEIGHT = 24;
const CLUSTER_COUNT_COLUMNS = 16;
const CLUSTER_COUNT_VALUES = [
  ...Array.from({ length: 98 }, (_, index) => String(index + 2)),
  '100+',
] as const;

export type MapClusterCountIcon = (typeof CLUSTER_COUNT_VALUES)[number];

export const MAP_CLUSTER_COUNT_ICON_MAPPING = Object.fromEntries(
  CLUSTER_COUNT_VALUES.map((value, index) => [value, {
    x: (index % CLUSTER_COUNT_COLUMNS) * CLUSTER_COUNT_TILE_WIDTH,
    y: Math.floor(index / CLUSTER_COUNT_COLUMNS) * CLUSTER_COUNT_TILE_HEIGHT,
    width: CLUSTER_COUNT_TILE_WIDTH,
    height: CLUSTER_COUNT_TILE_HEIGHT,
    anchorX: CLUSTER_COUNT_TILE_WIDTH / 2,
    anchorY: CLUSTER_COUNT_TILE_HEIGHT / 2,
    mask: false,
  }]),
) as Record<MapClusterCountIcon, {
  x: number;
  y: number;
  width: number;
  height: number;
  anchorX: number;
  anchorY: number;
  mask: false;
}>;

function clusterCountSvgAtlas() {
  const rows = Math.ceil(CLUSTER_COUNT_VALUES.length / CLUSTER_COUNT_COLUMNS);
  const width = CLUSTER_COUNT_COLUMNS * CLUSTER_COUNT_TILE_WIDTH;
  const height = rows * CLUSTER_COUNT_TILE_HEIGHT;
  const content = CLUSTER_COUNT_VALUES.map((value, index) => {
    const x = (index % CLUSTER_COUNT_COLUMNS) * CLUSTER_COUNT_TILE_WIDTH;
    const y = Math.floor(index / CLUSTER_COUNT_COLUMNS) * CLUSTER_COUNT_TILE_HEIGHT;
    const fontSize = value.length > 2 ? 9 : 11;
    return `<g transform="translate(${x} ${y})">`
      + '<rect x="1" y="2" width="34" height="20" rx="5" fill="#040a0e" fill-opacity="0.94" stroke="#dceeed" stroke-opacity="0.72"/>'
      + `<text x="18" y="16" text-anchor="middle" font-family="DejaVu Sans Mono,monospace" font-size="${fontSize}" font-weight="700" fill="#ecf8f6">${value}</text>`
      + '</g>';
  }).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">${content}</svg>`;
}

export const MAP_CLUSTER_COUNT_ATLAS = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(clusterCountSvgAtlas())}`;

export function mapClusterCountIcon(count: number): MapClusterCountIcon {
  if (!Number.isFinite(count) || count >= 100) return '100+';
  return String(Math.max(2, Math.round(count))) as MapClusterCountIcon;
}
