// « Regarder ensemble » : groupes en cours, rejoindre un groupe par son lien d'invitation.
import { get, post } from '../api.js';
import { clear, episodeCode, errorBox, h, icon, img, spinner } from '../ui.js';

export function groupCard(g) {
  const it = g.item;
  const thumb = it && !it.hidden ? (it.type === 'episode' ? (it.thumb || it.show_art) : (it.art || it.thumb)) : null;
  const what = !it ? 'Rien en cours' : it.hidden ? it.title
    : it.type === 'episode' ? `${it.show} · ${episodeCode(it)} — ${it.title}` : it.title;
  return h('div.group-card',
    thumb ? h('img', { src: img(thumb, 256, 144), alt: '', loading: 'lazy', onerror: (e) => {
      // pas d'image de fond : l'affiche fera l'affaire
      const alt = it.type === 'episode' ? it.show_thumb : it.thumb;
      if (alt && !e.target.dataset.retry) { e.target.dataset.retry = '1'; e.target.src = img(alt, 256, 144); }
    } }) : h('div.noimg'),
    h('div.grow',
      h('strong', g.name),
      h('div.small', what),
      h('div.muted.small', `${g.playing ? 'En lecture' : 'En pause'} · ${g.members.join(', ')}`)),
    it && !it.hidden ? h('a.btn.primary.small', { href: `#/together/${g.id}` }, icon('group'), g.member ? 'Revenir' : 'Rejoindre') : null);
}

export async function renderTogether(main, gid, ctx) {
  if (gid) {
    clear(main, h('div.center-page', spinner()));
    try {
      const g = await post(`/api/together/${gid}/join`);
      if (!ctx.isCurrent()) return;
      if (g.item && !g.item.hidden) { location.replace(`#/play/${g.item.id}?group=${gid}`); return; }
      clear(main, h('div.center-page', h('div.empty', h('p', g.item ? g.item.title : `Le groupe « ${g.name} » n’a encore rien lancé.`),
        h('a.btn', { href: '#/together' }, 'Voir les groupes'))));
    } catch (e) {
      clear(main, h('div.center-page', errorBox(e, () => { location.hash = '#/together'; })));
    }
    return;
  }
  const list = await get('/api/together');
  if (!ctx.isCurrent()) return;
  clear(main,
    h('div.page-head', h('h1', 'Regarder ensemble')),
    h('p.muted', 'Chacun regarde depuis son écran, en même temps : lecture, pauses, sauts et épisode suivant sont partagés par tout le groupe. Pour créer un groupe, ouvre un film ou un épisode et clique sur ', icon('group'), ' ; dans le lecteur, le même bouton donne le lien d’invitation.'),
    list.length ? h('div.group-list', list.map(groupCard)) : h('div.empty', 'Aucun groupe en cours.'));
  const t = setTimeout(() => { if (ctx.isCurrent()) renderTogether(main, null, ctx); }, 15000);
  ctx.setCleanup(() => clearTimeout(t));
}
