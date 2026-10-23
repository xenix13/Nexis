import { del, get, post, put } from '../api.js';
import { state } from '../app.js';
import { SIGNS_HINT, fixForced, menuSubs, pickAudio, pickSubtitle, trackPayload } from '../tracks.js';
import { chapterStrip } from '../chapters.js';
import { clear, episodeCode, field, fmtDuration, fmtTime, h, icon, img, modal, moreMenu, posterCard, progressBar, select, spinner, toast, toggle } from '../ui.js';

async function identify(d, onDone) {
  const q = h('input', { value: d.type === 'episode' ? d.show : d.title });
  const year = h('input', { type: 'number', value: d.type === 'episode' ? '' : (d.year || ''), placeholder: 'Année' });
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
            toast(`Identifié : ${x.title}`, 'ok');
            document.querySelector('.modal-overlay')?.remove();
            onDone();
          } catch (e) { toast(e.message, 'bad'); search(); }
        } },
      x.poster ? h('img', { src: x.poster, alt: '' }) : h('div.noimg'),
      h('div', h('strong', x.title), h('div.muted.small', [x.year, x.original_title !== x.title ? x.original_title : null].filter(Boolean).join(' · ')),
        h('p.small', (x.overview || '').slice(0, 220))))) : [h('p.muted', 'Aucun résultat.')]));
    } catch (e) { results.replaceChildren(h('p.form-error', e.message)); }
  };
  const form = h('form.identify-form', { onsubmit: (e) => { e.preventDefault(); search(); } },
    h('div.grid-search', field('Titre', q), field('Année', year), h('button.btn.primary', { type: 'submit' }, 'Rechercher')));
  search();
  await modal('Identifier sur TMDB', h('div', form, results), []);
}

function fileInfo(d) {
  const m = d.media || {};
  const mk = d.markers || {};
  const src = { manual: 'posé à la main', chapters: 'chapitres', 'chapters-guess': 'chapitres (estimé)', audio: 'empreinte audio' };
  const row = (k, v) => (v ? [h('dt', k), h('dd', v)] : null);
  modal('Informations sur le fichier', h('dl.facts.file-facts',
    row('Fichier', d.path ? h('code', d.path) : null),
    row('Conteneur', (m.container || '').toUpperCase()),
    row('Vidéo', [m.video_codec, m.resolution ? `${m.resolution}p` : null].filter(Boolean).join(' · ').toUpperCase()),
    row('Débit', m.bitrate ? `${m.bitrate} kb/s` : null),
    row('Audio', (d.audio || []).map((a) => a.title).join(' · ')),
    row('Sous-titres', (d.subtitles || []).map((x) => x.title).join(' · ')),
    row('Chapitres', d.chapters && d.chapters.length ? `${d.chapters.length}` : 'aucun'),
    row('Intro', mk.intro ? `${fmtTime(mk.intro.start)} → ${fmtTime(mk.intro.end)} (${src[mk.intro.source] || mk.intro.source})` : 'non détectée'),
    row('Générique de fin', mk.credits ? `${fmtTime(mk.credits.start)} (${src[mk.credits.source] || mk.credits.source})` : 'non détecté'),
    row('Métadonnées', { done: 'TMDB', local: 'fichiers locaux', nomatch: 'non identifié', error: 'erreur', pending: 'en attente' }[d.meta_state] || d.meta_state),
  ), []);
}

async function addToPlaylist(d) {
  const lists = await get('/api/playlists');
  const name = h('input', { placeholder: 'Nouvelle playlist…', maxlength: 100 });
  const err = h('p.form-error');
  const add = async (pid) => {
    try {
      const r = await post(`/api/playlists/${pid}/items`, { item_id: d.id });
      toast(r.added > 1 ? `${r.added} épisodes ajoutés` : 'Ajouté à la playlist', 'ok');
      document.querySelector('.modal-overlay')?.remove();
    } catch (e) { err.textContent = e.message; }
  };
  await modal('Ajouter à une playlist', h('div',
    lists.length ? h('div.pick-list', lists.map((p) => h('button.pick-item', { onclick: () => add(p.id) },
      h('strong', p.title), h('span.muted.small', ` ${p.count} élément(s)`)))) : h('p.muted', 'Aucune playlist pour l’instant.'),
    h('form.inline-form', { onsubmit: async (e) => {
      e.preventDefault();
      if (!name.value.trim()) return;
      try {
        await post('/api/playlists', { title: name.value.trim(), item_id: d.id });
        toast('Playlist créée', 'ok');
        document.querySelector('.modal-overlay')?.remove();
      } catch (ex) { err.textContent = ex.message; }
    } }, name, h('button.btn.primary', { type: 'submit' }, 'Créer')), err), []);
}

