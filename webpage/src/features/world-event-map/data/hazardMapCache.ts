import type { HazardMapResponse } from '../domain/types';

export const HAZARD_MAP_SOURCE_KEYS = [
  'usgs',
  'usgs-volcano-cap',
  'nhc',
  'eonet',
  'gdacs',
  'nws',
  'firms',
  'climate-anomaly',
] as const;

export type HazardMapSourceKey = typeof HAZARD_MAP_SOURCE_KEYS[number];

type HazardCacheEntry = {
  cacheKey: string;
  source: HazardMapSourceKey;
  geometryZoom: number;
  scope?: string;
  storedAt: number;
  payload: HazardMapResponse;
};

const DATABASE_NAME = 'polymonitor-world-event-map';
const STORE_NAME = 'hazard-source-snapshots';
const DATABASE_VERSION = 3;
const LOCAL_STORAGE_PREFIX = 'polymonitor:hazard-map:last-good:';

export function hazardMapGeometryZoom(zoom: number) {
  if (zoom < 3) return 2;
  if (zoom < 5) return 4;
  return 6;
}

function cacheKey(source: HazardMapSourceKey, geometryZoom: number, scope = '') {
  return `${source}:${geometryZoom}:${scope || 'global'}`;
}

function openDatabase(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === 'undefined') return Promise.resolve(null);
  return new Promise((resolve) => {
    let request: IDBOpenDBRequest;
    try {
      request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    } catch {
      resolve(null);
      return;
    }
    request.onupgradeneeded = (event) => {
      const database = request.result;
      if (database.objectStoreNames.contains(STORE_NAME) && event.oldVersion < 3) {
        database.deleteObjectStore(STORE_NAME);
      }
      if (!database.objectStoreNames.contains(STORE_NAME)) {
        database.createObjectStore(STORE_NAME, { keyPath: 'cacheKey' });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => resolve(null);
    request.onblocked = () => resolve(null);
  });
}

function localStorageKey(source: HazardMapSourceKey, geometryZoom: number, scope = '') {
  return `${LOCAL_STORAGE_PREFIX}${cacheKey(source, geometryZoom, scope)}`;
}

function readLocalFallback(source: HazardMapSourceKey, geometryZoom: number, scope = ''): HazardCacheEntry | null {
  if (typeof localStorage === 'undefined') return null;
  try {
    const value = JSON.parse(
      localStorage.getItem(localStorageKey(source, geometryZoom, scope)) || 'null',
    ) as HazardCacheEntry | null;
    return value?.source === source
      && value.geometryZoom === geometryZoom
      && (value.scope || '') === scope
      && value.payload ? value : null;
  } catch {
    return null;
  }
}

function writeLocalFallback(entry: HazardCacheEntry) {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(localStorageKey(entry.source, entry.geometryZoom, entry.scope), JSON.stringify(entry));
  } catch {
    // Storage can be disabled or full. Network data remains usable in memory.
  }
}

export async function readHazardMapSnapshot(
  source: HazardMapSourceKey,
  geometryZoom: number,
  scope = '',
): Promise<HazardCacheEntry | null> {
  const database = await openDatabase();
  if (!database) {
    return readLocalFallback(source, geometryZoom, scope)
      || (scope || geometryZoom === 2 ? null : readLocalFallback(source, 2));
  }
  return new Promise((resolve) => {
    const transaction = database.transaction(STORE_NAME, 'readonly');
    const store = transaction.objectStore(STORE_NAME);
    const request = store.get(cacheKey(source, geometryZoom, scope));
    request.onsuccess = () => {
      const exact = request.result as HazardCacheEntry | undefined;
      if (exact || scope || geometryZoom === 2) {
        resolve(exact || null);
        return;
      }
      const globalRequest = store.get(cacheKey(source, 2));
      globalRequest.onsuccess = () => resolve((globalRequest.result as HazardCacheEntry | undefined) || null);
      globalRequest.onerror = () => resolve(readLocalFallback(source, geometryZoom, scope) || readLocalFallback(source, 2));
    };
    request.onerror = () => resolve(
      readLocalFallback(source, geometryZoom, scope)
      || (scope || geometryZoom === 2 ? null : readLocalFallback(source, 2)),
    );
    transaction.oncomplete = () => database.close();
    transaction.onabort = () => database.close();
  });
}

export async function writeHazardMapSnapshot(
  source: HazardMapSourceKey,
  geometryZoom: number,
  payload: HazardMapResponse,
  scope = '',
) {
  const entry: HazardCacheEntry = {
    cacheKey: cacheKey(source, geometryZoom, scope),
    source,
    geometryZoom,
    scope,
    storedAt: Date.now(),
    payload,
  };
  const database = await openDatabase();
  if (!database) {
    writeLocalFallback(entry);
    return;
  }
  await new Promise<void>((resolve) => {
    const transaction = database.transaction(STORE_NAME, 'readwrite');
    transaction.objectStore(STORE_NAME).put(entry);
    transaction.oncomplete = () => {
      database.close();
      resolve();
    };
    transaction.onerror = () => {
      database.close();
      writeLocalFallback(entry);
      resolve();
    };
    transaction.onabort = () => {
      database.close();
      writeLocalFallback(entry);
      resolve();
    };
  });
}
