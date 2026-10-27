import { del, get, post, put } from '../api.js';
import { state } from '../app.js';
import { launchableGroups } from '../groups.js';
import { deviceCaps, download as offlineDownload, find as findDownload, onDownloads, supported as offlineSupported } from '../offline.js';
import { SIGNS_HINT, fixForced, menuSubs, pickAudio, pickSubtitle, trackPayload } from '../tracks.js';
import { chapterStrip } from '../chapters.js';
import { clear, episodeCode, field, fmtDuration, fmtTime, h, icon, img, modal, moreMenu, posterCard, progressBar, select, spinner, toast, toggle } from '../ui.js';
import { tr, plural, locale } from '../i18n.js';

async function identify(d, onDone) {
  const q = h('input', { value: d.type === 'episode' ? d.show : d.title });
  const year = h('input', { type: 'number', value: d.type === 'episode' ? '' : (d.year || ''), placeholder: tr('Année') });
  const results = h('div.identify-results');
  const search = async () => {
    results.replaceChildren(spinner());
    try {
      const r = await get(`/api/admin/identify/${d.id}?q=${encodeURIComponent(q.value)}&year=${encodeURIComponent(year.value)}`);
      results.replaceChildren(...(r.results.length ? r.results.map((x) => h('button.identify-hit', {
        class: x.tmdb_id === r.current ? 'identify-hit on' : 'identify-hit',
        onclick: async () => {
          results.replaceChildren(spinner());
          try {
            await post(`/api/admin/identify/${d.id}`, { tmdb_id: x.tmdb_id });
            toast(tr('Identifié : {title}', { title: x.title }), 'ok');
            document.querySelector('.modal-overlay')?.remove();
            onDone();
          } catch (e) { toast(e.message, 'bad'); search(); }
        } },
      x.poster ? h('img', { src: x.poster, alt: '' }) : h('div.noimg'),
      h('div', h('strong', x.title), h('div.muted.small', [x.year, x.original_title !== x.title ? x.original_title : null].filter(Boolean).join(' · ')),
        h('p.small', (x.overview || '').slice(0, 220))))) : [h('p.muted', tr('Aucun résultat.'))]));
    } catch (e) { results.replaceChildren(h('p.form-error', e.message)); }
  };
  const form = h('form.identify-form', { onsubmit: (e) => { e.preventDefault(); search(); } },
    h('div.grid-search', field(tr('Titre'), q), field(tr('Année'), year), h('button.btn.primary', { type: 'submit' }, tr('Rechercher'))));
  search();
  await modal(tr('Identifier sur TMDB'), h('div', form, results), []);
}

function fileInfo(d) {
  const m = d.media || {};
  const mk = d.markers || {};
  const src = { manual: tr('posé à la main'), chapters: tr('chapitres'), 'chapters-guess': tr('chapitres (estimé)'), audio: tr('empreinte audio') };
  const row = (k, v) => (v ? [h('dt', k), h('dd', v)] : null);
  modal(tr('Informations sur le fichier'), h('dl.facts.file-facts',
    row(tr('Fichier'), d.path ? h('code', d.path) : null),
    row(tr('Conteneur'), (m.container || '').toUpperCase()),
    row(tr('Vidéo'), [m.video_codec, m.resolution ? `${m.resolution}p` : null].filter(Boolean).join(' · ').toUpperCase()),
    row(tr('Débit'), m.bitrate ? `${m.bitrate} kb/s` : null),
    row(tr('Audio'), (d.audio || []).map((a) => a.title).join(' · ')),
    row(tr('Sous-titres'), (d.subtitles || []).map((x) => x.title).join(' · ')),
    row(tr('Chapitres'), d.chapters && d.chapters.length ? `${d.chapters.length}` : tr('aucun')),
    row(tr('Intro'), mk.intro ? `${fmtTime(mk.intro.start)} → ${fmtTime(mk.intro.end)} (${src[mk.intro.source] || mk.intro.source})` : tr('non détectée')),
    row(tr('Générique de fin'), mk.credits ? `${fmtTime(mk.credits.start)} (${src[mk.credits.source] || mk.credits.source})` : tr('non détecté')),
    row(tr('Métadonnées'), { done: 'TMDB', local: tr('fichiers locaux'), nomatch: tr('non identifié'), error: tr('erreur'), pending: tr('en attente') }[d.meta_state] || d.meta_state),
  ), []);
}

