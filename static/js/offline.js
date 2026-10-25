// Téléchargements pour regarder hors ligne.
// - Le serveur prépare un MP4 (H.264 / AAC, lisible partout) ; on le récupère par morceaux de 8 Mo, ce qui permet
//   de reprendre après une coupure ou une fermeture de l'application.
// - Les morceaux, l'affiche et les sous-titres vont dans le stockage du navigateur (Cache API) ; la fiche et
//   l'avancement dans IndexedDB. Rien ne quitte l'appareil.
// - Hors ligne, les positions de lecture sont gardées puis renvoyées au serveur au retour du réseau.
import { del, get, post } from './api.js';
import { img } from './ui.js';

const DB_NAME = 'nexis-offline';
const MEDIA = 'nexis-offline-media';
const CHUNK = 8 * 1024 * 1024;
const SAFE_CAPS = { video: ['h264'], audio: ['aac', 'mp3'], containers: ['mp4'] }; // relisible sans conversion

let dbPromise = null;
function db() {
  dbPromise = dbPromise || new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      req.result.createObjectStore('downloads', { keyPath: 'key' });
      req.result.createObjectStore('progress', { keyPath: 'key' });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbPromise;
}
async function tx(store, mode, fn) {
  const d = await db();
  return new Promise((resolve, reject) => {
    const t = d.transaction(store, mode);
    const out = fn(t.objectStore(store));
    t.oncomplete = () => resolve(out && 'result' in out ? out.result : out);
    t.onerror = () => reject(t.error);
  });
}
const all = (store) => tx(store, 'readonly', (s) => s.getAll());
const put = (store, value) => tx(store, 'readwrite', (s) => s.put(value));
const remove = (store, key) => tx(store, 'readwrite', (s) => s.delete(key));
const one = (store, key) => tx(store, 'readonly', (s) => s.get(key));

export const supported = () => 'indexedDB' in window && 'caches' in window && window.isSecureContext;

// ------------------------------------------------------------------ qui regarde (aussi hors ligne)
const ME_KEY = 'nexis.offline.me';
export function rememberMe(me) {
  try { localStorage.setItem(ME_KEY, JSON.stringify({ user: me.user, prefs: me.prefs, libraries: [], features: {} })); } catch { /* ignore */ }
}
export function forgetMe() {
  try { localStorage.removeItem(ME_KEY); } catch { /* ignore */ }
}
export function rememberedMe() {
  try { return JSON.parse(localStorage.getItem(ME_KEY) || 'null'); } catch { return null; }
}

// ------------------------------------------------------------------ abonnement (pages qui affichent l'état)
const listeners = new Set();
export function onDownloads(fn) { listeners.add(fn); return () => listeners.delete(fn); }
const notify = () => listeners.forEach((fn) => { try { fn(); } catch (e) { console.warn(e); } });

const keyOf = (uid, rk) => `${uid}:${rk}`;
const mediaUrl = (key, part) => `/offline/${key.replace(':', '/')}/${part}`;

export async function list(uid) {
  if (!supported()) return [];
  return (await all('downloads')).filter((d) => d.uid === uid)
    .sort((a, b) => (a.item.show || a.item.title).localeCompare(b.item.show || b.item.title, 'fr')
      || (a.item.season || 0) - (b.item.season || 0) || (a.item.episode || 0) - (b.item.episode || 0));
}
export const find = (uid, rk) => (supported() ? one('downloads', keyOf(uid, rk)) : Promise.resolve(null));

// ------------------------------------------------------------------ demander un téléchargement
// opts : { quality, audio (id), subs: [ids des sous-titres texte à garder], burn (id d'un sous-titre image) }
export async function download(uid, item, opts) {
  if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {}); // pas d'effacement par le navigateur
  const key = keyOf(uid, item.id);
  const existing = await one('downloads', key);
  if (existing && existing.state !== 'error') return existing;
  const subs = (item.subtitles || []).filter((s) => s.overlay && (opts.subs || []).includes(s.id));
  const rec = {
    key, uid, rk: String(item.id), item: { ...item, subtitles: subs.map((s) => ({ ...s, external: true })),
      audio: (item.audio || []).filter((a) => a.id === opts.audio).slice(0, 1) },
    opts, state: 'queued', job: null, size: 0, got: 0, chunks: 0, prep: 0, error: null, savedAt: Date.now(),
  };
  await put('downloads', rec);
  notify();
  pump();
  return rec;
}

