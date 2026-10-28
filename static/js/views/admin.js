import { del, get, post, put } from '../api.js';
import { refreshMe, state } from '../app.js';
import { followUpdate } from '../updater.js';
import { avatar, clear, confirmDialog, field, fmtTime, h, icon, modal, select, toast, toggle } from '../ui.js';
import { LANGUAGES, locale, plural, tr } from '../i18n.js';

// Pages de l'administration, rangées par thème dans le menu des Paramètres.
// keys : mots qui la font trouver avec la recherche du menu.
export const ADMIN_GROUPS = [
  { title: tr('Serveur'), items: [
    { id: 'dashboard', label: tr('Vue d’ensemble'), icon: 'dashboard', lead: tr('Version, état du serveur et lectures en cours.'),
      keys: tr('tableau de bord lectures en cours streams mémoire swap charge processeur version arrêter') },
    { id: 'users', label: tr('Utilisateurs'), icon: 'group', lead: tr('Comptes, profils du foyer, contrôle parental et accès aux bibliothèques.'),
      keys: tr('compte profil enfant pin âge restriction administrateur désactiver mot de passe foyer langue language') },
    { id: 'update', label: tr('Mise à jour'), icon: 'upgrade', lead: tr('Version installée et mises à jour de Nexis.'),
      keys: tr('version mettre à jour github nouveautés changements réinstaller') },
    { id: 'federation', label: tr('Serveurs liés'), icon: 'link', lead: tr('Relier vos serveurs Nexis : comptes communs, bibliothèques de chacun visibles et lisibles depuis l’autre.'),
      keys: tr('fédération cluster lier jumeler invitation second serveur réplication tunnel') },
    { id: 'tasks', label: tr('Tâches planifiées'), icon: 'clock', lead: tr('Travaux de fond : scans, métadonnées, intros, sous-titres, nettoyage.'),
      keys: tr('planification scan automatique nettoyage cache intro générique chapitres horaire') },
  ] },
  { title: tr('Médiathèque'), items: [
    { id: 'libraries', label: tr('Bibliothèques'), icon: 'folder', lead: tr('Dossiers de films et de séries, et leur analyse.'),
      keys: tr('dossier films séries animes scanner rescanner ajouter chemin') },
    { id: 'shares', label: tr('Partages réseau'), icon: 'storage', lead: tr('Montage des dossiers partagés d’un NAS (SMB) pour y créer des bibliothèques.'),
      keys: tr('nas smb cifs samba montage monter partage réseau') },
    { id: 'metadata', label: tr('Métadonnées'), icon: 'film', lead: tr('Affiches, résumés et titres d’épisodes (TMDB).'),
      keys: tr('tmdb clé api affiches résumés langue actualiser images') },
    { id: 'detection', label: tr('Ajout des nouveautés'), icon: 'eye', lead: tr('Détection automatique des fichiers ajoutés et notifications de Radarr / Sonarr.'),
      keys: tr('surveillance dossiers webhook radarr sonarr nouveaux fichiers détection') },
    { id: 'requests', label: tr('Demandes'), icon: 'star', lead: tr('Connexion à Seerr pour permettre aux utilisateurs de demander des films et des séries.'),
      keys: tr('seerr overseerr jellyseerr demander radarr sonarr découvrir') },
  ] },
  { title: tr('Lecture'), items: [
    { id: 'transcoding', label: tr('Transcodage'), icon: 'bolt', lead: tr('Lectures simultanées et carte graphique.'),
      keys: tr('processeur cpu gpu carte graphique nvidia nvenc vaapi intel amd limites lectures simultanées matériel') },
    { id: 'subtitles', label: tr('Sous-titres et son'), icon: 'subs', lead: tr('Préparation des sous-titres intégrés et mixage du son 5.1 / 7.1.'),
      keys: tr('sous-titres préparer audio son 5.1 7.1 stéréo mixage downmix volume dialogues') },
    { id: 'downloads', label: tr('Téléchargements'), icon: 'install', lead: tr('Visionnage hors ligne dans l’application.'),
      keys: tr('hors ligne télécharger offline téléphone') },
    { id: 'live', label: tr('TV en direct'), icon: 'tv', lead: tr('Listes de chaînes IPTV (M3U) et guide des programmes.'),
      keys: tr('iptv m3u xmltv chaînes guide epg télévision box') },
  ] },
];
// anciennes adresses (favoris, liens) -> nouvelles pages
export const ADMIN_ALIASES = { settings: 'metadata', streams: 'dashboard' };

function since(ts) {
  if (!ts) return tr('jamais');
  const s = Date.now() / 1000 - ts;
  if (s < 120) return tr('à l’instant');
  if (s < 3600) return tr('il y a {p1} min', { p1: Math.round(s / 60) });
  if (s < 86400) return tr('il y a {p1} h', { p1: Math.round(s / 3600) });
  return tr('il y a {p1} j', { p1: Math.round(s / 86400) });
}

// ------------------------------------------------------------------ users
const AGE_LIMITS = [['', tr('Aucune restriction')], ['7', tr('Jeunes enfants (7 ans max.)')], ['10', tr('Enfant (10 ans max.)')],
  ['12', tr('12 ans max.')], ['16', tr('16 ans max.')]];

