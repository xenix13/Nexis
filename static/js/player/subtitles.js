// Affichage des sous-titres pendant la lecture :
// - ASS / SSA rendus fidèlement par libass (WebAssembly) : positions, polices du fichier, karaoké, panneaux ;
// - PGS (Blu-ray) dessinés par libpgs, comme le fait Jellyfin ;
// - les autres (SRT, WebVTT…) en texte, sur deux calques (bas et haut de l'image).
// Une piste encore en cours d'extraction côté serveur est rechargée régulièrement (« prête jusqu'à … »).
import { get } from '../api.js';
import { h } from '../ui.js';

const REFRESH = 20000;     // rechargement d'une piste encore en cours d'extraction
const MAX_WAIT = 120000;   // attente maximale d'une piste texte avant de lancer la lecture

let JassubClass;
async function jassubClass() {
  if (JassubClass === undefined) {
    try {
      if (typeof WebAssembly !== 'object' || typeof Worker !== 'function') throw new Error('WebAssembly indisponible');
      JassubClass = (await import('/vendor/jassub/jassub.es.js')).default;
    } catch (e) {
      console.warn('[seance] moteur ASS indisponible, sous-titres simplifiés', e.message);
      JassubClass = null;
    }
  }
  return JassubClass;
}

const partialOf = (res) => {
  const v = res.headers.get('X-Seance-Partial');
  return v === null ? null : Number(v);
};

export class SubtitleRenderer {
  /**
   * @param {object} o
   * @param {HTMLVideoElement} o.video
   * @param {HTMLElement} o.bottom calque des répliques en bas de l'image
   * @param {HTMLElement} o.top calque des répliques placées en haut
   * @param {(seq: number) => boolean} o.isCurrent ce chargement concerne-t-il encore le flux affiché ?
   */
  constructor({ video, bottom, top, isCurrent }) {
    Object.assign(this, { video, bottom, top, isCurrent });
    this.cues = [];
    this.cueKey = '';
    this.cueMax = 0;
    this.jassub = null;
    this.jassubKey = '';
    this.pgs = null;
    this.pgsKey = '';
    this.until = null;      // instant (ms) jusqu'où la piste est prête ; null = complète
    this.refresh = null;
    this.offset = 0;
  }

  /** Charge les sous-titres choisis pour ce flux ; résolu quand la lecture peut démarrer. */
  async load(session, seq) {
    clearTimeout(this.refresh);
    this.until = null;
    this.cues = [];
    this.cueKey = '';
    this.bottom.replaceChildren();
    this.top.replaceChildren();
    if (session.subtitle_pgs) { await this.loadPgs(session.subtitle_pgs, seq); this.dropJassub(); return; }
    this.dropPgs();
    if (session.subtitle_ass && await this.loadAss(session.subtitle_ass, seq)) return;
    if (!this.isCurrent(seq)) return;
    if (!session.subtitle_ass) this.dropJassub();
    if (session.subtitle_overlay) await this.loadText(session.subtitle_overlay, seq);
  }

  /** Recale le moteur ASS (décalage du flux en millisecondes). */
  sync(offset = this.offset) {
    this.offset = offset;
    if (this.jassub) this.jassub.timeOffset = offset / 1000;
  }

