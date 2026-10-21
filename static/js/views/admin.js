import { del, get, post, put } from '../api.js';
import { refreshMe, state } from '../app.js';
import { avatar, clear, confirmDialog, field, fmtTime, h, icon, modal, select, toast, toggle } from '../ui.js';

function tabs(active) {
  const t = (id, label) => h('a.tab', { href: `#/admin/${id}`, class: id === active ? 'tab active' : 'tab' }, label);
  return h('div.tabs', t('libraries', 'Bibliothèques'), t('shares', 'Partages réseau'), t('users', 'Utilisateurs'), t('settings', 'Métadonnées et lecture'),
    t('streams', 'Lectures en cours'));
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
    const t = toggle(l.name, user.libraries.includes(l.id));
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
  const interval = select([['0', 'Jamais (manuel)'], ['1', 'Toutes les heures'], ['6', 'Toutes les 6 heures'], ['12', 'Toutes les 12 heures'],
    ['24', 'Une fois par jour']], String(s.scan_interval));
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
        } }, 'Tout réactualiser'))),
    h('section.panel', h('h2', 'Scan automatique'),
      field('Rechercher les nouveaux fichiers', interval),
      h('button.btn.primary', { onclick: () => save({ scan_interval: Number(interval.value) }, 'Enregistré') }, 'Enregistrer')),
    h('section.panel', h('h2', 'Lecture et processeur'),
      h('p.muted.small', `Ce serveur a ${s.cpus} cœur(s) et pas d’accélération matérielle. Une lecture directe ou un remux ne coûtent presque rien ; un transcodage (HEVC, 10 bits, sous-titres image, qualité réduite) occupe 1 à 2 cœurs par lecture en 1080p.`),
      s.ffmpeg ? null : h('div.plex-status.bad', 'ffmpeg est introuvable : installe-le (apt install ffmpeg).'),
      h('div.grid2', field('Lectures simultanées (max.)', maxStreams), field('Transcodages simultanés (max.)', maxTranscodes,
        '0 = interdire le transcodage (seules les lectures directes et remux passent).')),
      h('button.btn.primary', { onclick: () => save({ max_streams: Number(maxStreams.value), max_transcodes: Number(maxTranscodes.value) }, 'Enregistré') }, 'Enregistrer')),
    h('p.muted.small', `Séance ${s.version}`));
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
        s.reasons && s.reasons.length ? ` (${s.reasons.join(', ')})` : '')),
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
  else if (tab === 'shares') await sharesTab(body, ctx);
  else await librariesTab(body, ctx);
}