// ------------------------------------------------------------------ file : une préparation / un transfert à la fois
let running = false;
export async function pump() {
  if (running || !supported() || !navigator.onLine) return;
  running = true;
  try {
    for (;;) {
      const next = (await all('downloads')).find((d) => ['queued', 'preparing', 'downloading', 'paused'].includes(d.state));
      if (!next) break;
      const ok = await work(next);
      if (!ok) break; // réseau coupé : on reprendra au retour de la connexion
    }
  } finally {
    running = false;
  }
}

async function save(rec, patch) {
  Object.assign(rec, patch);
  await put('downloads', rec);
  notify();
}

async function work(rec) {
  const cache = await caches.open(MEDIA);
  try {
    if (!rec.posterSaved) {
      const art = rec.item.type === 'episode' ? (rec.item.thumb || rec.item.show_thumb) : rec.item.thumb;
      if (art) {
        const res = await fetch(img(art, 400, rec.item.type === 'episode' ? 225 : 600));
        if (res.ok) await cache.put(mediaUrl(rec.key, 'poster'), res);
      }
      for (const s of rec.item.subtitles) {  // sous-titres texte : les répliques, déjà converties par le serveur
        let r = await get(`/api/items/${rec.rk}/subtitles/${s.id}`);
        for (let i = 0; i < 90 && r && (r.pending || r.partial); i++) {  // encore en extraction sur le serveur
          await new Promise((ok) => setTimeout(ok, 2000));
          r = await get(`/api/items/${rec.rk}/subtitles/${s.id}`);
        }
        await cache.put(mediaUrl(rec.key, `subs-${s.id}`), new Response(JSON.stringify({ cues: r.cues || [] }),
          { headers: { 'Content-Type': 'application/json' } }));
      }
      await save(rec, { posterSaved: true });
    }
    // 1. préparation sur le serveur
    for (;;) {
      let job;
      try {
        job = rec.job ? await get(`/api/downloads/${rec.job}`) : null;
      } catch (e) {
        if (e.status !== 404) throw e;
        job = null; // serveur redémarré : on redemande
      }
      if (!job) {
        job = await post('/api/downloads', { id: rec.rk, quality: rec.opts.quality, audio: rec.opts.audio ?? null,
          burn: rec.opts.burn ?? null, caps: SAFE_CAPS });
        await save(rec, { job: job.id, state: 'preparing', got: 0, chunks: 0 });
      }
      if (job.state === 'error') { await save(rec, { state: 'error', error: job.error }); return true; }
      if (job.state === 'ready') {
        if (!rec.chunks || rec.size !== job.size) await save(rec, { size: job.size, chunks: Math.ceil(job.size / CHUNK), got: 0 });
        break;
      }
      await save(rec, { state: 'preparing', prep: job.progress || 0 });
      await new Promise((r) => setTimeout(r, 2500));
      const fresh = await one('downloads', rec.key);
      if (!fresh) return true; // supprimé entre-temps
    }
    // 2. transfert par morceaux (reprend là où il s'était arrêté)
    await save(rec, { state: 'downloading' });
    for (let n = rec.got; n < rec.chunks; n++) {
      if (!(await one('downloads', rec.key))) return true; // supprimé entre-temps
      const start = n * CHUNK;
      const end = Math.min(rec.size, start + CHUNK) - 1;
      const res = await fetch(`/api/downloads/${rec.job}/file`, { headers: { Range: `bytes=${start}-${end}`, 'X-Seance': '1' },
        credentials: 'same-origin' });
      if (res.status === 404) { await save(rec, { job: null, state: 'queued', got: 0 }); return true; }
      if (res.status !== 206 && res.status !== 200) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      if (blob.size !== end - start + 1) throw new Error('morceau incomplet');
      await cache.put(mediaUrl(rec.key, `chunk-${n}`), new Response(blob));
      await save(rec, { got: n + 1 });
    }
    del(`/api/downloads/${rec.job}`).catch(() => {}); // le serveur peut effacer sa copie
    await save(rec, { state: 'done', job: null, doneAt: Date.now() });
    return true;
  } catch (e) {
    if (e.name === 'QuotaExceededError') {
      await save(rec, { state: 'error', error: 'Plus assez de place sur cet appareil' });
      return true;
    }
    if (!navigator.onLine || e.status === 0 || e instanceof TypeError) {
      await save(rec, { state: 'paused' });
      return false;
    }
    await save(rec, { state: 'error', error: e.message });
    return true;
  }
}

