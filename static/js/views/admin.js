import { del, get, post, put } from '../api.js';
import { refreshMe, state } from '../app.js';
import { avatar, clear, confirmDialog, field, fmtTime, h, icon, modal, select, toast, toggle } from '../ui.js';

function tabs(active) {
  const t = (id, label) => h('a.tab', { href: `#/admin/${id}`, class: id === active ? 'tab active' : 'tab' }, label);
  return h('div.tabs', t('libraries', 'Bibliothèques'), t('shares', 'Partages réseau'), t('users', 'Utilisateurs'), t('settings', 'Métadonnées et lecture'),
    t('tasks', 'Tâches planifiées'), t('live', 'TV en direct'), t('requests', 'Demandes'), t('streams', 'Lectures en cours'));
}

function since(ts) {
  if (!ts) return 'jamais';
  const s = Date.now() / 1000 - ts;
  if (s < 120) return 'à l’instant';
  if (s < 3600) return `il y a ${Math.round(s / 60)} min`;
  if (s < 86400) return `il y a ${Math.round(s / 3600)} h`;
  return `il y a ${Math.round(s / 86400)} j`;
}

// ------------------------------------------------------------------ users
const AGE_LIMITS = [['', 'Aucune restriction'], ['7', 'Jeunes enfants (7 ans max.)'], ['10', 'Enfant (10 ans max.)'],
  ['12', '12 ans max.'], ['16', '16 ans max.']];