async function manageCollections(d, rerender) {
  const list = await get(`/api/admin/collections/for/${d.id}`);
  const name = h('input', { placeholder: 'Nouvelle collection…', maxlength: 100 });
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
    })) : h('p.muted', 'Aucune collection dans cette bibliothèque.'),
    h('form.inline-form', { onsubmit: async (e) => {
      e.preventDefault();
      if (!name.value.trim()) return;
      try { await post('/api/admin/collections', { title: name.value.trim(), item_id: d.id }); document.querySelector('.modal-overlay')?.remove(); manageCollections(d, rerender); }
      catch (ex) { err.textContent = ex.message; }
    } }, name, h('button.btn.primary', { type: 'submit' }, 'Créer')), err);
  await modal('Collections', body, [{ label: 'Terminé', primary: true, value: true }]);
  rerender();
}

function itemMenu(d, rerender) {
  const items = [{ label: d.watched ? 'Marquer comme non vu' : 'Marquer comme vu', onClick: async () => {
    try {
      await post(`/api/items/${d.id}/watched`, { watched: !d.watched });
      toast(d.watched ? 'Marqué comme non vu' : 'Marqué comme vu', 'ok');
      rerender();
    } catch (e) { toast(e.message, 'bad'); }
  } }];
  if (['movie', 'show', 'season', 'episode'].includes(d.type)) items.push({ label: 'Ajouter à une playlist…', onClick: () => addToPlaylist(d) });
  if (state.me.user.is_admin) {
    items.push({ divider: true }, { heading: 'Administration' },
      { label: 'Identifier…', onClick: () => identify(d, rerender) },
      { label: 'Actualiser les métadonnées', onClick: async () => {
        try { await post(`/api/admin/refresh/${d.id}`); toast('Métadonnées actualisées', 'ok'); rerender(); }
        catch (e) { toast(e.message, 'bad'); }
      } });
    if (d.type === 'movie' || d.type === 'show') items.push({ label: 'Collections…', onClick: () => manageCollections(d, rerender) });
    if (d.type === 'movie' || d.type === 'episode') items.push({ label: 'Informations sur le fichier', onClick: () => fileInfo(d) });
  }
  return moreMenu(items);
}

function watchlistButton(d) {
  if (!['movie', 'show'].includes(d.type)) return null;
  let on = d.in_watchlist;
  const btn = h('button.btn.ghost.icon-only', { title: '', 'aria-pressed': String(on) });
  const paint = () => {
    btn.replaceChildren(icon(on ? 'bookmarked' : 'bookmark'));
    btn.title = on ? 'Retirer de « À regarder »' : 'Ajouter à « À regarder »';
    btn.setAttribute('aria-label', btn.title);
    btn.setAttribute('aria-pressed', String(on));
    btn.classList.toggle('on', on);
  };
  btn.addEventListener('click', async () => {
    try {
      await post(`/api/items/${d.id}/watchlist`, { on: !on });
      on = !on;
      paint();
      toast(on ? 'Ajouté à « À regarder »' : 'Retiré de « À regarder »', 'ok');
    } catch (e) { toast(e.message, 'bad'); }
  });
  paint();
  return btn;
}

function trailerButton(d) {
  const local = (d.extras || []).find((e) => e.extra_kind === 'trailer');
  if (local) return h('a.btn.ghost', { href: `#/play/${local.id}` }, icon('film'), 'Bande-annonce');
  if (!d.trailer) return null;
  return h('button.btn.ghost', { onclick: () => {
    const frame = h('iframe.trailer-frame', { src: `https://www.youtube-nocookie.com/embed/${encodeURIComponent(d.trailer)}?autoplay=1&rel=0`,
      allow: 'autoplay; encrypted-media; picture-in-picture; fullscreen', allowfullscreen: true, title: 'Bande-annonce' });
    modal(`Bande-annonce — ${d.title}`, h('div.trailer-box', frame), []);
  } }, icon('film'), 'Bande-annonce');
}