export async function removeDownload(key) {
  const rec = await one('downloads', key);
  if (!rec) return;
  await remove('downloads', key);
  if (rec.job) del(`/api/downloads/${rec.job}`).catch(() => {});
  const cache = await caches.open(MEDIA);
  const prefix = mediaUrl(key, '');
  for (const req of await cache.keys()) {
    if (new URL(req.url).pathname.startsWith(prefix)) await cache.delete(req);
  }
  notify();
}

export async function retry(key) {
  const rec = await one('downloads', key);
  if (!rec) return;
  await save(rec, { state: 'queued', error: null, job: null, got: 0 });
  pump();
}

// ------------------------------------------------------------------ lecture hors ligne
export async function playable(uid, rk) {
  const rec = await one('downloads', keyOf(uid, rk));
  if (!rec || rec.state !== 'done') return null;
  const cache = await caches.open(MEDIA);
  const parts = [];
  for (let n = 0; n < rec.chunks; n++) {
    const res = await cache.match(mediaUrl(rec.key, `chunk-${n}`));
    if (!res) return null; // effacé par le navigateur (place manquante)
    parts.push(await res.blob());
  }
  const urls = [];
  const blobUrl = (b) => { const u = URL.createObjectURL(b); urls.push(u); return u; };
  const subUrls = {};
  for (const s of rec.item.subtitles) {
    const res = await cache.match(mediaUrl(rec.key, `subs-${s.id}`));
    if (res) subUrls[s.id] = blobUrl(await res.blob());
  }
  const posterRes = await cache.match(mediaUrl(rec.key, 'poster'));
  return {
    rec,
    item: rec.item,
    url: blobUrl(new Blob(parts, { type: 'video/mp4' })),
    subUrls,
    poster: posterRes ? blobUrl(await posterRes.blob()) : null,
    release: () => urls.forEach((u) => URL.revokeObjectURL(u)),
  };
}

export async function posterUrl(rec) {
  const res = await (await caches.open(MEDIA)).match(mediaUrl(rec.key, 'poster'));
  return res ? URL.createObjectURL(await res.blob()) : null;
}

// Épisode suivant… parmi les téléchargements
export async function nextDownloaded(uid, item) {
  if (item.type !== 'episode') return null;
  const eps = (await list(uid)).filter((d) => d.state === 'done' && d.item.show_id === item.show_id);
  const after = eps.filter((d) => (d.item.season - item.season || d.item.episode - item.episode) > 0);
  return after.length ? after[0].item : null;
}

// ------------------------------------------------------------------ positions de lecture hors ligne
export async function saveProgress(uid, item, position, duration, ended) {
  await put('progress', { key: keyOf(uid, item.id), id: String(item.id), uid, position, duration, ended: !!ended, at: Math.floor(Date.now() / 1000) });
  const rec = await one('downloads', keyOf(uid, item.id));
  if (rec) await put('downloads', { ...rec, item: { ...rec.item, position: ended ? 0 : position, watched: rec.item.watched || !!ended } });
  syncProgress(uid);
}

let syncing = false;
export async function syncProgress(uid) {
  if (syncing || !navigator.onLine || !supported()) return;
  syncing = true;
  try {
    const pending = (await all('progress')).filter((p) => p.uid === uid);
    if (!pending.length) return;
    await post('/api/progress/offline', { items: pending });
    await Promise.all(pending.map((p) => remove('progress', p.key)));
  } catch { /* on réessaiera */ } finally {
    syncing = false;
  }
}

export async function usage() {
  try { return await navigator.storage.estimate(); } catch { return null; }
}