async function addToPlaylist(d) {
  const lists = await get('/api/playlists');
  const name = h('input', { placeholder: tr('Nouvelle playlist…'), maxlength: 100 });
  const err = h('p.form-error');
  const add = async (pid) => {
    try {
      const r = await post(`/api/playlists/${pid}/items`, { item_id: d.id });
      toast(r.added > 1 ? tr('{added} épisodes ajoutés', { added: r.added }) : tr('Ajouté à la playlist'), 'ok');
      document.querySelector('.modal-overlay')?.remove();
    } catch (e) { err.textContent = e.message; }
  };
  await modal(tr('Ajouter à une playlist'), h('div',
    lists.length ? h('div.pick-list', lists.map((p) => h('button.pick-item', { onclick: () => add(p.id) },
      h('strong', p.title), h('span.muted.small', tr(' {count} élément(s)', { count: p.count }))))) : h('p.muted', tr('Aucune playlist pour l’instant.')),
    h('form.inline-form', { onsubmit: async (e) => {
      e.preventDefault();
      if (!name.value.trim()) return;
      try {
        await post('/api/playlists', { title: name.value.trim(), item_id: d.id });
        toast(tr('Playlist créée'), 'ok');
        document.querySelector('.modal-overlay')?.remove();
      } catch (ex) { err.textContent = ex.message; }
    } }, name, h('button.btn.primary', { type: 'submit' }, tr('Créer'))), err), []);
}

async function manageCollections(d, rerender) {
  const list = await get(`/api/admin/collections/for/${d.id}`);
  const name = h('input', { placeholder: tr('Nouvelle collection…'), maxlength: 100 });
  const err = h('p.form-error');
  const body = h('div',
    list.length ? h('div.checks.single', list.map((c) => {
      const t = h('label.toggle', h('input', { type: 'checkbox', checked: c.member, onchange: async (e) => {
        try {
          if (e.target.checked) await post(`/api/admin/collections/${c.id}/items`, { item_id: d.id });
          else await del(`/api/admin/collections/${c.id}/items/${d.id}`);
        } catch (ex) { err.textContent = ex.message; e.target.checked = !e.target.checked; }
      } }), h('span.toggle-ui'), h('span', c.title, c.kind === 'auto' ? h('span.muted.small', ' (TMDB)') : null));
      return t;
    })) : h('p.muted', tr('Aucune collection dans cette bibliothèque.')),
    h('form.inline-form', { onsubmit: async (e) => {
      e.preventDefault();
      if (!name.value.trim()) return;
      try { await post('/api/admin/collections', { title: name.value.trim(), item_id: d.id }); document.querySelector('.modal-overlay')?.remove(); manageCollections(d, rerender); }
      catch (ex) { err.textContent = ex.message; }
    } }, name, h('button.btn.primary', { type: 'submit' }, tr('Créer'))), err);
  await modal(tr('Collections'), body, [{ label: tr('Terminé'), primary: true, value: true }]);
  rerender();
}

function itemMenu(d, rerender) {
  const items = [{ label: d.watched ? tr('Marquer comme non vu') : tr('Marquer comme vu'), onClick: async () => {
    try {
      await post(`/api/items/${d.id}/watched`, { watched: !d.watched });
      toast(d.watched ? tr('Marqué comme non vu') : tr('Marqué comme vu'), 'ok');
      rerender();
    } catch (e) { toast(e.message, 'bad'); }
  } }];
  if (['movie', 'show', 'season', 'episode'].includes(d.type)) items.push({ label: tr('Ajouter à une playlist…'), onClick: () => addToPlaylist(d) });
  if (['movie', 'show', 'episode'].includes(d.type)) {
    items.push({ label: d.opinion === 0 ? tr('Suggérer à nouveau') : tr('Ne plus suggérer'), onClick: async () => {
      if (setOpinion) await setOpinion(0);
      d.opinion = d.opinion === 0 ? null : 0;
      rerender();
    } });
  }
  if (state.me.user.is_admin) {
    items.push({ divider: true }, { heading: tr('Administration') },
      { label: tr('Identifier…'), onClick: () => identify(d, rerender) },
      { label: tr('Actualiser les métadonnées'), onClick: async () => {
        try { await post(`/api/admin/refresh/${d.id}`); toast(tr('Métadonnées actualisées'), 'ok'); rerender(); }
        catch (e) { toast(e.message, 'bad'); }
      } });
    if (d.type === 'movie' || d.type === 'show') items.push({ label: tr('Collections…'), onClick: () => manageCollections(d, rerender) });
    if (d.type === 'movie' || d.type === 'episode') items.push({ label: tr('Informations sur le fichier'), onClick: () => fileInfo(d) });
  }
  return moreMenu(items);
}

