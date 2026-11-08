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
  const view = ['timeline', 'favorites', 'map'].includes(ctx.query.get('view')) ? ctx.query.get('view') : 'albums';
  const tabs = h('div.tabs.view-tabs',
    h('a.tab', { href: `#/library/${sid}`, class: view === 'albums' ? 'tab active' : 'tab' }, tr('Albums')),
    h('a.tab', { href: `#/library/${sid}?view=timeline`, class: view === 'timeline' ? 'tab active' : 'tab' }, tr('Toutes les photos')),
    h('a.tab', { href: `#/library/${sid}?view=favorites`, class: view === 'favorites' ? 'tab active' : 'tab' }, icon('starred'), tr('Favoris')),
    h('a.tab', { href: `#/library/${sid}?view=map`, class: view === 'map' ? 'tab active' : 'tab' }, icon('place'), tr('Carte')));
  if (view === 'map') { await renderMap(main, lib, tabs, ctx); return; }
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

// ------------------------------------------------------------ carte des lieux (GPS des photos)
// Fond de carte OpenStreetMap (tuiles 256 px, projection de Mercator), sans bibliothèque : on déplace et on
// zoome à la main ; les photos proches à l'écran sont regroupées en une pastille qui donne leur nombre.
const TILE = 256, MIN_Z = 2, MAX_Z = 18, CLUSTER_PX = 56;
const TILES = 'https://tile.openstreetmap.org';
const worldX = (lon, z) => ((lon + 180) / 360) * TILE * 2 ** z;
const worldY = (lat, z) => {
  const r = (Math.max(-85.05, Math.min(85.05, lat)) * Math.PI) / 180;
  return ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * TILE * 2 ** z;
};

