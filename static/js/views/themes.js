// Magasin de thèmes (Paramètres → Magasin de thèmes) : thèmes fournis avec Noxa et proposés par la communauté sur
// GitHub, thèmes installés, éditeur, import et export. Un thème n'est qu'une description vérifiée par le serveur
// (couleurs, dégradé, effets d'une liste fixe, police, arrondi) : il ne peut contenir ni code ni image.
import { get, post, put } from '../api.js';
import { applyPrefs, paintTheme, state } from '../app.js';
import { clear, field, h, icon, modal, select, spinner, toast, toggle } from '../ui.js';
import { lang, tr } from '../i18n.js';

const EFFECTS = [['neon', tr('Néon (lueurs)')], ['scanlines', tr('Lignes d’écran')], ['grain', tr('Grain de pellicule')],
  ['stars', tr('Ciel étoilé')], ['vignette', tr('Vignettage')], ['glow', tr('Halo des affiches')]];
const FONTS = [['default', tr('Par défaut')], ['mono', tr('Chasse fixe (terminal)')], ['serif', tr('Avec empattements (classique)')], ['rounded', tr('Arrondie')]];
const STYLE_NAMES = { classic: tr('Classique'), glass: tr('Verre dépoli'), sidebar: tr('Barre latérale'), minimal: tr('Épuré'),
  cinema: tr('Cinéma plein écran'), rounded: tr('Arrondi') };
const COLOR_FIELDS = [['bg', tr('Fond')], ['surface', tr('Panneaux')], ['surface2', tr('Panneaux (second plan)')], ['text', tr('Texte')],
  ['muted', tr('Texte secondaire')], ['line', tr('Bordures')], ['accent', tr('Accent')], ['accent2', tr('Second accent')],
  ['on_accent', tr('Texte sur l’accent')]];
const MAX_MINE = 20;

const label = (v) => (v ? (v[lang] || v.fr || v.en || '') : '');
const effectName = (e) => (EFFECTS.find(([k]) => k === e) || [e, e])[1];

// mini-aperçu du thème (couleurs vérifiées par le serveur : #rrggbb)
export function themePreview(t) {
  const c = t.colors;
  const bg = t.background ? `linear-gradient(${t.background.angle}deg, ${t.background.stops.join(', ')})` : c.bg;
  const cls = ['store-preview', ...(t.effects || []).map((e) => `fx-${e}`)].join(' ');
  return h('div', { class: cls, style: { background: bg, color: c.text, borderRadius: `${Math.min(t.radius, 16)}px` } },
    h('span.sp-bar', h('i', { style: { background: c.accent } }), h('i', { style: { background: c.muted } }), h('i', { style: { background: c.muted } })),
    h('span.sp-panel', { style: { background: c.surface, borderRadius: `${Math.min(t.radius, 12)}px` } },
      h('span.sp-title', { style: { color: c.text } }, label(t.name)),
      h('span.sp-btn', { style: { background: c.accent, color: c.on_accent, borderRadius: `${Math.min(t.radius, 10)}px` } }, tr('bouton|Lecture'))));
}

async function savePrefs(patch, msg) {
  const me = state.me;
  const r = await put('/api/me/prefs', patch);
  Object.assign(me.prefs, r);
  applyPrefs(me.prefs);
  if (msg) toast(msg, 'ok');
}

// le thème tel qu'il s'enregistre (sans l'indication de provenance ajoutée par la liste du magasin)
const bare = ({ source, ...t }) => t; // eslint-disable-line no-unused-vars

// installer (ou mettre à jour) un thème et l'utiliser
async function install(theme, withStyle) {
  const t = bare(theme);
  const mine = (state.me.prefs.my_themes || []).filter((x) => x.id !== t.id);
  if (mine.length >= MAX_MINE) { toast(tr('{n} thèmes installés au maximum : retirez-en un', { n: MAX_MINE }), 'bad'); return false; }
  const patch = { my_themes: [...mine, t], theme: `t:${t.id}`, accent: t.colors.accent };
  if (withStyle && t.style) patch.ui_style = t.style;
  try { await savePrefs(patch, tr('Thème « {name} » appliqué', { name: label(t.name) })); return true; } catch (e) { toast(e.message, 'bad'); return false; }
}

