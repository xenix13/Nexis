// « Regarder ensemble » : liste des groupes en direct (une connexion par onglet, partagée par toutes les pages)
// et création d'un groupe à l'avance (salon d'attente).
import { post } from './api.js';
import { field, h, modal, toggle } from './ui.js';

let source = null;
let groups = [];
const listeners = new Set();

export function currentGroups() { return groups; }

// fn(groups) appelé tout de suite puis à chaque changement ; renvoie de quoi se désabonner
export function onGroups(fn) {
  listeners.add(fn);
  fn(groups);
  return () => listeners.delete(fn);
}

export function startGroupsFeed() {
  wanted = true;
  if (!document.hidden) openFeed();
}

function openFeed() {
  if (source || typeof EventSource === 'undefined') return;
  source = new EventSource('/api/together/feed');
  source.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.type !== 'groups') return;
    groups = msg.groups;
    for (const fn of [...listeners]) { try { fn(groups); } catch (err) { console.warn(err); } }
  };
  // en cas de coupure, le navigateur se reconnecte tout seul (retry: 3000)
}

// Onglet en arrière-plan : on libère la connexion (les navigateurs en limitent le nombre par serveur),
// sauf si l'on fait partie d'un groupe (rester présent dans le salon pendant qu'on fait autre chose).
let wanted = false;
let hiddenTimer = null;
document.addEventListener('visibilitychange', () => {
  clearTimeout(hiddenTimer);
  if (!wanted) return;
  if (document.hidden) {
    hiddenTimer = setTimeout(() => {
      if (document.hidden && source && !groups.some((g) => g.member)) { source.close(); source = null; }
    }, 30000);
  } else if (!source) {
    openFeed();
  }
});

export function stopGroupsFeed() {
  wanted = false;
  if (source) source.close();
  source = null;
  groups = [];
  for (const fn of [...listeners]) { try { fn(groups); } catch { /* ignore */ } }
}

// Groupes où l'on peut lancer un titre : ceux dont on est membre et que l'on peut piloter
export function launchableGroups() {
  return groups.filter((g) => g.member && (g.is_host || !g.host_only));
}

export async function createGroupDialog(me) {
  const name = h('input', { placeholder: `Chez ${me.user.display_name}`, maxlength: 60 });
  const hostOnly = toggle('Seul moi peux mettre en pause, avancer ou changer d’épisode', false);
  const err = h('p.form-error');
  const g = await modal('Créer un groupe', h('div.form',
    h('p.muted.small', 'Crée le groupe maintenant et envoie le lien : chacun attend dans le salon pendant que tu choisis quoi regarder. Le film ou l’épisode lancé démarre en même temps pour tout le monde.'),
    field('Nom du groupe', name), hostOnly, err), [
    { label: 'Annuler', value: null },
    { label: 'Créer le groupe', primary: true, handler: async () => {
      try {
        return await post('/api/together', { name: name.value.trim() || undefined, host_only: hostOnly.querySelector('input').checked });
      } catch (e) { err.textContent = e.message; return false; }
    } },
  ]);
  if (g) location.hash = `#/together/${g.id}`;
}