  /** Affiche les répliques texte de l'instant p (ms). */
  paint(p) {
    const cues = this.cues;
    if (!cues.length) return;
    let lo = 0, hi = cues.length - 1, found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (cues[mid].s <= p) { found = mid; lo = mid + 1; } else hi = mid - 1;
    }
    // toutes les répliques affichées en même temps (dialogue + panneau, deux personnages…)
    const active = [];
    for (let i = found; i >= 0 && p - cues[i].s <= this.cueMax && active.length < 8; i--) if (p <= cues[i].e) active.unshift(i);
    const key = active.join(',');
    if (key === this.cueKey) return;
    this.cueKey = key;
    const line = (i) => h('div', h('span.sub-line', cues[i].t));
    this.bottom.replaceChildren(...active.filter((i) => !cues[i].top).map(line));
    this.top.replaceChildren(...active.filter((i) => cues[i].top).map(line));
  }

  destroy() {
    clearTimeout(this.refresh);
    this.dropJassub();
    this.dropPgs();
  }

  // ------------------------------------------------------------ interne
  again(seq, fn) {
    clearTimeout(this.refresh);
    this.refresh = setTimeout(() => { if (this.isCurrent(seq)) fn(seq); }, REFRESH);
  }

  dropJassub() {
    if (this.jassub) { try { this.jassub.destroy(); } catch { /* déjà détruit */ } }
    this.jassub = null;
    this.jassubKey = '';
  }

  async loadAss(url, seq) {
    const refresh = async (s) => {
      try {
        const res = await fetch(url, { credentials: 'same-origin' });
        if (!res.ok || !this.isCurrent(s) || !this.jassub) return;
        this.jassub.setTrack(await res.text());
        this.until = partialOf(res);
      } catch { /* on réessaie au prochain tour */ }
      if (this.until !== null) this.again(s, refresh);
    };
    if (this.jassub && this.jassubKey === url) {
      this.sync();
      if (this.until !== null) this.again(seq, refresh);
      return true;
    }
    this.dropJassub();
    const J = await jassubClass();
    if (!J || !this.isCurrent(seq)) return false;
    try {
      const [res, fonts] = await Promise.all([fetch(url, { credentials: 'same-origin' }),
        get(`${url.replace(/\/ass$/, '')}/fonts`).catch(() => ({ fonts: [] }))]);
      if (!res.ok) throw new Error(`script ASS ${res.status}`);
      const content = await res.text();
      const until = partialOf(res);
      if (!this.isCurrent(seq)) return false;
      const inst = new J({
        video: this.video, subContent: content, timeOffset: this.offset / 1000,
        workerUrl: '/vendor/jassub/jassub-worker.js', wasmUrl: '/vendor/jassub/jassub-worker.wasm',
        modernWasmUrl: '/vendor/jassub/jassub-worker-modern.wasm',
        availableFonts: { 'liberation sans': '/vendor/jassub/default.woff2' }, fallbackFont: 'liberation sans',
        fonts: fonts.fonts || [], useLocalFonts: false, prescaleHeightLimit: 1080, maxRenderHeight: 2160,
      });
      const ok = await new Promise((resolve) => {
        const t = setTimeout(() => resolve(false), 15000);
        inst.addEventListener('ready', () => { clearTimeout(t); resolve(true); }, { once: true });
        inst.addEventListener('error', (e) => { clearTimeout(t); console.warn('[seance] libass', e.error || e.message); resolve(false); }, { once: true });
      });
      if (!ok || !this.isCurrent(seq)) { try { inst.destroy(); } catch { /* */ } return false; }
      this.jassub = inst;
      this.jassubKey = url;
      this.sync();
      this.until = until;
      if (until !== null) this.again(seq, refresh);
      return true;
    } catch (e) {
      console.warn('[seance] sous-titres ASS', e.message);
      return false;
    }
  }

  dropPgs() {
    if (this.pgs) { try { this.pgs.dispose(); } catch { /* */ } if (this.pgs.ownCanvas) this.pgs.ownCanvas.remove(); }
    this.pgs = null;
    this.pgsKey = '';
  }

  async loadPgs(url, seq) {
    const fetchSup = async () => {
      const res = await fetch(url, { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`PGS ${res.status}`);
      return { buf: await res.arrayBuffer(), until: partialOf(res) };
    };
    const refresh = async (s) => {
      try {
        const { buf, until } = await fetchSup();
        if (!this.isCurrent(s) || !this.pgs) return;
        await this.pgs.loadFromBuffer(buf);
        this.until = until;
      } catch { /* prochain tour */ }
      if (this.until !== null) this.again(s, refresh);
    };
    if (this.pgs && this.pgsKey === url) {
      if (this.until !== null) this.again(seq, refresh);
      return true;
    }
    this.dropPgs();
    try {
      const [{ PgsRenderer }, data] = await Promise.all([import('/vendor/libpgs/libpgs.js'), fetchSup()]);
      if (!this.isCurrent(seq)) return false;
      const canvas = h('canvas.pgs-layer');
      this.video.after(canvas);  // sous les commandes, au-dessus de l'image
      const r = new PgsRenderer({ video: this.video, canvas, workerUrl: '/vendor/libpgs/libpgs.worker.js', aspectRatio: 'contain' });
      r.ownCanvas = canvas;
      this.pgs = r;
      this.pgsKey = url;
      await Promise.race([r.loadFromBuffer(data.buf), new Promise((res) => setTimeout(res, 20000))]);
      this.until = data.until;
      if (data.until !== null) this.again(seq, refresh);
      return true;
    } catch (e) {
      console.warn('[seance] sous-titres PGS', e.message);
      this.dropPgs();
      return false;
    }
  }

  // Pistes texte : normalement déjà prêtes (préparation avant la lecture) ; filet de sécurité pour les externes.
  loadText(url, seq) {
    return new Promise((resolve) => {
      let released = false;
      const release = () => { if (!released) { released = true; resolve(); } };
      const timer = setTimeout(release, MAX_WAIT);
      const poll = async (s = seq) => {
        if (!this.isCurrent(s)) { clearTimeout(timer); release(); return; }
        try {
          const r = await get(url);
          if (!this.isCurrent(s)) { clearTimeout(timer); release(); return; }
          if (r && r.pending) { setTimeout(() => poll(s), 800); return; }
          this.cues = r.cues || [];
          this.cueKey = '';
          this.cueMax = this.cues.reduce((m, c) => Math.max(m, c.e - c.s), 0);
          this.until = r.partial ? r.until : null;
          if (r.partial) this.again(s, poll);  // la suite arrive pendant la lecture
        } catch (e) {
          console.warn('[seance] sous-titres', e.message);
        }
        clearTimeout(timer);
        release();
      };
      poll();
    });
  }
}