// Avis : j'aime / je n'aime pas (un second clic retire l'avis) ; servent aux suggestions.
// (fonction gardée à part : l'objet du titre peut être rangé tel quel dans IndexedDB par un téléchargement)
let setOpinion = null;
function opinionButtons(d) {
  if (!['movie', 'show', 'episode'].includes(d.type)) return null;
  let value = d.opinion ?? null;
  const set = async (v) => {
    const next = value === v ? null : v;
    try {
      await put(`/api/items/${d.id}/opinion`, { value: next });
      value = next;
      paint();
      toast(next === 1 ? tr('Noté : j’aime') : next === -1 ? tr('Noté : je n’aime pas') : next === 0 ? tr('Ne sera plus suggéré') : tr('Avis retiré'), 'ok');
    } catch (e) { toast(e.message, 'bad'); }
  };
  const up = h('button.btn.ghost.icon-only', { type: 'button', onclick: () => set(1) }, icon('thumb-up'));
  const down = h('button.btn.ghost.icon-only', { type: 'button', onclick: () => set(-1) }, icon('thumb-down'));
  const paint = () => {
    for (const [b, v, label] of [[up, 1, tr('J’aime')], [down, -1, tr('Je n’aime pas')]]) {
      b.classList.toggle('on', value === v);
      b.setAttribute('aria-pressed', String(value === v));
      b.title = label;
      b.setAttribute('aria-label', label);
    }
  };
  paint();
  setOpinion = set;
  return [up, down];
}

function watchlistButton(d) {
  if (!['movie', 'show'].includes(d.type)) return null;
  let on = d.in_watchlist;
  const btn = h('button.btn.ghost.icon-only', { title: '', 'aria-pressed': String(on) });
  const paint = () => {
    btn.replaceChildren(icon(on ? 'bookmarked' : 'bookmark'));
    btn.title = on ? tr('Retirer de « À regarder »') : tr('Ajouter à « À regarder »');
    btn.setAttribute('aria-label', btn.title);
    btn.setAttribute('aria-pressed', String(on));
    btn.classList.toggle('on', on);
  };
  btn.addEventListener('click', async () => {
    try {
      await post(`/api/items/${d.id}/watchlist`, { on: !on });
      on = !on;
      paint();
      toast(on ? tr('Ajouté à « À regarder »') : tr('Retiré de « À regarder »'), 'ok');
    } catch (e) { toast(e.message, 'bad'); }
  });
  paint();
  return btn;
}

function trailerButton(d) {
  const local = (d.extras || []).find((e) => e.extra_kind === 'trailer');
  if (local) return h('a.btn.ghost', { href: `#/play/${local.id}` }, icon('film'), tr('Bande-annonce'));
  if (!d.trailer) return null;
  return h('button.btn.ghost', { onclick: () => {
    const frame = h('iframe.trailer-frame', { src: `https://www.youtube-nocookie.com/embed/${encodeURIComponent(d.trailer)}?autoplay=1&rel=0`,
      allow: 'autoplay; encrypted-media; picture-in-picture; fullscreen', allowfullscreen: true, title: tr('Bande-annonce') });
    modal(tr('Bande-annonce — {title}', { title: d.title }), h('div.trailer-box', frame), []);
  } }, icon('film'), tr('Bande-annonce'));
}

