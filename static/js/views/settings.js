import { adminTarget, del, get, post, put, setAdminTarget } from '../api.js';
import { qrSvg } from '../qr.js';
import { ADMIN_ALIASES, ADMIN_GROUPS, renderAdminSection } from './admin.js';
import { GRADIENTS, applyPrefs, navItems, refreshMe, repaintNav, repaintUser, state } from '../app.js';
import { PIN_LABELS, pins, removePin } from '../pins.js';
import { SUB_BGS, SUB_COLORS, SUB_EDGES, SUB_FONTS, SUB_POSITIONS, applySubStyle } from '../substyle.js';
import { arrange, avatar, clear, confirmDialog, field, h, icon, img, modal, select, spinner, toast, toggle } from '../ui.js';
import { ART, BACKGROUNDS, CATEGORIES, DEFAULT_BG, artDataUrl, artSvg } from '../avatar-art.js';
import { device, deviceName, setDevice } from '../device.js';
import { shortcutsPanel } from '../keys.js';
import { disablePush, enablePush, pushState, pushSupported } from '../push.js';

// notifications push : nouvel épisode d'une série suivie, titre demandé arrivé
function pushPanel() {
  const box = h('div');
  const panel = h('section.panel', h('h2', tr('Notifications')),
    h('p.muted.small', tr('Un nouvel épisode d’une série que vous suivez, ou un titre demandé qui arrive : une notification sur cet appareil, même Nexis fermé.')), box);
  const paint = async () => {
    const st = await pushState().catch(() => 'off');
    if (st === 'unsupported') {
      clear(box, h('p.small', /iphone|ipad/i.test(navigator.userAgent) ? tr('Sur iPhone et iPad : installez d’abord Nexis sur l’écran d’accueil (iOS 16.4 ou plus récent), puis ouvrez ce réglage depuis l’application.')
        : tr('Ce navigateur ne gère pas les notifications push, ou Nexis n’est pas ouvert en HTTPS.')));
      return;
    }
    if (st === 'denied') { clear(box, h('p.small', tr('Notifications bloquées pour ce site : autorisez-les dans les réglages du navigateur.'))); return; }
    const sw = toggle(tr('Recevoir les notifications sur cet appareil'), st === 'on', { onchange: async (e) => {
      e.target.disabled = true;
      try {
        if (e.target.checked) { await enablePush(); toast(tr('Notifications activées sur cet appareil'), 'ok'); }
        else { await disablePush(); toast(tr('Notifications désactivées sur cet appareil'), 'ok'); }
      } catch (ex) { toast(ex.message, 'bad'); }
      paint();
    } });
    clear(box, sw, st === 'on' ? h('div.actions-row', h('button.btn.small', { type: 'button', onclick: async () => {
      try { await post('/api/push/test'); toast(tr('Notification d’essai envoyée'), 'ok'); } catch (ex) { toast(ex.message, 'bad'); }
    } }, tr('Envoyer une notification d’essai'))) : null);
  };
  paint();
  return panel;
}
import { LANGUAGES, lang, locale, tr, useLanguage } from '../i18n.js';

const LANGS = [['fra', tr('Français')], ['eng', tr('Anglais')], ['jpn', tr('Japonais')], ['spa', tr('Espagnol')], ['deu', tr('Allemand')],
  ['ita', tr('Italien')], ['por', tr('Portugais')], ['kor', tr('Coréen')], ['zho', tr('Chinois')], ['', tr('Pas de préférence')]];
const ACCENTS = ['#ffa43a', '#e0a84f', '#e5484d', '#3dd68c', '#4c9aff', '#a78bfa', '#f472b6', '#2dd4bf', '#f97316'];

// Liste à réordonner (flèches ou glisser-déposer) avec un interrupteur par ligne.
// items : [{ key, label, hint?, fixed? }] ; onOrder(keys) ; onShow(key, visible)
function layoutList(items, isOn, onOrder, onShow) {
  const byKey = new Map(items.map((i) => [i.key, i]));
  let keys = items.map((i) => i.key);
  const list = h('ol.layout-list');
  let dragged = null;
  const move = (k, delta) => {
    const i = keys.indexOf(k);
    const j = i + delta;
    if (j < 0 || j >= keys.length) return;
    keys.splice(i, 1);
    keys.splice(j, 0, k);
    paint(k);
    onOrder([...keys]);
  };
  function paint(focusKey) {
    list.replaceChildren(...keys.map((k, i) => {
      const it = byKey.get(k);
      const sw = it.fixed ? h('span.muted.small', tr('toujours affiché')) : toggle('', isOn(k), { 'aria-label': tr('Afficher {label}', { label: it.label }) });
      if (!it.fixed) sw.querySelector('input').addEventListener('change', (e) => { li.classList.toggle('off', !e.target.checked); onShow(k, e.target.checked); });
      const li = h('li.layout-item', { draggable: 'true', dataset: { key: k }, class: !it.fixed && !isOn(k) ? 'layout-item off' : 'layout-item' },
        h('span.layout-grip', { 'aria-hidden': 'true' }, '⋮⋮'),
        h('div.layout-label', it.label, it.hint ? h('span.muted.small', it.hint) : null),
        h('button.icon-btn', { type: 'button', 'aria-label': tr('Monter {label}', { label: it.label }), disabled: i === 0, onclick: () => move(k, -1) }, icon('up')),
        h('button.icon-btn', { type: 'button', 'aria-label': tr('Descendre {label}', { label: it.label }), disabled: i === keys.length - 1, onclick: () => move(k, 1) }, icon('down')),
        sw,
        it.remove ? h('button.icon-btn', { type: 'button', title: tr('Retirer de l’accueil'), 'aria-label': tr('Retirer {label}', { label: it.label }), onclick: it.remove }, icon('trash')) : null);
      li.addEventListener('dragstart', (e) => { dragged = k; li.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; });
      li.addEventListener('dragend', () => { dragged = null; li.classList.remove('dragging'); });
      li.addEventListener('dragover', (e) => { if (dragged && dragged !== k) e.preventDefault(); });
      li.addEventListener('drop', (e) => {
        e.preventDefault();
        if (!dragged || dragged === k) return;
        keys.splice(keys.indexOf(dragged), 1);
        keys.splice(keys.indexOf(k) + (e.offsetY > li.offsetHeight / 2 ? 1 : 0), 0, dragged);
        paint();
        onOrder([...keys]);
      });
      return li;
    }));
    if (focusKey) {
      const btn = list.querySelector(`[data-key="${CSS.escape(focusKey)}"] .icon-btn:not([disabled])`);
      if (btn) btn.focus();
    }
  }
  paint();
  return list;
}

// Image choisie -> carré centré de `size` px, en JPEG (data URL)
function squareImage(fileObj, size) {
  return new Promise((resolve, reject) => {
    if (fileObj.size > 30 * 1024 * 1024) { reject(new Error(tr('Image trop lourde'))); return; }
    const url = URL.createObjectURL(fileObj);
    const img = new Image();
    img.onload = () => {
      const side = Math.min(img.naturalWidth, img.naturalHeight);
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = size;
      canvas.getContext('2d').drawImage(img, (img.naturalWidth - side) / 2, (img.naturalHeight - side) / 2, side, side, 0, 0, size, size);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL('image/jpeg', 0.9));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error(tr('Image illisible'))); };
    img.src = url;
  });
}

// ---------------------------------------------------------------- double authentification
function showRecoveryCodes(codes) {
  const text = codes.join('\n');
  const file = tr('Nexis : codes de secours ({p1})\nChaque code ne fonctionne qu\'une fois.\n\n{text}\n', { p1: new Date().toLocaleDateString(locale()), text });
  return modal(tr('Codes de secours'), h('div.form',
    h('p.small', tr('Ces codes remplacent le téléphone s’il est perdu ou indisponible. Chacun ne fonctionne qu’une fois. Rangez-les en lieu sûr (gestionnaire de mots de passe, impression) : ils ne seront plus affichés.')),
    h('ol.recovery-codes', codes.map((c) => h('li', h('code', c)))),
    h('div.actions-row',
      h('button.btn.small', { type: 'button', onclick: async () => {
        try { await navigator.clipboard.writeText(text); toast(tr('Codes copiés'), 'ok'); } catch { toast(tr('Copie impossible : sélectionnez les codes'), 'bad'); }
      } }, tr('Copier')),
      h('a.btn.small', { href: URL.createObjectURL(new Blob([file], { type: 'text/plain' })), download: tr('nexis-codes-de-secours.txt') }, tr('Télécharger')))),
  [{ label: tr('J’ai enregistré mes codes'), primary: true, value: true }]);
}

