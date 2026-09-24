import type { GeoEventSeverity } from '../domain/types';
import {
  MAP_SEVERITY_STYLES,
  MAP_SYMBOL_DEFINITIONS,
  MAP_SYMBOL_PALETTES,
  MAP_SYMBOL_SIZE,
  type MapSymbolKey,
} from '../config/mapSymbols';

export function MapSymbolIcon({
  symbol,
  color = 'currentColor',
  severity,
  size = 14,
  label,
  className,
  framed,
}: {
  symbol: MapSymbolKey;
  color?: string;
  severity?: GeoEventSeverity;
  size?: number;
  label?: string;
  className?: string;
  framed?: boolean;
}) {
  const definition = MAP_SYMBOL_DEFINITIONS[symbol];
  const palette = MAP_SYMBOL_PALETTES[symbol];
  const severityStyle = severity ? MAP_SEVERITY_STYLES[severity] : null;
  const showFrame = framed ?? Boolean(severityStyle);
  const glyphColor = color === 'currentColor' ? palette.primary : color;
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox={`0 0 ${MAP_SYMBOL_SIZE} ${MAP_SYMBOL_SIZE}`}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : 'true'}
      focusable="false"
    >
      {showFrame ? (
        <circle
          cx="24"
          cy="24"
          r="20.5"
          fill={palette.surface}
          fillOpacity="0.92"
          stroke={severityStyle?.color || palette.primary}
          strokeOpacity={severityStyle ? 1 : 0.52}
          strokeWidth={(severityStyle?.lineWidth || 1) * 1.45}
        />
      ) : null}
      {severity === 'critical' && showFrame ? (
        <circle
          cx="24"
          cy="24"
          r="17.3"
          fill="none"
          stroke={severityStyle?.color}
          strokeOpacity="0.72"
          strokeWidth="1.1"
        />
      ) : null}
      <g
        fill={glyphColor}
        fillRule="evenodd"
        stroke={palette.secondary}
        strokeWidth="0.65"
        paintOrder="stroke"
      >
        {definition.paths.map((path) => <path key={path} d={path} />)}
      </g>
    </svg>
  );
}
