import { put } from '../api.js';
import { applyPrefs, navItems, refreshMe, repaintNav, state } from '../app.js';
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

export async function renderSettings(main) {
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

  // --- profile
  const name = h('input', { value: me.user.display_name, maxlength: 50 });
  let color = me.user.avatar_color;
  const preview = h('span');
  const paintPreview = () => preview.replaceChildren(avatar({ ...me.user, display_name: name.value, avatar_color: color }, 56));
  paintPreview();
  name.addEventListener('input', paintPreview);
  const swatches = (current, onPick) => h('div.swatches', ACCENTS.map((c) => h('button.swatch', {
    type: 'button', style: { background: c }, 'aria-label': c, class: c === current ? 'swatch on' : 'swatch',
    onclick: (e) => {
      e.currentTarget.parentElement.querySelectorAll('.swatch').forEach((s) => s.classList.remove('on'));
      e.currentTarget.classList.add('on');
      onPick(c);
    } })));

  const profile = h('section.panel', h('h2', 'Profil'),
    h('div.profile-row', preview, h('div.grow',
      field('Nom affiché', name),
      field('Couleur de l’avatar', swatches(color, (c) => { color = c; paintPreview(); })),
      h('button.btn.primary', { onclick: async () => {
        try { await put('/api/me/profile', { display_name: name.value.trim(), avatar_color: color }); await refreshMe(); toast('Profil mis à jour', 'ok'); }
        catch (e) { toast(e.message, 'bad'); }
      } }, 'Enregistrer le profil'))));

  // --- playback
  const subSize = h('input', { type: 'range', min: 60, max: 200, step: 10, value: p.sub_size });
  const subSizeLabel = h('span.muted', `${p.sub_size} %`);
  subSize.addEventListener('input', () => { subSizeLabel.textContent = `${subSize.value} %`; });
  subSize.addEventListener('change', () => save({ sub_size: Number(subSize.value) }));
  const playback = h('section.panel', h('h2', 'Lecture'),
    h('div.grid2',
      field('Qualité par défaut', bindSelect('quality', [['original', 'Originale (le moins de transcodage)'],
        ['1080p', '1080p · 10 Mb/s'], ['720p', '720p · 4 Mb/s'], ['480p', '480p · 1,5 Mb/s']]),
      '« Originale » évite au maximum de faire travailler le processeur du serveur (lecture directe ou remux quand c’est possible).'),
      field('Langue audio préférée', bindSelect('audio_lang', LANGS)),
      field('Sous-titres', bindSelect('sub_mode', [['foreign', 'Si l’audio n’est pas dans ma langue'],
        ['always', 'Toujours'], ['never', 'Jamais']])),
      field('Langue des sous-titres', bindSelect('sub_lang', LANGS)),
      field('Taille des sous-titres', h('div.inline', subSize, subSizeLabel))),
    bindToggle('autoplay_next', 'Enchaîner automatiquement l’épisode suivant'),
    field('Délai avant l’épisode suivant', bindSelect('next_delay', [0, 3, 5, 10, 15, 20, 30].map((n) => [String(n), n ? `${n} secondes` : 'Immédiatement']), Number),
      'À la fin de l’épisode, un compte à rebours laisse le choix : lire tout de suite ou annuler. Pendant le générique, l’épisode suivant est seulement proposé.'),
    bindToggle('auto_skip_intro', 'Passer les génériques d’ouverture automatiquement'));

  // --- display
  const hidden = new Set(p.hidden_libraries);
  const display = h('section.panel', h('h2', 'Affichage'),
    field('Couleur d’accent', swatches(p.accent, (c) => save({ accent: c }, 'Couleur appliquée'))),
    h('div.grid2',
      field('Thème', bindSelect('theme', [['dark', 'Sombre'], ['oled', 'Noir profond (OLED)'], ['light', 'Clair']])),
      field('Taille des affiches', bindSelect('poster_size', [['s', 'Petites'], ['m', 'Moyennes'], ['l', 'Grandes']]))),
  );

  // --- accueil et barre du haut : ordre et rangées affichées
  const homeHidden = new Set(p.home_hidden);
  const homeItems = [
    { key: 'together', label: 'Regarder ensemble', hint: 'quand un groupe est ouvert' },
    { key: 'continue', label: 'Reprendre et à suivre' },
    { key: 'watchlist', label: 'À regarder' },
    { key: 'suggest', label: 'Suggestions pour toi', hint: 'd’après ce que tu as regardé récemment' },
    { key: 'because', label: 'Parce que tu as regardé…', hint: 'titres proches de tes derniers visionnages' },
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
    h('button.btn.ghost', { type: 'button', onclick: async () => { await save(resetPatch, 'Ordre par défaut rétabli'); Object.assign(p, me.prefs); renderSettings(main); } },
      'Rétablir l’ordre par défaut'));
  const homePanel = layoutPanel('Accueil', 'Glisse les rangées ou utilise les flèches pour choisir leur ordre ; l’interrupteur les affiche ou les masque.',
    () => layoutList(arrange(homeItems.map((i) => i.key), p.home_order).map((k) => homeById.get(k)), homeOn,
      (keys) => save({ home_order: keys }, 'Ordre de l’accueil enregistré'), homeShow),
    { home_order: [] });
  const navPanel = layoutPanel('Barre du haut', 'Ordre des liens en haut de l’écran. Les liens masqués restent accessibles depuis l’accueil.',
    () => layoutList(navItems(me), (k) => !navHidden.has(k),
      async (keys) => { await save({ nav_order: keys }, 'Barre du haut enregistrée'); refreshShell(); },
      async (k, on) => { if (on) navHidden.delete(k); else navHidden.add(k); await save({ nav_hidden: [...navHidden] }, 'Barre du haut enregistrée'); refreshShell(); }),
    { nav_order: [] });
  const refreshShell = repaintNav; // la barre du haut reflète tout de suite le nouvel ordre

  // --- password
  const cur = h('input', { type: 'password', autocomplete: 'current-password' });
  const nw = h('input', { type: 'password', autocomplete: 'new-password' });
  const nw2 = h('input', { type: 'password', autocomplete: 'new-password' });
  const security = h('section.panel', h('h2', 'Mot de passe'),
    h('div.grid3', field('Actuel', cur), field('Nouveau', nw), field('Confirmation', nw2)),
    h('button.btn', { onclick: async () => {
      if (nw.value !== nw2.value) { toast('Les mots de passe ne correspondent pas', 'bad'); return; }
      try {
        await put('/api/me/password', { current: cur.value, new: nw.value });
        cur.value = nw.value = nw2.value = '';
        toast('Mot de passe changé. Tes autres sessions ont été déconnectées.', 'ok');
      } catch (e) { toast(e.message, 'bad'); }
    } }, 'Changer le mot de passe'));

  const pin = h('input', { type: 'password', inputmode: 'numeric', maxlength: 8, autocomplete: 'off', placeholder: '4 à 8 chiffres' });
  const pinPass = h('input', { type: 'password', autocomplete: 'current-password' });
  const pinPanel = me.user.home ? h('section.panel', h('h2', 'Code PIN du profil'),
    h('p.muted.small', me.user.has_pin
      ? 'Ton profil est protégé : le code est demandé quand quelqu’un passe sur ton profil depuis « Changer de profil ».'
      : 'Sans code, n’importe quel membre du foyer peut passer sur ton profil depuis « Changer de profil » (ton mot de passe est alors demandé).'),
    h('div.grid2', field('Nouveau code PIN', pin), me.user.managed ? null : field('Mot de passe actuel', pinPass)),
    h('div.actions-row',
      h('button.btn.primary', { onclick: async () => {
        try { await put('/api/me/pin', { pin: pin.value, password: pinPass.value }); await refreshMe(); toast('Code PIN enregistré', 'ok'); renderSettings(main); }
        catch (e) { toast(e.message, 'bad'); }
      } }, 'Enregistrer le code'),
      me.user.has_pin ? h('button.btn', { onclick: async () => {
        try { await put('/api/me/pin', { pin: '', password: pinPass.value }); await refreshMe(); toast('Code PIN retiré', 'ok'); renderSettings(main); }
        catch (e) { toast(e.message, 'bad'); }
      } }, 'Retirer le code') : null)) : null;

  clear(main, h('div.page-head', h('h1', 'Réglages')), h('div.panels', profile, playback, display, homePanel, navPanel, pinPanel, me.user.managed ? null : security));
}
