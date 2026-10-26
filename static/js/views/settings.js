import { del, get, post, put } from '../api.js';
import { qrSvg } from '../qr.js';
import { ADMIN_ALIASES, ADMIN_GROUPS, renderAdminSection } from './admin.js';
import { applyPrefs, navItems, refreshMe, repaintNav, repaintUser, state } from '../app.js';
import { PIN_LABELS, pins, removePin } from '../pins.js';
import { SUB_BGS, SUB_COLORS, SUB_EDGES, SUB_FONTS, SUB_POSITIONS, applySubStyle } from '../substyle.js';
import { arrange, avatar, clear, field, h, icon, modal, select, spinner, toast, toggle } from '../ui.js';
import { ART, BACKGROUNDS, CATEGORIES, DEFAULT_BG, artDataUrl, artSvg } from '../avatar-art.js';
import { device, setDevice } from '../device.js';

const LANGS = [['fra', 'Français'], ['eng', 'Anglais'], ['jpn', 'Japonais'], ['spa', 'Espagnol'], ['deu', 'Allemand'],
  ['ita', 'Italien'], ['por', 'Portugais'], ['kor', 'Coréen'], ['zho', 'Chinois'], ['', 'Pas de préférence']];
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
      const sw = it.fixed ? h('span.muted.small', 'toujours affiché') : toggle('', isOn(k), { 'aria-label': `Afficher ${it.label}` });
      if (!it.fixed) sw.querySelector('input').addEventListener('change', (e) => { li.classList.toggle('off', !e.target.checked); onShow(k, e.target.checked); });
      const li = h('li.layout-item', { draggable: 'true', dataset: { key: k }, class: !it.fixed && !isOn(k) ? 'layout-item off' : 'layout-item' },
        h('span.layout-grip', { 'aria-hidden': 'true' }, '⋮⋮'),
        h('div.layout-label', it.label, it.hint ? h('span.muted.small', it.hint) : null),
        h('button.icon-btn', { type: 'button', 'aria-label': `Monter ${it.label}`, disabled: i === 0, onclick: () => move(k, -1) }, icon('up')),
        h('button.icon-btn', { type: 'button', 'aria-label': `Descendre ${it.label}`, disabled: i === keys.length - 1, onclick: () => move(k, 1) }, icon('down')),
        sw,
        it.remove ? h('button.icon-btn', { type: 'button', title: 'Retirer de l’accueil', 'aria-label': `Retirer ${it.label}`, onclick: it.remove }, icon('trash')) : null);
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
    if (fileObj.size > 30 * 1024 * 1024) { reject(new Error('Image trop lourde')); return; }
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
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Image illisible')); };
    img.src = url;
  });
}

// ---------------------------------------------------------------- double authentification
function showRecoveryCodes(codes) {
  const text = codes.join('\n');
  const file = `Nexis : codes de secours (${new Date().toLocaleDateString('fr-FR')})\nChaque code ne fonctionne qu'une fois.\n\n${text}\n`;
  return modal('Codes de secours', h('div.form',
    h('p.small', 'Ces codes remplacent le téléphone s’il est perdu ou indisponible. Chacun ne fonctionne qu’une fois. Rangez-les en lieu sûr (gestionnaire de mots de passe, impression) : ils ne seront plus affichés.'),
    h('ol.recovery-codes', codes.map((c) => h('li', h('code', c)))),
    h('div.actions-row',
      h('button.btn.small', { type: 'button', onclick: async () => {
        try { await navigator.clipboard.writeText(text); toast('Codes copiés', 'ok'); } catch { toast('Copie impossible : sélectionnez les codes', 'bad'); }
      } }, 'Copier'),
      h('a.btn.small', { href: URL.createObjectURL(new Blob([file], { type: 'text/plain' })), download: 'nexis-codes-de-secours.txt' }, 'Télécharger'))),
  [{ label: 'J’ai enregistré mes codes', primary: true, value: true }]);
}

