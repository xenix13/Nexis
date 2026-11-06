import { get } from '../api.js';
import { state } from '../app.js';
import { clear, fmtDuration, h, icon, img, posterCard, progressBar, select, spinner, store, toast } from '../ui.js';
import { pinButton } from '../pins.js';
import { tr, plural } from '../i18n.js';
import { renderPhotos } from './photos.js';
import { renderBooks } from './books.js';

const PAGE = 60;
// filtres de la page (paramètres de l'adresse, transmis tels quels au serveur et épinglables sur l'accueil)
const FILTERS = ['genre', 'watched', 'decade', 'duration', 'res', 'hdr', 'audio', 'subs', 'rating'];
const MODES = [['poster', 'grid', 'Affiches'], ['wide', 'wide', 'Vignettes'], ['list', 'list', 'Liste']];

export async function renderLibrary(main, sid, ctx) {
  const lib = state.me.libraries.find((l) => l.id === sid);
  if (!lib) { clear(main, h('div.empty', tr('Bibliothèque introuvable.'))); return; }
  if (lib.type === 'photo') { await renderPhotos(main, lib, ctx); return; }
  if (lib.type === 'audiobook') { await renderBooks(main, lib, ctx); return; }
  document.title = `${lib.title} · Nexis`;
  const sort = ctx.query.get('sort') || 'title';
  const genre = ctx.query.get('genre') || '';
  const view = ctx.query.get('view') || '';
  const [genres, collections, facets] = await Promise.all([
    get(`/api/libraries/${sid}/genres`).catch(() => []),
    get(`/api/libraries/${sid}/collections`).catch(() => []),
    get(`/api/libraries/${sid}/facets`).catch(() => ({ decades: [], audio: [], subs: [] })),
  ]);
  if (!ctx.isCurrent()) return;
  const viewTabs = collections.length ? h('div.tabs.view-tabs',
    h('a.tab', { href: `#/library/${sid}`, class: view ? 'tab' : 'tab active' }, lib.type === 'show' ? tr('Séries') : tr('Films')),
    h('a.tab', { href: `#/library/${sid}?view=collections`, class: view === 'collections' ? 'tab active' : 'tab' },
      tr('Collections ({length})', { length: collections.length }))) : null;
  if (view === 'collections') {
    clear(main, h('div.page-head', h('h1', lib.title)), viewTabs,
      h('div.grid', collections.map((c) => posterCard({ ...c, year: tr('{leaves} élément(s)', { leaves: c.leaves }) }, { href: `#/collection/${c.id}` }))));
    return;
  }

  const setQuery = (k, v) => {
    const q = new URLSearchParams(ctx.query);
    if (v) q.set(k, v); else q.delete(k);
    location.hash = `#/library/${sid}${q.toString() ? `?${q}` : ''}`;
  };
  const active = FILTERS.filter((k) => ctx.query.get(k));
  const filterQuery = () => {
    const q = new URLSearchParams();
    for (const k of FILTERS) if (ctx.query.get(k)) q.set(k, ctx.query.get(k));
    return q;
  };
  const pick = (key, options, label) => select(options, ctx.query.get(key) || '',
    { onchange: (e) => setQuery(key, e.target.value), 'aria-label': label });
  const sortSel = select([['title', tr('Titre')], ['added', tr('Date d’ajout')], ['year', tr('Année')], ['release', tr('Date de sortie')],
    ['rating', tr('Note')], ['mine', tr('Ma note')]], sort, { onchange: (e) => setQuery('sort', e.target.value), 'aria-label': tr('Trier') });
  const genreSel = pick('genre', [['', tr('Tous les genres')], ...genres.map((g) => [g.id, g.title])], tr('Genre'));

  // panneau des autres filtres (ouvert d'office si l'un d'eux est actif)
  const show = lib.type === 'show';
  const panel = h('div.filter-panel', { hidden: !active.some((k) => k !== 'genre') },
    h('label.filter', h('span', tr('Visionnage')), pick('watched', [['', tr('Tous')], ['unwatched', show ? tr('Pas commencées') : tr('Non vus')],
      ['inprogress', tr('En cours')], ['watched', show ? tr('Terminées') : tr('Vus')]], tr('Visionnage'))),
    facets.decades.length ? h('label.filter', h('span', tr('Décennie')), pick('decade', [['', tr('Toutes')],
      ...facets.decades.map((d) => [String(d), tr('Années {d}', { d: d >= 2000 ? d : String(d).slice(2) })])], tr('Décennie'))) : null,
    show ? null : h('label.filter', h('span', tr('Durée')), pick('duration', [['', tr('Toutes')], ['short', tr('Moins de 1 h 30')],
      ['medium', tr('1 h 30 à 2 h 30')], ['long', tr('Plus de 2 h 30')]], tr('Durée'))),
    h('label.filter', h('span', tr('Définition')), pick('res', [['', tr('Toutes')], ['4k', '4K'], ['1080p', '1080p'], ['720p', '720p'],
      ['sd', tr('SD')]], tr('Définition'))),
    h('label.filter', h('span', 'HDR'), pick('hdr', [['', tr('Indifférent')], ['1', tr('HDR seulement')]], 'HDR')),
    facets.audio.length > 1 ? h('label.filter', h('span', tr('Langue audio')), pick('audio', [['', tr('Toutes')],
      ...facets.audio.map((l) => [l.id, l.title])], tr('Langue audio'))) : null,
    facets.subs.length ? h('label.filter', h('span', tr('Sous-titres')), pick('subs', [['', tr('Tous')],
      ...facets.subs.map((l) => [l.id, l.title])], tr('Sous-titres'))) : null,
    h('label.filter', h('span', tr('Note')), pick('rating', [['', tr('Toutes')], ...[6, 7, 8].map((n) => [String(n), tr('{n} et plus', { n })])], tr('Note'))),
    active.length ? h('button.btn.small.ghost', { type: 'button', onclick: () => {
      const q = new URLSearchParams(ctx.query);
      FILTERS.forEach((k) => q.delete(k));
      location.hash = `#/library/${sid}${q.toString() ? `?${q}` : ''}`;
    } }, tr('Effacer les filtres')) : null);
  const filterBtn = h('button.btn.small', { type: 'button', 'aria-expanded': String(!panel.hidden),
    onclick: (e) => { panel.hidden = !panel.hidden; e.currentTarget.setAttribute('aria-expanded', String(!panel.hidden)); } },
  icon('list'), tr('Filtres'), active.filter((k) => k !== 'genre').length ? h('span.count-badge', active.filter((k) => k !== 'genre').length) : null);

  // épingler : le genre seul, ou toute la combinaison de filtres
  const describe = () => {
    const parts = [];
    if (genre) parts.push(genre);
    const label = (k, sel) => { const o = panel.querySelector(`select[aria-label="${sel}"]`); return o ? o.selectedOptions[0].textContent : ctx.query.get(k); };
    for (const [k, l] of [['watched', tr('Visionnage')], ['decade', tr('Décennie')], ['duration', tr('Durée')], ['res', tr('Définition')],
      ['audio', tr('Langue audio')], ['subs', tr('Sous-titres')], ['rating', tr('Note')]]) if (ctx.query.get(k)) parts.push(label(k, l));
    if (ctx.query.get('hdr')) parts.push('HDR');
    return `${parts.join(' · ')} · ${lib.title}`.slice(0, 100);
  };
  const pin = active.length === 1 && genre ? pinButton({ type: 'genre', ref: genre, lib: sid, title: `${genre} · ${lib.title}` })
    : active.length ? pinButton({ type: 'filter', ref: filterQuery().toString(), lib: sid, title: describe() }) : null;

  // au hasard : un film (lancé tout de suite, de préférence pas encore vu) ou une série (sa fiche), selon le genre choisi
  const random = h('button.btn.small', { type: 'button', title: show ? tr('Une série au hasard') : tr('Un film au hasard'),
    onclick: async () => {
      try {
        const q = new URLSearchParams({ library: sid });
        if (genre) q.set('genre', genre);
        const c = await get(`/api/random?${q}`);
        location.hash = c.type === 'movie' ? `#/play/${c.id}` : `#/item/${c.id}`;
      } catch (e) { toast(e.message, 'bad'); }
    } }, icon('shuffle'), tr('Au hasard'));

  // affichage : affiches, vignettes larges ou liste détaillée (retenu pour chaque bibliothèque, sur cet appareil)
  let mode = store(`libview:${sid}`) || 'poster';
  if (!MODES.some(([m]) => m === mode)) mode = 'poster';
  const modes = h('div.seg', MODES.map(([m, ic, label]) => h('button.seg-btn', { type: 'button', title: tr(label), 'aria-label': tr(label),
    'aria-pressed': String(m === mode), onclick: () => { store(`libview:${sid}`, m); renderLibrary(main, sid, ctx); } }, icon(ic))));

  const count = h('span.muted');
  const grid = h(mode === 'list' ? 'div.lib-list' : mode === 'wide' ? 'div.grid.wide' : 'div.grid');
  const sentinel = h('div.sentinel');
  // bibliothèque d'un serveur lié : un simple badge, elle s'utilise comme les autres
  const server = lib.server ? h('span.server-badge', { title: tr('Bibliothèque du serveur lié « {server} »', { server: lib.server }) }, icon('link'), lib.server) : null;
  clear(main, h('div.page-head', h('h1', lib.title), server, count,
    h('div.filters', sortSel, genreSel, filterBtn, random, modes, pin)), panel, viewTabs, grid, sentinel);

  const render = (c) => (mode === 'list' ? listRow(c) : mode === 'wide' ? wideLibCard(c) : posterCard(c));
  let start = 0, total = Infinity, loading = false;
  async function more() {
    if (loading || start >= total || !ctx.isCurrent()) return;
    loading = true;
    sentinel.replaceChildren(spinner());
    try {
      const q = filterQuery();
      q.set('start', start);
      q.set('size', PAGE);
      q.set('sort', sort);
      const r = await get(`/api/libraries/${sid}/items?${q}`);
      if (!ctx.isCurrent()) return;
      total = r.total;
      start += r.items.length;
      if (!r.items.length) total = start;
      count.textContent = show ? plural(total, '{n} série', '{n} séries') : plural(total, '{n} film', '{n} films');
      grid.append(...r.items.map(render));
      if (!total) grid.replaceWith(h('div.empty', tr('Rien ici pour ce filtre.')));
    } finally {
      loading = false;
      sentinel.replaceChildren();
    }
    if (sentinel.getBoundingClientRect().top < window.innerHeight + 600) more();
  }
  const io = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) more(); },
    { rootMargin: '600px' });
  io.observe(sentinel);
  ctx.setCleanup(() => io.disconnect());
  await more();
}

