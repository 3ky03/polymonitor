import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { defineConfig, loadEnv, type Plugin } from 'vite';
import preact from '@preact/preset-vite';
import { resolve } from 'path';

const LAZY_MAP_ASSET_RE = /(?:WorldEventMap|DeckMapRenderer|SvgMapRenderer|maplibre|deck-stack|map-tiles|map-geo)-[A-Za-z0-9_-]+\.(?:js|css)$/;

function repositorySha() {
  try {
    return execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  } catch {
    return 'development';
  }
}

function pwaServiceWorker(buildId: string): Plugin {
  return {
    name: 'polydata-pwa-service-worker',
    apply: 'build',
    generateBundle(_options, bundle) {
      const generatedAssets = Object.keys(bundle)
        .filter((name) => /\.(?:css|js)$/.test(name))
        // The map renderer is demand-loaded. Precaching it during SW install
        // would compete with first paint and defeat the lazy chunk boundary.
        .filter((name) => !LAZY_MAP_ASSET_RE.test(name))
        .map((name) => `/${name}`);
      const precache = [
        '/',
        '/offline.html',
        '/site.webmanifest',
        '/icons/polydata-monitor.svg',
        '/icons/polydata-monitor-192.png',
        '/icons/polydata-monitor-512.png',
        ...generatedAssets,
      ];
      const template = readFileSync(resolve(__dirname, 'src/pwa/sw-template.js'), 'utf8');
      this.emitFile({
        type: 'asset',
        fileName: 'sw.js',
        source: template
          .replace("'__POLYDATA_BUILD_ID__'", JSON.stringify(buildId))
          .replace("'__POLYDATA_PRECACHE__'", JSON.stringify(precache)),
      });
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = {
    ...loadEnv(mode, resolve(__dirname, '..'), ''),
    ...loadEnv(mode, process.cwd(), ''),
    ...process.env,
  };
  const apiHost = env.POLYDATA_API_HOST || '127.0.0.1';
  const apiPort = env.POLYDATA_API_PORT || '5000';
  const apiBase = env.VITE_POLYDATA_API_BASE_URL || '';
  const target = env.VITE_POLYDATA_PROXY_TARGET
    || (apiBase.startsWith('http') ? apiBase : `http://${apiHost}:${apiPort}`);
  const mapTilesTarget = env.POLYDATA_MAP_TILES_TARGET || 'https://maps.worldmonitor.app';
  const buildId = String(env.GITHUB_SHA || env.POLYDATA_BUILD_SHA || repositorySha()).slice(0, 40);

  const mapTilesProxy = {
    target: mapTilesTarget,
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/map-tiles/, ''),
  };

  return {
    plugins: [preact(), pwaServiceWorker(buildId)],
    define: {
      __BUILD_ID__: JSON.stringify(buildId),
    },
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src'),
      },
    },
    server: {
      port: 3000,
      proxy: {
        '/wm-api': {
          target,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/wm-api/, ''),
        },
        '/map-tiles': mapTilesProxy,
      },
    },
    preview: {
      proxy: {
        '/wm-api': {
          target,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/wm-api/, ''),
        },
        '/map-tiles': mapTilesProxy,
      },
    },
    build: {
      modulePreload: {
        resolveDependencies: (_filename, dependencies, context) => (
          context.hostType === 'html'
            ? dependencies.filter((dependency) => !LAZY_MAP_ASSET_RE.test(dependency))
            : dependencies
        ),
      },
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (id.includes('/maplibre-gl/')) return 'maplibre';
            if (id.includes('/@deck.gl/')
              || id.includes('/deck.gl/')
              || id.includes('/@luma.gl/')
              || id.includes('/@loaders.gl/')
              || id.includes('/@math.gl/')) return 'deck-stack';
            if (id.includes('/pmtiles/') || id.includes('/@protomaps/basemaps/')) return 'map-tiles';
            if (id.includes('/supercluster/') || id.includes('/d3-geo/')) return 'map-geo';
            return undefined;
          },
        },
      },
    },
  };
});