async function enableTwoFactor(rerender) {
  const pass = h('input', { type: 'password', autocomplete: 'current-password' });
  const err = h('p.form-error');
  const setup = await modal('Activer la double authentification', h('div.form',
    h('p.small', 'À chaque connexion, un code à 6 chiffres généré par une application d’authentification sera demandé en plus du mot de passe (Google Authenticator, Microsoft Authenticator, Aegis, 2FAS, Bitwarden…).'),
    field('Mot de passe actuel', pass), err), [
    { label: 'Annuler', value: null },
    { label: 'Continuer', primary: true, handler: async () => {
      try { return await post('/api/me/2fa/setup', { password: pass.value }); } catch (e) { err.textContent = e.message; return false; }
    } },
  ]);
  if (!setup) return;
  const qrBox = h('div.qr-box', spinner());
  qrSvg(setup.uri, 200).then((svg) => qrBox.replaceChildren(svg)).catch(() => qrBox.replaceChildren(h('p.muted.small', 'QR code indisponible : saisissez la clé ci-dessous.')));
  const code = h('input', { inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 6, placeholder: '123456',
    style: { letterSpacing: '0.2em', fontSize: '20px' } });
  const err2 = h('p.form-error');
  const done = await modal('Scanner le QR code', h('div.form',
    h('p.small', '1. Dans l’application d’authentification, ajoutez un compte en scannant ce QR code.'),
    qrBox,
    h('details.small', h('summary', 'Impossible de scanner ? Saisir la clé'), h('p', h('code.secret-key', setup.secret.match(/.{1,4}/g).join(' '))),
      h('p.muted', 'Type : basé sur le temps (TOTP), 6 chiffres, 30 secondes.')),
    field('2. Code affiché par l’application', code), err2), [
    { label: 'Annuler', value: null },
    { label: 'Activer', primary: true, handler: async () => {
      try { return await post('/api/me/2fa/enable', { code: code.value }); } catch (e) { err2.textContent = e.message; return false; }
    } },
  ]);
  if (!done) return;
  toast('Double authentification activée', 'ok');
  await showRecoveryCodes(done.recovery_codes);
  await refreshMe();
  rerender();
}

async function confirmCode(title, lead, withPassword, url) {
  const pass = h('input', { type: 'password', autocomplete: 'current-password' });
  const code = h('input', { autocomplete: 'one-time-code', autocapitalize: 'none', maxlength: 9, placeholder: '123456' });
  const err = h('p.form-error');
  return modal(title, h('div.form', h('p.small', lead), withPassword ? field('Mot de passe actuel', pass) : null,
    field('Code de l’application (ou code de secours)', code), err), [
    { label: 'Annuler', value: null },
    { label: 'Confirmer', primary: true, handler: async () => {
      try { return await post(url, { password: pass.value, code: code.value }); } catch (e) { err.textContent = e.message; return false; }
    } },
  ]);
}

function twoFactorPanel(rerender) {
  const box = h('div', spinner());
  get('/api/me/2fa').then((st) => {
    if (!st.available) { box.replaceChildren(h('p.muted.small', 'Non disponible pour ce compte.')); return; }
    if (!st.enabled) {
      box.replaceChildren(
        h('div.status-box', 'Désactivée.'),
        h('p.muted.small', 'Recommandée pour tout compte accessible depuis Internet, en particulier les comptes administrateurs.'),
        h('button.btn.primary', { onclick: () => enableTwoFactor(rerender) }, 'Activer'));
      return;
    }
    box.replaceChildren(
      h('div.status-box.ok', 'Activée : un code est demandé à chaque connexion.'),
      h('p.muted.small', `Codes de secours restants : ${st.recovery_left} sur 10.`, st.recovery_left <= 3 ? ' Pensez à en générer de nouveaux.' : ''),
      h('div.actions-row',
        h('button.btn', { onclick: async () => {
          const r = await confirmCode('Nouveaux codes de secours', 'Les anciens codes de secours cesseront de fonctionner.', false, '/api/me/2fa/recovery');
          if (r) { await showRecoveryCodes(r.recovery_codes); rerender(); }
        } }, 'Générer de nouveaux codes de secours'),
        h('button.btn.ghost', { onclick: async () => {
          const r = await confirmCode('Désactiver la double authentification', 'Le mot de passe suffira de nouveau pour se connecter.', true, '/api/me/2fa/disable');
          if (r) { toast('Double authentification désactivée', 'ok'); await refreshMe(); rerender(); }
        } }, 'Désactiver')),
      h('p.muted.small', 'Téléphone perdu et plus de codes de secours : un administrateur peut la réinitialiser (Paramètres → Utilisateurs).'));
  }).catch((e) => box.replaceChildren(h('p.form-error.small', e.message)));
  return h('section.panel', h('h2', 'Double authentification'), box);
}