async function enableTwoFactor(rerender) {
  const pass = h('input', { type: 'password', autocomplete: 'current-password' });
  const err = h('p.form-error');
  const setup = await modal(tr('Activer la double authentification'), h('div.form',
    h('p.small', tr('À chaque connexion, un code à 6 chiffres généré par une application d’authentification sera demandé en plus du mot de passe (Google Authenticator, Microsoft Authenticator, Aegis, 2FAS, Bitwarden…).')),
    field(tr('Mot de passe actuel'), pass), err), [
    { label: tr('Annuler'), value: null },
    { label: tr('Continuer'), primary: true, handler: async () => {
      try { return await post('/api/me/2fa/setup', { password: pass.value }); } catch (e) { err.textContent = e.message; return false; }
    } },
  ]);
  if (!setup) return;
  const qrBox = h('div.qr-box', spinner());
  qrSvg(setup.uri, 200).then((svg) => qrBox.replaceChildren(svg)).catch(() => qrBox.replaceChildren(h('p.muted.small', tr('QR code indisponible : saisissez la clé ci-dessous.'))));
  const code = h('input', { inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 6, placeholder: '123456',
    style: { letterSpacing: '0.2em', fontSize: '20px' } });
  const err2 = h('p.form-error');
  const done = await modal(tr('Scanner le QR code'), h('div.form',
    h('p.small', tr('1. Dans l’application d’authentification, ajoutez un compte en scannant ce QR code.')),
    qrBox,
    h('details.small', h('summary', tr('Impossible de scanner ? Saisir la clé')), h('p', h('code.secret-key', setup.secret.match(/.{1,4}/g).join(' '))),
      h('p.muted', tr('Type : basé sur le temps (TOTP), 6 chiffres, 30 secondes.'))),
    field(tr('2. Code affiché par l’application'), code), err2), [
    { label: tr('Annuler'), value: null },
    { label: tr('Activer'), primary: true, handler: async () => {
      try { return await post('/api/me/2fa/enable', { code: code.value }); } catch (e) { err2.textContent = e.message; return false; }
    } },
  ]);
  if (!done) return;
  toast(tr('Double authentification activée'), 'ok');
  await showRecoveryCodes(done.recovery_codes);
  await refreshMe();
  rerender();
}

async function confirmCode(title, lead, withPassword, url) {
  const pass = h('input', { type: 'password', autocomplete: 'current-password' });
  const code = h('input', { autocomplete: 'one-time-code', autocapitalize: 'none', maxlength: 9, placeholder: '123456' });
  const err = h('p.form-error');
  return modal(title, h('div.form', h('p.small', lead), withPassword ? field(tr('Mot de passe actuel'), pass) : null,
    field(tr('Code de l’application (ou code de secours)'), code), err), [
    { label: tr('Annuler'), value: null },
    { label: tr('Confirmer'), primary: true, handler: async () => {
      try { return await post(url, { password: pass.value, code: code.value }); } catch (e) { err.textContent = e.message; return false; }
    } },
  ]);
}

function twoFactorPanel(rerender) {
  const box = h('div', spinner());
  get('/api/me/2fa').then((st) => {
    if (!st.available) { box.replaceChildren(h('p.muted.small', tr('Non disponible pour ce compte.'))); return; }
    if (!st.enabled) {
      box.replaceChildren(
        h('div.status-box', tr('Désactivée.')),
        h('p.muted.small', tr('Recommandée pour tout compte accessible depuis Internet, en particulier les comptes administrateurs.')),
        h('button.btn.primary', { onclick: () => enableTwoFactor(rerender) }, tr('Activer')));
      return;
    }
    box.replaceChildren(
      h('div.status-box.ok', tr('Activée : un code est demandé à chaque connexion.')),
      h('p.muted.small', tr('Codes de secours restants : {recovery_left} sur 10.', { recovery_left: st.recovery_left }), st.recovery_left <= 3 ? tr(' Pensez à en générer de nouveaux.') : ''),
      h('div.actions-row',
        h('button.btn', { onclick: async () => {
          const r = await confirmCode(tr('Nouveaux codes de secours'), tr('Les anciens codes de secours cesseront de fonctionner.'), false, '/api/me/2fa/recovery');
          if (r) { await showRecoveryCodes(r.recovery_codes); rerender(); }
        } }, tr('Générer de nouveaux codes de secours')),
        h('button.btn.ghost', { onclick: async () => {
          const r = await confirmCode(tr('Désactiver la double authentification'), tr('Le mot de passe suffira de nouveau pour se connecter.'), true, '/api/me/2fa/disable');
          if (r) { toast(tr('Double authentification désactivée'), 'ok'); await refreshMe(); rerender(); }
        } }, tr('Désactiver'))),
      h('p.muted.small', tr('Téléphone perdu et plus de codes de secours : un administrateur peut la réinitialiser (Paramètres → Utilisateurs).')));
  }).catch((e) => box.replaceChildren(h('p.form-error.small', e.message)));
  return h('section.panel', h('h2', tr('Double authentification')), box);
}

// ---------------------------------------------------------------- Mon compte
export const ACCOUNT_GROUP = { title: tr('Mon compte'), items: [
  { id: 'profile', label: tr('Profil'), icon: 'user', lead: tr('Nom, photo, langue, code PIN, mot de passe et double authentification.'),
    keys: tr('nom avatar photo couleur langue language pin code mot de passe sécurité double authentification 2fa totp codes de secours') },
  { id: 'playback', label: tr('Lecture'), icon: 'play', lead: tr('Qualité, langues, sous-titres et enchaînement des épisodes.'),
    keys: tr('qualité langue audio sous-titres taille police couleur style épisode suivant générique intro automatique spoiler anti-spoiler résumé') },
  { id: 'appearance', label: tr('Apparence'), icon: 'palette', lead: tr('Thème, couleur d’accent, affiches et fond d’écran.'),
    keys: tr('thème sombre clair oled couleur accent affiches taille fond d’écran') },
  { id: 'device', label: tr('Cet appareil'), icon: 'tv', lead: tr('Réglages propres à ce navigateur ou à cette TV, prioritaires sur ceux du profil.'),
    keys: tr('appareil tv téléphone qualité sous-titres taille animations mouvement télécommande manette navigateur') },
  { id: 'services', label: tr('Trakt et Letterboxd'), icon: 'link', lead: tr('Ce que vous regardez, vos notes et votre liste « À regarder », partagés avec Trakt ou Letterboxd.'),
    keys: tr('trakt letterboxd synchroniser historique notes import export csv scrobble') },
  { id: 'privacy', label: tr('Contenu et notifications'), icon: 'bell', lead: tr('Genres masqués, mode privé, notifications et heures calmes.'),
    keys: tr('genres masquer mode privé historique vie privée notifications podcasts épisodes heures calmes nuit silence') },
  { id: 'layout', label: tr('Accueil et menu'), icon: 'home', lead: tr('Ordre et affichage des rangées de l’accueil et des liens de la barre du haut.'),
    keys: tr('accueil rangées ordre barre du haut menu liens masquer réorganiser épingler page d’ouverture démarrage') },
] };