async function userForm(user, libraries, defaultLang = 'fr') {
  const isNew = !user;
  user = user || { username: '', display_name: '', is_admin: false, all_libraries: true, libraries: [], disabled: false,
    managed: false, home: true, max_age: null, allow_unrated: true, has_pin: false, kids: false };
  const username = h('input', { value: user.username, disabled: !isNew, autocomplete: 'off' });
  const display = h('input', { value: user.display_name, maxlength: 50 });
  const pass = h('input', { type: 'password', autocomplete: 'new-password',
    placeholder: isNew ? tr('8 caractères minimum') : tr('Laisser vide pour ne pas changer') });
  const managed = toggle(tr('Profil géré : sans mot de passe, accessible depuis « Changer de profil » (adapté aux enfants)'), user.managed);
  const home = toggle(tr('Membre du foyer (affiché dans « Qui regarde ? »)'), user.home);
  const pin = h('input', { type: 'password', inputmode: 'numeric', maxlength: 8, autocomplete: 'off',
    placeholder: user.has_pin ? tr('Code défini (laisser vide pour le garder)') : tr('4 à 8 chiffres (facultatif)') });
  const clearPin = user.has_pin ? toggle(tr('Retirer le code PIN'), false) : null;
  const age = select(AGE_LIMITS, user.max_age == null ? '' : String(user.max_age));
  const unrated = toggle(tr('Autoriser les contenus sans classification'), user.allow_unrated);
  const kids = toggle(tr('Interface enfant : grandes vignettes, menu simplifié'), user.kids);
  const admin = toggle(tr('Administrateur'), user.is_admin);
  const disabled = toggle(tr('Compte désactivé'), user.disabled);
  const all = toggle(tr('Accès à toutes les bibliothèques (y compris les futures)'), user.all_libraries);
  const libChecks = libraries.map((l) => {
    const t = toggle(l.name, user.libraries.includes(l.id));
    t.dataset.id = l.id;
    return t;
  });
  const libBox = h('div.checks', libChecks);
  const passField = field(isNew ? tr('Mot de passe') : tr('Nouveau mot de passe'), pass);
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
  // téléphone perdu : l'administrateur désactive la double authentification du compte
  const twoFactor = !isNew && user.two_factor ? h('div.twofa-admin',
    h('span.small', tr('Double authentification activée.')),
    h('button.btn.small', { type: 'button', onclick: async (e) => {
      if (!await confirmDialog(tr('Réinitialiser la double authentification ?'),
        tr('{display_name} pourra se connecter avec son seul mot de passe, puis la réactiver dans Paramètres → Profil.', { display_name: user.display_name }), tr('Réinitialiser'))) return;
      try {
        await post(`/api/admin/users/${user.id}/2fa/reset`);
        user.two_factor = false;
        e.target.closest('.twofa-admin').replaceChildren(h('span.small', tr('Double authentification désactivée.')));
        toast(tr('Double authentification réinitialisée'), 'ok');
      } catch (ex) { toast(ex.message, 'bad'); }
    } }, tr('Réinitialiser'))) : null;
  const language = select(LANGUAGES.map((l) => [l.id, l.name]), (user && user.language) || defaultLang);
  const body = h('div.form',
    h('div.grid2', field(tr('Identifiant'), username), field(tr('Nom affiché'), display)),
    field(tr('Langue de l’interface'), language, tr('Chaque utilisateur peut la changer dans Paramètres → Profil.')),
    managed, passField, twoFactor,
    h('h4.form-sub', tr('Profil et contrôle parental')),
    home,
    h('div.grid2', field(tr('Code PIN du profil'), pin), field(tr('Restriction d’âge'), age, tr('D’après la classification (TMDB ou .nfo).'))),
    clearPin, unrated, kids,
    h('h4.form-sub', tr('Droits et bibliothèques')),
    admin, isNew ? null : disabled, all, libBox, err);
  return modal(isNew ? tr('Nouvel utilisateur') : tr('Modifier {display_name}', { display_name: user.display_name }), body, [
    { label: tr('Annuler'), value: null },
    { label: isNew ? tr('Créer') : tr('Enregistrer'), primary: true, handler: async () => {
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
        kids: kids.querySelector('input').checked,
        language: language.value,
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
  const [users, libs, settings] = await Promise.all([get('/api/admin/users'), get('/api/admin/libraries'),
    get('/api/admin/settings')]);
  if (!ctx.isCurrent()) return;
  const libraries = libs.libraries;
  const defaultLang = settings.default_language;
  // langue proposée aux nouveaux comptes (et affichée avant la connexion)
  const langSel = select(LANGUAGES.map((l) => [l.id, l.name]), defaultLang, { onchange: async (e) => {
    try { await put('/api/admin/settings', { default_language: e.target.value }); toast(tr('Langue par défaut enregistrée'), 'ok'); reload(); }
    catch (ex) { toast(ex.message, 'bad'); }
  } });
  const langPanel = h('section.panel', h('h2', tr('Langue par défaut')),
    field(tr('Langue des nouveaux comptes'), langSel,
      tr('Utilisée pour les comptes créés ensuite et sur la page de connexion. Chaque utilisateur peut choisir la sienne dans Paramètres → Profil.')));
  const reload = () => usersTab(body, ctx);
  const libName = (id) => (libraries.find((l) => l.id === id) || { name: `#${id}` }).name;
  clear(body,
    h('div.toolbar', h('p.muted', tr('{length} compte(s). Historique, reprise de lecture et préférences sont propres à chaque compte.', { length: users.length })),
      h('button.btn.primary', { onclick: async () => { if (await userForm(null, libraries, defaultLang)) { toast(tr('Utilisateur créé'), 'ok'); reload(); } } }, tr('+ Ajouter'))),
    h('div.user-list', users.map((u) => h('div.user-row', { class: u.disabled ? 'user-row off' : 'user-row' },
      avatar(u, 40),
      h('div.grow', h('strong', u.display_name), h('span.muted', ` @${u.username}`),
        h('div.muted.small',
          u.is_admin ? h('span.tag.accent', tr('Admin')) : null,
          u.disabled ? h('span.tag', tr('Désactivé')) : null,
          u.managed ? h('span.tag', tr('Profil géré')) : null,
          u.max_age != null ? h('span.tag', tr('≤ {max_age} ans', { max_age: u.max_age })) : null,
          u.kids ? h('span.tag', tr('Interface enfant')) : null,
          u.has_pin ? h('span.tag', 'PIN') : null,
          h('span.tag', (LANGUAGES.find((l) => l.id === u.language) || { name: u.language }).name),
          u.two_factor ? h('span.tag.ok', tr('Double authentification')) : null,
          u.all_libraries ? tr('Toutes les bibliothèques') : (u.libraries.map(libName).join(', ') || tr('Aucune bibliothèque')),
          tr(' · vu {p1}', { p1: since(u.last_seen) }))),
      h('button.btn.small', { onclick: async () => { if (await userForm(u, libraries, defaultLang)) { toast(tr('Modifications enregistrées'), 'ok'); reload(); } } }, tr('Modifier')),
      u.id === state.me.user.id ? null : h('button.icon-btn', { title: tr('Supprimer'), onclick: async () => {
        if (!await confirmDialog(tr('Supprimer ce compte ?'), tr('L’historique et les préférences de {display_name} seront supprimés. Cette action est définitive.', { display_name: u.display_name }), tr('Supprimer'))) return;
        try { await del(`/api/admin/users/${u.id}`); toast(tr('Compte supprimé'), 'ok'); reload(); } catch (e) { toast(e.message, 'bad'); }
      } }, icon('trash'))))),
    langPanel);
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
        r.parent ? h('button.folder', { onclick: () => load(r.parent) }, tr('⬑ Dossier parent')) : null,
        ...r.dirs.map((d) => h('button.folder', { onclick: () => load(`${r.path === '/' ? '' : r.path}/${d}`) }, `📁 ${d}`)),
        r.dirs.length ? null : h('p.muted.small', tr('Aucun sous-dossier.')));
    } catch (e) { toast(e.message, 'bad'); }
  };
  await load(current);
  return modal(tr('Choisir un dossier'), h('div', pathEl, list), [
    { label: tr('Annuler'), value: null },
    { label: tr('Choisir ce dossier'), primary: true, handler: () => current },
  ]);
}

async function libraryForm(lib) {
  const isNew = !lib || !lib.id;
  lib = lib || { name: '', kind: 'movie', paths: [] };
  const name = h('input', { value: lib.name, maxlength: 40, placeholder: tr('Films, Séries, Animes…') });
  const kind = select([['movie', tr('Films')], ['show', tr('Séries (et animes)')]], lib.kind);
  const paths = h('div.paths');
  const addPath = (value = '') => {
    const input = h('input', { value, placeholder: '/mnt/films' });
    const rowEl = h('div.path-row', input,
      h('button.btn.small', { type: 'button', onclick: async () => {
        const p = await pickFolder(input.value || '/mnt');
        if (p) input.value = p;
      } }, tr('Parcourir…')),
      h('button.icon-btn', { type: 'button', title: tr('Retirer'), onclick: () => rowEl.remove() }, icon('close')));
    paths.append(rowEl);
  };
  (lib.paths.length ? lib.paths : ['']).forEach(addPath);
  const err = h('p.form-error');
  const body = h('div.form',
    h('div.grid2', field(tr('Nom'), name), field(tr('Type de contenu'), kind)),
    field(tr('Dossiers'), paths, tr('Chemins sur le serveur Nexis (ex. /mnt/films). Chaque film ou série dans son propre sous-dossier.')),
    h('button.btn.small.ghost', { type: 'button', onclick: () => addPath() }, tr('+ Ajouter un dossier')),
    isNew ? null : h('p.muted.small', tr('Changer le type vide la bibliothèque avant de la rescanner.')), err);
  return modal(isNew ? tr('Nouvelle bibliothèque') : tr('Modifier « {name} »', { name: lib.name }), body, [
    { label: tr('Annuler'), value: null },
    { label: isNew ? tr('Créer et scanner') : tr('Enregistrer et rescanner'), primary: true, handler: async () => {
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
    h('div.scan-head', h('strong', scan.running ? tr('Scan en cours : {phase}', { phase: scan.phase }) : tr('Dernier scan terminé {p1}', { p1: since(scan.finished_at) })),
      scan.running && scan.library ? h('span.muted', ` · ${scan.library}`) : null),
    scan.running ? h('div.bar', h('div.bar-fill', { style: { width: `${pct}%` } })) : null,
    scan.running && scan.total ? h('div.muted.small', `${scan.done} / ${scan.total}`) : null,
    !scan.running ? h('div.muted.small', tr('{added} ajout(s), {removed} suppression(s)', { added: scan.added, removed: scan.removed })) : null,
    scan.unrecognized.length ? h('details', h('summary', tr('{length} fichier(s) non reconnu(s) comme épisode', { length: scan.unrecognized.length })),
      h('ul.small', scan.unrecognized.map((f) => h('li', f))),
      h('p.muted.small', tr('Pour qu’ils soient reconnus, les noms doivent contenir S01E02 ou « Nom - 02 ».'))) : null,
    scan.errors.length ? h('details', { open: true }, h('summary', tr('{n} erreur(s)', { n: scan.errors.length })),
      h('ul.small', scan.errors.map((f) => h('li', f)))) : null);
}

async function librariesTab(body, ctx) {
  const data = await get('/api/admin/libraries');
  if (!ctx.isCurrent()) return;
  const reload = () => librariesTab(body, ctx);
  const startScan = async (payload) => {
    try { await post('/api/admin/scan', payload); toast(tr('Scan lancé'), 'ok'); reload(); } catch (e) { toast(e.message, 'bad'); }
  };
  const c = (l, k) => l.counts[k] || 0;
  clear(body,
    h('div.toolbar', h('p.muted', tr('Chaque bibliothèque apparaît comme un onglet. Les animes peuvent avoir leur propre bibliothèque de type « Séries ».')),
      h('div.actions-row',
        data.libraries.length ? h('button.btn', { onclick: () => startScan({}) }, tr('Tout rescanner')) : null,
        h('button.btn.primary', { onclick: async () => { if (await libraryForm(null)) { await refreshMe(); toast(tr('Bibliothèque créée, scan en cours'), 'ok'); reload(); } } }, tr('+ Ajouter')))),
    scanBox(data.scan),
    data.libraries.length ? h('div.user-list', data.libraries.map((l) => h('div.user-row',
      h('div.lib-icon', l.kind === 'movie' ? '🎬' : '📺'),
      h('div.grow', h('strong', l.name),
        h('div.muted.small', l.kind === 'movie' ? plural(c(l, 'movie'), '{n} film', '{n} films') : tr('{p1} séries · {p2} épisodes', { p1: c(l, 'show'), p2: c(l, 'episode') }),
          l.unmatched ? h('span', tr(' · {unmatched} non identifié(s)', { unmatched: l.unmatched })) : null,
          tr(' · scanné {p1}', { p1: since(l.scanned_at) })),
        h('div.small', l.paths.map((p, i) => h('code', { class: l.paths_ok[i] ? '' : 'bad-path', title: l.paths_ok[i] ? '' : tr('Dossier inaccessible') }, p))),
        (l.paths_writable || []).some(Boolean) ? h('div.small.warn-text', tr('Nexis peut écrire dans ce dossier : par précaution, montez-le en lecture seule (voir l’audit de sécurité).')) : null),
      h('button.btn.small', { onclick: () => startScan({ library: l.id }) }, tr('Scanner')),
      h('button.btn.small', { onclick: async () => { if (await libraryForm(l)) { await refreshMe(); reload(); } } }, tr('Modifier')),
      h('button.icon-btn', { title: tr('Supprimer'), onclick: async () => {
        if (!await confirmDialog(tr('Supprimer cette bibliothèque ?'), tr('« {name} » sera retirée de Nexis. Les fichiers ne sont pas modifiés.', { name: l.name }), tr('Supprimer'))) return;
        try { await del(`/api/admin/libraries/${l.id}`); await refreshMe(); reload(); } catch (e) { toast(e.message, 'bad'); }
      } }, icon('trash')))))
      : h('div.empty', h('p', tr('Aucune bibliothèque.')), h('p.muted.small', tr('Exemple : « Films » → /mnt/films, « Séries » → /mnt/series, « Animes » → /mnt/animes.'))));
  if (data.scan.running) {
    const t = setTimeout(async () => { if (ctx.isCurrent()) { await refreshMe().catch(() => {}); reload(); } }, 1500);
    ctx.setCleanup(() => clearTimeout(t));
  }
}

// ----------------------------------------------------------- partages SMB
const SMB_VERSIONS = [['auto', tr('Automatique')], ['3.1.1', 'SMB 3.1.1'], ['3.0', 'SMB 3.0'], ['2.1', 'SMB 2.1'],
  ['2.0', 'SMB 2.0'], ['1.0', tr('SMB 1 (ancien NAS)')]];

const slug = (t) => t.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
  .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32) || 'partage';

async function shareForm(existing) {
  const isNew = !existing;
  const e = existing || { host: '', share: '', subdir: '', username: '', version: 'auto', name: '' };
  const host = h('input', { value: e.host, placeholder: '192.168.1.5' });
  const user = h('input', { value: e.username, autocomplete: 'off' });
  const pass = h('input', { type: 'password', autocomplete: 'new-password',
    placeholder: isNew ? '' : tr('Laisser vide pour garder le mot de passe actuel') });
  const domain = h('input', { placeholder: tr('WORKGROUP (facultatif)') });
  const version = select(SMB_VERSIONS, e.version || 'auto');
  const share = h('input', { value: e.share, placeholder: tr('Films') });
  const subdir = h('input', { value: e.subdir || '', placeholder: tr('facultatif, ex. Films/HD') });
  const name = h('input', { value: e.name, disabled: !isNew, placeholder: tr('films') });
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
    found.replaceChildren(h('p.muted.small', tr('Connexion au NAS…')));
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
        : [h('p.muted.small', tr('Aucun partage visible avec ce compte.'))]));
    } catch (ex) { found.replaceChildren(); err.textContent = ex.message; } finally { discover.disabled = false; }
  } }, tr('Voir les partages du NAS'));
  const body = h('div.form',
    h('div.grid2', field(tr('Adresse du NAS'), host), field(tr('Identifiant'), user)),
    field(tr('Mot de passe'), pass),
    h('div.actions-row', discover), found,
    h('div.grid2', field(tr('Partage'), share), field(tr('Nom local'), name, tr('Monté dans /mnt/seance/<nom>'))),
    h('details.advanced', h('summary', tr('Options avancées')),
      h('div.grid2', field(tr('Sous-dossier du partage'), subdir), field(tr('Version SMB'), version)), field(tr('Domaine'), domain)),
    h('p.muted.small', tr('Le partage est monté en lecture seule : Nexis ne peut ni modifier ni supprimer les fichiers. Le mot de passe est stocké par le système (lisible par root uniquement), jamais dans la base de Nexis.')),
    err);
  return modal(isNew ? tr('Ajouter un partage réseau (SMB)') : tr('Modifier « {name} »', { name: e.name }), body, [
    { label: tr('Annuler'), value: null },
    { label: tr('Monter le partage'), primary: true, handler: async () => {
      err.textContent = tr('Montage en cours…');
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
    if (ok) { await refreshMe(); toast(tr('Bibliothèque créée, scan en cours'), 'ok'); }
    reload();
  };
  if (!data.available) {
    clear(body, h('section.panel', h('h2', tr('Partages réseau')), h('div.status-box.bad', data.error),
      h('p.muted.small', tr('Le service seance-mountd est absent ou arrêté. Relancer l’installation le réinstalle ; son état s’affiche avec systemctl status seance-mountd.'))));
    return;
  }
  const env = data.env;
  const warning = env.unprivileged ? h('section.panel.warn',
    h('h2', tr('Ce conteneur ne peut pas monter de partages')),
    h('p', tr('Linux interdit le montage SMB dans un conteneur LXC non privilégié. Les partages du NAS peuvent être listés, mais leur montage échouera. Deux solutions :')),
    h('ol',
      h('li', h('strong', tr('Conteneur privilégié')), tr(' (gestion des partages depuis Nexis) : sauvegarder le conteneur, le restaurer en mode privilégié (Proxmox : Sauvegarde → Restaurer, décocher « Non privilégié »), puis activer l’option '), h('code', 'pct set <ID> -features mount=cifs,nesting=1'), '.'),
      h('li', h('strong', tr('Garder le conteneur non privilégié')), tr(' : monter les partages sur l’hôte Proxmox et les transmettre au conteneur (procédure dans le README). Ils apparaissent alors dans l’explorateur de dossiers des bibliothèques.')))) : null;
  const tools = env.tools && (!env.tools['mount.cifs'] || !env.tools.smbclient)
    ? h('div.status-box.bad', tr('cifs-utils ou smbclient est manquant. Relancer l’installation de Nexis les installe.')) : null;
  clear(body,
    h('div.toolbar', h('p.muted', tr('Dossiers partagés d’un NAS (SMB/CIFS), montés depuis Nexis pour y créer des bibliothèques.')),
      h('button.btn.primary', { onclick: async () => {
        const path = await shareForm(null);
        if (path) {
          toast(tr('Partage monté'), 'ok');
          if (await confirmDialog(tr('Partage monté'), tr('Le partage est disponible dans {path}. Créer une bibliothèque avec ce dossier ?', { path }), tr('Créer une bibliothèque'), false)) {
            await offerLibrary(path, path.split('/').pop());
            return;
          }
          reload();
        }
      } }, tr('+ Ajouter un partage'))),
    warning, tools,
    data.shares.length ? h('div.user-list', data.shares.map((sh) => h('div.user-row',
      h('div.lib-icon', '🗄️'),
      h('div.grow', h('strong', sh.name), h('span.muted', ` //${sh.host}/${sh.share}${sh.subdir ? `/${sh.subdir}` : ''}`),
        h('div.small', h('code', sh.path),
          sh.mounted ? h('span.tag.ok', tr('Monté')) : h('span.tag.bad', tr('Non monté')),
          sh.username ? h('span.muted', tr(' · compte {username}', { username: sh.username })) : null,
          sh.libraries.length ? h('span.muted', tr(' · utilisé par {p1}', { p1: sh.libraries.join(', ') })) : null),
        sh.error ? h('div.form-error.small', sh.error) : null),
      sh.mounted && !sh.libraries.length ? h('button.btn.small', { onclick: () => offerLibrary(sh.path, sh.share) }, tr('Créer une bibliothèque')) : null,
      sh.mounted ? null : h('button.btn.small', { onclick: async () => {
        try { await post(`/api/admin/shares/${sh.name}/remount`); toast(tr('Partage remonté'), 'ok'); } catch (e) { toast(e.message, 'bad'); }
        reload();
      } }, tr('Remonter')),
      h('button.btn.small', { onclick: async () => { if (await shareForm(sh)) { toast(tr('Partage mis à jour'), 'ok'); reload(); } } }, tr('Modifier')),
      h('button.icon-btn', { title: tr('Démonter et retirer'), onclick: async () => {
        const used = sh.libraries.length ? tr(' Les bibliothèques {p1} ne trouveront plus leurs fichiers (leur contenu est conservé tant qu’elles ne sont pas rescannées avec le partage monté).', { p1: sh.libraries.join(', ') }) : '';
        if (!await confirmDialog(tr('Retirer ce partage ?'), tr('Le partage sera démonté et retiré de Nexis. Les fichiers du NAS ne sont pas modifiés.{used}', { used }), tr('Retirer'))) return;
        try { await del(`/api/admin/shares/${sh.name}`); toast(tr('Partage retiré'), 'ok'); reload(); } catch (e) { toast(e.message, 'bad'); }
      } }, icon('trash')))))
      : h('div.empty', tr('Aucun partage réseau géré par Nexis.')));
}

// --------------------------------------------------------------- réglages du serveur
// Chaque page lit les réglages, puis se redessine après un enregistrement.
async function withSettings(body, ctx, build) {
  const s = await get('/api/admin/settings');
  if (!ctx.isCurrent()) return;
  const save = async (payload, msg) => {
    try { await put('/api/admin/settings', payload); toast(msg, 'ok'); withSettings(body, ctx, build); } catch (e) { toast(e.message, 'bad'); }
  };
  clear(body, ...[build(s, save, () => withSettings(body, ctx, build))].flat());
}

const metadataPage = (body, ctx) => withSettings(body, ctx, (s, save) => {
  const key = h('input', { type: 'password', autocomplete: 'off',
    placeholder: s.tmdb_set ? tr('Clé enregistrée (laisser vide pour la garder)') : tr('Clé API (v3) ou jeton de lecture (v4)') });
  const lang = select([['fr-FR', tr('Français (France)')], ['fr-CA', tr('Français (Canada)')], ['en-US', tr('Anglais')], ['de-DE', tr('Allemand')],
    ['es-ES', tr('Espagnol')], ['it-IT', tr('Italien')]], s.language);
  return [
    h('section.panel', h('h2', tr('Clé TMDB et langue')),
      h('p.muted.small', tr('Affiches, résumés, distribution et titres d’épisodes proviennent de The Movie Database. Une clé gratuite s’obtient sur themoviedb.org (Paramètres → API). Sans clé, Nexis utilise les noms de fichiers, les fichiers .nfo et les images présentes dans les dossiers.')),
      h('div.status-box', { class: s.tmdb_set ? 'status-box ok' : 'status-box' }, s.tmdb_set ? tr('Clé TMDB configurée.') : tr('Aucune clé TMDB.')),
      h('div.grid2', field(tr('Clé TMDB'), key), field(tr('Langue des métadonnées'), lang)),
      h('div.actions-row',
        h('button.btn.primary', { onclick: () => save({ ...(key.value.trim() ? { tmdb_key: key.value.trim() } : {}), language: lang.value }, tr('Métadonnées enregistrées')) }, tr('Enregistrer')),
        s.tmdb_set ? h('button.btn', { onclick: () => save({ tmdb_key: '' }, tr('Clé supprimée')) }, tr('Supprimer la clé')) : null)),
    h('section.panel', h('h2', tr('Actualisation')),
      h('p.muted.small', tr('Les fiches incomplètes sont complétées automatiquement (tâche « Actualiser les métadonnées »). Un rechargement complet est utile après un changement de langue.')),
      h('div.actions-row',
        h('button.btn', { onclick: async () => {
          try { await post('/api/admin/scan', { refresh_metadata: true }); toast(tr('Actualisation de toutes les métadonnées lancée'), 'ok'); }
          catch (e) { toast(e.message, 'bad'); }
        } }, tr('Tout réactualiser maintenant')),
        h('a.btn.ghost', { href: '#/admin/tasks' }, tr('Planifier l’actualisation…')))),
  ];
});

const detectionPage = (body, ctx) => withSettings(body, ctx, (s, save, reload) => {
  const watch = select([['0', tr('Désactivée')], ['30', tr('Toutes les 30 secondes')], ['60', tr('Toutes les minutes')], ['300', tr('Toutes les 5 minutes')],
    ['900', tr('Toutes les 15 minutes')]], String(s.watch_interval));
  const hookInput = h('input', { readonly: true, value: `${location.origin}${s.webhook_path}`, onfocus: (e) => e.target.select() });
  return [
    h('section.panel', h('h2', tr('Surveillance des dossiers')),
      h('p.muted.small', tr('Sur un partage réseau, les ajouts faits depuis une autre machine ne sont pas signalés au système. Nexis vérifie donc régulièrement les dossiers et analyse la bibliothèque concernée une fois la copie terminée.')),
      h('div.grid2', field(tr('Surveiller les dossiers'), watch, tr('Le scan complet quotidien se règle dans « Tâches planifiées ».'))),
      h('button.btn.primary', { onclick: () => save({ watch_interval: Number(watch.value) }, tr('Enregistré')) }, tr('Enregistrer')),
      s.watch_events.length ? h('details', h('summary.small', tr('Derniers déclenchements')),
        h('ul.small', s.watch_events.map((e) => h('li', `${new Date(e.t * 1000).toLocaleString(locale())} — ${e.text}`)))) : null),
    h('section.panel', h('h2', tr('Webhook Radarr / Sonarr')),
      h('p.muted.small', tr('Radarr et Sonarr signalent chaque import à Nexis : les nouveaux titres apparaissent immédiatement.')),
      h('div.copy-row', hookInput,
        h('button.btn.small', { onclick: async () => {
          try { await navigator.clipboard.writeText(hookInput.value); toast(tr('Adresse copiée'), 'ok'); }
          catch { hookInput.select(); }
        } }, tr('Copier')),
        h('button.btn.small.ghost', { onclick: async () => {
          if (!await confirmDialog(tr('Nouvelle adresse ?'), tr('L’ancienne adresse cessera de fonctionner : il faudra la remplacer dans Radarr et Sonarr.'), tr('Régénérer'))) return;
          await post('/api/admin/webhook/regenerate'); reload();
        } }, tr('Régénérer'))),
      h('p.muted.small', tr('Dans Radarr et Sonarr : Settings → Connect → + → Webhook. Renseigner cette adresse dans « URL » (méthode POST) et cocher « On Import », « On Upgrade », « On Rename » et « On Delete ». Si Radarr tourne sur une autre machine, utiliser l’adresse du serveur Nexis telle qu’elle est vue depuis Radarr.'))),
  ];
});

const subtitlesPage = (body, ctx) => withSettings(body, ctx, (s, save) => {
  const prefetch = toggle(tr('Préparer les sous-titres des nouveaux fichiers à l’avance'), s.subs_prefetch);
  prefetch.querySelector('input').addEventListener('change', (e) => save({ subs_prefetch: e.target.checked }, tr('Enregistré')));
  const algo = select([['none', tr('ffmpeg (par défaut)')], ['dave750', 'Dave750'], ['nightmode', tr('Dialogues (nuit)')],
    ['rfc7845', 'RFC 7845'], ['ac4', 'AC-4']], s.downmix_algo);
  const boost = select([['1', tr('× 1 (aucun)')], ['1.5', tr('× 1,5')], ['2', tr('× 2 (par défaut)')], ['2.5', tr('× 2,5')], ['3', '× 3']], String(s.downmix_boost));
  return [
    h('section.panel', h('h2', tr('Sous-titres intégrés')),
      h('p.muted.small', tr('Les sous-titres intégrés aux fichiers (MKV) ne peuvent être extraits qu’en lisant le fichier en entier. Nexis les prépare à l’avance, en dehors des lectures, pour qu’ils soient disponibles dès le début de la vidéo.')),
      prefetch,
      h('p.muted.small', s.subs_queue ? tr('{subs_queue} fichier(s) en attente de préparation.', { subs_queue: s.subs_queue }) : tr('Aucun fichier en attente.')),
      h('button.btn', { onclick: () => save({ subs_prefetch: true }, tr('Préparation lancée pour toute la médiathèque')) }, tr('Tout préparer maintenant'))),
    h('section.panel', h('h2', tr('Son 5.1 / 7.1 sur des enceintes stéréo')),
      h('p.muted.small', tr('« ffmpeg » : équilibre neutre. « Dave750 » : dialogues légèrement renforcés. « Dialogues (nuit) » : effets et musique fortement atténués. « RFC 7845 » et « AC-4 » : mixages normalisés. Le gain s’applique après le mixage ; un limiteur empêche toute saturation.')),
      h('div.grid2', field(tr('Mixage'), algo), field(tr('Gain après mixage'), boost)),
      h('button.btn.primary', { onclick: () => save({ downmix_algo: algo.value, downmix_boost: Number(boost.value) }, tr('Mixage enregistré (prochaines lectures)')) }, tr('Enregistrer'))),
  ];
});

const transcodingPage = (body, ctx) => withSettings(body, ctx, (s, save) => {
  const maxStreams = h('input', { type: 'number', min: 1, max: 50, value: s.max_streams });
  const maxTranscodes = h('input', { type: 'number', min: 0, max: 16, value: s.max_transcodes });
  // transcodage matériel : moteurs proposés selon ce que le conteneur voit
  const dev = s.hw_devices || { nvidia: false, dri: [] };
  const engines = [['none', tr('Aucune (processeur)')],
    ['nvenc', tr('NVIDIA NVENC{p1}', { p1: dev.nvidia ? '' : tr(' (carte non détectée)') })],
    ['vaapi', tr('Intel / AMD (VAAPI){p1}', { p1: dev.dri.length ? '' : tr(' (aucun /dev/dri détecté)') })]];
  const hwEngine = select(engines, s.hw_engine);
  const devices = [...new Set([...dev.dri, s.hw_device])];
  const hwDevice = select(devices.map((d) => [d, d]), s.hw_device);
  const hwDeviceField = field(tr('Périphérique VAAPI'), hwDevice);
  const paintHw = () => { hwDeviceField.hidden = hwEngine.value !== 'vaapi'; };
  hwEngine.addEventListener('change', paintHw);
  paintHw();
  const hwSave = h('button.btn.primary', { onclick: async () => {
    hwSave.disabled = true;
    hwSave.textContent = hwEngine.value === 'none' ? tr('Enregistrement…') : tr('Test de la carte…');
    await save({ hw_engine: hwEngine.value, hw_device: hwDevice.value },
      hwEngine.value === 'none' ? tr('Transcodage par le processeur') : tr('Test réussi : transcodage matériel activé (prochaines lectures)'));
    hwSave.disabled = false;
    hwSave.textContent = tr('Tester et enregistrer');
  } }, tr('Tester et enregistrer'));
  return [
    h('section.panel', h('h2', tr('Lectures simultanées')),
      h('p.muted.small', tr('Processeur : {cpus} cœur(s). Transcodage : {p2}. La lecture directe et le remux sont peu coûteux ; sans carte graphique, un transcodage 1080p (HEVC, 10 bits, sous-titres image, qualité réduite) occupe 1 à 2 cœurs.', { cpus: s.cpus, p2: s.hw_engine === 'none' ? tr('processeur') : tr('carte graphique') })),
      s.ffmpeg ? null : h('div.status-box.bad', tr('ffmpeg est introuvable sur le serveur (apt install ffmpeg).')),
      h('div.grid2', field(tr('Lectures simultanées (max.)'), maxStreams), field(tr('Transcodages simultanés (max.)'), maxTranscodes,
        tr('0 : transcodage désactivé (lecture directe et remux uniquement).'))),
      h('button.btn.primary', { onclick: () => save({ max_streams: Number(maxStreams.value), max_transcodes: Number(maxTranscodes.value) }, tr('Enregistré')) }, tr('Enregistrer'))),
    h('section.panel', h('h2', tr('Carte graphique')),
      h('p.muted.small', tr('La carte graphique décode et encode la vidéo à la place du processeur, ce qui permet beaucoup plus de transcodages simultanés. Un essai est effectué avant l’enregistrement ; si la carte refuse un fichier, le processeur prend le relais. L’accès d’un conteneur LXC à la carte est décrit dans le README.')),
      h('div.grid2', field(tr('Accélération matérielle'), hwEngine), hwDeviceField),
      hwSave),
  ];
});

const downloadsPage = (body, ctx) => withSettings(body, ctx, (s, save) => {
  const downloadsToggle = toggle(tr('Autoriser les téléchargements'), s.downloads);
  downloadsToggle.querySelector('input').addEventListener('change', (e) => save({ downloads: e.target.checked }, tr('Enregistré')));
  return h('section.panel', h('h2', tr('Visionnage hors ligne')),
    h('p.muted.small', tr('Les utilisateurs peuvent télécharger films et épisodes dans l’application pour les regarder sans connexion. Le serveur prépare une copie à la qualité choisie (conversions une à la fois, en priorité basse), puis la supprime une fois récupérée.')),
    downloadsToggle);
});

// --------------------------------------------------------------- mise à jour
// La vérification interroge GitHub (résultat gardé 30 min côté serveur) ; le bouton force une nouvelle vérification.
function updatePage(body) {
  const version = state.status.version;
  const box = h('div');
  const checkBtn = h('button.btn', { onclick: () => load(true) }, tr('Rechercher une mise à jour'));
  const checked = h('span.muted.small');
  clear(body, h('section.panel#update', h('h2', `Nexis ${version}`), h('p.muted.small', tr('Version installée sur ce serveur.')), box,
    h('div.actions-row.update-check', checkBtn, checked)));
  const load = async (refresh = false) => {
    checkBtn.disabled = true;
    checkBtn.textContent = tr('Recherche en cours…');
    if (!box.childElementCount) box.replaceChildren(h('p.muted.small', tr('Recherche d’une nouvelle version…')));
    try {
      const u = await get(`/api/admin/update${refresh ? '?refresh=1' : ''}`);
      paint(u);
      checked.textContent = tr('Dernière vérification : {p1}', { p1: new Date().toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' }) });
      if (refresh) {
        toast(u.available ? tr('Nexis {latest} est disponible', { latest: u.latest }) : (u.check_error ? u.check_error : tr('Aucune mise à jour disponible')),
          u.check_error ? 'bad' : 'ok');
        await refreshMe().catch(() => {}); // indicateur de la barre du haut
      }
    } catch (e) {
      box.replaceChildren(h('p.form-error.small', e.message));
    } finally {
      checkBtn.disabled = false;
      checkBtn.textContent = tr('Rechercher une mise à jour');
    }
  };
  const paint = (u) => {
    const parts = [];
    if (u.channels && u.can_update) {
      const channel = select(Object.entries(u.channels).map(([k, label]) => [k, `${label} (${k})`]), u.branch || 'main');
      channel.addEventListener('change', async () => {
        const to = channel.value;
        const msg = to === 'main'
          ? tr('Le serveur suivra les versions stables. La version stable actuelle pourra être plus ancienne que celle installée : la réinstaller ramène le serveur sur le canal stable.')
          : tr('Le serveur suivra les versions de test, publiées avant validation. Elles peuvent contenir des défauts.');
        if (!await confirmDialog(tr('Changer de canal ?'), msg, tr('Changer de canal'), false)) { channel.value = u.branch || 'main'; return; }
        try { await put('/api/admin/update/channel', { branch: to }); toast(tr('Canal enregistré'), 'ok'); load(true); }
        catch (e) { toast(e.message, 'bad'); channel.value = u.branch || 'main'; }
      });
      parts.push(field(tr('Canal de mise à jour'), channel, tr('Stable : versions validées. Test : nouveautés en cours de validation.')));
    }
    if (u.check_error) parts.push(h('div.status-box.bad', tr('Vérification impossible : {check_error}', { check_error: u.check_error })));
    else if (u.available) parts.push(h('div.status-box.ok', tr('Nouvelle version disponible : Nexis {latest}', { latest: u.latest })));
    else if (u.latest && u.latest !== version) parts.push(h('div.status-box', tr('Version publiée sur ce canal : Nexis {latest} (source : {source}).', { latest: u.latest, source: u.source })));
    else parts.push(h('div.status-box.ok', tr('Nexis est à jour (source : {source}).', { source: u.source })));
    if (u.available && u.changes && u.changes.length) {
      parts.push(h('details', h('summary.small', tr('Derniers changements')),
        h('ul.small', u.changes.map((c) => h('li', c.title)))));
    }
    if (u.running) {
      parts.push(h('p.small', tr('Mise à jour en cours : {p1} ({p2} %).', { p1: u.step || tr('installation'), p2: u.percent || 0 })),
        h('button.btn.primary', { onclick: () => followUpdate(version) }, tr('Afficher la progression')));
    }
    if (!u.can_update) {
      parts.push(h('p.form-error.small', u.update_error || tr('Mise à jour automatique indisponible sur cette installation.')),
        h('p.muted.small', tr('Mise à jour manuelle : relancer la commande d’installation sur le serveur (voir le README).')));
    } else if (!u.running) {
      parts.push(h('button.btn', { class: u.available ? 'btn primary' : 'btn', onclick: () => runUpdate(version) },
        u.latest && u.latest !== version ? tr('Installer Nexis {latest}', { latest: u.latest }) : tr('Réinstaller la version actuelle')));
    }
    box.replaceChildren(...parts);
  };
  load();
}

async function runUpdate(version) {
  if (!await confirmDialog(tr('Mettre à jour Nexis ?'), tr('Le serveur télécharge la nouvelle version puis redémarre (une à deux minutes). Les lectures en cours seront interrompues.'), tr('Mettre à jour'), false)) return;
  try { await post('/api/admin/update'); } catch (e) { toast(e.message, 'bad'); return; }
  followUpdate(version);
}

// --------------------------------------------------------------- demandes
async function requestsTab(body, ctx) {
  const cfg = await get('/api/admin/requests');
  if (!ctx.isCurrent()) return;
  const reload = () => requestsTab(body, ctx);
  const url = h('input', { value: cfg.seerr_url, placeholder: 'http://192.168.1.20:5055', autocomplete: 'off' });
  const key = h('input', { type: 'password', autocomplete: 'off',
    placeholder: cfg.seerr_key_set ? tr('Clé enregistrée (laisser vide pour la garder)') : tr('Paramètres → Général → Clé d’API') });
  const auto = toggle(tr('Demander automatiquement les titres ajoutés à « À regarder » qui ne sont pas sur le serveur'), cfg.auto_request);
  auto.querySelector('input').addEventListener('change', async (e) => {
    try { await put('/api/admin/requests', { auto_request: e.target.checked }); toast(tr('Enregistré'), 'ok'); } catch (err) { toast(err.message, 'bad'); }
  });
  const status = h('div.status-box', { class: cfg.enabled ? 'status-box ok' : 'status-box' },
    cfg.enabled ? tr('Seerr est configuré : les utilisateurs peuvent demander des films et des séries.') : tr('Seerr n’est pas encore configuré.'));
  clear(body,
    h('section.panel', h('h2', tr('Seerr')),
      h('p.muted.small', tr('Seerr reçoit les demandes faites depuis l’onglet Découvrir et les transmet à Radarr / Sonarr. Une demande est faite au nom du compte Seerr portant le même nom que le compte Nexis, sinon au nom du propriétaire de la clé d’API.')),
      status,
      h('div.grid2', field(tr('Adresse de Seerr'), url), field(tr('Clé d’API de Seerr'), key)),
      h('div.actions-row',
        h('button.btn.primary', { onclick: async () => {
          try {
            await put('/api/admin/requests', { seerr_url: url.value.trim(), ...(key.value.trim() ? { seerr_key: key.value.trim() } : {}) });
            const t = await post('/api/admin/requests/test');
            toast(tr('Connecté à {title} (serveur : {media_server})', { title: t.title, media_server: t.media_server }), 'ok');
            await refreshMe();
            reload();
          } catch (e) { toast(e.message, 'bad'); reload(); }
        } }, tr('Enregistrer et tester')),
        cfg.enabled ? h('button.btn', { onclick: async () => {
          try { const t = await post('/api/admin/requests/test'); toast(tr('Connecté à {title} (serveur : {media_server})', { title: t.title, media_server: t.media_server }), 'ok'); }
          catch (e) { toast(e.message, 'bad'); }
        } }, tr('Tester')) : null,
        cfg.enabled ? h('button.btn.ghost', { onclick: async () => {
          await put('/api/admin/requests', { seerr_url: '', seerr_key: '' }); await refreshMe(); reload();
        } }, tr('Déconnecter')) : null),
      auto,
      h('p.muted.small', tr('Catalogues affichés pour le pays : {region} (celui des métadonnées).', { region: cfg.region }))));
}

// ---------------------------------------------------------- tâches planifiées
// lundi … dimanche, dans la langue de l'interface (le 1er janvier 2024 est un lundi)
const WEEKDAYS = [...Array(7)].map((_, i) => new Date(2024, 0, 1 + i).toLocaleDateString(locale(), { weekday: 'long' }));
const TASK_ICONS = { scan: 'search', metadata: 'film', chapters: 'chapters', intros: 'skip', subtitles: 'subs', livetv: 'tv', cleanup: 'trash' };
// catégories envoyées par le serveur (en français : ce sont aussi des clés de traduction)
const CATEGORIES = ['Bibliothèques', 'Lecture', 'TV en direct', 'Maintenance'];

function scheduleText(s) {
  if (s.mode === 'interval') return s.hours % 24 === 0 && s.hours > 24 ? tr('Tous les {p1} jours', { p1: s.hours / 24 }) : (s.hours === 1 ? tr('Toutes les heures') : tr('Toutes les {hours} h', { hours: s.hours }));
  if (s.mode === 'daily') return tr('Chaque jour à {time}', { time: s.time });
  if (s.mode === 'weekly') return tr('Chaque {p1} à {time}', { p1: WEEKDAYS[s.weekday ?? 6], time: s.time });
  return tr('Désactivée');
}

function when(ts) {
  const d = new Date(ts * 1000);
  const today = new Date();
  const tomorrow = new Date(Date.now() + 86400000);
  const time = d.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' });
  if (d.toDateString() === today.toDateString()) return tr('aujourd’hui à {time}', { time });
  if (d.toDateString() === tomorrow.toDateString()) return tr('demain à {time}', { time });
  return tr('{day} à {time}', { day: d.toLocaleDateString(locale(), { weekday: 'long', day: 'numeric', month: 'short' }), time });
}

const duration = (sec) => (sec < 60 ? `${sec} s` : sec < 3600 ? `${Math.round(sec / 60)} min` : `${Math.floor(sec / 3600)} h ${String(Math.round((sec % 3600) / 60)).padStart(2, '0')}`);

async function taskSettings(task, libraries) {
  const sc = task.schedule;
  const mode = select([['off', tr('Désactivée (seulement à la main)')], ['interval', tr('À intervalle régulier')], ['daily', tr('Chaque jour')],
    ['weekly', tr('Chaque semaine')]], sc.mode);
  const hours = select([...[1, 2, 3, 6, 12, 24].map((n) => [n, plural(n, '{n} heure', '{n} heures')]),
    ...[2, 3, 7].map((n) => [n * 24, plural(n, '{n} jour', '{n} jours')])], String(sc.hours || 24));
  const time = h('input', { type: 'time', value: sc.time || '03:00' });
  const weekday = select(WEEKDAYS.map((d, i) => [i, d.charAt(0).toUpperCase() + d.slice(1)]), String(sc.weekday ?? 6));
  const fHours = field(tr('Toutes les'), hours);
  const fDay = field(tr('Jour'), weekday);
  const fTime = field(tr('Heure'), time);
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
    h('h4.form-sub', tr('Planification')),
    h('div.sched-row', field(tr('Quand'), mode), fHours, fDay, fTime),
    optionEls.some(Boolean) ? h('h4.form-sub', tr('Options')) : null,
    optionEls, err), [
    { label: tr('Annuler'), value: null },
    { label: tr('Enregistrer'), primary: true, handler: async () => {
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
    const status = last ? { ok: ['ok', tr('Terminée')], error: ['bad', tr('Échec')], cancelled: ['', tr('Arrêtée')] }[last.status] || ['', last.status] : null;
    return h('div.task-row',
      h('div.task-icon', icon(TASK_ICONS[t.id] || 'clock')),
      h('div.grow',
        h('strong', t.name),
        h('p.muted.small.task-desc', t.description),
        h('div.task-meta',
          h('span', icon('clock'), ' ', scheduleText(t.schedule)),
          t.next && !t.running ? h('span', tr('Prochaine : {p1}', { p1: when(t.next) })) : null,
          last ? h('span', h('span', { class: status[0] }, status[1]), ` ${since(last.start)}`,
            last.end ? ` · ${duration(Math.max(0, last.end - last.start))}` : '', last.summary ? ` · ${last.summary}` : '') : h('span', tr('Jamais lancée')),
          t.queued ? h('span.tag', tr('En file')) : null),
        t.note ? h('p.small', { class: t.available ? 'small muted' : 'small form-error' }, t.note) : null,
        t.running ? h('div', h('div.bar', h('div.bar-fill', { style: { width: `${Math.round(t.running.progress * 100)}%` } })),
          h('div.task-running', t.running.paused ? '⏸ ' : '', t.running.message || tr('En cours…'),
            h('span.muted', ` · ${Math.round(t.running.progress * 100)} % · ${t.running.reason}`))) : null),
      h('div.task-actions',
        t.running || t.queued
          ? h('button.btn.small', { onclick: () => act(`/api/admin/tasks/${t.id}/cancel`, tr('Tâche arrêtée')) }, tr('Arrêter'))
          : h('button.btn.small.primary', { disabled: !t.available, onclick: () => act(`/api/admin/tasks/${t.id}/run`, tr('Tâche lancée')) }, icon('play'), tr('Lancer')),
        h('button.btn.small', { onclick: async () => { if (await taskSettings(t, data.libraries)) { toast(tr('Réglages enregistrés'), 'ok'); reload(); } } }, tr('Réglages'))));
  };
  clear(body,
    h('p.muted', tr('Travaux de fond du serveur. Une seule tâche s’exécute à la fois ; les tâches lourdes se mettent en pause pendant les lectures.')),
    CATEGORIES.map((cat) => {
      const list = data.tasks.filter((t) => t.category === cat);
      return list.length ? h('section.task-group', h('h3', tr(cat)), list.map(row)) : null;
    }));
  const busy = data.tasks.some((t) => t.running || t.queued);
  const timer = setTimeout(() => { if (ctx.isCurrent()) reload(); }, busy ? 2500 : 15000);
  ctx.setCleanup(() => clearTimeout(timer));
}

// ------------------------------------------------------------ TV en direct
async function liveSourceForm(src) {
  const isNew = !src;
  src = src || { name: '', m3u_url: '', epg_url: '', user_agent: '', enabled: true };
  const name = h('input', { value: src.name, maxlength: 60, placeholder: tr('Fournisseur IPTV, box TV…') });
  const m3u = h('input', { value: src.m3u_url, placeholder: tr('http://…/playlist.m3u ou /srv/iptv/chaines.m3u') });
  const epg = h('input', { value: src.epg_url, placeholder: tr('facultatif : http://…/guide.xml(.gz)') });
  const ua = h('input', { value: src.user_agent, placeholder: tr('facultatif : VLC/3.0.20 LibVLC/3.0.20…') });
  const enabled = toggle(tr('Source active'), src.enabled);
  const err = h('p.form-error');
  return modal(isNew ? tr('Ajouter une source de chaînes') : tr('Modifier « {name} »', { name: src.name }), h('div.form',
    field(tr('Nom'), name),
    field(tr('Liste des chaînes (M3U / M3U8)'), m3u, tr('Adresse fournie par le fournisseur IPTV ou la box, ou chemin d’un fichier .m3u sur le serveur.')),
    field(tr('Guide des programmes (XMLTV)'), epg, tr('Facultatif si la liste indique déjà son guide (« url-tvg » dans l’en-tête). Fichiers .gz acceptés.')),
    h('details.advanced', h('summary', tr('Options avancées')),
      field('User-Agent', ua, tr('Certains fournisseurs n’acceptent qu’un lecteur précis (VLC, Kodi…).'))),
    isNew ? null : enabled, err), [
    { label: tr('Annuler'), value: null },
    { label: isNew ? tr('Ajouter et charger') : tr('Enregistrer'), primary: true, handler: async () => {
      err.textContent = isNew ? tr('Chargement de la liste…') : '';
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
  const checks = groups.map((g) => { const t = toggle(`${g.name || tr('Sans groupe')} (${g.count})`, !g.hidden); t.dataset.name = g.name; return t; });
  const all = (on) => checks.forEach((t) => { t.querySelector('input').checked = on; });
  return modal(tr('Groupes de « {name} »', { name: src.name }), h('div',
    h('p.muted.small', tr('Les groupes décochés sont masqués pour tout le monde (pratique pour une liste de plusieurs milliers de chaînes).')),
    h('div.actions-row', h('button.btn.small', { onclick: () => all(true) }, tr('Tout cocher')), h('button.btn.small', { onclick: () => all(false) }, tr('Tout décocher'))),
    h('div.checks.single.scroll-list', checks)), [
    { label: tr('Annuler'), value: null },
    { label: tr('Enregistrer'), primary: true, handler: async () => {
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
  const vmode = select([['auto', tr('Automatique (H.264 recopié, le reste converti)')], ['copy', tr('Ne jamais convertir (le moins de processeur)')],
    ['transcode', tr('Toujours convertir en H.264 (le plus compatible)')]], st.video);
  const hide = toggle(tr('Masquer la TV en direct pour les profils avec une restriction d’âge'), st.hide_restricted);
  clear(body,
    h('section.panel', h('div.toolbar', h('div', h('h2', tr('Sources de chaînes')),
      h('p.muted.small', tr('Listes M3U (IPTV, box opérateur, tuner TNT réseau) et guides XMLTV. Les chaînes sont proposées à tous les utilisateurs dans l’onglet « TV en direct ». L’actualisation automatique se règle dans « Tâches planifiées ».'))),
    h('button.btn.primary', { onclick: async () => { if (await liveSourceForm(null)) { toast(tr('Source ajoutée'), 'ok'); reload(); } } }, tr('+ Ajouter'))),
    data.sources.length ? h('div.user-list', data.sources.map((s) => h('div.user-row', { class: s.enabled ? 'user-row' : 'user-row off' },
      h('div.lib-icon', '📡'),
      h('div.grow', h('strong', s.name),
        h('div.muted.small', tr('{channels} chaîne(s)', { channels: s.channels }), s.hidden ? tr(' dont {hidden} masquée(s)', { hidden: s.hidden }) : '', s.dead ? tr(' · {dead} hors service (masquée(s) automatiquement)', { dead: s.dead }) : '', tr(' · {programmes} programme(s) dans le guide', { programmes: s.programmes }),
          tr(' · actualisée {p1}', { p1: since(s.refreshed_at) })),
        h('div.small', h('code', s.m3u_url.length > 70 ? `${s.m3u_url.slice(0, 70)}…` : s.m3u_url)),
        s.error ? h('div.form-error.small', s.error) : null),
      h('button.btn.small', { onclick: async (e) => {
        e.currentTarget.disabled = true;
        try { const r = await post(`/api/admin/live/sources/${s.id}/refresh`); toast(tr('{channels} chaîne(s){p2}', { channels: r.channels, p2: r.programmes != null ? tr(', {programmes} programme(s)', { programmes: r.programmes }) : '' }), 'ok'); }
        catch (ex) { toast(ex.message, 'bad'); }
        reload();
      } }, tr('Actualiser')),
      h('button.btn.small', { onclick: async () => { if (await liveGroups(s)) { toast(tr('Groupes enregistrés'), 'ok'); reload(); } } }, tr('Groupes…')),
      h('button.btn.small', { onclick: async () => { if (await liveSourceForm(s)) { toast(tr('Source modifiée'), 'ok'); reload(); } } }, tr('Modifier')),
      h('button.icon-btn', { title: tr('Supprimer'), onclick: async () => {
        if (!await confirmDialog(tr('Supprimer cette source ?'), tr('Les chaînes de « {name} » et les favoris associés disparaîtront.', { name: s.name }), tr('Supprimer'))) return;
        await del(`/api/admin/live/sources/${s.id}`); reload();
      } }, icon('trash')))))
      : h('div.empty', tr('Aucune source configurée.'))),
    h('section.panel', h('h2', tr('Lecture')),
      h('p.muted.small', tr('Les navigateurs ne lisent pas directement les flux TV (MPEG-TS) : Nexis les reçoit et les convertit. Une chaîne regardée par plusieurs personnes n’est reçue qu’une fois. La plupart des fournisseurs limitent le nombre de connexions simultanées.')),
      h('div.grid2', field(tr('Chaînes regardées en même temps (max.)'), maxS), field(tr('Vidéo'), vmode)),
      hide,
      h('button.btn.primary', { onclick: async () => {
        try { await put('/api/admin/live/settings', { max_sessions: Number(maxS.value), video: vmode.value, hide_restricted: hide.querySelector('input').checked }); toast(tr('Enregistré'), 'ok'); reload(); }
        catch (e) { toast(e.message, 'bad'); }
      } }, tr('Enregistrer'))),
    data.sessions.length ? h('section.panel', h('h2', tr('Chaînes regardées en ce moment')), h('div.user-list', data.sessions.map((x) => h('div.user-row',
      h('div.grow', h('strong', x.channel), h('div.muted.small', tr('{p1} · {p2}{p3} · depuis {p4}', { p1: x.viewers.join(', ') || tr('personne'), p2: x.mode === 'transcode' ? tr('vidéo convertie') : tr('vidéo recopiée'), p3: x.codec ? ` (${x.codec})` : '', p4: since(x.since) })),
        x.error ? h('div.form-error.small', x.error) : null),
      h('button.btn.small.danger', { onclick: async () => { await del(`/api/admin/live/sessions/${x.sid}`); reload(); } }, tr('Arrêter')))))) : null);
}

// ---------------------------------------------------------------- streams
const MODES = { direct: tr('Lecture directe'), copy: tr('Remux (vidéo copiée)'), transcode: tr('Transcodage') };

async function dashboardPage(body, ctx) {
  const version = state.status.version;
  const latest = state.me.update;
  const versionCard = h('div.panel.scan', h('strong', `Nexis ${version}`), latest
    ? h('div.small', h('span.tag.accent', tr('Mise à jour')), tr(' Nexis {latest} est disponible · ', { latest }), h('a', { href: '#/admin/update' }, tr('Mettre à jour')))
    : h('div.muted.small', tr('À jour (vérification automatique toutes les 6 heures) · '), h('a', { href: '#/admin/update' }, tr('Détails'))));
  const load = async () => {
    const [list, sys] = await Promise.all([get('/api/admin/streams'), get('/api/admin/system').catch(() => null)]);
    if (!ctx.isCurrent()) return;
    const gb = (mb) => tr('{n} Go', { n: (mb / 1024).toLocaleString(locale(), { maximumFractionDigits: 1, minimumFractionDigits: 1 }) });
    const swapHigh = sys && sys.swap_total && sys.swap_used > sys.swap_total * 0.25;
    const server = sys && sys.mem_total ? h('div.panel.scan', h('strong', tr('Serveur')), h('div.muted.small',
      tr('Mémoire libre {p1} sur {p2} · swap utilisé {p3} sur {p4} · ', { p1: gb(sys.mem_available), p2: gb(sys.mem_total), p3: gb(sys.swap_used), p4: gb(sys.swap_total || 0) }),
      tr('{ffmpeg} ffmpeg ({p2}) · charge {p3} pour {cpus} cœur(s)', { ffmpeg: sys.ffmpeg, p2: gb(sys.ffmpeg_mb), p3: Number(sys.load).toLocaleString(locale()), cpus: sys.cpus })),
      swapHigh ? h('div.form-error.small', tr('Utilisation élevée du swap : les lectures peuvent saccader. Réduire le nombre de transcodages simultanés ou ajouter de la mémoire.')) : null) : null;
    const sec = sys && sys.security;
    const secLine = (ok, label, hint) => h('li', { class: ok ? 'sec-ok' : 'sec-warn' }, h('strong', ok ? '✓ ' : '! '), label, hint ? h('span.muted', ` · ${hint}`) : null);
    const security = sec ? h('div.panel.scan', h('strong', tr('Sécurité de l’accès')), h('ul.sec-list.small',
      secLine(sec.cookie_secure, sec.cookie_secure ? tr('Cookies réservés au HTTPS') : tr('Cookies envoyés aussi en HTTP'),
        sec.cookie_secure ? null : tr('SEANCE_COOKIE_SECURE=1 si Nexis est exposé sur Internet')),
      secLine(!sec.via_proxy || sec.trust_proxy, sec.trust_proxy ? tr('Adresse des visiteurs lue depuis le proxy') : (sec.via_proxy ? tr('Proxy détecté mais non déclaré') : tr('Accès direct (sans proxy)')),
        sec.via_proxy && !sec.trust_proxy ? tr('SEANCE_TRUST_PROXY=1 pour distinguer les visiteurs') : null),
      secLine(sec.https || !sec.via_proxy, sec.https ? tr('Connexion chiffrée (HTTPS)') : (sec.via_proxy ? tr('Proxy sans HTTPS') : tr('Connexion locale en HTTP'))),
      secLine(sec.admins_2fa === sec.admins, tr('Double authentification : {admins_2fa} administrateur(s) sur {admins}', { admins_2fa: sec.admins_2fa, admins: sec.admins }),
        sec.admins_2fa === sec.admins ? null : tr('à activer dans Paramètres → Profil par chaque administrateur')),
      h('li.muted', tr('Adresse vue par le serveur pour cette session : {client_ip}', { client_ip: sec.client_ip })))) : null;
    clear(body, versionCard, server, security, h('h3.sub', tr('Lectures en cours')), list.length ? h('div.user-list', list.map((s) => h('div.user-row',
      h('div.grow', h('strong', s.title), h('div.muted.small',
        tr(s.state === 'paused' ? '{user} · en pause à {time} · {mode}' : '{user} · en lecture à {time} · {mode}', { user: s.user, time: fmtTime(s.position_ms), mode: MODES[s.mode] || s.mode }),
        s.hw ? tr(' · carte graphique ({p1})', { p1: s.hw === 'nvenc' ? 'NVIDIA' : 'VAAPI' }) : '',
        s.source ? tr(' · source {source}', { source: s.source }) : '', s.rss_mb ? tr(' · ffmpeg {rss_mb} Mo', { rss_mb: s.rss_mb }) : '',
        s.peer ? tr(' · relayée depuis {peer}', { peer: s.peer }) : '',
        s.reasons && s.reasons.length ? ` (${s.reasons.join(', ')})` : '',
        s.speed ? h('span', { class: s.speed < 1.05 && s.mode === 'transcode' ? 'tag bad' : 'tag ok' }, tr('vitesse ×{speed}', { speed: s.speed })) : null),
        s.warnings && s.warnings.length ? h('details.small', h('summary', tr('Messages de ffmpeg')), h('pre.log', s.warnings.join('\n'))) : null),
      h('button.btn.small.danger', { onclick: async () => { await del(`/api/admin/streams/${s.id}`); load(); } }, tr('Arrêter')))))
      : h('div.empty', tr('Aucune lecture en cours.')));
  };
  await load();
  const t = setInterval(load, 10000);
  ctx.setCleanup(() => clearInterval(t));
}

// ----------------------------------------------------------------- serveurs liés
async function inviteDialog(view) {
  const address = h('input', { value: view.address_manual || view.address, placeholder: view.address });
  const out = h('div');
  const err = h('p.form-error');
  const body = h('div.form',
    h('p.muted.small', tr('Créez l’invitation sur le serveur que l’autre peut joindre : en réseau local, n’importe lequel ; par Internet, celui dont le port {port} est ouvert (redirection sur la box ou reverse proxy TCP).', { port: view.port })),
    field(tr('Adresse de ce serveur, vue depuis l’autre'), address, tr('Adresse IP ou nom, suivi du port des serveurs liés (ex. 192.168.1.20:{port}).', { port: view.port })),
    out, err);
  return modal(tr('Inviter un serveur'), body, [
    { label: tr('Fermer'), value: null },
    { label: tr('Créer l’invitation'), primary: true, handler: async () => {
      err.textContent = '';
      try {
        const r = await post('/api/admin/federation/invite', { address: address.value.trim() });
        const text = h('textarea.invite-text', { readonly: true, rows: 4, onfocus: (e) => e.target.select() }, r.invite);
        clear(out,
          field(tr('Invitation (à coller sur l’autre serveur, dans Serveurs liés → Rejoindre)'), text,
            tr('Valable 15 minutes, une seule fois. Elle contient l’empreinte du certificat de ce serveur : l’autre vérifiera qu’il parle bien à celui-ci.')),
          h('div.actions-row', h('button.btn', { type: 'button', onclick: async () => {
            try { await navigator.clipboard.writeText(r.invite); toast(tr('Invitation copiée'), 'ok'); }
            catch { text.select(); toast(tr('Copie impossible : sélectionnez le texte'), 'bad'); }
          } }, tr('Copier'))));
      } catch (e) { err.textContent = e.message; }
      return false;
    } },
  ]);
}

async function joinDialog() {
  const invite = h('textarea', { rows: 4, placeholder: 'NEXIS1-…' });
  const address = h('input', { placeholder: tr('facultatif, ex. 192.168.1.30:8421') });
  const err = h('p.form-error');
  const body = h('div.form',
    field(tr('Invitation créée sur l’autre serveur'), invite),
    field(tr('Adresse de ce serveur, vue depuis l’autre'), address,
      tr('Laissez vide si l’autre ne peut pas joindre ce serveur (box sans port ouvert) : ce serveur lui ouvrira lui-même un tunnel chiffré.')),
    h('div.status-box', tr('Les comptes sont mis en commun. Un compte portant le même identifiant sur les deux serveurs devient un seul compte, avec le mot de passe et les réglages du serveur qui a créé l’invitation.')),
    err);
  return modal(tr('Rejoindre un serveur'), body, [
    { label: tr('Annuler'), value: null },
    { label: tr('Lier les serveurs'), primary: true, handler: async () => {
      err.textContent = tr('Connexion à l’autre serveur…');
      try { return await post('/api/admin/federation/join', { invite: invite.value.trim(), address: address.value.trim() }); }
      catch (e) { err.textContent = e.message; return false; }
    } },
  ]);
}

const shortPrint = (fp) => (fp ? fp.slice(0, 32).toUpperCase().match(/.{4}/g).join(' ') : '—');

async function federationPage(body, ctx) {
  const render = (v) => {
    if (!ctx.isCurrent()) return;
    const name = h('input', { value: v.name, maxlength: 40 });
    const me = h('section.panel',
      h('h2', tr('Ce serveur')),
      h('div.grid2', field(tr('Nom affiché chez les serveurs liés'), name), h('div.field',
        h('span.field-label', tr('Empreinte du certificat')), h('code.small', shortPrint(v.fingerprint)),
        h('span.field-hint', v.listening ? tr('Port des serveurs liés : {port} (TLS)', { port: v.port }) : tr('Le port des serveurs liés s’ouvrira à la première invitation.')))),
      h('div.actions-row', h('button.btn', { onclick: async () => {
        try { render(await put('/api/admin/federation', { name: name.value.trim() })); toast(tr('Enregistré'), 'ok'); }
        catch (e) { toast(e.message, 'bad'); }
      } }, tr('Enregistrer'))));
    const state = (p) => (p.online === undefined || p.online === null ? h('span.tag', tr('En attente'))
      : p.online ? h('span.tag.ok', p.via === 'tunnel' ? tr('En ligne · par tunnel') : tr('En ligne · direct'))
        : h('span.tag.bad', tr('Injoignable')));
    const peers = v.peers.length ? h('div.user-list', v.peers.map((p) => h('div.user-row.peer-row',
      h('div.lib-icon', '🖧'),
      h('div.grow', h('strong', p.name), ' ', state(p),
        h('div.muted.small',
          p.address ? tr('Adresse {address}', { address: p.address }) : tr('Sans adresse : joint par le tunnel qu’il ouvre'),
          p.latency != null && p.online ? tr(' · {ms} ms', { ms: p.latency }) : '',
          p.version ? ` · Nexis ${p.version}` : '',
          tr(' · vu {when}', { when: since(p.last_seen) })),
        p.error && !p.online ? h('div.form-error.small', p.error) : null,
        p.libraries.length ? h('div.peer-libs', p.libraries.map((l) => {
          const options = [['', tr('Nouvelle bibliothèque')], ...v.local_libraries.filter((t) => t.kind === l.kind)
            .map((t) => [t.id, tr('Fusionner avec « {name} »', { name: t.name })])];
          const pick = select(options, l.merge_into || '', { 'aria-label': tr('Bibliothèque « {name} »', { name: l.name }),
            onchange: async () => {
              pick.disabled = true;
              try { render(await put(`/api/admin/federation/libraries/${l.id}`, { merge_into: pick.value || null })); await refreshMe(); toast(tr('Enregistré'), 'ok'); }
              catch (e) { toast(e.message, 'bad'); pick.disabled = false; }
            } });
          return h('div.peer-lib', h('div.grow', h('strong', l.name),
            h('div.muted.small', l.kind === 'show' ? plural(l.count, '{n} série', '{n} séries') : plural(l.count, '{n} film', '{n} films'),
              l.merge_into && l.merged ? tr(' · {n} en commun avec ce serveur', { n: l.merged }) : '')), pick);
        })) : null),
      h('div.peer-actions', h('button.btn.small', { onclick: async (e) => {
        e.currentTarget.disabled = true;
        try { render(await post(`/api/admin/federation/peers/${p.id}/sync`)); } catch (ex) { toast(ex.message, 'bad'); }
      } }, tr('Synchroniser')),
      h('button.btn.small', { onclick: async () => {
        const input = h('input', { value: p.address || '', placeholder: tr('vide : par tunnel uniquement') });
        const ok = await modal(tr('Adresse de « {name} »', { name: p.name }), h('div.form', field(tr('Adresse et port des serveurs liés'), input,
          tr('Comment ce serveur joint l’autre. Vide si l’autre n’est pas joignable : il passe alors par le tunnel qu’il ouvre.'))),
        [{ label: tr('Annuler'), value: null }, { label: tr('Enregistrer'), primary: true, value: true }]);
        if (!ok) return;
        try { render(await put(`/api/admin/federation/peers/${p.id}`, { address: input.value.trim() })); } catch (e) { toast(e.message, 'bad'); }
      } }, tr('Adresse')),
      h('button.icon-btn', { title: tr('Délier ce serveur'), onclick: async () => {
        if (!await confirmDialog(tr('Délier « {name} » ?', { name: p.name }),
          tr('Ses bibliothèques disparaissent de ce serveur (et les vôtres du sien). Les comptes restent sur les deux serveurs, mais ne sont plus synchronisés. Aucun fichier n’est touché.'), tr('Délier'))) return;
        try { render(await del(`/api/admin/federation/peers/${p.id}`)); toast(tr('Serveur délié'), 'ok'); } catch (e) { toast(e.message, 'bad'); }
      } }, icon('trash'))))))
      : h('div.empty', tr('Aucun serveur lié.'));
    clear(body,
      h('div.toolbar', h('div.actions-row',
          h('button.btn', { onclick: async () => { const r = await joinDialog(); if (r) { toast(tr('Serveurs liés'), 'ok'); await refreshMe(); render(r); } } }, tr('Rejoindre un serveur')),
          h('button.btn.primary', { onclick: async () => { await inviteDialog(v); load(); } }, tr('+ Inviter un serveur')))),
      peers, me,
      h('section.panel', h('h2', tr('Comment ça marche')), h('ul.small.muted',
        h('li', tr('Les deux serveurs se reconnaissent par leur certificat, échangé à l’invitation, et chiffrent tout ce qu’ils s’envoient (TLS). Pas besoin de VPN ni de nom de domaine, et cela marche aussi en HTTP sur le réseau local.')),
        h('li', tr('Un seul des deux doit pouvoir joindre l’autre : celui qui ne l’est pas ouvre lui-même un tunnel vers lui. Rien à ouvrir sur sa box.')),
        h('li', tr('Les comptes, mots de passe, profils et accès aux bibliothèques sont les mêmes sur les deux serveurs ; une modification faite sur l’un arrive sur l’autre en moins d’une minute.')),
        h('li', tr('Une bibliothèque reçue s’ajoute comme une nouvelle, ou se fusionne avec l’une des vôtres : un titre présent sur les deux serveurs n’apparaît qu’une fois, et les saisons et épisodes de chacun sont réunis.')),
        h('li', tr('À la lecture d’un titre présent sur les deux serveurs, la copie est choisie toute seule : celle qui a la langue voulue si une seule l’a, sinon la meilleure qualité. Si un serveur ne répond pas, l’autre prend le relais, et les titres qu’il est seul à avoir sont masqués jusqu’à son retour.')),
        h('li', tr('Une lecture d’un titre de l’autre serveur passe par celui-ci : c’est l’autre serveur qui lit le fichier et le convertit si besoin. Le débit montant de sa connexion limite la qualité à distance.')),
        h('li', tr('À réserver à vos propres serveurs : partager des films avec un autre foyer sort du cadre de la copie privée.')))));
  };
  const load = async () => { const v = await get('/api/admin/federation'); render(v); };
  await load();
  const t = setInterval(() => { if (!document.querySelector('.modal-overlay')) load(); }, 15000);
  ctx.setCleanup(() => clearInterval(t));
}

const PAGES = { dashboard: dashboardPage, users: usersTab, update: updatePage, tasks: tasksTab, libraries: librariesTab,
  federation: federationPage, shares: sharesTab, metadata: metadataPage, detection: detectionPage, requests: requestsTab, transcoding: transcodingPage,
  subtitles: subtitlesPage, downloads: downloadsPage, live: liveTab };

export async function renderAdminSection(id, body, ctx) {
  await PAGES[id](body, ctx);
}
