// Collections, playlists et « À regarder ».
import { del, get, put } from '../api.js';
import { state } from '../app.js';
import { discoverCard } from './discover.js';
import { pinButton } from '../pins.js';
import { clear, confirmDialog, episodeCode, fmtDuration, h, icon, img, modal, field, posterCard, progressBar, toast } from '../ui.js';
import { tr } from '../i18n.js';

export async function renderCollection(main, cid, ctx) {
  const c = await get(`/api/collections/${cid}`);
  if (!ctx.isCurrent()) return;
  document.title = `${c.title} · Nexis`;
  const adminTools = state.me.user.is_admin ? h('div.actions-row',
    h('button.btn.ghost.small', { onclick: async () => {
      const t = h('input', { value: c.title, maxlength: 100 });
      const sm = h('textarea', { rows: 4, maxlength: 4000 }, c.summary || '');
      const ok = await modal(tr('Modifier la collection'), h('div', field(tr('Titre'), t), field(tr('Résumé'), sm)), [
        { label: tr('Annuler'), value: null },
        { label: tr('Enregistrer'), primary: true, handler: async () => { await put(`/api/admin/collections/${c.id}`, { title: t.value, summary: sm.value }); return true; } }]);
      if (ok) renderCollection(main, cid, ctx);
    } }, tr('Modifier')),
    h('button.btn.ghost.small', { onclick: async () => {
      if (!await confirmDialog(tr('Supprimer la collection ?'), tr('Les films et séries restent dans la bibliothèque.'), tr('Supprimer'))) return;
      await del(`/api/admin/collections/${c.id}`);
      location.hash = c.library ? `#/library/${c.library.id}?view=collections` : '#/';
    } }, tr('Supprimer'))) : null;
  clear(main,
    h('section.detail', { style: { '--backdrop': `url("${img(c.art, 1600, 900)}")` } }, h('div.detail-backdrop'),
      h('div.detail-inner',
        h('div.detail-poster', h('img', { src: img(c.thumb, 300, 450), alt: '' })),
        h('div.detail-info',
          c.library ? h('a.detail-kicker', { href: `#/library/${c.library.id}?view=collections` }, tr('Collections · {title}', { title: c.library.title })) : null,
          h('h1.detail-title', c.title),
          h('div.meta-line', h('span', tr('{length} élément(s)', { length: c.items.length }))),
          c.summary ? h('p.summary', c.summary) : null,
          h('div.actions-row', pinButton({ type: 'collection', ref: c.id, title: c.title })),
          adminTools))),
    h('section.block', h('div.grid', c.items.map((x) => posterCard(x)))));
}

export async function renderWatchlist(main, ctx) {
  const discover = state.me.features && state.me.features.discover;
  const [items, wishes] = await Promise.all([get('/api/watchlist'), discover ? get('/api/wishlist').catch(() => []) : []]);
  if (!ctx.isCurrent()) return;
  const total = items.length + wishes.length;
  clear(main, h('div.page-head', h('h1', tr('À regarder')), h('span.muted', tr('{total} élément(s)', { total }))),
    items.length ? h('div.grid', items.map((x) => posterCard(x))) : null,
    wishes.length ? h('section.block', h('h2', tr('Pas encore dans la médiathèque')),
      h('p.muted.small', tr('Ces titres apparaîtront dans la liste dès leur arrivée sur le serveur.')),
      h('div.grid', wishes.map(discoverCard))) : null,
    total ? null : h('div.empty', tr('La liste est vide. Le bouton '), icon('bookmark'), tr(' sur un film ou une série pour l’y ajouter'),
      discover ? h('span', tr(', même s’il n’est pas encore sur le serveur (onglet '), h('a', { href: '#/discover' }, tr('Découvrir')), ').') : '.'));
}

