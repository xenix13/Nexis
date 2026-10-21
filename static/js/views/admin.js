import { del, get, post, put } from '../api.js';
import { state } from '../app.js';
import { plexConnector } from './auth.js';
import { avatar, clear, confirmDialog, field, fmtTime, h, icon, modal, toast, toggle } from '../ui.js';

function tabs(active) {
  const t = (id, label) => h('a.tab', { href: `#/admin/${id}`, class: id === active ? 'tab active' : 'tab' }, label);
  return h('div.tabs', t('users', 'Utilisateurs'), t('plex', 'Serveur Plex'), t('streams', 'Lectures en cours'));
}

function since(ts) {
  if (!ts) return 'jamais';
  const s = Date.now() / 1000 - ts;
  if (s < 120) return 'à l’instant';
  if (s < 3600) return `il y a ${Math.round(s / 60)} min`;
  if (s < 86400) return `il y a ${Math.round(s / 3600)} h`;
  return `il y a ${Math.round(s / 86400)} j`;
}

async function userForm(user, libraries) {
  const isNew = !user;
  user = user || { username: '', display_name: '', is_admin: false, all_libraries: true, libraries: [], disabled: false };
  const username = h('input', { value: user.username, disabled: !isNew, autocomplete: 'off' });
  const display = h('input', { value: user.display_name, maxlength: 50 });
  const pass = h('input', { type: 'password', autocomplete: 'new-password',
    placeholder: isNew ? '8 caractères minimum' : 'Laisser vide pour ne pas changer' });
  const admin = toggle('Administrateur', user.is_admin);
  const disabled = toggle('Compte désactivé', user.disabled);
  const all = toggle('Accès à toutes les bibliothèques (y compris les futures)', user.all_libraries);
  const libChecks = libraries.map((l) => {
    const t = toggle(l.title, user.libraries.includes(l.id));
    t.dataset.id = l.id;
    return t;
  });
  const libBox = h('div.checks', libChecks);
  const syncLibs = () => { libBox.hidden = all.querySelector('input').checked; };
  all.querySelector('input').addEventListener('change', syncLibs);
  syncLibs();
  const err = h('p.form-error');

  const body = h('div.form',
    h('div.grid2', field('Identifiant', username), field('Nom affiché', display)),
    field(isNew ? 'Mot de passe' : 'Nouveau mot de passe', pass),
    admin, isNew ? null : disabled, all, libBox, err);

  return modal(isNew ? 'Nouvel utilisateur' : `Modifier ${user.display_name}`, body, [
    { label: 'Annuler', value: null },
    { label: isNew ? 'Créer' : 'Enregistrer', primary: true, handler: async () => {
      const payload = {
        display_name: display.value.trim() || username.value.trim(),
        is_admin: admin.querySelector('input').checked,
        all_libraries: all.querySelector('input').checked,
        libraries: libChecks.filter((t) => t.querySelector('input').checked).map((t) => t.dataset.id),
      };
      if (pass.value) payload.password = pass.value;
      if (!isNew) payload.disabled = disabled.querySelector('input').checked;
      try {
        if (isNew) await post('/api/admin/users', { username: username.value.trim(), ...payload });
        else await put(`/api/admin/users/${user.id}`, payload);
        return true;
      } catch (e) { err.textContent = e.message; return false; }
    } },
  ]);
}

async function usersTab(body, ctx) {
  const [users, libraries] = await Promise.all([get('/api/admin/users'), get('/api/admin/libraries')]);
  if (!ctx.isCurrent()) return;
  const reload = () => usersTab(body, ctx);
  const libName = (id) => (libraries.find((l) => l.id === id) || { title: `#${id}` }).title;
  clear(body,
    h('div.toolbar', h('p.muted', `${users.length} compte(s). Chaque compte a son propre historique, sa reprise de lecture et ses préférences.`),
      h('button.btn.primary', { onclick: async () => { if (await userForm(null, libraries)) { toast('Utilisateur créé', 'ok'); reload(); } } }, '+ Ajouter')),
    h('div.user-list', users.map((u) => h('div.user-row', { class: u.disabled ? 'user-row off' : 'user-row' },
      avatar(u, 40),
      h('div.grow', h('strong', u.display_name), h('span.muted', ` @${u.username}`),
        h('div.muted.small',
          u.is_admin ? h('span.tag.accent', 'Admin') : null,
          u.disabled ? h('span.tag', 'Désactivé') : null,
          u.all_libraries ? 'Toutes les bibliothèques' : (u.libraries.map(libName).join(', ') || 'Aucune bibliothèque'),
          ` · vu ${since(u.last_seen)}`)),
      h('button.btn.small', { onclick: async () => { if (await userForm(u, libraries)) { toast('Modifications enregistrées', 'ok'); reload(); } } }, 'Modifier'),
      u.id === state.me.user.id ? null : h('button.icon-btn', { title: 'Supprimer', onclick: async () => {
        if (!await confirmDialog('Supprimer ce compte ?', `${u.display_name} perdra son historique et ses préférences. Cette action est définitive.`, 'Supprimer')) return;
        try { await del(`/api/admin/users/${u.id}`); toast('Compte supprimé', 'ok'); reload(); } catch (e) { toast(e.message, 'bad'); }
      } }, icon('trash'))))));
}

async function plexTab(body, ctx) {
  const s = await get('/api/admin/settings');
  if (!ctx.isCurrent()) return;
  const conn = plexConnector({ initialUrl: s.plex_url, tokenSet: s.token_set });
  ctx.setCleanup(conn.stop);
  const statusLine = s.server
    ? h('div.plex-status.ok', `Connecté à « ${s.server.name} » — Plex ${s.server.version}`)
    : h('div.plex-status.bad', s.error || 'Non connecté');
  clear(body,
    h('section.panel', h('h2', 'État'), statusLine, h('p.muted.small', `Séance ${s.version}`)),
    h('section.panel', h('h2', 'Connexion à Plex'), conn.el,
      h('div.actions-row', h('button.btn.primary', { onclick: async () => {
        try { const r = await put('/api/admin/settings', conn.values()); toast(`Enregistré : ${r.server}`, 'ok'); plexTab(body, ctx); }
        catch (e) { toast(e.message, 'bad'); }
      } }, 'Enregistrer'))));
}

async function streamsTab(body, ctx) {
  const load = async () => {
    const list = await get('/api/admin/streams');
    if (!ctx.isCurrent()) return;
    clear(body, list.length ? h('div.user-list', list.map((s) => h('div.user-row',
      h('div.grow', h('strong', s.title), h('div.muted.small',
        `${s.user} · ${s.state === 'paused' ? 'en pause' : 'en lecture'} à ${fmtTime(s.position_ms)} · qualité ${s.quality}`)),
      h('button.btn.small.danger', { onclick: async () => { await del(`/api/admin/streams/${s.id}`); load(); } }, 'Arrêter'))))
      : h('div.empty', 'Aucune lecture en cours.'));
  };
  await load();
  const t = setInterval(load, 10000);
  ctx.setCleanup(() => clearInterval(t));
}

export async function renderAdmin(main, tab, ctx) {
  const body = h('div.admin-body');
  clear(main, h('div.page-head', h('h1', 'Administration')), tabs(tab), body);
  if (tab === 'plex') await plexTab(body, ctx);
  else if (tab === 'streams') await streamsTab(body, ctx);
  else await usersTab(body, ctx);
}