async function userForm(user, libraries) {
  const isNew = !user;
  user = user || { username: '', display_name: '', is_admin: false, all_libraries: true, libraries: [], disabled: false,
    managed: false, home: true, max_age: null, allow_unrated: true, has_pin: false };
  const username = h('input', { value: user.username, disabled: !isNew, autocomplete: 'off' });
  const display = h('input', { value: user.display_name, maxlength: 50 });
  const pass = h('input', { type: 'password', autocomplete: 'new-password',
    placeholder: isNew ? '8 caractères minimum' : 'Laisser vide pour ne pas changer' });
  const managed = toggle('Profil géré : pas de mot de passe, on y accède par « Changer de profil » (idéal pour les enfants)', user.managed);
  const home = toggle('Fait partie du foyer (apparaît dans « Qui regarde ? »)', user.home);
  const pin = h('input', { type: 'password', inputmode: 'numeric', maxlength: 8, autocomplete: 'off',
    placeholder: user.has_pin ? 'Code défini (laisser vide pour le garder)' : '4 à 8 chiffres (facultatif)' });
  const clearPin = user.has_pin ? toggle('Retirer le code PIN', false) : null;
  const age = select(AGE_LIMITS, user.max_age == null ? '' : String(user.max_age));
  const unrated = toggle('Autoriser les contenus sans classification', user.allow_unrated);
  const admin = toggle('Administrateur', user.is_admin);
  const disabled = toggle('Compte désactivé', user.disabled);
  const all = toggle('Accès à toutes les bibliothèques (y compris les futures)', user.all_libraries);
  const libChecks = libraries.map((l) => {
    const t = toggle(l.name, user.libraries.includes(l.id));
    t.dataset.id = l.id;
    return t;
  });
  const libBox = h('div.checks', libChecks);
  const passField = field(isNew ? 'Mot de passe' : 'Nouveau mot de passe', pass);
  const sync = () => {
    libBox.hidden = all.querySelector('input').checked;
    const isManaged = managed.querySelector('input').checked;
    passField.hidden = isManaged;
    admin.hidden = isManaged;
    unrated.hidden = !age.value;
  };
  [all, managed].forEach((t) => t.querySelector('input').addEventListener('change', sync));
  age.addEventListener('change', sync);
  sync();
  const err = h('p.form-error');
  const body = h('div.form',
    h('div.grid2', field('Identifiant', username), field('Nom affiché', display)),
    managed, passField,
    h('h4.form-sub', 'Profil et contrôle parental'),
    home,
    h('div.grid2', field('Code PIN du profil', pin), field('Restriction d’âge', age, 'Selon la classification (TMDB / .nfo).')),
    clearPin, unrated,
    h('h4.form-sub', 'Droits et bibliothèques'),
    admin, isNew ? null : disabled, all, libBox, err);
  return modal(isNew ? 'Nouvel utilisateur' : `Modifier ${user.display_name}`, body, [
    { label: 'Annuler', value: null },
    { label: isNew ? 'Créer' : 'Enregistrer', primary: true, handler: async () => {
      const isManaged = managed.querySelector('input').checked;
      const payload = {
        display_name: display.value.trim() || username.value.trim(),
        is_admin: !isManaged && admin.querySelector('input').checked,
        all_libraries: all.querySelector('input').checked,
        libraries: libChecks.filter((t) => t.querySelector('input').checked).map((t) => t.dataset.id),
        managed: isManaged,
        home: home.querySelector('input').checked,
        max_age: age.value ? Number(age.value) : null,
        allow_unrated: unrated.querySelector('input').checked,
      };
      if (pass.value && !isManaged) payload.password = pass.value;
      if (pin.value) payload.pin = pin.value;
      else if (clearPin && clearPin.querySelector('input').checked) payload.pin = '';
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
  const [users, libs] = await Promise.all([get('/api/admin/users'), get('/api/admin/libraries')]);
  if (!ctx.isCurrent()) return;
  const libraries = libs.libraries;
  const reload = () => usersTab(body, ctx);
  const libName = (id) => (libraries.find((l) => l.id === id) || { name: `#${id}` }).name;
  clear(body,
    h('div.toolbar', h('p.muted', `${users.length} compte(s). Chaque compte a son propre historique, sa reprise de lecture et ses préférences.`),
      h('button.btn.primary', { onclick: async () => { if (await userForm(null, libraries)) { toast('Utilisateur créé', 'ok'); reload(); } } }, '+ Ajouter')),
    h('div.user-list', users.map((u) => h('div.user-row', { class: u.disabled ? 'user-row off' : 'user-row' },
      avatar(u, 40),
      h('div.grow', h('strong', u.display_name), h('span.muted', ` @${u.username}`),
        h('div.muted.small',
          u.is_admin ? h('span.tag.accent', 'Admin') : null,
          u.disabled ? h('span.tag', 'Désactivé') : null,
          u.managed ? h('span.tag', 'Profil géré') : null,
          u.max_age != null ? h('span.tag', `≤ ${u.max_age} ans`) : null,
          u.has_pin ? h('span.tag', 'PIN') : null,
          u.all_libraries ? 'Toutes les bibliothèques' : (u.libraries.map(libName).join(', ') || 'Aucune bibliothèque'),
          ` · vu ${since(u.last_seen)}`)),
      h('button.btn.small', { onclick: async () => { if (await userForm(u, libraries)) { toast('Modifications enregistrées', 'ok'); reload(); } } }, 'Modifier'),
      u.id === state.me.user.id ? null : h('button.icon-btn', { title: 'Supprimer', onclick: async () => {
        if (!await confirmDialog('Supprimer ce compte ?', `${u.display_name} perdra son historique et ses préférences. Cette action est définitive.`, 'Supprimer')) return;
        try { await del(`/api/admin/users/${u.id}`); toast('Compte supprimé', 'ok'); reload(); } catch (e) { toast(e.message, 'bad'); }
      } }, icon('trash'))))));
}

// -------------------------------------------------------------- libraries
async function pickFolder(start) {
  let current = start || '/';
  const list = h('div.folder-list');
  const pathEl = h('div.folder-path');
  const load = async (p) => {
    try {
      const r = await get(`/api/admin/browse?path=${encodeURIComponent(p)}`);
      current = r.path;
      pathEl.textContent = r.path;
      list.replaceChildren(
        r.parent ? h('button.folder', { onclick: () => load(r.parent) }, '⬑ Dossier parent') : null,
        ...r.dirs.map((d) => h('button.folder', { onclick: () => load(`${r.path === '/' ? '' : r.path}/${d}`) }, `📁 ${d}`)),
        r.dirs.length ? null : h('p.muted.small', 'Aucun sous-dossier.'));
    } catch (e) { toast(e.message, 'bad'); }
  };
  await load(current);
  return modal('Choisir un dossier', h('div', pathEl, list), [
    { label: 'Annuler', value: null },
    { label: 'Choisir ce dossier', primary: true, handler: () => current },
  ]);
}

async function libraryForm(lib) {
  const isNew = !lib || !lib.id;
  lib = lib || { name: '', kind: 'movie', paths: [] };
  const name = h('input', { value: lib.name, maxlength: 40, placeholder: 'Films, Séries, Animes…' });
  const kind = select([['movie', 'Films'], ['show', 'Séries (et animes)']], lib.kind);
  const paths = h('div.paths');
  const addPath = (value = '') => {
    const input = h('input', { value, placeholder: '/mnt/films' });
    const rowEl = h('div.path-row', input,
      h('button.btn.small', { type: 'button', onclick: async () => {
        const p = await pickFolder(input.value || '/mnt');
        if (p) input.value = p;
      } }, 'Parcourir…'),
      h('button.icon-btn', { type: 'button', title: 'Retirer', onclick: () => rowEl.remove() }, icon('close')));
    paths.append(rowEl);
  };
  (lib.paths.length ? lib.paths : ['']).forEach(addPath);
  const err = h('p.form-error');
  const body = h('div.form',
    h('div.grid2', field('Nom', name), field('Type de contenu', kind)),
    field('Dossiers', paths, 'Chemins vus depuis le serveur Séance (ex. /mnt/films). Un dossier par sous-dossier de film ou de série.'),
    h('button.btn.small.ghost', { type: 'button', onclick: () => addPath() }, '+ Ajouter un dossier'),
    isNew ? null : h('p.muted.small', 'Changer le type vide la bibliothèque avant de la rescanner.'), err);
  return modal(isNew ? 'Nouvelle bibliothèque' : `Modifier « ${lib.name} »`, body, [
    { label: 'Annuler', value: null },
    { label: isNew ? 'Créer et scanner' : 'Enregistrer et rescanner', primary: true, handler: async () => {
      const payload = { name: name.value.trim(), kind: kind.value,
        paths: [...paths.querySelectorAll('input')].map((i) => i.value.trim()).filter(Boolean) };
      try {
        if (isNew) await post('/api/admin/libraries', payload);
        else await put(`/api/admin/libraries/${lib.id}`, payload);
        return true;
      } catch (e) { err.textContent = e.message; return false; }
    } },
  ]);
}

function scanBox(scan) {
  if (!scan.running && !scan.finished_at) return null;
  const pct = scan.total ? Math.round((scan.done / scan.total) * 100) : 0;
  return h('section.panel.scan',
    h('div.scan-head', h('strong', scan.running ? `Scan en cours — ${scan.phase}` : `Dernier scan terminé ${since(scan.finished_at)}`),
      scan.running && scan.library ? h('span.muted', ` · ${scan.library}`) : null),
    scan.running ? h('div.bar', h('div.bar-fill', { style: { width: `${pct}%` } })) : null,
    scan.running && scan.total ? h('div.muted.small', `${scan.done} / ${scan.total}`) : null,
    !scan.running ? h('div.muted.small', `${scan.added} ajout(s), ${scan.removed} suppression(s)`) : null,
    scan.unrecognized.length ? h('details', h('summary', `${scan.unrecognized.length} fichier(s) non reconnu(s) comme épisode`),
      h('ul.small', scan.unrecognized.map((f) => h('li', f))),
      h('p.muted.small', 'Renomme-les avec S01E02 ou « Nom - 02 » pour qu’ils soient reconnus.')) : null,
    scan.errors.length ? h('details', { open: true }, h('summary', `${scan.errors.length} erreur(s)`),
      h('ul.small', scan.errors.map((f) => h('li', f)))) : null);
}

async function librariesTab(body, ctx) {
  const data = await get('/api/admin/libraries');
  if (!ctx.isCurrent()) return;
  const reload = () => librariesTab(body, ctx);
  const startScan = async (payload) => {
    try { await post('/api/admin/scan', payload); toast('Scan lancé', 'ok'); reload(); } catch (e) { toast(e.message, 'bad'); }
  };
  const c = (l, k) => l.counts[k] || 0;
  clear(body,
    h('div.toolbar', h('p.muted', 'Chaque bibliothèque devient un onglet. Les animes peuvent avoir leur propre bibliothèque de type « Séries ».'),
      h('div.actions-row',
        data.libraries.length ? h('button.btn', { onclick: () => startScan({}) }, 'Tout rescanner') : null,
        h('button.btn.primary', { onclick: async () => { if (await libraryForm(null)) { await refreshMe(); toast('Bibliothèque créée, scan en cours', 'ok'); reload(); } } }, '+ Ajouter'))),
    scanBox(data.scan),
    data.libraries.length ? h('div.user-list', data.libraries.map((l) => h('div.user-row',
      h('div.lib-icon', l.kind === 'movie' ? '🎬' : '📺'),
      h('div.grow', h('strong', l.name),
        h('div.muted.small', l.kind === 'movie' ? `${c(l, 'movie')} films` : `${c(l, 'show')} séries · ${c(l, 'episode')} épisodes`,
          l.unmatched ? h('span', ` · ${l.unmatched} non identifié(s)`) : null,
          ` · scanné ${since(l.scanned_at)}`),
        h('div.small', l.paths.map((p, i) => h('code', { class: l.paths_ok[i] ? '' : 'bad-path', title: l.paths_ok[i] ? '' : 'Dossier inaccessible' }, p)))),
      h('button.btn.small', { onclick: () => startScan({ library: l.id }) }, 'Scanner'),
      h('button.btn.small', { onclick: async () => { if (await libraryForm(l)) { await refreshMe(); reload(); } } }, 'Modifier'),
      h('button.icon-btn', { title: 'Supprimer', onclick: async () => {
        if (!await confirmDialog('Supprimer cette bibliothèque ?', `« ${l.name} » disparaîtra de Séance. Tes fichiers ne sont pas touchés.`, 'Supprimer')) return;
        try { await del(`/api/admin/libraries/${l.id}`); await refreshMe(); reload(); } catch (e) { toast(e.message, 'bad'); }
      } }, icon('trash')))))
      : h('div.empty', h('p', 'Aucune bibliothèque.'), h('p.muted.small', 'Ajoute par exemple « Films » → /mnt/films, « Séries » → /mnt/series, « Animes » → /mnt/animes.')));
  if (data.scan.running) {
    const t = setTimeout(async () => { if (ctx.isCurrent()) { await refreshMe().catch(() => {}); reload(); } }, 1500);
    ctx.setCleanup(() => clearTimeout(t));
  }
}

// ----------------------------------------------------------- partages SMB
const SMB_VERSIONS = [['auto', 'Automatique'], ['3.1.1', 'SMB 3.1.1'], ['3.0', 'SMB 3.0'], ['2.1', 'SMB 2.1'],
  ['2.0', 'SMB 2.0'], ['1.0', 'SMB 1 (ancien NAS)']];

const slug = (t) => t.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
  .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32) || 'partage';

async function shareForm(existing) {
  const isNew = !existing;
  const e = existing || { host: '', share: '', subdir: '', username: '', version: 'auto', name: '' };
  const host = h('input', { value: e.host, placeholder: '192.168.1.5' });
  const user = h('input', { value: e.username, autocomplete: 'off' });
  const pass = h('input', { type: 'password', autocomplete: 'new-password',
    placeholder: isNew ? '' : 'Laisser vide pour garder le mot de passe actuel' });
  const domain = h('input', { placeholder: 'WORKGROUP (facultatif)' });
  const version = select(SMB_VERSIONS, e.version || 'auto');
  const share = h('input', { value: e.share, placeholder: 'Films' });
  const subdir = h('input', { value: e.subdir || '', placeholder: 'facultatif, ex. Films/HD' });
  const name = h('input', { value: e.name, disabled: !isNew, placeholder: 'films' });
  let nameTouched = !isNew;
  name.addEventListener('input', () => { nameTouched = true; });
  share.addEventListener('input', () => { if (!nameTouched) name.value = slug(share.value); });
  const found = h('div.share-list');
  const err = h('p.form-error');
  const creds = () => ({ host: host.value.trim(), username: user.value, password: pass.value, domain: domain.value.trim(),
    version: version.value, reuse: isNew ? undefined : e.name });
  const discover = h('button.btn', { type: 'button', onclick: async () => {
    err.textContent = '';
    discover.disabled = true;
    found.replaceChildren(h('p.muted.small', 'Connexion au NAS…'));
    try {
      const r = await post('/api/admin/shares/discover', creds());
      found.replaceChildren(...(r.shares.length ? r.shares.map((x) => h('button.share-pick', { type: 'button',
        class: x.name === share.value ? 'share-pick on' : 'share-pick',
        onclick: (ev) => {
          share.value = x.name;
          if (!nameTouched) name.value = slug(x.name);
          found.querySelectorAll('.share-pick').forEach((b) => b.classList.remove('on'));
          ev.currentTarget.classList.add('on');
        } }, '🗂 ', h('strong', x.name), x.comment ? h('span.muted.small', ` ${x.comment}`) : null))
        : [h('p.muted.small', 'Aucun partage visible avec ce compte.')]));
    } catch (ex) { found.replaceChildren(); err.textContent = ex.message; } finally { discover.disabled = false; }
  } }, 'Voir les partages du NAS');
  const body = h('div.form',
    h('div.grid2', field('Adresse du NAS', host), field('Identifiant', user)),
    field('Mot de passe', pass),
    h('div.actions-row', discover), found,
    h('div.grid2', field('Partage', share), field('Nom local', name, 'Monté dans /mnt/seance/<nom>')),
    h('details.advanced', h('summary', 'Options avancées'),
      h('div.grid2', field('Sous-dossier du partage', subdir), field('Version SMB', version)), field('Domaine', domain)),
    h('p.muted.small', 'Le partage est monté en lecture seule : Séance ne peut ni modifier ni supprimer tes fichiers. Le mot de passe est stocké côté système (lisible par root uniquement), jamais dans la base de Séance.'),
    err);
  return modal(isNew ? 'Ajouter un partage réseau (SMB)' : `Modifier « ${e.name} »`, body, [
    { label: 'Annuler', value: null },
    { label: 'Monter le partage', primary: true, handler: async () => {
      err.textContent = 'Montage en cours…';
      try {
        const r = await post('/api/admin/shares', { name: name.value.trim(), host: host.value.trim(), share: share.value.trim(),
          subdir: subdir.value.trim(), username: user.value, password: pass.value, domain: domain.value.trim(),
          version: version.value });
        return r.path;
      } catch (ex) { err.textContent = ex.message; return false; }
    } },
  ]);
}

async function sharesTab(body, ctx) {
  const data = await get('/api/admin/shares');
  if (!ctx.isCurrent()) return;
  const reload = () => sharesTab(body, ctx);
  const offerLibrary = async (path, label) => {
    const ok = await libraryForm({ name: label.charAt(0).toUpperCase() + label.slice(1), kind: 'movie', paths: [path] });
    if (ok) { await refreshMe(); toast('Bibliothèque créée, scan en cours', 'ok'); }
    reload();
  };
  if (!data.available) {
    clear(body, h('section.panel', h('h2', 'Partages réseau'), h('div.plex-status.bad', data.error),
      h('p.muted.small', 'Relance le script d’installation pour (ré)installer le service seance-mountd, puis : systemctl status seance-mountd')));
    return;
  }
  const env = data.env;
  const warning = env.unprivileged ? h('section.panel.warn',
    h('h2', 'Ce conteneur ne peut pas monter de partages'),
    h('p', 'Linux interdit le montage SMB dans un conteneur LXC non privilégié, quelle que soit l’application. Tu peux quand même lister les partages de ton NAS ici, mais le montage échouera. Deux solutions :'),
    h('ol',
      h('li', h('strong', 'Conteneur privilégié'), ' (le plus simple pour gérer les partages depuis Séance) : sauvegarde le conteneur puis restaure-le en « privilégié » (Proxmox : Sauvegarde → Restaurer, décocher « Non privilégié »), et active l’option ', h('code', 'pct set <ID> -features mount=cifs,nesting=1'), '.'),
      h('li', h('strong', 'Garder le conteneur non privilégié'), ' : monter les partages sur l’hôte Proxmox et les prêter au conteneur (méthode détaillée dans le README). Ils apparaîtront alors directement dans l’explorateur de dossiers des bibliothèques.'))) : null;
  const tools = env.tools && (!env.tools['mount.cifs'] || !env.tools.smbclient)
    ? h('div.plex-status.bad', 'cifs-utils ou smbclient manquant : relance le script d’installation.') : null;
  clear(body,
    h('div.toolbar', h('p.muted', 'Monte les dossiers partagés de ton NAS (SMB/CIFS) directement depuis Séance, puis crée tes bibliothèques dessus.'),
      h('button.btn.primary', { onclick: async () => {
        const path = await shareForm(null);
        if (path) {
          toast('Partage monté', 'ok');
          if (await confirmDialog('Partage monté', `Le partage est disponible dans ${path}. Créer une bibliothèque avec ce dossier ?`, 'Créer une bibliothèque', false)) {
            await offerLibrary(path, path.split('/').pop());
            return;
          }
          reload();
        }
      } }, '+ Ajouter un partage')),
    warning, tools,
    data.shares.length ? h('div.user-list', data.shares.map((sh) => h('div.user-row',
      h('div.lib-icon', '🗄️'),
      h('div.grow', h('strong', sh.name), h('span.muted', ` //${sh.host}/${sh.share}${sh.subdir ? `/${sh.subdir}` : ''}`),
        h('div.small', h('code', sh.path),
          sh.mounted ? h('span.tag.ok', 'Monté') : h('span.tag.bad', 'Non monté'),
          sh.username ? h('span.muted', ` · compte ${sh.username}`) : null,
          sh.libraries.length ? h('span.muted', ` · utilisé par ${sh.libraries.join(', ')}`) : null),
        sh.error ? h('div.form-error.small', sh.error) : null),
      sh.mounted && !sh.libraries.length ? h('button.btn.small', { onclick: () => offerLibrary(sh.path, sh.share) }, 'Créer une bibliothèque') : null,
      sh.mounted ? null : h('button.btn.small', { onclick: async () => {
        try { await post(`/api/admin/shares/${sh.name}/remount`); toast('Partage remonté', 'ok'); } catch (e) { toast(e.message, 'bad'); }
        reload();
      } }, 'Remonter'),
      h('button.btn.small', { onclick: async () => { if (await shareForm(sh)) { toast('Partage mis à jour', 'ok'); reload(); } } }, 'Modifier'),
      h('button.icon-btn', { title: 'Démonter et retirer', onclick: async () => {
        const used = sh.libraries.length ? ` Les bibliothèques ${sh.libraries.join(', ')} ne trouveront plus leurs fichiers (leur contenu est conservé tant qu’elles ne sont pas rescannées avec le partage monté).` : '';
        if (!await confirmDialog('Retirer ce partage ?', `Le partage sera démonté et oublié. Tes fichiers sur le NAS ne sont pas touchés.${used}`, 'Retirer')) return;
        try { await del(`/api/admin/shares/${sh.name}`); toast('Partage retiré', 'ok'); reload(); } catch (e) { toast(e.message, 'bad'); }
      } }, icon('trash')))))
      : h('div.empty', 'Aucun partage réseau géré par Séance.'));
}

// --------------------------------------------------------------- settings
async function settingsTab(body, ctx) {
  const s = await get('/api/admin/settings');
  if (!ctx.isCurrent()) return;
  const key = h('input', { type: 'password', autocomplete: 'off',
    placeholder: s.tmdb_set ? 'Clé enregistrée (laisser vide pour la garder)' : 'Clé API (v3) ou jeton de lecture (v4)' });
  const lang = select([['fr-FR', 'Français (France)'], ['fr-CA', 'Français (Canada)'], ['en-US', 'Anglais'], ['de-DE', 'Allemand'],
    ['es-ES', 'Espagnol'], ['it-IT', 'Italien']], s.language);
  const watch = select([['0', 'Désactivée'], ['30', 'Toutes les 30 secondes'], ['60', 'Toutes les minutes'], ['300', 'Toutes les 5 minutes'],
    ['900', 'Toutes les 15 minutes']], String(s.watch_interval));
  const hookInput = h('input', { readonly: true, value: `${location.origin}${s.webhook_path}`, onfocus: (e) => e.target.select() });
  const prefetch = toggle('Préparer les sous-titres des nouveaux fichiers à l’avance', s.subs_prefetch);
  prefetch.querySelector('input').addEventListener('change', (e) => save({ subs_prefetch: e.target.checked }, 'Enregistré'));
  const maxStreams = h('input', { type: 'number', min: 1, max: 50, value: s.max_streams });
  const maxTranscodes = h('input', { type: 'number', min: 0, max: 16, value: s.max_transcodes });
  const save = async (payload, msg) => {
    try { await put('/api/admin/settings', payload); toast(msg, 'ok'); settingsTab(body, ctx); } catch (e) { toast(e.message, 'bad'); }
  };
  clear(body,
    h('section.panel', h('h2', 'Métadonnées (TMDB)'),
      h('p.muted.small', 'Affiches, résumés, distribution et titres d’épisodes viennent de The Movie Database. Crée un compte gratuit sur themoviedb.org, puis Paramètres → API → « Clé d’API ». Sans clé, Séance utilise les noms de fichiers, les .nfo et les images présents dans tes dossiers.'),
      h('div.plex-status', { class: s.tmdb_set ? 'plex-status ok' : 'plex-status' }, s.tmdb_set ? 'Clé TMDB configurée.' : 'Aucune clé TMDB.'),
      h('div.grid2', field('Clé TMDB', key), field('Langue des métadonnées', lang)),
      h('div.actions-row',
        h('button.btn.primary', { onclick: () => save({ ...(key.value.trim() ? { tmdb_key: key.value.trim() } : {}), language: lang.value }, 'Métadonnées enregistrées') }, 'Enregistrer'),
        s.tmdb_set ? h('button.btn', { onclick: () => save({ tmdb_key: '' }, 'Clé supprimée') }, 'Supprimer la clé') : null,
        h('button.btn', { onclick: async () => {
          try { await post('/api/admin/scan', { refresh_metadata: true }); toast('Actualisation de toutes les métadonnées lancée', 'ok'); }
          catch (e) { toast(e.message, 'bad'); }
        } }, 'Tout réactualiser'),
        h('a.btn.ghost', { href: '#/admin/tasks' }, 'Planifier l’actualisation…'))),
    h('section.panel', h('h2', 'Détection des nouveautés'),
      h('p.muted.small', 'Sur un partage réseau, le système n’est pas prévenu des fichiers ajoutés depuis une autre machine. Séance surveille donc les dossiers (très léger) et rescanne la bibliothèque concernée dès qu’une copie est terminée. Pour un ajout instantané depuis Radarr / Sonarr, utilise le webhook ci-dessous.'),
      h('div.grid2',
        field('Surveiller les dossiers', watch, 'Le scan complet de sécurité se règle dans « Tâches planifiées ».')),
      h('button.btn.primary', { onclick: () => save({ watch_interval: Number(watch.value) }, 'Enregistré') }, 'Enregistrer'),
      h('h3.sub', 'Webhook Radarr / Sonarr'),
      h('div.copy-row', hookInput,
        h('button.btn.small', { onclick: async () => {
          try { await navigator.clipboard.writeText(hookInput.value); toast('Adresse copiée', 'ok'); }
          catch { hookInput.select(); }
        } }, 'Copier'),
        h('button.btn.small.ghost', { onclick: async () => {
          if (!await confirmDialog('Nouvelle adresse ?', 'L’ancienne adresse cessera de fonctionner : il faudra la remplacer dans Radarr et Sonarr.', 'Régénérer')) return;
          await post('/api/admin/webhook/regenerate'); settingsTab(body, ctx);
        } }, 'Régénérer')),
      h('p.muted.small', 'Dans Radarr et Sonarr : Paramètres → Connect → + → Webhook. Colle cette adresse dans « URL », méthode POST, et coche « On Import », « On Upgrade », « On Rename » et « On Delete ». Si Radarr est sur une autre machine, remplace l’adresse par celle du serveur Séance vue depuis Radarr.'),
      s.watch_events.length ? h('details', h('summary.small', 'Derniers déclenchements'),
        h('ul.small', s.watch_events.map((e) => h('li', `${new Date(e.t * 1000).toLocaleString('fr-FR')} — ${e.text}`)))) : null),
    h('section.panel', h('h2', 'Sous-titres'),
      h('p.muted.small', 'Les sous-titres intégrés aux fichiers (MKV) ne peuvent être lus qu’en parcourant tout le fichier. Séance les prépare à l’avance (toutes les pistes en une seule lecture), quand aucune lecture n’est en cours, pour qu’ils soient là dès la première seconde. Au lancement, le lecteur attend qu’ils soient prêts.'),
      prefetch,
      h('p.muted.small', s.subs_queue ? `${s.subs_queue} fichier(s) en attente de préparation.` : 'Aucun fichier en attente.'),
      h('button.btn', { onclick: () => save({ subs_prefetch: true }, 'Préparation lancée pour toute la médiathèque') }, 'Tout préparer maintenant')),
    h('section.panel', h('h2', 'Son multicanal (5.1 / 7.1) sur des enceintes stéréo'),
      h('p.muted.small', 'Mêmes réglages que Jellyfin. « ffmpeg » garde un équilibre neutre ; « Dave750 » met un peu plus en avant les dialogues ; « Dialogues (nuit) » réduit fortement les effets et la musique ; « RFC 7845 » et « AC-4 » sont les mixages normalisés. Le gain remonte le volume après le mixage ; Séance ajoute toujours un limiteur pour qu’il ne puisse pas saturer.'),
      h('div.grid2',
        field('Mixage', select([['none', 'ffmpeg (Jellyfin par défaut)'], ['dave750', 'Dave750'], ['nightmode', 'Dialogues (nuit)'],
          ['rfc7845', 'RFC 7845'], ['ac4', 'AC-4']], s.downmix_algo, { id: 'downmix-algo' })),
        field('Gain après mixage', select([['1', '× 1 (aucun)'], ['1.5', '× 1,5'], ['2', '× 2 (Jellyfin par défaut)'], ['2.5', '× 2,5'], ['3', '× 3']],
          String(s.downmix_boost), { id: 'downmix-boost' }))),
      h('button.btn.primary', { onclick: () => save({ downmix_algo: document.getElementById('downmix-algo').value,
        downmix_boost: Number(document.getElementById('downmix-boost').value) }, 'Mixage enregistré (prochaines lectures)') }, 'Enregistrer')),
    h('section.panel', h('h2', 'Lecture et processeur'),
      h('p.muted.small', `Ce serveur a ${s.cpus} cœur(s) et pas d’accélération matérielle. Une lecture directe ou un remux ne coûtent presque rien ; un transcodage (HEVC, 10 bits, sous-titres image, qualité réduite) occupe 1 à 2 cœurs par lecture en 1080p.`),
      s.ffmpeg ? null : h('div.plex-status.bad', 'ffmpeg est introuvable : installe-le (apt install ffmpeg).'),
      h('div.grid2', field('Lectures simultanées (max.)', maxStreams), field('Transcodages simultanés (max.)', maxTranscodes,
        '0 = interdire le transcodage (seules les lectures directes et remux passent).')),
      h('button.btn.primary', { onclick: () => save({ max_streams: Number(maxStreams.value), max_transcodes: Number(maxTranscodes.value) }, 'Enregistré') }, 'Enregistrer')),
    h('p.muted.small', `Séance ${s.version}`));
}

// --------------------------------------------------------------- demandes
async function requestsTab(body, ctx) {
  const cfg = await get('/api/admin/requests');
  if (!ctx.isCurrent()) return;
  const reload = () => requestsTab(body, ctx);
  const url = h('input', { value: cfg.seerr_url, placeholder: 'http://192.168.1.20:5055', autocomplete: 'off' });
  const key = h('input', { type: 'password', autocomplete: 'off',
    placeholder: cfg.seerr_key_set ? 'Clé enregistrée (laisser vide pour la garder)' : 'Paramètres → Général → Clé d’API' });
  const auto = toggle('Demander automatiquement les titres ajoutés à « À regarder » qui ne sont pas sur le serveur', cfg.auto_request);
  auto.querySelector('input').addEventListener('change', async (e) => {
    try { await put('/api/admin/requests', { auto_request: e.target.checked }); toast('Enregistré', 'ok'); } catch (err) { toast(err.message, 'bad'); }
  });
  const status = h('div.plex-status', { class: cfg.enabled ? 'plex-status ok' : 'plex-status' },
    cfg.enabled ? 'Seerr est configuré : les utilisateurs peuvent demander des films et des séries.' : 'Seerr n’est pas encore configuré.');
  clear(body,
    h('section.panel', h('h2', 'Seerr'),
      h('p.muted.small', 'Seerr (le successeur d’Overseerr et de Jellyseerr) reçoit les demandes du bouton « Demander » de l’onglet Découvrir et les envoie à Radarr / Sonarr. Si un compte Seerr porte le même nom qu’un compte Séance, la demande est faite à son nom ; sinon, au nom du propriétaire de la clé d’API.'),
      status,
      h('div.grid2', field('Adresse de Seerr', url), field('Clé d’API de Seerr', key)),
      h('div.actions-row',
        h('button.btn.primary', { onclick: async () => {
          try {
            await put('/api/admin/requests', { seerr_url: url.value.trim(), ...(key.value.trim() ? { seerr_key: key.value.trim() } : {}) });
            const t = await post('/api/admin/requests/test');
            toast(`Connecté à ${t.title} (serveur : ${t.media_server})`, 'ok');
            await refreshMe();
            reload();
          } catch (e) { toast(e.message, 'bad'); reload(); }
        } }, 'Enregistrer et tester'),
        cfg.enabled ? h('button.btn', { onclick: async () => {
          try { const t = await post('/api/admin/requests/test'); toast(`Connecté à ${t.title} (serveur : ${t.media_server})`, 'ok'); }
          catch (e) { toast(e.message, 'bad'); }
        } }, 'Tester') : null,
        cfg.enabled ? h('button.btn.ghost', { onclick: async () => {
          await put('/api/admin/requests', { seerr_url: '', seerr_key: '' }); await refreshMe(); reload();
        } }, 'Déconnecter') : null),
      auto,
      h('p.muted.small', `Catalogues affichés pour le pays : ${cfg.region} (celui des métadonnées).`)));
}

// ---------------------------------------------------------- tâches planifiées
const WEEKDAYS = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche'];
const TASK_ICONS = { scan: 'search', metadata: 'film', chapters: 'chapters', intros: 'skip', subtitles: 'subs', livetv: 'tv', cleanup: 'trash' };
const CATEGORIES = ['Bibliothèques', 'Lecture', 'TV en direct', 'Maintenance'];

function scheduleText(s) {
  if (s.mode === 'interval') return s.hours % 24 === 0 && s.hours > 24 ? `Tous les ${s.hours / 24} jours` : (s.hours === 1 ? 'Toutes les heures' : `Toutes les ${s.hours} h`);
  if (s.mode === 'daily') return `Chaque jour à ${s.time}`;
  if (s.mode === 'weekly') return `Chaque ${WEEKDAYS[s.weekday ?? 6]} à ${s.time}`;
  return 'Désactivée';
}

function when(ts) {
  const d = new Date(ts * 1000);
  const today = new Date();
  const tomorrow = new Date(Date.now() + 86400000);
  const time = d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  if (d.toDateString() === today.toDateString()) return `aujourd’hui à ${time}`;
  if (d.toDateString() === tomorrow.toDateString()) return `demain à ${time}`;
  return `${d.toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'short' })} à ${time}`;
}

const duration = (sec) => (sec < 60 ? `${sec} s` : sec < 3600 ? `${Math.round(sec / 60)} min` : `${Math.floor(sec / 3600)} h ${String(Math.round((sec % 3600) / 60)).padStart(2, '0')}`);

async function taskSettings(task, libraries) {
  const sc = task.schedule;
  const mode = select([['off', 'Désactivée (seulement à la main)'], ['interval', 'À intervalle régulier'], ['daily', 'Chaque jour'],
    ['weekly', 'Chaque semaine']], sc.mode);
  const hours = select([[1, '1 heure'], [2, '2 heures'], [3, '3 heures'], [6, '6 heures'], [12, '12 heures'], [24, '24 heures'],
    [48, '2 jours'], [72, '3 jours'], [168, '7 jours']], String(sc.hours || 24));
  const time = h('input', { type: 'time', value: sc.time || '03:00' });
  const weekday = select(WEEKDAYS.map((d, i) => [i, d.charAt(0).toUpperCase() + d.slice(1)]), String(sc.weekday ?? 6));
  const fHours = field('Toutes les', hours);
  const fDay = field('Jour', weekday);
  const fTime = field('Heure', time);
  const sync = () => {
    fHours.hidden = mode.value !== 'interval';
    fDay.hidden = mode.value !== 'weekly';
    fTime.hidden = !['daily', 'weekly'].includes(mode.value);
  };
  mode.addEventListener('change', sync);
  sync();
  const inputs = {};
  const optionEls = task.option_defs.map((o) => {
    const v = task.options[o.key];
    if (o.type === 'bool') {
      const t = toggle(o.label, !!v);
      inputs[o.key] = () => t.querySelector('input').checked;
      return h('div', t, o.hint ? h('p.field-hint.toggle-hint', o.hint) : null);
    }
    if (o.type === 'int') {
      const i = h('input', { type: 'number', min: o.min, max: o.max, value: v });
      inputs[o.key] = () => Number(i.value);
      return field(o.label + (o.unit ? ` (${o.unit})` : ''), i, o.hint);
    }
    if (o.type === 'select') {
      const sel = select(o.choices, v);
      inputs[o.key] = () => sel.value;
      return field(o.label, sel, o.hint);
    }
    if (o.type === 'libraries') {
      const libs = libraries.filter((l) => !o.kind || l.kind === o.kind);
      const checks = libs.map((l) => { const t = toggle(l.name, (v || []).includes(l.id)); t.dataset.id = l.id; return t; });
      inputs[o.key] = () => checks.filter((t) => t.querySelector('input').checked).map((t) => t.dataset.id);
      return libs.length ? field(o.label, h('div.checks', checks), o.hint) : null;
    }
    return null;
  });
  const err = h('p.form-error');
  return modal(task.name, h('div.form',
    h('p.muted.small', task.description),
    h('h4.form-sub', 'Planification'),
    h('div.sched-row', field('Quand', mode), fHours, fDay, fTime),
    optionEls.some(Boolean) ? h('h4.form-sub', 'Options') : null,
    optionEls, err), [
    { label: 'Annuler', value: null },
    { label: 'Enregistrer', primary: true, handler: async () => {
      const schedule = { mode: mode.value };
      if (mode.value === 'interval') schedule.hours = Number(hours.value);
      if (['daily', 'weekly'].includes(mode.value)) schedule.time = time.value || '03:00';
      if (mode.value === 'weekly') schedule.weekday = Number(weekday.value);
      const options = Object.fromEntries(Object.entries(inputs).map(([k, f]) => [k, f()]));
      try { await put(`/api/admin/tasks/${task.id}`, { schedule, options }); return true; } catch (e) { err.textContent = e.message; return false; }
    } },
  ]);
}

async function tasksTab(body, ctx) {
  const data = await get('/api/admin/tasks');
  if (!ctx.isCurrent()) return;
  const reload = () => tasksTab(body, ctx);
  const act = async (path, msg) => { try { await post(path); if (msg) toast(msg, 'ok'); } catch (e) { toast(e.message, 'bad'); } reload(); };
  const row = (t) => {
    const last = t.last;
    const status = last ? { ok: ['ok', 'Terminée'], error: ['bad', 'Échec'], cancelled: ['', 'Arrêtée'] }[last.status] || ['', last.status] : null;
    return h('div.task-row',
      h('div.task-icon', icon(TASK_ICONS[t.id] || 'clock')),
      h('div.grow',
        h('strong', t.name),
        h('p.muted.small.task-desc', t.description),
        h('div.task-meta',
          h('span', icon('clock'), ' ', scheduleText(t.schedule)),
          t.next && !t.running ? h('span', `Prochaine : ${when(t.next)}`) : null,
          last ? h('span', h('span', { class: status[0] }, status[1]), ` ${since(last.start)}`,
            last.end ? ` · ${duration(Math.max(0, last.end - last.start))}` : '', last.summary ? ` · ${last.summary}` : '') : h('span', 'Jamais lancée'),
          t.queued ? h('span.tag', 'En file') : null),
        t.note ? h('p.small', { class: t.available ? 'small muted' : 'small form-error' }, t.note) : null,
        t.running ? h('div', h('div.bar', h('div.bar-fill', { style: { width: `${Math.round(t.running.progress * 100)}%` } })),
          h('div.task-running', t.running.paused ? '⏸ ' : '', t.running.message || 'En cours…',
            h('span.muted', ` · ${Math.round(t.running.progress * 100)} % · ${t.running.reason}`))) : null),
      h('div.task-actions',
        t.running || t.queued
          ? h('button.btn.small', { onclick: () => act(`/api/admin/tasks/${t.id}/cancel`, 'Tâche arrêtée') }, 'Arrêter')
          : h('button.btn.small.primary', { disabled: !t.available, onclick: () => act(`/api/admin/tasks/${t.id}/run`, 'Tâche lancée') }, icon('play'), 'Lancer'),
        h('button.btn.small', { onclick: async () => { if (await taskSettings(t, data.libraries)) { toast('Réglages enregistrés', 'ok'); reload(); } } }, 'Réglages')));
  };
  clear(body,
    h('p.muted', 'Les travaux de fond du serveur. Les tâches lourdes (lecture des fichiers sur le partage) se mettent en pause pendant qu’on regarde quelque chose. Une seule tâche tourne à la fois.'),
    CATEGORIES.map((cat) => {
      const list = data.tasks.filter((t) => t.category === cat);
      return list.length ? h('section.task-group', h('h3', cat), list.map(row)) : null;
    }));
  const busy = data.tasks.some((t) => t.running || t.queued);
  const timer = setTimeout(() => { if (ctx.isCurrent()) reload(); }, busy ? 2500 : 15000);
  ctx.setCleanup(() => clearTimeout(timer));
}

// ------------------------------------------------------------ TV en direct
async function liveSourceForm(src) {
  const isNew = !src;
  src = src || { name: '', m3u_url: '', epg_url: '', user_agent: '', enabled: true };
  const name = h('input', { value: src.name, maxlength: 60, placeholder: 'Mon fournisseur IPTV, Freebox TV…' });
  const m3u = h('input', { value: src.m3u_url, placeholder: 'http://…/playlist.m3u ou /srv/iptv/chaines.m3u' });
  const epg = h('input', { value: src.epg_url, placeholder: 'facultatif : http://…/guide.xml(.gz)' });
  const ua = h('input', { value: src.user_agent, placeholder: 'facultatif : VLC/3.0.20 LibVLC/3.0.20…' });
  const enabled = toggle('Source active', src.enabled);
  const err = h('p.form-error');
  return modal(isNew ? 'Ajouter une source de chaînes' : `Modifier « ${src.name} »`, h('div.form',
    field('Nom', name),
    field('Liste des chaînes (M3U / M3U8)', m3u, 'Adresse fournie par ton fournisseur IPTV ou ta box, ou chemin d’un fichier .m3u sur le serveur.'),
    field('Guide des programmes (XMLTV)', epg, 'Laisse vide si la liste indique déjà son guide (« url-tvg » dans l’en-tête). Les fichiers .gz sont acceptés.'),
    h('details.advanced', h('summary', 'Options avancées'),
      field('User-Agent', ua, 'Certains fournisseurs n’acceptent qu’un lecteur précis (VLC, Kodi…).')),
    isNew ? null : enabled, err), [
    { label: 'Annuler', value: null },
    { label: isNew ? 'Ajouter et charger' : 'Enregistrer', primary: true, handler: async () => {
      err.textContent = isNew ? 'Chargement de la liste…' : '';
      const payload = { name: name.value.trim(), m3u_url: m3u.value.trim(), epg_url: epg.value.trim(), user_agent: ua.value.trim(),
        enabled: enabled.querySelector('input').checked };
      try {
        const r = isNew ? await post('/api/admin/live/sources', payload) : await put(`/api/admin/live/sources/${src.id}`, payload);
        if (r.warning) toast(r.warning, 'bad');
        return true;
      } catch (e) { err.textContent = e.message; return false; }
    } },
  ]);
}

async function liveGroups(src) {
  const groups = await get(`/api/admin/live/sources/${src.id}/groups`);
  const checks = groups.map((g) => { const t = toggle(`${g.name || 'Sans groupe'} (${g.count})`, !g.hidden); t.dataset.name = g.name; return t; });
  const all = (on) => checks.forEach((t) => { t.querySelector('input').checked = on; });
  return modal(`Groupes de « ${src.name} »`, h('div',
    h('p.muted.small', 'Les groupes décochés sont masqués pour tout le monde (pratique pour une liste de plusieurs milliers de chaînes).'),
    h('div.actions-row', h('button.btn.small', { onclick: () => all(true) }, 'Tout cocher'), h('button.btn.small', { onclick: () => all(false) }, 'Tout décocher')),
    h('div.checks.single.scroll-list', checks)), [
    { label: 'Annuler', value: null },
    { label: 'Enregistrer', primary: true, handler: async () => {
      await put(`/api/admin/live/sources/${src.id}/groups`, { hidden: checks.filter((t) => !t.querySelector('input').checked).map((t) => t.dataset.name) });
      return true;
    } },
  ]);
}

async function liveTab(body, ctx) {
  const data = await get('/api/admin/live');
  if (!ctx.isCurrent()) return;
  const reload = async () => { await refreshMe().catch(() => {}); liveTab(body, ctx); };
  const st = data.settings;
  const maxS = h('input', { type: 'number', min: 1, max: 20, value: st.max_sessions });
  const vmode = select([['auto', 'Automatique (H.264 recopié, le reste converti)'], ['copy', 'Ne jamais convertir (le moins de processeur)'],
    ['transcode', 'Toujours convertir en H.264 (le plus compatible)']], st.video);
  const hide = toggle('Masquer la TV en direct pour les profils avec une restriction d’âge', st.hide_restricted);
  clear(body,
    h('section.panel', h('div.toolbar', h('div', h('h2', 'Sources de chaînes'),
      h('p.muted.small', 'Listes M3U (IPTV, box opérateur, TNT via un tuner réseau…) et leur guide XMLTV. Les chaînes sont proposées à tous les utilisateurs dans l’onglet « TV en direct ». L’actualisation automatique se règle dans « Tâches planifiées ».')),
    h('button.btn.primary', { onclick: async () => { if (await liveSourceForm(null)) { toast('Source ajoutée', 'ok'); reload(); } } }, '+ Ajouter')),
    data.sources.length ? h('div.user-list', data.sources.map((s) => h('div.user-row', { class: s.enabled ? 'user-row' : 'user-row off' },
      h('div.lib-icon', '📡'),
      h('div.grow', h('strong', s.name),
        h('div.muted.small', `${s.channels} chaîne(s)`, s.hidden ? ` dont ${s.hidden} masquée(s)` : '', ` · ${s.programmes} programme(s) dans le guide`,
          ` · actualisée ${since(s.refreshed_at)}`),
        h('div.small', h('code', s.m3u_url.length > 70 ? `${s.m3u_url.slice(0, 70)}…` : s.m3u_url)),
        s.error ? h('div.form-error.small', s.error) : null),
      h('button.btn.small', { onclick: async (e) => {
        e.currentTarget.disabled = true;
        try { const r = await post(`/api/admin/live/sources/${s.id}/refresh`); toast(`${r.channels} chaîne(s)${r.programmes != null ? `, ${r.programmes} programme(s)` : ''}`, 'ok'); }
        catch (ex) { toast(ex.message, 'bad'); }
        reload();
      } }, 'Actualiser'),
      h('button.btn.small', { onclick: async () => { if (await liveGroups(s)) { toast('Groupes enregistrés', 'ok'); reload(); } } }, 'Groupes…'),
      h('button.btn.small', { onclick: async () => { if (await liveSourceForm(s)) { toast('Source modifiée', 'ok'); reload(); } } }, 'Modifier'),
      h('button.icon-btn', { title: 'Supprimer', onclick: async () => {
        if (!await confirmDialog('Supprimer cette source ?', `Les chaînes de « ${s.name} » et les favoris associés disparaîtront.`, 'Supprimer')) return;
        await del(`/api/admin/live/sources/${s.id}`); reload();
      } }, icon('trash')))))
      : h('div.empty', 'Aucune source. Ajoute l’adresse de ta liste M3U.')),
    h('section.panel', h('h2', 'Lecture'),
      h('p.muted.small', 'Le navigateur ne lit pas directement les flux TV (MPEG-TS) : Séance les reçoit et les redécoupe pour lui. Une chaîne regardée par plusieurs personnes n’est reçue qu’une fois. Beaucoup de fournisseurs limitent le nombre de connexions simultanées.'),
      h('div.grid2', field('Chaînes regardées en même temps (max.)', maxS), field('Vidéo', vmode)),
      hide,
      h('button.btn.primary', { onclick: async () => {
        try { await put('/api/admin/live/settings', { max_sessions: Number(maxS.value), video: vmode.value, hide_restricted: hide.querySelector('input').checked }); toast('Enregistré', 'ok'); reload(); }
        catch (e) { toast(e.message, 'bad'); }
      } }, 'Enregistrer')),
    data.sessions.length ? h('section.panel', h('h2', 'Chaînes regardées en ce moment'), h('div.user-list', data.sessions.map((x) => h('div.user-row',
      h('div.grow', h('strong', x.channel), h('div.muted.small', `${x.viewers.join(', ') || 'personne'} · ${x.mode === 'transcode' ? 'vidéo convertie' : 'vidéo recopiée'}${x.codec ? ` (${x.codec})` : ''} · depuis ${since(x.since)}`),
        x.error ? h('div.form-error.small', x.error) : null),
      h('button.btn.small.danger', { onclick: async () => { await del(`/api/admin/live/sessions/${x.sid}`); reload(); } }, 'Arrêter'))))) : null);
}

// ---------------------------------------------------------------- streams
const MODES = { direct: 'Lecture directe', copy: 'Remux (vidéo copiée)', transcode: 'Transcodage' };

async function streamsTab(body, ctx) {
  const load = async () => {
    const list = await get('/api/admin/streams');
    if (!ctx.isCurrent()) return;
    clear(body, list.length ? h('div.user-list', list.map((s) => h('div.user-row',
      h('div.grow', h('strong', s.title), h('div.muted.small',
        `${s.user} · ${s.state === 'paused' ? 'en pause' : 'en lecture'} à ${fmtTime(s.position_ms)} · ${MODES[s.mode] || s.mode}`,
        s.reasons && s.reasons.length ? ` (${s.reasons.join(', ')})` : '',
        s.speed ? h('span', { class: s.speed < 1.05 && s.mode === 'transcode' ? 'tag bad' : 'tag ok' }, `vitesse ×${s.speed}`) : null),
        s.warnings && s.warnings.length ? h('details.small', h('summary', 'Messages de ffmpeg'), h('pre.log', s.warnings.join('\n'))) : null),
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
  if (tab === 'users') await usersTab(body, ctx);
  else if (tab === 'settings') await settingsTab(body, ctx);
  else if (tab === 'streams') await streamsTab(body, ctx);
  else if (tab === 'requests') await requestsTab(body, ctx);
  else if (tab === 'tasks') await tasksTab(body, ctx);
  else if (tab === 'live') await liveTab(body, ctx);
  else if (tab === 'shares') await sharesTab(body, ctx);
  else await librariesTab(body, ctx);
}

