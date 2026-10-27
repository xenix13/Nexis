import { get } from '../api.js';
import { state } from '../app.js';
import { clear, h, posterCard, select, spinner } from '../ui.js';
import { pinButton } from '../pins.js';
import { tr, plural } from '../i18n.js';

const PAGE = 60;

export async function renderLibrary(main, sid, ctx) {
  const lib = state.me.libraries.find((l) => l.id === sid);
  if (!lib) { clear(main, h('div.empty', tr('Bibliothèque introuvable.'))); return; }
  document.title = `${lib.title} · Nexis`;
  const sort = ctx.query.get('sort') || 'title';
  const genre = ctx.query.get('genre') || '';
  const view = ctx.query.get('view') || '';
  const [genres, collections] = await Promise.all([
    get(`/api/libraries/${sid}/genres`).catch(() => []),
    get(`/api/libraries/${sid}/collections`).catch(() => []),
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
  const sortSel = select([['title', tr('Titre')], ['added', tr('Date d’ajout')], ['year', tr('Année')], ['release', tr('Date de sortie')],
    ['rating', tr('Note')]], sort, { onchange: (e) => setQuery('sort', e.target.value), 'aria-label': tr('Trier') });
  const genreSel = select([['', tr('Tous les genres')], ...genres.map((g) => [g.id, g.title])], genre,
    { onchange: (e) => setQuery('genre', e.target.value), 'aria-label': tr('Genre') });
  const count = h('span.muted');
  const grid = h('div.grid');
  const sentinel = h('div.sentinel');
  const pin = genre ? pinButton({ type: 'genre', ref: genre, lib: sid, title: `${genre} · ${lib.title}` }) : null;
  clear(main, h('div.page-head', h('h1', lib.title), count, h('div.filters', sortSel, genreSel, pin)), viewTabs, grid, sentinel);

  let start = 0, total = Infinity, loading = false;
  async function more() {
    if (loading || start >= total || !ctx.isCurrent()) return;
    loading = true;
    sentinel.replaceChildren(spinner());
    try {
      const q = new URLSearchParams({ start, size: PAGE, sort });
      if (genre) q.set('genre', genre);
      const r = await get(`/api/libraries/${sid}/items?${q}`);
      if (!ctx.isCurrent()) return;
      total = r.total;
      start += r.items.length;
      if (!r.items.length) total = start;
      count.textContent = lib.type === 'show' ? plural(total, '{n} série', '{n} séries') : plural(total, '{n} film', '{n} films');
      grid.append(...r.items.map((c) => posterCard(c)));
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