// Trakt (compte relié par un code, synchronisé toutes les 6 h) et Letterboxd (export / import de fichiers)
async function servicesPage(body) {
  const when = (ts) => new Date(ts * 1000).toLocaleString(locale(), { dateStyle: 'medium', timeStyle: 'short' });
  const traktBox = h('section.panel', h('h2', 'Trakt'), spinner());
  const lbResult = h('div');
  const lbFile = h('input', { type: 'file', accept: '.zip,.csv,text/csv,application/zip', hidden: true, onchange: async () => {
    const f = lbFile.files[0];
    if (!f) return;
    lbResult.replaceChildren(spinner());
    try {
      const r = await fetch('/api/me/letterboxd', { method: 'POST', body: f, credentials: 'same-origin', headers: { 'X-Seance': '1', 'X-Lang': lang } });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || r.statusText);
      lbResult.replaceChildren(h('div.status-box.ok', tr('Import terminé : {w} film(s) marqué(s) comme vu(s), {r} note(s), {l} ajout(s) à « À regarder ».', { w: j.watched, r: j.rated, l: j.watchlist })),
        j.unmatched_count ? h('details.small', h('summary', tr('{n} film(s) absent(s) de ce serveur', { n: j.unmatched_count })),
          h('ul', j.unmatched.map((n) => h('li', n)))) : null);
    } catch (e) { lbResult.replaceChildren(h('p.form-error', e.message)); }
    lbFile.value = '';
  } });
  clear(body, traktBox, h('section.panel', h('h2', 'Letterboxd'),
    h('p.muted.small', tr('Letterboxd n’a pas d’API publique : on passe par des fichiers. L’export contient vos films vus sur Nexis, avec la date et votre note, au format d’import de Letterboxd (letterboxd.com → Settings → Import & Export → Import). Pour l’import, téléchargez votre export Letterboxd (fichier ZIP) et envoyez-le ici : les films vus, les notes et la liste « À regarder » sont reconnus par titre et année.')),
    h('div.actions-row', h('a.btn', { href: '/api/me/letterboxd.csv', download: 'nexis-letterboxd.csv' }, icon('download'), tr('Exporter pour Letterboxd')),
      h('button.btn', { onclick: () => lbFile.click() }, tr('Importer l’export Letterboxd…')), lbFile),
    lbResult));

  let timer = null;
  const paint = (v) => {
    clearTimeout(timer);
    const admin = state.me.user.is_admin ? (() => {
      const id = h('input', { value: v.client_id || '', spellcheck: false, autocomplete: 'off' });
      const secret = h('input', { type: 'password', autocomplete: 'off', placeholder: v.secret_set ? tr('Enregistré (laisser vide pour le garder)') : '' });
      return h('details.alert-conf', { open: !v.configured },
        h('summary.small', tr('Application Trakt du serveur (administrateur)')),
        h('p.muted.small', tr('Une fois pour tout le serveur : sur trakt.tv → Settings → Your API Apps → New Application, nom « Nexis », Redirect URI : urn:ietf:wg:oauth:2.0:oob. Copiez ici le Client ID et le Client Secret.')),
        h('div.grid2', field('Client ID', id), field('Client Secret', secret)),
        h('button.btn', { onclick: async () => {
          const b = { client_id: id.value.trim() };
          if (secret.value.trim()) b.client_secret = secret.value.trim();
          try { paint(await put('/api/admin/trakt', b)); toast(tr('Enregistré'), 'ok'); } catch (e) { toast(e.message, 'bad'); }
        } }, tr('Enregistrer')));
    })() : null;
    const intro = h('p.muted.small', tr('Relié à Trakt, Nexis y envoie ce que vous regardez et récupère ce que vous avez vu ailleurs (toutes les 6 heures) ; les notes et la liste « À regarder » s’ajoutent dans les deux sens. Rien n’est jamais retiré.'));
    if (!v.configured) {
      clear(traktBox, h('h2', 'Trakt'), intro, state.me.user.is_admin ? null : h('div.status-box', tr('L’administrateur du serveur doit d’abord enregistrer une application Trakt.')), admin);
      return;
    }
    if (v.linked) {
      const r = v.last_result;
      clear(traktBox, h('h2', 'Trakt'), intro,
        h('div.status-box.ok', tr('Relié au compte Trakt {name}.', { name: v.username || '?' })),
        v.last_sync ? h('p.small.muted', tr('Dernière synchronisation : {date}', { date: when(v.last_sync) }),
          r ? ` · ${tr('{a} reçu(s), {b} envoyé(s)', { a: r.imported + r.ratings_in + r.watchlist_in, b: r.exported + r.ratings_out + r.watchlist_out })}` : '') : null,
        v.last_error ? h('p.form-error.small', v.last_error) : null,
        h('div.actions-row',
          h('button.btn.primary', { onclick: async (e) => {
            e.target.disabled = true;
            e.target.textContent = tr('Synchronisation…');
            try { const j = await post('/api/me/trakt/sync'); paint(j); toast(tr('Synchronisé'), 'ok'); } catch (ex) { toast(ex.message, 'bad'); paint(await get('/api/me/trakt')); }
          } }, tr('Synchroniser maintenant')),
          h('button.btn', { onclick: async () => { paint(await del('/api/me/trakt')); toast(tr('Compte Trakt délié'), 'ok'); } }, tr('Délier'))),
        admin);
      return;
    }
    clear(traktBox, h('h2', 'Trakt'), intro,
      h('button.btn.primary', { onclick: async () => {
        try {
          const c = await post('/api/me/trakt/link');
          const poll = async () => {
            try {
              const r = await post('/api/me/trakt/poll');
              if (r.status === 'pending') { timer = setTimeout(poll, (c.interval || 5) * 1000); return; }
              if (r.status === 'linked') { toast(tr('Compte Trakt relié : première synchronisation en cours'), 'ok'); paint(await get('/api/me/trakt')); return; }
              toast(r.status === 'denied' ? tr('Accès refusé sur Trakt') : tr('Code expiré : recommencez'), 'bad');
              paint(await get('/api/me/trakt'));
            } catch (e) { toast(e.message, 'bad'); }
          };
          clear(traktBox, h('h2', 'Trakt'),
            h('p', tr('Sur {url}, saisissez ce code :', { url: c.verification_url })),
            h('div.trakt-code', c.user_code),
            h('div.actions-row', h('a.btn.primary', { href: c.verification_url, target: '_blank', rel: 'noopener' }, tr('Ouvrir trakt.tv')),
              h('button.btn', { onclick: () => { clearTimeout(timer); get('/api/me/trakt').then(paint); } }, tr('Annuler'))),
            h('p.muted.small', tr('Cette page se met à jour toute seule une fois l’accès accordé.')));
          timer = setTimeout(poll, (c.interval || 5) * 1000);
        } catch (e) { toast(e.message, 'bad'); }
      } }, tr('Relier mon compte Trakt')), admin);
  };
  try { paint(await get('/api/me/trakt')); } catch (e) { clear(traktBox, h('h2', 'Trakt'), h('p.muted', e.message)); }
}

