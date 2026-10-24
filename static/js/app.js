import { get, post, setUnauthorizedHandler } from './api.js';
import { arrange, avatar, clear, errorBox, h, icon, spinner, toast } from './ui.js';
import { renderLogin, renderSetup } from './views/auth.js';
import { renderProfiles } from './views/profiles.js';
import { renderCollection, renderPlaylist, renderPlaylists, renderWatchlist } from './views/lists.js';
import { renderDiscover } from './views/discover.js';
import { renderHome } from './views/home.js';
import { renderLibrary } from './views/library.js';
import { renderItem } from './views/item.js';
import { renderSearch } from './views/search.js';
import { renderHistory } from './views/history.js';
import { renderSettings } from './views/settings.js';
import { renderAdmin } from './views/admin.js';
import { renderPlayer } from './player.js';
import { renderTogether } from './views/together.js';
import { renderLive, renderLivePlayer } from './views/live.js';

export const state = { me: null, status: null };
const root = document.getElementById('app');
let cleanup = null;
let renderToken = 0;
// lecteur réduit : il continue pendant la navigation, dans un coin de l'écran (en dehors de #app)
let mini = null;

function closeMini() {
  if (!mini) return;
  const m = mini;
  mini = null;
  m.stop();
  m.el.remove();
}

function minimize(m) {
  let dock = document.getElementById('mini-dock');
  if (!dock) {
    dock = h('div', { id: 'mini-dock' });
    document.body.append(dock);
  }
  closeMini();
  mini = m;
  cleanup = null; // la page suivante ne doit pas arrêter la lecture
  dock.append(m.el);
}

export function applyPrefs(prefs) {
  const r = document.documentElement;
  r.style.setProperty('--accent', prefs.accent);
  r.dataset.theme = prefs.theme;
  r.dataset.poster = prefs.poster_size;
}

export async function refreshMe() {
  state.me = await get('/api/me');
  applyPrefs(state.me.prefs);
  return state.me;
}

function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const [path, qs] = raw.split('?');
  return { parts: path.split('/').filter(Boolean), query: new URLSearchParams(qs || '') };
}

function navLink(href, label, active) {
  return h('a.nav-link', { href, class: active ? 'nav-link active' : 'nav-link' }, label);
}

// Liens de la barre du haut, dans l'ordre choisi par l'utilisateur (Réglages → Affichage)
export function navItems(me) {
  const items = [{ key: 'home', href: '#/', label: 'Accueil', fixed: true }, { key: 'watchlist', href: '#/watchlist', label: 'À regarder' }];
  if (me.features && me.features.discover) items.push({ key: 'discover', href: '#/discover', label: 'Découvrir' });
  for (const l of me.libraries) items.push({ key: `lib:${l.id}`, href: `#/library/${l.id}`, label: l.title });
  if (me.features && me.features.live) items.push({ key: 'live', href: '#/live', label: 'TV en direct' });
  const byKey = new Map(items.map((i) => [i.key, i]));
  return arrange(items.map((i) => i.key), me.prefs.nav_order).map((k) => byKey.get(k));
}

function navLinks(me, active) {
  return navItems(me).filter((i) => i.fixed || !me.prefs.nav_hidden.includes(i.key))
    .map((i) => navLink(i.href, i.label, active === i.key));
}

// après un changement dans Réglages : on redessine les liens sans recharger la page
export function repaintNav() {
  const nav = document.querySelector('header.topbar .nav');
  if (nav && state.me) nav.replaceChildren(...navLinks(state.me, null));
}

