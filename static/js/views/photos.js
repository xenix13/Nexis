import { get } from '../api.js';
import { clear, h, icon, img, spinner } from '../ui.js';
import { locale, plural, tr } from '../i18n.js';

// Photothèque : albums (les sous-dossiers), toutes les photos par mois, visionneuse plein écran.
const PAGE = 200;

const monthLabel = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(locale(), { month: 'long', year: 'numeric' });
};
const dateLabel = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(locale(), { dateStyle: 'long', timeStyle: 'short' });
};

export async function renderPhotos(main, lib, ctx) {
  const sid = lib.id;
  const view = ctx.query.get('view') === 'timeline' ? 'timeline' : 'albums';
  const album = view === 'albums' ? ctx.query.get('album') || '' : '';
  const tabs = h('div.tabs.view-tabs',
    h('a.tab', { href: `#/library/${sid}`, class: view === 'albums' ? 'tab active' : 'tab' }, tr('Albums')),
    h('a.tab', { href: `#/library/${sid}?view=timeline`, class: view === 'timeline' ? 'tab active' : 'tab' }, tr('Toutes les photos')));
  const crumbs = h('nav.crumbs');
  const count = h('span.muted');
  const albumsGrid = h('div.album-grid');
  const content = h('div.photo-sections');
  const sentinel = h('div.sentinel');
  clear(main, h('div.page-head', h('h1', lib.title), count), tabs, crumbs, albumsGrid, content, sentinel);

  const photos = [];
  let start = 0, total = Infinity, loading = false, lastMonth = null, grid = null;
  const addPhotos = (list) => {
    for (const p of list) {
      const i = photos.length;
      photos.push(p);
      if (view === 'timeline') {
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
      grid.append(h('button.photo-tile', { type: 'button', title: p.title, onclick: () => openViewer(photos, i, more, () => start < total) },
        h('img', { src: img(p.thumb, 240, 240), alt: p.title, loading: 'lazy', decoding: 'async' })));
    }
  };

  async function more() {
    if (loading || start >= total || !ctx.isCurrent()) return;
    loading = true;
    sentinel.replaceChildren(spinner());
    try {
      const q = new URLSearchParams({ start, size: PAGE });
      if (view === 'timeline') q.set('view', 'timeline');
      if (album) q.set('album', album);
      const r = await get(`/api/libraries/${sid}/photos?${q}`);
      if (!ctx.isCurrent()) return;
      if (start === 0) {
        clear(crumbs, ...(r.path.length ? [h('a', { href: `#/library/${sid}` }, lib.title),
          ...r.path.flatMap((p, i) => [' › ', i === r.path.length - 1 ? h('strong', p.title) : h('a', { href: `#/library/${sid}?album=${p.id}` }, p.title)])] : []));
        albumsGrid.replaceChildren(...r.albums.map((a) => h('a.album-card', { href: `#/library/${sid}?album=${a.id}` },
          h('div.album-cover', h('img', { src: img(a.thumb, 320, 320), alt: '', loading: 'lazy', decoding: 'async' })),
          h('div.album-title', a.title), h('div.muted.small', plural(a.count, '{n} photo', '{n} photos')))));
        if (r.path.length) document.title = `${r.path[r.path.length - 1].title} · Nexis`;
      }
      total = r.total;
      start += r.photos.length;
      if (!r.photos.length) total = start;
      count.textContent = plural(total, '{n} photo', '{n} photos');
      addPhotos(r.photos);
      if (!total && !r.albums.length && start === 0) content.replaceChildren(h('div.empty', tr('Aucune photo ici.')));
    } finally {
      loading = false;
      sentinel.replaceChildren();
    }
    if (sentinel.getBoundingClientRect().top < window.innerHeight + 800) await more();
  }
  const io = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) more(); }, { rootMargin: '800px' });
  io.observe(sentinel);
  ctx.setCleanup(() => { io.disconnect(); closeViewer(); });
  document.title = `${lib.title} · Nexis`;
  await more();
}

// ------------------------------------------------------------ visionneuse
let viewer = null;

function closeViewer() {
  if (!viewer) return;
  viewer.stop();
  viewer = null;
}

function openViewer(list, index, loadMore, hasMore) {
  closeViewer();
  const width = Math.round(Math.min(3840, window.innerWidth * Math.min(2, window.devicePixelRatio || 1)));
  const url = (p) => `/api/photos/${p.id}/view?w=${width}`;
  const picture = h('img.viewer-img', { alt: '' });
  const caption = h('div.viewer-caption');
  const info = h('div.viewer-info', { hidden: true });
  let i = index, timer = null, startX = null;
  const prevBtn = h('button.viewer-nav.prev', { type: 'button', 'aria-label': tr('Photo précédente'), onclick: () => go(-1) }, icon('chevleft'));
  const nextBtn = h('button.viewer-nav.next', { type: 'button', 'aria-label': tr('Photo suivante'), onclick: () => go(1) }, icon('chevright'));
  const playBtn = h('button.icon-btn', { type: 'button', title: tr('Diaporama'), onclick: () => toggleShow() }, icon('play'));
  const download = h('a.icon-btn', { title: tr('Télécharger l’original'), download: '' }, icon('download'));
  const root = h('div.photo-viewer', { role: 'dialog', 'aria-modal': 'true', tabindex: '-1' },
    h('div.viewer-bar', caption, h('div.grow'), playBtn,
      h('button.icon-btn', { type: 'button', title: tr('Informations'), onclick: () => { info.hidden = !info.hidden; } }, icon('info')),
      download,
      h('button.icon-btn', { type: 'button', title: tr('Fermer'), onclick: () => closeViewer() }, icon('close'))),
    picture, prevBtn, nextBtn, info);

  function show() {
    const p = list[i];
    picture.src = url(p);
    picture.alt = p.title;
    caption.replaceChildren(h('strong', p.title), h('span.muted', ` · ${dateLabel(p.taken)}`), h('span.muted', ` · ${i + 1} / ${list.length}`));
    clear(info, h('div', dateLabel(p.taken)),
      p.w && p.h ? h('div', `${p.w} × ${p.h}`) : null,
      p.camera ? h('div', p.camera) : null,
      p.size ? h('div', tr('{n} Mo', { n: (p.size / 1048576).toFixed(1) })) : null);
    download.href = `/api/photos/${p.id}/original`;
    prevBtn.hidden = i === 0;
    nextBtn.hidden = i === list.length - 1 && !hasMore();
    const n = list[i + 1];
    if (n) new Image().src = url(n); // la suivante est prête quand on avance
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
    else timer = setInterval(() => go(1), 5000);
    playBtn.replaceChildren(icon(timer ? 'pause' : 'play'));
  }
  const onKey = (e) => {
    const act = { Escape: () => closeViewer(), ArrowLeft: () => go(-1), ArrowRight: () => go(1), ' ': () => toggleShow() }[e.key];
    if (!act) return;
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
  picture.addEventListener('click', () => { info.hidden = true; });
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
