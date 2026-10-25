// TV en direct : grille des chaînes (programme en cours / à suivre), guide des programmes et lecteur.
import { get, post } from '../api.js';
import { clear, errorBox, h, icon, loadHls, modal, spinner, store, toast } from '../ui.js';

const PX_PER_HOUR = 320;
const hhmm = (t) => new Date(t * 1000).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
const dayLabel = (t) => new Date(t * 1000).toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' });


function logo(c, size = 'm') {
  const initials = c.name.replace(/[^\p{L}\p{N} ]/gu, '').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase();
  return h(`span.ch-logo.${size}`, c.logo ? h('img', { src: c.logo, alt: '', loading: 'lazy',
    onerror: (e) => { e.target.replaceWith(h('span.ch-initials', initials || '?')); } }) : h('span.ch-initials', initials || '?'));
}

function progress(p, now) {
  if (!p) return null;
  const pct = Math.min(100, Math.max(0, ((now - p.start) / (p.stop - p.start)) * 100));
  return h('div.progress', h('div.progress-fill', { style: { width: `${pct}%` } }));
}

async function toggleFavorite(c, btn) {
  try {
    await post(`/api/live/${c.id}/favorite`, { on: !c.favorite });
    c.favorite = !c.favorite;
    btn.replaceChildren(icon(c.favorite ? 'starred' : 'star'));
    btn.classList.toggle('on', c.favorite);
    btn.setAttribute('aria-label', c.favorite ? 'Retirer des favoris' : 'Ajouter aux favoris');
  } catch (e) { toast(e.message, 'bad'); }
}

function favButton(c) {
  const btn = h('button.icon-btn.fav', { class: c.favorite ? 'icon-btn fav on' : 'icon-btn fav',
    'aria-label': c.favorite ? 'Retirer des favoris' : 'Ajouter aux favoris',
    onclick: (e) => { e.preventDefault(); e.stopPropagation(); toggleFavorite(c, btn); } }, icon(c.favorite ? 'starred' : 'star'));
  return btn;
}

function channelCard(c, now) {
  return h('a.ch-card', { href: `#/tv/${c.id}`, title: c.name },
    h('div.ch-head', logo(c), h('div.grow', h('div.ch-name', c.number ? h('span.ch-num', String(c.number)) : null, c.name),
      c.now ? h('div.ch-now', c.now.title) : h('div.ch-now.muted', 'Pas de programme')), favButton(c)),
    progress(c.now, now),
    c.next ? h('div.ch-next.muted.small', `${hhmm(c.next.start)} · ${c.next.title}`) : null);
}

function programmeDetails(c, p) {
  const now = Date.now() / 1000;
  const live = p.start <= now && p.stop > now;
  return modal(p.title, h('div',
    h('p.muted.small', `${c.name} · ${dayLabel(p.start)} · ${hhmm(p.start)} – ${hhmm(p.stop)}`, p.category ? ` · ${p.category}` : ''),
    p.subtitle ? h('p', h('strong', p.subtitle)) : null,
    p.desc ? h('p.summary', p.desc) : h('p.muted', 'Pas de résumé.')), live ? [
    { label: 'Fermer', value: null },
    { label: 'Regarder', primary: true, handler: () => { location.hash = `#/tv/${c.id}`; return true; } },
  ] : [{ label: 'Fermer', value: null }]);
}

