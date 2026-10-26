// « Découvrir » : catalogues Netflix, Prime Video, Disney+… et demandes via Seerr.
import { get, post } from '../api.js';
import { state } from '../app.js';
import { clear, h, icon, modal, row, select, spinner, toast } from '../ui.js';

export const STATUS_LABEL = {
  available: 'Disponible', partial: 'En partie disponible', processing: 'En cours de téléchargement',
  pending: 'Demandé, en attente de validation', blocked: 'Refusé', deleted: null,
};
const STATUS_SHORT = { available: 'Disponible', partial: 'Partiel', processing: 'Bientôt', pending: 'Demandé', blocked: 'Refusé' };

export function tmdbImg(path, kind = 'poster') {
  return path ? `/api/discover/img?p=${encodeURIComponent(path)}&k=${kind}` : '';
}

const titleHref = (c) => (c.item_id ? `#/item/${c.item_id}` : `#/discover/title/${c.media_type}/${c.tmdb_id}`);

export function discoverCard(c) {
  const st = STATUS_SHORT[c.status];
  return h('a.card.poster.dcard', { href: titleHref(c), title: c.title },
    h('div.card-img', c.poster ? h('img', { src: tmdbImg(c.poster), alt: '', loading: 'lazy' }) : h('div.noimg', c.title),
      st ? h('span.dbadge', { class: `dbadge ${c.status}` }, c.status === 'available' ? icon('check') : null, st) : null,
      c.wished && !st ? h('span.dbadge.wished', icon('bookmarked')) : null),
    h('div.card-title', c.title),
    h('div.card-sub', [c.year, c.media_type === 'tv' ? 'Série' : 'Film'].filter(Boolean).join(' · ')));
}

function searchBox(value) {
  const input = h('input', { type: 'search', value, placeholder: 'Un film, une série…', 'aria-label': 'Rechercher un titre' });
  return h('form.discover-search', { onsubmit: (e) => {
    e.preventDefault();
    const q = input.value.trim();
    location.hash = q ? `#/discover?q=${encodeURIComponent(q)}` : '#/discover';
  } }, icon('search'), input);
}

export async function renderDiscover(main, ctx) {
  if (ctx.parts[1] === 'title') return renderTitle(main, ctx.parts[2], ctx.parts[3], ctx);
  const q = ctx.query.get('q') || '';
  const provider = ctx.query.get('provider');
  const head = h('div.page-head', h('h1', 'Découvrir'), searchBox(q));
  if (q) {
    const r = await get(`/api/discover/search?q=${encodeURIComponent(q)}`);
    if (!ctx.isCurrent()) return;
    clear(main, head, h('p.muted', `Résultats pour « ${q} »`),
      r.results.length ? h('div.grid', r.results.map(discoverCard)) : h('div.empty', 'Aucun titre trouvé.'));
    return;
  }
  if (provider) return renderBrowse(main, head, Number(provider), ctx);
  const data = await get('/api/discover/home');
  if (!ctx.isCurrent()) return;
  const intro = data.seerr ? null : h('p.muted.small.discover-note', state.me.user.is_admin
    ? h('span', 'Les demandes ne sont pas encore branchées : configure Seerr dans ', h('a', { href: '#/admin/requests' }, 'Paramètres → Demandes'), '.')
    : 'Les demandes ne sont pas encore activées sur ce serveur.');
  clear(main, head, intro,
    row('Tendances de la semaine', data.trending, discoverCard),
    data.providers.map((p) => row(h('span.provider-title', p.logo ? h('img.provider-logo', { src: tmdbImg(p.logo, 'logo'), alt: '' }) : null,
      `Sur ${p.name}`), p.items, discoverCard, { href: `#/discover?provider=${p.id}` })),
    !data.providers.length && !data.trending.length ? h('div.empty', 'Catalogue indisponible pour le moment.') : null);
}

