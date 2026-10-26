// Rangées personnelles de l'accueil : collections, playlists ou genres épinglés par chaque utilisateur.
import { get, put } from './api.js';
import { applyPrefs, state } from './app.js';
import { h, icon, posterCard, row, toast, wideCard } from './ui.js';

const same = (a, b) => a.type === b.type && String(a.ref) === String(b.ref) && String(a.lib || '') === String(b.lib || '');

export const pins = () => state.me.prefs.home_pins || [];

async function savePins(list, msg) {
  const r = await put('/api/me/prefs', { home_pins: list });
  Object.assign(state.me.prefs, r);
  applyPrefs(state.me.prefs);
  toast(msg, 'ok');
}

export async function removePin(id) {
  const order = (state.me.prefs.home_order || []).filter((k) => k !== `pin:${id}`);
  await put('/api/me/prefs', { home_order: order });
  state.me.prefs.home_order = order;
  await savePins(pins().filter((p) => p.id !== id), 'Rangée retirée de l’accueil');
}

// Bouton « Épingler à l'accueil » / « Retirer de l'accueil » ; pin = { type, ref, lib?, title }
export function pinButton(pin) {
  const btn = h('button.btn.ghost.small.pin-btn', { type: 'button' });
  const paint = () => {
    const on = pins().some((p) => same(p, pin));
    btn.replaceChildren(icon(on ? 'bookmarked' : 'bookmark'), on ? 'Retirer de l’accueil' : 'Épingler à l’accueil');
    btn.setAttribute('aria-pressed', String(on));
  };
  btn.addEventListener('click', async () => {
    try {
      const existing = pins().find((p) => same(p, pin));
      if (existing) await removePin(existing.id);
      else {
        if (pins().length >= 20) { toast('20 rangées épinglées au maximum', 'bad'); return; }
        const id = Math.max(0, ...pins().map((p) => p.id + 1));
        await savePins([...pins(), { id, ...pin, ref: String(pin.ref), ...(pin.lib ? { lib: String(pin.lib) } : {}) }],
          'Rangée ajoutée à l’accueil');
      }
      paint();
    } catch (e) { toast(e.message, 'bad'); }
  });
  paint();
  return btn;
}

export const PIN_LABELS = { collection: 'collection', playlist: 'playlist', genre: 'genre' };

// Rangée de l'accueil pour une épingle (null si la source n'existe plus ou est vide)
export async function pinRow(p) {
  try {
    if (p.type === 'collection') {
      const c = await get(`/api/collections/${p.ref}`);
      return row(c.title, c.items, (x) => posterCard(x), { href: `#/collection/${p.ref}` });
    }
    if (p.type === 'playlist') {
      const pl = await get(`/api/playlists/${p.ref}`);
      return row(pl.title, pl.items, (x) => wideCard(x), { href: `#/playlist/${p.ref}`, wide: true });
    }
    const lib = (state.me.libraries || []).find((l) => String(l.id) === String(p.lib));
    if (!lib) return null;
    const q = new URLSearchParams({ start: 0, size: 24, sort: 'added', genre: p.ref });
    const r = await get(`/api/libraries/${p.lib}/items?${q}`);
    return row(`${p.ref} · ${lib.title}`, r.items, (x) => posterCard(x), { href: `#/library/${p.lib}?genre=${encodeURIComponent(p.ref)}` });
  } catch {
    return null;
  }
}
