import { del, get, post } from '../api.js';
import { onAudio, nowPlaying, playAudio } from '../audio.js';
import { clear, confirmDialog, fmtDuration, fmtTime, h, icon, spinner, toast } from '../ui.js';
import { locale, plural, tr } from '../i18n.js';

// Podcasts : abonnements (recherche dans l'annuaire d'Apple Podcasts ou adresse RSS), épisodes à reprendre,
// derniers épisodes, et la page d'un podcast.
const day = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString(locale(), { day: 'numeric', month: 'short', year: 'numeric' }) : '');

export function episodeAudio(e) {
  return { kind: 'podcast', id: e.id, title: e.title, subtitle: e.podcast_title || '', cover: e.image || '',
    files: [{ url: e.audio, duration: e.duration || 0 }], duration: e.duration || 0,
    position: e.played ? 0 : e.position || 0, progressUrl: `/api/podcasts/episodes/${e.id}/progress` };
}

function episodeRow(e, onChange) {
  const pct = e.position && e.duration ? Math.min(100, (e.position / e.duration) * 100) : 0;
  const playBtn = h('button.icon-btn.ep-play', { type: 'button', 'aria-label': tr('Écouter'), onclick: () => playAudio(episodeAudio(e)) }, icon('play'));
  const desc = h('p.ep-desc.small', { hidden: true }, e.description || '');
  const row = h('div.ep-row', { class: e.played ? 'ep-row played' : 'ep-row' },
    playBtn,
    h('div.ep-info',
      h('div.ep-title', { onclick: () => { desc.hidden = !desc.hidden; } }, e.title),
      h('div.muted.small', [e.podcast_title, day(e.published), e.duration ? fmtDuration(e.duration) : null,
        pct && !e.played ? tr('{t} restantes', { t: fmtTime(e.duration - e.position) }) : null, e.played ? tr('écouté') : null].filter(Boolean).join(' · ')),
      pct && !e.played ? h('div.ep-progress', h('div', { style: `width:${pct}%` })) : null, desc),
    h('button.icon-btn', { type: 'button', title: e.played ? tr('Marquer comme non écouté') : tr('Marquer comme écouté'), onclick: async () => {
      await post(`/api/podcasts/episodes/${e.id}/progress`, { position: 0, duration: e.duration || 0, played: !e.played, reset: true });
      e.played = !e.played;
      e.position = 0;
      if (onChange) onChange(); else row.replaceWith(episodeRow(e, onChange));
    } }, e.played ? '↺' : '✓'));
  return row;
}

function podTile(p) {
  return h('a.book-tile', { href: `#/podcasts/${p.id}`, title: p.title },
    h('div.book-cover', p.image ? h('img', { src: p.image, alt: '', loading: 'lazy' }) : h('span', icon('mic')),
      p.unplayed ? h('span.pod-badge', String(p.unplayed > 99 ? '99+' : p.unplayed)) : null),
    h('div.book-title', p.title), h('div.muted.small', p.author || ' '));
}

