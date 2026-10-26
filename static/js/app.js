import { get, post, setUnauthorizedHandler } from './api.js';
import { arrange, avatar, clear, episodeCode, errorBox, h, icon, img, spinner, toast } from './ui.js';
import { renderLogin, renderSetup } from './views/auth.js';
import { renderProfiles } from './views/profiles.js';
import { renderCollection, renderPlaylist, renderPlaylists, renderWatchlist } from './views/lists.js';
import { renderDiscover } from './views/discover.js';
import { renderHome } from './views/home.js';
import { renderLibrary } from './views/library.js';
import { renderItem } from './views/item.js';
import { renderSearch } from './views/search.js';
import { renderHistory } from './views/history.js';
import { renderSettingsArea } from './views/settings.js';
import { renderPlayer } from './player.js';
import { renderTogether } from './views/together.js';
import './remote.js'; // télécommande, clavier et manette
import { applyDevice } from './device.js';
import { createGroupDialog, currentGroups, isFreshFeed, onGroups, startGroupsFeed, stopGroupsFeed } from './groups.js';
import { canInstall, install, onInstallChange, setupInstall } from './install.js';
import { forgetMe, list as listDownloads, pump, rememberMe, rememberedMe, supported as offlineSupported, syncProgress } from './offline.js';
import { renderDownloads } from './views/downloads.js';
import { followUpdate, resumeUpdate, updatePending } from './updater.js';
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
  const kids = !!(state.me && state.me.user && state.me.user.kids);
  r.classList.toggle('kids', kids); // interface enfant : grandes vignettes, menu simplifié
  r.dataset.poster = kids ? 'l' : prefs.poster_size;
  r.classList.toggle('no-spoilers', !!prefs.hide_spoilers);
  // barre d'état du téléphone / de la fenêtre installée à la couleur du thème
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = getComputedStyle(r).getPropertyValue('--bg').trim() || '#0b0b0e';
}

export async function refreshMe() {
  state.me = await get('/api/me');
  state.offline = false;
  applyPrefs(state.me.prefs);
  rememberMe(state.me); // pour retrouver ses téléchargements hors ligne
  syncProgress(state.me.user.id); // positions enregistrées hors ligne
  pump(); // téléchargements à reprendre
  return state.me;
}

// Hors ligne (serveur injoignable) : on bascule sur les téléchargements de la dernière personne connectée
async function offlineMode(e, page, parts, token) {
  const me = rememberedMe();
  if (e.status !== 0 || !me || !offlineSupported() || !(await listDownloads(me.user.id)).length) return false;
  if (token !== renderToken) return true;
  state.me = me;
  state.offline = true;
  applyPrefs(me.prefs);
  const ctx = { query: new URLSearchParams(), parts, isCurrent: () => token === renderToken,
    setCleanup: (fn) => { cleanup = fn; }, offline: true };
  if (page === 'offline' && parts[1]) {
    document.body.classList.add('playing');
    document.title = 'Nexis';
    const stopPlayer = await renderPlayer(root, parts[1], ctx);
    if (token === renderToken) cleanup = stopPlayer; else stopPlayer();
    return true;
  }
  document.body.classList.remove('playing');
  document.title = 'Téléchargements · Nexis';
  const main = h('main.content');
  clear(root, h('header.topbar.offline-bar', h('span.logo', h('span.logo-mark', { 'aria-hidden': 'true' }), 'Nexis'),
    h('div.grow'), h('button.btn.small', { onclick: () => location.reload() }, 'Réessayer la connexion')), main);
  await renderDownloads(main, ctx, { offline: true });
  return true;
}
// anti-spoiler : un clic sur un résumé masqué l'affiche
document.addEventListener('click', (e) => {
  const sp = e.target.closest && e.target.closest('.no-spoilers .spoiler:not(.revealed)');
  if (sp) { sp.classList.add('revealed'); e.preventDefault(); }
});
window.addEventListener('online', () => {
  if (!state.offline) return;
  state.status = null;
  state.me = null;
  toast('Connexion retrouvée', 'ok');
  route();
});