// Choix audio / sous-titres avant la lecture (retenu pour le film ou la série)
function trackChooser(d, target, onChange) {
  const audio = d.audio || [];
  const subs = d.subtitles || [];
  if (!audio.length && !subs.length) return null;
  const prefs = state.me.prefs;
  let a = pickAudio(audio, prefs, d.track_prefs);
  let s = pickSubtitle(subs, a, prefs, d.track_prefs);
  const aSel = select(audio.map((x) => [x.id, x.title]), a ? a.id : '', { 'aria-label': tr('Langue audio') });
  const subOptions = () => [['0', tr('Aucun')], ...menuSubs(subs, a).map((x) => [x.id, x.forced ? `${x.title} — ${SIGNS_HINT}` : x.title])];
  const sSel = select(subOptions(), s ? s.id : '0', { 'aria-label': tr('Sous-titres') });
  const save = () => {
    a = audio.find((x) => String(x.id) === aSel.value) || null;
    s = sSel.value === '0' ? null : subs.find((x) => String(x.id) === sSel.value) || null;
    s = fixForced(s, a, subs);
    // la liste dépend de l'audio (pistes forcées visibles seulement si elles servent)
    sSel.replaceChildren(...subOptions().map(([v, l]) => h('option', { value: v, selected: String(v) === String(s ? s.id : '0') }, l)));
    put(`/api/items/${d.id}/tracks`, trackPayload(a, s)).catch((e) => toast(e.message, 'bad'));
    onChange(a, s);
  };
  aSel.addEventListener('change', save);
  sSel.addEventListener('change', save);
  onChange(a, s);
  return h('div.track-chooser',
    audio.length ? h('label.track-field', h('span', tr('Audio')), aSel) : null,
    h('label.track-field', h('span', tr('Sous-titres')), sSel),
    target ? h('span.muted.small', tr('pour {p1}, retenu pour toute la série', { p1: episodeCode(target) })) : null);
}

// « Regarder ensemble » : lance ce film / cet épisode dans un groupe dont on fait partie (salon créé à
// l'avance), ou crée un groupe dessus, puis ouvre le lecteur
async function watchTogether(target) {
  const lobbies = launchableGroups();
  if (lobbies.length) {
    const what = target.type === 'episode' ? `${target.show} · ${episodeCode(target)} — ${target.title}` : target.title;
    const pick = await modal(tr('Regarder ensemble'), h('div.form', h('p.small', h('strong', what)),
      h('p.muted.small', tr('Vous faites partie d’un groupe : la lecture démarrera en même temps pour tous ses membres.'))), [
      { label: tr('Nouveau groupe'), value: 'new' },
      ...lobbies.slice(0, 2).map((g) => ({ label: tr('Lancer dans « {name} »', { name: g.name }), primary: true, value: g.id })),
    ]);
    if (!pick) return;
    if (pick !== 'new') {
      try {
        await post(`/api/together/${pick}/cmd`, { action: 'item', item_id: target.id,
          position: target.position && !target.watched ? target.position : 0 });
        location.hash = `#/play/${target.id}?group=${pick}`;
      } catch (e) { toast(e.message, 'bad'); }
      return;
    }
  }
  const name = h('input', { placeholder: tr('Chez {display_name}', { display_name: state.me.user.display_name }), maxlength: 60 });
  const hostOnly = toggle(tr('Seul l’hôte contrôle la lecture (pause, avance, épisode suivant)'), false);
  const err = h('p.form-error');
  const g = await modal(tr('Regarder ensemble'), h('div.form',
    h('p.muted.small', tr('Chaque membre regarde sur son propre appareil ; lecture, pauses, sauts et épisode suivant sont synchronisés. Le lien d’invitation est disponible dans le lecteur, et le groupe apparaît aussi sur l’accueil des autres utilisateurs.')),
    h('p.small', h('strong', target.type === 'episode' ? `${target.show} · ${episodeCode(target)} — ${target.title}` : target.title)),
    field(tr('Nom du groupe'), name), hostOnly, err), [
    { label: tr('Annuler'), value: null },
    { label: tr('Créer et lancer'), primary: true, handler: async () => {
      try {
        return await post('/api/together', { item_id: target.id, position: target.position && !target.watched ? target.position : 0,
          name: name.value.trim() || undefined, host_only: hostOnly.querySelector('input').checked });
      } catch (e) { err.textContent = e.message; return false; }
    } },
  ]);
  if (g) location.hash = `#/play/${target.id}?group=${g.id}`;
}

// ------------------------------------------------------------ téléchargements (regarder hors ligne)
// « Auto » : le fichier tel quel dès que l'appareil sait le lire (une copie ne coûte presque rien au serveur,
// une conversion environ 30 s de processeur par minute de vidéo)
const SEASON_QUALITIES = [['auto', tr('Automatique : sans conversion si possible (recommandé)')],
  ['1080p', tr('1080p au plus')], ['720p', tr('720p (converti, fichier plus léger)')],
  ['480p', tr('480p (converti, fichier le plus léger)')]];
