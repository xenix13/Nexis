// Nexis : service worker. Rend l'application installable et lui permet de s'ouvrir même quand le serveur
// est injoignable (message « Réessayer » au lieu d'une page d'erreur du navigateur).
// Toujours le réseau d'abord : après une mise à jour du serveur, on n'utilise jamais une ancienne version.
// Le cache ne sert que hors ligne. API, vidéos et images ne passent pas par ici (cache HTTP du navigateur).
// Le serveur remplace les deux lignes ci-dessous (version et liste des fichiers de l'interface).
const VERSION = 'dev';
const SHELL = ['/'];
const CACHE = `nexis-${VERSION}`;

self.addEventListener('install', (event) => {
  // cache: 'reload' : les fichiers viennent du serveur, jamais d'une copie gardée par le navigateur
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL.map((u) => new Request(u, { cache: 'reload' }))))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith('nexis-') && k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET' || req.headers.has('range')) return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith('/api/') || url.pathname.startsWith('/vendor/')
      || url.pathname === '/sw.js') return;
  event.respondWith(networkFirst(req));
});

async function networkFirst(req) {
  const key = req.mode === 'navigate' ? '/' : req; // application d'une seule page : toute navigation est « / »
  const cache = await caches.open(CACHE);
  try {
    const res = await fetch(req);
    if (res.ok && res.type === 'basic') cache.put(key, res.clone());
    // serveur en train de redémarrer (mise à jour) : le proxy répond 502 / 503 / 504. On ouvre quand même
    // l'interface, qui affiche « Nexis redémarre… » et se recharge seule.
    if (res.status >= 502 && res.status <= 504) return (await cache.match(key)) || res;
    return res;
  } catch (err) {
    const hit = await cache.match(key);
    if (hit) return hit;
    throw err;
  }
}
