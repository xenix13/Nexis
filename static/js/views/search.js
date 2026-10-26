import { get } from '../api.js';
import { state } from '../app.js';
import { clear, h, posterCard, spinner, wideCard } from '../ui.js';
import { discoverCard } from './discover.js';

export async function renderSearch(main, q, ctx) {
  if (q.length < 2) { clear(main, h('div.empty', 'Tape au moins deux caractères.')); return; }
  const discover = state.me.features && state.me.features.discover;
  // ce qui est sur le serveur d'abord, puis le reste du catalogue (Netflix, Prime Video…)
  const elsewhere = discover ? get(`/api/discover/search?q=${encodeURIComponent(q)}`).catch(() => ({ results: [] })) : null;
  const r = await get(`/api/search?q=${encodeURIComponent(q)}`);
  if (!ctx.isCurrent()) return;
  const sections = [h('div.page-head', h('h1', `Résultats pour « ${q} »`))];
  if (r.movies.length) sections.push(h('section.block', h('h2', 'Films'), h('div.grid', r.movies.map((c) => posterCard(c)))));
  if (r.shows.length) sections.push(h('section.block', h('h2', 'Séries'), h('div.grid', r.shows.map((c) => posterCard(c)))));
  if (r.episodes.length) sections.push(h('section.block', h('h2', 'Épisodes'), h('div.grid.wide', r.episodes.map((c) => wideCard(c)))));
  const local = sections.length > 1;
  const more = h('section.block');
  if (elsewhere) {
    more.append(spinner());
    sections.push(more);
  } else if (!local) sections.push(h('div.empty', 'Aucun résultat.'));
  clear(main, sections);
  if (!elsewhere) return;
  const ext = await elsewhere;
  if (!ctx.isCurrent()) return;
  const missing = ext.results.filter((c) => !c.item_id);
  if (missing.length) {
    more.replaceChildren(h('h2', local ? 'Pas encore sur le serveur' : 'Disponible sur demande'),
      h('div.grid', missing.map(discoverCard)));
  } else more.replaceChildren(local ? '' : h('div.empty', 'Aucun résultat.'));
}
