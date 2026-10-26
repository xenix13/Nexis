// « Regarder ensemble » : groupes en cours, rejoindre un groupe par son lien d'invitation.
import { post } from '../api.js';
import { state } from '../app.js';
import { createGroupDialog, onGroups } from '../groups.js';
import { avatar, clear, episodeCode, errorBox, h, icon, img, spinner, toast } from '../ui.js';

export function groupCard(g) {
  const it = g.item;
  const thumb = it && !it.hidden ? (it.type === 'episode' ? (it.thumb || it.show_art) : (it.art || it.thumb)) : null;
  const what = !it ? 'Dans le salon : le film n’est pas encore choisi' : it.hidden ? it.title
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
      h('div.muted.small', `${!it ? 'Salon' : g.playing ? 'En lecture' : 'En pause'} · ${g.members.join(', ')}`)),
    !it ? h('a.btn.small', { href: `#/together/${g.id}` }, g.member ? 'Salon' : 'Rejoindre le salon')
      : !it.hidden ? h('a.btn.primary.small', { href: `#/together/${g.id}` }, icon('group'), g.member ? 'Revenir' : 'Rejoindre') : null);
}

export async function renderTogether(main, gid, ctx) {
  if (gid) return renderLobby(main, gid, ctx);
  const listBox = h('div');
  clear(main,
    h('div.page-head', h('h1', 'Regarder ensemble'),
      h('button.btn.primary', { onclick: () => createGroupDialog(state.me) }, icon('group'), 'Créer un groupe')),
    h('p.muted', 'Chaque membre regarde sur son propre appareil ; lecture, pauses, sauts et épisode suivant sont synchronisés. Un groupe peut être créé à l’avance, ou depuis la fiche d’un film ou d’un épisode avec le bouton ', icon('group'), '.'),
    listBox);
  // liste tenue à jour en direct : un groupe créé ou fermé apparaît / disparaît sans recharger
  ctx.setCleanup(onGroups((list) => {
    listBox.replaceChildren(list.length ? h('div.group-list', list.map(groupCard)) : h('div.empty', 'Aucun groupe en cours.'));
  }));
}

// Salon d'un groupe : on attend que quelqu'un lance un film ou un épisode, puis tout le monde part ensemble.
async function renderLobby(main, gid, ctx) {
  clear(main, h('div.center-page', spinner()));
  let g;
  try {
    g = await post(`/api/together/${gid}/join`);
  } catch (e) {
    if (ctx.isCurrent()) clear(main, h('div.center-page', errorBox(e, () => { location.hash = '#/together'; })));
    return;
  }
  if (!ctx.isCurrent()) return;
  if (g.item && !g.item.hidden) { location.replace(`#/play/${g.item.id}?group=${gid}`); return; }
  if (g.item) {
    clear(main, h('div.center-page', h('div.empty', h('p', g.item.title), h('a.btn', { href: '#/together' }, 'Voir les groupes'))));
    return;
  }
  const link = `${location.origin}/#/together/${gid}`;
  const members = h('div.lobby-members');
  const paintMembers = (list) => members.replaceChildren(...list.map((m) => h('div.lobby-member', { class: m.online ? 'lobby-member' : 'lobby-member away' },
    avatar({ display_name: m.name, avatar_color: m.color, avatar: m.avatar }, 44), h('span', m.name), m.host ? h('span.chip.small', 'hôte') : null)));
  paintMembers(g.members);
  const canLaunch = g.is_host || !g.host_only;
  clear(main, h('section.lobby',
    h('div.lobby-kicker', icon('group'), 'Salon'),
    h('h1.lobby-title', g.name),
    h('p.muted', canLaunch
      ? 'Ouvrez la fiche d’un film ou d’un épisode et utilisez « Regarder ensemble » : la lecture démarrera en même temps pour tous les membres.'
      : 'En attente du choix de l’hôte. La lecture démarrera automatiquement.'),
    members,
    h('div.lobby-actions',
      h('button.btn.primary', { onclick: async () => {
        try { await navigator.clipboard.writeText(link); toast('Lien d’invitation copié', 'ok'); }
        catch { window.prompt('Lien d’invitation', link); }
      } }, icon('link'), 'Copier le lien d’invitation'),
      canLaunch ? h('a.btn', { href: '#/' }, 'Choisir quoi regarder') : null,
      h('button.btn.ghost', { onclick: async () => {
        await post(`/api/together/${gid}/leave`).catch(() => {});
        location.hash = '#/together';
      } }, 'Quitter le groupe'))));
  // événements du groupe : arrivées et départs, puis lancement de la lecture
  const es = new EventSource(`/api/together/${gid}/events`);
  es.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.type === 'members') paintMembers(msg.members);
    else if (msg.type === 'state' && msg.item_id) { es.close(); location.replace(`#/play/${msg.item_id}?group=${gid}`); }
    else if (msg.type === 'closed') { es.close(); toast('Le groupe a été fermé'); location.hash = '#/together'; }
  };
  es.onerror = () => {
    if (es.readyState !== EventSource.CLOSED || !ctx.isCurrent()) return;
    toast('Le groupe est terminé');
    location.hash = '#/together';
  };
  ctx.setCleanup(() => es.close()); // on reste membre en naviguant : on peut aller chercher un film
}
