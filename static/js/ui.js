// DOM helpers, formatting and shared components. Text is always set with
// textContent, never innerHTML, so metadata cannot inject markup.

export function h(tag, attrs, ...children) {
  const m = /^([a-z0-9]+)?((?:[.#][\w-]+)*)$/i.exec(tag);
  const el = document.createElement((m && m[1]) || 'div');
  if (m && m[2]) {
    for (const part of m[2].match(/[.#][\w-]+/g)) {
      if (part[0] === '.') el.classList.add(part.slice(1));
      else el.id = part.slice(1);
    }
  }
  if (attrs && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') {
      for (const [sk, sv] of Object.entries(v)) {
        if (sk.startsWith('--')) el.style.setProperty(sk, sv); else el.style[sk] = sv;
      }
    }
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k in el && typeof v !== 'string') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

// Préférences locales du navigateur (volume, filtre…) : sans effet si le stockage est indisponible.
export function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch { /* stockage indisponible (navigation privée…) */ }
  return null;
}

export function img(path, w, h) {
  if (!path) return '';
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  return `/api/img?p=${encodeURIComponent(path)}&w=${Math.round(w * dpr)}&h=${Math.round(h * dpr)}`;
}

export function fmtTime(ms, withHours = false) {
  ms = Math.max(0, Math.floor((ms || 0) / 1000));
  const hh = Math.floor(ms / 3600), mm = Math.floor((ms % 3600) / 60), ss = ms % 60;
  const p = (n) => String(n).padStart(2, '0');
  return hh || withHours ? `${hh}:${p(mm)}:${p(ss)}` : `${mm}:${p(ss)}`;
}

export function fmtDuration(ms) {
  if (!ms) return '';
  const min = Math.round(ms / 60000);
  if (min < 60) return `${min} min`;
  return `${Math.floor(min / 60)} h ${String(min % 60).padStart(2, '0')}`;
}

export function fmtDate(ts) {
  return new Date(ts * 1000).toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' });
}

export function episodeCode(c) {
  if (c.season == null || c.episode == null) return '';
  return `S${String(c.season).padStart(2, '0')}E${String(c.episode).padStart(2, '0')}`;
}

export function icon(name) {
  const paths = {
    play: 'M8 5v14l11-7z',
    pause: 'M6 5h4v14H6zM14 5h4v14h-4z',
    check: 'M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z',
    back: 'M20 11H7.8l5.6-5.6L12 4l-8 8 8 8 1.4-1.4L7.8 13H20z',
    search: 'M15.5 14h-.8l-.3-.3A6.5 6.5 0 1 0 14 15.5l.3.3v.8l5 5 1.5-1.5-5-5zm-6 0a4.5 4.5 0 1 1 0-9 4.5 4.5 0 0 1 0 9z',
    rewind: 'M12 5V1L7 6l5 5V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z',
    forward: 'M12 5V1l5 5-5 5V7a6 6 0 1 0 6 6h2a8 8 0 1 1-8-8z',
    full: 'M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z',
    exitfull: 'M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z',
    volume: 'M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 8v8a4.5 4.5 0 0 0 2.5-4z',
    mute: 'M16.5 12A4.5 4.5 0 0 0 14 8v2.2l2.5 2.5V12zM19 12a7 7 0 0 1-.6 2.8l1.5 1.5A9 9 0 0 0 21 12a9 9 0 0 0-7-8.8v2A7 7 0 0 1 19 12zM4.3 3 3 4.3 7.7 9H3v6h4l5 5v-6.7l4.3 4.3a7 7 0 0 1-2.3 1.2v2a9 9 0 0 0 3.7-1.8l2 2 1.3-1.3L4.3 3zM12 4 9.9 6.1 12 8.2V4z',
    cog: 'M19.4 13a7.5 7.5 0 0 0 0-2l2.1-1.6-2-3.5-2.5 1a7.7 7.7 0 0 0-1.7-1L15 3.3h-4l-.4 2.6a7.7 7.7 0 0 0-1.7 1l-2.5-1-2 3.5L6.6 11a7.5 7.5 0 0 0 0 2l-2.1 1.6 2 3.5 2.5-1c.5.4 1.1.7 1.7 1l.4 2.6h4l.4-2.6c.6-.3 1.2-.6 1.7-1l2.5 1 2-3.5L19.4 13zM13 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7z',
    subs: 'M20 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2zM4 12h4v2H4v-2zm10 6H4v-2h10v2zm6 0h-4v-2h4v2zm0-4H10v-2h10v2z',
    next: 'M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z',
    skip: 'M5 5v14l7-7zm7 0v14l7-7z',
    eye: 'M12 5C7 5 2.7 8.1 1 12.5 2.7 16.9 7 20 12 20s9.3-3.1 11-7.5C21.3 8.1 17 5 12 5zm0 12.5a5 5 0 1 1 0-10 5 5 0 0 1 0 10zm0-8a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
    trash: 'M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z',
    close: 'M19 6.4 17.6 5 12 10.6 6.4 5 5 6.4 10.6 12 5 17.6 6.4 19 12 13.4 17.6 19 19 17.6 13.4 12z',
    chapters: 'M3 13h2v-2H3v2zm0 4h2v-2H3v2zm0-8h2V7H3v2zm4 4h14v-2H7v2zm0 4h14v-2H7v2zM7 7v2h14V7H7z',
    more: 'M12 8a2 2 0 1 0 0-4 2 2 0 0 0 0 4zm0 2a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm0 6a2 2 0 1 0 0 4 2 2 0 0 0 0-4z',
    bookmark: 'M17 3H7a2 2 0 0 0-2 2v16l7-3 7 3V5a2 2 0 0 0-2-2zm0 15-5-2.2L7 18V5h10v13z',
    bookmarked: 'M17 3H7a2 2 0 0 0-2 2v16l7-3 7 3V5a2 2 0 0 0-2-2z',
    film: 'M18 4l2 4h-3l-2-4h-2l2 4h-3l-2-4H8l2 4H7L5 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V4h-4z',
    playlist: 'M3 10h11v2H3v-2zm0-4h11v2H3V6zm0 8h7v2H3v-2zm13-1v8l6-4-6-4z',
    group: 'M16 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm-8 0a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm0 2c-2.3 0-7 1.2-7 3.5V19h14v-2.5C15 14.2 10.3 13 8 13zm8 0c-.3 0-.6 0-1 .1 1.2.8 2 2 2 3.4V19h6v-2.5c0-2.3-4.7-3.5-7-3.5z',
    tv: 'M21 3H3a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h5v2h8v-2h5a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2zm0 14H3V5h18v12z',
    star: 'M22 9.2l-7.2-.6L12 2 9.2 8.6 2 9.2l5.5 4.7L5.8 21 12 17.3 18.2 21l-1.6-7.1L22 9.2zM12 15.4l-3.8 2.3 1-4.3-3.3-2.9 4.4-.4L12 6.1l1.7 4 4.4.4-3.3 2.9 1 4.3-3.8-2.3z',
    starred: 'M12 17.3 18.2 21l-1.6-7.1L22 9.2l-7.2-.6L12 2 9.2 8.6 2 9.2l5.5 4.7L5.8 21z',
    guide: 'M3 5h18v2H3V5zm0 6h6v2H3v-2zm8 0h10v2H11v-2zm-8 6h10v2H3v-2zm12 0h6v2h-6v-2z',
    clock: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16zm.5-13H11v6l5.2 3.2.8-1.3-4.5-2.7V7z',
    link: 'M3.9 12a3.1 3.1 0 0 1 3.1-3.1h4V7H7a5 5 0 0 0 0 10h4v-1.9H7A3.1 3.1 0 0 1 3.9 12zM8 13h8v-2H8v2zm9-6h-4v1.9h4a3.1 3.1 0 0 1 0 6.2h-4V17h4a5 5 0 0 0 0-10z',
    up: 'M7.4 15.4 12 10.8l4.6 4.6L18 14l-6-6-6 6z',
    down: 'M7.4 8.6 12 13.2l4.6-4.6L18 10l-6 6-6-6z',
    pip: 'M19 11h-8v6h8v-6zm4 8V5a2 2 0 0 0-2-2H3a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h18a2 2 0 0 0 2-2zm-2 0H3V5h18v14z',
    list: 'M3 13h2v-2H3v2zm0 4h2v-2H3v2zm0-8h2V7H3v2zm4 4h14v-2H7v2zm0 4h14v-2H7v2zM7 7v2h14V7H7z',
  };
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('icon');
  const p = document.createElementNS(ns, 'path');
  p.setAttribute('d', paths[name] || '');
  svg.append(p);
  return svg;
}

export function toast(message, kind = 'info', action = null) {
  let box = document.getElementById('toasts');
  if (!box) {
    box = h('div#toasts', { role: 'status', 'aria-live': 'polite' });
    document.body.append(box);
  }
  const t = h(`div.toast.${kind}`, message, action ? h('button.toast-action', {
    onclick: () => { t.remove(); action.onClick(); } }, action.label) : null);
  box.append(t);
  const wait = action ? 6000 : 3600;
  setTimeout(() => t.classList.add('out'), wait);
  setTimeout(() => t.remove(), wait + 400);
}

export function modal(title, body, actions = []) {
  return new Promise((resolve) => {
    const close = (v) => { overlay.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
    const onKey = (e) => { if (e.key === 'Escape') close(null); };
    const overlay = h('div.modal-overlay', { onclick: (e) => { if (e.target === overlay) close(null); } },
      h('div.modal', { role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
        h('div.modal-head', h('h2', title),
          h('button.icon-btn', { onclick: () => close(null), 'aria-label': 'Fermer' }, icon('close'))),
        h('div.modal-body', body),
        actions.length ? h('div.modal-actions', actions.map((a) =>
          h(`button.btn${a.primary ? '.primary' : ''}${a.danger ? '.danger' : ''}`, {
            onclick: async (e) => {
              if (!a.handler) return close(a.value);
              const btn = e.currentTarget;
              btn.disabled = true;
              try {
                const v = await a.handler();
                if (v !== false) close(v ?? a.value);
              } finally { btn.disabled = false; }
            },
          }, a.label))) : null));
    document.body.append(overlay);
    document.addEventListener('keydown', onKey);
    const first = overlay.querySelector('input, select, button.primary');
    if (first) setTimeout(() => first.focus(), 30);
  });
}

export async function confirmDialog(title, message, label = 'Confirmer', danger = true) {
  const r = await modal(title, h('p', message), [
    { label: 'Annuler', value: false },
    { label, value: true, primary: !danger, danger },
  ]);
  return r === true;
}

export function field(label, input, hint) {
  return h('label.field', h('span.field-label', label), input, hint ? h('span.field-hint', hint) : null);
}

export function select(options, value, attrs = {}) {
  return h('select', attrs, options.map(([v, l]) => h('option', { value: v, selected: String(v) === String(value) }, l)));
}

export function toggle(label, checked, attrs = {}) {
  return h('label.toggle', h('input', { type: 'checkbox', checked, ...attrs }), h('span.toggle-ui'), h('span', label));
}

export function spinner() {
  return h('div.spinner', { role: 'progressbar', 'aria-label': 'Chargement' });
}

export function errorBox(err, retry) {
  return h('div.error-box', h('p', err.message || String(err)),
    retry ? h('button.btn', { onclick: retry }, 'Réessayer') : null);
}

export function avatar(user, size = 32) {
  const initial = (user.display_name || user.username || '?').trim().charAt(0).toUpperCase();
  return h('span.avatar', { style: { background: user.avatar_color, width: `${size}px`, height: `${size}px`,
    fontSize: `${Math.round(size * 0.45)}px` } }, initial);
}

// ---------- media cards ----------
export function progressBar(position, duration) {
  if (!position || !duration) return null;
  const pct = Math.min(100, Math.max(2, (position / duration) * 100));
  return h('div.progress', h('div.progress-fill', { style: { width: `${pct}%` } }));
}

function badge(c) {
  if (c.type === 'show' || c.type === 'season') {
    if (c.watched) return h('span.badge.done', icon('check'));
    if (c.leaves && c.leaves > (c.watched_count || 0)) return h('span.badge.count', c.leaves - (c.watched_count || 0));
    return null;
  }
  return c.watched ? h('span.badge.done', icon('check')) : null;
}

export function posterCard(c, { href } = {}) {
  const link = href || (c.type === 'episode' ? `#/item/${c.id}` : `#/item/${c.id}`);
  let sub = c.year || '';
  if (c.type === 'episode') sub = `${episodeCode(c)} · ${c.title}`;
  if (c.type === 'show' && c.latest) sub = `Nouveau : S${c.latest.season} E${c.latest.episode}`;
  if (c.type === 'season') sub = `${c.leaves || 0} épisodes`;
  const thumb = c.type === 'episode' ? (c.show_thumb || c.thumb) : c.thumb;
  return h('a.card.poster', { href: link, title: c.title },
    h('div.card-img', thumb ? h('img', { src: img(thumb, 200, 300), alt: '', loading: 'lazy' }) : h('div.noimg', c.title),
      badge(c), progressBar(c.position, c.duration)),
    h('div.card-title', c.type === 'episode' ? c.show : c.title),
    h('div.card-sub', String(sub)));
}

export function wideCard(c, { onPlay, onWatched } = {}) {
  const thumb = c.type === 'episode' ? (c.thumb || c.show_art) : (c.art || c.thumb);
  const title = c.type === 'episode' ? c.show : c.title;
  let sub = c.type === 'episode' ? `${episodeCode(c)} · ${c.title}` : (c.year || '');
  if (c.next_up) sub = `À suivre · ${sub}`;
  const left = c.duration && c.position ? `${fmtDuration(c.duration - c.position)} restantes` : '';
  const card = h('a.card.wide', { href: `#/play/${c.id}${c.position ? '?resume=1' : ''}`, title,
    onclick: onPlay ? (e) => { e.preventDefault(); onPlay(c); } : null },
    h('div.card-img', thumb ? h('img', { src: img(thumb, 400, 225), alt: '', loading: 'lazy' }) : h('div.noimg', title),
      h('span.play-hover', icon('play')), progressBar(c.position, c.duration)),
    h('div.card-title', title),
    h('div.card-sub', String(sub), left ? h('span.muted', ` — ${left}`) : null));
  if (!onWatched) return card;
  // bouton à côté du lien (pas dedans) : « marquer comme vu » sans lancer la lecture
  const label = c.type === 'episode' ? `Marquer « ${episodeCode(c)} » comme vu` : 'Marquer comme vu';
  return h('div.card-slot', card,
    h('button.card-watch', { type: 'button', title: label, 'aria-label': `${label} : ${title}`,
      onclick: (e) => { e.currentTarget.disabled = true; onWatched(c, slot()); } }, icon('check')));
  function slot() { return card.parentElement; }
}

export function row(title, items, render, opts = {}) {
  if (!items || !items.length) return null;
  const track = h('div.row-track', items.map(render));
  const scroll = (dir) => track.scrollBy({ left: dir * track.clientWidth * 0.85, behavior: 'smooth' });
  return h('section.row', opts.wide ? { class: 'row wide-row' } : null,
    h('div.row-head', opts.href ? h('a.row-title', { href: opts.href }, title) : h('h2.row-title', title),
      h('div.row-nav',
        h('button.icon-btn', { onclick: () => scroll(-1), 'aria-label': 'Précédent' }, '‹'),
        h('button.icon-btn', { onclick: () => scroll(1), 'aria-label': 'Suivant' }, '›'))),
    track);
}

// Menu « ⋯ » : bouton + liste d'actions, fermé au clic extérieur ou avec Échap.
export function moreMenu(items, label = 'Plus d’actions') {
  const list = h('div.more-list', { role: 'menu', hidden: true });
  const close = () => { list.hidden = true; document.removeEventListener('click', onDoc, true); document.removeEventListener('keydown', onKey); };
  const onDoc = (e) => { if (!wrap.contains(e.target)) close(); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  for (const it of items.filter(Boolean)) {
    if (it.divider) { list.append(h('div.more-divider')); continue; }
    if (it.heading) { list.append(h('div.more-heading', it.heading)); continue; }
    list.append(h('button.more-item', { role: 'menuitem', class: it.danger ? 'more-item danger' : 'more-item',
      onclick: async () => { close(); await it.onClick(); } }, it.label));
  }
  const btn = h('button.btn.ghost.icon-only', { 'aria-label': label, title: label, 'aria-haspopup': 'menu', onclick: (e) => {
    e.stopPropagation();
    if (list.hidden) {
      list.hidden = false;
      document.addEventListener('click', onDoc, true);
      document.addEventListener('keydown', onKey);
    } else close();
  } }, icon('more'));
  const wrap = h('div.more-wrap', btn, list);
  return wrap;
}
