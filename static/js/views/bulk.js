// Sélection multiple dans une bibliothèque : un bouton « Sélectionner », puis un clic sur les titres les coche au
// lieu de les ouvrir, et une barre d'actions s'applique à toute la sélection (vus / non vus, téléchargement,
// playlist, masquer ; pour un administrateur : métadonnées, collection).
import { get, post, put } from '../api.js';
import { state } from '../app.js';
import { h, icon, modal, toast } from '../ui.js';
import { plural, tr } from '../i18n.js';

// une action sur chaque titre, quatre à la fois -> nombre de réussites
async function each(ids, fn) {
  let ok = 0;
  let next = 0;
  let firstError = null;
  const lane = async () => {
    while (next < ids.length) {
      const id = ids[next++];
      try { await fn(id); ok += 1; } catch (e) { firstError = firstError || e; }
    }
  };
  await Promise.all(Array.from({ length: Math.min(4, ids.length) }, lane));
  if (firstError && !ok) throw firstError;
  return ok;
}

async function pickPlaylist() {
  const lists = await get('/api/playlists');
  const name = h('input', { placeholder: tr('Nouvelle playlist…'), maxlength: 100 });
  let chosen = null;
  const ok = await modal(tr('Ajouter à une playlist'), h('div',
    lists.length ? h('div.pick-list', lists.filter((p) => p.mine !== false).map((p) => h('button.pick-item', { type: 'button', onclick: (e) => {
      chosen = { id: p.id };
      e.currentTarget.closest('.modal').querySelector('.modal-actions .btn.primary').click();
    } }, h('strong', p.title), h('span.muted.small', tr(' {count} élément(s)', { count: p.count }))))) : h('p.muted', tr('Aucune playlist pour l’instant.')),
    h('div.field', h('span.field-label', tr('Ou une nouvelle playlist')), name)),
  [{ label: tr('Annuler'), value: false }, { label: tr('Ajouter'), primary: true, value: true }]);
  if (!ok) return null;
  return chosen || (name.value.trim() ? { title: name.value.trim() } : null);
}

async function pickCollection(firstId) {
  const list = await get(`/api/admin/collections/for/${firstId}`);
  const name = h('input', { placeholder: tr('Nouvelle collection…'), maxlength: 100 });
  let chosen = null;
  const ok = await modal(tr('Ajouter à une collection'), h('div',
    list.length ? h('div.pick-list', list.filter((c) => c.kind !== 'auto').map((c) => h('button.pick-item', { type: 'button', onclick: (e) => {
      chosen = { id: c.id };
      e.currentTarget.closest('.modal').querySelector('.modal-actions .btn.primary').click();
    } }, h('strong', c.title)))) : h('p.muted', tr('Aucune collection dans cette bibliothèque.')),
    h('div.field', h('span.field-label', tr('Ou une nouvelle collection')), name)),
  [{ label: tr('Annuler'), value: false }, { label: tr('Ajouter'), primary: true, value: true }]);
  if (!ok) return null;
  return chosen || (name.value.trim() ? { title: name.value.trim() } : null);
}