// ---------------------------------------------------------------- Mon compte
export const ACCOUNT_GROUP = { title: 'Mon compte', items: [
  { id: 'profile', label: 'Profil', icon: 'user', lead: 'Nom, photo, code PIN, mot de passe et double authentification.',
    keys: 'nom avatar photo couleur pin code mot de passe sécurité double authentification 2fa totp codes de secours' },
  { id: 'playback', label: 'Lecture', icon: 'play', lead: 'Qualité, langues, sous-titres et enchaînement des épisodes.',
    keys: 'qualité langue audio sous-titres taille police couleur style épisode suivant générique intro automatique spoiler anti-spoiler résumé' },
  { id: 'appearance', label: 'Apparence', icon: 'palette', lead: 'Thème, couleur d’accent et taille des affiches.',
    keys: 'thème sombre clair oled couleur accent affiches taille' },
  { id: 'device', label: 'Cet appareil', icon: 'tv', lead: 'Réglages propres à ce navigateur ou à cette TV, prioritaires sur ceux du profil.',
    keys: 'appareil tv téléphone qualité sous-titres taille animations mouvement télécommande manette navigateur' },
  { id: 'layout', label: 'Accueil et menu', icon: 'home', lead: 'Ordre et affichage des rangées de l’accueil et des liens de la barre du haut.',
    keys: 'accueil rangées ordre barre du haut menu liens masquer réorganiser épingler page d’ouverture démarrage' },
] };