async function renderBrowse(main, head, provider, ctx) {
  const type = ctx.query.get('type') === 'tv' ? 'tv' : 'movie';
  const sort = ctx.query.get('sort') || 'popular';
  const go = (k, v) => {
    const q = new URLSearchParams(ctx.query);
    q.set(k, v);
    location.hash = `#/discover?${q}`;
  };
  const grid = h('div.grid');
  const more = h('div.center-row');
  const title = h('h2.browse-title');
  const tabs = h('div.tabs',
    h('a.tab', { href: '#', class: type === 'movie' ? 'tab active' : 'tab', onclick: (e) => { e.preventDefault(); go('type', 'movie'); } }, 'Films'),
    h('a.tab', { href: '#', class: type === 'tv' ? 'tab active' : 'tab', onclick: (e) => { e.preventDefault(); go('type', 'tv'); } }, 'Séries'));
  const sortSel = select([['popular', 'Populaires'], ['recent', 'Les plus récents'], ['rating', 'Les mieux notés']], sort,
    { onchange: (e) => go('sort', e.target.value), 'aria-label': 'Trier' });
  clear(main, head, h('div.browse-head', h('a.btn.ghost.small', { href: '#/discover' }, icon('back'), 'Découvrir'), title),
    h('div.filters-row', tabs, sortSel), grid, more);
  let page = 0, pages = 1;
  const load = async () => {
    page += 1;
    more.replaceChildren(spinner());
    const r = await get(`/api/discover/browse?provider=${provider}&type=${type}&sort=${sort}&page=${page}`);
    if (!ctx.isCurrent()) return;
    pages = r.pages;
    if (r.provider) {
      title.replaceChildren(r.provider.logo ? h('img.provider-logo.big', { src: tmdbImg(r.provider.logo, 'logo'), alt: '' }) : null, r.provider.name);
      document.title = `${r.provider.name} · Nexis`;
    }
    grid.append(...r.results.map(discoverCard));
    more.replaceChildren(page < pages ? h('button.btn', { onclick: load }, 'Voir plus') : null);
    if (!grid.children.length) grid.replaceWith(h('div.empty', 'Rien à afficher.'));
  };
  await load();
}

async function chooseSeasons(t) {
  const boxes = t.seasons.map((s) => h('label.check-row', h('input', { type: 'checkbox', checked: true, value: s.number }),
    h('span', s.name || `Saison ${s.number}`), h('span.muted.small', ` ${s.episodes || '?'} épisodes${s.year ? ` · ${s.year}` : ''}`)));
  return modal(`Demander « ${t.title} »`, h('div', h('p.muted.small', 'Saisons à demander :'), h('div.check-list', boxes)), [
    { label: 'Annuler', value: null },
    { label: 'Demander', primary: true, handler: () => {
      const picked = boxes.map((b) => b.querySelector('input')).filter((i) => i.checked).map((i) => Number(i.value));
      if (!picked.length) { toast('Sélectionnez au moins une saison', 'bad'); return false; }
      return picked.length === t.seasons.length ? 'all' : picked;
    } }]);
}