async function renderGuide(box, filter, ctx) {
  const now = Math.floor(Date.now() / 1000);
  const start = Math.floor((now - 1800) / 1800) * 1800;
  const hours = 6;
  let offset = 0;
  const q = new URLSearchParams({ start, hours, size: 60 });
  if (filter.group !== null) q.set('group', filter.group);
  if (filter.q) q.set('q', filter.q);
  const rows = h('div.guide-rows');
  const width = hours * PX_PER_HOUR;
  const x = (t) => ((t - start) / 3600) * PX_PER_HOUR;
  const ruler = h('div.guide-ruler', { style: { width: `${width}px` } },
    Array.from({ length: hours * 2 }, (_, i) => h('span.guide-tick', { style: { left: `${i * PX_PER_HOUR / 2}px` } },
      hhmm(start + i * 1800))));
  const nowLine = h('div.guide-now', { style: { left: `${x(now)}px` } });
  const more = h('button.btn.small', { hidden: true }, 'Afficher plus de chaînes');
  const load = async () => {
    q.set('offset', offset);
    const g = await get(`/api/live/guide?${q}`);
    if (!ctx.isCurrent()) return;
    for (const c of g.channels) {
      const track = h('div.guide-track', { style: { width: `${width}px` } },
        c.programmes.map((p) => {
          const left = Math.max(0, x(p.start));
          const right = Math.min(width, x(p.stop));
          if (right - left < 2) return null;
          const live = p.start <= now && p.stop > now;
          return h('button.guide-prog', { class: live ? 'guide-prog live' : (p.stop <= now ? 'guide-prog past' : 'guide-prog'),
            style: { left: `${left}px`, width: `${right - left - 3}px` }, title: `${hhmm(p.start)} ${p.title}`,
            onclick: () => programmeDetails(c, p) },
          h('span.guide-title', p.title), h('span.guide-time', `${hhmm(p.start)} – ${hhmm(p.stop)}`));
        }),
        c.programmes.length ? null : h('span.guide-empty.muted.small', 'Pas de guide pour cette chaîne'));
      rows.append(h('div.guide-row',
        h('a.guide-ch', { href: `#/tv/${c.id}`, title: `Regarder ${c.name}` }, logo(c, 's'),
          h('span.guide-ch-name', c.number ? h('span.ch-num', String(c.number)) : null, c.name)),
        track));
    }
    offset += g.channels.length;
    more.hidden = offset >= g.total;
    if (!g.total) rows.append(h('div.empty', 'Aucune chaîne.'));
  };
  more.addEventListener('click', load);
  const scroller = h('div.guide-scroll', h('div.guide-grid', { style: { '--guide-w': `${width}px` } },
    h('div.guide-row.guide-head', h('div.guide-ch.muted.small', dayLabel(now)), h('div.guide-track', ruler, nowLine)),
    rows));
  clear(box, scroller, h('div.center', more));
  await load();
  scroller.scrollLeft = Math.max(0, x(now) - 120);
}