function shell(active) {
  const me = state.me;
  const search = h('input.search-input', { type: 'search', placeholder: 'Rechercher…', 'aria-label': 'Rechercher',
    value: active === 'search' ? (parseHash().query.get('q') || '') : '' });
  let t;
  search.addEventListener('input', () => {
    clearTimeout(t);
    t = setTimeout(() => {
      const q = search.value.trim();
      if (q.length >= 2) location.hash = `#/search?q=${encodeURIComponent(q)}`;
    }, 350);
  });
  const menu = h('div.user-menu', { hidden: true },
    h('div.user-menu-head', avatar(me.user, 36), h('div', h('strong', me.user.display_name),
      h('div.muted', `@${me.user.username}`))),
    me.user.home ? h('a', { href: '#/profiles' }, 'Changer de profil') : null,
    h('a', { href: '#/playlists' }, 'Mes playlists'),
    h('a', { href: '#/together' }, 'Regarder ensemble'),
    h('a', { href: '#/history' }, 'Historique'),
    h('a', { href: '#/settings' }, 'Réglages'),
    me.user.is_admin ? h('a', { href: '#/admin' }, 'Administration') : null,
    h('button', { onclick: logout }, 'Se déconnecter'));
  const userBtn = h('button.user-btn', { 'aria-label': 'Menu utilisateur', 'aria-haspopup': 'true',
    onclick: (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; } }, avatar(me.user, 34));
  const header = h('header.topbar',
    h('a.logo', { href: '#/', 'aria-label': 'Nexis — accueil' }, h('span.logo-mark', { 'aria-hidden': 'true' }), 'Nexis'),
    h('nav.nav', navLinks(me, active)),
    h('div.topbar-right', h('span.together-slot'), h('label.search', icon('search'), search), h('div.user-wrap', userBtn, menu)));
  // groupes « Regarder ensemble » en cours : un raccourci discret pour les rejoindre
  get('/api/together').then((groups) => {
    const slot = header.querySelector('.together-slot');
    if (slot && groups.length && active !== 'together') {
      slot.replaceWith(h('a.group-pill', { href: '#/together', title: 'Regarder ensemble' }, icon('group'),
        groups.length === 1 ? `${groups[0].owner} regarde` : `${groups.length} groupes`));
    }
  }).catch(() => {});
  const main = h('main.content');
  const banner = me.plex_error ? h('div.banner-error', me.plex_error) : null;
  clear(root, header, banner, main);
  // barre transparente au-dessus de l'image d'accueil, opaque dès qu'on fait défiler
  const onScroll = () => header.classList.toggle('scrolled', window.scrollY > 24);
  onScroll();
  window.removeEventListener('scroll', window.__nexisScroll || (() => {}));
  window.__nexisScroll = onScroll;
  window.addEventListener('scroll', onScroll, { passive: true });
  return main;
}

async function logout() {
  closeMini();
  try { await post('/api/logout'); } catch { /* ignore */ }
  state.me = null;
  location.hash = '#/login';
}

