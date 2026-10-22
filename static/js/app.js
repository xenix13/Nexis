import { get, post, setUnauthorizedHandler } from './api.js';
import { avatar, clear, errorBox, h, icon, spinner, toast } from './ui.js';
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

export const state = { me: null, status: null };
const root = document.getElementById('app');
let cleanup = null;
let renderToken = 0;

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
    h('a', { href: '#/history' }, 'Historique'),
    h('a', { href: '#/settings' }, 'Réglages'),
    me.user.is_admin ? h('a', { href: '#/admin' }, 'Administration') : null,
    h('button', { onclick: logout }, 'Se déconnecter'));
  const userBtn = h('button.user-btn', { 'aria-label': 'Menu utilisateur', 'aria-haspopup': 'true',
    onclick: (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; } }, avatar(me.user, 34));
  const header = h('header.topbar',
    h('a.logo', { href: '#/' }, h('span.logo-mark'), 'Séance'),
    h('nav.nav', navLink('#/', 'Accueil', active === 'home'), navLink('#/watchlist', 'À regarder', active === 'watchlist'),
      me.features && me.features.discover ? navLink('#/discover', 'Découvrir', active === 'discover') : null,
      me.libraries.map((l) => navLink(`#/library/${l.id}`, l.title, active === `lib:${l.id}`))),
    h('div.topbar-right', h('label.search', icon('search'), search), h('div.user-wrap', userBtn, menu)));
  const main = h('main.content');
  const banner = me.plex_error ? h('div.banner-error', me.plex_error) : null;
  clear(root, header, banner, main);
  return main;
}

async function logout() {
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
    document.title = 'Installation · Séance';
    return renderSetup(root, () => { state.status = null; state.me = null; location.hash = '#/admin/libraries'; route(); });
  }
  if (page === 'login') {
    document.title = 'Connexion · Séance';
    return renderLogin(root, async () => { await refreshMe(); location.hash = '#/profiles'; });
  }
  if (page === 'profiles') {
    document.title = 'Qui regarde ? · Séance';
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

  const ctx = { query, parts, isCurrent: () => token === renderToken, setCleanup: (fn) => { cleanup = fn; } };
  if (page === 'play') {
    document.body.classList.add('playing');
    const stopPlayer = await renderPlayer(root, parts[1], ctx);
    if (token === renderToken) cleanup = stopPlayer;
    else stopPlayer();
    return;
  }
  document.body.classList.remove('playing');
  const active = page === '' ? 'home' : page === 'library' ? `lib:${parts[1]}` : page;
  const main = shell(active);
  main.append(spinner());
  const titles = { '': 'Accueil', watchlist: 'À regarder', discover: 'Découvrir', playlists: 'Mes playlists', search: 'Recherche', history: 'Historique', settings: 'Réglages', admin: 'Administration' };
  document.title = `${titles[page] || 'Séance'} · Séance`;
  try {
    switch (page) {
      case '': await renderHome(main, ctx); break;
      case 'library': await renderLibrary(main, parts[1], ctx); break;
      case 'item': await renderItem(main, parts[1], ctx); break;
      case 'search': await renderSearch(main, query.get('q') || '', ctx); break;
      case 'collection': await renderCollection(main, parts[1], ctx); break;
      case 'watchlist': await renderWatchlist(main, ctx); break;
      case 'discover': await renderDiscover(main, ctx); break;
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
window.addEventListener('hashchange', route);
route();
