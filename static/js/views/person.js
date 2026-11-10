import { get } from '../api.js';
import { clear, h, posterCard } from '../ui.js';
import { plural, tr } from '../i18n.js';

// Fiche d'une personne : ses films et séries présents sur le serveur (distribution et réalisation)
export async function renderPerson(main, name, ctx) {
  const r = await get(`/api/person?name=${encodeURIComponent(name)}`);
  if (!ctx.isCurrent()) return;
  document.title = `${r.name} · Noxa`;
  const acted = r.items.filter((c) => c.role !== undefined);
  const directed = r.items.filter((c) => c.director);
  const grid = (list, sub) => h('div.grid', list.map((c) => posterCard({ ...c, year: sub(c) })));
  const photo = r.photo ? h('img.person-photo', { src: `/api/discover/img?p=${encodeURIComponent(r.photo)}&k=profile`, alt: '' })
    : h('span.person-photo.person-init', r.name.charAt(0));
  clear(main,
    h('div.person-head', photo, h('div', h('h1', r.name),
      h('p.muted', plural(r.items.length, '{n} titre sur ce serveur', '{n} titres sur ce serveur')))),
    acted.length ? h('section.block', h('h2', tr('Distribution')),
      grid(acted, (c) => [c.year, c.role].filter(Boolean).join(' · '))) : null,
    directed.length ? h('section.block', h('h2', tr('Réalisation')), grid(directed, (c) => c.year || '')) : null,
    r.items.length ? null : h('div.empty', tr('Aucun titre de cette personne sur ce serveur.')));
}