const dlQuality = () => { try { return localStorage.getItem('nexis.dl.quality') || 'auto'; } catch { return 'auto'; } };
const GB = 1024 ** 3;
const fmtSize = (b) => (!b ? tr('taille inconnue') : b >= GB ? tr('{n} Go', { n: (b / GB).toLocaleString(locale(), { maximumFractionDigits: 1, minimumFractionDigits: 1 }) }) : tr('{n} Mo', { n: Math.round(b / 1024 ** 2) }));

async function qualityChoices(one, tracks) {
  let opts;
  try {
    opts = (await post('/api/downloads/options', { id: one.id, audio: tracks.audio, burn: tracks.burn, caps: deviceCaps() })).options;
  } catch { return SEASON_QUALITIES; }
  const seen = new Set();
  const out = [];
  for (const o of opts) {
    const sig = `${o.convert}:${o.height}`;
    if (seen.has(sig)) continue; // même résultat qu'une option déjà proposée
    seen.add(sig);
    const what = o.quality === 'auto' ? (o.convert ? tr('Auto ({height}p)', { height: o.height }) : tr('Qualité d’origine{p1}', { p1: o.height ? ` (${o.height}p)` : '' })) : o.quality;
    out.push([o.quality, `${what} · ${fmtSize(o.size)} · ${o.convert ? tr('conversion par le serveur') : tr('sans conversion')}`]);
  }
  return out;
}
const canDownload = () => state.me.features && state.me.features.downloads && offlineSupported();

// choix des pistes comme au lancement de la lecture (langues préférées, sous-titres forcés…)
function defaultTracks(it) {
  const prefs = state.me.prefs;
  const audio = pickAudio(it.audio || [], prefs, it.track_prefs);
  const sub = fixForced(pickSubtitle(it.subtitles || [], audio, prefs, it.track_prefs), audio, it.subtitles || []);
  return { audio: audio ? audio.id : null, subs: sub && sub.overlay ? [sub.id] : [], burn: sub && !sub.overlay ? sub.id : null };
}

async function startDownloads(items, opts) {
  try { localStorage.setItem('nexis.dl.quality', opts.quality); } catch { /* ignore */ }
  for (const it of items) {
    const full = it.audio ? it : await get(`/api/items/${it.id}`); // les épisodes d'une saison : pistes à connaître
    await offlineDownload(state.me.user.id, full, { ...defaultTracks(full), ...opts.tracks, quality: opts.quality });
  }
  toast(items.length > 1 ? tr('{length} épisodes ajoutés aux téléchargements', { length: items.length }) : tr('Téléchargement lancé'), 'ok',
    { label: tr('Voir'), onClick: () => { location.hash = '#/downloads'; } });
}

async function downloadDialog(items, title) {
  const one = items.length === 1 && items[0].audio ? items[0] : null;
  const choices = one ? await qualityChoices(one, defaultTracks(one)) : SEASON_QUALITIES;
  const wanted = dlQuality();
  const quality = select(choices, choices.some(([q]) => q === wanted) ? wanted : choices[0][0]);
  const rows = [field(tr('Qualité'), quality, tr('Sans conversion, le fichier est copié tel quel. Une conversion s’exécute en arrière-plan sur le serveur et se met en pause pendant les lectures.'))];
  let audioSel = null;
  const subChecks = [];
  let burnSel = null;
  if (one) {
    const d = defaultTracks(one);
    if ((one.audio || []).length > 1) {
      audioSel = select(one.audio.map((a) => [String(a.id), a.title]), String(d.audio));
      rows.push(field(tr('Langue'), audioSel));
    }
    const text = (one.subtitles || []).filter((x) => x.overlay);
    if (text.length) {
      rows.push(field(tr('Sous-titres à garder'), h('div.checks.single', text.map((x) => {
        const t = toggle(x.title, d.subs.includes(x.id));
        subChecks.push([x.id, t.querySelector('input')]);
        return t;
      }))));
    }
    const image = (one.subtitles || []).filter((x) => !x.overlay);
    if (image.length) {
      burnSel = select([['', tr('Aucun')], ...image.map((x) => [String(x.id), x.title])], d.burn ? String(d.burn) : '');
      rows.push(field(tr('Sous-titres image à incruster'), burnSel, tr('Les sous-titres sont incrustés dans l’image (ils ne pourront plus être masqués), ce qui impose une conversion par le serveur.')));
    }
  } else {
    rows.push(h('p.muted.small', tr('Langue et sous-titres : selon vos préférences de lecture.')));
  }
  const ok = await modal(tr('Télécharger {title}', { title }), h('div.form', ...rows), [
    { label: tr('Annuler'), value: false },
    { label: tr('Télécharger'), primary: true, value: true },
  ]);
  if (!ok) return;
  const tracks = one ? {
    audio: audioSel ? Number(audioSel.value) : defaultTracks(one).audio,
    subs: subChecks.filter(([, c]) => c.checked).map(([id]) => id),
    burn: burnSel && burnSel.value ? Number(burnSel.value) : null,
  } : {};
  startDownloads(items, { quality: quality.value, tracks });
}