function accountPage(id, body, rerender) {
  const me = state.me;
  const p = { ...me.prefs };

  const save = async (patch, msg = tr('Préférences enregistrées')) => {
    try {
      const r = await put('/api/me/prefs', patch);
      Object.assign(me.prefs, r);
      applyPrefs(me.prefs);
      toast(msg, 'ok');
    } catch (e) { toast(e.message, 'bad'); }
  };
  const bindSelect = (key, opts, cast = (v) => v) =>
    select(opts, p[key], { onchange: (e) => save({ [key]: cast(e.target.value) }) });
  const bindToggle = (key, label) => {
    const t = toggle(label, p[key]);
    t.querySelector('input').addEventListener('change', (e) => save({ [key]: e.target.checked }));
    return t;
  };
  const swatches = (current, onPick) => h('div.swatches', ACCENTS.map((c) => h('button.swatch', {
    type: 'button', style: { background: c }, 'aria-label': c, class: c === current ? 'swatch on' : 'swatch',
    onclick: (e) => {
      e.currentTarget.parentElement.querySelectorAll('.swatch').forEach((sw) => sw.classList.remove('on'));
      e.currentTarget.classList.add('on');
      onPick(c);
    } })));

  if (id === 'profile') {
    const name = h('input', { value: me.user.display_name, maxlength: 50 });
    let color = me.user.avatar_color;
    const preview = h('span');
    const paintPreview = () => preview.replaceChildren(avatar({ ...me.user, display_name: name.value, avatar_color: color }, 56));
    paintPreview();
    name.addEventListener('input', paintPreview);
    // image : recadrée au centre en carré de 256 px dans le navigateur, réencodée par le serveur
    const file = h('input', { type: 'file', accept: 'image/jpeg,image/png,image/webp', hidden: true });
    file.addEventListener('change', async () => {
      const f = file.files[0];
      file.value = '';
      if (!f) return;
      try { await uploadAvatar(await squareImage(f, 256), tr('Photo de profil enregistrée')); }
      catch (e) { toast(e.message || tr('Image illisible'), 'bad'); }
    });
    const uploadAvatar = async (image, msg) => {
      await put('/api/me/avatar', { image });
      await refreshMe(); repaintUser();
      toast(msg, 'ok');
      rerender();
    };
    // galerie d'avatars : catégories, aperçu, fond au choix
    const choosePreset = async () => {
      let current = Object.keys(ART)[0];
      let bg = null; // null : fond conseillé pour l'illustration
      const preview = h('div.avatar-preview');
      const label = h('div.avatar-preview-label');
      const paintPreview = () => {
        preview.innerHTML = artSvg(current, bg, 160); // dessin fixe du code, pas de contenu externe
        label.textContent = ART[current][0];
        swatchRow.querySelectorAll('.swatch').forEach((sw, i) => sw.classList.toggle('on', i === (bg ?? DEFAULT_BG[current] ?? 0)));
        grid.querySelectorAll('.preset').forEach((b) => b.classList.toggle('on', b.dataset.id === current));
      };
      const grid = h('div.preset-grid');
      const showCategory = (cat) => {
        tabs.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.cat === cat));
        grid.replaceChildren(...Object.entries(ART).filter(([, a]) => a[1] === cat).map(([id, a]) => {
          const btn = h('button.preset', { type: 'button', title: a[0], 'aria-label': a[0], dataset: { id } });
          btn.innerHTML = artSvg(id, null, 72);
          btn.addEventListener('click', () => { current = id; bg = null; paintPreview(); });
          return btn;
        }));
        paintPreview();
      };
      const tabs = h('div.tabs.avatar-tabs', CATEGORIES.map(([cat, name]) =>
        h('button.tab', { type: 'button', dataset: { cat }, onclick: () => showCategory(cat) }, name)));
      const swatchRow = h('div.swatches', BACKGROUNDS.map(([a, b], i) => h('button.swatch', {
        type: 'button', 'aria-label': tr('Fond {p1}', { p1: i + 1 }), style: { background: `linear-gradient(135deg, ${a}, ${b})` },
        onclick: () => { bg = i; paintPreview(); } })));
      showCategory(CATEGORIES[0][0]);
      const ok = await modal(tr('Choisir un avatar'), h('div.avatar-picker',
        h('div.avatar-side', preview, label, h('div.field-label.small', tr('Fond')), swatchRow),
        h('div.avatar-main', tabs, grid)), [
        { label: tr('Annuler'), value: null },
        { label: tr('Utiliser cet avatar'), primary: true, value: true },
      ]);
      if (!ok) return;
      try { await uploadAvatar(await artDataUrl(current, bg), tr('Avatar enregistré')); } catch (e) { toast(e.message, 'bad'); }
    };
    const pictureActions = h('div.avatar-actions',
      h('button.btn.small', { type: 'button', onclick: () => file.click() }, me.user.avatar ? tr('Changer la photo') : tr('Choisir une photo')),
      h('button.btn.small', { type: 'button', onclick: choosePreset }, tr('Avatars')),
      me.user.avatar ? h('button.btn.small.ghost', { type: 'button', onclick: async () => {
        try { await del('/api/me/avatar'); await refreshMe(); repaintUser(); toast(tr('Photo retirée'), 'ok'); rerender(); }
        catch (e) { toast(e.message, 'bad'); }
      } }, tr('Retirer la photo')) : null, file);
    const profile = h('section.panel', h('h2', tr('Nom et avatar')),
      h('div.profile-row', h('div', preview, pictureActions), h('div.grow',
        field(tr('Nom affiché'), name),
        field(me.user.avatar ? tr('Couleur (sans photo)') : tr('Couleur de l’avatar'), swatches(color, (c) => { color = c; paintPreview(); })),
        h('button.btn.primary', { onclick: async () => {
          try { await put('/api/me/profile', { display_name: name.value.trim(), avatar_color: color }); await refreshMe(); repaintUser(); toast(tr('Profil mis à jour'), 'ok'); }
          catch (e) { toast(e.message, 'bad'); }
        } }, tr('Enregistrer le profil')))));

    const pin = h('input', { type: 'password', inputmode: 'numeric', maxlength: 8, autocomplete: 'off', placeholder: tr('4 à 8 chiffres') });
    const pinPass = h('input', { type: 'password', autocomplete: 'current-password' });
    const pinPanel = me.user.home ? h('section.panel', h('h2', tr('Code PIN du profil')),
      h('p.muted.small', me.user.has_pin
        ? tr('Profil protégé : le code est demandé pour y accéder depuis « Changer de profil ».')
        : tr('Sans code, l’accès à ce profil depuis « Changer de profil » demande le mot de passe du compte.')),
      h('div.grid2', field(tr('Nouveau code PIN'), pin), me.user.managed ? null : field(tr('Mot de passe actuel'), pinPass)),
      h('div.actions-row',
        h('button.btn.primary', { onclick: async () => {
          try { await put('/api/me/pin', { pin: pin.value, password: pinPass.value }); await refreshMe(); repaintUser(); toast(tr('Code PIN enregistré'), 'ok'); rerender(); }
          catch (e) { toast(e.message, 'bad'); }
        } }, tr('Enregistrer le code')),
        me.user.has_pin ? h('button.btn', { onclick: async () => {
          try { await put('/api/me/pin', { pin: '', password: pinPass.value }); await refreshMe(); repaintUser(); toast(tr('Code PIN retiré'), 'ok'); rerender(); }
          catch (e) { toast(e.message, 'bad'); }
        } }, tr('Retirer le code')) : null)) : null;

    const cur = h('input', { type: 'password', autocomplete: 'current-password' });
    const nw = h('input', { type: 'password', autocomplete: 'new-password' });
    const nw2 = h('input', { type: 'password', autocomplete: 'new-password' });
    const security = me.user.managed ? null : h('section.panel', h('h2', tr('Mot de passe')),
      h('div.grid3', field(tr('Actuel'), cur), field(tr('Nouveau'), nw), field(tr('Confirmation'), nw2)),
      h('button.btn', { onclick: async () => {
        if (nw.value !== nw2.value) { toast(tr('Les mots de passe ne correspondent pas'), 'bad'); return; }
        try {
          await put('/api/me/password', { current: cur.value, new: nw.value });
          cur.value = nw.value = nw2.value = '';
          toast(tr('Mot de passe modifié. Les autres sessions ont été déconnectées.'), 'ok');
        } catch (e) { toast(e.message, 'bad'); }
      } }, tr('Changer le mot de passe')));
    // langue de l'interface, propre à ce compte (les noms des langues restent dans leur propre langue)
    const language = select(LANGUAGES.map((l) => [l.id, l.name]), lang, { 'aria-label': 'Langue / Language',
      onchange: async (e) => {
        try {
          await put('/api/me/language', { language: e.target.value });
          useLanguage(e.target.value); // la page se recharge dans la nouvelle langue
        } catch (err) { toast(err.message, 'bad'); }
      } });
    const languagePanel = h('section.panel', h('h2', tr('Langue')), field('Langue / Language', language,
      tr('Langue des menus et des messages de Nexis, pour ce compte sur tous les appareils.')));
    // tous les réglages du profil : les mêmes sur chaque serveur lié ; retour aux valeurs par défaut
    const resetPanel = h('section.panel', h('h2', tr('Mes réglages')),
      h('p.muted.small', tr('Vos réglages (apparence, langues, lecture, accueil et menu) sont propres à votre profil et vous suivent sur tous les serveurs liés. Ceux de « Cet appareil » restent propres à ce navigateur.')),
      h('button.btn', { onclick: async () => {
        if (!await confirmDialog(tr('Revenir aux réglages par défaut ?'), tr('Apparence, langues, lecture, accueil et menu reprennent leurs valeurs de départ, sur tous les serveurs liés.'), tr('Réinitialiser'))) return;
        try {
          const r = await post('/api/me/prefs/reset');
          state.me.prefs = r;
          applyPrefs(r);
          repaintNav();
          toast(tr('Réglages par défaut rétablis'), 'ok');
          rerender();
        } catch (e) { toast(e.message, 'bad'); }
      } }, tr('Revenir aux réglages par défaut')));
    clear(body, profile, languagePanel, pinPanel, security, me.user.managed ? null : twoFactorPanel(rerender), resetPanel);
    return;
  }

  // langues propres à une bibliothèque : par exemple les animes en VO sous-titrée, les films en VF
  const libLangsPanel = () => {
    const libs = me.libraries.filter((l) => ['movie', 'show'].includes(l.type));
    if (!libs.length) return null;
    const cur = { ...(p.lib_langs || {}) };
    const rowFor = (l) => {
      const o = cur[l.id] || {};
      const set = (k, v) => {
        const next = { ...(cur[l.id] || {}) };
        if (v === '' || v == null) delete next[k]; else next[k] = v;
        if (Object.keys(next).length) cur[l.id] = next; else delete cur[l.id];
        save({ lib_langs: { ...cur } }, tr('Langues de « {name} » enregistrées', { name: l.title }));
      };
      const same = [['', tr('Comme ailleurs')]];
      return h('div.lib-lang-row', h('strong', l.title),
        h('div.grid3',
          field(tr('Audio'), select([...same, ...LANGS.filter(([v]) => v)], o.audio_lang || '', { onchange: (e) => set('audio_lang', e.target.value) })),
          field(tr('Sous-titres'), select([...same, ['foreign', tr('Si l’audio n’est pas dans ma langue')], ['always', tr('Toujours')], ['never', tr('Jamais')]],
            o.sub_mode || '', { onchange: (e) => set('sub_mode', e.target.value) })),
          field(tr('Langue des sous-titres'), select([...same, ...LANGS.filter(([v]) => v)], o.sub_lang || '', { onchange: (e) => set('sub_lang', e.target.value) }))));
    };
    return h('section.panel', h('h2', tr('Langues par bibliothèque')),
      h('p.muted.small', tr('Pour une bibliothèque, d’autres langues que celles du profil : par exemple les animes en japonais sous-titré, les films en français. « Comme ailleurs » garde le réglage général. Le choix fait pour une série ou un film reste prioritaire.')),
      libs.map(rowFor));
  };

  if (id === 'playback') {
    // apparence des sous-titres, avec un aperçu qui suit chaque réglage
    const subStylePanel = () => {
      const sample = h('div.sub-layer', h('span.sub-line', tr('Aperçu des sous-titres\nsur deux lignes')));
      const preview = h('div.sub-preview', { 'aria-hidden': 'true' }, sample);
      const paint = () => {
        applySubStyle(preview, p);
        sample.style.fontSize = `${(p.sub_size / 100) * 18}px`;
      };
      const set = (patch) => { Object.assign(p, patch); paint(); save(patch, tr('Sous-titres enregistrés')); };
      const opt = (key, table) => select(Object.entries(table).map(([k, v]) => [k, v[0]]), p[key] || Object.keys(table)[0],
        { onchange: (e) => set({ [key]: e.target.value }) });
      const subSize = h('input', { type: 'range', min: 60, max: 200, step: 10, value: p.sub_size });
      const subSizeLabel = h('span.muted', `${p.sub_size} %`);
      subSize.addEventListener('input', () => { subSizeLabel.textContent = `${subSize.value} %`; p.sub_size = Number(subSize.value); paint(); });
      subSize.addEventListener('change', () => save({ sub_size: Number(subSize.value) }, tr('Sous-titres enregistrés')));
      const colors = h('div.swatches', SUB_COLORS.map((c) => h('button.swatch', {
        type: 'button', style: { background: c }, 'aria-label': tr('Couleur {c}', { c }), class: c === (p.sub_color || '#ffffff') ? 'swatch on' : 'swatch',
        onclick: (e) => {
          colors.querySelectorAll('.swatch').forEach((x) => x.classList.remove('on'));
          e.currentTarget.classList.add('on');
          set({ sub_color: c });
        } })));
      paint();
      return h('section.panel', h('h2', tr('Apparence des sous-titres')),
        h('p.muted.small', tr('S’applique aux sous-titres texte (SRT, WebVTT, ASS simplifiés). Les sous-titres ASS stylés et les PGS gardent leur apparence d’origine.')),
        preview,
        h('div.grid2',
          field(tr('Taille'), h('div.inline', subSize, subSizeLabel)),
          field(tr('Police'), opt('sub_font', SUB_FONTS)),
          field(tr('Fond'), opt('sub_bg', SUB_BGS)),
          field(tr('Bordure du texte'), opt('sub_edge', SUB_EDGES)),
          field(tr('Position'), opt('sub_position', SUB_POSITIONS))),
        field(tr('Couleur du texte'), colors));
    };
    clear(body,
      h('section.panel', h('h2', tr('Qualité et langue')),
        h('div.grid2',
          field(tr('Qualité par défaut'), bindSelect('quality', [['auto', tr('Automatique (selon le débit)')], ['original', tr('Originale (le moins de transcodage)')],
            ['1080p', '1080p · 10 Mb/s'], ['720p', '720p · 4 Mb/s'], ['480p', '480p · 1,5 Mb/s']]),
          tr('« Automatique » mesure le débit de l’appareil à chaque lecture : l’original quand la connexion suffit, sinon la meilleure qualité qui passe.')),
          field(tr('Langue audio préférée'), bindSelect('audio_lang', LANGS)))),
      h('section.panel', h('h2', tr('Sous-titres')),
        h('div.grid2',
          field(tr('Afficher les sous-titres'), bindSelect('sub_mode', [['foreign', tr('Si l’audio n’est pas dans ma langue')],
            ['always', tr('Toujours')], ['never', tr('Jamais')]])),
          field(tr('Langue des sous-titres'), bindSelect('sub_lang', LANGS)))),
      subStylePanel(),
      h('section.panel', h('h2', tr('Séries')),
        bindToggle('autoplay_next', tr('Enchaîner automatiquement l’épisode suivant')),
        field(tr('Délai avant l’épisode suivant'), bindSelect('next_delay', [0, 3, 5, 10, 15, 20, 30].map((n) => [String(n), n ? tr('{n} secondes', { n }) : tr('Immédiatement')]), Number),
          tr('Un compte à rebours s’affiche à la fin de l’épisode, avec « Lire maintenant » et « Annuler ». Pendant le générique, l’épisode suivant est seulement proposé.')),
        bindToggle('auto_skip_intro', tr('Passer les génériques d’ouverture automatiquement')),
        bindToggle('auto_skip_recap', tr('Passer les récapitulatifs (« Précédemment… ») automatiquement')),
        bindToggle('hide_spoilers', tr('Anti-spoiler : flouter les vignettes et masquer les résumés des épisodes non vus')),
        h('p.field-hint.toggle-hint', tr('Un clic sur un résumé masqué l’affiche.'))),
      h('section.panel', h('h2', tr('Lecteur')),
        h('div.grid2',
          field(tr('Bouton reculer'), bindSelect('skip_back', [5, 10, 15, 30].map((n) => [String(n), tr('{n} secondes', { n })]), Number),
            tr('Aussi les flèches gauche et droite du clavier.')),
          field(tr('Bouton avancer'), bindSelect('skip_forward', [10, 15, 30, 60, 90].map((n) => [String(n), tr('{n} secondes', { n })]), Number))),
        field(tr('Vitesse de départ'), bindSelect('default_speed', [0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2].map((v) => [String(v), v === 1 ? tr('Normale') : `×${String(v).replace('.', ',')}`]), Number)),
        bindToggle('remember_volume', tr('Garder le volume d’une lecture à l’autre')),
        bindToggle('night_audio', tr('Son « nuit » : dialogues renforcés, explosions et musique atténuées (son 5.1 / 7.1 converti)'))),
      libLangsPanel());
    return;
  }

  if (id === 'privacy') {
    // genres masqués : bascule par genre (tous les genres des bibliothèques du compte)
    const hidden = new Set(p.hidden_genres || []);
    const genreBox = h('div.chips', spinner());
    get('/api/me/genres').then((gs) => {
      const names = [...new Set([...gs.map((g) => g.name), ...hidden])].sort((a, b) => a.localeCompare(b, locale()));
      clear(genreBox, names.length ? names.map((g) => h('button.chip.chip-toggle', { type: 'button', class: hidden.has(g) ? 'chip chip-toggle on' : 'chip chip-toggle',
        'aria-pressed': String(hidden.has(g)), onclick: async (e) => {
          const b = e.currentTarget;
          if (hidden.has(g)) hidden.delete(g); else if (hidden.size >= 40) { toast(tr('40 genres masqués au maximum'), 'bad'); return; } else hidden.add(g);
          b.classList.toggle('on', hidden.has(g));
          b.setAttribute('aria-pressed', String(hidden.has(g)));
          await save({ hidden_genres: [...hidden] }, hidden.has(g) ? tr('Genre « {g} » masqué', { g }) : tr('Genre « {g} » de nouveau visible', { g }));
        } }, g)) : h('p.muted.small', tr('Aucun genre dans vos bibliothèques.')));
    }).catch((e) => clear(genreBox, h('p.muted.small', e.message)));
    // heures calmes : "HH:MM-HH:MM", vide = jamais
    const [qa, qb] = (p.quiet_hours || '22:00-08:00').split('-');
    const from = h('input', { type: 'time', value: qa, disabled: !p.quiet_hours });
    const to = h('input', { type: 'time', value: qb, disabled: !p.quiet_hours });
    const quietSave = () => {
      if (!from.value || !to.value || from.value === to.value) { toast(tr('Choisissez deux heures différentes'), 'bad'); return; }
      save({ quiet_hours: `${from.value}-${to.value}` }, tr('Heures calmes enregistrées'));
    };
    from.addEventListener('change', quietSave);
    to.addEventListener('change', quietSave);
    const quiet = toggle(tr('Heures calmes : pas de notification sur les appareils pendant cette plage'), !!p.quiet_hours, { onchange: (e) => {
      from.disabled = to.disabled = !e.target.checked;
      if (e.target.checked) quietSave(); else save({ quiet_hours: '' }, tr('Heures calmes désactivées'));
    } });
    const mutedList = h('div.user-list');
    const paintMuted = async () => {
      const list = await get('/api/me/muted').catch(() => []);
      clear(mutedList, list.length ? list.map((c) => h('div.user-row', h('a.grow', { href: `#/item/${c.id}` }, h('strong', c.title),
        c.year ? h('span.muted', ` (${c.year})`) : null),
      h('button.btn.small', { onclick: async () => {
        await save({ muted_shows: (me.prefs.muted_shows || []).filter((x) => x !== String(c.id)) }, tr('Vous serez prévenu des nouveaux épisodes'));
        paintMuted();
      } }, tr('Réactiver')))) : h('p.muted.small', tr('Aucune série en sourdine.')));
    };
    paintMuted();
    clear(body,
      h('section.panel', h('h2', tr('Genres masqués')),
        h('p.muted.small', tr('Les films et séries de ces genres n’apparaissent plus pour vous : bibliothèques, accueil, recherche et suggestions. Leur fiche reste accessible par un lien direct.')),
        genreBox),
      h('section.panel', h('h2', tr('Mode privé')),
        h('p.muted.small', tr('Votre progression reste enregistrée (pour reprendre où vous en étiez), mais rien n’est ajouté à votre historique, rien n’est envoyé à Trakt, et les administrateurs voient « Lecture privée » au lieu du titre.')),
        bindToggle('private_mode', tr('Activer le mode privé'))),
      h('section.panel', h('h2', tr('Notifications')),
        h('p.muted.small', tr('Ce que Nexis peut vous signaler. Les notifications s’activent appareil par appareil dans « Cet appareil » ; un titre demandé qui arrive est toujours signalé.')),
        bindToggle('notify_episodes', tr('Nouveaux épisodes des séries que je suis')),
        bindToggle('notify_podcasts', tr('Nouveaux épisodes des podcasts auxquels je suis abonné')),
        quiet, h('div.grid2', field(tr('De'), from), field(tr('À'), to)),
        h('p.muted.small', tr('Pendant les heures calmes, les notifications restent dans la cloche de Nexis mais ne font pas sonner vos appareils. Heure du serveur.'))),
      h('section.panel', h('h2', tr('Séries en sourdine')),
        h('p.muted.small', tr('Pas de notification pour ces séries (menu ⋯ d’une série → Ne plus être prévenu des nouveaux épisodes).')),
        mutedList));
    return;
  }

  if (id === 'device') {
    const d = device();
    const set = (patch, msg = tr('Réglage enregistré sur cet appareil')) => { setDevice(patch); toast(msg, 'ok'); };
    const pick = (key, options) => select(options, d[key] || '', { onchange: (e) => set({ [key]: e.target.value }) });
    clear(body,
      h('section.panel', h('h2', tr('Lecture sur cet appareil')),
        h('div.grid2',
          field(tr('Qualité'), pick('quality', [['', tr('Selon le profil ({p1})', { p1: ({ auto: tr('automatique'), original: tr('originale'), '1080p': '1080p', '720p': '720p', '480p': '480p' })[p.quality] || p.quality })],
            ['auto', tr('Automatique')], ['original', tr('Originale')], ['1080p', '1080p'], ['720p', '720p'], ['480p', '480p']]),
          tr('Utile pour réduire la qualité sur un téléphone en données mobiles.')),
          field(tr('Taille des sous-titres'), pick('sub_size', [['', tr('Selon le profil ({sub_size} %)', { sub_size: p.sub_size })],
            ...[80, 100, 120, 140, 160, 180, 200].map((n) => [String(n), `${n} %`])]), tr('Une TV regardée de loin demande souvent des sous-titres plus grands.')))),
      h('section.panel', h('h2', tr('Affichage et navigation')),
        h('div.grid2',
          field(tr('Animations'), pick('motion', [['', tr('Selon le système')], ['reduced', tr('Réduites')]])),
          field(tr('Navigation à la télécommande'), pick('remote', [['', tr('Automatique')], ['on', tr('Toujours activée')], ['off', tr('Désactivée')]]),
            tr('Repères de sélection visibles et déplacement aux flèches, comme sur une TV.')))),
      pushPanel(),
      h('section.panel', h('h2', tr('Télécommande')),
        field(tr('Nom de cet appareil'), h('input', { value: d.name || '', maxlength: 60, placeholder: deviceName(),
          onchange: (e) => set({ name: e.target.value.trim() || undefined }) }),
        tr('Nom affiché dans la page Télécommande des autres appareils quand celui-ci lit une vidéo (ex. « TV du salon »).'))),
      shortcutsPanel(),
      h('p.muted.small', tr('Ces réglages sont enregistrés dans ce navigateur uniquement.')));
    return;
  }

  if (id === 'services') {
    servicesPage(body);
    return;
  }

  if (id === 'appearance') {
    // thèmes : aperçu en vignette (fond, surface, texte, accent)
    const THEMES = [['dark', tr('Sombre'), '#0b0b0e', '#1c1c22', '#f5f3ef'], ['oled', tr('Noir profond (OLED)'), '#000', '#161619', '#f5f3ef'],
      ['midnight', tr('Bleu nuit'), '#0a0f1c', '#19243f', '#eef2fb'], ['light', tr('Clair'), '#f5f3ee', '#ffffff', '#16161a'],
      ['sepia', tr('Sépia'), '#f1e7d6', '#fbf5ea', '#3b2f22'], ['contrast', tr('Contraste élevé'), '#000', '#1b1b1b', '#ffffff'],
      ['auto', tr('Automatique (comme l’appareil)'), 'linear-gradient(135deg, #0b0b0e 50%, #f5f3ee 50%)', '#888', '#888']];
    const themeTiles = h('div.theme-tiles', THEMES.map(([v, label, bg, surf, text]) => h('button.theme-tile', {
      type: 'button', 'aria-pressed': String(p.theme === v), title: label,
      onclick: async () => { await save({ theme: v }, tr('Thème appliqué')); rerender(); } },
    h('span.theme-preview', { style: `background:${bg}` }, h('span', { style: `background:${surf}` }, h('i', { style: `background:${text}` }), h('b', { style: 'background:var(--accent)' }))),
    h('span.theme-name', label))));
    const picker = h('input.color-input', { type: 'color', value: p.accent, title: tr('Autre couleur'),
      onchange: (e) => save({ accent: e.target.value }, tr('Couleur appliquée')) });
    const cardToggle = (key, label) => bindToggle(key, label);
    // fond d'écran
    const MODES = [['none', tr('Aucun')], ['title', tr('L’image d’un titre')], ['daily', tr('Un de vos titres, différent chaque jour')],
      ['gradient', tr('Un dégradé')], ['color', tr('Une couleur')], ['upload', tr('Une image à vous')]];
    const mode = p.wallpaper_mode === 'title' && !p.wallpaper ? 'none' : p.wallpaper_mode;
    const modeSel = select(MODES, mode, { onchange: async (e) => { await save({ wallpaper_mode: e.target.value }, tr('Fond d’écran appliqué')); rerender(); } });
    let detail = null;
    if (mode === 'title') {
      detail = h('div', p.wallpaper ? h('div.wallpaper-preview', { style: `background-image: url("${img(p.wallpaper, 480, 270)}")` }) : null,
        h('p.muted.small', tr('Sur la fiche d’un film ou d’une série, menu ⋯ puis « Utiliser comme fond d’écran ».')));
    } else if (mode === 'none') {
      detail = h('p.muted.small', tr('Le fond de la page est celui du thème.'));
    } else if (mode === 'daily') {
      detail = h('p.muted.small', tr('Chaque jour, l’image d’un titre de votre liste « À regarder », de ceux que vous avez notés 4 ou 5 étoiles, ou regardés récemment.'));
    } else if (mode === 'gradient') {
      detail = h('div.swatches', Object.entries(GRADIENTS).map(([k, css]) => h('button.swatch.grad', { type: 'button', title: k, style: `background:${css}`,
        'aria-pressed': String(p.wallpaper_gradient === k), onclick: () => save({ wallpaper_gradient: k }, tr('Fond d’écran appliqué')) })));
    } else if (mode === 'color') {
      detail = h('input.color-input', { type: 'color', value: p.wallpaper_color, onchange: (e) => save({ wallpaper_color: e.target.value }, tr('Fond d’écran appliqué')) });
    } else if (mode === 'upload') {
      const file = h('input', { type: 'file', accept: 'image/jpeg,image/png,image/webp', hidden: true, onchange: async () => {
        const f = file.files[0];
        if (!f) return;
        try {
          const r = await fetch('/api/me/wallpaper', { method: 'POST', body: f, credentials: 'same-origin', headers: { 'X-Seance': '1', 'X-Lang': lang } });
          const j = await r.json();
          if (!r.ok) throw new Error(j.error || r.statusText);
          state.wallVersion = j.wallpaper_version;
          Object.assign(me.prefs, j);
          applyPrefs(me.prefs);
          toast(tr('Fond d’écran appliqué'), 'ok');
          rerender();
        } catch (e) { toast(e.message, 'bad'); }
      } });
      detail = h('div', h('div.wallpaper-preview', { style: `background-image: url("/api/me/wallpaper/image?v=${state.wallVersion || 0}")` }),
        h('div.actions-row', h('button.btn', { onclick: () => file.click() }, tr('Choisir une image…')), file),
        h('p.muted.small', tr('JPEG, PNG ou WebP, 8 Mo au plus. L’image reste sur ce serveur.')));
    }
    clear(body,
      h('section.panel', h('h2', tr('Thème')), themeTiles),
      h('section.panel', h('h2', tr('Couleur d’accent')),
        h('div.accent-row', swatches(p.accent, (c) => save({ accent: c }, tr('Couleur appliquée'))), h('label.color-pick', picker, tr('Autre couleur…'))),
        h('p.muted.small', tr('Boutons, liens actifs, barres de progression.'))),
      h('section.panel', h('h2', tr('Taille et densité')),
        h('div.grid2',
          field(tr('Taille du texte'), bindSelect('text_size', [['s', tr('Petite')], ['m', tr('Normale')], ['l', tr('Grande')], ['xl', tr('Très grande')]])),
          field(tr('Densité'), bindSelect('density', [['compact', tr('Compacte')], ['normal', tr('Normale')], ['comfy', tr('Aérée')]]))),
        field(tr('Taille des affiches'), bindSelect('poster_size', [['s', tr('Petites')], ['m', tr('Moyennes')], ['l', tr('Grandes')]]))),
      h('section.panel', h('h2', tr('Cartes des films et séries')),
        cardToggle('card_titles', tr('Titre et année sous l’affiche')),
        cardToggle('card_progress', tr('Barre de progression')),
        cardToggle('card_watched', tr('Coche « vu »')),
        cardToggle('card_quality', tr('Badges 4K et HDR')),
        cardToggle('card_new', tr('Badge « Nouveau » (ajouts de la semaine)')),
        cardToggle('card_rating', tr('Note TMDB'))),
      h('section.panel', h('h2', tr('Fond d’écran')), field(tr('Derrière les pages'), modeSel), detail));
    return;
  }

  // --- accueil et barre du haut : ordre et rangées affichées
  const hidden = new Set(p.hidden_libraries);
  const homeHidden = new Set(p.home_hidden);
  const homeItems = [
    { key: 'together', label: tr('Regarder ensemble'), hint: tr('quand un groupe est ouvert') },
    { key: 'continue', label: tr('Reprendre et à suivre') },
    { key: 'watchlist', label: tr('À regarder') },
    { key: 'suggest', label: tr('Suggestions'), hint: tr('selon vos visionnages récents') },
    { key: 'because', label: tr('Parce que vous avez regardé…'), hint: tr('titres proches de vos derniers visionnages') },
    { key: 'short', label: tr('Pour une soirée courte'), hint: tr('films de moins de 1 h 30 pas encore vus') },
    { key: 'unfinished', label: tr('Séries à reprendre'), hint: tr('commencées puis laissées de côté depuis 2 mois') },
    ...me.libraries.map((l) => ({ key: `lib:${l.id}`, label: l.title, hint: l.type === 'show' ? tr('nouveaux épisodes') : tr('ajoutés récemment') })),
    ...pins().map((pn) => ({ key: `pin:${pn.id}`, label: pn.title, hint: tr('rangée épinglée ({p1})', { p1: PIN_LABELS[pn.type] }),
      remove: async () => { try { await removePin(pn.id); rerender(); } catch (e) { toast(e.message, 'bad'); } } })),
  ];
  const homeById = new Map(homeItems.map((i) => [i.key, i]));
  const homeOn = (k) => (k === 'continue' ? p.show_continue : k.startsWith('lib:') ? !hidden.has(k.slice(4)) : !homeHidden.has(k));
  const homeShow = (k, on) => {
    if (k === 'continue') { p.show_continue = on; save({ show_continue: on }); return; }
    if (k.startsWith('lib:')) {
      if (on) hidden.delete(k.slice(4)); else hidden.add(k.slice(4));
      save({ hidden_libraries: [...hidden] });
      return;
    }
    if (on) homeHidden.delete(k); else homeHidden.add(k);
    save({ home_hidden: [...homeHidden] });
  };
  const navHidden = new Set(p.nav_hidden);
  const layoutPanel = (title, lead, list, resetPatch) => h('section.panel', h('h2', title), h('p.muted.small', lead), list(),
    h('button.btn.ghost', { type: 'button', onclick: async () => { await save(resetPatch, tr('Ordre par défaut rétabli')); rerender(); } },
      tr('Rétablir l’ordre par défaut')));
  const homePanel = layoutPanel(tr('Accueil'), tr('Faites glisser les rangées ou utilisez les flèches pour les réordonner. L’interrupteur affiche ou masque chaque rangée. Pour épingler une rangée : bouton « Épingler à l’accueil » d’une collection, d’une playlist ou d’un genre filtré dans une bibliothèque.'),
    () => layoutList(arrange(homeItems.map((i) => i.key), p.home_order).map((k) => homeById.get(k)), homeOn,
      (keys) => save({ home_order: keys }, tr('Ordre de l’accueil enregistré')), homeShow),
    { home_order: [] });
  const navPanel = layoutPanel(tr('Barre du haut'), tr('Ordre des liens de la barre du haut. Les liens masqués restent accessibles depuis l’accueil.'),
    () => layoutList(navItems(me), (k) => !navHidden.has(k),
      async (keys) => { await save({ nav_order: keys }, tr('Barre du haut enregistrée')); repaintNav(); },
      async (k, on) => { if (on) navHidden.delete(k); else navHidden.add(k); await save({ nav_hidden: [...navHidden] }, tr('Barre du haut enregistrée')); repaintNav(); }),
    { nav_order: [] });
  const startSel = select(navItems(me).map((i) => [i.key, i.label]), p.start_page || 'home',
    { onchange: (e) => save({ start_page: e.target.value }, tr('Page d’ouverture enregistrée')) });
  const startPanel = h('section.panel', h('h2', tr('Page d’ouverture')),
    field(tr('Page affichée à l’ouverture de Nexis'), startSel, tr('Les liens directs (fiche, lecture) s’ouvrent toujours à leur adresse.')));
  // titres masqués pour soi (menu ⋯ d'une fiche) : la liste, pour les rendre de nouveau visibles
  const hiddenList = h('div.user-list');
  const hiddenPanel = h('section.panel', h('h2', tr('Titres masqués')),
    h('p.muted.small', tr('Masqués par vous seul (menu ⋯ d’une fiche → Masquer pour moi) : ils n’apparaissent plus dans vos bibliothèques, l’accueil, la recherche ni les suggestions.')),
    hiddenList);
  const paintHidden = async () => {
    const list = await get('/api/me/hidden').catch(() => []);
    clear(hiddenList, list.length ? list.map((c) => h('div.user-row', h('a.grow', { href: `#/item/${c.id}` }, h('strong', c.title),
      c.year ? h('span.muted', ` (${c.year})`) : null),
    h('button.btn.small', { onclick: async () => { await put(`/api/items/${c.id}/hidden`, { hidden: false }); paintHidden(); } }, tr('Ne plus masquer'))))
      : h('p.muted.small', tr('Aucun titre masqué.')));
  };
  paintHidden();
  const heroPanel = h('section.panel', h('h2', tr('Grande bannière')),
    field(tr('En haut de l’accueil'), bindSelect('home_hero', [['auto', tr('Reprise en cours, sinon un ajout récent')], ['resume', tr('Reprise en cours seulement')],
      ['recent', tr('Un ajout récent')], ['random', tr('Un titre au hasard (change toutes les heures)')], ['watchlist', tr('Un titre de ma liste « À regarder »')],
      ['none', tr('Pas de bannière')]])));
  const rowsPanel = h('section.panel', h('h2', tr('Mes rangées')),
    h('p.muted.small', tr('Des rangées à vous sur l’accueil : une bibliothèque, des filtres, un tri et un nom. Elles se réordonnent avec les autres ci-dessous.')),
    pins().filter((pn) => ['filter', 'genre'].includes(pn.type)).map((pn) => h('div.user-row', h('div.grow', h('strong', pn.title),
      h('div.muted.small', tr('{n} titres', { n: pn.count || 24 }))),
    h('button.btn.small', { onclick: async () => { if (await rowDialog(pn)) rerender(); } }, tr('Modifier')),
    h('button.icon-btn', { title: tr('Supprimer'), onclick: async () => { await removePin(pn.id); rerender(); } }, icon('trash')))),
    h('button.btn', { onclick: async () => { if (await rowDialog(null)) rerender(); } }, tr('+ Nouvelle rangée')));
  // création / modification d'une rangée : bibliothèque, filtres, tri, nombre de titres, nom
  const rowDialog = async (pn) => {
    const libs = me.libraries.filter((l) => ['movie', 'show'].includes(l.type));
    if (!libs.length) { toast(tr('Aucune bibliothèque de films ou de séries'), 'bad'); return false; }
    const q = new URLSearchParams(pn ? (pn.type === 'genre' ? `genre=${encodeURIComponent(pn.ref)}` : pn.ref) : '');
    const libSel = select(libs.map((l) => [l.id, l.title]), pn ? pn.lib : libs[0].id);
    const genre = select([['', tr('Tous les genres')]], q.get('genre') || '');
    const loadGenres = async () => {
      try {
        const gs = await get(`/api/libraries/${libSel.value}/genres`);
        genre.replaceChildren(...[['', tr('Tous les genres')], ...gs.map((g) => [g.id, g.title])]
          .map(([v, l]) => h('option', { value: v, selected: v === (q.get('genre') || '') }, l)));
      } catch { /* sans genres */ }
    };
    libSel.addEventListener('change', loadGenres);
    loadGenres();
    const watched = select([['', tr('Vus et non vus')], ['unwatched', tr('Pas encore vus')], ['watched', tr('Déjà vus')]], q.get('watched') || '');
    const decade = select([['', tr('Toutes les années')], ...[2020, 2010, 2000, 1990, 1980, 1970, 1960].map((d) => [String(d), tr('Années {d}', { d })])], q.get('decade') || '');
    const sort = select([['added', tr('Ajout récent')], ['title', tr('Titre')], ['year', tr('Année')], ['rating', tr('Note')], ['release', tr('Date de sortie')]], q.get('sort') || 'added');
    const count = select([12, 24, 36, 48].map((n) => [String(n), String(n)]), String((pn && pn.count) || 24));
    const name = h('input', { value: pn ? pn.title : '', maxlength: 100, placeholder: tr('Ex. Comédies des années 90 pas encore vues') });
    const err = h('p.form-error');
    return modal(pn ? tr('Modifier la rangée') : tr('Nouvelle rangée'), h('div.form',
      field(tr('Nom'), name), h('div.grid2', field(tr('Bibliothèque'), libSel), field(tr('Genre'), genre)),
      h('div.grid2', field(tr('Visionnage'), watched), field(tr('Années'), decade)),
      h('div.grid2', field(tr('Tri'), sort), field(tr('Nombre de titres'), count)), err),
    [{ label: tr('Annuler'), value: false }, { label: pn ? tr('Enregistrer') : tr('Ajouter'), primary: true, handler: async () => {
      const f = new URLSearchParams();
      for (const [k, el] of [['genre', genre], ['watched', watched], ['decade', decade], ['sort', sort]]) if (el.value) f.set(k, el.value);
      const lib = libs.find((l) => String(l.id) === String(libSel.value));
      const title = name.value.trim() || [genre.value, decade.value && tr('Années {d}', { d: decade.value }), lib.title].filter(Boolean).join(' · ');
      const list = pins().filter((x) => !pn || x.id !== pn.id);
      if (!pn && list.length >= 20) { err.textContent = tr('20 rangées épinglées au maximum'); return false; }
      const id = pn ? pn.id : Math.max(0, ...pins().map((x) => x.id + 1));
      const entry = { id, type: 'filter', ref: f.toString() || 'sort=added', lib: String(lib.id), title: title.slice(0, 100), count: Number(count.value) };
      try {
        const r = await put('/api/me/prefs', { home_pins: [...list, entry] });
        Object.assign(me.prefs, r);
        toast(pn ? tr('Rangée modifiée') : tr('Rangée ajoutée à l’accueil'), 'ok');
        return true;
      } catch (e) { err.textContent = e.message; return false; }
    } }]);
  };
  clear(body, startPanel, heroPanel, rowsPanel, homePanel, navPanel, hiddenPanel);
}

