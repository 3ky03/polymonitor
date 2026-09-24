const BUILD_ID = '__POLYDATA_BUILD_ID__';
const PRECACHE = '__POLYDATA_PRECACHE__';
const SHELL_CACHE = `polydata-shell-${BUILD_ID}`;
const STATIC_CACHE = `polydata-static-${BUILD_ID}`;
const OFFLINE_URL = '/offline.html';

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(PRECACHE)));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys
        .filter((key) => key.startsWith('polydata-') && ![SHELL_CACHE, STATIC_CACHE].includes(key))
        .map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('message', (event) => {
  if (event.data?.type === 'SKIP_WAITING') self.skipWaiting();
});

const safeNotificationPath = (value) => {
  try {
    const target = new URL(typeof value === 'string' ? value : '/watchlist', self.location.origin);
    return target.origin === self.location.origin ? `${target.pathname}${target.search}${target.hash}` : '/watchlist';
  } catch {
    return '/watchlist';
  }
};

self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = { title: 'PolyMonitor', body: event.data?.text() || '' };
  }
  const title = typeof payload.title === 'string' && payload.title ? payload.title : 'PolyMonitor';
  const count = Number.isFinite(payload.count) ? payload.count : 1;
  event.waitUntil(self.registration.showNotification(title, {
    body: typeof payload.body === 'string' ? payload.body : '',
    icon: '/icons/polydata-monitor-192.png',
    badge: '/icons/polydata-monitor-192.png',
    tag: typeof payload.tag === 'string' ? payload.tag : 'polymonitor-alert',
    renotify: count > 1,
    data: { url: safeNotificationPath(payload.url) },
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const path = safeNotificationPath(event.notification.data?.url);
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(async (clients) => {
    const target = new URL(path, self.location.origin).href;
    for (const client of clients) {
      if (client.url === target && 'focus' in client) return client.focus();
    }
    for (const client of clients) {
      if ('navigate' in client && 'focus' in client) {
        await client.navigate(target);
        return client.focus();
      }
    }
    return self.clients.openWindow ? self.clients.openWindow(target) : undefined;
  }));
});

async function networkFirst(request) {
  const cache = await caches.open(SHELL_CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) await cache.put(request, response.clone());
    return response;
  } catch {
    return (await cache.match(request))
      || (await cache.match('/'))
      || (await cache.match(OFFLINE_URL))
      || Response.error();
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(STATIC_CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) await cache.put(request, response.clone());
  return response;
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Canonical market, auth and product data must never be served from a Service Worker cache.
  if (url.pathname === '/wm-api' || url.pathname.startsWith('/wm-api/')) {
    event.respondWith(fetch(request));
    return;
  }
  if (request.mode === 'navigate') {
    event.respondWith(networkFirst(request));
    return;
  }
  if (['script', 'style', 'image', 'font', 'worker'].includes(request.destination)) {
    event.respondWith(cacheFirst(request));
  }
});
