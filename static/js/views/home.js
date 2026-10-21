import { get } from '../api.js';
import { state } from '../app.js';
import { clear, h, icon, img, posterCard, row, wideCard } from '../ui.js';

export async function renderHome(main, ctx) {
  const data = await get('/api/home');
  if (!ctx.isCurrent()) return;
  const parts = [];
  const hero = data.continue[0] || data.hero;
  if (hero) {
    const art = hero.type === 'episode' ? (hero.show_art || hero.art) : hero.art;
    const resume = data.continue[0] === hero;
    const playHref = `#/play/${hero.id}${resume && hero.position ? '?resume=1' : ''}`;
    const infoHref = `#/item/${hero.type === 'episode' ? hero.show_id : hero.id}`;
    parts.push(h('section.hero', art ? { style: { backgroundImage: `url("${img(art, 1600, 900)}")` } } : null,
      h('div.hero-shade'),
      h('div.hero-body',
        h('div.hero-kicker', resume ? (hero.next_up ? 'À suivre' : 'Reprendre') : 'Ajouté récemment'),
        h('h1.hero-title', hero.type === 'episode' ? hero.show : hero.title),
        hero.type === 'episode'
          ? h('p.hero-sub', `Saison ${hero.season} · Épisode ${hero.episode} — ${hero.title}`)
          : (hero.year ? h('p.hero-sub', String(hero.year)) : null),
        h('div.hero-actions',
          hero.type === 'show' ? null : h('a.btn.primary', { href: playHref }, icon('play'), resume && hero.position ? 'Reprendre' : 'Lecture'),
          h('a.btn.ghost', { href: infoHref }, 'Plus d’infos')))));
  }
  parts.push(row('Reprendre et à suivre', data.continue, (c) => wideCard(c), { wide: true }));
  for (const r of data.rows) {
    const title = r.library.type === 'show' ? `Nouveaux épisodes · ${r.library.title}` : `Ajoutés récemment · ${r.library.title}`;
    parts.push(row(title, r.items, (c) => posterCard(c), { href: `#/library/${r.library.id}` }));
  }
  if (!data.rows.length && !data.continue.length) {
    parts.push(state.me.user.is_admin
      ? h('div.empty', h('p', 'Aucun média pour l’instant.'),
        h('a.btn.primary', { href: '#/admin/libraries' }, 'Ajouter une bibliothèque'))
      : h('div.empty', 'Aucune bibliothèque ne t’est accessible pour le moment. Demande à l’administrateur.'));
  }
  clear(main, parts);
}
