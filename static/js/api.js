import { lang, tr } from './i18n.js';
// Thin wrapper around fetch for the Noxa API.
export class ApiError extends Error {
  constructor(status, message, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

// Serveur administré (Paramètres → sélecteur de serveur) : les requêtes d'administration faites depuis les pages
// d'administration partent vers ce serveur lié, relayées par celui-ci. Vide : ce serveur.
let target = null;
try { target = JSON.parse(sessionStorage.getItem('admin-server') || 'null'); } catch { target = null; }
export const adminTarget = () => target;
export function setAdminTarget(t) {
  target = t && t.id ? { id: String(t.id), name: t.name } : null;
  try { if (target) sessionStorage.setItem('admin-server', JSON.stringify(target)); else sessionStorage.removeItem('admin-server'); } catch { /* navigation privée */ }
}
const relayed = (path) => target && path.startsWith('/api/admin/') && path !== '/api/admin/servers' && location.hash.startsWith('#/admin');
// adresse à utiliser dans un lien (téléchargement) ou un fetch direct
export const adminUrl = (path) => (relayed(path) ? `${path}${path.includes('?') ? '&' : '?'}_server=${encodeURIComponent(target.id)}` : path);

// Réponses demandées par la page dès son arrivée (script de démarrage d'index.html), reprises une seule fois et
// seulement à l'ouverture : toute requête qui modifie quelque chose (connexion, choix du profil…) les oublie.
let boot = (window.__nexisBoot && window.__nexisBoot.req) || {};
const bootAt = (window.__nexisBoot && window.__nexisBoot.at) || 0;
function fromBoot(method, path) {
  if (method !== 'GET') { boot = {}; return null; }
  const p = boot[path];
  if (!p || Date.now() - bootAt > 20000) return null;
  delete boot[path];
  return p;
}

export async function api(method, path, body, { headers = {}, keepalive = false, quiet401 = false } = {}) {
  const early = !relayed(path) && fromBoot(method, path);
  const pre = early ? await early : null;
  if (pre) return finish(pre.status, pre.ok, pre.text, quiet401);
  // X-Lang : messages d'erreur du serveur dans la langue de l'interface
  const opts = { method, headers: { 'X-Seance': '1', 'X-Lang': lang, ...headers }, credentials: 'same-origin', keepalive };
  if (relayed(path)) opts.headers['X-Nexis-Server'] = target.id;
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new ApiError(0, tr('Serveur injoignable'));
  }
  return finish(res.status, res.ok, await res.text(), quiet401);
}

function finish(status, ok, text, quiet401) {
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }
  if (!ok) {
    if (status === 401 && !quiet401) onUnauthorized();
    throw new ApiError(status, (data && data.error) || tr('Erreur {status}', { status }), data);
  }
  return data;
}

export const get = (p, o) => api('GET', p, undefined, o);
export const post = (p, b, o) => api('POST', p, b ?? {}, o);
export const put = (p, b, o) => api('PUT', p, b ?? {}, o);
export const del = (p, o) => api('DELETE', p, undefined, o);