// Le proxy (nginx…) répond « Bad Gateway » le temps que Nexis redémarre : on réessaie tout seul
function restarting(token) {
  clear(root, h('div.center-page', h('div.restart-box', spinner(), h('h2', 'Nexis redémarre…'),
    h('p.muted', 'La page se rechargera automatiquement dès que le serveur répond.'))));
  const retry = async () => {
    if (token !== renderToken) return;
    try { await get('/api/status'); } catch { setTimeout(retry, 3000); return; }
    route();
  };
  setTimeout(retry, 3000);
}

function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const [path, qs] = raw.split('?');
  return { parts: path.split('/').filter(Boolean), query: new URLSearchParams(qs || '') };
}

function navLink(href, label, active) {
  return h('a.nav-link', { href, class: active ? 'nav-link active' : 'nav-link' }, label);
}

// Liens de la barre du haut, dans l'ordre choisi par l'utilisateur (Paramètres → Accueil et menu)
let startPending = true;
applyDevice(); // réglages propres à cet appareil (animations, télécommande)

export function navItems(me) {
  const items = [{ key: 'home', href: '#/', label: 'Accueil', fixed: true }, { key: 'watchlist', href: '#/watchlist', label: 'À regarder' }];
  const kids = me.user && me.user.kids;
  if (me.features && me.features.discover && !kids) items.push({ key: 'discover', href: '#/discover', label: 'Découvrir' });
  for (const l of me.libraries) items.push({ key: `lib:${l.id}`, href: `#/library/${l.id}`, label: l.title });
  if (me.features && me.features.live && !kids) items.push({ key: 'live', href: '#/live', label: 'TV en direct' });
  const byKey = new Map(items.map((i) => [i.key, i]));
  return arrange(items.map((i) => i.key), me.prefs.nav_order).map((k) => byKey.get(k));
}

function navLinks(me, active) {
  return navItems(me).filter((i) => i.fixed || !me.prefs.nav_hidden.includes(i.key))
    .map((i) => navLink(i.href, i.label, active === i.key));
}

// après un changement dans Réglages : on redessine les liens sans recharger la page
// Nouveaux épisodes des séries suivies : cloche avec le nombre de non-lus, liste au clic (marquée lue à l'ouverture)
function notifBell(me) {
  const badge = h('span.notif-badge', { hidden: !me.notifications }, String(me.notifications || ''));
  const list = h('div.notif-menu.user-menu', { hidden: true, role: 'menu' });
  const btn = h('button.icon-btn.notif-btn', { type: 'button', 'aria-label': 'Notifications', title: 'Nouveaux épisodes',
    'aria-haspopup': 'true', onclick: async (e) => {
      e.stopPropagation();
      document.querySelectorAll('.user-menu').forEach((m) => { if (m !== list) m.hidden = true; });
      list.hidden = !list.hidden;
      if (list.hidden) return;
      list.replaceChildren(h('div.notif-head', 'Nouveaux épisodes'), spinner());
      try {
        const r = await get('/api/notifications');
        list.replaceChildren(h('div.notif-head', 'Nouveaux épisodes'),
          ...(r.items.length ? r.items.map((n) => h('a.notif-item', { href: `#/item/${n.show.id}`, class: n.unread ? 'notif-item unread' : 'notif-item' },
            n.show.thumb ? h('img', { src: img(n.show.thumb, 80, 120), alt: '', loading: 'lazy' }) : h('span.noimg'),
            h('span.notif-text', h('strong', n.show.title),
              h('span.muted', n.count > 1 ? `${n.count} nouveaux épisodes` : `Nouvel épisode : ${episodeCode(n.episode)} · ${n.episode.title}`))))
            : [h('p.muted.small.notif-empty', 'Aucun nouvel épisode pour les séries suivies.')]),
          h('p.muted.small.notif-foot', 'Séries suivies : celles dont un épisode a été regardé, ou ajoutées à « À regarder ».'));
        if (r.unread) {
          await post('/api/notifications/seen');
          me.notifications = 0;
          badge.hidden = true;
        }
      } catch (err) { list.replaceChildren(h('p.form-error.small', err.message)); }
    } }, icon('bell'), badge);
  return h('div.user-wrap.notif-wrap', btn, list);
}

