import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..', 'src', 'locales');
const localeNames = ['en', 'zh'];

const readCatalog = async (name) => {
  const raw = await readFile(resolve(root, `${name}.json`), 'utf8');
  const value = JSON.parse(raw);
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${name}.json must contain one flat message object`);
  }
  for (const [key, message] of Object.entries(value)) {
    if (!/^[a-z][a-zA-Z0-9]*(?:\.[a-zA-Z0-9]+)+$/.test(key)) {
      throw new Error(`${name}.json contains unstable message key: ${key}`);
    }
    if (typeof message !== 'string' || !message.trim()) {
      throw new Error(`${name}.json contains an empty or non-string message: ${key}`);
    }
  }
  return value;
};

const catalogs = Object.fromEntries(await Promise.all(localeNames.map(async (name) => [name, await readCatalog(name)])));
const reference = Object.keys(catalogs.en).sort();
let failed = false;
for (const name of localeNames.slice(1)) {
  const keys = Object.keys(catalogs[name]).sort();
  const missing = reference.filter((key) => !keys.includes(key));
  const extra = keys.filter((key) => !reference.includes(key));
  if (missing.length || extra.length) {
    failed = true;
    console.error(`${name}: missing=${missing.join(',') || 'none'} extra=${extra.join(',') || 'none'}`);
  }
}
if (failed) process.exit(1);

const specialistRaw = await readFile(resolve(root, 'specialist.ts'), 'utf8');
const specialistMarker = 'export const specialistZh';
const markerIndex = specialistRaw.indexOf(specialistMarker);
if (markerIndex < 0) throw new Error('specialist.ts must export specialistZh');
const specialistSections = {
  en: specialistRaw.slice(0, markerIndex),
  zh: specialistRaw.slice(markerIndex),
};
const specialistCatalogs = Object.fromEntries(Object.entries(specialistSections).map(([name, source]) => {
  const entries = [...source.matchAll(/^\s*'([^']+)':\s*'((?:\\'|[^'])*)',?$/gm)]
    .map((match) => [match[1], match[2]]);
  const catalog = Object.fromEntries(entries);
  for (const [key, message] of entries) {
    if (!/^[a-z][a-zA-Z0-9-]*(?:\.[a-zA-Z0-9-]+)+$/.test(key)) {
      throw new Error(`specialist ${name} contains unstable message key: ${key}`);
    }
    if (!message.trim()) throw new Error(`specialist ${name} contains an empty message: ${key}`);
  }
  return [name, catalog];
}));
const specialistReference = Object.keys(specialistCatalogs.en).sort();
for (const name of localeNames.slice(1)) {
  const keys = Object.keys(specialistCatalogs[name]).sort();
  const missing = specialistReference.filter((key) => !keys.includes(key));
  const extra = keys.filter((key) => !specialistReference.includes(key));
  if (missing.length || extra.length) {
    console.error(`specialist ${name}: missing=${missing.join(',') || 'none'} extra=${extra.join(',') || 'none'}`);
    process.exit(1);
  }
  for (const key of specialistReference) {
    const placeholders = (message) => [...message.matchAll(/\{([a-zA-Z0-9_]+)\}/g)].map((match) => match[1]).sort().join(',');
    if (placeholders(specialistCatalogs.en[key]) !== placeholders(specialistCatalogs[name][key])) {
      console.error(`specialist ${name}: placeholder mismatch for ${key}`);
      process.exit(1);
    }
  }
}

console.log(`Locale contract OK: ${localeNames.length} locales, ${reference.length} core keys and ${specialistReference.length} specialist keys each.`);
