import {
  registerWebPushSubscription,
  revokeWebPushSubscription,
  type WebPushStatus,
} from '@/services/product';

export type BrowserPushState = {
  supported: boolean;
  permission: NotificationPermission | 'unsupported';
  subscribed: boolean;
};

const applicationServerKey = (value: string): Uint8Array<ArrayBuffer> => {
  const padding = '='.repeat((4 - (value.length % 4)) % 4);
  const binary = window.atob((value + padding).replace(/-/g, '+').replace(/_/g, '/'));
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
};

const supported = () =>
  typeof window !== 'undefined'
  && 'serviceWorker' in navigator
  && 'PushManager' in window
  && 'Notification' in window;

const currentRegistration = async () => {
  if (!supported()) throw new Error('WEB_PUSH_UNSUPPORTED');
  return navigator.serviceWorker.getRegistration();
};

const ensureRegistration = async () => {
  const current = await currentRegistration();
  return current || navigator.serviceWorker.register('/sw.js');
};

export async function getBrowserPushState(): Promise<BrowserPushState> {
  if (!supported()) {
    return { supported: false, permission: 'unsupported', subscribed: false };
  }
  const registration = await currentRegistration();
  const current = registration ? await registration.pushManager.getSubscription() : null;
  return {
    supported: true,
    permission: Notification.permission,
    subscribed: current !== null,
  };
}

export async function enableBrowserPush(status: WebPushStatus): Promise<WebPushStatus> {
  if (!supported()) throw new Error('WEB_PUSH_UNSUPPORTED');
  if (!status.available || !status.publicKey) throw new Error('WEB_PUSH_UNAVAILABLE');
  const permission = Notification.permission === 'granted'
    ? 'granted'
    : await Notification.requestPermission();
  if (permission !== 'granted') throw new Error('WEB_PUSH_PERMISSION_DENIED');
  const pushManager = (await ensureRegistration()).pushManager;
  let subscription = await pushManager.getSubscription();
  if (!subscription) {
    subscription = await pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: applicationServerKey(status.publicKey),
    });
  }
  try {
    return await registerWebPushSubscription(subscription.toJSON());
  } catch (error) {
    await subscription.unsubscribe().catch(() => false);
    throw error;
  }
}

export async function disableBrowserPush(): Promise<WebPushStatus | null> {
  if (!supported()) return null;
  const registration = await currentRegistration();
  const subscription = registration ? await registration.pushManager.getSubscription() : null;
  if (!subscription) return null;
  const endpoint = subscription.endpoint;
  const status = await revokeWebPushSubscription(endpoint);
  await subscription.unsubscribe();
  return status;
}