function download(t) {
  const blob = new Blob([JSON.stringify(t, null, 1) + '\n'], { type: 'application/json' });
  const a = h('a', { href: URL.createObjectURL(blob), download: `${t.id}.json` });
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

// GitHub : page « nouveau fichier » du dépôt du magasin, déjà remplie ; GitHub propose de créer une copie (fork)
// et une demande de fusion, vérifiée par l'intégration continue puis par les responsables du dépôt
function proposeUrl(submit, t) {
  const body = JSON.stringify(t, null, 1) + '\n';
  return `${submit}?filename=${encodeURIComponent(`${t.id}.json`)}&value=${encodeURIComponent(body)}`;
}

function slug(s) {
  return (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
}

export async function renderThemeStore(body, ctx) {
  clear(body, spinner());
  let data;
  try { data = await get('/api/themes'); } catch (e) { clear(body, h('p.form-error', e.message)); return; }
  if (!ctx.isCurrent()) return;
  const me = state.me;
  let previewing = null;
  const restore = () => { if (previewing) { previewing = null; applyPrefs(me.prefs); bar.hidden = true; } };
  ctx.setCleanup(restore); // aperçu en cours quand on quitte la page : on revient au thème choisi
  const bar = h('div.status-box.preview-bar', { hidden: true });
  const preview = (t) => {
    previewing = t;
    paintTheme(t);
    document.documentElement.dataset.theme = t.dark ? 'dark' : 'light';
    document.documentElement.style.setProperty('--accent', t.colors.accent);
    clear(bar, h('strong', tr('Aperçu : {name}', { name: label(t.name) })), ' ',
      h('button.btn.small.primary', { onclick: async () => { const tt = previewing; previewing = null; bar.hidden = true; await install(tt, styleBox.querySelector('input').checked); paint(); } }, tr('Installer')),
      ' ', h('button.btn.small', { onclick: () => restore() }, tr('Annuler')));
    bar.hidden = false;
    bar.scrollIntoView({ block: 'nearest' });
  };
  const styleBox = toggle(tr('Appliquer aussi le style d’interface conseillé par le thème'), true);
  const search = h('input', { type: 'search', placeholder: tr('Chercher un thème (nom, auteur, effet)…'), 'aria-label': tr('Chercher un thème') });
  const mineBox = h('div');
  const gridBox = h('div');
  const footBox = h('p.small.muted');
  const storePanel = h('section.panel', h('h2', tr('Magasin de thèmes')),
    h('p.muted.small', tr('Thèmes fournis avec Noxa et proposés par la communauté sur GitHub. « Aperçu » les essaie sur toute l’interface sans rien changer ; « Installer » les ajoute à vos thèmes (ils vous suivent sur les serveurs liés).')),
    h('div.grid2', search, styleBox), bar, gridBox, footBox);

  const card = (t, { mine = false } = {}) => {
    const active = me.prefs.theme === `t:${t.id}`;
    const installed = (me.prefs.my_themes || []).find((x) => x.id === t.id);
    const update = installed && !mine && t.version > installed.version;
    return h('div.store-card', { class: active ? 'store-card on' : 'store-card' },
      themePreview(t),
      h('div.store-name', label(t.name)),
      h('div.store-meta', t.author ? [tr('par '), h('a', { href: `https://github.com/${encodeURIComponent(t.author)}`, target: '_blank', rel: 'noopener' }, t.author)] : tr('par vous'),
        t.source === 'bundled' ? ` · ${tr('fourni avec Noxa')}` : t.source === 'store' ? ` · ${tr('communauté')}` : '',
        t.style ? ` · ${tr('style conseillé : {s}', { s: STYLE_NAMES[t.style] || t.style })}` : ''),
      t.description ? h('div.store-desc', label(t.description)) : h('div.store-desc'),
      (t.effects || []).length ? h('div.store-tags', t.effects.map((e) => h('span', effectName(e)))) : null,
      h('div.store-actions', mine
        ? [active ? h('span.small.muted', tr('Utilisé')) : h('button.btn.small.primary', { onclick: async () => { await savePrefs({ theme: `t:${t.id}`, accent: t.colors.accent }, tr('Thème « {name} » appliqué', { name: label(t.name) })); paint(); } }, tr('Utiliser')),
          h('button.btn.small', { onclick: () => editor(t) }, tr('Modifier')),
          h('button.btn.small', { onclick: () => download(t) }, icon('download'), tr('Exporter')),
          h('button.icon-btn', { title: tr('Retirer'), 'aria-label': tr('Retirer'), onclick: async () => {
            const patch = { my_themes: (me.prefs.my_themes || []).filter((x) => x.id !== t.id) };
            if (active) patch.theme = 'dark';
            await savePrefs(patch, tr('Thème retiré'));
            paint();
          } }, icon('trash'))]
        : [h('button.btn.small', { onclick: () => preview(t) }, tr('Aperçu')),
          installed && !update ? h('span.small.muted', tr('Installé')) : h('button.btn.small.primary', { onclick: async () => { if (await install(t, styleBox.querySelector('input').checked)) paint(); } }, update ? tr('Mettre à jour') : tr('Installer'))]));
  };

  const paint = () => {
    const mine = me.prefs.my_themes || [];
    clear(mineBox, h('section.panel', h('h2', tr('Mes thèmes')),
      mine.length ? h('div.store-grid', mine.map((t) => card(t, { mine: true }))) : h('p.muted.small', tr('Aucun thème installé. Choisissez-en un dans le magasin ci-dessous, ou créez le vôtre.')),
      h('div.actions-row',
        h('button.btn', { onclick: () => editor(null) }, tr('Créer un thème…')),
        h('button.btn', { onclick: () => file.click() }, tr('Importer un fichier…')), file,
        me.prefs.theme && me.prefs.theme.startsWith('t:') ? h('button.btn.ghost', { onclick: async () => { await savePrefs({ theme: 'dark' }, tr('Thème « Sombre » appliqué')); paint(); } }, tr('Revenir aux thèmes de base')) : null)));
    const q = search.value.trim().toLowerCase();
    const list = data.themes.filter((t) => !q || [label(t.name), label(t.description), t.author, ...(t.effects || []).map(effectName)].join(' ').toLowerCase().includes(q));
    clear(gridBox, list.length ? h('div.store-grid', list.map((t) => card(t))) : h('p.muted.small', tr('Aucun thème ne correspond.')));
    const store = data.store;
    clear(footBox, ...(store.enabled
      ? [store.error ? tr('Magasin en ligne injoignable pour le moment ({error}) : seuls les thèmes fournis sont affichés.', { error: store.error })
        : tr('{n} thème(s) de la communauté.', { n: store.count }), ' ',
      h('a', { href: repoUrl(store.submit_url), target: '_blank', rel: 'noopener' }, tr('Voir le dépôt des thèmes et comment proposer le vôtre'))]
      : [tr('Le magasin en ligne est désactivé sur ce serveur : seuls les thèmes fournis sont affichés.')]));
  };

  const file = h('input', { type: 'file', accept: '.json,application/json', hidden: true, onchange: async () => {
    const f = file.files[0];
    file.value = '';
    if (!f) return;
    try {
      const t = JSON.parse(await f.text());
      const r = await post('/api/themes/check', { theme: t });
      editor(r.theme);
    } catch (e) {
      toast(e.data && e.data.problems ? `${tr('Thème invalide')} : ${e.data.problems.join(' ; ')}` : tr('Fichier illisible : un thème Noxa (.json) est attendu'), 'bad');
    }
  } });

  // ---------------------------------------------------------------- éditeur
  function editor(base) {
    const start = base ? JSON.parse(JSON.stringify(bare(base))) : {
      schema: 1, id: '', name: { fr: '', en: '' }, author: '', version: 1, dark: true,
      colors: { bg: '#0b0b0e', surface: '#16161c', surface2: '#202029', text: '#f5f3ef', muted: '#a6a3ae', line: '#2a2a33', accent: '#ffa43a', accent2: '#ff5ea8', on_accent: '#1c0f00' },
      effects: [], font: 'default', radius: 10 };
    const t = start;
    const name = h('input', { value: label(t.name), maxlength: 40, placeholder: tr('Ex. Ville néon') });
    const author = h('input', { value: t.author || '', maxlength: 39, placeholder: tr('Votre identifiant GitHub (facultatif)') });
    const desc = h('input', { value: label(t.description), maxlength: 160 });
    const colors = {};
    const colorFields = COLOR_FIELDS.map(([k, l]) => {
      const inp = h('input', { type: 'color', value: t.colors[k] || t.colors.accent });
      colors[k] = inp;
      inp.addEventListener('input', live);
      return h('label.color-field', inp, h('span', l));
    });
    const gradOn = toggle(tr('Fond en dégradé'), !!t.background, { onchange: live });
    const stops = (t.background ? t.background.stops : [t.colors.bg, t.colors.surface2, t.colors.bg]).slice(0, 3);
    while (stops.length < 3) stops.push(t.colors.bg);
    const stopInputs = stops.map((c) => { const i = h('input', { type: 'color', value: c }); i.addEventListener('input', live); return i; });
    const angle = h('input', { type: 'range', min: 0, max: 360, step: 15, value: t.background ? t.background.angle : 180 });
    angle.addEventListener('input', live);
    const fx = EFFECTS.map(([k, l]) => toggle(l, (t.effects || []).includes(k), { onchange: live, dataset: { fx: k } }));
    const font = select(FONTS, t.font || 'default', { onchange: live });
    const radius = h('input', { type: 'range', min: 0, max: 28, step: 1, value: t.radius });
    radius.addEventListener('input', live);
    const style = select([['', tr('Aucun')], ...Object.entries(STYLE_NAMES)], t.style || '', { onchange: live });
    const problems = h('ul.theme-problems');
    const prev = h('div');

    function build() {
      const c = Object.fromEntries(Object.entries(colors).map(([k, i]) => [k, i.value]));
      const lum = (x) => { const n = parseInt(x.slice(1), 16); return 0.2126 * (n >> 16) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255); };
      const id = base && base.id ? base.id : slug(name.value) || 'mon-theme';
      const out = { schema: 1, id, name: { fr: name.value.trim() || tr('Mon thème'), en: name.value.trim() || tr('Mon thème') },
        author: author.value.trim(), version: base ? (base.version || 1) + 1 : 1, dark: lum(c.bg) < lum(c.text), colors: c,
        effects: fx.filter((x) => x.querySelector('input').checked).map((x) => x.querySelector('input').dataset.fx).slice(0, 4),
        font: font.value, radius: Number(radius.value) };
      if (desc.value.trim()) out.description = { fr: desc.value.trim(), en: desc.value.trim() };
      if (gradOn.querySelector('input').checked) out.background = { type: 'gradient', angle: Number(angle.value), stops: stopInputs.map((i) => i.value) };
      if (style.value) out.style = style.value;
      if (!out.author) delete out.author;
      return out;
    }
    let timer = null;
    function live() {
      const tt = build();
      clear(prev, themePreview(tt));
      paintTheme(tt); // l'interface entière sert d'aperçu
      document.documentElement.dataset.theme = tt.dark ? 'dark' : 'light';
      document.documentElement.style.setProperty('--accent', tt.colors.accent);
      clearTimeout(timer);
      timer = setTimeout(async () => {
        try { await post('/api/themes/check', { theme: tt }); clear(problems); } catch (e) {
          clear(problems, ...((e.data && e.data.problems) || [e.message]).map((p) => h('li', p)));
        }
      }, 300);
    }
    name.addEventListener('input', live);
    live();
    const check = async () => {
      try { return (await post('/api/themes/check', { theme: build() })).theme; } catch (e) {
        clear(problems, ...((e.data && e.data.problems) || [e.message]).map((p) => h('li', p)));
        return null;
      }
    };
    modal(base ? tr('Modifier le thème') : tr('Créer un thème'), h('div.form.theme-editor',
      prev,
      h('div.grid2', field(tr('Nom'), name), field(tr('Auteur'), author)),
      field(tr('Description'), desc),
      h('h3.small', tr('Couleurs')), h('div.grid-colors', colorFields),
      gradOn, h('div.grad-row', ...stopInputs, h('label.grad-angle', h('span.small.muted', tr('Angle')), angle)),
      h('h3.small', tr('Effets (4 au plus)')), h('div.checks', fx),
      h('div.grid2', field(tr('Police'), font), field(tr('Style d’interface conseillé'), style)),
      field(tr('Arrondi des angles'), radius),
      h('p.muted.small', tr('Le texte doit rester lisible : le contraste entre le texte et le fond, et sur les boutons, est vérifié.')),
      problems),
    [{ label: tr('Annuler'), value: false },
      { label: tr('Exporter'), handler: async () => { const tt = await check(); if (tt) download(tt); return false; } },
      { label: tr('Proposer sur GitHub'), handler: async () => {
        const tt = await check();
        if (!tt) return false;
        window.open(proposeUrl(data.store.submit_url, tt), '_blank', 'noopener');
        toast(tr('GitHub s’ouvre avec votre thème : validez la création du fichier, puis la demande de fusion'), 'ok');
        return false;
      } },
      { label: tr('Enregistrer et utiliser'), primary: true, handler: async () => {
        const tt = await check();
        if (!tt) return false;
        return install(tt, false);
      } }]).then(() => { applyPrefs(me.prefs); paint(); });
  }

  // ---------------------------------------------------------------- administrateur : source du magasin
  function adminPanel() {
    const box = h('section.panel', h('h2', tr('Magasin de thèmes (administrateur)')), spinner());
    get('/api/admin/themes').then((c) => {
      const on = toggle(tr('Proposer les thèmes de la communauté (index en ligne)'), c.enabled);
      const idx = h('input', { value: c.index_url === c.default_index ? '' : c.index_url, placeholder: c.default_index, spellcheck: false });
      const sub = h('input', { value: c.submit_url === c.default_submit ? '' : c.submit_url, placeholder: c.default_submit, spellcheck: false });
      const status = c.error ? h('p.form-error.small', tr('Dernier essai : {error}', { error: c.error }))
        : h('p.muted.small', c.updated_at ? tr('{n} thème(s) en ligne, relus toutes les 6 heures.', { n: c.count }) : tr('Pas encore relu.'));
      clear(box, h('h2', tr('Magasin de thèmes (administrateur)')),
        h('p.muted.small', tr('Le serveur lit l’index des thèmes (un fichier JSON du dépôt GitHub, vérifié à chaque lecture) ; les appareils ne contactent pas GitHub. Changez les adresses pour utiliser votre propre dépôt de thèmes.')),
        on, field(tr('Index des thèmes'), idx), field(tr('Page pour proposer un thème'), sub), status,
        h('div.actions-row', h('button.btn.primary', { onclick: async () => {
          try {
            await put('/api/admin/themes', { enabled: on.querySelector('input').checked, index_url: idx.value.trim(), submit_url: sub.value.trim() });
            toast(tr('Enregistré'), 'ok');
            data = await get('/api/themes');
            paint();
          } catch (e) { toast(e.message, 'bad'); }
        } }, tr('Enregistrer et actualiser'))));
    }).catch((e) => clear(box, h('p.form-error', e.message)));
    return box;
  }

  search.addEventListener('input', paint);
  clear(body, mineBox, storePanel, me.user.is_admin ? adminPanel() : null);
  paint();
}

// https://github.com/<dépôt>/new/<branche>/<dossier> -> la page du dossier sur GitHub
function repoUrl(submit) {
  const m = /^(https:\/\/github\.com\/[^/]+\/[^/]+)\/new\/([^/]+)\/(.*)$/.exec(submit || '');
  return m ? `${m[1]}/tree/${m[2]}/${m[3]}` : submit;
}