export async function renderLive(main, ctx) {
  const data = await get('/api/live');
  if (!ctx.isCurrent()) return;
  const view = ctx.query.get('view') === 'guide' ? 'guide' : 'channels';
  // filtre : null = toutes, '__fav__' = favoris, sinon nom du groupe ('' = sans groupe)
  const saved = store('seance.live.group');
  const filter = { group: null, q: '' };
  if (saved === '__fav__') filter.group = '__fav__';
  else if (saved && saved.startsWith('g:') && data.groups.includes(saved.slice(2))) filter.group = saved.slice(2);
  const body = h('div.live-body');
  const search = h('input.search-input.live-search', { type: 'search', placeholder: 'Chercher une chaîne…', 'aria-label': 'Chercher une chaîne' });
  const current = filter.group === null ? '__all__' : (filter.group === '__fav__' ? '__fav__' : `g:${filter.group}`);
  const groupOptions = [['__all__', 'Toutes les chaînes'], ['__fav__', '★ Favoris'], ...data.groups.map((g) => [`g:${g}`, g || 'Sans groupe'])];
  const groupSel = h('select', { 'aria-label': 'Groupe' }, groupOptions.map(([v, l]) => h('option', { value: v, selected: v === current }, l)));
  const paint = () => {
    if (view === 'guide') { renderGuide(body, filter, ctx); return; }
    const now = Math.floor(Date.now() / 1000);
    const q = filter.q.toLowerCase();
    const list = data.channels.filter((c) => (filter.group === null || (filter.group === '__fav__' ? c.favorite : c.group === filter.group))
      && (!q || c.name.toLowerCase().includes(q)));
    clear(body, list.length ? h('div.ch-grid', list.map((c) => channelCard(c, now)))
      : h('div.empty', filter.group === '__fav__' ? 'Aucune chaîne favorite : clique sur ☆ pour en ajouter.' : 'Aucune chaîne.'));
  };
  groupSel.addEventListener('change', () => {
    const v = groupSel.value;
    filter.group = v === '__all__' ? null : (v === '__fav__' ? v : v.slice(2));
    store('seance.live.group', v);
    paint();
  });
  let t;
  search.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { filter.q = search.value.trim(); paint(); }, 250); });
  const tab = (id, label, ic) => h('a.tab', { href: id === 'guide' ? '#/live?view=guide' : '#/live', class: view === id ? 'tab active' : 'tab' }, icon(ic), label);
  clear(main,
    h('div.page-head', h('h1', 'TV en direct'), h('span.muted', `${data.channels.length} chaîne(s)`)),
    h('div.filters-row', h('div.tabs.live-tabs', tab('channels', 'Chaînes', 'tv'), tab('guide', 'Guide des programmes', 'guide')),
      h('div.filters', search, groupSel)),
    body);
  paint();
  if (view === 'channels') {
    const timer = setInterval(async () => {
      try {
        const fresh = await get('/api/live');
        if (!ctx.isCurrent()) return;
        data.channels = fresh.channels;
        paint();
      } catch { /* prochain tour */ }
    }, 60000);
    ctx.setCleanup(() => clearInterval(timer));
  }
}

// ------------------------------------------------------------------ lecteur
let channelCache = null;

