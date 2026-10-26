import { del, get } from '../api.js';
import { clear, episodeCode, fmtDuration, h, icon, img, posterCard, select, spinner, toast } from '../ui.js';

const DAY = { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' };
const MONTHS = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];
const MONTHS_LONG = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'];

// Bilan : temps passé, titres et genres préférés, sur une année ou un mois
async function renderRecap(box, ctx) {
  const years = await get('/api/history/years').catch(() => []);
  if (!ctx.isCurrent() || !years.length) return;
  const yearSel = select(years.map((y) => [String(y), String(y)]), String(years[0]), { 'aria-label': 'Année' });
  const monthSel = select([['0', 'Toute l’année'], ...MONTHS_LONG.map((m, i) => [String(i + 1), m])], '0', { 'aria-label': 'Mois' });
  const body = h('div.recap-body');
  const paint = async () => {
    body.replaceChildren(spinner());
    const r = await get(`/api/history/recap?year=${yearSel.value}&month=${monthSel.value}`).catch(() => null);
    if (!ctx.isCurrent()) return;
    if (!r || r.empty) { body.replaceChildren(h('p.muted', 'Aucun visionnage sur cette période.')); return; }
    const stat = (value, label) => h('div.stat', h('strong', value), h('span', label));
    const maxMonth = Math.max(...(r.months || [0]), 0.1);
    body.replaceChildren(
      h('div.stats', stat(r.minutes < 60 ? `${r.minutes} min` : `${String(r.hours).replace('.', ',')} h`, 'de visionnage'), stat(String(r.films), r.films > 1 ? 'films' : 'film'),
        stat(String(r.episodes), r.episodes > 1 ? 'épisodes' : 'épisode'), stat(String(r.days), r.days > 1 ? 'jours actifs' : 'jour actif'),
        r.streak > 1 ? stat(`${r.streak} jours`, 'd’affilée au maximum') : null,
        r.weekday ? stat(r.weekday, 'jour préféré') : null),
      r.months ? h('div.recap-months', { role: 'img', 'aria-label': 'Heures de visionnage par mois' },
        r.months.map((v, i) => h('div.recap-month', { title: `${MONTHS_LONG[i]} : ${String(v).replace('.', ',')} h` },
          h('div.recap-bar', { style: { height: `${Math.max(2, (v / maxMonth) * 100)}%` } }), h('span', MONTHS[i])))) : null,
      r.genres.length ? h('div.chips.recap-genres', h('span.muted', 'Genres les plus regardés :'), r.genres.map((g) => h('span.chip', g))) : null,
      r.top.length ? h('div', h('h3.sub', 'Les plus regardés'), h('div.grid.recap-top', r.top.map((c) => posterCard(c)))) : null);
  };
  yearSel.addEventListener('change', paint);
  monthSel.addEventListener('change', paint);
  box.replaceChildren(h('div.recap-head', h('h2', 'Bilan'), yearSel, monthSel), body);
  await paint();
}

export async function renderHistory(main, ctx) {
  const list = h('div.history');
  const head = h('div.page-head', h('h1', 'Historique'));
  const stats = h('div.stats');
  const moreBtn = h('button.btn.ghost', { onclick: () => load() }, 'Afficher plus');
  const recapBox = h('section.recap');
  clear(main, head, recapBox, stats, list, h('div.center', moreBtn));
  renderRecap(recapBox, ctx);
  let start = 0, lastDay = null;

  async function load() {
    moreBtn.replaceChildren(spinner());
    const r = await get(`/api/history?start=${start}&size=50`);
    if (!ctx.isCurrent()) return;
    if (start === 0) {
      const hours = Math.round((r.stats.ms || 0) / 3600000);
      const mins = Math.round((r.stats.ms || 0) / 60000);
      stats.replaceChildren(h('div.stat', h('strong', String(r.stats.n)), h('span', r.stats.n > 1 ? 'visionnages' : 'visionnage')),
        h('div.stat', h('strong', mins < 60 ? `${mins} min` : `${hours} h`), h('span', 'de visionnage')));
      if (!r.items.length) list.append(h('div.empty', 'Aucun visionnage pour le moment.'));
    }
    for (const it of r.items) {
      const day = new Date(it.watched_at * 1000).toLocaleDateString('fr-FR', DAY);
      if (day !== lastDay) { list.append(h('h3.history-day', day)); lastDay = day; }
      const thumb = it.type === 'episode' ? (it.thumb || it.show_thumb) : it.thumb;
      const line = h('div.history-item', { class: it.available ? 'history-item' : 'history-item gone' },
        h('a.history-thumb', { href: it.available ? `#/item/${it.id}` : null },
          thumb ? h('img', { src: img(thumb, 160, 90), alt: '', loading: 'lazy' }) : h('div.noimg')),
        h('div.history-body',
          h('strong', it.type === 'episode' ? it.show : it.title),
          h('div.muted', it.type === 'episode' ? `${episodeCode(it)} · ${it.title}` : String(it.year || ''),
            ` · ${new Date(it.watched_at * 1000).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`,
            it.duration ? ` · ${fmtDuration(it.duration)}` : '')),
        h('button.icon-btn', { title: 'Retirer de l’historique', onclick: async () => {
          try { await del(`/api/history/${it.history_id}`); line.remove(); } catch (e) { toast(e.message, 'bad'); }
        } }, icon('trash')));
      list.append(line);
    }
    start += r.items.length;
    moreBtn.replaceChildren('Afficher plus');
    moreBtn.hidden = start >= r.total;
  }
  await load();
}
