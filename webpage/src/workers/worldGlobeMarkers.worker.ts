/// <reference lib="webworker" />

import { buildMarkerPayload } from './worldGlobeMarkerPipeline';
import type { GlobeMarkerWorkerMessage, GlobeMarkerWorkerResult } from './worldGlobeMarkersTypes';

const workerScope = self as DedicatedWorkerGlobalScope;

workerScope.onmessage = (event: MessageEvent<GlobeMarkerWorkerMessage>) => {
  const message = event.data;
  if (message.type === 'DISPOSE') return;

  const result = buildMarkerPayload(message);
  const transfer: Transferable[] = [
    result.positions.buffer,
    result.colors.buffer,
    result.sizes.buffer,
    result.opacities.buffer,
    result.flags.buffer,
  ];
  workerScope.postMessage(result satisfies GlobeMarkerWorkerResult, { transfer });
};
