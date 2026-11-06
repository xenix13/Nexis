import { del, get, post, put } from '../api.js';
import { clear, fmtTime, h, icon, img, modal, select, spinner, toast } from '../ui.js';
import { locale, plural, tr } from '../i18n.js';

// Photothèque : albums (les sous-dossiers), toutes les photos par mois, favoris, visionneuse plein écran
// (photos et vidéos du téléphone), et album partagé par lien (#/share/…, consultable sans compte).
const PAGE = 200;

const monthLabel = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(locale(), { month: 'long', year: 'numeric' });
};
const dateLabel = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(locale(), { dateStyle: 'long', timeStyle: 'short' });
};
// vignette : par /api/img (compte connecté) ou par l'adresse de l'album partagé
const thumbSrc = (c, size) => (c.thumb_url ? `${c.thumb_url}&w=${size}` : img(c.thumb, size, size));

export async function renderPhotos(main, lib, ctx) {
  const sid = lib.id;
  const view = ['timeline', 'favorites'].includes(ctx.query.get('view')) ? ctx.query.get('view') : 'albums';
  const tabs = h('div.tabs.view-tabs',
    h('a.tab', { href: `#/library/${sid}`, class: view === 'albums' ? 'tab active' : 'tab' }, tr('Albums')),
    h('a.tab', { href: `#/library/${sid}?view=timeline`, class: view === 'timeline' ? 'tab active' : 'tab' }, tr('Toutes les photos')),
    h('a.tab', { href: `#/library/${sid}?view=favorites`, class: view === 'favorites' ? 'tab active' : 'tab' }, icon('starred'), tr('Favoris')));
  await browse(main, ctx, {
    title: lib.title, tabs, view, album: view === 'albums' ? ctx.query.get('album') || '' : '',
    fetch: (q) => get(`/api/libraries/${sid}/photos?${q}`),
    albumHref: (id) => `#/library/${sid}?album=${id}`, rootHref: `#/library/${sid}`, canFav: true, canShare: true,
  });
}

// Album partagé par lien : sans compte, sans favoris ni partage
export async function renderShared(main, token, ctx) {
  await browse(main, ctx, {
    view: 'albums', album: ctx.query.get('album') || '', shared: true,
    fetch: (q) => get(`/api/share/${token}?${q}`),
    albumHref: (id) => `#/share/${token}?album=${id}`, rootHref: `#/share/${token}`, canFav: false, canShare: false,
  });
}

async function browse(main, ctx, o) {
  const crumbs = h('nav.crumbs');
  const count = h('span.muted');
  const heading = h('h1', o.title || '');
  const actions = h('div.filters');
  const albumsGrid = h('div.album-grid');
  const content = h('div.photo-sections');
  const sentinel = h('div.sentinel');
  clear(main, h('div.page-head', heading, count, actions), o.tabs || null, crumbs, albumsGrid, content, sentinel);

  const photos = [];
  let start = 0, total = Infinity, loading = false, lastMonth = null, grid = null;
  const addPhotos = (list) => {
    for (const p of list) {
      const i = photos.length;
      photos.push(p);
      if (o.view !== 'albums') {
        const m = monthLabel(p.taken);
        if (m !== lastMonth || !grid) {
          lastMonth = m;
          grid = h('div.photo-grid');
          content.append(h('h2.photo-month', m), grid);
        }
      } else if (!grid) {
        grid = h('div.photo-grid');
        content.append(grid);
      }
      grid.append(h('button.photo-tile', { type: 'button', title: p.title,
        onclick: () => openViewer(photos, i, more, () => start < total, o) },
      h('img', { src: thumbSrc(p, 240), alt: p.title, loading: 'lazy', decoding: 'async' }),
      p.video ? h('span.tile-video', icon('play'), p.duration ? fmtTime(p.duration) : '') : null,
      p.fav ? h('span.tile-fav', icon('starred')) : null));
    }
  };

  async function more() {
    if (loading || start >= total || !ctx.isCurrent()) return;
    loading = true;
    sentinel.replaceChildren(spinner());
    try {
      const q = new URLSearchParams({ start, size: PAGE });
      if (o.view !== 'albums') q.set('view', o.view);
      if (o.album) q.set('album', o.album);
      const r = await o.fetch(q);
      if (!ctx.isCurrent()) return;
      if (start === 0) {
        if (o.shared) {
          heading.textContent = r.title;
          document.title = `${r.title} · Nexis`;
        }
        // album partagé : on ne remonte pas au-dessus de l'album du lien
        const trail = o.shared ? r.path.slice(1) : r.path;
        clear(crumbs, ...(trail.length ? [h('a', { href: o.rootHref }, o.shared ? r.title : o.title),
          ...trail.flatMap((p, i) => [' › ', i === trail.length - 1 ? h('strong', p.title) : h('a', { href: o.albumHref(p.id) }, p.title)])] : []));
        albumsGrid.replaceChildren(...r.albums.map((a) => h('a.album-card', { href: o.albumHref(a.id) },
          h('div.album-cover', a.thumb || a.thumb_url ? h('img', { src: thumbSrc(a, 320), alt: '', loading: 'lazy', decoding: 'async' }) : null),
          h('div.album-title', a.title), h('div.muted.small', plural(a.count, '{n} photo', '{n} photos')))));
        const here = r.path[r.path.length - 1];
        if (here && !o.shared) document.title = `${here.title} · Nexis`;
        if (o.canShare && here) actions.replaceChildren(h('button.btn.small', { type: 'button', onclick: () => shareDialog(here) }, icon('share'), tr('Partager l’album')));
      }
      total = r.total;
      start += r.photos.length;
      if (!r.photos.length) total = start;
      count.textContent = plural(total, '{n} photo', '{n} photos');
      addPhotos(r.photos);
      if (!total && !r.albums.length && start === 0) {
        content.replaceChildren(h('div.empty', o.view === 'favorites' ? tr('Aucun favori : l’étoile de la visionneuse ajoute une photo ici.') : tr('Aucune photo ici.')));
      }
    } catch (e) {
      clear(content, h('div.empty', e.message));
      total = 0;
    } finally {
      loading = false;
      sentinel.replaceChildren();
    }
    if (sentinel.getBoundingClientRect().top < window.innerHeight + 800) await more();
  }
  const io = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) more(); }, { rootMargin: '800px' });
  io.observe(sentinel);
  ctx.setCleanup(() => { io.disconnect(); closeViewer(); });
  if (o.title) document.title = `${o.title} · Nexis`;
  await more();
}