// vignette large (image de fond) qui ouvre la fiche
function wideLibCard(c) {
  const thumb = c.art || c.thumb;
  return h('a.card.wide', { href: `#/item/${c.id}`, title: c.title },
    h('div.card-img', thumb ? h('img', { src: img(thumb, 400, 225), alt: '', loading: 'lazy' }) : h('div.noimg', c.title),
      progressBar(c.position, c.duration), c.watched ? h('span.badge.done', icon('check')) : null),
    h('div.card-title', c.title), h('div.card-sub', String(c.year || '')));
}

// ligne de la liste détaillée : affiche, titre, année, durée ou nombre d'épisodes, note, état
function listRow(c) {
  const facts = [c.year, c.type === 'show' ? plural(c.leaves || 0, '{n} épisode', '{n} épisodes') : fmtDuration(c.duration),
    c.rating ? `★ ${c.rating}` : null, c.content_rating].filter(Boolean);
  return h('a.lib-row', { href: `#/item/${c.id}` },
    h('div.lib-row-img', c.thumb ? h('img', { src: img(c.thumb, 60, 90), alt: '', loading: 'lazy' }) : null),
    h('div.lib-row-main', h('strong', c.title), h('div.muted.small', facts.join(' · ')),
      c.position && c.duration ? progressBar(c.position, c.duration) : null),
    c.watched ? h('span.lib-row-state', icon('check')) : null);
}
