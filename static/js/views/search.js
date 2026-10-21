import { get } from '../api.js';
import { clear, h, posterCard, wideCard } from '../ui.js';

export async function renderSearch(main, q, ctx) {
  if (q.length < 2) { clear(main, h('div.empty', 'Tape au moins deux caractères.')); return; }
  const r = await get(`/api/search?q=${encodeURIComponent(q)}`);
  if (!ctx.isCurrent()) return;
  const sections = [h('div.page-head', h('h1', `Résultats pour « ${q} »`))];
  if (r.movies.length) sections.push(h('section.block', h('h2', 'Films'), h('div.grid', r.movies.map((c) => posterCard(c)))));
  if (r.shows.length) sections.push(h('section.block', h('h2', 'Séries'), h('div.grid', r.shows.map((c) => posterCard(c)))));
  if (r.episodes.length) sections.push(h('section.block', h('h2', 'Épisodes'), h('div.grid.wide', r.episodes.map((c) => wideCard(c)))));
  if (sections.length === 1) sections.push(h('div.empty', 'Aucun résultat.'));
  clear(main, sections);
}