// Nom ou photo du profil modifiés : la barre du haut suit sans recharger la page
export function repaintUser() {
  const me = state.me;
  if (!me) return;
  const btn = document.querySelector('.user-btn .avatar');
  if (btn) btn.replaceWith(avatar(me.user, 34));
  const head = document.querySelector('.user-menu-head');
  if (head) head.replaceChildren(avatar(me.user, 36), h('div', h('strong', me.user.display_name), h('div.muted', `@${me.user.username}`)));
}

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
    me.features && me.features.downloads && offlineSupported() ? h('a', { href: '#/downloads' }, 'Téléchargements') : null,
    h('a', { href: '#/settings' }, 'Paramètres'),
    h('button.install-app', { hidden: !canInstall(), onclick: () => install() }, icon('install'), 'Installer l’application'),
    h('button', { onclick: logout }, 'Se déconnecter'),
    state.status ? h('div.menu-version', `Nexis ${state.status.version}`) : null);
  const userBtn = h('button.user-btn', { 'aria-label': 'Menu utilisateur', 'aria-haspopup': 'true',
    onclick: (e) => {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
      const first = !menu.hidden && menu.querySelector('a, button:not([hidden])');
      if (first) first.focus({ preventScroll: true });
    } }, avatar(me.user, 34));
  const header = h('header.topbar',
    // application installée : plus de bouton « précédent » du navigateur, on en met un
    h('button.icon-btn.topbar-back', { type: 'button', 'aria-label': 'Retour', title: 'Retour', hidden: active === 'home',
      onclick: () => { if (history.length > 1) history.back(); else location.hash = '#/'; } }, icon('back')),
    h('a.logo', { href: '#/', 'aria-label': 'Nexis — accueil' }, h('span.logo-mark', { 'aria-hidden': 'true' }), 'Nexis'),
    h('nav.nav', navLinks(me, active)),
    h('div.topbar-right', h('span.together-slot', { dataset: { active } }),
      h('button.icon-btn.group-new', { type: 'button', title: 'Créer un groupe « Regarder ensemble »', 'aria-label': 'Créer un groupe',
        onclick: () => createGroupDialog(me) }, icon('group'), h('span.plus', '+')),
      me.update ? h('a.icon-btn.update-btn', { href: '#/admin/update', title: `Nexis ${me.update} est disponible`,
        'aria-label': `Mise à jour disponible : Nexis ${me.update}` }, icon('upgrade')) : null,
      h('label.search', icon('search'), search), notifBell(me), h('div.user-wrap', userBtn, menu)));
  paintTogetherSlot(header.querySelector('.together-slot'));
  const main = h('main.content');
  const banner = me.warning ? h('div.banner-error', me.warning) : null;
  clear(root, header, banner, main);
  // barre transparente au-dessus de l'image d'accueil, opaque dès qu'on fait défiler
  const onScroll = () => header.classList.toggle('scrolled', window.scrollY > 24);
  onScroll();
  window.removeEventListener('scroll', window.__nexisScroll || (() => {}));
  window.__nexisScroll = onScroll;
  window.addEventListener('scroll', onScroll, { passive: true });
  return main;
}

// Raccourci « Regarder ensemble » de la barre du haut, tenu à jour par la liste en direct
function paintTogetherSlot(slot, list = currentGroups()) {
  if (!slot) return;
  const mine = list.find((g) => g.member);
  let pill = null;
  if (mine) {
    pill = h('a.group-pill.mine', { href: `#/together/${mine.id}`, title: `Votre groupe : ${mine.name}` }, icon('group'),
      mine.item ? mine.name : `${mine.name} · salon`, h('span.group-count', String(mine.members.length)));
  } else if (list.length && slot.dataset.active !== 'together') {
    pill = h('a.group-pill', { href: '#/together', title: 'Regarder ensemble' }, icon('group'),
      list.length === 1 ? `${list[0].owner} regarde` : `${list.length} groupes`);
  }
  slot.replaceChildren(...(pill ? [pill] : []));
}
onGroups((list) => paintTogetherSlot(document.querySelector('.topbar .together-slot'), list));
// lecture lancée dans un groupe dont on fait partie, pendant qu'on navigue ailleurs : on propose de la rejoindre
const launched = new Map();
onGroups((list) => {
  const fresh = isFreshFeed(); // liste rouverte (retour du lecteur…) : on ne sait pas ce qu'on a manqué, on ne propose rien
  for (const g of list) {
    const was = launched.get(g.id);
    const now = g.member && g.item && !g.item.hidden ? g.item.id : null;
    launched.set(g.id, now);
    if (now && was === null && !fresh && !location.hash.includes(`group=${g.id}`) && !location.hash.startsWith(`#/together/${g.id}`)) {
      toast(`${g.name} : la lecture commence`, 'ok', { label: 'Rejoindre', onClick: () => { location.hash = `#/play/${now}?group=${g.id}`; } });
    }
  }
});

