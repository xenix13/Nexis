// Notifications push sur cet appareil : inscription auprès du service du navigateur, puis du serveur Nexis.
import { get, post } from './api.js';
import { tr } from './i18n.js';

// Application Android : sa vue web ne fait pas Web Push, c'est l'application qui affiche les notifications
// (elle vient les chercher sur le serveur avec un jeton propre à l'appareil)
const android = () => window.NexisAndroid && typeof window.NexisAndroid.pushEnable === 'function';
const oldAndroid = () => window.NexisAndroid && !android(); // application 1.0 : à mettre à jour

export const pushSupported = () => android() || (window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window
  && 'Notification' in window);

const keyBytes = (b64) => {
  const s = atob(b64.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (b64.length % 4)) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
};

export async function pushState() {
  if (android()) {
    let st = {};
    try { st = JSON.parse(window.NexisAndroid.pushStatus()); } catch { /* ignore */ }
    if (st.registered && !st.allowed) return 'denied';
    return st.registered ? 'on' : 'off';
  }
  if (oldAndroid()) return 'update';
  if (!pushSupported()) return 'unsupported';
  if (Notification.permission === 'denied') return 'denied';
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  if (!sub) return 'off';
  const r = await get(`/api/push/key?endpoint=${encodeURIComponent(sub.endpoint)}`);
  return r.subscribed ? 'on' : 'off';
}

export async function enablePush() {
  if (android()) {
    const { token } = await post('/api/push/device');
    // l'application demande l'autorisation d'Android (version 13 et plus) puis répond
    const ok = await new Promise((resolve) => { window.onNexisPush = resolve; window.NexisAndroid.pushEnable(token); });
    // refusées : l'inscription reste, les messages arriveront dès qu'elles seront autorisées dans les réglages
    if (!ok) throw new Error(tr('Notifications refusées pour l’application Nexis : autorisez-les dans les réglages d’Android.'));
    return;
  }
  if (await Notification.requestPermission() !== 'granted') throw new Error(tr('Notifications refusées dans le navigateur'));
  const reg = await navigator.serviceWorker.ready;
  const { key } = await get('/api/push/key');
  if (!key) throw new Error(tr('Le serveur ne peut pas envoyer de notifications (openssl manquant ?)'));
  let sub = await reg.pushManager.getSubscription();
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
  await post('/api/push/subscribe', { endpoint: sub.endpoint });
}

// application Android : aller chercher tout de suite les messages (notification d'essai)
export function pushCheckNow() { if (android()) window.NexisAndroid.pushCheck(); }
export function openPushSettings() { if (android()) window.NexisAndroid.notificationSettings(); }

export async function disablePush() {
  if (android()) {
    const token = window.NexisAndroid.pushDisable();
    if (token) await post('/api/push/device/remove', { token }).catch(() => {});
    return;
  }
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  if (!sub) return;
  await post('/api/push/unsubscribe', { endpoint: sub.endpoint }).catch(() => {});
  await sub.unsubscribe();
}