async function renderMap(main, lib, tabs, ctx) {
  const sid = lib.id;
  const count = h('span.muted');
  const tiles = h('div.map-tiles');
  const marks = h('div.map-marks');
  const zoomIn = h('button.map-btn', { type: 'button', 'aria-label': tr('Zoomer') }, '+');
  const zoomOut = h('button.map-btn', { type: 'button', 'aria-label': tr('Dézoomer') }, '−');
  const map = h('div.photo-map', tiles, marks, h('div.map-zoom', zoomIn, zoomOut),
    h('div.map-credit', '© ', h('a', { href: 'https://www.openstreetmap.org/copyright', target: '_blank', rel: 'noopener' }, 'OpenStreetMap')));
  const placeHead = h('h2.map-place-head');
  const placeGrid = h('div.photo-grid');
  clear(main, h('div.page-head', h('h1', lib.title), count), tabs, map, placeHead, placeGrid);
  document.title = `${lib.title} · Nexis`;
  map.append(spinner());
  let pts;
  try {
    pts = (await get(`/api/libraries/${sid}/photos?view=map`)).points;
  } catch (e) { clear(map, h('div.empty', e.message)); return; }
  if (!ctx.isCurrent()) return;
  map.querySelector('.spinner')?.remove();
  count.textContent = plural(pts.length, '{n} photo située', '{n} photos situées');
  if (!pts.length) {
    clear(map, h('div.empty', tr('Aucune photo ne porte de position GPS (elle est enregistrée par la plupart des téléphones).')));
    return;
  }

  // vue de départ : toutes les photos à l'écran
  let z = MAX_Z, cx = 0, cy = 0;
  const fit = (list) => {
    const lats = list.map((p) => p[1]), lons = list.map((p) => p[2]);
    const [s, n, w, e] = [Math.min(...lats), Math.max(...lats), Math.min(...lons), Math.max(...lons)];
    const W = map.clientWidth || 800, H = map.clientHeight || 480;
    z = MAX_Z - 2;
    while (z > MIN_Z && (worldX(e, z) - worldX(w, z) > W - 80 || worldY(s, z) - worldY(n, z) > H - 80)) z--;
    cx = (worldX(w, z) + worldX(e, z)) / 2;
    cy = (worldY(n, z) + worldY(s, z)) / 2;
  };
  fit(pts);

  function draw() {
    const W = map.clientWidth, H = map.clientHeight, n = 2 ** z;
    const left = cx - W / 2, top = cy - H / 2;
    const imgs = [];
    for (let ty = Math.floor(top / TILE); ty <= Math.floor((top + H) / TILE); ty++) {
      if (ty < 0 || ty >= n) continue;
      for (let tx = Math.floor(left / TILE); tx <= Math.floor((left + W) / TILE); tx++) {
        const wx = ((tx % n) + n) % n;
        imgs.push(h('img', { src: `${TILES}/${z}/${wx}/${ty}.png`, alt: '', draggable: false, referrerpolicy: 'strict-origin-when-cross-origin',
          style: `left:${Math.round(tx * TILE - left)}px;top:${Math.round(ty * TILE - top)}px` }));
      }
    }
    tiles.replaceChildren(...imgs);
    // regroupement : une case de CLUSTER_PX à l'écran = une pastille
    const cells = new Map();
    for (const p of pts) {
      const x = worldX(p[2], z) - left, y = worldY(p[1], z) - top;
      if (x < -CLUSTER_PX || y < -CLUSTER_PX || x > W + CLUSTER_PX || y > H + CLUSTER_PX) continue;
      const k = `${Math.floor(x / CLUSTER_PX)}:${Math.floor(y / CLUSTER_PX)}`;
      const c = cells.get(k) || { x: 0, y: 0, list: [] };
      c.x += x; c.y += y; c.list.push(p);
      cells.set(k, c);
    }
    marks.replaceChildren(...[...cells.values()].map((c) => {
      const x = c.x / c.list.length, y = c.y / c.list.length;
      return h('button.map-mark', { type: 'button', style: `left:${Math.round(x)}px;top:${Math.round(y)}px`,
        title: plural(c.list.length, '{n} photo', '{n} photos'),
        onclick: (e) => { e.stopPropagation(); if (!moved) showPlace(c.list); } },
      h('img', { src: img(`/library/metadata/${c.list[0][0]}/thumb/0`, 48, 48), alt: '', draggable: false }),
      c.list.length > 1 ? h('span', c.list.length > 999 ? '999+' : String(c.list.length)) : null);
    }));
  }

  function zoomAt(dz, px, py) {
    const nz = Math.max(MIN_Z, Math.min(MAX_Z, z + dz));
    if (nz === z) return;
    const W = map.clientWidth, H = map.clientHeight;
    const ax = cx - W / 2 + px, ay = cy - H / 2 + py, f = 2 ** (nz - z);
    cx = ax * f - px + W / 2;
    cy = ay * f - py + H / 2;
    z = nz;
    draw();
  }

  async function showPlace(list) {
    if (list.length > 1 && z < 15) {  // plusieurs lieux mêlés : on s'en rapproche d'abord
      const spread = Math.max(...list.map((p) => p[1])) - Math.min(...list.map((p) => p[1]))
        + Math.max(...list.map((p) => p[2])) - Math.min(...list.map((p) => p[2]));
      if (spread > 0.0005) { fit(list); draw(); }
    }
    placeHead.textContent = plural(list.length, '{n} photo à cet endroit', '{n} photos à cet endroit');
    placeGrid.replaceChildren(spinner());
    try {
      const r = await get(`/api/libraries/${sid}/photos?ids=${list.slice(0, 500).map((p) => p[0]).join(',')}`);
      if (!ctx.isCurrent()) return;
      const photos = r.photos;
      placeGrid.replaceChildren(...photos.map((p, i) => h('button.photo-tile', { type: 'button', title: p.title,
        onclick: () => openViewer(photos, i, async () => {}, () => false, { canFav: true }) },
      h('img', { src: thumbSrc(p, 240), alt: p.title, loading: 'lazy', decoding: 'async' }),
      p.video ? h('span.tile-video', icon('play'), p.duration ? fmtTime(p.duration) : '') : null)));
      placeHead.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (e) { placeGrid.replaceChildren(h('div.empty', e.message)); }
  }

  // déplacement à la souris ou au doigt, pincement pour zoomer, molette, double-clic
  const touches = new Map();
  let drag = null, moved = false, pinch = null;
  map.addEventListener('pointerdown', (e) => {
    if (e.target.closest('.map-zoom, .map-credit')) return;
    touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
    moved = false;
    if (touches.size === 1) drag = { x: e.clientX, y: e.clientY, cx, cy };
    else if (touches.size === 2) {
      const [a, b] = [...touches.values()];
      pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) };
      drag = null;
    }
  });
  map.addEventListener('pointermove', (e) => {
    if (!touches.has(e.pointerId)) return;
    touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pinch && touches.size === 2) {
      const [a, b] = [...touches.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (Math.abs(Math.log2(d / pinch.d)) >= 0.6) {
        const r = map.getBoundingClientRect();
        zoomAt(d > pinch.d ? 1 : -1, (a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top);
        pinch.d = d;
        moved = true;
        for (const id of touches.keys()) map.setPointerCapture(id);
      }
      return;
    }
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (!moved && Math.abs(dx) + Math.abs(dy) > 4) {
      moved = true;
      map.setPointerCapture(e.pointerId); // seulement une fois le glissement commencé : un simple clic atteint la pastille
    }
    if (!moved) return;
    cx = drag.cx - dx;
    cy = Math.max(0, Math.min(TILE * 2 ** z, drag.cy - dy));
    draw();
  });
  const release = (e) => {
    touches.delete(e.pointerId);
    if (touches.size < 2) pinch = null;
    if (!touches.size) drag = null;
  };
  map.addEventListener('pointerup', release);
  map.addEventListener('pointercancel', release);
  map.addEventListener('wheel', (e) => {
    e.preventDefault();
    const r = map.getBoundingClientRect();
    zoomAt(e.deltaY < 0 ? 1 : -1, e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  map.addEventListener('dblclick', (e) => {
    const r = map.getBoundingClientRect();
    zoomAt(1, e.clientX - r.left, e.clientY - r.top);
  });
  zoomIn.addEventListener('click', () => zoomAt(1, map.clientWidth / 2, map.clientHeight / 2));
  zoomOut.addEventListener('click', () => zoomAt(-1, map.clientWidth / 2, map.clientHeight / 2));
  const ro = new ResizeObserver(() => draw());
  ro.observe(map);
  ctx.setCleanup(() => { ro.disconnect(); closeViewer(); });
  draw();
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
      p.lat != null && p.lon != null ? h('div', h('a', { href: `https://www.openstreetmap.org/?mlat=${p.lat}&mlon=${p.lon}#map=16/${p.lat}/${p.lon}`,
        target: '_blank', rel: 'noopener' }, icon('place'), ` ${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}`)) : null,
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