async function logout() {
  closeMini();
  forgetMe(); // appareil partagé : le mode hors ligne ne montre plus les téléchargements de cette personne
  stopGroupsFeed();
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
      if (updatePending()) { followUpdate(); return; } // page rechargée pendant une mise à jour
      if ([502, 503, 504].includes(e.status)) { restarting(token); return; }
      if (await offlineMode(e, page, parts, token)) return;
      clear(root, h('div.center-page', errorBox(e, () => location.reload())));
      return;
    }
    if (await resumeUpdate(state.status)) return;
  }
  if (state.status.setup) {
    document.title = 'Installation · Nexis';
    return renderSetup(root, () => { state.status = null; state.me = null; location.hash = '#/admin/libraries'; route(); });
  }
  if (page === 'login' || page === 'profiles') stopGroupsFeed(); // la liste suit la personne connectée
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
    return renderProfiles(root, async () => { await refreshMe(); startPending = true; location.replace('#/'); });
  }
  if (!state.me) {
    try { await refreshMe(); } catch (e) {
      if (e.status === 401) { location.hash = '#/login'; return; }
      if (await offlineMode(e, page, parts, token)) return;
      clear(root, h('div.center-page', errorBox(e, route)));
      return;
    }
  }
  if (token !== renderToken) return;
  // page d'ouverture choisie par l'utilisateur (Paramètres → Accueil et menu), seulement à l'ouverture de l'appli
  if (startPending) {
    startPending = false;
    const target = page === '' ? navItems(state.me).find((i) => i.key === state.me.prefs.start_page && i.key !== 'home') : null;
    if (target) { location.replace(target.href); return; }
  }
  startGroupsFeed({ paused: page === 'play' || page === 'tv' });

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
  if (page === 'offline') ctx.offline = true; // téléchargement lu depuis l'appareil (aussi en ligne : pas de réseau utilisé)
  if (page === 'play' || page === 'tv' || page === 'offline') {
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
  const titles = { '': 'Accueil', live: 'TV en direct', together: 'Regarder ensemble', watchlist: 'À regarder', discover: 'Découvrir', playlists: 'Mes playlists', search: 'Recherche', history: 'Historique', downloads: 'Téléchargements', settings: 'Paramètres', admin: 'Paramètres' };
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
      case 'downloads': await renderDownloads(main, ctx); break;
      case 'playlists': await renderPlaylists(main, ctx); break;
      case 'playlist': await renderPlaylist(main, parts[1], ctx); break;
      case 'history': await renderHistory(main, ctx); break;
      case 'settings': await renderSettingsArea(main, 'settings', parts[1], ctx); break;
      case 'admin':
        if (!state.me.user.is_admin) { location.hash = '#/'; return; }
        await renderSettingsArea(main, 'admin', parts[1], ctx); break;
      default: clear(main, h('div.empty', 'Page introuvable.'));
    }
  } catch (e) {
    if (token === renderToken) clear(main, errorBox(e, route));
  }
}

setUnauthorizedHandler(() => {
  state.me = null;
  stopGroupsFeed();
  if (!location.hash.startsWith('#/login')) {
    toast('Session expirée. Veuillez vous reconnecter.');
    location.hash = '#/login';
  }
});

document.addEventListener('error', (e) => {
  if (e.target && e.target.tagName === 'IMG') e.target.classList.add('broken');
}, true);
document.addEventListener('click', () => {
  document.querySelectorAll('.user-menu, .notif-menu').forEach((m) => { m.hidden = true; });
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

setupInstall();
onInstallChange(() => document.querySelectorAll('.install-app').forEach((b) => { b.hidden = !canInstall(); }));

window.addEventListener('hashchange', route);
route();
