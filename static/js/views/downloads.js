// « Téléchargements » : ce qui est gardé sur cet appareil pour regarder hors ligne.
import { state } from '../app.js';
import { askPersist, list, onDownloads, posterUrl, pump, removeDownload, removeMany, retry, setSmart, smartPrefs, supported, usage } from '../offline.js';
import { clear, confirmDialog, episodeCode, fmtDuration, h, icon, select, toast, toggle } from '../ui.js';
import { tr, locale } from '../i18n.js';
import { nativeApp } from '../install.js';

const GB = 1024 ** 3;
const size = (b) => (b >= GB ? tr('{n} Go', { n: (b / GB).toLocaleString(locale(), { maximumFractionDigits: 1, minimumFractionDigits: 1 }) }) : tr('{n} Mo', { n: Math.max(1, Math.round(b / 1024 ** 2)) }));

const DAY = 86400000;
const received = (d) => (d.state === 'done' ? d.size : Math.min(d.size || Infinity, (d.got || 0) * (d.chunk || 8 * 1024 ** 2)));
const groupOf = (d) => (d.item.type === 'episode' ? `show:${d.item.show_id || d.item.show}` : 'movies');

function status(d) {
  if (d.state === 'done') return d.item.watched ? tr('Vu') : (d.item.position ? tr('Commencé · {p1} restantes', { p1: fmtDuration(d.item.duration - d.item.position) }) : tr('Prêt à regarder'));
  if (d.unstable && (d.state === 'downloading' || d.state === 'preparing')) return tr('Connexion instable : nouvel essai…');
  if (d.state === 'preparing' && d.waiting) return tr('En pause pendant une lecture sur le serveur');
  if (d.state === 'preparing') {
    const p = tr(d.convert ? 'Conversion sur le serveur… {p} %' : 'Préparation sur le serveur… {p} %', { p: Math.round((d.prep || 0) * 100) });
    return d.got ? `${p} · ${tr('{n} reçus', { n: size(received(d)) })}` : p; // récupéré pendant la préparation
  }
  if (d.state === 'downloading') return tr('Téléchargement… {p1} %', { p1: d.chunks ? Math.round((d.got / d.chunks) * 100) : 0 });
  if (d.state === 'paused') return tr('En attente du réseau : nouvel essai automatique');
  if (d.state === 'error') return d.error || tr('Échec');
  return tr('En attente');
}

function progress(d) {
  const v = d.state === 'done' ? 1 : d.state === 'downloading' ? (d.chunks ? d.got / d.chunks : 0) : d.state === 'preparing' ? (d.prep || 0) : 0;
  return h('div.dl-progress', { class: `dl-progress ${d.state}` }, h('span', { style: { width: `${v * 100}%` } }));
}