async function route() {
  const token = ++renderToken;
  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }
  const { parts, query } = parseHash();
  const page = parts[0] || '';
  window.scrollTo(0, 0);

  if (!state.status) {
    try { state.status = await get('/api/status'); } catch (e) {
      clear(root, h('div.center-page', errorBox(e, () => location.reload())));
      return;
    }
  }
  if (state.status.setup) {
    document.title = 'Installation · Nexis';
    return renderSetup(root, () => { state.status = null; state.me = null; location.hash = '#/admin/libraries'; route(); });
  }
  if (page === 'login') {
    document.title = 'Connexion · Nexis';
    return renderLogin(root, async () => { await refreshMe(); location.hash = '#/profiles'; });
  }
  if (page === 'profiles') {
    document.title = 'Qui regarde ? · Nexis';
    if (!state.me) {
      try { await refreshMe(); } catch { location.hash = '#/login'; return; }
    }
    document.body.classList.remove('playing');
    return renderProfiles(root, async () => { await refreshMe(); location.replace('#/'); });
  }
  if (!state.me) {
    try { await refreshMe(); } catch (e) {
      if (e.status === 401) { location.hash = '#/login'; return; }
      clear(root, h('div.center-page', errorBox(e, route)));
      return;
    }
  }
  if (token !== renderToken) return;

  const ctx = { query, parts, isCurrent: () => token === renderToken, setCleanup: (fn) => { cleanup = fn; },
    minimize, closeMini };
  if (page === 'play' && mini && mini.itemId === parts[1]) {
    // on agrandit le lecteur réduit : la lecture continue sans recharger
    document.body.classList.add('playing');
    const m = mini;
    mini = null;
    m.restore(root);
    cleanup = m.stop;
    return;
  }
  if (page === 'play' || page === 'tv') {
    closeMini(); // une autre vidéo remplace celle du lecteur réduit
    document.body.classList.add('playing');
    const stopPlayer = page === 'tv' ? await renderLivePlayer(root, parts[1], ctx) : await renderPlayer(root, parts[1], ctx);
    if (token === renderToken) cleanup = stopPlayer;
    else stopPlayer();
    return;
  }
  document.body.classList.remove('playing');
  const active = page === '' ? 'home' : page === 'library' ? `lib:${parts[1]}` : page;
  const main = shell(active);
  main.append(spinner());
  const titles = { '': 'Accueil', live: 'TV en direct', together: 'Regarder ensemble', watchlist: 'À regarder', discover: 'Découvrir', playlists: 'Mes playlists', search: 'Recherche', history: 'Historique', settings: 'Réglages', admin: 'Administration' };
  document.title = `${titles[page] || 'Nexis'} · Nexis`;
  try {
    switch (page) {
      case '': await renderHome(main, ctx); break;
      case 'library': await renderLibrary(main, parts[1], ctx); break;
      case 'item': await renderItem(main, parts[1], ctx); break;
      case 'search': await renderSearch(main, query.get('q') || '', ctx); break;
      case 'collection': await renderCollection(main, parts[1], ctx); break;
      case 'watchlist': await renderWatchlist(main, ctx); break;
      case 'discover': await renderDiscover(main, ctx); break;
      case 'live': await renderLive(main, ctx); break;
      case 'together': await renderTogether(main, parts[1], ctx); break;
      case 'playlists': await renderPlaylists(main, ctx); break;
      case 'playlist': await renderPlaylist(main, parts[1], ctx); break;
      case 'history': await renderHistory(main, ctx); break;
      case 'settings': await renderSettings(main, ctx); break;
      case 'admin':
        if (!state.me.user.is_admin) { location.hash = '#/'; return; }
        await renderAdmin(main, parts[1] || 'libraries', ctx); break;
      default: clear(main, h('div.empty', 'Page introuvable.'));
    }
  } catch (e) {
    if (token === renderToken) clear(main, errorBox(e, route));
  }
}

setUnauthorizedHandler(() => {
  state.me = null;
  if (!location.hash.startsWith('#/login')) {
    toast('Session expirée, reconnecte-toi');
    location.hash = '#/login';
  }
});

document.addEventListener('error', (e) => {
  if (e.target && e.target.tagName === 'IMG') e.target.classList.add('broken');
}, true);
document.addEventListener('click', () => {
  document.querySelectorAll('.user-menu').forEach((m) => { m.hidden = true; });
});
// Images des pages : fondu quand elles sont chargées (celles déjà en cache s'affichent tout de suite).
function fadeImage(im) {
  if (im.complete || im.classList.contains('fade') || im.closest('.player')) return;
  im.classList.add('fade');
  const done = () => im.classList.add('in');
  im.addEventListener('load', done, { once: true });
  im.addEventListener('error', done, { once: true });
  setTimeout(done, 8000); // filet de sécurité : jamais d'image restée invisible
}
new MutationObserver((records) => {
  for (const r of records) {
    for (const n of r.addedNodes) {
      if (n.nodeType !== 1) continue;
      if (n.tagName === 'IMG') fadeImage(n);
      else n.querySelectorAll('img').forEach(fadeImage);
    }
  }
}).observe(document.getElementById('app') || document.body, { childList: true, subtree: true });

window.addEventListener('hashchange', route);
route();
