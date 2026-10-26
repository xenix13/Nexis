import { get, post } from '../api.js';
import { state } from '../app.js';
import { arrange, clear, h, icon, img, posterCard, row, toast, wideCard } from '../ui.js';
import { groupCard } from './together.js';
import { onGroups } from '../groups.js';

export async function renderHome(main, ctx) {
  const data = await get('/api/home');
  if (!ctx.isCurrent()) return;
  const parts = [];
  const sections = new Map(); // clé -> rangée ; affichées dans l'ordre choisi (Paramètres → Accueil et menu)
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
          : (hero.year ? h('p.hero-sub', [hero.year, hero.content_rating].filter(Boolean).join(' · ')) : null),
        hero.summary ? h('p.hero-summary', hero.summary) : null,
        h('div.hero-actions',
          hero.type === 'show' ? null : h('a.btn.light.lg', { href: playHref }, icon('play'), resume && hero.position ? 'Reprendre' : 'Lecture'),
          h('a.btn.ghost.lg', { href: infoHref }, 'Plus d’infos')))));
  }
  // groupes en cours : rangée tenue à jour en direct (vide et invisible quand il n'y en a pas)
  const togetherBox = h('div.together-row');
  sections.set('together', togetherBox);
  const stopGroups = onGroups((groups) => {
    const shown = groups.filter((g) => !g.item || !g.item.hidden);
    togetherBox.replaceChildren(...(shown.length ? [h('section.row', h('div.row-head', h('a.row-title', { href: '#/together' }, 'Regarder ensemble')),
      h('div.group-list', shown.map(groupCard)))] : []));
  });
  ctx.setCleanup(stopGroups);
  const continueRow = (items) => row('Reprendre et à suivre', items, (c) => wideCard(c, { onWatched }), { wide: true });
  let current = continueRow(data.continue);
  // « marquer comme vu » : la carte s'efface, l'épisode suivant prend sa place ; annulable
  async function refreshRow(c) {
    if (hero && String(hero.id) === String(c.id)) { stopGroups(); await renderHome(main, ctx); return; } // la grande affiche change aussi
    const fresh = await get('/api/home');
    if (!ctx.isCurrent()) return;
    const next = continueRow(fresh.continue) || h('div', { hidden: true });
    if (current && current.isConnected) current.replaceWith(next);
    current = next;
  }
  async function setWatched(c, watched) {
    await post(`/api/items/${c.id}/watched`, watched ? { watched } : { watched, undo: true, position: c.position || 0 });
    await refreshRow(c);
  }
  async function onWatched(c, slot) {
    slot.classList.add('leaving');
    try {
      await setWatched(c, true);
      toast(c.type === 'episode' ? `${c.show} · ${c.title} : marqué comme vu` : `${c.title} : marqué comme vu`, 'ok',
        { label: 'Annuler', onClick: () => setWatched(c, false).catch((x) => toast(x.message, 'bad')) });
    } catch (x) {
      slot.classList.remove('leaving');
      slot.querySelector('.card-watch').disabled = false;
      toast(x.message, 'bad');
    }
  }
  sections.set('continue', current);
  sections.set('watchlist', row('À regarder', data.watchlist, (c) => posterCard(c), { href: '#/watchlist' }));
  // suggestions calculées à partir de ce qui a été regardé récemment (genres, réalisateurs, acteurs)
  sections.set('suggest', row('Suggestions pour toi', data.suggestions, (c) => posterCard(c)));
  const because = (data.because || []).map((b) => row(`Parce que tu as regardé ${b.seed.type === 'show' ? 'la série ' : ''}${b.seed.title}`,
    b.items, (c) => posterCard(c)));
  sections.set('because', because.length ? h('div.row-group', because) : null);
  for (const r of data.rows) {
    const title = r.library.type === 'show' ? `Nouveaux épisodes · ${r.library.title}` : `Ajoutés récemment · ${r.library.title}`;
    sections.set(`lib:${r.library.id}`, row(title, r.items, (c) => posterCard(c), { href: `#/library/${r.library.id}` }));
  }
  const { home_order: order = [], home_hidden: hiddenKeys = [] } = state.me.prefs;
  for (const k of arrange([...sections.keys()], order)) {
    if (!hiddenKeys.includes(k)) parts.push(sections.get(k));
  }
  if (!data.rows.length && !data.continue.length && !(data.watchlist || []).length) {
    parts.push(state.me.user.is_admin
      ? h('div.empty', h('p', 'Aucun média pour l’instant.'),
        h('a.btn.primary', { href: '#/admin/libraries' }, 'Ajouter une bibliothèque'))
      : h('div.empty', 'Aucune bibliothèque ne t’est accessible pour le moment. Demande à l’administrateur.'));
  }
  clear(main, parts);
}
