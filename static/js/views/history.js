import { del, get } from '../api.js';
import { clear, episodeCode, fmtDuration, h, icon, img, spinner, toast } from '../ui.js';

const DAY = { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' };

export async function renderHistory(main, ctx) {
  const list = h('div.history');
  const head = h('div.page-head', h('h1', 'Historique'));
  const stats = h('div.stats');
  const moreBtn = h('button.btn.ghost', { onclick: () => load() }, 'Afficher plus');
  clear(main, head, stats, list, h('div.center', moreBtn));
  let start = 0, lastDay = null;

  async function load() {
    moreBtn.replaceChildren(spinner());
    const r = await get(`/api/history?start=${start}&size=50`);
    if (!ctx.isCurrent()) return;
    if (start === 0) {
      const hours = Math.round((r.stats.ms || 0) / 3600000);
      stats.replaceChildren(h('div.stat', h('strong', String(r.stats.n)), h('span', 'visionnages')),
        h('div.stat', h('strong', `${hours} h`), h('span', 'de visionnage')));
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
