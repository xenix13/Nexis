import { get, post } from '../api.js';
import { playAudio } from '../audio.js';
import { clear, fmtDuration, fmtTime, h, icon, img, select, store, toast } from '../ui.js';
import { plural, tr } from '../i18n.js';

// Livres audio : la bibliothèque (couvertures, « En cours »), et la fiche d'un livre (chapitres, reprise).
const cover = (b, size) => (b.thumb ? img(b.thumb, size, size) : '');

function bookTile(b) {
  const pct = b.position && b.duration ? Math.min(100, (b.position / b.duration) * 100) : 0;
  return h('a.book-tile', { href: `#/book/${b.id}`, title: b.title },
    h('div.book-cover', b.thumb ? h('img', { src: cover(b, 240), alt: '', loading: 'lazy', decoding: 'async' }) : h('span', icon('headphones')),
      pct ? h('div.book-progress', h('div', { style: `width:${pct}%` })) : null,
      b.finished ? h('span.book-done', '✓') : null),
    h('div.book-title', b.title), h('div.muted.small', b.author || ' '));
}

export function bookAudio(d) {
  return { kind: 'book', id: d.id, title: d.title, subtitle: d.author, cover: d.thumb ? img(d.thumb, 512, 512) : '',
    files: d.files.map((f) => ({ url: f.url, duration: f.duration })), chapters: d.chapter_list, duration: d.duration,
    position: d.finished ? 0 : d.position || 0, progressUrl: `/api/books/${d.id}/progress` };
}

export async function renderBooks(main, lib, ctx) {
  const sort = store(`booksort:${lib.id}`) || 'title';
  const sortSel = select([['title', tr('Titre')], ['author', tr('Auteur')], ['added', tr('Ajout récent')], ['year', tr('Année')]], sort,
    { onchange: () => { store(`booksort:${lib.id}`, sortSel.value); renderBooks(main, lib, ctx); } });
  const r = await get(`/api/libraries/${lib.id}/books?sort=${sort}`);
  if (!ctx.isCurrent()) return;
  document.title = `${lib.title} · Nexis`;
  clear(main, h('div.page-head', h('h1', lib.title), h('span.muted', plural(r.books.length, '{n} livre', '{n} livres')),
    h('div.filters', sortSel)),
  r.reading.length ? h('section.block', h('h2', tr('En cours')), h('div.book-grid', r.reading.map(bookTile))) : null,
  r.books.length ? h('div.book-grid', r.books.map(bookTile))
    : h('div.empty', tr('Aucun livre audio : un dossier par livre (Auteur/Titre/fichiers audio), ou un fichier .m4b.')));
}

export async function renderBook(main, id, ctx) {
  const d = await get(`/api/books/${id}`);
  if (!ctx.isCurrent()) return;
  document.title = `${d.title} · Nexis`;
  const long = d.duration >= 3600000;
  const resume = d.position && !d.finished;
  const play = (at = null) => playAudio(bookAudio(d), at);
  const chapters = d.chapter_list.length > 1 ? h('section.block', h('h2', plural(d.chapter_list.length, '{n} chapitre', '{n} chapitres')),
    h('ol.chapter-list', d.chapter_list.map((c) => h('li', h('button.chapter-row', { type: 'button', onclick: () => play(c.s + 1) },
      h('span', c.t), h('span.muted', fmtTime(c.e - c.s, long))))))) : null;
  clear(main, h('div.book-detail',
    h('div.book-cover.big', d.thumb ? h('img', { src: cover(d, 480), alt: '' }) : h('span', icon('headphones'))),
    h('div.book-info',
      h('h1', d.title),
      d.author ? h('p.book-author', d.author) : null,
      h('p.muted', [d.narrator ? tr('Lu par {name}', { name: d.narrator }) : null, d.year, fmtDuration(d.duration), d.genre].filter(Boolean).join(' · ')),
      resume ? h('p.small', tr('Arrêté à {at} sur {total}', { at: fmtTime(d.position, long), total: fmtTime(d.duration, long) })) : null,
      h('div.detail-actions',
        h('button.btn.primary', { onclick: () => play() }, icon('play'), resume ? tr('Reprendre') : tr('Écouter')),
        resume || d.finished ? h('button.btn', { onclick: async () => {
          await post(`/api/books/${d.id}/progress`, { position: 0, restart: true });
          d.position = 0; d.finished = false;
          play(0);
        } }, tr('Recommencer')) : null,
        !d.finished ? h('button.btn', { onclick: async () => {
          await post(`/api/books/${d.id}/progress`, { position: d.duration, finished: true });
          toast(tr('Marqué comme terminé'), 'ok');
          renderBook(main, id, ctx);
        } }, tr('Marquer comme terminé')) : null),
      d.summary ? h('p.book-summary', d.summary) : null)),
  chapters);
}
