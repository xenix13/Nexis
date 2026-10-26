import { del, put } from '../api.js';
import { ADMIN_ALIASES, ADMIN_GROUPS, renderAdminSection } from './admin.js';
import { applyPrefs, navItems, refreshMe, repaintNav, repaintUser, state } from '../app.js';
import { arrange, avatar, clear, field, h, icon, select, toast, toggle } from '../ui.js';

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
        sw);
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

// ---------------------------------------------------------------- Mon compte
export const ACCOUNT_GROUP = { title: 'Mon compte', items: [
  { id: 'profile', label: 'Profil', icon: 'user', lead: 'Nom, photo, code PIN et mot de passe.',
    keys: 'nom avatar couleur pin code mot de passe sécurité' },
  { id: 'playback', label: 'Lecture', icon: 'play', lead: 'Qualité, langues, sous-titres et enchaînement des épisodes.',
    keys: 'qualité langue audio sous-titres taille épisode suivant générique intro automatique' },
  { id: 'appearance', label: 'Apparence', icon: 'palette', lead: 'Thème, couleur d’accent et taille des affiches.',
    keys: 'thème sombre clair oled couleur accent affiches taille' },
  { id: 'layout', label: 'Accueil et menu', icon: 'home', lead: 'Ordre et affichage des rangées de l’accueil et des liens de la barre du haut.',
    keys: 'accueil rangées ordre barre du haut menu liens masquer réorganiser' },
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
      try {
        const image = await squareImage(f, 256);
        await put('/api/me/avatar', { image });
        await refreshMe(); repaintUser();
        toast('Photo de profil enregistrée', 'ok');
        rerender();
      } catch (e) { toast(e.message || 'Image illisible', 'bad'); }
    });
    const pictureActions = h('div.avatar-actions',
      h('button.btn.small', { type: 'button', onclick: () => file.click() }, me.user.avatar ? 'Changer la photo' : 'Choisir une photo'),
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
    clear(body, profile, pinPanel, security);
    return;
  }

  if (id === 'playback') {
    const subSize = h('input', { type: 'range', min: 60, max: 200, step: 10, value: p.sub_size });
    const subSizeLabel = h('span.muted', `${p.sub_size} %`);
    subSize.addEventListener('input', () => { subSizeLabel.textContent = `${subSize.value} %`; });
    subSize.addEventListener('change', () => save({ sub_size: Number(subSize.value) }));
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
          field('Langue des sous-titres', bindSelect('sub_lang', LANGS))),
        field('Taille des sous-titres', h('div.inline', subSize, subSizeLabel))),
      h('section.panel', h('h2', 'Séries'),
        bindToggle('autoplay_next', 'Enchaîner automatiquement l’épisode suivant'),
        field('Délai avant l’épisode suivant', bindSelect('next_delay', [0, 3, 5, 10, 15, 20, 30].map((n) => [String(n), n ? `${n} secondes` : 'Immédiatement']), Number),
          'Un compte à rebours s’affiche à la fin de l’épisode, avec « Lire maintenant » et « Annuler ». Pendant le générique, l’épisode suivant est seulement proposé.'),
        bindToggle('auto_skip_intro', 'Passer les génériques d’ouverture automatiquement')));
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
  const homePanel = layoutPanel('Accueil', 'Faites glisser les rangées ou utilisez les flèches pour les réordonner. L’interrupteur affiche ou masque chaque rangée.',
    () => layoutList(arrange(homeItems.map((i) => i.key), p.home_order).map((k) => homeById.get(k)), homeOn,
      (keys) => save({ home_order: keys }, 'Ordre de l’accueil enregistré'), homeShow),
    { home_order: [] });
  const navPanel = layoutPanel('Barre du haut', 'Ordre des liens de la barre du haut. Les liens masqués restent accessibles depuis l’accueil.',
    () => layoutList(navItems(me), (k) => !navHidden.has(k),
      async (keys) => { await save({ nav_order: keys }, 'Barre du haut enregistrée'); repaintNav(); },
      async (k, on) => { if (on) navHidden.delete(k); else navHidden.add(k); await save({ nav_hidden: [...navHidden] }, 'Barre du haut enregistrée'); repaintNav(); }),
    { nav_order: [] });
  clear(body, homePanel, navPanel);
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