function accountPage(id, body, rerender) {
  const me = state.me;
  const p = { ...me.prefs };

  const save = async (patch, msg = 'Préférences enregistrées') => {
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
      try { await uploadAvatar(await squareImage(f, 256), 'Photo de profil enregistrée'); }
      catch (e) { toast(e.message || 'Image illisible', 'bad'); }
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
        type: 'button', 'aria-label': `Fond ${i + 1}`, style: { background: `linear-gradient(135deg, ${a}, ${b})` },
        onclick: () => { bg = i; paintPreview(); } })));
      showCategory(CATEGORIES[0][0]);
      const ok = await modal('Choisir un avatar', h('div.avatar-picker',
        h('div.avatar-side', preview, label, h('div.field-label.small', 'Fond'), swatchRow),
        h('div.avatar-main', tabs, grid)), [
        { label: 'Annuler', value: null },
        { label: 'Utiliser cet avatar', primary: true, value: true },
      ]);
      if (!ok) return;
      try { await uploadAvatar(await artDataUrl(current, bg), 'Avatar enregistré'); } catch (e) { toast(e.message, 'bad'); }
    };
    const pictureActions = h('div.avatar-actions',
      h('button.btn.small', { type: 'button', onclick: () => file.click() }, me.user.avatar ? 'Changer la photo' : 'Choisir une photo'),
      h('button.btn.small', { type: 'button', onclick: choosePreset }, 'Avatars'),
      me.user.avatar ? h('button.btn.small.ghost', { type: 'button', onclick: async () => {
        try { await del('/api/me/avatar'); await refreshMe(); repaintUser(); toast('Photo retirée', 'ok'); rerender(); }
        catch (e) { toast(e.message, 'bad'); }
      } }, 'Retirer la photo') : null, file);
    const profile = h('section.panel', h('h2', 'Nom et avatar'),
      h('div.profile-row', h('div', preview, pictureActions), h('div.grow',
        field('Nom affiché', name),
        field(me.user.avatar ? 'Couleur (sans photo)' : 'Couleur de l’avatar', swatches(color, (c) => { color = c; paintPreview(); })),
        h('button.btn.primary', { onclick: async () => {
          try { await put('/api/me/profile', { display_name: name.value.trim(), avatar_color: color }); await refreshMe(); repaintUser(); toast('Profil mis à jour', 'ok'); }
          catch (e) { toast(e.message, 'bad'); }
        } }, 'Enregistrer le profil'))));

    const pin = h('input', { type: 'password', inputmode: 'numeric', maxlength: 8, autocomplete: 'off', placeholder: '4 à 8 chiffres' });
    const pinPass = h('input', { type: 'password', autocomplete: 'current-password' });
    const pinPanel = me.user.home ? h('section.panel', h('h2', 'Code PIN du profil'),
      h('p.muted.small', me.user.has_pin
        ? 'Profil protégé : le code est demandé pour y accéder depuis « Changer de profil ».'
        : 'Sans code, l’accès à ce profil depuis « Changer de profil » demande le mot de passe du compte.'),
      h('div.grid2', field('Nouveau code PIN', pin), me.user.managed ? null : field('Mot de passe actuel', pinPass)),
      h('div.actions-row',
        h('button.btn.primary', { onclick: async () => {
          try { await put('/api/me/pin', { pin: pin.value, password: pinPass.value }); await refreshMe(); repaintUser(); toast('Code PIN enregistré', 'ok'); rerender(); }
          catch (e) { toast(e.message, 'bad'); }
        } }, 'Enregistrer le code'),
        me.user.has_pin ? h('button.btn', { onclick: async () => {
          try { await put('/api/me/pin', { pin: '', password: pinPass.value }); await refreshMe(); repaintUser(); toast('Code PIN retiré', 'ok'); rerender(); }
          catch (e) { toast(e.message, 'bad'); }
        } }, 'Retirer le code') : null)) : null;

    const cur = h('input', { type: 'password', autocomplete: 'current-password' });
    const nw = h('input', { type: 'password', autocomplete: 'new-password' });
    const nw2 = h('input', { type: 'password', autocomplete: 'new-password' });
    const security = me.user.managed ? null : h('section.panel', h('h2', 'Mot de passe'),
      h('div.grid3', field('Actuel', cur), field('Nouveau', nw), field('Confirmation', nw2)),
      h('button.btn', { onclick: async () => {
        if (nw.value !== nw2.value) { toast('Les mots de passe ne correspondent pas', 'bad'); return; }
        try {
          await put('/api/me/password', { current: cur.value, new: nw.value });
          cur.value = nw.value = nw2.value = '';
          toast('Mot de passe modifié. Les autres sessions ont été déconnectées.', 'ok');
        } catch (e) { toast(e.message, 'bad'); }
      } }, 'Changer le mot de passe'));
    clear(body, profile, pinPanel, security, me.user.managed ? null : twoFactorPanel(rerender));
    return;
  }

  if (id === 'playback') {
    // apparence des sous-titres, avec un aperçu qui suit chaque réglage
    const subStylePanel = () => {
      const sample = h('div.sub-layer', h('span.sub-line', 'Aperçu des sous-titres\nsur deux lignes'));
      const preview = h('div.sub-preview', { 'aria-hidden': 'true' }, sample);
      const paint = () => {
        applySubStyle(preview, p);
        sample.style.fontSize = `${(p.sub_size / 100) * 18}px`;
      };
      const set = (patch) => { Object.assign(p, patch); paint(); save(patch, 'Sous-titres enregistrés'); };
      const opt = (key, table) => select(Object.entries(table).map(([k, v]) => [k, v[0]]), p[key] || Object.keys(table)[0],
        { onchange: (e) => set({ [key]: e.target.value }) });
      const subSize = h('input', { type: 'range', min: 60, max: 200, step: 10, value: p.sub_size });
      const subSizeLabel = h('span.muted', `${p.sub_size} %`);
      subSize.addEventListener('input', () => { subSizeLabel.textContent = `${subSize.value} %`; p.sub_size = Number(subSize.value); paint(); });
      subSize.addEventListener('change', () => save({ sub_size: Number(subSize.value) }, 'Sous-titres enregistrés'));
      const colors = h('div.swatches', SUB_COLORS.map((c) => h('button.swatch', {
        type: 'button', style: { background: c }, 'aria-label': `Couleur ${c}`, class: c === (p.sub_color || '#ffffff') ? 'swatch on' : 'swatch',
        onclick: (e) => {
          colors.querySelectorAll('.swatch').forEach((x) => x.classList.remove('on'));
          e.currentTarget.classList.add('on');
          set({ sub_color: c });
        } })));
      paint();
      return h('section.panel', h('h2', 'Apparence des sous-titres'),
        h('p.muted.small', 'S’applique aux sous-titres texte (SRT, WebVTT, ASS simplifiés). Les sous-titres ASS stylés et les PGS gardent leur apparence d’origine.'),
        preview,
        h('div.grid2',
          field('Taille', h('div.inline', subSize, subSizeLabel)),
          field('Police', opt('sub_font', SUB_FONTS)),
          field('Fond', opt('sub_bg', SUB_BGS)),
          field('Bordure du texte', opt('sub_edge', SUB_EDGES)),
          field('Position', opt('sub_position', SUB_POSITIONS))),
        field('Couleur du texte', colors));
    };
    clear(body,
      h('section.panel', h('h2', 'Qualité et langue'),
        h('div.grid2',
          field('Qualité par défaut', bindSelect('quality', [['original', 'Originale (le moins de transcodage)'],
            ['1080p', '1080p · 10 Mb/s'], ['720p', '720p · 4 Mb/s'], ['480p', '480p · 1,5 Mb/s']]),
          '« Originale » limite la charge du serveur : lecture directe ou remux lorsque c’est possible.'),
          field('Langue audio préférée', bindSelect('audio_lang', LANGS)))),
      h('section.panel', h('h2', 'Sous-titres'),
        h('div.grid2',
          field('Afficher les sous-titres', bindSelect('sub_mode', [['foreign', 'Si l’audio n’est pas dans ma langue'],
            ['always', 'Toujours'], ['never', 'Jamais']])),
          field('Langue des sous-titres', bindSelect('sub_lang', LANGS)))),
      subStylePanel(),
      h('section.panel', h('h2', 'Séries'),
        bindToggle('autoplay_next', 'Enchaîner automatiquement l’épisode suivant'),
        field('Délai avant l’épisode suivant', bindSelect('next_delay', [0, 3, 5, 10, 15, 20, 30].map((n) => [String(n), n ? `${n} secondes` : 'Immédiatement']), Number),
          'Un compte à rebours s’affiche à la fin de l’épisode, avec « Lire maintenant » et « Annuler ». Pendant le générique, l’épisode suivant est seulement proposé.'),
        bindToggle('auto_skip_intro', 'Passer les génériques d’ouverture automatiquement'),
        bindToggle('hide_spoilers', 'Anti-spoiler : flouter les vignettes et masquer les résumés des épisodes non vus'),
        h('p.field-hint.toggle-hint', 'Un clic sur un résumé masqué l’affiche.')));
    return;
  }

  if (id === 'device') {
    const d = device();
    const set = (patch, msg = 'Réglage enregistré sur cet appareil') => { setDevice(patch); toast(msg, 'ok'); };
    const pick = (key, options) => select(options, d[key] || '', { onchange: (e) => set({ [key]: e.target.value }) });
    clear(body,
      h('section.panel', h('h2', 'Lecture sur cet appareil'),
        h('div.grid2',
          field('Qualité', pick('quality', [['', `Selon le profil (${({ original: 'originale', '1080p': '1080p', '720p': '720p', '480p': '480p' })[p.quality] || p.quality})`],
            ['original', 'Originale'], ['1080p', '1080p'], ['720p', '720p'], ['480p', '480p']]),
          'Utile pour réduire la qualité sur un téléphone en données mobiles.'),
          field('Taille des sous-titres', pick('sub_size', [['', `Selon le profil (${p.sub_size} %)`],
            ...[80, 100, 120, 140, 160, 180, 200].map((n) => [String(n), `${n} %`])]), 'Une TV regardée de loin demande souvent des sous-titres plus grands.'))),
      h('section.panel', h('h2', 'Affichage et navigation'),
        h('div.grid2',
          field('Animations', pick('motion', [['', 'Selon le système'], ['reduced', 'Réduites']])),
          field('Navigation à la télécommande', pick('remote', [['', 'Automatique'], ['on', 'Toujours activée'], ['off', 'Désactivée']]),
            'Repères de sélection visibles et déplacement aux flèches, comme sur une TV.'))),
      h('p.muted.small', 'Ces réglages sont enregistrés dans ce navigateur uniquement.'));
    return;
  }

  if (id === 'appearance') {
    clear(body, h('section.panel', h('h2', 'Couleurs et affiches'),
      field('Couleur d’accent', swatches(p.accent, (c) => save({ accent: c }, 'Couleur appliquée'))),
      h('div.grid2',
        field('Thème', bindSelect('theme', [['dark', 'Sombre'], ['oled', 'Noir profond (OLED)'], ['light', 'Clair']])),
        field('Taille des affiches', bindSelect('poster_size', [['s', 'Petites'], ['m', 'Moyennes'], ['l', 'Grandes']])))));
    return;
  }

  // --- accueil et barre du haut : ordre et rangées affichées
  const hidden = new Set(p.hidden_libraries);
  const homeHidden = new Set(p.home_hidden);
  const homeItems = [
    { key: 'together', label: 'Regarder ensemble', hint: 'quand un groupe est ouvert' },
    { key: 'continue', label: 'Reprendre et à suivre' },
    { key: 'watchlist', label: 'À regarder' },
    { key: 'suggest', label: 'Suggestions', hint: 'selon vos visionnages récents' },
    { key: 'because', label: 'Parce que vous avez regardé…', hint: 'titres proches de vos derniers visionnages' },
    ...me.libraries.map((l) => ({ key: `lib:${l.id}`, label: l.title, hint: l.type === 'show' ? 'nouveaux épisodes' : 'ajoutés récemment' })),
    ...pins().map((pn) => ({ key: `pin:${pn.id}`, label: pn.title, hint: `rangée épinglée (${PIN_LABELS[pn.type]})`,
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
    h('button.btn.ghost', { type: 'button', onclick: async () => { await save(resetPatch, 'Ordre par défaut rétabli'); rerender(); } },
      'Rétablir l’ordre par défaut'));
  const homePanel = layoutPanel('Accueil', 'Faites glisser les rangées ou utilisez les flèches pour les réordonner. L’interrupteur affiche ou masque chaque rangée. Pour épingler une rangée : bouton « Épingler à l’accueil » d’une collection, d’une playlist ou d’un genre filtré dans une bibliothèque.',
    () => layoutList(arrange(homeItems.map((i) => i.key), p.home_order).map((k) => homeById.get(k)), homeOn,
      (keys) => save({ home_order: keys }, 'Ordre de l’accueil enregistré'), homeShow),
    { home_order: [] });
  const navPanel = layoutPanel('Barre du haut', 'Ordre des liens de la barre du haut. Les liens masqués restent accessibles depuis l’accueil.',
    () => layoutList(navItems(me), (k) => !navHidden.has(k),
      async (keys) => { await save({ nav_order: keys }, 'Barre du haut enregistrée'); repaintNav(); },
      async (k, on) => { if (on) navHidden.delete(k); else navHidden.add(k); await save({ nav_hidden: [...navHidden] }, 'Barre du haut enregistrée'); repaintNav(); }),
    { nav_order: [] });
  const startSel = select(navItems(me).map((i) => [i.key, i.label]), p.start_page || 'home',
    { onchange: (e) => save({ start_page: e.target.value }, 'Page d’ouverture enregistrée') });
  const startPanel = h('section.panel', h('h2', 'Page d’ouverture'),
    field('Page affichée à l’ouverture de Nexis', startSel, 'Les liens directs (fiche, lecture) s’ouvrent toujours à leur adresse.'));
  clear(body, startPanel, homePanel, navPanel);
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

  const search = h('input.settings-search', { type: 'search', placeholder: 'Rechercher un réglage…', 'aria-label': 'Rechercher un réglage' });
  const nav = h('nav.settings-nav', { 'aria-label': 'Sections des paramètres' }, search,
    groups.map((g) => h('div.settings-group', h('div.settings-group-title', g.title),
      g.items.map((it) => {
        const on = it.id === current.id && g.area === current.area;
        const badge = g.area === 'admin' && it.id === 'update' && me.update
          ? h('span.settings-badge', { title: `Nexis ${me.update} est disponible` }, 'Nouveau') : null;
        return h('a.settings-link', { href: `#/${g.area}/${it.id}`, dataset: { keys: `${it.label} ${it.lead} ${it.keys}` },
          class: on ? 'settings-link active' : 'settings-link', 'aria-current': on ? 'page' : null }, icon(it.icon), h('span', it.label), badge);
      }))),
    h('p.settings-empty.muted.small', { hidden: true }, 'Aucun réglage ne correspond.'));
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
  const content = h('section.settings-content',
    h('a.settings-back', { href: `#/${area}` }, icon('back'), 'Paramètres'),
    h('header.settings-head', h('h2', current.label), h('p.muted', current.lead)),
    body);
  clear(main, h('div.settings-page', { class: section ? 'settings-page' : 'settings-page no-section' },
    h('div.page-head', h('h1', 'Paramètres')), h('div.settings-layout', nav, content)));
  document.title = `${current.label} · Paramètres · Nexis`;
  if (current.area === 'admin') await renderAdminSection(current.id, body, ctx);
  else accountPage(current.id, body, () => renderSettingsArea(main, area, section, ctx));
}