// ------------------------------------------------------------ partage d'un album par lien
async function shareDialog(album) {
  const list = h('div.share-list');
  const days = select([['1', tr('1 jour')], ['7', tr('1 semaine')], ['30', tr('1 mois')], ['90', tr('3 mois')]], '7', { 'aria-label': tr('Durée') });
  const full = (s) => `${location.origin}${s.url}`;
  const copy = async (s) => {
    try { await navigator.clipboard.writeText(full(s)); toast(tr('Lien copié'), 'ok'); }
    catch { toast(full(s)); }
  };
  const paint = async () => {
    const shares = await get(`/api/photos/albums/${album.id}/shares`).catch(() => []);
    clear(list, shares.length ? shares.map((s) => h('div.share-row',
      h('div.grow', h('code.small', full(s)), h('div.muted.small', tr('Expire le {date}', { date: new Date(s.expires_at * 1000).toLocaleDateString(locale()) }))),
      h('button.btn.small', { type: 'button', onclick: () => copy(s) }, tr('Copier')),
      h('button.btn.small.ghost', { type: 'button', onclick: async () => { await del(`/api/photos/shares/${s.token}`); paint(); } }, tr('Révoquer'))))
      : h('p.muted.small', tr('Aucun lien actif.')));
  };
  paint();
  await modal(tr('Partager « {title} »', { title: album.title }), h('div.form',
    h('p.muted.small', tr('Toute personne qui a le lien voit cet album et ses sous-albums, sans compte, jusqu’à son expiration. Le reste de la photothèque reste privé.')),
    h('div.actions-row', days, h('button.btn.primary.small', { type: 'button', onclick: async () => {
      try { const s = await post(`/api/photos/albums/${album.id}/shares`, { days: Number(days.value) }); await copy(s); paint(); }
      catch (e) { toast(e.message, 'bad'); }
    } }, tr('Créer un lien'))), list), [{ label: tr('Fermer'), primary: true, value: true }]);
}

// ------------------------------------------------------------ visionneuse
let viewer = null;

function closeViewer() {
  if (!viewer) return;
  viewer.stop();
  viewer = null;
}