export async function renderPodcasts(main, ctx) {
  document.title = `${tr('Podcasts')} · Nexis`;
  const input = h('input.search-field', { type: 'search', placeholder: tr('Chercher un podcast, ou coller l’adresse de son flux RSS'),
    'aria-label': tr('Chercher un podcast') });
  const results = h('div');
  const body = h('div', spinner());
  const subscribe = async (url, btn) => {
    if (btn) btn.disabled = true;
    try {
      const p = await post('/api/podcasts', { url });
      toast(tr('Abonné à {title}', { title: p.title }), 'ok');
      location.hash = `#/podcasts/${p.id}`;
    } catch (e) { toast(e.message, 'bad'); if (btn) btn.disabled = false; }
  };
  let timer = null;
  const search = async () => {
    const q = input.value.trim();
    if (/^(https?|itpc|pcast|podcast):\/\//i.test(q)) {
      results.replaceChildren(h('div.ep-row', h('div.ep-info', h('div.ep-title', q)),
        h('button.btn.primary', { onclick: (e) => subscribe(q, e.target) }, tr('S’abonner'))));
      return;
    }
    if (q.length < 2) { results.replaceChildren(); return; }
    results.replaceChildren(spinner());
    try {
      const r = await get(`/api/podcasts/search?q=${encodeURIComponent(q)}`);
      if (input.value.trim() !== q) return;
      results.replaceChildren(r.results.length ? h('div.pod-results', r.results.map((p) => h('div.ep-row',
        p.image ? h('img.pod-thumb', { src: p.image, alt: '', loading: 'lazy' }) : h('span.pod-thumb'),
        h('div.ep-info', h('div.ep-title', p.title), h('div.muted.small', [p.author, p.genre, p.episodes ? plural(p.episodes, '{n} épisode', '{n} épisodes') : null].filter(Boolean).join(' · '))),
        p.subscribed ? h('span.os-badge.good', tr('abonné')) : h('button.btn.small.primary', { onclick: (e) => subscribe(p.feed, e.target) }, tr('S’abonner')))))
        : h('p.muted', tr('Aucun podcast trouvé.')));
    } catch (e) { results.replaceChildren(h('p.form-error', e.message)); }
  };
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(search, 400); });
  clear(main, h('div.page-head', h('h1', tr('Podcasts'))), h('section.block', input, results), body);
  const r = await get('/api/podcasts');
  if (!ctx.isCurrent()) return;
  const refresh = () => renderPodcasts(main, ctx);
  clear(body,
    r.resume.length ? h('section.block', h('h2', tr('À reprendre')), h('div.ep-list', r.resume.map((e) => episodeRow(e, refresh)))) : null,
    r.podcasts.length ? h('section.block', h('h2', tr('Mes podcasts')), h('div.book-grid', r.podcasts.map(podTile)))
      : h('div.empty', tr('Aucun abonnement : cherchez un podcast ci-dessus.')),
    r.latest.length ? h('section.block', h('h2', tr('Derniers épisodes')), h('div.ep-list', r.latest.map((e) => episodeRow(e, refresh)))) : null);
}

export async function renderPodcast(main, id, ctx) {
  const p = await get(`/api/podcasts/${id}`);
  if (!ctx.isCurrent()) return;
  document.title = `${p.title} · Nexis`;
  const list = h('div.ep-list', p.episodes.map((e) => episodeRow(e)));
  const off = onAudio(() => {
    const n = nowPlaying();
    list.querySelectorAll('.ep-row').forEach((row, i) => row.classList.toggle('current', !!n && n.kind === 'podcast' && n.id === p.episodes[i].id));
  });
  ctx.setCleanup(off);
  clear(main, h('div.book-detail',
    h('div.book-cover.big', p.image ? h('img', { src: p.image, alt: '' }) : h('span', icon('mic'))),
    h('div.book-info', h('h1', p.title), p.author ? h('p.book-author', p.author) : null,
      h('p.muted', plural(p.episodes.length, '{n} épisode', '{n} épisodes'), p.unplayed ? ` · ${tr('{n} non écouté(s)', { n: p.unplayed })}` : ''),
      p.error ? h('p.form-error.small', p.error) : null,
      h('div.detail-actions',
        p.episodes.length ? h('button.btn.primary', { onclick: () => {
          const next = [...p.episodes].reverse().find((e) => !e.played) || p.episodes[0];
          playAudio(episodeAudio(next));
        } }, icon('play'), tr('Écouter')) : null,
        h('button.btn', { onclick: async () => {
          try { await post(`/api/podcasts/${id}/refresh`); renderPodcast(main, id, ctx); } catch (e) { toast(e.message, 'bad'); }
        } }, tr('Actualiser')),
        h('button.btn', { onclick: async () => { await post(`/api/podcasts/${id}/played`); renderPodcast(main, id, ctx); } }, tr('Tout marquer comme écouté')),
        h('button.btn', { onclick: async () => {
          if (!await confirmDialog(tr('Se désabonner ?'), p.title, tr('Se désabonner'))) return;
          await del(`/api/podcasts/${id}`);
          location.hash = '#/podcasts';
        } }, tr('Se désabonner'))),
      p.description ? h('p.book-summary', p.description) : null)),
  h('section.block', h('h2', tr('Épisodes')), list));
}