export async function renderPlaylists(main, ctx) {
  const lists = await get('/api/playlists');
  if (!ctx.isCurrent()) return;
  clear(main, h('div.page-head', h('h1', tr('Mes playlists'))),
    lists.length ? h('div.grid.wide', lists.map((p) => h('a.card.wide.playlist-card', { href: `#/playlist/${p.id}` },
      h('div.card-img.mosaic', p.thumbs.length ? p.thumbs.map((t) => h('img', { src: img(t, 200, 300), alt: '' })) : h('div.noimg', p.title)),
      h('div.card-title', p.title), h('div.card-sub', tr('{count} élément(s) · {p2}', { count: p.count, p2: fmtDuration(p.duration) })))))
      : h('div.empty', tr('Aucune playlist. Pour en créer une : menu ⋯ d’un film ou d’un épisode, puis « Ajouter à une playlist… ».')));
}

export async function renderPlaylist(main, pid, ctx) {
  const p = await get(`/api/playlists/${pid}`);
  if (!ctx.isCurrent()) return;
  document.title = `${p.title} · Nexis`;
  const reload = () => renderPlaylist(main, pid, ctx);
  const move = async (i, dir) => {
    const order = p.items.map((x) => x.entry_id);
    const j = i + dir;
    if (j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    await put(`/api/playlists/${p.id}`, { order });
    reload();
  };
  const first = p.items.find((x) => !x.watched) || p.items[0];
  clear(main,
    h('div.page-head', h('h1', p.title), h('span.muted', tr('{count} élément(s) · {p2}', { count: p.count, p2: fmtDuration(p.duration) })),
      h('div.filters',
        first ? h('a.btn.primary', { href: `#/play/${first.id}?playlist=${p.id}` }, icon('play'), tr('Tout lire')) : null,
        pinButton({ type: 'playlist', ref: p.id, title: p.title }),
        h('button.btn.ghost', { onclick: async () => {
          const t = h('input', { value: p.title, maxlength: 100 });
          const ok = await modal(tr('Renommer'), field(tr('Titre'), t), [{ label: tr('Annuler'), value: null },
            { label: tr('Enregistrer'), primary: true, handler: async () => { await put(`/api/playlists/${p.id}`, { title: t.value }); return true; } }]);
          if (ok) reload();
        } }, tr('Renommer')),
        h('button.btn.ghost', { onclick: async () => {
          if (!await confirmDialog(tr('Supprimer la playlist ?'), tr('« {title} » sera supprimée.', { title: p.title }), tr('Supprimer'))) return;
          await del(`/api/playlists/${p.id}`);
          location.hash = '#/playlists';
        } }, tr('Supprimer')))),
    p.items.length ? h('div.episodes', p.items.map((x, i) => h('div.episode', { class: x.watched ? 'episode watched' : 'episode' },
      h('a.episode-thumb', { href: `#/play/${x.id}?playlist=${p.id}${x.position && !x.watched ? '&resume=1' : ''}` },
        h('img', { src: img(x.type === 'episode' ? x.thumb : (x.art || x.thumb), 320, 180), alt: '', loading: 'lazy' }),
        h('span.play-hover', icon('play')), progressBar(x.position, x.duration)),
      h('div.episode-body',
        h('div.episode-head',
          h('a.episode-title', { href: `#/item/${x.id}` }, x.type === 'episode' ? `${x.show} · ${episodeCode(x)} · ${x.title}` : x.title),
          h('span.muted', fmtDuration(x.duration)),
          h('button.icon-btn', { title: tr('Monter'), onclick: () => move(i, -1), disabled: i === 0 }, '↑'),
          h('button.icon-btn', { title: tr('Descendre'), onclick: () => move(i, 1), disabled: i === p.items.length - 1 }, '↓'),
          h('button.icon-btn', { title: tr('Retirer'), onclick: async () => {
            try { await del(`/api/playlists/${p.id}/items/${x.entry_id}`); reload(); } catch (e) { toast(e.message, 'bad'); }
          } }, icon('trash')))))))
      : h('div.empty', tr('Playlist vide.')));
}