// Choix audio / sous-titres avant la lecture (retenu pour le film ou la série)
function trackChooser(d, target, onChange) {
  const audio = d.audio || [];
  const subs = d.subtitles || [];
  if (!audio.length && !subs.length) return null;
  const prefs = state.me.prefs;
  let a = pickAudio(audio, prefs, d.track_prefs);
  let s = pickSubtitle(subs, a, prefs, d.track_prefs);
  const aSel = select(audio.map((x) => [x.id, x.title]), a ? a.id : '', { 'aria-label': 'Langue audio' });
  const subOptions = () => [['0', 'Aucun'], ...menuSubs(subs, a).map((x) => [x.id, x.forced ? `${x.title} — ${SIGNS_HINT}` : x.title])];
  const sSel = select(subOptions(), s ? s.id : '0', { 'aria-label': 'Sous-titres' });
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
    audio.length ? h('label.track-field', h('span', 'Audio'), aSel) : null,
    h('label.track-field', h('span', 'Sous-titres'), sSel),
    target ? h('span.muted.small', `pour ${episodeCode(target)}, retenu pour toute la série`) : null);
}

// « Regarder ensemble » : crée un groupe sur ce film / cet épisode puis ouvre le lecteur
async function watchTogether(target) {
  const name = h('input', { placeholder: `Chez ${state.me.user.display_name}`, maxlength: 60 });
  const hostOnly = toggle('Seul moi peux mettre en pause, avancer ou changer d’épisode', false);
  const err = h('p.form-error');
  const g = await modal('Regarder ensemble', h('div.form',
    h('p.muted.small', 'Chaque membre regarde depuis son écran (ou un autre appareil) : lecture, pauses, sauts et épisode suivant sont partagés. Ceux qui rejoignent arrivent au même endroit. Partage le lien d’invitation depuis le lecteur, ou ils trouveront le groupe sur l’accueil.'),
    h('p.small', h('strong', target.type === 'episode' ? `${target.show} · ${episodeCode(target)} — ${target.title}` : target.title)),
    field('Nom du groupe', name), hostOnly, err), [
    { label: 'Annuler', value: null },
    { label: 'Créer et lancer', primary: true, handler: async () => {
      try {
        return await post('/api/together', { item_id: target.id, position: target.position && !target.watched ? target.position : 0,
          name: name.value.trim() || undefined, host_only: hostOnly.querySelector('input').checked });
      } catch (e) { err.textContent = e.message; return false; }
    } },
  ]);
  if (g) location.hash = `#/play/${target.id}?group=${g.id}`;
}

function togetherButton(target) {
  if (!target) return null;
  return h('button.btn.ghost.icon-only', { title: 'Regarder ensemble', 'aria-label': 'Regarder ensemble',
    onclick: () => watchTogether(target) }, icon('group'));
}