export async function renderDownloads(main, ctx, { offline = false } = {}) {
  if (!supported()) {
    clear(main, h('div.page-head', h('h1', tr('Téléchargements'))),
      h('div.empty', tr('Les téléchargements demandent un accès en HTTPS (ou l’application installée).')));
    return;
  }
  const uid = state.me.user.id;
  const box = h('div.dl-list');
  const meter = h('div.dl-meter');
  const room = h('div.dl-room');
  const tools = h('div.dl-tools');
  const firefox = /firefox|fxios/i.test(navigator.userAgent);
  const posters = new Map();
  const picked = new Set();
  let selecting = false;
  let sort = 'title';
  try { sort = localStorage.getItem('nexis.dl.sort') || 'title'; } catch { /* ignore */ }
  let items = [];

  const removeSelected = async () => {
    const chosen = items.filter((d) => picked.has(d.key));
    if (!chosen.length) return;
    const total = chosen.reduce((n, d) => n + received(d), 0);
    if (!await confirmDialog(tr('Supprimer {n} téléchargement(s) ?', { n: chosen.length }),
      tr('{n} libérés sur cet appareil. Ils ne seront plus disponibles hors ligne.', { n: size(total) }), tr('Supprimer'))) return;
    picked.clear();
    selecting = false;
    await removeMany(chosen.map((d) => d.key));
    await paint();
    toast(tr('{n} téléchargement(s) supprimé(s)', { n: chosen.length }), 'ok');
  };
  // sélections rapides
  const pickWhere = (fn) => { picked.clear(); items.filter(fn).forEach((d) => picked.add(d.key)); paint(); };

  const paintTools = () => {
    const shows = new Map();
    for (const d of items) if (d.item.type === 'episode') shows.set(groupOf(d), d.item.show);
    const chosen = items.filter((d) => picked.has(d.key));
    const sortSel = select([['title', tr('Titre')], ['size', tr('Taille')], ['date', tr('Date')]], sort, { onchange: (e) => {
      sort = e.target.value;
      try { localStorage.setItem('nexis.dl.sort', sort); } catch { /* ignore */ }
      paint();
    } });
    if (!selecting) {
      clear(tools, items.length ? h('button.btn.small', { onclick: () => { selecting = true; paint(); } }, icon('check'), tr('Sélectionner')) : null,
        items.length > 1 ? h('label.dl-sort', h('span.muted.small', tr('Trier par')), sortSel) : null);
      return;
    }
    clear(tools,
      h('strong.small', tr('{n} sélectionné(s)', { n: chosen.length }), chosen.length ? ` · ${size(chosen.reduce((n, d) => n + received(d), 0))}` : ''),
      h('button.btn.small', { onclick: () => pickWhere(() => true) }, tr('Tout')),
      h('button.btn.small', { onclick: () => pickWhere(() => false) }, tr('Aucun')),
      h('button.btn.small', { onclick: () => pickWhere((d) => d.item.watched) }, tr('Déjà vus')),
      h('button.btn.small', { onclick: () => pickWhere((d) => Date.now() - (d.doneAt || d.savedAt || Date.now()) > 30 * DAY) }, tr('Plus de 30 jours')),
      items.some((d) => d.state === 'error') ? h('button.btn.small', { onclick: () => pickWhere((d) => d.state === 'error') }, tr('En échec')) : null,
      shows.size ? select([['', tr('Une série…')], ...[...shows].map(([k, t]) => [k, t])], '', { onchange: (e) => {
        const g = e.target.value;
        if (g) pickWhere((d) => groupOf(d) === g);
      } }) : null,
      h('button.btn.small.danger', { disabled: !chosen.length, onclick: removeSelected }, icon('trash'), tr('Supprimer')),
      h('button.btn.small.ghost', { onclick: () => { selecting = false; picked.clear(); paint(); } }, tr('Terminé')));
  };

  const smartPanel = () => {
    const s = smartPrefs();
    const next = toggle(tr('Télécharger l’épisode suivant quand un épisode téléchargé est regardé jusqu’au bout'), s.next,
      { onchange: (e) => { setSmart({ next: e.target.checked }); toast(tr('Réglage enregistré sur cet appareil'), 'ok'); } });
    const clean = toggle(tr('Supprimer un épisode téléchargé une fois regardé'), s.clean,
      { onchange: (e) => { setSmart({ clean: e.target.checked }); toast(tr('Réglage enregistré sur cet appareil'), 'ok'); } });
    return h('details.dl-smart', h('summary', icon('bolt'), tr('Téléchargements intelligents')),
      h('p.muted.small', tr('Pour les séries : l’épisode suivant arrive tout seul, dans les mêmes langues et la même qualité ; l’épisode vu peut être supprimé pour faire de la place. Si l’épisode a été regardé hors ligne, c’est fait au retour de la connexion.')),
      next, clean);
  };

  const paint = async () => {
    items = await list(uid);
    if (!ctx.isCurrent()) return;
    for (const k of [...picked]) if (!items.some((d) => d.key === k)) picked.delete(k);
    if (sort === 'size') items.sort((a, b) => received(b) - received(a));
    else if (sort === 'date') items.sort((a, b) => (b.doneAt || b.savedAt || 0) - (a.doneAt || a.savedAt || 0));
    const u = await usage();
    const used = items.reduce((n, d) => n + received(d), 0);
    // Pas de « place restante » : Chrome (depuis la version 144) annonce volontairement « utilisé + 10 Go » pour ne pas
    // révéler la taille du disque, alors que la vraie limite est ~60 % du stockage. Le chiffre tromperait.
    const films = items.filter((d) => d.item.type !== 'episode').reduce((n, d) => n + received(d), 0);
    const watched = items.filter((d) => d.item.watched).reduce((n, d) => n + received(d), 0);
    clear(meter, h('p.muted.small', tr('{length} téléchargement(s) · {p2} sur cet appareil', { length: items.length, p2: size(used) })),
      used ? h('div.dl-bar', { title: tr('Films {a} · Séries {b}', { a: size(films), b: size(used - films) }) },
        h('span.films', { style: { width: `${(films / used) * 100}%` } }), h('span.shows', { style: { width: `${((used - films) / used) * 100}%` } })) : null,
      used ? h('p.small.muted.dl-legend', films ? [h('i.films'), tr('Films {n}', { n: size(films) }), ' '] : null,
        used - films ? [h('i.shows'), tr('Séries {n}', { n: size(used - films) })] : null,
        watched ? ` · ${tr('dont {n} déjà vus', { n: size(watched) })}` : '') : null);
    // La limite vient du navigateur, pas du téléphone : Firefox plafonne un site à 10 Go tant qu'on ne l'autorise pas
    // à garder ses données (jusqu'à la moitié de l'appareil ensuite) ; Chrome, Edge et Safari vont jusqu'à ~60 %.
    // Application Noxa (ordinateur, Android) : les téléchargements sont dans les données de l'application, que le
    // système n'efface pas ; la demande « stockage persistant » n'existe que dans un navigateur (l'application répond
    // toujours non). Rien à autoriser : la place est celle de l'appareil.
    if (nativeApp()) {
      room.replaceChildren(h('p.small.muted', tr('Application Noxa : les téléchargements sont gardés par l’application, sans limite propre au navigateur. La place disponible est celle de l’appareil.')));
    } else room.replaceChildren(...(u && !u.persisted ? [
      h('p.small', firefox
        ? tr('Firefox limite chaque site à 10 Go tant que le stockage persistant n’est pas autorisé. Une fois autorisé, Noxa peut utiliser jusqu’à la moitié du stockage de l’appareil, et les téléchargements ne sont plus effacés automatiquement.')
        : tr('Le navigateur peut effacer ces téléchargements s’il manque de place. Autoriser le stockage persistant les protège ; installer l’application sur l’écran d’accueil aide également.')),
      h('button.btn.small', { onclick: async () => {
        const ok = await askPersist();
        toast(ok ? tr('Stockage autorisé : les téléchargements sont gardés') : tr('Autorisation refusée par le navigateur. Vérifiez les autorisations du site.'), ok ? 'ok' : 'bad');
        paint();
      } }, tr('Autoriser plus d’espace'))] : []));
    paintTools();
    if (!items.length) {
      box.replaceChildren(h('div.empty', offline ? tr('Rien n’est téléchargé sur cet appareil.')
        : tr('Aucun téléchargement. Le bouton « Télécharger » d’un film ou d’un épisode permet de le regarder plus tard sans connexion.')));
      return;
    }
    const rows = [];
    for (const d of items) {
      if (!posters.get(d.key)) posters.set(d.key, await posterUrl(d)); // l'affiche arrive au début du téléchargement
      const it = d.item;
      const title = it.type === 'episode' ? it.show : it.title;
      const sub = it.type === 'episode' ? `${episodeCode(it)} · ${it.title}` : String(it.year || '');
      const thumb = posters.get(d.key);
      const playable = d.state === 'done' && !selecting;
      const check = selecting ? h('input.dl-check', { type: 'checkbox', checked: picked.has(d.key), 'aria-label': tr('Sélectionner {title}', { title }),
        onchange: (e) => { if (e.target.checked) picked.add(d.key); else picked.delete(d.key); row.classList.toggle('picked', e.target.checked); paintTools(); } }) : null;
      const row = h('div.dl-row', { class: selecting && picked.has(d.key) ? 'dl-row picked' : 'dl-row',
        onclick: selecting ? (e) => { if (e.target !== check) { check.checked = !check.checked; check.dispatchEvent(new Event('change')); } } : null },
        check,
        h(playable ? 'a.dl-thumb' : 'div.dl-thumb', playable ? { href: `#/offline/${d.rk}`, 'aria-label': tr('Lire {title}', { title }) } : {},
          thumb ? h('img', { src: thumb, alt: '' }) : h('div.noimg', title), playable ? h('span.play-hover', icon('play')) : null),
        h('div.dl-body',
          h('strong', title), h('div.small', sub),
          h('div.muted.small', `${status(d)}${d.size ? ` · ${size(d.size)}` : ''}`),
          d.state === 'done' ? null : progress(d)),
        selecting ? null : h('div.dl-actions',
          playable ? h('a.btn.primary.small', { href: `#/offline/${d.rk}` }, icon('play'), tr('Lire')) : null,
          d.state === 'error' ? h('button.btn.small', { onclick: () => retry(d.key) }, tr('Réessayer')) : null,
          h('button.icon-btn', { title: tr('Supprimer de cet appareil'), 'aria-label': tr('Supprimer {title}', { title }), onclick: async () => {
            if (!await confirmDialog(tr('Supprimer ce téléchargement ?'), tr('{title} {sub} ne sera plus disponible hors ligne sur cet appareil.', { title, sub }), tr('Supprimer'))) return;
            await removeDownload(d.key);
            toast(tr('Téléchargement supprimé'), 'ok');
          } }, icon('trash'))));
      rows.push(row);
    }
    box.replaceChildren(...rows);
  };
  clear(main,
    h('div.page-head', h('h1', tr('Téléchargements'))),
    offline ? h('div.offline-banner', icon('cloud-off'), tr('Hors ligne : seuls les titres téléchargés sur cet appareil sont disponibles.')) : null,
    meter, room, offline ? null : smartPanel(), tools, box);
  await paint();
  const stop = onDownloads(() => { if (!selecting) paint(); else paintTools(); });
  ctx.setCleanup(() => { stop(); posters.forEach((u) => u && URL.revokeObjectURL(u)); });
  if (!offline) pump();
}