export async function renderLivePlayer(root, cid, ctx) {
  const video = h('video.video', { playsinline: true, autoplay: true });
  const loader = h('div.player-loader', spinner());
  const errorLayer = h('div.player-error', { hidden: true });
  const titleEl = h('div.player-title');
  const nowBar = h('div.live-now');
  const playBtn = h('button.ctl', { 'aria-label': 'Lecture/Pause' }, icon('pause'));
  const muteBtn = h('button.ctl', { 'aria-label': 'Couper le son' }, icon('volume'));
  const volume = h('input.volume', { type: 'range', min: 0, max: 1, step: 0.05, 'aria-label': 'Volume' });
  const prevBtn = h('button.ctl', { 'aria-label': 'Chaîne précédente', title: 'Chaîne précédente (↓)' }, icon('down'));
  const nextBtn = h('button.ctl', { 'aria-label': 'Chaîne suivante', title: 'Chaîne suivante (↑)' }, icon('up'));
  const listBtn = h('button.ctl', { 'aria-label': 'Liste des chaînes', title: 'Chaînes (L)' }, icon('list'));
  const fullBtn = h('button.ctl', { 'aria-label': 'Plein écran' }, icon('full'));
  const liveTag = h('span.live-tag', 'DIRECT');
  const side = h('div.live-side', { hidden: true });
  const backBtn = h('button.ctl.back', { 'aria-label': 'Retour' }, icon('back'));
  const controls = h('div.controls', nowBar,
    h('div.ctl-row', playBtn, h('div.vol', muteBtn, volume), liveTag, h('div.grow'), prevBtn, nextBtn, listBtn, fullBtn));
  const player = h('div.player.live-player', video, loader, errorLayer, h('div.player-top', backBtn, titleEl), side, controls);
  clear(root, player);

  let hls = null, session = null, destroyed = false, idleTimer = null, keepAlive = null, info = null;
  const savedVol = Number(store('seance.volume'));
  video.volume = Number.isFinite(savedVol) && store('seance.volume') !== null ? savedVol : 1;
  video.muted = store('seance.muted') === '1';
  volume.value = video.volume;

  const showError = (msg) => {
    loader.hidden = true;
    errorLayer.hidden = false;
    clear(errorLayer, h('div.error-box', h('p', msg), h('div.actions-row',
      h('button.btn.primary', { onclick: () => start() }, 'Réessayer'),
      h('button.btn', { onclick: goBack }, 'Retour'))));
  };
  const goBack = () => { if (history.length > 1) history.back(); else location.hash = '#/live'; };

  const paintInfo = () => {
    if (!info) return;
    const now = Date.now() / 1000;
    const progs = info.programmes || [];
    const cur = progs.find((p) => p.start <= now && p.stop > now);
    const nxt = progs.find((p) => p.start >= (cur ? cur.stop : now));
    clear(titleEl, h('strong', info.number ? `${info.number} · ${info.name}` : info.name),
      cur ? h('span', `${cur.title}${cur.subtitle ? ` — ${cur.subtitle}` : ''}`) : null);
    clear(nowBar, cur ? h('div.live-prog',
      h('span.small', `${hhmm(cur.start)} – ${hhmm(cur.stop)}`), progress(cur, now),
      nxt ? h('span.small.muted', `Ensuite : ${hhmm(nxt.start)} ${nxt.title}`) : null) : null);
  };

  async function start() {
    errorLayer.hidden = true;
    loader.hidden = false;
    if (hls) { hls.destroy(); hls = null; }
    try {
      [info, session] = await Promise.all([get(`/api/live/${cid}`), post(`/api/live/${cid}/play`), loadHls()]);
    } catch (e) { if (!destroyed) showError(e.message); return; }
    if (destroyed) { post(`/api/live/s/${session.session}/stop`).catch(() => {}); return; }
    document.title = `${info.name} · TV en direct · Nexis`;
    paintInfo();
    if (window.Hls && window.Hls.isSupported()) {
      hls = new window.Hls({
        liveSyncDurationCount: 3, liveMaxLatencyDurationCount: 8, maxBufferLength: 30, backBufferLength: 30,
        manifestLoadPolicy: { default: { maxTimeToFirstByteMs: 40000, maxLoadTimeMs: 45000,
          timeoutRetry: { maxNumRetry: 2, retryDelayMs: 1000, maxRetryDelayMs: 4000 },
          errorRetry: { maxNumRetry: 3, retryDelayMs: 1500, maxRetryDelayMs: 6000 } } },
      });
      hls.on(window.Hls.Events.ERROR, (_, d) => {
        if (!d.fatal) return;
        if (d.type === window.Hls.ErrorTypes.MEDIA_ERROR) { hls.recoverMediaError(); return; }
        let msg = 'La chaîne ne répond pas.';
        if (d.response && d.response.code) msg += ` (${d.response.code})`;
        if (d.networkDetails && d.networkDetails.responseText) {
          try { msg = JSON.parse(d.networkDetails.responseText).error || msg; } catch { /* */ }
        }
        showError(msg);
      });
      hls.loadSource(session.url);
      hls.attachMedia(video);
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = session.url;
    } else {
      showError('Ce navigateur ne lit pas le HLS.');
      return;
    }
    video.play().catch(() => { loader.hidden = true; paint(); });
    clearInterval(keepAlive);
    keepAlive = setInterval(async () => {
      try { info = await get(`/api/live/${cid}`); paintInfo(); } catch { /* */ }
    }, 60000);
  }

  const paint = () => {
    playBtn.replaceChildren(icon(video.paused ? 'play' : 'pause'));
    player.classList.toggle('paused', video.paused);
  };
  const wake = () => {
    player.classList.remove('idle');
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => { if (!video.paused && side.hidden) player.classList.add('idle'); }, 3000);
  };
  const zap = async (dir) => {
    if (!channelCache) channelCache = (await get('/api/live').catch(() => ({ channels: [] }))).channels;
    const i = channelCache.findIndex((c) => c.id === String(cid));
    if (i < 0 || !channelCache.length) return;
    const n = channelCache[(i + dir + channelCache.length) % channelCache.length];
    location.replace(`#/tv/${n.id}`);
  };
  const openList = async () => {
    if (!side.hidden) { side.hidden = true; return; }
    channelCache = (await get('/api/live').catch(() => ({ channels: channelCache || [] }))).channels;
    clear(side, h('div.live-side-head', h('strong', 'Chaînes'),
      h('button.icon-btn', { 'aria-label': 'Fermer', onclick: () => { side.hidden = true; } }, icon('close'))),
    h('div.live-side-list', channelCache.map((c) => h('a.live-side-item', { href: `#/tv/${c.id}`, class: c.id === String(cid) ? 'live-side-item on' : 'live-side-item',
      onclick: (e) => { e.preventDefault(); location.replace(`#/tv/${c.id}`); } },
    logo(c, 's'), h('span.grow', h('strong', c.name), c.now ? h('span.muted.small', c.now.title) : null)))));
    side.hidden = false;
    const on = side.querySelector('.on');
    if (on) on.scrollIntoView({ block: 'center' });
  };
  const toggleFull = () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else player.requestFullscreen().catch(() => {});
  };

  playBtn.addEventListener('click', () => { if (video.paused) video.play().catch(() => {}); else video.pause(); });
  muteBtn.addEventListener('click', () => { video.muted = !video.muted; });
  volume.addEventListener('input', () => { video.volume = Number(volume.value); video.muted = video.volume === 0; });
  video.addEventListener('volumechange', () => {
    muteBtn.replaceChildren(icon(video.muted || video.volume === 0 ? 'mute' : 'volume'));
    volume.value = video.muted ? 0 : video.volume;
    store('seance.volume', String(video.volume));
    store('seance.muted', video.muted ? '1' : '0');
  });
  video.addEventListener('playing', () => { loader.hidden = true; paint(); });
  video.addEventListener('waiting', () => { loader.hidden = false; });
  video.addEventListener('pause', paint);
  video.addEventListener('play', paint);
  video.addEventListener('click', () => { if (!side.hidden) side.hidden = true; else if (video.paused) video.play(); else video.pause(); });
  video.addEventListener('dblclick', toggleFull);
  prevBtn.addEventListener('click', () => zap(-1));
  nextBtn.addEventListener('click', () => zap(1));
  listBtn.addEventListener('click', (e) => { e.stopPropagation(); openList(); });
  fullBtn.addEventListener('click', toggleFull);
  backBtn.addEventListener('click', goBack);
  player.addEventListener('pointermove', wake);
  const onKey = (e) => {
    if (e.target.closest && e.target.closest('input, select, textarea')) return;
    wake();
    switch (e.key) {
      case ' ': case 'k': e.preventDefault(); playBtn.click(); break;
      case 'ArrowUp': case 'PageUp': e.preventDefault(); zap(1); break;
      case 'ArrowDown': case 'PageDown': e.preventDefault(); zap(-1); break;
      case 'f': toggleFull(); break;
      case 'm': video.muted = !video.muted; break;
      case 'l': openList(); break;
      case 'Escape': if (!side.hidden) side.hidden = true; else if (!document.fullscreenElement) goBack(); break;
      default:
    }
  };
  document.addEventListener('keydown', onKey);
  await start();
  wake();
  return () => {
    destroyed = true;
    clearInterval(keepAlive);
    clearTimeout(idleTimer);
    document.removeEventListener('keydown', onKey);
    if (session) post(`/api/live/s/${session.session}/stop`, {}, { keepalive: true }).catch(() => {});
    if (hls) hls.destroy();
    video.removeAttribute('src');
    video.load();
    if (document.fullscreenElement && !location.hash.startsWith('#/tv/')) document.exitFullscreen().catch(() => {});
  };
}