function playButtons(c) {
  const out = [];
  if (c.position > 0 && !c.watched) {
    out.push(h('a.btn.primary', { href: `#/play/${c.id}?resume=1` }, icon('play'), `Reprendre à ${fmtTime(c.position)}`));
    out.push(h('a.btn.ghost', { href: `#/play/${c.id}` }, 'Depuis le début'));
  } else {
    out.push(h('a.btn.primary', { href: `#/play/${c.id}` }, icon('play'), 'Lecture'));
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
  if (d.type === 'show' && d.leaves) bits.push(`${d.watched_count || 0}/${d.leaves} épisodes vus`);
  return h('div.meta-line', bits.map((b) => h('span', String(b))));
}

function episodeRow(e, refresh) {
  return h('div.episode', { class: e.watched ? 'episode watched' : 'episode' },
    h('a.episode-thumb', { href: `#/play/${e.id}${e.position && !e.watched ? '?resume=1' : ''}`, 'aria-label': `Lire ${e.title}` },
      e.thumb ? h('img', { src: img(e.thumb, 320, 180), alt: '', loading: 'lazy' }) : h('div.noimg'),
      h('span.play-hover', icon('play')), progressBar(e.position, e.duration)),
    h('div.episode-body',
      h('div.episode-head',
        h('a.episode-title', { href: `#/item/${e.id}` }, `${e.episode}. ${e.title}`),
        h('span.muted', fmtDuration(e.duration)),
        h('button.icon-btn.watch-toggle', { title: e.watched ? 'Marquer non vu' : 'Marquer vu',
          class: e.watched ? 'icon-btn watch-toggle on' : 'icon-btn watch-toggle',
          onclick: async () => {
            try { await post(`/api/items/${e.id}/watched`, { watched: !e.watched }); refresh(); } catch (x) { toast(x.message, 'bad'); }
          } }, icon('check'))),
      e.summary ? h('p.episode-summary', e.summary) : null));
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
      const label = t.position > 0 ? `Reprendre ${episodeCode(t)}` : `Lire ${episodeCode(t)}`;
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
  actions.append(...[watchlistButton(d), together, trailerButton(d)].filter(Boolean), itemMenu(d, rerender));
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
    d.summary ? h('p.summary', d.summary) : null,
    d.genres && d.genres.length ? h('div.chips', d.genres.map((g) => h('span.chip', g))) : null,
    h('dl.facts',
      d.directors && d.directors.length ? [h('dt', 'Réalisation'), h('dd', d.directors.join(', '))] : null,
      d.studio ? [h('dt', 'Studio'), h('dd', d.studio)] : null,
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
    parts.push(h('section.block', h('h2', 'Chapitres'),
      chapterStrip(chapters, { href: (c) => `#/play/${d.id}?t=${c.start + 1}` })));
  }

  if (d.extras && d.extras.length) {
    parts.push(h('section.block', h('h2', 'Bonus'), h('div.grid.wide', d.extras.map((e) => h('a.card.wide', { href: `#/play/${e.id}` },
      h('div.card-img', h('img', { src: img(e.thumb, 400, 225), alt: '', loading: 'lazy' }), h('span.play-hover', icon('play'))),
      h('div.card-title', e.title === d.title ? e.extra_label : e.title),
      h('div.card-sub', `${e.extra_label} · ${fmtTime(e.duration, false)}`))))));
  }
  for (const c of d.collections || []) {
    const others = c.items.filter((x) => x.id !== d.id);
    if (!others.length) continue;
    parts.push(h('section.block', h('div.row-head', h('a.row-title', { href: `#/collection/${c.id}` }, `Collection : ${c.title}`)),
      h('div.grid.small', others.map((x) => posterCard(x)))));
  }
  if (d.cast && d.cast.length) {
    parts.push(h('section.block', h('h2', 'Distribution'),
      h('div.cast', d.cast.map((p) => h('div.person', h('span.person-init', p.name.charAt(0)),
        h('div', h('strong', p.name), p.role ? h('div.muted', p.role) : null))))));
  }

  if (d.type === 'episode' && d.next) {
    parts.push(h('section.block', h('h2', 'Épisode suivant'), h('div.episodes', episodeRow({ ...d.next, summary: null }, rerender))));
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
        s.index === 0 ? 'Spéciaux' : `Saison ${s.index}`,
        s.watched ? h('span.tab-done', icon('check')) : null));
    }
    parts.push(h('section.block', h('div.season-head', tabs,
      h('button.btn.small.ghost', { onclick: async () => {
        await post(`/api/items/${current.id}/watched`, { watched: !current.watched });
        rerender();
      } }, 'Saison vue / non vue')), list));
    loadSeason(current);
  }

  if (d.type === 'show' && d.seasons.length > 1) {
    parts.push(h('section.block', h('h2', 'Saisons'),
      h('div.grid.small', d.seasons.map((s) => posterCard({ ...s, title: s.index === 0 ? 'Spéciaux' : `Saison ${s.index}` },
        { href: `#/item/${d.id}?season=${s.id}` })))));
  }
  clear(main, parts);
}
