import { adminTarget, adminUrl, del, get, post, put } from '../api.js';
import { refreshMe, state } from '../app.js';
import { followUpdate } from '../updater.js';
import { avatar, clear, confirmDialog, field, fmtTime, h, icon, modal, select, spinner, store, toast, toggle } from '../ui.js';
import { LANGUAGES, locale, plural, tr } from '../i18n.js';

// Pages de l'administration, rangées par thème dans le menu des Paramètres.
// keys : mots qui la font trouver avec la recherche du menu.
export const ADMIN_GROUPS = [
  { title: tr('Serveur'), items: [
    { id: 'dashboard', label: tr('Vue d’ensemble'), icon: 'dashboard', lead: tr('Version, état du serveur et lectures en cours.'),
      keys: tr('tableau de bord lectures en cours streams mémoire swap charge processeur version arrêter') },
    { id: 'stats', label: tr('Statistiques'), icon: 'list', lead: tr('Qui regarde quoi, combien de temps, débit utilisé, et les titres jamais regardés.'),
      keys: tr('statistiques activité heures visionnage débit données comptes titres populaires jamais regardés place disque') },
    { id: 'users', label: tr('Utilisateurs'), icon: 'group', lead: tr('Comptes, profils du foyer, contrôle parental et accès aux bibliothèques.'),
      keys: tr('compte profil enfant pin âge restriction administrateur désactiver mot de passe foyer langue language') },
    { id: 'update', label: tr('Mise à jour'), icon: 'upgrade', lead: tr('Version installée et mises à jour de Nexis.'),
      keys: tr('version mettre à jour github nouveautés changements réinstaller') },
    { id: 'backups', label: tr('Sauvegardes'), icon: 'backup', lead: tr('Sauvegarde automatique de la base et des clés, et restauration.'),
      keys: tr('sauvegarde backup restaurer restauration archive base de données migration nouveau serveur') },
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
    { id: 'cold', label: tr('Stockage froid'), icon: 'backup', lead: tr('Copies des titres sur un autre stockage (disque, NAS, cloud) : lus depuis là si le fichier quitte la bibliothèque.'),
      keys: tr('stockage froid archive archiver libérer place disque usb nas sftp webdav s3 backblaze hetzner storage box nextcloud rclone strm') },
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
    { id: 'subtitles', label: tr('Sous-titres et son'), icon: 'subs', lead: tr('Sous-titres manquants (OpenSubtitles), sous-titres intégrés et mixage du son 5.1 / 7.1.'),
      keys: tr('sous-titres opensubtitles bazarr télécharger manquants préparer audio son 5.1 7.1 stéréo mixage downmix volume dialogues') },
    { id: 'dlna', label: tr('DLNA'), icon: 'tv', lead: tr('Téléviseurs, consoles et box du réseau local : Nexis apparaît comme serveur multimédia.'),
      keys: tr('dlna upnp téléviseur smart tv samsung lg sony console xbox ps5 box réseau local serveur multimédia') },
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

async function userForm(user, libraries, defaultLang = 'fr', others = []) {
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
  // temps d'écran et limites du compte
  const minutes = select([['', tr('Illimité')], ['30', tr('30 min')], ['45', tr('45 min')], ['60', tr('1 h')], ['90', tr('1 h 30')],
    ['120', tr('2 h')], ['180', tr('3 h')], ['240', tr('4 h')], ['360', tr('6 h')]],
  user.daily_minutes ? String(user.daily_minutes) : '');
  if (user.daily_minutes && !minutes.querySelector(`option[value="${user.daily_minutes}"]`)) {
    minutes.append(h('option', { value: String(user.daily_minutes), selected: true }, tr('{n} min', { n: user.daily_minutes })));
  }
  const [bedFrom, bedTo] = (user.bedtime || '-').split('-');
  const bedOn = toggle(tr('Heure du coucher : pas de lecture la nuit'), !!user.bedtime);
  const bedStart = h('input', { type: 'time', value: bedFrom || '21:00' });
  const bedEnd = h('input', { type: 'time', value: bedTo || '07:00' });
  const bedFields = h('div.grid2', field(tr('À partir de'), bedStart), field(tr('Jusqu’à'), bedEnd));
  const streams = h('input', { type: 'number', min: 1, max: 20, value: user.max_streams || '', placeholder: tr('Illimité') });
  const remote = select([['', tr('Illimité')], ['2000', '2 Mbit/s'], ['4000', '4 Mbit/s'], ['8000', '8 Mbit/s'], ['12000', '12 Mbit/s'],
    ['20000', '20 Mbit/s'], ['40000', '40 Mbit/s']], user.remote_kbps ? String(user.remote_kbps) : '');
  if (user.remote_kbps && !remote.querySelector(`option[value="${user.remote_kbps}"]`)) {
    remote.append(h('option', { value: String(user.remote_kbps), selected: true }, `${(user.remote_kbps / 1000).toLocaleString(locale())} Mbit/s`));
  }
  const admin = toggle(tr('Administrateur'), user.is_admin);
  const copyFrom = select([['', tr('Ne rien copier')], ...others.filter((o) => !user.id || o.id !== user.id).map((o) => [o.id, o.display_name])], '');
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
    bedFields.hidden = !bedOn.querySelector('input').checked;
  };
  [all, managed, bedOn].forEach((t) => t.querySelector('input').addEventListener('change', sync));
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
    h('h4.form-sub', tr('Temps d’écran et limites')),
    h('div.grid2', field(tr('Temps d’écran par jour'), minutes, user.screen_used != null ? tr('{n} min regardées aujourd’hui.', { n: user.screen_used }) : null),
      field(tr('Lectures simultanées (max.)'), streams, tr('Sur tous les appareils du compte.'))),
    bedOn, bedFields,
    field(tr('Débit maximum hors de la maison'), remote, tr('Depuis l’extérieur, la qualité est réduite pour tenir dans ce débit. À la maison, rien ne change.')),
    others.length ? h('h4.form-sub', tr('Réglages')) : null,
    others.length ? field(tr('Copier les réglages de…'), copyFrom, tr('Apparence, langues, lecture, accueil et menu de ce compte remplacent ceux-ci (pratique pour les profils des enfants).')) : null,
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
        daily_minutes: minutes.value ? Number(minutes.value) : null,
        bedtime: bedOn.querySelector('input').checked ? `${bedStart.value || '21:00'}-${bedEnd.value || '07:00'}` : null,
        max_streams: streams.value ? Number(streams.value) : null,
        remote_kbps: remote.value ? Number(remote.value) : null,
      };
      if (pass.value && !isManaged) payload.password = pass.value;
      if (pin.value) payload.pin = pin.value;
      else if (clearPin && clearPin.querySelector('input').checked) payload.pin = '';
      if (!isNew) payload.disabled = disabled.querySelector('input').checked;
      try {
        const saved = isNew ? await post('/api/admin/users', { username: username.value.trim(), ...payload })
          : await put(`/api/admin/users/${user.id}`, payload);
        if (copyFrom.value) await post(`/api/admin/users/${(saved && saved.id) || user.id}/prefs`, { from: Number(copyFrom.value) });
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
      h('button.btn.primary', { onclick: async () => { if (await userForm(null, libraries, defaultLang, users)) { toast(tr('Utilisateur créé'), 'ok'); reload(); } } }, tr('+ Ajouter'))),
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
      h('button.btn.small', { onclick: async () => { if (await userForm(u, libraries, defaultLang, users)) { toast(tr('Modifications enregistrées'), 'ok'); reload(); } } }, tr('Modifier')),
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
  const kind = select([['movie', tr('Films')], ['show', tr('Séries (et animes)')], ['photo', tr('Photos')], ['audiobook', tr('Livres audio')]], lib.kind);
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
    field(tr('Dossiers'), paths, tr('Chemins sur le serveur Nexis (ex. /mnt/films). Chaque film ou série dans son propre sous-dossier ; pour des photos, chaque sous-dossier devient un album.')),
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
      h('div.lib-icon', l.kind === 'movie' ? '🎬' : l.kind === 'photo' ? '🖼️' : l.kind === 'audiobook' ? '🎧' : '📺'),
      h('div.grow', h('strong', l.name),
        h('div.muted.small', l.kind === 'movie' ? plural(c(l, 'movie'), '{n} film', '{n} films')
          : l.kind === 'photo' ? tr('{p1} photos · {p2} albums', { p1: c(l, 'photo'), p2: c(l, 'album') })
            : l.kind === 'audiobook' ? plural(c(l, 'book'), '{n} livre', '{n} livres')
              : tr('{p1} séries · {p2} épisodes', { p1: c(l, 'show'), p2: c(l, 'episode') }),
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
      h('div.status-box', { class: s.tmdb_set || s.tmdb_shared ? 'status-box ok' : 'status-box' }, s.tmdb_set ? tr('Clé TMDB configurée.')
        : s.tmdb_shared ? tr('Pas de clé ici : celle du serveur lié « {name} » est utilisée.', { name: s.tmdb_shared }) : tr('Aucune clé TMDB.')),
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
  const osPanel = h('section.panel', h('h2', 'OpenSubtitles'), h('p.muted.small', tr('Chargement…')));
  openSubtitlesPanel(osPanel);
  const wPanel = h('section.panel', h('h2', tr('Sous-titres générés (Whisper)')), h('p.muted.small', tr('Chargement…')));
  whisperPanel(wPanel);
  return [
    osPanel, wPanel,
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

// Sous-titres téléchargés depuis OpenSubtitles.com : compte, langues, quota du jour
async function openSubtitlesPanel(panel) {
  let v;
  try { v = await get('/api/admin/opensubtitles'); } catch (e) { clear(panel, h('h2', 'OpenSubtitles'), h('p.muted', e.message)); return; }
  const key = h('input', { type: 'password', autocomplete: 'off', placeholder: v.key_set ? tr('Clé enregistrée (laisser vide pour la garder)') : tr('Clé d’API (consumer)') });
  const user = h('input', { value: v.user, autocomplete: 'off', spellcheck: false });
  const pass = h('input', { type: 'password', autocomplete: 'new-password', placeholder: v.password_set ? tr('Enregistré (laisser vide pour le garder)') : '' });
  const chosen = new Set(v.langs);
  const chips = h('div.chips', v.all_langs.map((c) => {
    const b = h('button.chip', { type: 'button', 'aria-pressed': String(chosen.has(c)), onclick: () => {
      if (chosen.has(c)) chosen.delete(c); else if (chosen.size < 6) chosen.add(c);
      b.setAttribute('aria-pressed', String(chosen.has(c)));
    } }, v.lang_names[c] || c);
    return b;
  }));
  const hi = toggle(tr('Accepter les sous-titres pour sourds et malentendants (SDH) s’il n’y a qu’eux'), v.hi);
  const status = v.configured
    ? h('div.status-box.ok', v.remaining != null ? tr('Compte connecté : {n} téléchargement(s) restant(s) aujourd’hui.', { n: v.remaining }) : tr('Compte connecté.'))
    : h('div.status-box', tr('Pas encore configuré.'));
  const saveBtn = h('button.btn.primary', { onclick: async () => {
    saveBtn.disabled = true;
    try {
      const body = { user: user.value.trim(), langs: [...chosen], hi: hi.querySelector('input').checked };
      if (key.value.trim()) body.key = key.value.trim();
      if (pass.value) body.password = pass.value;
      const r = await put('/api/admin/opensubtitles', body);
      toast(r.account ? tr('Connexion réussie : {n} téléchargement(s) restant(s) aujourd’hui', { n: r.account.remaining ?? '?' }) : tr('Enregistré'), 'ok');
      await refreshMe();
      openSubtitlesPanel(panel);
    } catch (e) { toast(e.message, 'bad'); saveBtn.disabled = false; }
  } }, tr('Tester et enregistrer'));
  clear(panel, h('h2', 'OpenSubtitles'),
    h('p.muted.small', tr('Nexis télécharge lui-même les sous-titres manquants, sans Bazarr. Il faut un compte gratuit sur opensubtitles.com (20 sous-titres par jour) et une clé d’API, créée sur le site dans « API consumers ». Les sous-titres sont rangés dans le dossier de données de Nexis, jamais à côté des vidéos.')),
    status,
    h('div.grid2', field(tr('Clé d’API'), key), field(tr('Identifiant OpenSubtitles'), user)),
    h('div.grid2', field(tr('Mot de passe OpenSubtitles'), pass), h('div')),
    field(tr('Langues à télécharger automatiquement (6 au plus)'), chips,
      tr('Un film ou un épisode qui n’a aucun sous-titre complet dans l’une de ces langues en reçoit un, chaque nuit et après chaque ajout (tâche « Télécharger les sous-titres manquants »). Les sous-titres calés sur le fichier exact passent en premier ; les traductions automatiques sont écartées.')),
    hi,
    v.last ? h('p.muted.small', tr('Dernier passage : {summary}', { summary: v.last.summary || v.last.status })) : null,
    h('div.actions-row', saveBtn, h('a.btn', { href: '#/admin/tasks' }, tr('Tâches planifiées'))));
}

// Whisper : transcription de la voix (service compatible OpenAI, ou whisper.cpp sur ce serveur)
async function whisperPanel(panel) {
  let v;
  try { v = await get('/api/admin/whisper'); } catch (e) { clear(panel, h('h2', 'Whisper'), h('p.muted', e.message)); return; }
  const url = h('input', { value: v.url, placeholder: 'http://192.168.1.20:8000', spellcheck: false });
  const key = h('input', { type: 'password', autocomplete: 'off', placeholder: v.key_set ? tr('Clé enregistrée (laisser vide pour la garder)') : tr('Facultative') });
  const modelHttp = h('input', { value: v.model_http, spellcheck: false });
  const threads = h('input', { type: 'number', min: 1, max: 64, value: v.threads });
  const local = v.filter || v.cli;
  const save = async (patch, msg = tr('Enregistré')) => {
    try { await put('/api/admin/whisper', patch); toast(msg, 'ok'); await refreshMe(); whisperPanel(panel); } catch (e) { toast(e.message, 'bad'); }
  };
  const status = v.available
    ? h('div.status-box.ok', v.backend === 'http' ? tr('Prêt : service de transcription.') : tr('Prêt : whisper.cpp sur ce serveur (modèle {m}).', { m: v.model }))
    : h('div.status-box', v.note);
  const job = v.current ? h('p.small', tr('En cours : {title} ({p} %)', { title: v.current.title, p: Math.round((v.current.progress || 0) * 100) }),
    v.queue.length ? tr(' · {n} en attente', { n: v.queue.length }) : '') : null;
  const dl = v.download && !v.download.finished ? h('p.small', tr('Téléchargement du modèle {m} : {p} %', { m: v.download.model,
    p: Math.round((v.download.done / Math.max(1, v.download.total)) * 100) })) : (v.download && v.download.error ? h('p.form-error.small', v.download.error) : null);
  const modelRows = Object.entries(v.models).map(([m, info]) => h('div.os-row', h('div.os-info', h('strong', m),
    h('div.muted.small', tr('{n} Mo', { n: info.size_mb }), m === 'base' ? tr(' · bon compromis') : m === 'small' ? tr(' · plus juste, 3 fois plus lent') : '')),
    info.installed
      ? (v.model === m ? h('span.os-badge.good', tr('utilisé')) : h('button.btn.small', { onclick: () => save({ model: m }) }, tr('Utiliser')))
      : h('button.btn.small', { onclick: async () => {
        try { await post('/api/admin/whisper/model', { model: m }); toast(tr('Téléchargement lancé'), 'ok'); setTimeout(() => whisperPanel(panel), 1500); } catch (e) { toast(e.message, 'bad'); }
      } }, tr('Télécharger'))));
  clear(panel, h('h2', tr('Sous-titres générés (Whisper)')),
    h('p.muted.small', tr('Pour les vidéos sans aucun sous-titre dans la langue parlée, Whisper écrit les sous-titres à partir de la voix (dans cette langue : il ne traduit pas). C’est un calcul long ; il se fait une vidéo à la fois, en pause pendant les lectures.')),
    status, job, dl,
    h('h4.form-sub', tr('Service de transcription (recommandé)')),
    h('p.muted.small', tr('Un service compatible avec l’API d’OpenAI, par exemple Speaches (faster-whisper) lancé dans un conteneur à côté de Nexis, avec une carte graphique s’il y en a une. OpenAI convient aussi, mais l’audio des vidéos part alors sur Internet.')),
    h('div.grid2', field(tr('Adresse du service'), url), field(tr('Modèle'), modelHttp, tr('Ex. Systran/faster-whisper-small pour Speaches, whisper-1 pour OpenAI.'))),
    h('div.grid2', field(tr('Clé d’API'), key), h('div')),
    h('div.actions-row', h('button.btn.primary', { onclick: () => {
      const patch = { url: url.value.trim(), model_http: modelHttp.value.trim() || 'whisper-1' };
      if (key.value.trim()) patch.key = key.value.trim();
      save(patch);
    } }, tr('Enregistrer')),
    v.key_set ? h('button.btn', { onclick: () => save({ clear_key: true }, tr('Clé retirée')) }, tr('Retirer la clé')) : null),
    h('h4.form-sub', tr('Sur ce serveur (whisper.cpp)')),
    local
      ? [h('p.muted.small', v.filter ? tr('ffmpeg de ce serveur sait transcrire (filtre whisper). Choisissez un modèle : il est téléchargé dans le dossier de données.')
        : tr('whisper-cli est installé. Choisissez un modèle : il est téléchargé dans le dossier de données.')),
      h('div.os-results', modelRows), field(tr('Cœurs utilisés'), threads, tr('{n} cœur(s) sur ce serveur.', { n: v.cpus })),
      h('button.btn', { onclick: () => save({ threads: Number(threads.value) }) }, tr('Enregistrer'))]
      : h('p.muted.small', tr('Ni ffmpeg 8 avec le filtre whisper, ni whisper-cli n’est installé sur ce serveur (l’image Docker ne les contient pas) : utilisez un service de transcription.')),
    h('p.muted.small', tr('Automatique : tâche « Générer les sous-titres manquants (Whisper) », désactivée par défaut. À la main : fiche d’un film ou d’un épisode, menu ⋯.')));
}

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
    h('section.panel', h('h2', tr('Films HDR')),
      h('p.muted.small', tr('Quand un film HDR (HDR10, HLG, Dolby Vision avec base HDR10) doit être transcodé, ses couleurs sont converties pour un écran ordinaire ; sans cela, l’image paraît délavée et grise. La lecture directe d’un fichier HDR n’est pas touchée. La conversion se fait sur le processeur et coûte environ un cœur de plus en 1080p.')),
      s.tonemap_ok ? null : h('div.status-box.bad', tr('Le ffmpeg de ce serveur n’a pas le filtre zscale : la conversion HDR est impossible.')),
      field(tr('Conversion HDR → SDR'), select([['hable', tr('Hable (contraste naturel, par défaut)')], ['mobius', tr('Mobius (couleurs plus vives)')],
        ['reinhard', tr('Reinhard (plus doux)')], ['off', tr('Désactivée')]], s.tonemap, { onchange: (e) => save({ tonemap: e.target.value }, tr('Enregistré (prochaines lectures)')) }))),
    h('section.panel', h('h2', tr('Carte graphique')),
      h('p.muted.small', tr('La carte graphique décode et encode la vidéo à la place du processeur, ce qui permet beaucoup plus de transcodages simultanés. Un essai est effectué avant l’enregistrement ; si la carte refuse un fichier, le processeur prend le relais. L’accès d’un conteneur LXC à la carte est décrit dans le README.')),
      h('div.grid2', field(tr('Accélération matérielle'), hwEngine), hwDeviceField),
      hwSave),
  ];
});

const downloadsPage = (body, ctx) => withSettings(body, ctx, (s, save) => {
  const downloadsToggle = toggle(tr('Autoriser les téléchargements'), s.downloads);
  downloadsToggle.querySelector('input').addEventListener('change', (e) => save({ downloads: e.target.checked }, tr('Enregistré')));
  const prio = select([['low', tr('Discrète : le moins de ressources possible, en pause pendant les lectures')],
    ['normal', tr('Normale : pleine vitesse quand personne ne regarde (conseillé)')],
    ['high', tr('Rapide : tout le processeur, même pendant les lectures')]], s.downloads_priority || 'normal',
  { onchange: (e) => save({ downloads_priority: e.target.value }, tr('Enregistré')) });
  return h('section.panel', h('h2', tr('Visionnage hors ligne')),
    h('p.muted.small', tr('Les utilisateurs peuvent télécharger films et épisodes dans l’application pour les regarder sans connexion. Le serveur prépare une copie à la qualité choisie, que l’appareil récupère pendant la préparation, puis la supprime une fois récupérée.')),
    downloadsToggle,
    field(tr('Vitesse de préparation'), prio,
      tr('Normale : en priorité basse pendant une lecture, et une conversion attend si un spectateur a besoin du processeur ; deux préparations à la fois sur un serveur de 8 cœurs ou plus. Rapide : deux à la fois, sans jamais attendre (les lectures peuvent en pâtir sur un petit serveur).')));
});

// --------------------------------------------------------------- mise à jour
// La vérification interroge GitHub (résultat gardé 30 min côté serveur) ; le bouton force une nouvelle vérification.
async function updatePage(body) {
  // serveur lié choisi dans le sélecteur : sa version, pas celle de ce serveur
  const version = adminTarget() ? (await get('/api/admin/update')).current : state.status.version;
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
  const remote = adminTarget();
  if (remote) { toast(tr('Mise à jour lancée sur « {name} » : il redémarre dans une à deux minutes', { name: remote.name }), 'ok'); return; }
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
    cfg.shared_by ? tr('Pas de Seerr ici : les demandes passent par celui du serveur lié « {name} ».', { name: cfg.shared_by })
      : cfg.enabled ? tr('Seerr est configuré : les utilisateurs peuvent demander des films et des séries.') : tr('Seerr n’est pas encore configuré.'));
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
        cfg.enabled && !cfg.shared_by ? h('button.btn', { onclick: async () => {
          try { const t = await post('/api/admin/requests/test'); toast(tr('Connecté à {title} (serveur : {media_server})', { title: t.title, media_server: t.media_server }), 'ok'); }
          catch (e) { toast(e.message, 'bad'); }
        } }, tr('Tester')) : null,
        cfg.enabled && !cfg.shared_by ? h('button.btn.ghost', { onclick: async () => {
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
  const remote = adminTarget();
  const version = remote ? (await get('/api/admin/settings')).version : state.status.version;
  const latest = remote ? null : state.me.update;
  const versionCard = h('div.panel.scan', h('strong', `Nexis ${version}`), latest
    ? h('div.small', h('span.tag.accent', tr('Mise à jour')), tr(' Nexis {latest} est disponible · ', { latest }), h('a', { href: '#/admin/update' }, tr('Mettre à jour')))
    : h('div.muted.small', tr('À jour (vérification automatique toutes les 6 heures) · '), h('a', { href: '#/admin/update' }, tr('Détails'))));
  const alertsBox = h('div');
  const paintAlerts = (v) => {
    const c = v.conf;
    const sw = (k, label) => {
      const t = toggle(label, c[k]);
      t.querySelector('input').addEventListener('change', async (e) => {
        try { paintAlerts(await put('/api/admin/alerts', { [k]: e.target.checked })); } catch (ex) { toast(ex.message, 'bad'); }
      });
      return t;
    };
    const gbInput = h('input', { type: 'number', min: 1, max: 5000, value: c.disk_gb, onchange: async () => {
      try { paintAlerts(await put('/api/admin/alerts', { disk_gb: Number(gbInput.value) })); toast(tr('Enregistré'), 'ok'); } catch (ex) { toast(ex.message, 'bad'); }
    } });
    clear(alertsBox,
      v.alerts.length ? h('div.alert-list', v.alerts.map((a) => h('a.alert-row', { href: a.url },
        h('strong', a.title), h('div.small', a.body), h('div.muted.small', new Date(a.created_at * 1000).toLocaleString(locale(), { dateStyle: 'short', timeStyle: 'short' })))))
        : h('div.status-box.ok', tr('Aucune alerte : tout va bien.')),
      h('details.alert-conf', h('summary.small', tr('Alertes envoyées aux administrateurs')),
        h('p.muted.small', tr('Chaque alerte part en notification push sur les appareils où vous l’avez activée (Paramètres → Profil → Notifications), une fois, puis une fois par jour tant que le problème dure.')),
        sw('disk', tr('Disque presque plein (données de Nexis, sauvegardes)')),
        field(tr('Seuil d’espace libre (Go)'), gbInput),
        sw('folder', tr('Dossier de bibliothèque inaccessible (partage réseau démonté)')),
        sw('peer', tr('Serveur lié injoignable depuis 15 minutes')),
        sw('task', tr('Tâche planifiée en échec')),
        h('div.actions-row',
          h('button.btn.small', { onclick: async () => { try { paintAlerts(await post('/api/admin/alerts/check')); toast(tr('Vérification faite'), 'ok'); } catch (ex) { toast(ex.message, 'bad'); } } }, tr('Vérifier maintenant')),
          h('button.btn.small', { onclick: async () => {
            const r = await post('/api/admin/alerts/test');
            toast(r.sent ? tr('Alerte de test envoyée') : tr('Aucun appareil inscrit aux notifications pour ce compte'), r.sent ? 'ok' : 'bad');
          } }, tr('Envoyer une alerte de test')))));
  };
  const load = async () => {
    const [list, sys, alerts] = await Promise.all([get('/api/admin/streams'), get('/api/admin/system').catch(() => null),
      get('/api/admin/alerts').catch(() => null)]);
    if (!ctx.isCurrent()) return;
    if (alerts && !alertsBox.querySelector('details[open]')) paintAlerts(alerts);
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
    clear(body, versionCard, h('section.panel', h('h2', tr('Alertes')), alertsBox), server, security, h('h3.sub', tr('Lectures en cours')), list.length ? h('div.user-list', list.map((s) => h('div.user-row',
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
  const accounts = h('div');
  const err = h('p.form-error');
  let choices = null; // identifiants présents des deux côtés, une fois l'invitation vérifiée
  invite.addEventListener('input', () => { choices = null; clear(accounts); });
  const body = h('div.form',
    field(tr('Invitation créée sur l’autre serveur'), invite),
    field(tr('Adresse de ce serveur, vue depuis l’autre'), address,
      tr('Laissez vide si l’autre ne peut pas joindre ce serveur (box sans port ouvert) : ce serveur lui ouvrira lui-même un tunnel chiffré.')),
    accounts, err);
  const showChoices = (preview) => {
    choices = preview.conflicts.map((c) => {
      const rename = h('input', { value: c.suggested, maxlength: 32, 'aria-label': tr('Nouvel identifiant ici') });
      const same = select([['split', tr('Personnes différentes')], ['merge', tr('Même personne : fusionner')]], 'split');
      const renameRow = h('div.small', tr('Le compte d’ici devient : '), rename);
      same.addEventListener('change', () => { renameRow.hidden = same.value === 'merge'; });
      return { c, same, rename, row: h('div.peer-lib.account-pick',
        h('div.grow', h('strong', c.username),
          h('div.muted.small', tr('Ici : {local} · sur « {server} » : {remote}', { local: c.display_name, server: preview.server, remote: c.remote_display_name || c.username })),
          renameRow), same) };
    });
    clear(accounts, choices.length ? h('div.status-box',
      h('p', tr('Ces identifiants existent sur les deux serveurs. S’il s’agit de la même personne, les comptes fusionnent avec le mot de passe et les réglages de « {server} » ; sinon, le compte d’ici est renommé et chacun garde le sien.', { server: preview.server })),
      h('div.peer-libs', choices.map((x) => x.row))) : null);
  };
  return modal(tr('Rejoindre un serveur'), body, [
    { label: tr('Annuler'), value: null },
    { label: tr('Lier les serveurs'), primary: true, handler: async () => {
      err.textContent = tr('Connexion à l’autre serveur…');
      try {
        if (!choices) {
          const preview = await post('/api/admin/federation/join/preview', { invite: invite.value.trim() });
          showChoices(preview);
          if (choices.length) { err.textContent = ''; return false; } // l'administrateur vérifie, puis relance
        }
        const picked = {};
        for (const x of choices) picked[x.c.username] = x.same.value === 'merge' ? { merge: true } : { rename: x.rename.value.trim() };
        return await post('/api/admin/federation/join', { invite: invite.value.trim(), address: address.value.trim(), accounts: picked });
      } catch (e) { err.textContent = e.message; return false; }
    } },
  ]);
}

const shortPrint = (fp) => (fp ? fp.slice(0, 32).toUpperCase().match(/.{4}/g).join(' ') : '—');

// Accepter ou non d'être administré depuis un serveur lié (réglé sur place : jamais depuis un autre serveur)
function remoteAdminPanel() {
  if (adminTarget()) return null;
  const box = h('section.panel', h('h2', tr('Administration depuis les serveurs liés')));
  get('/api/admin/servers').then((sv) => {
    const t = toggle(tr('Les administrateurs peuvent régler ce serveur depuis un serveur lié'), sv.remote_admin);
    t.querySelector('input').addEventListener('change', async (e) => {
      try { await put('/api/admin/servers', { remote_admin: e.target.checked }); toast(tr('Enregistré'), 'ok'); } catch (ex) { toast(ex.message, 'bad'); }
    });
    box.append(h('p.muted.small', tr('Un administrateur connecté à un autre serveur de la liste choisit ce serveur en haut des pages d’administration et règle tout comme s’il était connecté ici, avec son propre compte (le même sur tous les serveurs). Chaque action est notée dans le journal de ce serveur. Ce réglage ne se change que sur place.')), t);
  }).catch(() => {});
  return box;
}

async function federationPage(body, ctx) {
  const render = (v) => {
    if (!ctx.isCurrent()) return;
    const name = h('input', { value: v.name, maxlength: 40 });
    const busy = h('input', { type: 'number', min: 0, max: 50, value: v.busy });
    const me = h('section.panel',
      h('h2', tr('Ce serveur')),
      h('div.grid2', field(tr('Nom affiché chez les serveurs liés'), name), h('div.field',
        h('span.field-label', tr('Empreinte du certificat')), h('code.small', shortPrint(v.fingerprint)),
        h('span.field-hint', v.listening ? tr('Port des serveurs liés : {port} (TLS)', { port: v.port }) : tr('Le port des serveurs liés s’ouvrira à la première invitation.')))),
      h('div.grid2', field(tr('Serveur occupé à partir de'), busy, tr('Lectures en cours ({n} en ce moment). Au-delà, un titre présent sur un serveur moins occupé est lu depuis celui-ci. 0 : toujours la meilleure copie.', { n: v.load })), h('div')),
      h('div.actions-row', h('button.btn', { onclick: async () => {
        try { render(await put('/api/admin/federation', { name: name.value.trim(), busy: Number(busy.value) })); toast(tr('Enregistré'), 'ok'); }
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
          p.online && p.load != null ? plural(p.load, ' · {n} lecture en cours', ' · {n} lectures en cours') : '',
          tr(' · vu {when}', { when: since(p.last_seen) })),
        p.online && p.shares && p.shares.length ? h('div.muted.small', tr('Prête : {list}', {
          list: p.shares.map((k) => (k === 'tmdb' ? tr('sa clé TMDB') : tr('son Seerr (demandes)'))).join(', ') })) : null,
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
    const notices = v.notices && v.notices.length ? h('section.panel.warn', h('h2', tr('Comptes renommés')),
      h('ul.small', v.notices.map((n) => h('li', tr('« {username} » a été créé sur deux serveurs avant leur synchronisation : ce sont deux comptes, l’un s’appelle maintenant « {renamed} ».', n)))),
      h('div.actions-row', h('button.btn.small', { onclick: async () => { render(await del('/api/admin/federation/notices')); } }, tr('Compris')))) : null;
    clear(body, notices,
      h('div.toolbar', h('div.actions-row',
          h('button.btn', { onclick: async () => { const r = await joinDialog(); if (r) { toast(tr('Serveurs liés'), 'ok'); await refreshMe(); render(r); } } }, tr('Rejoindre un serveur')),
          h('button.btn.primary', { onclick: async () => { await inviteDialog(v); load(); } }, tr('+ Inviter un serveur')))),
      peers, me, remoteAdminPanel(),
      h('section.panel', h('h2', tr('Comment ça marche')), h('ul.small.muted',
        h('li', tr('Les deux serveurs se reconnaissent par leur certificat, échangé à l’invitation, et chiffrent tout ce qu’ils s’envoient (TLS). Pas besoin de VPN ni de nom de domaine, et cela marche aussi en HTTP sur le réseau local.')),
        h('li', tr('Un seul des deux doit pouvoir joindre l’autre : celui qui ne l’est pas ouvre lui-même un tunnel vers lui. Rien à ouvrir sur sa box.')),
        h('li', tr('Les comptes, mots de passe, profils et accès aux bibliothèques sont les mêmes sur les deux serveurs ; une modification faite sur l’un arrive sur l’autre en moins d’une minute.')),
        h('li', tr('Un identifiant présent sur les deux serveurs au moment de les lier n’est fusionné que si vous indiquez qu’il s’agit de la même personne ; sinon, l’un des comptes est renommé.')),
        h('li', tr('Une bibliothèque reçue s’ajoute comme une nouvelle, ou se fusionne avec l’une des vôtres : un titre présent sur les deux serveurs n’apparaît qu’une fois, et les saisons et épisodes de chacun sont réunis.')),
        h('li', tr('À la lecture d’un titre présent sur les deux serveurs, la copie est choisie toute seule : celle qui a la langue voulue si une seule l’a, puis celle d’un serveur qui n’est pas occupé, puis la meilleure qualité. Si un serveur ne répond pas, l’autre prend le relais, et les titres qu’il est seul à avoir sont masqués jusqu’à son retour.')),
        h('li', tr('Un serveur sans clé TMDB ou sans Seerr se sert de ceux d’un serveur lié : métadonnées, onglet Découvrir et demandes.')),
        h('li', tr('« Regarder ensemble » marche d’un serveur à l’autre : les groupes créés sur l’un apparaissent sur l’autre, et chacun regarde depuis le serveur auquel il est connecté.')),
        h('li', tr('Une lecture d’un titre de l’autre serveur passe par celui-ci : c’est l’autre serveur qui lit le fichier et le convertit si besoin. Le débit montant de sa connexion limite la qualité à distance.')),
        h('li', tr('À réserver à vos propres serveurs : partager des films avec un autre foyer sort du cadre de la copie privée.')))));
  };
  const load = async () => { const v = await get('/api/admin/federation'); render(v); };
  await load();
  const t = setInterval(() => { if (!document.querySelector('.modal-overlay')) load(); }, 15000);
  ctx.setCleanup(() => clearInterval(t));
}

// ----------------------------------------------------------------- DLNA
async function dlnaPage(body, ctx) {
  const render = (v) => {
    if (!ctx.isCurrent()) return;
    const on = toggle(tr('Activer le serveur DLNA'), v.enabled);
    const name = h('input', { value: v.custom_name, maxlength: 60, placeholder: v.name });
    const libs = v.libraries.map((l) => ({ l, box: h('input', { type: 'checkbox', checked: l.shared }) }));
    const status = !v.enabled ? h('div.status-box', tr('Le serveur DLNA est désactivé.'))
      : v.error ? h('div.status-box.bad', v.error)
        : h('div.status-box.ok', tr('Nexis est visible sur le réseau local sous le nom « {name} ».', { name: v.name }));
    clear(body,
      h('section.panel', h('h2', tr('Serveur DLNA')),
        h('p.muted.small', tr('Les téléviseurs, consoles et box du réseau local trouvent Nexis dans leur liste de sources (« Serveurs multimédia », « DLNA », « Partage »). Ils lisent les fichiers tels quels : sans conversion, un format que l’appareil ne connaît pas ne se lit pas (préférez alors l’application Nexis).')),
        status, on,
        field(tr('Nom affiché sur les appareils'), name),
        field(tr('Bibliothèques partagées'), h('div.check-list', libs.map(({ l, box }) => h('label.check-row', box, h('span', l.name)))),
          tr('Le DLNA ne connaît pas les comptes : tout appareil du réseau local voit ces bibliothèques, sans contrôle parental.')),
        h('div.actions-row', h('button.btn.primary', { onclick: async () => {
          try {
            render(await put('/api/admin/dlna', { enabled: on.querySelector('input').checked, name: name.value.trim(),
              libraries: libs.filter((x) => x.box.checked).map((x) => x.l.id) }));
            toast(tr('Enregistré'), 'ok');
          } catch (e) { toast(e.message, 'bad'); }
        } }, tr('Enregistrer')))),
      h('section.panel', h('h2', tr('Bon à savoir')), h('ul.small.muted',
        h('li', tr('Réservé au réseau local : les appareils doivent être sur le même réseau que le serveur, et les adresses extérieures sont refusées.')),
        h('li', tr('Avec Docker, le conteneur doit utiliser le réseau de l’hôte (network_mode: host) : l’annonce DLNA ne traverse pas le réseau interne de Docker.')),
        h('li', tr('Les titres d’un serveur lié ne sont pas partagés en DLNA, seulement ceux de ce serveur.')),
        v.address ? h('li', tr('Adresse de description : {address}', { address: v.address })) : null)));
  };
  render(await get('/api/admin/dlna'));
}

// --------------------------------------------------------------- sauvegardes
async function backupsPage(body, ctx) {
  const size = (b) => (b >= 1e9 ? tr('{n} Go', { n: (b / 1e9).toLocaleString(locale(), { maximumFractionDigits: 1 }) })
    : b >= 1e6 ? tr('{n} Mo', { n: (b / 1e6).toLocaleString(locale(), { maximumFractionDigits: 1 }) })
      : tr('{n} Ko', { n: Math.max(1, Math.round(b / 1e3)) }));
  const when = (ts) => new Date(ts * 1000).toLocaleString(locale(), { dateStyle: 'medium', timeStyle: 'short' });
  const reload = async () => render(await get('/api/admin/backups'));
  // après une restauration : Nexis redémarre, la page se recharge une fois revenu
  const restarted = (r) => {
    if (!r.restarting) { toast(tr('Restauration prête : redémarrez Nexis pour l’appliquer'), 'ok'); return; }
    clear(body, h('section.panel', h('h2', tr('Restauration en cours')), h('p.muted', tr('Nexis redémarre avec la sauvegarde. La page se rechargera toute seule.'))));
    const started = Date.now();
    const wait = async () => {
      await new Promise((res) => setTimeout(res, 2500));
      try { await get('/api/status'); location.reload(); } catch { if (Date.now() - started < 180000) wait(); }
    };
    wait();
  };
  const askPassphrase = async (title) => {
    const input = h('input', { type: 'password', autocomplete: 'off' });
    const ok = await modal(title, h('div.form', h('p.muted.small', tr('Cette archive est chiffrée : indiquez la phrase secrète choisie au moment de la sauvegarde (laisser vide pour essayer celle enregistrée ici).')),
      field(tr('Phrase secrète'), input)), [{ label: tr('Annuler'), value: false }, { label: tr('Restaurer'), primary: true, value: true }]);
    return ok ? input.value : null;
  };
  const confirmRestore = (b) => confirmDialog(tr('Restaurer cette sauvegarde ?'),
    `${b && b.server && !b.mine ? tr('Elle vient du serveur « {name} » : ce serveur prendra son identité (comptes, réglages, liaisons). À faire seulement pour remplacer « {name} », par exemple sur une nouvelle machine.', { name: b.server }) + ' ' : ''}${
      tr('Comptes, historique, réglages et bibliothèques reviennent à leur état de la sauvegarde. Ce qui a changé depuis est perdu (l’état actuel reste gardé dans le dossier de données, sous « before-restore »). Nexis redémarre.')}`, tr('Restaurer'));

  const render = (v) => {
    if (!ctx.isCurrent()) return;
    const sched = v.schedule || { mode: 'off' };
    const mode = select([['off', tr('Désactivée')], ['daily', tr('Chaque jour')], ['weekly', tr('Chaque semaine (dimanche)')]],
      sched.mode === 'interval' ? 'daily' : sched.mode);
    const time = h('input', { type: 'time', value: sched.time || '04:30' });
    const keep = h('input', { type: 'number', min: 1, max: 90, value: v.keep });
    const dir = h('input', { value: v.custom_dir ? v.dir : '', placeholder: v.default_dir, spellcheck: false });
    const saveConf = async (extra = {}) => {
      const schedule = mode.value === 'off' ? { mode: 'off' } : { mode: mode.value, time: time.value || '04:30', ...(mode.value === 'weekly' ? { weekday: 6 } : {}) };
      try { render(await put('/api/admin/backups/settings', { schedule, keep: Number(keep.value), dir: dir.value.trim(), ...extra })); toast(tr('Enregistré'), 'ok'); } catch (e) { toast(e.message, 'bad'); }
    };
    const setOnly = async (patch, msg = tr('Enregistré')) => {
      try { render(await put('/api/admin/backups/settings', patch)); toast(msg, 'ok'); } catch (e) { toast(e.message, 'bad'); }
    };
    const nowBtn = h('button.btn.primary', { onclick: async () => {
      nowBtn.disabled = true;
      nowBtn.textContent = tr('Sauvegarde en cours…');
      try { render(await post('/api/admin/backups')); toast(tr('Sauvegarde terminée : copies en cours vers les autres destinations'), 'ok'); } catch (e) { toast(e.message, 'bad'); nowBtn.disabled = false; }
    } }, tr('Sauvegarder maintenant'));
    // envoyer une archive (pour la garder), ou restaurer directement depuis un fichier de l'appareil
    const pick = (restore) => {
      const input = h('input', { type: 'file', accept: '.gz,.enc,application/gzip,application/octet-stream', hidden: true, onchange: async () => {
        const f = input.files[0];
        if (!f) return;
        let pass = '';
        if (restore) {
          if (!await confirmRestore(null)) return;
          if (/\.enc$/i.test(f.name)) { pass = await askPassphrase(tr('Archive chiffrée')); if (pass === null) return; }
        }
        toast(tr('Envoi de la sauvegarde…'));
        try {
          const headers = { 'X-Seance': '1', 'Content-Type': 'application/octet-stream' };
          if (pass) headers['X-Backup-Passphrase'] = pass;
          const r = await fetch(adminUrl(restore ? '/api/admin/backups/restore-file' : '/api/admin/backups/upload'), { method: 'POST', body: f, credentials: 'same-origin', headers });
          const j = await r.json();
          if (!r.ok) throw new Error(j.error || r.statusText);
          if (restore) { restarted(j); return; }
          render(j);
          toast(tr('Sauvegarde ajoutée : vous pouvez la restaurer'), 'ok');
        } catch (e) { toast(e.message, 'bad'); }
      } });
      document.body.append(input);
      input.click();
      setTimeout(() => input.remove(), 60000);
    };
    const restorePath = async () => {
      const path = h('input', { placeholder: '/backups/nexis-salon_20261107-043000-auto.tar.gz', spellcheck: false });
      const pass = h('input', { type: 'password', autocomplete: 'off', placeholder: tr('Seulement pour une archive chiffrée (.enc)') });
      const ok = await modal(tr('Restaurer depuis un fichier du serveur'), h('div.form',
        h('p.muted.small', tr('Un fichier déjà présent sur le serveur : disque externe, NAS monté, dossier partagé avec le conteneur…')),
        field(tr('Chemin du fichier'), path), field(tr('Phrase secrète'), pass)),
      [{ label: tr('Annuler'), value: false }, { label: tr('Restaurer'), primary: true, value: true }]);
      if (!ok || !path.value.trim()) return;
      if (!await confirmRestore(null)) return;
      try { restarted(await post('/api/admin/backups/restore-path', { path: path.value.trim(), passphrase: pass.value || undefined })); } catch (e) { toast(e.message, 'bad'); }
    };
    const last = v.last;
    const row = (b) => h('div.backup-row',
      h('div.backup-info', h('strong', when(b.created)),
        h('div.muted.small', [b.version ? `Nexis ${b.version}` : null, size(b.size),
          b.uploaded ? tr('envoyée') : b.name.includes('-restauree') ? tr('restaurée') : b.auto ? tr('automatique') : tr('manuelle')].filter(Boolean).join(' · '),
        b.broken ? h('span.bad', ` · ${tr('illisible')}`) : null), h('code.backup-name', b.name)),
      h('div.actions-row',
        h('a.btn.small', { href: adminUrl(`/api/admin/backups/${b.name}`), download: b.name }, icon('download'), tr('Télécharger')),
        h('button.btn.small', { disabled: !!b.broken, onclick: async () => {
          if (!await confirmRestore(b)) return;
          try { restarted(await post(`/api/admin/backups/${b.name}/restore`)); } catch (e) { toast(e.message, 'bad'); }
        } }, tr('Restaurer')),
        h('button.icon-btn', { title: tr('Supprimer'), 'aria-label': tr('Supprimer'), onclick: async () => {
          if (!await confirmDialog(tr('Supprimer cette sauvegarde ?'), when(b.created), tr('Supprimer'))) return;
          try { render(await del(`/api/admin/backups/${b.name}`)); } catch (e) { toast(e.message, 'bad'); }
        } }, icon('trash'))));
    // sauvegardes de ce serveur, puis copies de chaque serveur lié (rangées par serveur d'origine)
    const mine = v.backups.filter((b) => b.mine);
    const others = {};
    for (const b of v.backups.filter((x) => !x.mine)) (others[b.server || b.slug || '?'] ||= []).push(b);
    const copyLine = (st) => (st ? h('span', { class: st.ok ? 'muted' : 'bad' }, st.ok ? tr('dernière copie {date}', { date: when(st.at) }) : tr('échec le {date} : {error}', { date: when(st.at), error: st.error || '?' })) : h('span.muted', tr('pas encore de copie')));
    clear(body,
      v.restored ? h('div.status-box.ok', tr('Sauvegarde du {date} restaurée (Nexis {version}).', { date: when(v.restored.created), version: v.restored.version || '?' })) : null,
      h('section.panel', h('h2', tr('Sauvegarde automatique')),
        h('p.muted.small', tr('L’archive contient la base (comptes, historique, notes, playlists, réglages, bibliothèques et métadonnées), les clés des serveurs liés et des notifications, les images de profil et les sous-titres téléchargés. Les vidéos et les photos n’y sont pas. Elle contient les mots de passe (chiffrés) : gardez-la en lieu sûr.')),
        h('div.grid2', field(tr('Fréquence'), mode), field(tr('Heure'), time)),
        h('div.grid2', field(tr('Sauvegardes automatiques gardées'), keep, tr('Pour ce serveur, et pour chaque serveur lié dont il garde les copies.')),
          field(tr('Dossier des sauvegardes'), dir, tr('De préférence sur un autre disque ou un NAS (dossier monté dans le conteneur avec Docker). Vide : dans le dossier de données.'))),
        h('p.muted.small', [v.free != null ? tr('Espace libre : {n}', { n: size(v.free) }) : null,
          last ? tr('Dernière sauvegarde automatique : {date} ({status})', { date: when(last.start), status: last.status === 'ok' ? tr('réussie') : last.summary || tr('échec') }) : null].filter(Boolean).join(' · ')),
        h('button.btn', { onclick: () => saveConf() }, tr('Enregistrer'))),
      v.peers.length ? (() => {
        const to = toggle(tr('Recopier chaque sauvegarde de ce serveur sur les serveurs liés'), v.to_peers);
        to.querySelector('input').addEventListener('change', (e) => setOnly({ to_peers: e.target.checked }));
        const from = toggle(tr('Garder ici les sauvegardes envoyées par les serveurs liés'), v.from_peers);
        from.querySelector('input').addEventListener('change', (e) => setOnly({ from_peers: e.target.checked }));
        return h('section.panel', h('h2', tr('Recopie entre les serveurs liés')),
          h('p.muted.small', tr('Chaque serveur garde aussi les sauvegardes des autres : si l’un tombe en panne, sa dernière sauvegarde est sur un autre. Les noms commencent par le nom du serveur d’origine (nexis-<serveur>_date), et chaque serveur garde celles des autres à part.')),
          to, from,
          h('ul.small.copy-status', v.peers.map((p) => h('li', h('strong', p.name), ' · ', copyLine(p.last)))));
      })() : null,
      remotesPanel(v),
      h('section.panel', h('h2', tr('Chiffrement des copies hors du serveur')),
        h('p.muted.small', tr('Avec une phrase secrète, les copies envoyées sur S3, Google Drive ou OneDrive sont chiffrées (AES-256) : le service ne peut pas les lire. Notez la phrase ailleurs : sans elle, ces copies ne se restaurent pas. Les copies entre vos serveurs liés et celles du dossier ne sont pas chiffrées.')),
        h('div.status-box', { class: v.passphrase_set ? 'status-box ok' : 'status-box' }, v.passphrase_set ? tr('Phrase secrète enregistrée : les copies externes sont chiffrées.') : tr('Pas de phrase secrète : les copies externes ne sont pas chiffrées.')),
        (() => {
          const pw = h('input', { type: 'password', autocomplete: 'new-password', placeholder: tr('10 caractères au moins') });
          return h('div.actions-row', pw,
            h('button.btn', { onclick: () => { if (pw.value) setOnly({ passphrase: pw.value }, tr('Phrase secrète enregistrée')); } }, v.passphrase_set ? tr('Changer') : tr('Enregistrer')),
            v.passphrase_set ? h('button.btn', { onclick: () => setOnly({ clear_passphrase: true }, tr('Chiffrement désactivé')) }, tr('Retirer')) : null);
        })()),
      h('section.panel', h('h2', tr('Sauvegardes de ce serveur')),
        h('div.actions-row', nowBtn, h('button.btn', { onclick: () => pick(true) }, tr('Restaurer depuis un fichier…')),
          h('button.btn', { onclick: restorePath }, tr('Restaurer depuis un fichier du serveur…')),
          h('button.btn', { onclick: () => pick(false) }, tr('Ajouter un fichier à la liste…'))),
        h('p.muted.small', tr('Pour passer sur un nouveau serveur, ou remplacer un serveur en panne : installez Nexis, créez le compte administrateur, puis « Restaurer depuis un fichier… » avec sa sauvegarde (téléchargée depuis un autre serveur lié ou un stockage externe).')),
        mine.length ? h('div.backup-list', mine.map(row)) : h('p.muted', tr('Aucune sauvegarde pour l’instant.'))),
      ...Object.entries(others).map(([server, list]) => h('section.panel', h('h2', tr('Copies de « {name} »', { name: server })),
        h('p.muted.small', tr('Sauvegardes reçues de ce serveur lié. Pour le reconstruire, téléchargez la plus récente et restaurez-la sur sa nouvelle installation.')),
        h('div.backup-list', list.map(row)))));
  };

  // stockages externes : S3, Google Drive, OneDrive
  function remotesPanel() {
    const box = h('section.panel', h('h2', tr('Stockages externes')), spinner());
    const kinds = { s3: 'S3', gdrive: 'Google Drive', onedrive: 'OneDrive' };
    const load = async () => {
      let r;
      try { r = await get('/api/admin/backups/remotes'); } catch (e) { clear(box, h('h2', tr('Stockages externes')), h('p.muted', e.message)); return; }
      const card = (s) => {
        const files = h('div');
        const on = toggle(tr('Envoyer chaque sauvegarde'), s.enabled);
        on.querySelector('input').addEventListener('change', async (e) => { await put(`/api/admin/backups/remotes/${s.id}`, { enabled: e.target.checked }); load(); });
        const showFiles = async () => {
          files.replaceChildren(spinner());
          try {
            const f = await get(`/api/admin/backups/remotes/${s.id}/files`);
            files.replaceChildren(f.files.length ? h('div.backup-list', f.files.map((x) => h('div.backup-row',
              h('div.backup-info', h('strong', x.name), h('div.muted.small', [size(x.size), x.encrypted ? tr('chiffrée') : null, x.server].filter(Boolean).join(' · '))),
              h('button.btn.small', { onclick: async () => {
                if (!await confirmRestore(null)) return;
                let pass = '';
                if (x.encrypted) { pass = await askPassphrase(tr('Archive chiffrée')); if (pass === null) return; }
                toast(tr('Téléchargement de la sauvegarde…'));
                try { restarted(await post(`/api/admin/backups/remotes/${s.id}/restore`, { id: x.id, passphrase: pass || undefined })); } catch (e) { toast(e.message, 'bad'); }
              } }, tr('Restaurer'))))) : h('p.muted.small', tr('Aucune sauvegarde sur ce stockage.')));
          } catch (e) { files.replaceChildren(h('p.form-error.small', e.message)); }
        };
        const link = async () => {
          try {
            const c = await post(`/api/admin/backups/remotes/${s.id}/link`);
            const status = h('p.muted.small', tr('Cette page se met à jour toute seule une fois l’accès accordé.'));
            files.replaceChildren(h('p', tr('Sur {url}, saisissez ce code :', { url: c.verification_url })), h('div.trakt-code', c.user_code),
              h('div.actions-row', h('a.btn.primary', { href: c.verification_url, target: '_blank', rel: 'noopener' }, tr('Ouvrir la page'))), status);
            const poll = async () => {
              const p = await post(`/api/admin/backups/remotes/${s.id}/poll`);
              if (p.status === 'pending') { setTimeout(poll, (c.interval || 5) * 1000); return; }
              toast(p.status === 'linked' ? tr('Compte relié') : tr('Liaison annulée ou expirée : recommencez'), p.status === 'linked' ? 'ok' : 'bad');
              load();
            };
            setTimeout(poll, (c.interval || 5) * 1000);
          } catch (e) { toast(e.message, 'bad'); }
        };
        return h('div.remote-card',
          h('div.remote-head', h('strong', s.name), h('span.os-badge', kinds[s.kind]),
            s.kind === 's3' ? h('span.muted.small', `${s.conf.bucket}${s.conf.prefix ? `/${s.conf.prefix}` : ''}`) : null,
            !s.linked ? h('span.os-badge', tr('à relier')) : null),
          h('p.small', s.last ? h('span', { class: s.last.ok ? 'muted' : 'bad' }, s.last.ok ? tr('dernière copie {date}', { date: when(s.last.at) }) : tr('échec le {date} : {error}', { date: when(s.last.at), error: s.last.error || '?' })) : h('span.muted', tr('pas encore de copie'))),
          on,
          h('div.actions-row',
            !s.linked ? h('button.btn.primary', { onclick: link }, tr('Relier le compte')) : null,
            s.linked ? h('button.btn.small', { onclick: async (e) => {
              e.target.disabled = true;
              try { await post(`/api/admin/backups/remotes/${s.id}/push`); toast(tr('Sauvegarde envoyée'), 'ok'); load(); } catch (ex) { toast(ex.message, 'bad'); e.target.disabled = false; }
            } }, tr('Envoyer maintenant')) : null,
            s.linked ? h('button.btn.small', { onclick: showFiles }, tr('Voir les sauvegardes')) : null,
            h('button.btn.small', { onclick: () => remoteForm(s.kind, s).then((ok) => ok && load()) }, tr('Modifier')),
            h('button.icon-btn', { title: tr('Supprimer'), onclick: async () => {
              if (!await confirmDialog(tr('Retirer ce stockage ?'), tr('Les sauvegardes déjà envoyées y restent.'), tr('Retirer'))) return;
              await del(`/api/admin/backups/remotes/${s.id}`);
              load();
            } }, icon('trash'))),
          files);
      };
      clear(box, h('h2', tr('Stockages externes')),
        h('p.muted.small', tr('Chaque sauvegarde est aussi envoyée sur ces stockages, hors de chez vous : un compartiment S3 (AWS, Backblaze B2, Wasabi, Scaleway, OVH, MinIO…), Google Drive ou OneDrive. Chacun garde le nombre de copies automatiques choisi.')),
        r.remotes.length ? h('div.remote-list', r.remotes.map(card)) : null,
        h('div.actions-row', Object.entries(kinds).map(([k, label]) => h('button.btn', { onclick: () => remoteForm(k).then((ok) => ok && load()) }, `+ ${label}`))));
    };
    load();
    return box;
  }

  async function remoteForm(kind, existing = null) {
    const c = (existing && existing.conf) || {};
    const has = (k) => (c.secrets_set || []).includes(k);
    const inp = (k, opts = {}) => h('input', { value: c[k] || opts.value || '', spellcheck: false, autocomplete: 'off', ...opts, type: opts.type || 'text' });
    const name = h('input', { value: existing ? existing.name : '', placeholder: { s3: 'S3', gdrive: 'Google Drive', onedrive: 'OneDrive' }[kind] });
    const keepIn = h('input', { type: 'number', min: 1, max: 90, value: existing ? existing.keep : 7 });
    let fields, help;
    if (kind === 's3') {
      fields = { endpoint: inp('endpoint', { placeholder: 'https://s3.eu-west-3.amazonaws.com' }), region: inp('region', { placeholder: 'eu-west-3' }),
        bucket: inp('bucket'), prefix: inp('prefix', { value: existing ? '' : 'nexis' }), access_key: inp('access_key'),
        secret_key: inp('secret_key', { type: 'password', value: '', placeholder: has('secret_key') ? tr('Enregistrée (laisser vide pour la garder)') : '' }) };
      help = tr('Adresse du service (vide pour AWS), région, compartiment existant, et une clé d’accès qui peut y lire, écrire et supprimer. Exemples d’adresse : https://s3.eu-central-003.backblazeb2.com, https://s3.fr-par.scw.cloud, https://s3.gra.io.cloud.ovh.net.');
    } else if (kind === 'gdrive') {
      fields = { client_id: inp('client_id'), client_secret: inp('client_secret', { type: 'password', value: '', placeholder: has('client_secret') ? tr('Enregistré (laisser vide pour le garder)') : '' }),
        folder: inp('folder', { value: existing ? '' : 'Nexis' }) };
      help = tr('Une fois : console.cloud.google.com → nouveau projet → activer « Google Drive API » → Identifiants → ID client OAuth de type « TV et appareils à saisie limitée » (écran de consentement en mode test, avec votre adresse comme testeur). Copiez ici l’ID client et le code secret, puis reliez le compte avec le code affiché. Nexis ne voit que les fichiers qu’il a créés.');
    } else {
      fields = { client_id: inp('client_id', { placeholder: '00000000-0000-0000-0000-000000000000' }) };
      help = tr('Une fois : portal.azure.com → Inscriptions d’applications → Nouvelle inscription (comptes personnels Microsoft et professionnels), puis dans « Authentification » : « Autoriser les flux clients publics » = Oui. Copiez ici l’ID d’application (client), puis reliez le compte avec le code affiché. Nexis n’écrit que dans son dossier d’application (Applications/Nexis).');
    }
    const labels = { endpoint: tr('Adresse du service'), region: tr('Région'), bucket: tr('Compartiment (bucket)'), prefix: tr('Dossier dans le compartiment'),
      access_key: tr('Clé d’accès'), secret_key: tr('Clé secrète'), client_id: tr('ID client'), client_secret: tr('Code secret du client'), folder: tr('Dossier dans Google Drive') };
    const err = h('p.form-error');
    return modal(existing ? tr('Modifier {name}', { name: existing.name }) : { s3: tr('Ajouter un stockage S3'), gdrive: tr('Ajouter Google Drive'), onedrive: tr('Ajouter OneDrive') }[kind],
      h('div.form', h('p.muted.small', help), field(tr('Nom'), name),
        h('div.grid2', ...Object.entries(fields).map(([k, el]) => field(labels[k], el))),
        field(tr('Copies automatiques gardées'), keepIn), err),
      [{ label: tr('Annuler'), value: false }, { label: existing ? tr('Enregistrer') : tr('Ajouter'), primary: true, handler: async () => {
        const conf = Object.fromEntries(Object.entries(fields).map(([k, el]) => [k, el.value.trim()]));
        try {
          if (existing) await put(`/api/admin/backups/remotes/${existing.id}`, { name: name.value.trim(), keep: Number(keepIn.value), conf });
          else await post('/api/admin/backups/remotes', { kind, name: name.value.trim(), keep: Number(keepIn.value), conf });
          return true;
        } catch (e) { err.textContent = e.message; return false; }
      } }]);
  }

  await reload();
}

// --------------------------------------------------------------- statistiques
async function statsPage(body, ctx) {
  const GB = 1e9;
  const size = (b) => (b >= GB ? tr('{n} Go', { n: (b / GB).toLocaleString(locale(), { maximumFractionDigits: 1 }) }) : tr('{n} Mo', { n: Math.max(0, Math.round(b / 1e6)) }));
  const hours = (s) => (s >= 3600 ? tr('{n} h', { n: (s / 3600).toLocaleString(locale(), { maximumFractionDigits: 1 }) }) : tr('{n} min', { n: Math.round(s / 60) }));
  const when = (ts) => new Date(ts * 1000).toLocaleDateString(locale(), { day: 'numeric', month: 'short' });
  const days = select([['7', tr('7 derniers jours')], ['30', tr('30 derniers jours')], ['90', tr('3 derniers mois')], ['365', tr('12 derniers mois')]],
    store('stats-days') || '30', { onchange: () => { store('stats-days', days.value); load(); } });
  const out = h('div', spinner());
  clear(body, h('div.actions-row.stats-head', days), out);
  async function load() {
    clear(out, spinner());
    let r;
    try { r = await get(`/api/admin/stats?days=${days.value}`); } catch (e) { clear(out, h('p.form-error', e.message)); return; }
    if (!ctx.isCurrent()) return;
    const t = r.totals;
    const tile = (label, value, sub) => h('div.stat-tile', h('div.stat-label', label), h('div.stat-value', value), sub ? h('div.stat-sub', sub) : null);
    const tModes = r.modes;
    const pct = (n) => (t.plays ? Math.round((n / t.plays) * 100) : 0);
    // une seule série (heures par jour) : barres, valeur au survol, titre qui la nomme
    const max = Math.max(1, ...r.per_day.map((d) => d.seconds));
    const chart = h('div.stat-chart', { role: 'img', 'aria-label': tr('Heures de visionnage par jour') },
      r.per_day.map((d) => h('div.stat-col', { title: `${new Date(d.day).toLocaleDateString(locale(), { weekday: 'short', day: 'numeric', month: 'short' })} : ${hours(d.seconds)}` },
        h('span', { style: { height: `${d.seconds ? Math.max(3, (d.seconds / max) * 100) : 0}%` } }))));
    const table = (head, rows) => h('div.table-wrap', h('table.stat-table', h('thead', h('tr', head.map((x) => h('th', x)))), h('tbody', rows)));
    clear(out,
      h('div.stat-tiles',
        tile(tr('Temps de visionnage'), hours(t.seconds), plural(t.plays, '{n} lecture', '{n} lectures')),
        tile(tr('Données envoyées (estimation)'), size(t.bytes), tr('débit de la vidéo et du son')),
        tile(tr('Comptes actifs'), String(t.users)),
        tile(tr('Conversions'), `${pct(t.transcodes)} %`, tr('des lectures demandent une conversion par le processeur ou la carte graphique'))),
      h('section.panel', h('h2', tr('Heures de visionnage par jour')),
        t.plays ? chart : h('p.muted.small', tr('Aucune lecture sur cette période. Les statistiques commencent avec cette version de Nexis.')),
        t.plays ? h('div.stat-axis.muted.small', h('span', when(Date.parse(r.per_day[0].day) / 1000)), h('span', when(Date.parse(r.per_day[r.per_day.length - 1].day) / 1000))) : null),
      h('section.panel', h('h2', tr('Par compte')),
        r.users.length ? table([tr('Compte'), tr('Temps'), tr('Lectures'), tr('Titres'), tr('Données'), tr('Conversions'), tr('Dernière lecture')],
          r.users.map((u) => h('tr', h('td', h('strong', u.name)), h('td', hours(u.seconds)), h('td', String(u.plays)), h('td', String(u.titles)),
            h('td', size(u.bytes)), h('td', `${u.plays ? Math.round((u.transcodes / u.plays) * 100) : 0} %`), h('td', when(u.last)))))
          : h('p.muted.small', tr('Aucune lecture sur cette période.')),
        Object.keys(tModes).length ? h('p.muted.small', tr('Lecture directe {a} % · copie (remux) {b} % · conversion {c} %',
          { a: pct((tModes.direct || {}).plays || 0), b: pct((tModes.copy || {}).plays || 0), c: pct((tModes.transcode || {}).plays || 0) })) : null),
      h('section.panel', h('h2', tr('Les plus regardés')),
        r.top.length ? table([tr('Titre'), tr('Temps'), tr('Lectures'), tr('Spectateurs')],
          r.top.map((x) => h('tr', h('td', h('a', { href: `#/item/${x.id}` }, x.title), x.year ? h('span.muted', ` (${x.year})`) : null, x.show ? h('span.muted.small', ` · ${tr('série')}`) : null),
            h('td', hours(x.seconds)), h('td', String(x.plays)), h('td', String(x.viewers)))))
          : h('p.muted.small', tr('Aucune lecture sur cette période.'))),
      h('section.panel', h('h2', tr('Jamais regardés')),
        h('p.muted.small', tr('Films et séries de ce serveur que personne n’a commencés, ajoutés il y a plus de {d} jours : {n} titre(s), {size} au total. Les plus gros d’abord.',
          { d: r.never.after_days, n: r.never.count, size: size(r.never.size) })),
        r.never.items.length ? table([tr('Titre'), tr('Bibliothèque'), tr('Ajouté le'), tr('Taille')],
          r.never.items.map((x) => h('tr', h('td', h('a', { href: `#/item/${x.id}` }, x.title), x.year ? h('span.muted', ` (${x.year})`) : null),
            h('td', x.library), h('td', when(x.added_at)), h('td', size(x.size))))) : null));
  }
  await load();
}

// --------------------------------------------------------------- stockage froid
const COLD_KINDS = [
  ['local', tr('Dossier de cette machine'), tr('Disque USB, NAS déjà monté par le système, volume Docker…')],
  ['smb', tr('Partage SMB (NAS)'), tr('Monté en écriture par Nexis (installation par script ou paquet).')],
  ['sftp', tr('SFTP'), tr('Hetzner Storage Box, NAS, serveur SSH.')],
  ['webdav', tr('WebDAV'), tr('Nextcloud, Synology, Hetzner Storage Box…')],
  ['s3', tr('S3 et compatibles'), tr('Backblaze B2, Wasabi, Scaleway, OVH, AWS, MinIO.')],
  ['rclone', tr('rclone (autres services)'), tr('Un « remote » déjà configuré avec rclone config : pCloud, Mega, Google Drive…')],
  ['peer', tr('Serveur lié'), tr('Un serveur de la fédération qui réserve de la place pour vos archives (réglage de son administrateur).')],
];
const coldSize = (b) => (b >= 1e12 ? tr('{n} To', { n: (b / 1e12).toLocaleString(locale(), { maximumFractionDigits: 2 }) })
  : b >= 1e9 ? tr('{n} Go', { n: (b / 1e9).toLocaleString(locale(), { maximumFractionDigits: 1 }) }) : tr('{n} Mo', { n: Math.max(0, Math.round(b / 1e6)) }));

function coldSummary(s) {
  const c = s.conf;
  if (s.kind === 'local') return c.path;
  if (s.kind === 'smb') return `//${c.host}/${c.share}${c.subdir ? `/${c.subdir}` : ''}`;
  if (s.kind === 'sftp') return `${c.username}@${c.host}:${c.port}/${c.path}`;
  if (s.kind === 'webdav') return c.url;
  if (s.kind === 's3') return `${c.bucket}/${c.prefix || ''} · ${c.endpoint || 'AWS'}`;
  if (s.kind === 'peer') return tr('serveur lié n° {n}', { n: c.peer });
  return c.remote;
}

async function coldForm(existing, available, peers = []) {
  const kind = select(COLD_KINDS.map(([k, l]) => [k, l + (available[k] ? ` — ${tr('indisponible ici')}` : '')]), existing ? existing.kind : 'local',
    { disabled: !!existing });
  const c = existing ? existing.conf : {};
  const keep = existing ? tr('Laisser vide pour garder l’actuel') : '';
  const inputs = {};
  const inp = (k, attrs = {}) => { inputs[k] = h('input', { value: c[k] ?? attrs.value ?? '', autocomplete: 'off', ...attrs }); return inputs[k]; };
  const name = h('input', { value: existing ? existing.name : '', placeholder: tr('ex. Disque USB, Storage Box') });
  const fieldsFor = (k) => {
    if (k === 'local') return [field(tr('Dossier'), inp('path', { placeholder: '/mnt/archive' }), tr('Chemin sur cette machine (dans Docker : un volume du conteneur). Nexis doit pouvoir y écrire.'))];
    if (k === 'smb') return [h('div.grid2', field(tr('Adresse du NAS'), inp('host', { placeholder: '192.168.1.5' })), field(tr('Partage'), inp('share', { placeholder: 'archive' }))),
      h('div.grid2', field(tr('Sous-dossier'), inp('subdir', { placeholder: tr('facultatif') })), field(tr('Version SMB'), (inputs.version = select(SMB_VERSIONS, c.version || 'auto')))),
      h('div.grid2', field(tr('Identifiant'), inp('username')), field(tr('Mot de passe'), inp('password', { type: 'password', value: '', placeholder: keep })))];
    if (k === 'sftp') return [h('div.grid2', field(tr('Serveur'), inp('host', { placeholder: 'u123456.your-storagebox.de' })), field(tr('Port'), inp('port', { type: 'number', value: 22, min: 1, max: 65535 }))),
      h('div.grid2', field(tr('Identifiant'), inp('username')), field(tr('Mot de passe'), inp('password', { type: 'password', value: '', placeholder: keep }))),
      field(tr('Dossier'), inp('path', { placeholder: 'nexis' }), tr('Relatif au dossier personnel, ou absolu (/data/nexis). Créé au besoin.'))];
    if (k === 'webdav') return [field(tr('Adresse WebDAV'), inp('url', { placeholder: 'https://cloud.exemple.fr/remote.php/dav/files/moi/Nexis' })),
      h('div.grid2', field(tr('Identifiant'), inp('username')), field(tr('Mot de passe'), inp('password', { type: 'password', value: '', placeholder: keep })))];
    if (k === 's3') return [field(tr('Adresse (vide pour AWS)'), inp('endpoint', { placeholder: 'https://s3.eu-central-003.backblazeb2.com' })),
      h('div.grid2', field(tr('Région'), inp('region', { placeholder: 'eu-central-003' })), field(tr('Seau (bucket)'), inp('bucket'))),
      h('div.grid2', field(tr('Clé d’accès'), inp('access_key')), field(tr('Clé secrète'), inp('secret_key', { type: 'password', value: '', placeholder: keep }))),
      field(tr('Préfixe'), inp('prefix', { value: 'nexis-archive' }))];
    if (k === 'peer') {
      inputs.peer = select((peers || []).map((p) => [p.id, p.name]), c.peer || (peers[0] || {}).id);
      return [field(tr('Serveur'), inputs.peer, tr('Son administrateur doit réserver de la place pour les archives (Paramètres → Stockage froid de son serveur).'))];
    }
    return [field(tr('Remote rclone et dossier'), inp('remote', { placeholder: 'pcloud:Nexis' }), tr('Nom donné dans rclone config, suivi de « : » et du dossier.')),
      field(tr('Fichier de configuration'), inp('config', { placeholder: tr('facultatif, ex. /home/seance/.config/rclone/rclone.conf') }))];
  };
  const box = h('div.form');
  const note = h('p.muted.small');
  const err = h('p.form-error');
  const paint = () => {
    for (const k of Object.keys(inputs)) delete inputs[k];
    const k = kind.value;
    note.textContent = available[k] || COLD_KINDS.find((x) => x[0] === k)[2];
    note.className = available[k] ? 'form-error small' : 'muted small';
    box.replaceChildren(...fieldsFor(k));
  };
  kind.addEventListener('change', paint);
  paint();
  const conf = () => {
    const out = {};
    for (const [k, el] of Object.entries(inputs)) out[k] = k === 'port' || k === 'peer' ? Number(el.value) : el.value.trim();
    return out;
  };
  // SFTP : la clé du serveur est montrée une fois, à confirmer (elle sera vérifiée ensuite à chaque connexion)
  const confirmKey = async (fp) => {
    const pretty = fp.match(/../g).join(':');
    return confirmDialog(tr('Clé du serveur SFTP'), tr('Empreinte MD5 de la clé du serveur : {fp}. Vérifiez-la si vous le pouvez (chez votre hébergeur, ou avec ssh-keygen -l -E md5), puis confirmez : Nexis refusera ensuite tout serveur qui présente une autre clé.', { fp: pretty }), tr('Confirmer la clé'), false);
  };
  return modal(existing ? tr('Modifier le stockage') : tr('Ajouter un stockage froid'), h('div.form',
    existing ? null : field(tr('Type'), kind), note, field(tr('Nom'), name), box, err), [
    { label: tr('Annuler'), value: false },
    { label: existing ? tr('Enregistrer') : tr('Ajouter'), primary: true, handler: async () => {
      err.textContent = '';
      const k = existing ? existing.kind : kind.value;
      const body = { kind: k, name: name.value.trim(), conf: conf() };
      try {
        for (let round = 0; round < 2; round += 1) {
          const r = existing ? await put(`/api/admin/cold/stores/${existing.id}`, body) : await post('/api/admin/cold/stores', body);
          if (r.ok) return true;
          if (r.fingerprint && await confirmKey(r.fingerprint)) { body.conf.hostkey = r.fingerprint; continue; }
          return false;
        }
      } catch (e) { err.textContent = e.message; }
      return false;
    } },
  ]);
}

async function coldPage(body, ctx) {
  let data, freeData, extra;
  try {
    [data, freeData, extra] = await Promise.all([get('/api/admin/cold'), get('/api/admin/cold/free'), get('/api/admin/cold/extra')]);
  } catch (e) { clear(body, h('p.form-error', e.message)); return; }
  if (!ctx.isCurrent()) return;
  const reload = () => coldPage(body, ctx);
  const stores = data.stores;
  const byId = Object.fromEntries(stores.map((s) => [s.id, s]));
  const free = (s) => {
    const el = h('span.muted');
    get(`/api/admin/cold/stores/${s.id}/free`).then((r) => { if (r.free != null) el.textContent = tr(' · {n} libres', { n: coldSize(r.free) }); }).catch(() => {});
    return el;
  };
  const storeRow = (s) => h('div.user-row',
    h('div.lib-icon', icon(s.kind === 's3' || s.kind === 'webdav' || s.kind === 'rclone' ? 'backup' : 'storage')),
    h('div.grow', h('strong', s.name), h('span.muted', ` · ${COLD_KINDS.find((k) => k[0] === s.kind)[1]}`),
      h('div.small', h('code', coldSummary(s))),
      h('div.small.muted', plural(s.copies, '{n} titre archivé', '{n} titres archivés'), s.bytes ? ` · ${coldSize(s.bytes)}` : '', free(s))),
    toggle(tr('Utilisé'), s.enabled, { onchange: async (e) => {
      try { await put(`/api/admin/cold/stores/${s.id}`, { enabled: e.target.checked }); } catch (x) { toast(x.message, 'bad'); }
    } }),
    s.kind === 'smb' ? null : h('button.btn.small', { onclick: async () => { if (await coldForm(s, data.available, extra.peers)) { toast(tr('Stockage mis à jour'), 'ok'); reload(); } } }, tr('Modifier')),
    h('button.icon-btn', { title: tr('Retirer'), onclick: async () => {
      if (!await confirmDialog(tr('Retirer ce stockage ?'), tr('Nexis oublie ce stockage et ses copies. Les fichiers déjà copiés restent sur le stockage.'), tr('Retirer'))) return;
      try { await del(`/api/admin/cold/stores/${s.id}`); toast(tr('Stockage retiré'), 'ok'); reload(); } catch (e) { toast(e.message, 'bad'); }
    } }, icon('trash')));
  const running = data.jobs.filter((j) => j.state === 'running');
  const errors = data.jobs.filter((j) => j.state === 'error');
  const jobRow = (j) => h('div.user-row',
    h('div.grow', h('strong', j.title), h('span.muted', ` → ${(byId[j.store_id] || {}).name || '?'}`),
      j.state === 'running' && j.total ? h('div.dl-bar.cold-bar', h('span.films', { style: { width: `${Math.round((j.done / j.total) * 100)}%` } })) : null,
      j.state === 'running' ? h('div.small.muted', j.total ? tr('{a} sur {b}', { a: coldSize(j.done || 0), b: coldSize(j.total) }) : tr('Préparation…')) : null,
      j.error ? h('div.form-error.small', j.error) : null),
    j.state === 'error' ? h('button.btn.small', { onclick: async () => { await post(`/api/admin/cold/jobs/${j.id}/retry`); reload(); } }, tr('Réessayer')) : null,
    h('button.icon-btn', { title: tr('Annuler'), onclick: async () => { await del(`/api/admin/cold/jobs/${j.id}`); reload(); } }, icon('close')));
  const rate = h('input', { type: 'number', min: 0, step: 1, value: data.settings.cold_rate || 0 });
  const ratePlay = h('input', { type: 'number', min: 0, step: 1, value: data.settings.cold_rate_play });
  clear(body,
    h('div.toolbar', h('p.muted', tr('Le titre garde son affiche, son résumé et son historique ; si son fichier quitte la bibliothèque, Nexis le lit depuis la copie (quelques secondes de plus au démarrage). Pour archiver : menu ⋯ d’un titre, ou sélection multiple d’une bibliothèque.')),
      h('button.btn.primary', { onclick: async () => { if (await coldForm(null, data.available, extra.peers)) { toast(tr('Stockage ajouté'), 'ok'); reload(); } } }, tr('+ Ajouter un stockage'))),
    stores.length ? h('div.user-list', stores.map(storeRow)) : h('div.empty', tr('Aucun stockage froid. Ajoutez un disque, un NAS ou un stockage en ligne.')),
    running.length || data.queued || errors.length ? h('section.panel', h('h2', tr('Archivage en cours')),
      data.queued ? h('p.muted.small', plural(data.queued, '{n} titre en attente', '{n} titres en attente')) : null,
      h('div.user-list', [...running, ...errors].map(jobRow)),
      data.jobs.filter((j) => j.state === 'queued').length ? h('details', h('summary.small', tr('File d’attente')),
        h('div.user-list', data.jobs.filter((j) => j.state === 'queued').map(jobRow))) : null) : null,
    freePanel(freeData, stores, reload),
    freeLists(freeData, reload),
    cachePanel(extra, reload),
    h('section.panel', h('h2', tr('Débit des copies')),
      h('p.muted.small', tr('Les copies se font une à la fois, en tâche de fond. Pendant qu’une vidéo est regardée, leur débit est réduit pour ne pas la gêner. 0 : sans limite.')),
      h('div.grid2', field(tr('Débit maximal (Mo/s)'), rate), field(tr('Pendant une lecture (Mo/s)'), ratePlay)),
      h('div.actions-row', h('button.btn', { onclick: async () => {
        try { await put('/api/admin/cold/settings', { cold_rate: Number(rate.value), cold_rate_play: Number(ratePlay.value) }); toast(tr('Enregistré'), 'ok'); } catch (e) { toast(e.message, 'bad'); }
      } }, tr('Enregistrer')))));
  // suivi de la copie en cours
  if (running.length || data.queued) {
    setTimeout(() => { if (ctx.isCurrent() && body.isConnected) reload(); }, 2500);
  }
}

// libérer de la place : règle, Radarr / Sonarr, suppression par bibliothèque
function freePanel(free, stores, reload) {
  const c = free.conf;
  const on = toggle(tr('Libérer de la place automatiquement'), c.enabled);
  const days = h('input', { type: 'number', min: 7, max: 3650, value: c.days });
  const mode = select([['review', tr('Me proposer une liste à valider')], ['auto', tr('Archiver et libérer d’office')]], c.mode);
  const store = select([['', tr('Premier stockage actif')], ...stores.map((s) => [s.id, s.name])], c.store || '');
  const delay = h('input', { type: 'number', min: 0, max: 90, value: c.delay_days });
  const minFree = h('input', { type: 'number', min: 0, value: c.min_free_gb });
  const keepList = toggle(tr('Garder les titres d’une liste « À regarder »'), c.keep_watchlist);
  const keepRated = toggle(tr('Garder les titres notés 4 ou 5 étoiles'), c.keep_rated);
  const keepStarted = toggle(tr('Garder les titres commencés'), c.keep_started);
  const methods = {};
  const libRows = free.libraries.map((l) => {
    const chosen = select([['arr', l.kind === 'movie' ? tr('par Radarr') : tr('par Sonarr')], ['write', tr('par Nexis (bibliothèque en écriture)')],
      ['manual', tr('à la main (liste des fichiers)')]], l.method);
    methods[l.id] = chosen;
    const use = h('input', { type: 'checkbox', checked: !c.libraries.length || c.libraries.includes(l.id) });
    return { id: l.id, use, row: h('div.cold-lib', h('label.check', use, h('span', l.name)), chosen) };
  });
  const arrFields = (which, label) => {
    const url = h('input', { value: c[`${which}_url`] || '', placeholder: which === 'radarr' ? 'http://192.168.1.20:7878' : 'http://192.168.1.20:8989' });
    const key = h('input', { type: 'password', autocomplete: 'off', placeholder: c.secrets_set.includes(`${which}_key`) ? tr('Laisser vide pour garder l’actuel') : tr('Settings → General → API Key') });
    const test = h('button.btn.small', { type: 'button', onclick: async () => {
      try { const r = await post('/api/admin/cold/free/check', { which, url: url.value.trim(), key: key.value.trim() }); toast(tr('{app} {v} répond', { app: r.app, v: r.version || '' }), 'ok'); } catch (e) { toast(e.message, 'bad'); }
    } }, tr('Tester'));
    return { url, key, el: h('div.grid2', field(tr('Adresse de {app}', { app: label }), url), field(tr('Clé d’API de {app}', { app: label }), h('div.actions-row', key, test))) };
  };
  const radarr = arrFields('radarr', 'Radarr');
  const sonarr = arrFields('sonarr', 'Sonarr');
  const preview = h('div');
  const save = async () => {
    const libs = libRows.filter((x) => x.use.checked).map((x) => x.id);
    const body = { enabled: on.querySelector('input').checked, days: Number(days.value), mode: mode.value, store: store.value ? Number(store.value) : null,
      delay_days: Number(delay.value), min_free_gb: Number(minFree.value), keep_watchlist: keepList.querySelector('input').checked,
      keep_rated: keepRated.querySelector('input').checked, keep_started: keepStarted.querySelector('input').checked,
      libraries: libs.length === libRows.length ? [] : libs, methods: Object.fromEntries(Object.entries(methods).map(([k, el]) => [k, el.value])),
      radarr_url: radarr.url.value.trim(), sonarr_url: sonarr.url.value.trim() };
    if (radarr.key.value.trim()) body.radarr_key = radarr.key.value.trim();
    if (sonarr.key.value.trim()) body.sonarr_key = sonarr.key.value.trim();
    await put('/api/admin/cold/free', body);
  };
  return h('section.panel', h('h2', tr('Libérer de la place')),
    h('p.muted.small', tr('Les films et les séries que personne n’a regardés depuis le nombre de jours choisi sont copiés sur le stockage froid, puis leur fichier quitte la bibliothèque après un délai. Ils restent dans Nexis et se lisent depuis la copie. Un titre regardé pendant le délai est gardé.')),
    on,
    h('div.grid2', field(tr('Non regardés depuis (jours)'), days), field(tr('Fonctionnement'), mode)),
    h('div.grid2', field(tr('Stockage froid'), store), field(tr('Délai avant suppression (jours)'), delay)),
    field(tr('Seulement si la place libre de la bibliothèque passe sous (Go)'), minFree, tr('0 : sans condition de place')),
    keepList, keepRated, keepStarted,
    h('h3.install-sub', tr('Bibliothèques et suppression des fichiers')),
    h('p.muted.small', tr('Les bibliothèques restent en lecture seule pour Nexis : le fichier est supprimé par Radarr ou Sonarr (le titre n’y est plus surveillé, sinon ils le retéléchargeraient), par Nexis si la bibliothèque est montée en écriture, ou par vous à partir d’une liste.')),
    h('div.cold-libs', libRows.map((x) => x.row)),
    radarr.el, sonarr.el,
    h('div.actions-row',
      h('button.btn.primary', { onclick: async () => { try { await save(); toast(tr('Enregistré'), 'ok'); reload(); } catch (e) { toast(e.message, 'bad'); } } }, tr('Enregistrer')),
      h('button.btn', { onclick: async () => {
        try {
          await save();
          const r = await get('/api/admin/cold/free/preview');
          clear(preview, h('p.small', tr('Nouveaux titres concernés avec ces réglages (en plus de ceux déjà listés) : {n}, {size}.', { n: r.count, size: coldSize(r.size) })),
            r.items.length ? h('div.table-wrap', h('table.stat-table', h('tbody', r.items.slice(0, 50).map((x) => h('tr',
              h('td', h('a', { href: `#/item/${x.root}` }, x.title), x.year ? h('span.muted', ` (${x.year})`) : null), h('td', x.library),
              h('td', x.last ? tr('vu {when}', { when: since(x.last) }) : tr('jamais vu')), h('td', coldSize(x.size))))))) : null);
        } catch (e) { toast(e.message, 'bad'); }
      } }, tr('Aperçu')),
      h('button.btn', { onclick: async () => {
        try { await save(); const r = await post('/api/admin/cold/free/run'); toast(tr('{n} nouveau(x) · {f} libéré(s)', { n: r.new, f: r.freed }), 'ok'); reload(); } catch (e) { toast(e.message, 'bad'); }
      } }, tr('Lancer maintenant'))),
    preview);
}

// ce qui est prévu : à valider, en cours d'archivage, suppression prévue, à effacer à la main ; titres gardés
function freeLists(free, reload) {
  const by = (st) => free.groups.filter((g) => g.state === st);
  const act = (label, fn, extra = '') => h(`button.btn.small${extra}`, { onclick: async () => { try { await fn(); reload(); } catch (e) { toast(e.message, 'bad'); } } }, label);
  const row = (g, extras) => h('div.user-row',
    h('div.grow', h('a', { href: `#/item/${g.root}` }, h('strong', g.title)), h('span.muted', ` · ${coldSize(g.size)}`),
      g.files.length > 1 ? h('span.muted.small', ` · ${plural(g.files.length, '{n} fichier', '{n} fichiers')}`) : null,
      g.delete_after ? h('div.small.muted', tr('Supprimé de la bibliothèque le {d}', { d: new Date(g.delete_after * 1000).toLocaleDateString(locale(), { day: 'numeric', month: 'long' }) })) : null,
      g.errors.map((e) => h('div.form-error.small', e)),
      g.state === 'manual' ? h('details', h('summary.small', tr('Fichiers à supprimer')), h('div.small', g.files.map((f) => h('div', h('code', f.path))))) : null),
    ...extras(g));
  const keepBtn = (g) => act(tr('Garder sur le disque'), () => post('/api/admin/cold/free/keep', { root: g.root }));
  const skipBtn = (g) => act(tr('Pas cette fois'), () => post('/api/admin/cold/free/cancel', { root: g.root }), '.ghost');
  const proposed = by('proposed');
  const panels = [];
  if (proposed.length) {
    const size = proposed.reduce((n, g) => n + g.size, 0);
    panels.push(h('section.panel', h('h2', tr('À valider')),
      h('p.muted.small', tr('{n} titre(s) non regardés, {size} à libérer.', { n: proposed.length, size: coldSize(size) })),
      h('div.actions-row', act(tr('Tout valider'), () => post('/api/admin/cold/free/approve', { roots: proposed.map((g) => g.root) }), '.primary')),
      h('div.user-list', proposed.map((g) => row(g, (x) => [act(tr('Valider'), () => post('/api/admin/cold/free/approve', { roots: [x.root] })), keepBtn(x), skipBtn(x)])))));
  }
  const approved = by('approved');
  if (approved.length) panels.push(h('section.panel', h('h2', tr('Copie sur le stockage froid')), h('div.user-list', approved.map((g) => row(g, (x) => [keepBtn(x), skipBtn(x)])))));
  const pending = by('pending');
  if (pending.length) panels.push(h('section.panel', h('h2', tr('Suppression prévue')), h('p.muted.small', tr('Copie vérifiée. Le fichier quittera la bibliothèque à la date indiquée, sauf si le titre est regardé d’ici là.')),
    h('div.user-list', pending.map((g) => row(g, (x) => [keepBtn(x), skipBtn(x)])))));
  const manual = by('manual');
  if (manual.length) panels.push(h('section.panel', h('h2', tr('À supprimer à la main')), h('p.muted.small', tr('Copie vérifiée : vous pouvez effacer ces fichiers de la bibliothèque. Nexis s’en aperçoit au prochain scan.')),
    h('div.user-list', manual.map((g) => row(g, (x) => [keepBtn(x)])))));
  if (free.freed.n) panels.push(h('p.muted.small', tr('Déjà libéré : {n} fichier(s), {size}. Ces titres se lisent depuis le stockage froid.', { n: free.freed.n, size: coldSize(free.freed.bytes) })));
  if (free.kept.length) panels.push(h('details.panel', h('summary', tr('Toujours gardés sur le disque ({n})', { n: free.kept.length })),
    h('div.user-list', free.kept.map((k) => h('div.user-row', h('div.grow', h('a', { href: `#/item/${k.item_id}` }, k.title)),
      act(tr('Ne plus garder'), () => post('/api/admin/cold/free/keep', { root: k.item_id, keep: false }), '.ghost'))))));
  return h('div', ...panels);
}

// titres ramenés en local, et place réservée aux archives des serveurs liés
function cachePanel(extra, reload) {
  const c = extra.cache;
  const gb = h('input', { type: 'number', min: 0, step: 1, value: c.gb });
  const keep = h('input', { type: 'number', min: 0, step: 1, value: c.keep_free_gb });
  const next = h('input', { type: 'number', min: 0, max: 20, value: c.next });
  const host = h('input', { type: 'number', min: 0, step: 1, value: extra.host_gb });
  const saveBtn = (body) => async () => { try { await put('/api/admin/cold/extra', body()); toast(tr('Enregistré'), 'ok'); reload(); } catch (e) { toast(e.message, 'bad'); } };
  return h('div',
    h('section.panel', h('h2', tr('Titres ramenés en local')),
      h('p.muted.small', tr('Un titre lu depuis un stockage en ligne est ramené sur ce serveur, avec les épisodes qui suivent : les lectures suivantes démarrent tout de suite. Les plus anciens repartent quand la place réservée est pleine (la copie froide, elle, reste).')),
      h('p.small', tr('{n} titre(s) ramené(s), {size}', { n: c.count, size: coldSize(c.bytes) })),
      h('div.grid2', field(tr('Place réservée (Go)'), gb, tr('0 : ne rien ramener')), field(tr('Toujours garder libres sur le disque (Go)'), keep)),
      field(tr('Épisodes suivants à ramener'), next),
      h('div.actions-row', h('button.btn', { onclick: saveBtn(() => ({ cache_gb: Number(gb.value), keep_free_gb: Number(keep.value), next: Number(next.value) })) }, tr('Enregistrer')),
        c.count ? h('button.btn.ghost', { onclick: saveBtn(() => ({ clear_cache: true })) }, tr('Vider')) : null)),
    extra.peers.length ? h('section.panel', h('h2', tr('Archives des serveurs liés')),
      h('p.muted.small', tr('Place que ce serveur réserve aux archives des autres serveurs de la fédération (dans son dossier de données). 0 : il n’en accueille pas.')),
      field(tr('Place réservée (Go)'), host),
      extra.hosted.length ? h('div.small', extra.hosted.map((x) => h('div', `${x.peer} : ${coldSize(x.bytes)}`))) : null,
      h('div.actions-row', h('button.btn', { onclick: saveBtn(() => ({ host_gb: Number(host.value) })) }, tr('Enregistrer')))) : null);
}

// archiver des titres (fiche, sélection multiple) : choix du stockage
export async function archiveDialog(ids, label) {
  let data;
  try { data = await get('/api/admin/cold'); } catch (e) { toast(e.message, 'bad'); return; }
  const stores = data.stores.filter((s) => s.enabled);
  if (!stores.length) {
    if (await confirmDialog(tr('Aucun stockage froid'), tr('Ajoutez d’abord un stockage froid (disque, NAS, stockage en ligne) dans l’administration.'), tr('Ouvrir le stockage froid'), false)) location.hash = '#/admin/cold';
    return;
  }
  const pick = select(stores.map((s) => [s.id, `${s.name} — ${coldSummary(s)}`]), stores[0].id);
  const ok = await modal(tr('Archiver sur un stockage froid'), h('div.form',
    h('p', label), field(tr('Stockage'), pick),
    h('p.muted.small', tr('Le fichier est copié puis vérifié, en tâche de fond. Il reste aussi dans la bibliothèque : rien n’y est supprimé.'))),
  [{ label: tr('Annuler'), value: false }, { label: tr('Archiver'), primary: true, value: true }]);
  if (!ok) return;
  try {
    const r = await post('/api/admin/cold/archive', { ids, store: Number(pick.value) });
    toast(r.queued ? plural(r.queued, '{n} fichier à archiver', '{n} fichiers à archiver') : tr('Déjà archivé sur ce stockage'), 'ok');
  } catch (e) { toast(e.message, 'bad'); }
}

// copies froides d'un titre (fiche, menu ⋯)
export async function coldCopiesDialog(item) {
  let r;
  try { r = await get(`/api/admin/cold/items/${item.id}`); } catch (e) { toast(e.message, 'bad'); return; }
  const list = h('div.user-list', r.copies.map((c) => h('div.user-row',
    h('div.grow', h('strong', c.store), h('div.small', h('code', c.name)),
      h('div.small.muted', `${coldSize(c.size)} · ${tr('vérifiée {when}', { when: since(c.verified_at) })}`)),
    h('button.icon-btn', { title: tr('Supprimer cette copie'), onclick: async () => {
      if (!await confirmDialog(tr('Supprimer cette copie ?'), tr('Le fichier est effacé du stockage « {s} ».', { s: c.store }), tr('Supprimer'))) return;
      try { await del(`/api/admin/cold/items/${item.id}/${c.store_id}`); toast(tr('Copie supprimée'), 'ok'); document.querySelector('.modal-overlay')?.remove(); } catch (e) { toast(e.message, 'bad'); }
    } }, icon('trash')))));
  const jobs = r.jobs.map((j) => h('p.small', j.state === 'error' ? h('span.form-error', `${tr('Échec')} : ${j.error}`) : tr('Archivage en attente ou en cours')));
  modal(tr('Copies sur le stockage froid'), h('div.form', r.copies.length ? list : h('p.muted', tr('Aucune copie pour l’instant.')), ...jobs),
    [{ label: tr('Fermer'), value: false }]);
}

const PAGES = { cold: coldPage, stats: statsPage, dashboard: dashboardPage, backups: backupsPage, users: usersTab, update: updatePage, tasks: tasksTab, libraries: librariesTab,
  federation: federationPage, dlna: dlnaPage, shares: sharesTab, metadata: metadataPage, detection: detectionPage, requests: requestsTab, transcoding: transcodingPage,
  subtitles: subtitlesPage, downloads: downloadsPage, live: liveTab };

export async function renderAdminSection(id, body, ctx) {
  await PAGES[id](body, ctx);
}
