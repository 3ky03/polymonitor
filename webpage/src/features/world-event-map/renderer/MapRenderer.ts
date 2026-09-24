import type { GeoEvent } from '../domain/types';
import type { WorldEventMapState } from '../state/mapState';

export type BasemapState =
  | 'idle'
  | 'initializing'
  | 'primary-ready'
  | 'local-fallback-ready'
  | 'renderer-fallback-ready'
  | 'failed';

export type MapHoverPosition = { x: number; y: number };
export type MapCountryTarget = {
  iso2: string;
  name: string;
  bounds: [[number, number], [number, number]];
};

export interface MapRendererCallbacks {
  onCameraChange: (camera: Pick<WorldEventMapState, 'center' | 'zoom'>) => void;
  onEventSelect: (eventId: string | null) => void;
  onCountrySelect: (country: MapCountryTarget | null, position?: MapHoverPosition) => void;
  onCountryContextMenu: (country: MapCountryTarget, position: MapHoverPosition) => void;
  onBasemapStateChange: (state: BasemapState) => void;
  onRendererFallbackRequested: (error: Error) => void;
  onLayerDegraded?: (layerId: string, error: Error) => void;
  onError: (error: Error) => void;
}

export interface MapRenderer {
  mount(container: HTMLElement, callbacks: MapRendererCallbacks): Promise<void>;
  setState(state: WorldEventMapState): void;
  setEvents(events: GeoEvent[]): void;
  resize(): void;
  setReducedMotion(reduced: boolean): void;
  fitCountry(country: MapCountryTarget): void;
  pause(): void;
  resume(): void;
  destroy(): void;
}