function openViewer(list, index, loadMore, hasMore, o) {
  closeViewer();
  const width = Math.round(Math.min(3840, window.innerWidth * Math.min(2, window.devicePixelRatio || 1)));
  const url = (p) => `${p.view_url}${p.view_url.includes('?') ? '&' : '?'}w=${width}`;
  const stage = h('div.viewer-stage');
  const caption = h('div.viewer-caption');
  const info = h('div.viewer-info', { hidden: true });
  let i = index, timer = null, startX = null;
  const prevBtn = h('button.viewer-nav.prev', { type: 'button', 'aria-label': tr('Photo précédente'), onclick: () => go(-1) }, icon('chevleft'));
  const nextBtn = h('button.viewer-nav.next', { type: 'button', 'aria-label': tr('Photo suivante'), onclick: () => go(1) }, icon('chevright'));
  const playBtn = h('button.icon-btn', { type: 'button', title: tr('Diaporama'), onclick: () => toggleShow() }, icon('play'));
  const favBtn = o.canFav ? h('button.icon-btn', { type: 'button', onclick: () => toggleFav() }) : null;
  const download = h('a.icon-btn', { title: tr('Télécharger l’original'), download: '' }, icon('download'));
  const root = h('div.photo-viewer', { role: 'dialog', 'aria-modal': 'true', tabindex: '-1' },
    h('div.viewer-bar', caption, h('div.grow'), playBtn, favBtn,
      h('button.icon-btn', { type: 'button', title: tr('Informations'), onclick: () => { info.hidden = !info.hidden; } }, icon('info')),
      download,
      h('button.icon-btn', { type: 'button', title: tr('Fermer'), onclick: () => closeViewer() }, icon('close'))),
    stage, prevBtn, nextBtn, info);

  function paintFav() {
    if (!favBtn) return;
    const on = list[i].fav;
    favBtn.replaceChildren(icon(on ? 'starred' : 'star'));
    favBtn.title = on ? tr('Retirer des favoris') : tr('Ajouter aux favoris');
    favBtn.classList.toggle('on', on);
  }
  async function toggleFav() {
    const p = list[i];
    try {
      const r = await put(`/api/photos/${p.id}/favorite`, { on: !p.fav });
      p.fav = r.fav;
      paintFav();
    } catch (e) { toast(e.message, 'bad'); }
  }
  function show() {
    const p = list[i];
    if (p.video) {
      // vidéo du téléphone : lue telle quelle par le navigateur (sinon : à télécharger)
      const v = h('video.viewer-img', { src: p.stream_url, controls: true, autoplay: true, playsinline: true });
      v.addEventListener('error', () => stage.replaceChildren(h('div.viewer-msg', h('p', tr('Ce navigateur ne sait pas lire cette vidéo.')),
        h('a.btn', { href: p.original_url, download: '' }, icon('download'), tr('Télécharger')))));
      v.addEventListener('ended', () => { if (timer) go(1); });
      stage.replaceChildren(v);
    } else {
      stage.replaceChildren(h('img.viewer-img', { src: url(p), alt: p.title, onclick: () => { info.hidden = true; } }));
    }
    caption.replaceChildren(h('strong', p.title), h('span.muted', ` · ${dateLabel(p.taken)}`), h('span.muted', ` · ${i + 1} / ${list.length}`));
    clear(info, h('div', dateLabel(p.taken)),
      p.w && p.h ? h('div', `${p.w} × ${p.h}`) : null,
      p.duration ? h('div', fmtTime(p.duration)) : null,
      p.camera ? h('div', p.camera) : null,
      p.size ? h('div', tr('{n} Mo', { n: (p.size / 1048576).toFixed(1) })) : null);
    download.href = p.original_url;
    prevBtn.hidden = i === 0;
    nextBtn.hidden = i === list.length - 1 && !hasMore();
    paintFav();
    const n = list[i + 1];
    if (n && !n.video) new Image().src = url(n); // la suivante est prête quand on avance
  }
  async function go(step) {
    if (step > 0 && i === list.length - 1 && hasMore()) await loadMore();
    const j = i + step;
    if (j < 0 || j >= list.length) { if (timer) toggleShow(); return; }
    i = j;
    show();
  }
  function toggleShow() {
    if (timer) { clearInterval(timer); timer = null; }
    else timer = setInterval(() => { if (!list[i].video) go(1); }, 5000); // une vidéo passe à la suite à sa fin
    playBtn.replaceChildren(icon(timer ? 'pause' : 'play'));
  }
  const onKey = (e) => {
    const act = { Escape: () => closeViewer(), ArrowLeft: () => go(-1), ArrowRight: () => go(1), ' ': () => toggleShow() }[e.key];
    if (!act || (e.key === ' ' && list[i].video)) return;
    e.preventDefault();
    e.stopPropagation(); // la visionneuse d'abord (pas de retour en arrière de l'application)
    act();
  };
  root.addEventListener('touchstart', (e) => { startX = e.touches[0].clientX; }, { passive: true });
  root.addEventListener('touchend', (e) => {
    if (startX === null) return;
    const dx = e.changedTouches[0].clientX - startX;
    startX = null;
    if (Math.abs(dx) > 50) go(dx < 0 ? 1 : -1);
  });
  document.addEventListener('keydown', onKey, true);
  document.body.append(root);
  document.body.classList.add('viewer-open');
  root.focus();
  viewer = {
    stop() {
      if (timer) clearInterval(timer);
      document.removeEventListener('keydown', onKey, true);
      document.body.classList.remove('viewer-open');
      root.remove();
    },
  };
  show();
}
