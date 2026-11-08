// Notifications push sur cet appareil : inscription auprès du service du navigateur, puis du serveur Nexis.
import { get, post } from './api.js';
import { tr } from './i18n.js';

export const pushSupported = () => window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window
  && 'Notification' in window;

const keyBytes = (b64) => {
  const s = atob(b64.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (b64.length % 4)) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
};

export async function pushState() {
  if (!pushSupported()) return 'unsupported';
  if (Notification.permission === 'denied') return 'denied';
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  if (!sub) return 'off';
  const r = await get(`/api/push/key?endpoint=${encodeURIComponent(sub.endpoint)}`);
  return r.subscribed ? 'on' : 'off';
}

export async function enablePush() {
  if (await Notification.requestPermission() !== 'granted') throw new Error(tr('Notifications refusées dans le navigateur'));
  const reg = await navigator.serviceWorker.ready;
  const { key } = await get('/api/push/key');
  if (!key) throw new Error(tr('Le serveur ne peut pas envoyer de notifications (openssl manquant ?)'));
  let sub = await reg.pushManager.getSubscription();
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
  await post('/api/push/subscribe', { endpoint: sub.endpoint });
}

export async function disablePush() {
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  if (!sub) return;
  await post('/api/push/unsubscribe', { endpoint: sub.endpoint }).catch(() => {});
  await sub.unsubscribe();
}