// grid : conteneur des cartes (liens vers #/item/<id>) ; cards : Map id -> carte reçue du serveur
export function bulkSelection(grid, cards, { downloadDialog, canDownload }) {
  const picked = new Set();
  let on = false;
  const count = h('strong.bulk-count');
  const admin = state.me.user.is_admin;
  const chosen = () => [...picked].map((id) => cards.get(id)).filter(Boolean);
  const idOf = (el) => { const m = /#\/item\/(\d+)/.exec(el.getAttribute('href') || ''); return m ? m[1] : null; };
  const paint = () => {
    for (const el of grid.querySelectorAll('a[href^="#/item/"]')) el.classList.toggle('sel', picked.has(idOf(el)));
    count.textContent = plural(picked.size, '{n} sélectionné', '{n} sélectionnés');
    bar.querySelectorAll('[data-needs]').forEach((b) => { b.disabled = !picked.size; });
  };
  const run = async (label, fn, ids = [...picked]) => {
    if (!ids.length) return;
    bar.classList.add('busy');
    try {
      const n = await each(ids, fn);
      toast(`${label} : ${plural(n, '{n} titre', '{n} titres')}`, 'ok');
    } catch (e) { toast(e.message, 'bad'); } finally { bar.classList.remove('busy'); }
  };
  const setWatched = (watched) => run(watched ? tr('Marqués comme vus') : tr('Marqués comme non vus'), async (id) => {
    await post(`/api/items/${id}/watched`, { watched });
    const c = cards.get(id);
    if (c) c.watched = watched;
    for (const el of grid.querySelectorAll(`a[href="#/item/${id}"]`)) {
      const img = el.querySelector('.card-img');
      if (!img) continue;
      img.querySelector('.badge.done')?.remove();
      if (watched) img.append(h('span.badge.done', icon('check')));
    }
  });
  const btn = (label, ic, onclick, extra = {}) => h('button.btn.small', { type: 'button', 'data-needs': '1', onclick, ...extra }, ic ? icon(ic) : null, label);
  const bar = h('div.bulk-bar', { hidden: true, role: 'toolbar', 'aria-label': tr('Actions sur la sélection') },
    count,
    h('button.btn.small.ghost', { type: 'button', onclick: () => {
      for (const el of grid.querySelectorAll('a[href^="#/item/"]')) { const id = idOf(el); if (id) picked.add(id); }
      paint();
    } }, tr('Tout')),
    h('button.btn.small.ghost', { type: 'button', onclick: () => { picked.clear(); paint(); } }, tr('Aucun')),
    h('span.bulk-sep'),
    btn(tr('Vus'), 'check', () => setWatched(true)),
    btn(tr('Non vus'), null, () => setWatched(false)),
    canDownload() ? btn(tr('Télécharger'), 'download', async () => {
      const items = chosen().filter((c) => ['movie', 'episode'].includes(c.type));
      if (!items.length) { toast(tr('Seuls les films et les épisodes se téléchargent : ouvrez une série pour télécharger ses épisodes'), 'bad'); return; }
      await downloadDialog(items, plural(items.length, '{n} titre', '{n} titres'));
    }) : null,
    btn(tr('Playlist…'), 'playlist', async () => {
      const target = await pickPlaylist();
      if (!target) return;
      const ids = [...picked];
      let pid = target.id;
      if (!pid) pid = (await post('/api/playlists', { title: target.title, item_id: ids[0] })).id;
      const rest = target.id ? ids : ids.slice(1);
      if (rest.length) await run(tr('Ajoutés à la playlist'), (id) => post(`/api/playlists/${pid}/items`, { item_id: id }), rest);
      else toast(tr('Playlist créée'), 'ok');
    }),
    btn(tr('Masquer pour moi'), 'eye', () => run(tr('Masqués'), async (id) => {
      await put(`/api/items/${id}/hidden`, { hidden: true });
      for (const el of grid.querySelectorAll(`a[href="#/item/${id}"]`)) el.remove();
      picked.delete(id);
    }).then(paint)),
    admin ? h('span.bulk-sep') : null,
    admin ? btn(tr('Actualiser les métadonnées'), 'cog', () => run(tr('Métadonnées actualisées'), (id) => post(`/api/admin/refresh/${id}`))) : null,
    admin ? btn(tr('Collection…'), 'folder', async () => {
      const ids = [...picked];
      const target = await pickCollection(ids[0]);
      if (!target) return;
      let cid = target.id;
      if (!cid) cid = (await post('/api/admin/collections', { title: target.title, item_id: ids[0] })).id;
      const rest = target.id ? ids : ids.slice(1);
      if (rest.length) await run(tr('Ajoutés à la collection'), (id) => post(`/api/admin/collections/${cid}/items`, { item_id: id }), rest);
      else toast(tr('Collection créée'), 'ok');
    }) : null,
    admin ? btn(tr('Archiver…'), 'backup', async () => {
      const ids = [...picked];
      (await import('./admin.js')).archiveDialog(ids, plural(ids.length, '{n} titre', '{n} titres'));
    }) : null,
    h('button.btn.small.primary.bulk-done', { type: 'button', onclick: () => toggle(false) }, tr('Terminé')));

  // en mode sélection, un clic coche au lieu d'ouvrir
  grid.addEventListener('click', (e) => {
    if (!on) return;
    const a = e.target.closest('a[href^="#/item/"]');
    if (!a || !grid.contains(a)) return;
    e.preventDefault();
    e.stopPropagation();
    const id = idOf(a);
    if (!id) return;
    if (picked.has(id)) picked.delete(id); else picked.add(id);
    paint();
  }, true);

  const button = h('button.btn.small', { type: 'button', onclick: () => toggle(!on) }, icon('check'), tr('Sélectionner'));
  function toggle(v) {
    on = v;
    if (!on) picked.clear();
    grid.classList.toggle('selecting', on);
    bar.hidden = !on;
    button.classList.toggle('primary', on);
    paint();
  }
  // nouvelles cartes chargées pendant qu'on fait défiler : cochées si elles l'étaient
  new MutationObserver(() => { if (on) paint(); }).observe(grid, { childList: true });
  return { button, bar, active: () => on };
}