// Bouton qui suit l'état du téléchargement de ce titre sur l'appareil
function downloadButton(d) {
  if (!canDownload() || !['movie', 'episode'].includes(d.type)) return null;
  const btn = h('button.btn.ghost.icon-only', { title: tr('Télécharger'), 'aria-label': tr('Télécharger pour regarder hors ligne') }, icon('install'));
  const paint = async () => {
    if (!btn.isConnected && btn.dataset.painted) { stop(); return; }
    btn.dataset.painted = '1';
    const rec = await findDownload(state.me.user.id, d.id);
    const st = rec ? rec.state : null;
    btn.classList.toggle('on', st === 'done');
    btn.replaceChildren(icon(st === 'done' ? 'check' : 'install'));
    const label = st === 'done' ? tr('Téléchargé : lire hors ligne') : st && st !== 'error' ? tr('Téléchargement en cours') : tr('Télécharger pour regarder hors ligne');
    btn.title = label;
    btn.setAttribute('aria-label', label);
    btn.onclick = () => {
      if (st === 'done') location.hash = `#/offline/${d.id}`;
      else if (st && st !== 'error') location.hash = '#/downloads';
      else downloadDialog([d], d.type === 'episode' ? `${episodeCode(d)} · ${d.title}` : d.title);
    };
  };
  const stop = onDownloads(paint);
  paint();
  return btn;
}

function togetherButton(target) {
  if (!target) return null;
  return h('button.btn.ghost.icon-only', { title: tr('Regarder ensemble'), 'aria-label': tr('Regarder ensemble'),
    onclick: () => watchTogether(target) }, icon('group'));
}

function playButtons(c) {
  const out = [];
  if (c.position > 0 && !c.watched) {
    out.push(h('a.btn.primary', { href: `#/play/${c.id}?resume=1` }, icon('play'), tr('Reprendre à {p1}', { p1: fmtTime(c.position) })));
    out.push(h('a.btn.ghost', { href: `#/play/${c.id}` }, tr('Depuis le début')));
  } else {
    out.push(h('a.btn.primary', { href: `#/play/${c.id}` }, icon('play'), tr('bouton|Lecture')));
  }
  return out;
}


function metaLine(d) {
  const bits = [];
  if (d.type === 'episode') bits.push(episodeCode(d));
  if (d.year) bits.push(d.year);
  if (d.duration) bits.push(fmtDuration(d.duration));
  if (d.content_rating) bits.push(d.content_rating);
  if (d.rating) bits.push(`★ ${Number(d.rating).toFixed(1)}`);
  if (d.media && d.media.resolution) bits.push(/^\d+$/.test(d.media.resolution) ? `${d.media.resolution}p` : d.media.resolution.toUpperCase());
  if (d.type === 'show' && d.leaves) bits.push(tr('{p1}/{leaves} épisodes vus', { p1: d.watched_count || 0, leaves: d.leaves }));
  return h('div.meta-line', bits.map((b) => h('span', String(b))));
}

function episodeRow(e, refresh) {
  return h('div.episode', { class: e.watched ? 'episode watched' : 'episode' },
    h('a.episode-thumb', { href: `#/play/${e.id}${e.position && !e.watched ? '?resume=1' : ''}`, 'aria-label': tr('Lire {title}', { title: e.title }),
      class: e.watched ? 'episode-thumb' : 'episode-thumb spoiler-img' },
      e.thumb ? h('img', { src: img(e.thumb, 320, 180), alt: '', loading: 'lazy' }) : h('div.noimg'),
      h('span.play-hover', icon('play')), progressBar(e.position, e.duration)),
    h('div.episode-body',
      h('div.episode-head',
        h('a.episode-title', { href: `#/item/${e.id}` }, `${e.episode}. ${e.title}`),
        h('span.muted', fmtDuration(e.duration)),
        h('button.icon-btn.watch-toggle', { title: e.watched ? tr('Marquer non vu') : tr('Marquer vu'),
          class: e.watched ? 'icon-btn watch-toggle on' : 'icon-btn watch-toggle',
          onclick: async () => {
            try { await post(`/api/items/${e.id}/watched`, { watched: !e.watched }); refresh(); } catch (x) { toast(x.message, 'bad'); }
          } }, icon('check'))),
      e.summary ? h('p.episode-summary', { class: e.watched ? 'episode-summary' : 'episode-summary spoiler' }, e.summary) : null));
}

