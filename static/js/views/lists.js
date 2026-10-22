// Collections, playlists et « À regarder ».
import { del, get, put } from '../api.js';
import { state } from '../app.js';
import { discoverCard } from './discover.js';
import { clear, confirmDialog, episodeCode, fmtDuration, h, icon, img, modal, field, posterCard, progressBar, toast } from '../ui.js';

export async function renderCollection(main, cid, ctx) {
  const c = await get(`/api/collections/${cid}`);
  if (!ctx.isCurrent()) return;
  document.title = `${c.title} · Séance`;
  const adminTools = state.me.user.is_admin ? h('div.actions-row',
    h('button.btn.ghost.small', { onclick: async () => {
      const t = h('input', { value: c.title, maxlength: 100 });
      const sm = h('textarea', { rows: 4, maxlength: 4000 }, c.summary || '');
      const ok = await modal('Modifier la collection', h('div', field('Titre', t), field('Résumé', sm)), [
        { label: 'Annuler', value: null },
        { label: 'Enregistrer', primary: true, handler: async () => { await put(`/api/admin/collections/${c.id}`, { title: t.value, summary: sm.value }); return true; } }]);
      if (ok) renderCollection(main, cid, ctx);
    } }, 'Modifier'),
    h('button.btn.ghost.small', { onclick: async () => {
      if (!await confirmDialog('Supprimer la collection ?', 'Les films et séries restent dans la bibliothèque.', 'Supprimer')) return;
      await del(`/api/admin/collections/${c.id}`);
      location.hash = c.library ? `#/library/${c.library.id}?view=collections` : '#/';
    } }, 'Supprimer')) : null;
  clear(main,
    h('section.detail', { style: { '--backdrop': `url("${img(c.art, 1600, 900)}")` } }, h('div.detail-backdrop'),
      h('div.detail-inner',
        h('div.detail-poster', h('img', { src: img(c.thumb, 300, 450), alt: '' })),
        h('div.detail-info',
          c.library ? h('a.detail-kicker', { href: `#/library/${c.library.id}?view=collections` }, `Collections · ${c.library.title}`) : null,
          h('h1.detail-title', c.title),
          h('div.meta-line', h('span', `${c.items.length} élément(s)`)),
          c.summary ? h('p.summary', c.summary) : null,
          adminTools))),
    h('section.block', h('div.grid', c.items.map((x) => posterCard(x)))));
}

export async function renderWatchlist(main, ctx) {
  const discover = state.me.features && state.me.features.discover;
  const [items, wishes] = await Promise.all([get('/api/watchlist'), discover ? get('/api/wishlist').catch(() => []) : []]);
  if (!ctx.isCurrent()) return;
  const total = items.length + wishes.length;
  clear(main, h('div.page-head', h('h1', 'À regarder'), h('span.muted', `${total} élément(s)`)),
    items.length ? h('div.grid', items.map((x) => posterCard(x))) : null,
    wishes.length ? h('section.block', h('h2', 'Pas encore dans la médiathèque'),
      h('p.muted.small', 'Ces titres rejoindront ta liste dès leur arrivée sur le serveur.'),
      h('div.grid', wishes.map(discoverCard))) : null,
    total ? null : h('div.empty', 'Ta liste est vide. Utilise le bouton ', icon('bookmark'), ' sur un film ou une série pour l’y ajouter',
      discover ? h('span', ', même s’il n’est pas encore sur le serveur (onglet ', h('a', { href: '#/discover' }, 'Découvrir'), ').') : '.'));
}

export async function renderPlaylists(main, ctx) {
  const lists = await get('/api/playlists');
  if (!ctx.isCurrent()) return;
  clear(main, h('div.page-head', h('h1', 'Mes playlists')),
    lists.length ? h('div.grid.wide', lists.map((p) => h('a.card.wide.playlist-card', { href: `#/playlist/${p.id}` },
      h('div.card-img.mosaic', p.thumbs.length ? p.thumbs.map((t) => h('img', { src: img(t, 200, 300), alt: '' })) : h('div.noimg', p.title)),
      h('div.card-title', p.title), h('div.card-sub', `${p.count} élément(s) · ${fmtDuration(p.duration)}`))))
      : h('div.empty', 'Aucune playlist. Ajoute un film ou un épisode depuis son menu ⋯ → « Ajouter à une playlist… ».'));
}

export async function renderPlaylist(main, pid, ctx) {
  const p = await get(`/api/playlists/${pid}`);
  if (!ctx.isCurrent()) return;
  document.title = `${p.title} · Séance`;
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
    h('div.page-head', h('h1', p.title), h('span.muted', `${p.count} élément(s) · ${fmtDuration(p.duration)}`),
      h('div.filters',
        first ? h('a.btn.primary', { href: `#/play/${first.id}?playlist=${p.id}` }, icon('play'), 'Tout lire') : null,
        h('button.btn.ghost', { onclick: async () => {
          const t = h('input', { value: p.title, maxlength: 100 });
          const ok = await modal('Renommer', field('Titre', t), [{ label: 'Annuler', value: null },
            { label: 'Enregistrer', primary: true, handler: async () => { await put(`/api/playlists/${p.id}`, { title: t.value }); return true; } }]);
          if (ok) reload();
        } }, 'Renommer'),
        h('button.btn.ghost', { onclick: async () => {
          if (!await confirmDialog('Supprimer la playlist ?', `« ${p.title} » sera supprimée.`, 'Supprimer')) return;
          await del(`/api/playlists/${p.id}`);
          location.hash = '#/playlists';
        } }, 'Supprimer'))),
    p.items.length ? h('div.episodes', p.items.map((x, i) => h('div.episode', { class: x.watched ? 'episode watched' : 'episode' },
      h('a.episode-thumb', { href: `#/play/${x.id}?playlist=${p.id}${x.position && !x.watched ? '&resume=1' : ''}` },
        h('img', { src: img(x.type === 'episode' ? x.thumb : (x.art || x.thumb), 320, 180), alt: '', loading: 'lazy' }),
        h('span.play-hover', icon('play')), progressBar(x.position, x.duration)),
      h('div.episode-body',
        h('div.episode-head',
          h('a.episode-title', { href: `#/item/${x.id}` }, x.type === 'episode' ? `${x.show} · ${episodeCode(x)} · ${x.title}` : x.title),
          h('span.muted', fmtDuration(x.duration)),
          h('button.icon-btn', { title: 'Monter', onclick: () => move(i, -1), disabled: i === 0 }, '↑'),
          h('button.icon-btn', { title: 'Descendre', onclick: () => move(i, 1), disabled: i === p.items.length - 1 }, '↓'),
          h('button.icon-btn', { title: 'Retirer', onclick: async () => {
            try { await del(`/api/playlists/${p.id}/items/${x.entry_id}`); reload(); } catch (e) { toast(e.message, 'bad'); }
          } }, icon('trash')))))))
      : h('div.empty', 'Playlist vide.'));
}