// ---------------------------------------------------------------- l'espace Paramètres
// Un seul endroit pour tout régler : menu groupé à gauche (sur téléphone : le menu, puis la page choisie),
// une page par sujet. Réglages personnels sous /settings/…, réglages du serveur sous /admin/….
export async function renderSettingsArea(main, area, section, ctx) {
  const me = state.me;
  const groups = [{ ...ACCOUNT_GROUP, area: 'settings' },
    ...(me.user.is_admin ? ADMIN_GROUPS.map((g) => ({ ...g, area: 'admin' })) : [])];
  if (area === 'admin') section = ADMIN_ALIASES[section] || section;
  const all = groups.flatMap((g) => g.items.map((it) => ({ ...it, area: g.area })));
  const chosen = section ? all.find((it) => it.area === area && it.id === section) : null;
  const current = chosen || all.find((it) => it.area === area) || all[0];

  const search = h('input.settings-search', { type: 'search', placeholder: tr('Rechercher un réglage…'), 'aria-label': tr('Rechercher un réglage') });
  const nav = h('nav.settings-nav', { 'aria-label': tr('Sections des paramètres') }, search,
    groups.map((g) => h('div.settings-group', h('div.settings-group-title', g.title),
      g.items.map((it) => {
        const on = it.id === current.id && g.area === current.area;
        const badge = g.area === 'admin' && it.id === 'update' && me.update
          ? h('span.settings-badge', { title: tr('Nexis {update} est disponible', { update: me.update }) }, tr('Nouveau')) : null;
        return h('a.settings-link', { href: `#/${g.area}/${it.id}`, dataset: { keys: `${it.label} ${it.lead} ${it.keys}` },
          class: on ? 'settings-link active' : 'settings-link', 'aria-current': on ? 'page' : null }, icon(it.icon), h('span', it.label), badge);
      }))),
    h('p.settings-empty.muted.small', { hidden: true }, tr('Aucun réglage ne correspond.')));
  const fold = (t) => t.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  search.addEventListener('input', () => {
    const words = fold(search.value).split(/\s+/).filter(Boolean);
    let shown = 0;
    nav.querySelectorAll('.settings-group').forEach((g) => {
      let any = false;
      g.querySelectorAll('.settings-link').forEach((a) => {
        const ok = words.every((w) => fold(a.dataset.keys).includes(w));
        a.hidden = !ok;
        any = any || ok;
        shown += ok ? 1 : 0;
      });
      g.hidden = !any;
    });
    nav.querySelector('.settings-empty').hidden = shown > 0;
  });
  search.addEventListener('keydown', (e) => { // Entrée : ouvre le premier résultat
    if (e.key !== 'Enter') return;
    const first = [...nav.querySelectorAll('.settings-link')].find((a) => !a.hidden);
    if (first) { e.preventDefault(); location.hash = first.getAttribute('href'); }
  });

  const body = h('div.settings-body');
  // administration des serveurs liés : choix du serveur réglé par les pages « Serveur », « Médiathèque », « Lecture »
  const serverBar = h('div.server-pick', { hidden: true });
  const content = h('section.settings-content',
    h('a.settings-back', { href: `#/${area}` }, icon('back'), tr('Paramètres')),
    current.area === 'admin' ? serverBar : null,
    h('header.settings-head', h('h2', current.label), h('p.muted', current.lead)),
    body);
  if (current.area === 'admin') {
    try {
      const sv = await get('/api/admin/servers');
      const t = adminTarget();
      if (t && !sv.peers.some((p) => p.id === t.id)) setAdminTarget(null);  // serveur délié depuis
      if (sv.peers.length) {
        const sel = select([['', tr('{name} (ce serveur)', { name: sv.local.name })],
          ...sv.peers.map((p) => [p.id, p.online ? p.name : tr('{name} (injoignable)', { name: p.name })])], (adminTarget() || {}).id || '');
        sel.addEventListener('change', () => {
          const p = sv.peers.find((x) => x.id === sel.value);
          setAdminTarget(p ? { id: p.id, name: p.name } : null);
          renderSettingsArea(main, area, section, ctx);
        });
        const remote = adminTarget();
        serverBar.className = remote ? 'server-pick remote' : 'server-pick';
        serverBar.replaceChildren(h('label', icon('link'), h('span', tr('Serveur administré')), sel),
          remote ? h('p.small', tr('Les réglages de ces pages s’appliquent à « {name} », relayés par ce serveur. Vos préférences (Mon compte) restent celles de ce serveur.', { name: remote.name })) : null);
        serverBar.hidden = false;
      }
    } catch { setAdminTarget(null); }
  }
  clear(main, h('div.settings-page', { class: section ? 'settings-page' : 'settings-page no-section' },
    h('div.page-head', h('h1', tr('Paramètres'))), h('div.settings-layout', nav, content)));
  document.title = tr('{label} · Paramètres · Nexis', { label: current.label });
  if (current.area === 'admin') await renderAdminSection(current.id, body, ctx);
  else accountPage(current.id, body, () => renderSettingsArea(main, area, section, ctx));
}
