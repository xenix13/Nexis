// « Téléchargements » : ce qui est gardé sur cet appareil pour regarder hors ligne.
import { state } from '../app.js';
import { askPersist, list, onDownloads, posterUrl, pump, removeDownload, retry, supported, usage } from '../offline.js';
import { clear, confirmDialog, episodeCode, fmtDuration, h, icon, toast } from '../ui.js';
import { tr, locale } from '../i18n.js';

const GB = 1024 ** 3;
const size = (b) => (b >= GB ? tr('{n} Go', { n: (b / GB).toLocaleString(locale(), { maximumFractionDigits: 1, minimumFractionDigits: 1 }) }) : tr('{n} Mo', { n: Math.max(1, Math.round(b / 1024 ** 2)) }));

function status(d) {
  if (d.state === 'done') return d.item.watched ? tr('Vu') : (d.item.position ? tr('Commencé · {p1} restantes', { p1: fmtDuration(d.item.duration - d.item.position) }) : tr('Prêt à regarder'));
  if (d.state === 'preparing' && d.waiting) return tr('En pause pendant une lecture sur le serveur');
  if (d.state === 'preparing') return tr(d.convert ? 'Conversion sur le serveur… {p} %' : 'Préparation sur le serveur… {p} %', { p: Math.round((d.prep || 0) * 100) });
  if (d.state === 'downloading') return tr('Téléchargement… {p1} %', { p1: d.chunks ? Math.round((d.got / d.chunks) * 100) : 0 });
  if (d.state === 'paused') return tr('En pause : reprendra avec la connexion');
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
  const meter = h('p.muted.small');
  const room = h('div.dl-room');
  const firefox = /firefox|fxios/i.test(navigator.userAgent);
  const posters = new Map();
  const paint = async () => {
    const items = await list(uid);
    if (!ctx.isCurrent()) return;
    const u = await usage();
    const used = items.reduce((n, d) => n + (d.state === 'done' ? d.size : (d.got || 0) * 8 * 1024 * 1024), 0);
    // Pas de « place restante » : Chrome (depuis la version 144) annonce volontairement « utilisé + 10 Go » pour ne pas
    // révéler la taille du disque, alors que la vraie limite est ~60 % du stockage. Le chiffre tromperait.
    meter.textContent = tr('{length} téléchargement(s) · {p2} sur cet appareil', { length: items.length, p2: size(used) });
    // La limite vient du navigateur, pas du téléphone : Firefox plafonne un site à 10 Go tant qu'on ne l'autorise pas
    // à garder ses données (jusqu'à la moitié de l'appareil ensuite) ; Chrome, Edge et Safari vont jusqu'à ~60 %.
    room.replaceChildren(...(u && !u.persisted ? [
      h('p.small', firefox
        ? tr('Firefox limite chaque site à 10 Go tant que le stockage persistant n’est pas autorisé. Une fois autorisé, Nexis peut utiliser jusqu’à la moitié du stockage de l’appareil, et les téléchargements ne sont plus effacés automatiquement.')
        : tr('Le navigateur peut effacer ces téléchargements s’il manque de place. Autoriser le stockage persistant les protège ; installer l’application sur l’écran d’accueil aide également.')),
      h('button.btn.small', { onclick: async () => {
        const ok = await askPersist();
        toast(ok ? tr('Stockage autorisé : les téléchargements sont gardés') : tr('Autorisation refusée par le navigateur. Vérifiez les autorisations du site.'), ok ? 'ok' : 'bad');
        paint();
      } }, tr('Autoriser plus d’espace'))] : []));
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
      const playable = d.state === 'done';
      rows.push(h('div.dl-row',
        h(playable ? 'a.dl-thumb' : 'div.dl-thumb', playable ? { href: `#/offline/${d.rk}`, 'aria-label': tr('Lire {title}', { title }) } : {},
          thumb ? h('img', { src: thumb, alt: '' }) : h('div.noimg', title), playable ? h('span.play-hover', icon('play')) : null),
        h('div.dl-body',
          h('strong', title), h('div.small', sub),
          h('div.muted.small', `${status(d)}${d.size ? ` · ${size(d.size)}` : ''}`),
          d.state === 'done' ? null : progress(d)),
        h('div.dl-actions',
          playable ? h('a.btn.primary.small', { href: `#/offline/${d.rk}` }, icon('play'), tr('Lire')) : null,
          d.state === 'error' ? h('button.btn.small', { onclick: () => retry(d.key) }, tr('Réessayer')) : null,
          h('button.icon-btn', { title: tr('Supprimer de cet appareil'), 'aria-label': tr('Supprimer {title}', { title }), onclick: async () => {
            if (!await confirmDialog(tr('Supprimer ce téléchargement ?'), tr('{title} {sub} ne sera plus disponible hors ligne sur cet appareil.', { title, sub }), tr('Supprimer'))) return;
            await removeDownload(d.key);
            toast(tr('Téléchargement supprimé'), 'ok');
          } }, icon('trash')))));
    }
    box.replaceChildren(...rows);
  };
  clear(main,
    h('div.page-head', h('h1', tr('Téléchargements'))),
    offline ? h('div.offline-banner', icon('cloud-off'), tr('Hors ligne : seuls les titres téléchargés sur cet appareil sont disponibles.')) : null,
    meter, room, box);
  await paint();
  const stop = onDownloads(() => paint());
  ctx.setCleanup(() => { stop(); posters.forEach((u) => u && URL.revokeObjectURL(u)); });
  if (!offline) pump();
}
