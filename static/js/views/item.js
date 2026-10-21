import { get, post, put } from '../api.js';
import { state } from '../app.js';
import { pickAudio, pickSubtitle, trackPayload } from '../tracks.js';
import { clear, episodeCode, field, fmtDuration, fmtTime, h, icon, img, modal, moreMenu, posterCard, progressBar, select, spinner, toast } from '../ui.js';

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
  const src = { manual: 'posé à la main', chapters: 'chapitres', 'chapters-guess': 'chapitres (estimé)' };
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

function itemMenu(d, rerender) {
  const items = [{ label: d.watched ? 'Marquer comme non vu' : 'Marquer comme vu', onClick: async () => {
    try {
      await post(`/api/items/${d.id}/watched`, { watched: !d.watched });
      toast(d.watched ? 'Marqué comme non vu' : 'Marqué comme vu', 'ok');
      rerender();
    } catch (e) { toast(e.message, 'bad'); }
  } }];
  if (state.me.user.is_admin) {
    items.push({ divider: true }, { heading: 'Administration' },
      { label: 'Identifier…', onClick: () => identify(d, rerender) },
      { label: 'Actualiser les métadonnées', onClick: async () => {
        try { await post(`/api/admin/refresh/${d.id}`); toast('Métadonnées actualisées', 'ok'); rerender(); }
        catch (e) { toast(e.message, 'bad'); }
      } });
    if (d.type === 'movie' || d.type === 'episode') items.push({ label: 'Informations sur le fichier', onClick: () => fileInfo(d) });
  }
  return moreMenu(items);
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
  const sSel = select([['0', 'Aucun'], ...subs.map((x) => [x.id, x.title])], s ? s.id : '0', { 'aria-label': 'Sous-titres' });
  const save = () => {
    a = audio.find((x) => String(x.id) === aSel.value) || null;
    s = sSel.value === '0' ? null : subs.find((x) => String(x.id) === sSel.value) || null;
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
  document.title = `${d.type === 'episode' ? d.show : d.title} · Séance`;
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
  actions.append(itemMenu(d, rerender));
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
