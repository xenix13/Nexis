// « Regarder ensemble » côté lecteur : une position partagée par le groupe (voir seance/syncplay.py).
//
// Le serveur diffuse l'état du groupe (« en lecture depuis tel instant à telle position », « en pause », « on
// attend Léa »). Chaque lecteur cale sa vidéo dessus : petit écart rattrapé en accélérant ou ralentissant à
// peine (± 5 %), grand écart par un saut. Les actions locales (lecture, pause, saut, épisode suivant) sont
// envoyées au serveur, qui les répercute à tout le groupe.
import { get, post, put } from '../api.js';
import { avatar, fmtTime, h, toast } from '../ui.js';

const QUIET_MS = 4000;      // après un ordre du groupe, un court chargement est normal : pas d'alerte « coupure »
const DRIFT_EVERY = 1000;   // fréquence du recalage
const JUMP_IF = 2500;       // écart (ms) au-delà duquel on saute à la bonne position
const NUDGE_IF = 200;       // écart (ms) en dessous duquel on ne touche à rien

/** Bouton du lecteur quand on n'est dans aucun groupe. */
export function paintGroupButton(btn, badge, group) {
  btn.classList.toggle('on', !!group);
  badge.hidden = !group;
  if (group) badge.textContent = String((group.members || []).filter((m) => m.online !== false).length || 1);
}

/** Menu « Regarder ensemble » hors groupe : créer un groupe sur la vidéo en cours. */
export function createGroupMenu(opt, { itemId, position, beforeLeave }) {
  return h('div.menu-col.group-col', h('h4', 'Regarder ensemble'),
    h('p.menu-info', 'Crée un groupe : chacun suit la même lecture depuis son écran, les pauses et les sauts sont partagés.'),
    opt('Créer un groupe avec cette vidéo', false, async () => {
      try {
        const g = await post('/api/together', { item_id: itemId, position: position() });
        beforeLeave();
        location.replace(`#/play/${itemId}?group=${g.id}`);
      } catch (e) { toast(e.message, 'bad'); }
    }),
    opt('Voir les groupes en cours', false, () => { location.hash = '#/together'; }));
}

export class WatchTogether {
  /**
   * @param {string} id identifiant du groupe
   * @param {object} host ce que le lecteur prête au groupe :
   *   video, itemId, me (utilisateur), banner, button, badge,
   *   pos() position actuelle (ms), seekTo(ms) saut sans le renvoyer au groupe,
   *   ready() la vidéo est-elle chargée (hors démarrage du flux) ?, seq() numéro du flux en cours,
   *   waitBuffer(seq) -> Promise : réserve suffisante après un saut, paintPlay(),
   *   leaveFor(itemId) : enregistre la progression puis ouvre une autre vidéo du groupe,
   *   onMenuChange() : le menu du groupe est peut-être à redessiner, onEnd() : on n'est plus dans le groupe.
   */
  constructor(id, host) {
    this.id = id;
    this.host = host;
    this.state = null;
    this.members = [];
    this.name = '';
    this.hostOnly = false;
    this.isHost = false;
    this.offset = 0;          // horloge du serveur - la nôtre
    this.es = null;
    this.quietUntil = 0;
    this.readyPending = false;
    this.lastDrift = 0;
  }

  async join() {
    const g = await post(`/api/together/${this.id}/join`);
    Object.assign(this, { name: g.name, state: g, members: g.members, hostOnly: g.host_only, isHost: g.is_host });
    await this.syncClock();
    return g;
  }

  /** Décalage d'horloge avec le serveur, mesuré au plus court des trois allers-retours. */
  async syncClock() {
    let best = null;
    for (let i = 0; i < 3; i++) {
      const t0 = Date.now();
      try {
        const r = await get('/api/together/time');
        const t1 = Date.now();
        if (!best || t1 - t0 < best.rtt) best = { rtt: t1 - t0, offset: r.now - (t0 + t1) / 2 };
      } catch { break; }
    }
    if (best) this.offset = best.offset;
  }

  serverNow() { return Date.now() + this.offset; }

  /** Position (ms) où le groupe en est maintenant. */
  target(st = this.state) {
    return st.position + (st.playing && !st.waiting ? Math.max(0, this.serverNow() - st.ref) : 0);
  }

  /** Le groupe attend quelqu'un alors qu'il devrait jouer. */
  get waitingWhilePlaying() { return !!(this.state && this.state.waiting && this.state.playing); }

  send(action, extra = {}) {
    return post(`/api/together/${this.id}/cmd`, { action, ...extra }).catch((e) => {
      if (e.status === 403 && ['play', 'pause', 'seek'].includes(action)) {
        toast(e.message, 'bad');
        this.apply(); // on revient à la position du groupe
      } else if (e.status === 404) this.end('Le groupe est terminé');
    });
  }

