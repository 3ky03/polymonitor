type InstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
};

export type PwaState = {
  online: boolean;
  installed: boolean;
  installable: boolean;
  installing: boolean;
  updateReady: boolean;
};

const listeners = new Set<() => void>();
let installPrompt: InstallPromptEvent | null = null;
let waitingWorker: ServiceWorker | null = null;
let initialized = false;
let state: PwaState = {
  online: typeof navigator === 'undefined' ? true : navigator.onLine,
  installed: typeof window !== 'undefined' && window.matchMedia('(display-mode: standalone)').matches,
  installable: false,
  installing: false,
  updateReady: false,
};

const emit = (patch: Partial<PwaState>) => {
  state = { ...state, ...patch };
  listeners.forEach((listener) => listener());
};

export const getPwaState = () => state;
export const subscribePwa = (listener: () => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};

export async function installPwa() {
  if (!installPrompt) return;
  emit({ installing: true });
  try {
    await installPrompt.prompt();
    const choice = await installPrompt.userChoice;
    if (choice.outcome === 'accepted') {
      installPrompt = null;
      emit({ installed: true, installable: false });
    }
  } finally {
    emit({ installing: false });
  }
}

export function activatePwaUpdate() {
  waitingWorker?.postMessage({ type: 'SKIP_WAITING' });
}

function watchRegistration(registration: ServiceWorkerRegistration) {
  if (registration.waiting && navigator.serviceWorker.controller) {
    waitingWorker = registration.waiting;
    emit({ updateReady: true });
  }
  registration.addEventListener('updatefound', () => {
    const worker = registration.installing;
    if (!worker) return;
    worker.addEventListener('statechange', () => {
      if (worker.state === 'installed' && navigator.serviceWorker.controller) {
        waitingWorker = worker;
        emit({ updateReady: true });
      }
    });
  });
}

export function registerPwa() {
  if (initialized || typeof window === 'undefined') return;
  initialized = true;
  if (new URLSearchParams(window.location.search).get('mapPerf') === '1') return;
  window.addEventListener('online', () => emit({ online: true }));
  window.addEventListener('offline', () => emit({ online: false }));
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    installPrompt = event as InstallPromptEvent;
    emit({ installable: true });
  });
  window.addEventListener('appinstalled', () => {
    installPrompt = null;
    emit({ installed: true, installable: false, installing: false });
  });
  if (!('serviceWorker' in navigator) || import.meta.env.DEV) return;
  navigator.serviceWorker.addEventListener('controllerchange', () => window.location.reload());
  navigator.serviceWorker.register(`/sw.js?build=${encodeURIComponent(__BUILD_ID__)}`, {
    scope: '/',
    updateViaCache: 'none',
  }).then((registration) => {
    watchRegistration(registration);
    window.setInterval(() => void registration.update(), 60 * 60 * 1000);
  }).catch((error) => {
    console.warn('[PWA] service worker registration failed', error);
  });
}