export async function renderItem(main, rk, ctx) {
  const d = await get(`/api/items/${rk}`);
  if (!ctx.isCurrent()) return;
  if (d.type === 'season') { location.replace(`#/item/${d.show_id}?season=${d.id}`); return; }
  document.title = `${d.type === 'episode' ? d.show : d.title} · Nexis`;
  const rerender = () => renderItem(main, rk, ctx);
  const art = d.type === 'episode' ? (d.show_art || d.art) : d.art;
  const poster = d.type === 'episode' ? d.show_thumb : d.thumb;

  const actions = h('div.detail-actions');
  const playLinks = [];
  const withTracks = (href, au, su) => {
    const q = [];
    if (au) q.push(`a=${au.id}`);
    q.push(`s=${su ? su.id : 0}`);
    return href + (href.includes('?') ? '&' : '?') + q.join('&');
  };
  if (d.type === 'show') {
    const t = d.play_target;
    if (t) {
      const label = t.position > 0 ? tr('Reprendre {p1}', { p1: episodeCode(t) }) : tr('Lire {p1}', { p1: episodeCode(t) });
      const link = h('a.btn.primary', { href: `#/play/${t.id}${t.position > 0 ? '?resume=1' : ''}` }, icon('play'), label);
      link.dataset.base = link.getAttribute('href');
      playLinks.push(link);
      actions.append(link);
    }
  } else {
    for (const link of playButtons(d)) {
      link.dataset.base = link.getAttribute('href');
      playLinks.push(link);
      actions.append(link);
    }
  }
  const together = d.type === 'show' ? togetherButton(d.play_target ? { ...d.play_target, show: d.title } : null)
    : (['movie', 'episode'].includes(d.type) ? togetherButton(d) : null);
  actions.append(...[watchlistButton(d), ...(opinionButtons(d) || []), together, downloadButton(d), trailerButton(d)].filter(Boolean), itemMenu(d, rerender));
  const chooser = trackChooser(d, d.type === 'show' ? d.play_target : null, (au, su) => {
    for (const l of playLinks) l.setAttribute('href', withTracks(l.dataset.base, au, su));
  });

  const info = h('div.detail-info',
    d.type === 'episode' ? h('a.detail-kicker', { href: `#/item/${d.show_id}` }, d.show) : null,
    h('h1.detail-title', d.title),
    metaLine(d),
    d.tagline ? h('p.tagline', d.tagline) : null,
    actions,
    chooser,
    d.summary ? h('p.summary', { class: d.type === 'episode' && !d.watched ? 'summary spoiler' : 'summary' }, d.summary) : null,
    d.genres && d.genres.length ? h('div.chips', d.genres.map((g) => h('span.chip', g))) : null,
    h('dl.facts',
      d.directors && d.directors.length ? [h('dt', tr('Réalisation')), h('dd', d.directors.join(', '))] : null,
      d.studio ? [h('dt', tr('Studio')), h('dd', d.studio)] : null,
      null));

  const parts = [
    h('section.detail', art ? { style: { '--backdrop': `url("${img(art, 1600, 900)}")` } } : null,
      h('div.detail-backdrop'),
      h('div.detail-inner',
        h('div.detail-poster', poster ? h('img', { src: img(poster, 300, 450), alt: '' }) : h('div.noimg', d.title)),
        info)),
  ];

  const chapters = (d.chapters || []).filter((c) => c.end > c.start);
  if (['movie', 'episode'].includes(d.type) && chapters.length >= 2) {
    parts.push(h('section.block', h('h2', tr('Chapitres')),
      chapterStrip(chapters, { href: (c) => `#/play/${d.id}?t=${c.start + 1}` })));
  }

  if (d.extras && d.extras.length) {
    parts.push(h('section.block', h('h2', tr('Bonus')), h('div.grid.wide', d.extras.map((e) => h('a.card.wide', { href: `#/play/${e.id}` },
      h('div.card-img', h('img', { src: img(e.thumb, 400, 225), alt: '', loading: 'lazy' }), h('span.play-hover', icon('play'))),
      h('div.card-title', e.title === d.title ? e.extra_label : e.title),
      h('div.card-sub', `${e.extra_label} · ${fmtTime(e.duration, false)}`))))));
  }
  for (const c of d.collections || []) {
    const others = c.items.filter((x) => x.id !== d.id);
    if (!others.length) continue;
    parts.push(h('section.block', h('div.row-head', h('a.row-title', { href: `#/collection/${c.id}` }, tr('Collection : {title}', { title: c.title }))),
      h('div.grid.small', others.map((x) => posterCard(x)))));
  }
  // titres proches (genres, réalisateur, acteurs), chargés après la fiche
  if (['movie', 'show', 'episode'].includes(d.type)) {
    const similar = h('section.block', { hidden: true });
    parts.push(similar);
    get(`/api/items/${d.id}/similar`).then((r) => {
      if (!r.items.length || !similar.isConnected) return;
      similar.append(h('h2', d.type === 'movie' ? tr('Films similaires') : tr('Titres similaires')),
        h('div.grid.small', r.items.slice(0, 12).map((x) => posterCard(x))));
      similar.hidden = false;
    }).catch(() => {});
  }
  if (d.cast && d.cast.length) {
    parts.push(h('section.block', h('h2', tr('Distribution')),
      h('div.cast', d.cast.map((p) => h('div.person', h('span.person-init', p.name.charAt(0)),
        h('div', h('strong', p.name), p.role ? h('div.muted', p.role) : null))))));
  }

  if (d.type === 'episode' && d.next) {
    parts.push(h('section.block', h('h2', tr('Épisode suivant')), h('div.episodes', episodeRow({ ...d.next, summary: null }, rerender))));
  }

  if (d.type === 'show' && d.seasons.length) {
    const wanted = ctx.query.get('season');
    let current = d.seasons.find((s) => s.id === wanted)
      || d.seasons.find((s) => d.play_target && s.id === d.play_target.season_id) || d.seasons[0];
    const list = h('div.episodes');
    const tabs = h('div.tabs', { role: 'tablist' });
    const loadSeason = async (s) => {
      current = s;
      tabs.querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.id === s.id));
      list.replaceChildren(spinner());
      try {
        const eps = await get(`/api/items/${s.id}/children`);
        list.replaceChildren(...eps.map((e) => episodeRow(e, () => loadSeason(current))));
      } catch (e) { list.replaceChildren(h('p.form-error', e.message)); }
    };
    for (const s of d.seasons) {
      tabs.append(h('button.tab', { role: 'tab', dataset: { id: s.id }, onclick: () => loadSeason(s) },
        s.index === 0 ? tr('Spéciaux') : tr('Saison {index}', { index: s.index }),
        s.watched ? h('span.tab-done', icon('check')) : null));
    }
    parts.push(h('section.block', h('div.season-head', tabs,
      h('button.btn.small.ghost', { onclick: async () => {
        await post(`/api/items/${current.id}/watched`, { watched: !current.watched });
        rerender();
      } }, tr('Saison vue / non vue')),
      canDownload() ? h('button.btn.small.ghost', { onclick: async () => {
        const eps = await get(`/api/items/${current.id}/children`);
        const todo = eps.filter((e) => !e.watched);
        const pick = todo.length ? todo : eps;
        const name = current.index === 0 ? tr('les spéciaux') : tr('la saison {index}', { index: current.index });
        downloadDialog(pick, todo.length && todo.length < eps.length ? plural(pick.length, '{name} ({n} épisode non vu)', '{name} ({n} épisodes non vus)', { name }) : plural(pick.length, '{name} ({n} épisode)', '{name} ({n} épisodes)', { name }));
      } }, icon('install'), tr('Télécharger la saison')) : null), list));
    loadSeason(current);
  }

  if (d.type === 'show' && d.seasons.length > 1) {
    parts.push(h('section.block', h('h2', tr('Saisons')),
      h('div.grid.small', d.seasons.map((s) => posterCard({ ...s, title: s.index === 0 ? tr('Spéciaux') : tr('Saison {index}', { index: s.index }) },
        { href: `#/item/${d.id}?season=${s.id}` })))));
  }
  clear(main, parts);
}
