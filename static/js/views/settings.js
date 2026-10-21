import { put } from '../api.js';
import { applyPrefs, refreshMe, state } from '../app.js';
import { avatar, clear, field, h, select, toast, toggle } from '../ui.js';

const LANGS = [['fra', 'Français'], ['eng', 'Anglais'], ['jpn', 'Japonais'], ['spa', 'Espagnol'], ['deu', 'Allemand'],
  ['ita', 'Italien'], ['por', 'Portugais'], ['kor', 'Coréen'], ['zho', 'Chinois'], ['', 'Pas de préférence']];
const ACCENTS = ['#e0a84f', '#e5484d', '#3dd68c', '#4c9aff', '#a78bfa', '#f472b6', '#2dd4bf', '#f97316'];

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
      'Sans Plex Pass, tout transcodage vidéo se fait au processeur : « Originale » le limite au maximum.'),
      field('Langue audio préférée', bindSelect('audio_lang', LANGS)),
      field('Sous-titres', bindSelect('sub_mode', [['foreign', 'Si l’audio n’est pas dans ma langue'],
        ['always', 'Toujours'], ['never', 'Jamais']])),
      field('Langue des sous-titres', bindSelect('sub_lang', LANGS)),
      field('Taille des sous-titres', h('div.inline', subSize, subSizeLabel))),
    bindToggle('autoplay_next', 'Enchaîner automatiquement l’épisode suivant'),
    bindToggle('auto_skip_intro', 'Passer les génériques d’ouverture automatiquement'));

  // --- display
  const hidden = new Set(p.hidden_libraries);
  const display = h('section.panel', h('h2', 'Affichage'),
    field('Couleur d’accent', swatches(p.accent, (c) => save({ accent: c }, 'Couleur appliquée'))),
    h('div.grid2',
      field('Thème', bindSelect('theme', [['dark', 'Sombre'], ['oled', 'Noir profond (OLED)'], ['light', 'Clair']])),
      field('Taille des affiches', bindSelect('poster_size', [['s', 'Petites'], ['m', 'Moyennes'], ['l', 'Grandes']]))),
    bindToggle('show_continue', 'Afficher « Reprendre et à suivre » sur l’accueil'),
    me.libraries.length ? field('Bibliothèques affichées sur l’accueil', h('div.checks', me.libraries.map((l) => {
      const t = toggle(l.title, !hidden.has(l.id));
      t.querySelector('input').addEventListener('change', (e) => {
        if (e.target.checked) hidden.delete(l.id); else hidden.add(l.id);
        save({ hidden_libraries: [...hidden] });
      });
      return t;
    }))) : null);

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

  clear(main, h('div.page-head', h('h1', 'Réglages')), h('div.panels', profile, playback, display, security));
}
