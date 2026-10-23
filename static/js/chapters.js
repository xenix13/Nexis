// Sélection de chapitre en vignettes (lecteur et fiche), comme Jellyfin : image, titre, heure de début.
import { fmtTime, h } from './ui.js';

export const chapterTitle = (c, n) => c.title || `Chapitre ${n + 1}`;

export function chapterCards(chapters, { current = -1, onPick, href } = {}) {
  return chapters.map((c, n) => {
    const attrs = {
      class: n === current ? 'chapter-card on' : 'chapter-card',
      title: `${chapterTitle(c, n)} · ${fmtTime(c.start)}`,
      dataset: { n: String(n) },
    };
    const body = [
      h('span.chapter-img', c.image ? h('img', { src: c.image, alt: '', loading: 'lazy',
        onerror: (e) => { e.target.remove(); } }) : null),
      h('span.chapter-name', chapterTitle(c, n)),
      h('span.chapter-time', fmtTime(c.start)),
    ];
    if (href) return h('a', { ...attrs, href: href(c, n) }, body);
    return h('button', { ...attrs, type: 'button', onclick: () => onPick && onPick(c, n) }, body);
  });
}

export function chapterStrip(chapters, opts = {}) {
  const track = h('div.chapter-strip', chapterCards(chapters, opts));
  return track;
}
