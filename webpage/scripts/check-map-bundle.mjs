import { readFileSync, readdirSync, statSync } from 'node:fs';
import { resolve } from 'node:path';

const dist = resolve(process.cwd(), 'dist');
const assets = resolve(dist, 'assets');
const files = readdirSync(assets);
const required = ['maplibre-', 'deck-stack-', 'map-geo-'];
const missing = required.filter((prefix) => !files.some((file) => file.startsWith(prefix) && file.endsWith('.js')));
if (missing.length) throw new Error(`Missing lazy map chunks: ${missing.join(', ')}`);

const html = readFileSync(resolve(dist, 'index.html'), 'utf8');
const forbiddenPreloads = files.filter((file) => required.some((prefix) => file.startsWith(prefix)) && html.includes(file));
if (forbiddenPreloads.length) {
  throw new Error(`Lazy map chunks leaked into entry HTML preload: ${forbiddenPreloads.join(', ')}`);
}

const entry = files
  .filter((file) => /^index-.*\.js$/.test(file))
  .map((file) => ({ file, size: statSync(resolve(assets, file)).size }))
  .sort((left, right) => right.size - left.size)[0];
if (!entry) throw new Error('Unable to locate the frontend entry chunk.');
const maxEntryBytes = 2_200_000;
if (entry.size > maxEntryBytes) {
  throw new Error(`Entry chunk ${entry.file} is ${entry.size} bytes; map split gate is ${maxEntryBytes}.`);
}

const sw = readFileSync(resolve(dist, 'sw.js'), 'utf8');
const lazyAssetsInPrecache = files.filter((file) => (
  /^(?:WorldEventMap|DeckMapRenderer|SvgMapRenderer|maplibre|deck-stack|map-tiles|map-geo)-/.test(file)
  && sw.includes(`/assets/${file}`)
));
if (lazyAssetsInPrecache.length) {
  throw new Error(`Service worker eagerly precaches lazy map assets: ${lazyAssetsInPrecache.join(', ')}`);
}

console.log(JSON.stringify({
  entry,
  lazyMapChunks: files.filter((file) => required.some((prefix) => file.startsWith(prefix))),
  preloadedLazyChunks: forbiddenPreloads.length,
  precachedLazyChunks: lazyAssetsInPrecache.length,
}, null, 2));