async function renderTitle(main, kind, tid, ctx) {
  const t = await get(`/api/discover/title/${kind}/${tid}`);
  if (!ctx.isCurrent()) return;
  document.title = `${t.title} · Nexis`;
  const rerender = () => renderTitle(main, kind, tid, ctx);
  const actions = h('div.detail-actions');
  if (t.item_id) actions.append(h('a.btn.primary', { href: `#/item/${t.item_id}` }, icon('play'), 'Regarder'));
  else if (t.can_request) {
    actions.append(h('button.btn.primary', { onclick: async (e) => {
      let seasons = null;
      if (kind === 'tv' && t.seasons.length > 1) {
        seasons = await chooseSeasons(t);
        if (!seasons) return;
      }
      e.target.disabled = true;
      try {
        await post('/api/discover/request', { type: kind, tmdb_id: t.tmdb_id, seasons: Array.isArray(seasons) ? seasons : undefined });
        toast('Demande envoyée', 'ok');
        rerender();
      } catch (err) { toast(err.message, 'bad'); e.target.disabled = false; }
    } }, icon('playlist'), 'Demander'));
  }
  const statusLine = t.status && STATUS_LABEL[t.status] && !t.item_id
    ? h('div.request-status', { class: `request-status ${t.status}` }, STATUS_LABEL[t.status]) : null;
  if (!t.item_id) {
    let on = t.wished;
    const wbtn = h('button.btn.ghost.icon-only', { 'aria-pressed': String(on) });
    const paint = () => {
      wbtn.replaceChildren(icon(on ? 'bookmarked' : 'bookmark'));
      wbtn.title = on ? 'Retirer de « À regarder »' : 'Ajouter à « À regarder »';
      wbtn.setAttribute('aria-label', wbtn.title);
      wbtn.setAttribute('aria-pressed', String(on));
      wbtn.classList.toggle('on', on);
    };
    wbtn.addEventListener('click', async () => {
      try {
        const r = await post('/api/wishlist', { type: kind, tmdb_id: t.tmdb_id, on: !on });
        on = !on;
        paint();
        if (on && r.requested) { toast('Ajouté à « À regarder » et demandé', 'ok'); rerender(); }
        else if (on && r.request_error) toast(`Ajouté à « À regarder » (demande impossible : ${r.request_error})`, 'bad');
        else toast(on ? 'Ajouté à « À regarder »' : 'Retiré de « À regarder »', 'ok');
      } catch (err) { toast(err.message, 'bad'); }
    });
    paint();
    actions.append(wbtn);
  }
  if (t.trailer) {
    actions.append(h('button.btn.ghost', { onclick: () => {
      const frame = h('iframe.trailer-frame', { src: `https://www.youtube-nocookie.com/embed/${encodeURIComponent(t.trailer)}?autoplay=1&rel=0`,
        allow: 'autoplay; encrypted-media; picture-in-picture; fullscreen', allowfullscreen: true, title: 'Bande-annonce' });
      modal(`Bande-annonce — ${t.title}`, h('div.trailer-box', frame), []);
    } }, icon('film'), 'Bande-annonce'));
  }
  const meta = [t.year, t.media_type === 'tv' ? `${t.seasons.length} saison(s)` : null,
    t.runtime ? `${t.runtime} min` : null, t.rating ? `★ ${t.rating}` : null].filter(Boolean);
  clear(main,
    h('section.detail', { style: { '--backdrop': t.backdrop ? `url("${tmdbImg(t.backdrop, 'backdrop')}")` : 'none' } }, h('div.detail-backdrop'),
      h('div.detail-inner',
        h('div.detail-poster', t.poster ? h('img', { src: tmdbImg(t.poster), alt: '' }) : h('div.noimg', t.title)),
        h('div.detail-info',
          h('a.detail-kicker', { href: '#/discover' }, t.media_type === 'tv' ? 'Série · Découvrir' : 'Film · Découvrir'),
          h('h1.detail-title', t.title),
          h('div.meta-line', meta.map((m) => h('span', String(m)))),
          t.tagline ? h('p.tagline', t.tagline) : null,
          statusLine, actions,
          t.overview ? h('p.summary', t.overview) : null,
          t.genres.length ? h('div.chips', t.genres.map((g) => h('span.chip', g))) : null,
          t.streams.length ? h('div.streams', h('span.muted.small', 'Disponible sur '),
            t.streams.map((s) => h('a.stream', { href: `#/discover?provider=${s.id}`, title: s.name },
              s.logo ? h('img', { src: tmdbImg(s.logo, 'logo'), alt: s.name }) : s.name))) : null,
          t.cast.length ? h('p.muted.small', `Avec ${t.cast.join(', ')}`) : null))));
}