  open() {
    const es = new EventSource(`/api/together/${this.id}/events`);
    this.es = es;
    es.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === 'state') {
        if (this.state && m.seq && this.state.seq && m.seq < this.state.seq) return; // ancien état arrivé en retard
        const prev = this.state;
        this.state = m;
        this.notify(m, prev);
        this.apply(m);
      } else if (m.type === 'members') {
        this.members = m.members;
        this.hostOnly = m.host_only;
        this.name = m.name;
        this.isHost = m.members.some((x) => x.host && x.id === this.host.me.id);
        this.paint();
        this.host.onMenuChange();
      } else if (m.type === 'notice') {
        toast(m.text);
      } else if (m.type === 'closed') {
        this.end('Le groupe est terminé');
      }
    };
    es.onerror = () => { if (es.readyState === EventSource.CLOSED) this.end('Connexion au groupe perdue'); };
  }

  /** Message quand quelqu'un d'autre agit sur la lecture. */
  notify(m, prev) {
    if (!m.by || m.by === this.host.me.display_name) return;
    if (m.action === 'pause') toast(`${m.by} a mis en pause`);
    else if (m.action === 'play') toast(`${m.by} a repris la lecture`);
    else if (m.action === 'seek') toast(`${m.by} a ${prev && m.position < this.target(prev) ? 'reculé' : 'avancé'} jusqu’à ${fmtTime(m.position)}`);
    else if (m.action === 'item' && prev && prev.item_id !== m.item_id) toast(`${m.by} lance une autre vidéo`);
    if (m.notice) toast(m.notice);
  }

  /** Cale la vidéo sur l'état du groupe. */
  apply(st = this.state) {
    const { video } = this.host;
    if (!st) return;
    if (st.item_id && st.item_id !== String(this.host.itemId)) { this.host.leaveFor(st.item_id); return; }
    if (!this.host.ready()) return; // la vidéo se charge encore : on appliquera l'état une fois prête
    const target = this.target(st);
    const meWaited = st.waiting && (st.waiting_ids || []).includes(this.host.me.id);
    this.host.banner.hidden = !st.waiting;
    if (st.waiting) this.host.banner.textContent = st.waiting_for.length ? `En attente de ${st.waiting_for.join(', ')}…` : 'Synchronisation…';
    this.quietUntil = Date.now() + QUIET_MS;
    if (!st.playing || st.waiting) {
      if (!video.paused) video.pause();
      video.playbackRate = 1;
      if (Math.abs(this.host.pos() - target) > 400) this.host.seekTo(target);
      if (meWaited && !this.readyPending) {
        // on recharge à la nouvelle position, puis on prévient le groupe
        this.readyPending = true;
        const seq = this.host.seq();
        this.host.waitBuffer(seq).then(() => {
          this.readyPending = false;
          if (this.es && seq === this.host.seq()) this.send('ready');
        });
      }
    } else {
      if (Math.abs(this.host.pos() - target) > 1500) this.host.seekTo(target);
      if (video.paused) video.play().catch(() => this.host.paintPlay());
    }
    this.host.paintPlay();
  }

  /** Recalage continu pendant la lecture (appelé à chaque timeupdate). */
  drift(p) {
    const st = this.state;
    const { video } = this.host;
    if (!st || !st.playing || st.waiting || video.paused || video.seeking || !this.host.ready()) return;
    const now = Date.now();
    if (now - this.lastDrift < DRIFT_EVERY) return;
    this.lastDrift = now;
    const diff = p - this.target(st);
    if (Math.abs(diff) > JUMP_IF) { video.playbackRate = 1; this.host.seekTo(this.target(st)); return; }
    // petit écart : on accélère ou ralentit à peine, sans saut visible
    video.playbackRate = Math.abs(diff) < NUDGE_IF ? 1 : Math.min(1.05, Math.max(0.95, 1 - diff / 5000));
  }

  paint() { paintGroupButton(this.host.button, this.host.badge, this); }

  /** Colonne du menu du lecteur : membres, invitation, réglage de l'hôte, quitter. */
  menu(opt) {
    const status = (m) => (m.online === false ? 'hors ligne' : (m.ready ? 'prêt' : 'chargement…'));
    return h('div.menu-col.group-col', h('h4', this.name),
      h('div.group-members', (this.members || []).map((m) => h('div.group-member', avatar({ display_name: m.name, avatar_color: m.color }, 26),
        h('span.grow', m.name, m.host ? h('span.tag.accent', 'Hôte') : null), h('span.muted.small', status(m))))),
      this.hostOnly ? h('p.menu-info', 'Seul l’hôte contrôle la lecture.') : null,
      opt('Copier le lien d’invitation', false, async () => {
        const link = `${location.origin}/#/together/${this.id}`;
        try { await navigator.clipboard.writeText(link); toast('Lien copié : envoie-le aux autres membres du foyer', 'ok'); }
        catch { toast(link); }
      }),
      this.isHost ? opt(this.hostOnly ? 'Tout le monde peut contrôler' : 'Seul l’hôte contrôle la lecture', false, async () => {
        try { await put(`/api/together/${this.id}`, { host_only: !this.hostOnly }); } catch (e) { toast(e.message, 'bad'); }
      }) : null,
      opt('Quitter le groupe', false, async () => {
        this.end();
        await post(`/api/together/${this.id}/leave`).catch(() => {});
        history.replaceState(null, '', `#/play/${this.host.itemId}`);
      }));
  }

  /** Fin du mode groupe (le lecteur continue seul). */
  end(msg) {
    if (!this.es) return;
    this.es.close();
    this.es = null;
    this.host.video.playbackRate = 1;
    this.host.banner.hidden = true;
    paintGroupButton(this.host.button, this.host.badge, null);
    if (msg) toast(msg);
    this.host.onEnd();
  }

  /** Le lecteur se ferme : on quitte le groupe, sauf si on enchaîne sur une autre vidéo du même groupe. */
  dispose() {
    if (this.es) this.es.close();
    this.es = null;
    if (!location.hash.includes(`group=${this.id}`)) {
      post(`/api/together/${this.id}/leave`, {}, { keepalive: true }).catch(() => {});
    }
  }
}
