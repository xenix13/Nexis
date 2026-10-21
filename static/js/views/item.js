import { get, post } from '../api.js';
import { clear, episodeCode, fmtDuration, fmtTime, h, icon, img, posterCard, progressBar, spinner, toast } from '../ui.js';

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

function watchedButton(c, onChange) {
  const btn = h('button.btn.ghost', { onclick: async () => {
    btn.disabled = true;
    try {
      await post(`/api/items/${c.id}/watched`, { watched: !c.watched });
      toast(c.watched ? 'Marqué comme non vu' : 'Marqué comme vu', 'ok');
      onChange();
    } catch (e) { toast(e.message, 'bad'); btn.disabled = false; }
  } }, icon(c.watched ? 'eye' : 'check'), c.watched ? 'Marquer non vu' : 'Marquer vu');
  return btn;
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
  if (d.type === 'show') {
    const t = d.play_target;
    if (t) {
      const label = t.position > 0 ? `Reprendre ${episodeCode(t)}` : `Lire ${episodeCode(t)}`;
      actions.append(h('a.btn.primary', { href: `#/play/${t.id}${t.position > 0 ? '?resume=1' : ''}` }, icon('play'), label));
    }
  } else {
    actions.append(...playButtons(d));
  }
  actions.append(watchedButton(d, rerender));

  const info = h('div.detail-info',
    d.type === 'episode' ? h('a.detail-kicker', { href: `#/item/${d.show_id}` }, d.show) : null,
    h('h1.detail-title', d.title),
    metaLine(d),
    d.tagline ? h('p.tagline', d.tagline) : null,
    actions,
    d.summary ? h('p.summary', d.summary) : null,
    d.genres && d.genres.length ? h('div.chips', d.genres.map((g) => h('span.chip', g))) : null,
    h('dl.facts',
      d.directors && d.directors.length ? [h('dt', 'Réalisation'), h('dd', d.directors.join(', '))] : null,
      d.studio ? [h('dt', 'Studio'), h('dd', d.studio)] : null,
      d.audio && d.audio.length ? [h('dt', 'Audio'), h('dd', d.audio.map((a) => a.title).join(' · '))] : null,
      d.subtitles && d.subtitles.length ? [h('dt', 'Sous-titres'), h('dd', d.subtitles.map((s) => s.title).join(' · '))] : null));

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
