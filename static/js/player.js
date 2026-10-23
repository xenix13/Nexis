// Web player: HLS stream proxied through Séance, custom controls, resume,
// per-user track preferences, "skip intro" banner and next-episode countdown.
import { get, post, put } from './api.js';
import { SIGNS_HINT, fixForced, menuSubs, pickAudio, pickSubtitle, trackPayload } from './tracks.js';
import { state } from './app.js';
import { chapterStrip, chapterTitle } from './chapters.js';
import { browserCaps } from './player/caps.js';
import { SubtitleRenderer } from './player/subtitles.js';
import { WatchTogether, createGroupMenu, paintGroupButton } from './player/together.js';
import { clear, episodeCode, errorBox, fmtTime, h, icon, img, spinner, store, toast } from './ui.js';

const REPORT_EVERY = 10000;
const HIDE_AFTER = 3000;
// Secondes de vidéo à avoir en réserve avant de démarrer / de reprendre après une coupure.
// Une conversion à la volée produit à peine plus vite que le temps réel : on prend de l'avance.
const PREROLL = { direct: 5, copy: 12, transcode: 20 };
const PREROLL_SEEK = { direct: 3, copy: 6, transcode: 10 }; // après un saut : réserve plus courte
const REBUFFER = { direct: 6, copy: 15, transcode: 25 };
const PREPARE_MAX = 10 * 60 * 1000;


export async function renderPlayer(root, rk, ctx) {
  const prefs = state.me.prefs;
  const isAdmin = state.me.user.is_admin;
  const video = h('video.video', { playsinline: true, preload: 'auto' });
  const subLayer = h('div.sub-layer', { style: { fontSize: `${(prefs.sub_size / 100) * 2.4}vmin` } });
  const subTop = h('div.sub-layer.sub-top', { style: { fontSize: `${(prefs.sub_size / 100) * 2.4}vmin` } });
  const subsPending = h('div.subs-pending', { hidden: true }, 'Sous-titres en préparation…');
  const loader = h('div.player-loader', spinner());
  const errorLayer = h('div.player-error', { hidden: true });
  const titleEl = h('div.player-title');
  const timeEl = h('span.time');
  const played = h('div.tl-played');
  const buffered = h('div.tl-buffered');
  const introMark = h('div.tl-intro', { hidden: true });
  const previewImg = h('div.tl-preview-img', { hidden: true });
  const previewChapter = h('div.tl-preview-chapter');
  const previewTime = h('div.tl-preview-time');
  const hoverTip = h('div.tl-tip', { hidden: true }, previewImg, previewChapter, previewTime);
  const chapterMarks = h('div.tl-chapters');
  const timeline = h('div.timeline', { role: 'slider', 'aria-label': 'Position', tabindex: 0 },
    h('div.tl-track', buffered, introMark, chapterMarks, played, h('div.tl-thumb')), hoverTip);
  const playBtn = h('button.ctl', { 'aria-label': 'Lecture/Pause' }, icon('play'));
  const muteBtn = h('button.ctl', { 'aria-label': 'Couper le son' }, icon('volume'));
  const volume = h('input.volume', { type: 'range', min: 0, max: 1, step: 0.05, 'aria-label': 'Volume' });
  const nextBtn = h('button.ctl', { 'aria-label': 'Épisode suivant', hidden: true }, icon('next'));
  const subsBtn = h('button.ctl', { 'aria-label': 'Audio et sous-titres' }, icon('subs'));
  const chaptersBtn = h('button.ctl', { 'aria-label': 'Chapitres', title: 'Chapitres (C)', hidden: true }, icon('chapters'));
  const groupCount = h('span.ctl-badge', { hidden: true });
  const groupBtn = h('button.ctl', { 'aria-label': 'Regarder ensemble', title: 'Regarder ensemble' }, icon('group'), groupCount);
  const cogBtn = h('button.ctl', { 'aria-label': 'Qualité' }, icon('cog'));
  const fullBtn = h('button.ctl', { 'aria-label': 'Plein écran' }, icon('full'));
  const menu = h('div.player-menu', { hidden: true });
  const chapterPanel = h('div.chapter-panel', { hidden: true });
  const groupBanner = h('div.group-banner', { hidden: true });
  const skipBtn = h('button.skip-btn', { hidden: true }, icon('skip'), 'Passer l’intro');
  const nextCard = h('div.next-card', { hidden: true });
  const backBtn = h('button.ctl.back', { 'aria-label': 'Retour' }, icon('back'));

  const controls = h('div.controls',
    timeline,
    h('div.ctl-row',
      playBtn,
      h('button.ctl', { 'aria-label': 'Reculer de 10 secondes', onclick: () => seekBy(-10000) }, icon('rewind')),
      h('button.ctl', { 'aria-label': 'Avancer de 30 secondes', onclick: () => seekBy(30000) }, icon('forward')),
      h('div.vol', muteBtn, volume), timeEl, h('div.grow'), nextBtn, groupBtn, chaptersBtn, subsBtn, cogBtn, fullBtn));
  const top = h('div.player-top', backBtn, titleEl);
  const player = h('div.player', video, subLayer, subTop, subsPending, loader, errorLayer, top, groupBanner, skipBtn, nextCard,
    menu, chapterPanel, controls);
  clear(root, player);

  // ------------------------------------------------------------ state
  let item, session = null, hls = null, base = 0;
  let audioSel = null, subSel = null, quality = prefs.quality;
  let lastReport = 0, introSkipped = false, nextTimer = null, nextDismissed = false, destroyed = false;
  let idleTimer = null, scrubbing = false, startedAt = 0;
  let switching = true, finished = false; // no progress reports while a stream is (re)starting or after the end
  let rebuffering = false, stalls = [], lowerHinted = false, everPlayed = false;

  // « Regarder ensemble » : position partagée par le groupe (voir seance/syncplay.py)
  const groupId = ctx.query.get('group');
  let group = null;

  let startSeq = 0, autoRetries = 0, forceHls = false;
  const subs = new SubtitleRenderer({ video, bottom: subLayer, top: subTop, isCurrent: (seq) => seq === startSeq && !destroyed });

  const duration = () => (session && session.duration) || (item && item.duration) || 0;
  const pos = () => base + Math.round((video.currentTime || 0) * 1000);

  // ------------------------------------------------------------ boot
  try {
    item = await get(`/api/items/${rk}`);
  } catch (e) {
    clear(root, h('div.center-page', errorBox(e, () => history.back())));
    return () => {};
  }
  if (destroyed || !ctx.isCurrent()) return () => {};
  if (!['movie', 'episode', 'extra'].includes(item.type)) { location.replace(`#/item/${rk}`); return () => {}; }
  document.title = `${item.type === 'episode' ? `${item.show} ${episodeCode(item)}` : item.title} · Séance`;
  titleEl.append(h('strong', item.type === 'episode' ? item.show : item.title),
    item.type === 'episode' ? h('span', `${episodeCode(item)} · ${item.title}`) : (item.year ? h('span', String(item.year)) : null));
  const poster = item.type === 'episode' ? (item.show_art || item.art) : item.art;
  if (poster) video.poster = img(poster, 1280, 720);

  audioSel = pickAudio(item.audio || [], prefs, item.track_prefs);
  subSel = pickSubtitle(item.subtitles || [], audioSel, prefs, item.track_prefs);
  // choix faits sur la fiche juste avant de lancer
  const qa = ctx.query.get('a'), qs = ctx.query.get('s');
  if (qa !== null) audioSel = (item.audio || []).find((a) => String(a.id) === qa) || audioSel;
  if (qs !== null) subSel = qs === '0' ? null : ((item.subtitles || []).find((x) => String(x.id) === qs) || subSel);
  subSel = fixForced(subSel, audioSel, item.subtitles || []);
  const chapters = (item.chapters || []).filter((c) => c.end > c.start);
  let trick = null;
  const paintChapters = () => {
    const d = duration();
    chapterMarks.replaceChildren(...(d ? chapters.filter((c) => c.start > 0).map((c) =>
      h('span.tl-chapter', { style: { left: `${(c.start / d) * 100}%` } })) : []));
  };
  chaptersBtn.hidden = chapters.length < 2;
  paintChapters();
  const loadTrick = async (again) => {
    try {
      const r = await get(`/api/items/${item.id}/trickplay`);
      if (r.ready) trick = r;
      else if (again && !destroyed) setTimeout(() => loadTrick(false), 60000);
    } catch { /* pas d'aperçu */ }
  };
  loadTrick(true);
  const rememberTracks = () => put(`/api/items/${item.id}/tracks`, trackPayload(audioSel, subSel)).catch(() => {});
  let startAt = 0;
  if (ctx.query.get('t')) startAt = Number(ctx.query.get('t')) || 0;
  else if (ctx.query.get('resume') && item.position && !item.watched) startAt = item.position;
  if (groupId) {
    group = new WatchTogether(groupId, {
      video, itemId: item.id, me: state.me.user, banner: groupBanner, button: groupBtn, badge: groupCount,
      pos, seekTo: (ms) => seekTo(ms, true), ready: () => !!session && !switching, seq: () => startSeq,
      waitBuffer: (seq) => waitBuffer(PREROLL_SEEK[session.mode] ?? 6, 30000, seq), paintPlay,
      leaveFor: (id) => { finished = true; report('stopped', true); location.replace(`#/play/${id}?group=${groupId}`); },
      onMenuChange: () => { if (!menu.hidden && menu.dataset.kind === 'group') { menu.hidden = true; openMenu('group'); } },
      onEnd: () => { group = null; },
    });
    try {
      const g = await group.join();
      if (destroyed || !ctx.isCurrent()) return () => {};
      if (g.item_id && g.item_id !== String(item.id)) { location.replace(`#/play/${g.item_id}?group=${groupId}`); return () => {}; }
      if (g.item_id) startAt = group.target(g);
      group.open();
    } catch (e) {
      toast(e.message, 'bad');
      group = null;
    }
  }
  paintGroupButton(groupBtn, groupCount, group);

  const savedVol = Number(store('seance.volume'));
  video.volume = Number.isFinite(savedVol) && store('seance.volume') !== null ? savedVol : 1;
  video.muted = store('seance.muted') === '1';
  volume.value = video.volume;

  // ------------------------------------------------------------ streaming
  // The server can take a while to produce the first segment after a jump (CPU transcode,
  // burned-in ASS subtitles…): be much more patient than hls.js defaults.
  const slow = (firstByte, total, retries) => ({ default: {
    maxTimeToFirstByteMs: firstByte, maxLoadTimeMs: total,
    timeoutRetry: { maxNumRetry: retries, retryDelayMs: 1000, maxRetryDelayMs: 4000 },
    errorRetry: { maxNumRetry: retries, retryDelayMs: 1000, maxRetryDelayMs: 8000 },
  } });
  // Flux « à la Jellyfin » : playlist VOD complète, horodatages d'origine -> currentTime = position réelle.
  const HLS_CONFIG = {
    maxBufferLength: 60, maxMaxBufferLength: 120, backBufferLength: 90,
    manifestLoadPolicy: slow(60000, 90000, 2),
    playlistLoadPolicy: slow(60000, 90000, 4),
    fragLoadPolicy: slow(60000, 120000, 6),
  };

  async function start(offset) {
    const seq = ++startSeq;
    switching = true;
    finished = false;
    loader.hidden = false;
    errorLayer.hidden = true;
    const previous = session && session.stream;
    if (hls) { hls.destroy(); hls = null; }
    if (video.getAttribute('src')) { video.removeAttribute('src'); video.load(); }
    let s;
    try {
      s = await post('/api/play', { id: item.id, offset: Math.round(offset), quality, no_direct: forceHls || undefined,
        caps: browserCaps(),
        playlist: ctx.query.get('playlist') || undefined,
        audio: audioSel ? audioSel.id : null, subtitle: subSel ? subSel.id : 0, replace: previous || undefined });
    } catch (e) { if (seq === startSeq) showError(e.message, true); return; }
    if (destroyed || seq !== startSeq) {
      // a newer seek (or leaving the player) superseded this one
      post('/api/stop', { stream: s.stream }).catch(() => {});
      return;
    }
    session = s;
    base = 0;
    subs.sync(base);
    introSkipped = false;
    nextBtn.hidden = !session.next;
    updateIntroMark();
    // la lecture ne démarre qu'une fois les sous-titres prêts (pas de trou au début de l'épisode)
    const subsReady = subs.load(session, seq);
    let autoplayed = false;
    const autoplay = async () => {
      if (autoplayed) return;
      autoplayed = true;
      await subsReady;
      if (seq !== startSeq || destroyed) return;
      // on laisse le tampon se remplir avant de démarrer : pas de coupure dans les premières secondes
      await waitBuffer((everPlayed ? PREROLL_SEEK : PREROLL)[session.mode] ?? 12, 90000, seq);
      if (seq !== startSeq || destroyed) return;
      if (group) {
        // en groupe : on signale qu'on est prêt, la lecture part quand tout le monde l'est
        loader.hidden = true;
        switching = false;
        group.send('ready');
        group.apply();
        return;
      }
      video.play().catch(() => { loader.hidden = true; paintPlay(); });
    };
    if (session.mode === 'direct') {
      // le navigateur lit le fichier tel quel
      video.src = session.url;
      video.addEventListener('loadedmetadata', () => {
        if (session.offset) video.currentTime = session.offset / 1000;
        autoplay();
      }, { once: true });
      video.addEventListener('error', () => {
        if (seq === startSeq && session.mode === 'direct' && !destroyed) {
          // format refusé par ce navigateur : on bascule sur un flux converti
          forceHls = true;
          start(Math.max(session.offset, pos()));
        }
      }, { once: true });
    } else if (window.Hls && window.Hls.isSupported()) {
      hls = new window.Hls({ ...HLS_CONFIG, startPosition: session.offset / 1000 });
      hls.on(window.Hls.Events.ERROR, (_, data) => {
        if (!data.fatal || seq !== startSeq) return;
        if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) { hls.recoverMediaError(); return; }
        console.warn('[seance] erreur HLS', data.type, data.details, data.response && data.response.code);
        const at = pos() || session.offset;
        if (autoRetries < 2) {
          autoRetries += 1;
          toast('Flux interrompu, reconnexion…');
          start(at);
          return;
        }
        const code = data.response && data.response.code ? ` ${data.response.code}` : '';
        showError(`Le flux a été interrompu (${data.details}${code}).`, true, at);
      });
      hls.on(window.Hls.Events.MANIFEST_PARSED, autoplay);
      hls.on(window.Hls.Events.FRAG_BUFFERED, () => { autoRetries = 0; });
      hls.loadSource(session.url);
      hls.attachMedia(video);
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = session.url;  // Safari : HLS natif
      video.addEventListener('loadedmetadata', () => {
        if (session.offset) video.currentTime = session.offset / 1000;
        autoplay();
      }, { once: true });
    } else {
      showError('Ce navigateur ne lit pas le HLS et hls.js est absent (static/vendor/hls.min.js). Relance le script d’installation.');
      return;
    }
    startedAt = Date.now();
    setupMediaSession();
  }

  // ---------------------------------------------------------- tampon vidéo
  const aheadSec = () => {
    const b = video.buffered, t = video.currentTime;
    for (let i = 0; i < b.length; i++) {
      if (t >= b.start(i) - 0.3 && t <= b.end(i)) return b.end(i) - t;
      // tout début d'un flux : la première plage peut commencer un peu après la position
      if (b.start(i) >= t && b.start(i) - t < 1.5) return b.end(i) - t;
    }
    return 0;
  };
  const nearEnd = () => { const d = duration(); return d > 0 && pos() + aheadSec() * 1000 >= d - 5000; };
  function waitBuffer(target, maxMs, seq) {
    return new Promise((resolve) => {
      const t0 = Date.now();
      const tick = () => {
        if (destroyed || seq !== startSeq) { resolve(false); return; }
        const enough = aheadSec() >= target || nearEnd()
          || (session && session.mode === 'direct' && video.readyState >= 4 && aheadSec() >= Math.min(target, 2));
        if (enough || Date.now() - t0 > maxMs) { resolve(true); return; }
        setTimeout(tick, 250);
      };
      tick();
    });
  }

  // Coupure en cours de lecture : on met en pause le temps de refaire une vraie réserve,
  // plutôt que de repartir dès la première seconde reçue et de recouper aussitôt.
  let stallTimer = null;
  function onStall() {
    if (switching || video.seeking || rebuffering || finished || destroyed || !session || video.paused) return;
    if (group) {
      // en groupe, une coupure met tout le monde en attente : on ne la signale que si elle dure
      if (Date.now() < group.quietUntil || stallTimer) return;
      stallTimer = setTimeout(() => {
        stallTimer = null;
        if (!group || video.paused || video.readyState >= 3 || destroyed) return;
        rebufferNow();
      }, 2000);
      return;
    }
    rebufferNow();
  }
  function rebufferNow() {
    rebuffering = true;
    const seq = startSeq;
    const now = Date.now();
    stalls = stalls.filter((t) => now - t < 180000);
    stalls.push(now);
    video.pause();
    paintPlay();
    if (group) group.send('buffering');
    waitBuffer(REBUFFER[session.mode] ?? 15, 45000, seq).then(() => {
      if (!rebuffering) return; // l'utilisateur a repris la main entre-temps
      rebuffering = false;
      if (seq !== startSeq || destroyed) return;
      if (group) { paintPlay(); group.send('ready'); return; } // le groupe repart quand tout le monde est prêt
      video.play().catch(() => paintPlay());
    });
    if (stalls.length >= 2 && ['original', '1080p'].includes(quality) && !lowerHinted) {
      lowerHinted = true;
      suggestLowerQuality(session.mode === 'transcode');
    }
  }

  function suggestLowerQuality(cpu) {
    const box = h('div.player-hint',
      h('span', cpu ? 'Le serveur n’arrive pas à convertir cette vidéo assez vite.'
        : 'La connexion n’arrive pas à suivre le débit de ce fichier.'),
      h('button.btn.primary.small', { onclick: () => { box.remove(); quality = '720p'; start(pos()); } }, 'Passer en 720p'),
      h('button.icon-btn', { 'aria-label': 'Fermer', onclick: () => box.remove() }, icon('close')));
    player.append(box);
    setTimeout(() => box.remove(), 20000);
  }

  // Avant la toute première lecture : toutes les pistes de sous-titres sont extraites du fichier
  // (une seule lecture, à pleine vitesse, sans flux vidéo en concurrence sur le partage réseau).
  async function prepare(at = 0) {
    const t0 = Date.now();
    let hint = null;
    while (!destroyed && ctx.isCurrent()) {
      let r;
      // prêt dès que les sous-titres couvrent les premières minutes à regarder : la suite arrive pendant la lecture
      try { r = await get(`/api/items/${item.id}/prepare?wait=10&at=${Math.round(at)}`); } catch (e) {
        console.warn('[seance] préparation', e.message);
        break;
      }
      if (r.ready) break;
      if (!hint && Date.now() - t0 > 4000) { hint = h('div.loader-hint', 'Préparation de la lecture…'); loader.append(hint); }
      if (Date.now() - t0 > PREPARE_MAX) break;
    }
    if (hint) hint.remove();
  }

  function showError(msg, retry = false, at = null) {
    loader.hidden = true;
    errorLayer.hidden = false;
    clear(errorLayer, h('div.error-box', h('p', msg),
      h('div.actions-row',
        retry ? h('button.btn.primary', { onclick: () => { autoRetries = 0; start(at ?? pos()); } }, 'Réessayer') : null,
        h('button.btn', { onclick: goBack }, 'Retour'))));
  }

  function seekTo(ms, remote = false) {
    const d = duration();
    ms = Math.max(0, d ? Math.min(ms, d - 3000) : ms);
    if (group && !remote) group.send('seek', { position: Math.round(ms) });
    // la playlist couvre toute la vidéo : le lecteur va chercher les bons segments, le serveur relance
    // ffmpeg au bon endroit si besoin (comme Jellyfin) — plus besoin de recréer le flux
    video.currentTime = ms / 1000;
  }
  const seekBy = (delta) => seekTo(pos() + delta);

  // ------------------------------------------------------------ progress
  function report(st, force = false, extra = {}) {
    if (!session || !item || destroyed || switching || finished) return;
    const now = Date.now();
    if (!force && now - lastReport < REPORT_EVERY) return;
    lastReport = now;
    const p = pos();
    if (p < 1000 && st !== 'stopped' && !extra.ended) return;
    post('/api/progress', { id: item.id, position: p, duration: duration(), stream: session.stream, state: st, ...extra },
      { keepalive: st === 'stopped' }).catch(() => {});
  }

  // ------------------------------------------------------------ UI updates
  function paintPlay() {
    playBtn.replaceChildren(icon(video.paused && !rebuffering ? 'play' : 'pause'));
    player.classList.toggle('paused', video.paused && !rebuffering);
    if (rebuffering) loader.hidden = false;
  }

  function paintTime() {
    const d = duration(), p = pos();
    timeEl.textContent = `${fmtTime(p, d >= 3600000)} / ${fmtTime(d, d >= 3600000)}`;
    if (!scrubbing && d) played.style.width = `${Math.min(100, (p / d) * 100)}%`;
    const b = video.buffered;
    if (b.length && d) {
      const end = base + b.end(b.length - 1) * 1000;
      const startB = base + b.start(0) * 1000;
      buffered.style.left = `${(startB / d) * 100}%`;
      buffered.style.width = `${Math.max(0, ((end - startB) / d) * 100)}%`;
    }
    timeline.setAttribute('aria-valuenow', String(Math.round(p / 1000)));
  }

  function updateIntroMark() {
    const intro = session && session.markers && session.markers.intro;
    const d = duration();
    introMark.hidden = !(intro && d);
    if (intro && d) {
      introMark.style.left = `${(intro.start / d) * 100}%`;
      introMark.style.width = `${((intro.end - intro.start) / d) * 100}%`;
    }
  }

  function paintMarkers(p) {
    if (switching) return;
    const m = (session && session.markers) || {};
    const intro = m.intro;
    const inIntro = intro && p >= intro.start && p < intro.end - 1000;
    if (inIntro && prefs.auto_skip_intro && !introSkipped) {
      introSkipped = true;
      toast('Intro passée');
      seekTo(intro.end);
      return;
    }
    skipBtn.hidden = !inIntro;
    // next episode countdown
    const next = session && session.next;
    const d = duration();
    if (!next || nextDismissed || !d) return;
    const creditsAt = m.credits ? m.credits.start : d - 25000;
    if (p >= creditsAt && nextCard.hidden && !video.paused) showNextCard(next);
    if (p < creditsAt - 2000 && !nextCard.hidden) hideNextCard();
  }

  function showNextCard(next) {
    let left = prefs.autoplay_next ? 10 : null;
    const counter = h('span.next-count');
    const paint = () => { counter.textContent = left === null ? '' : `dans ${left} s`; };
    paint();
    clear(nextCard,
      next.thumb ? h('img', { src: img(next.type === 'episode' ? next.thumb : next.art, 320, 180), alt: '' }) : null,
      h('div.next-body', h('div.muted.small', next.type === 'episode' ? 'Épisode suivant ' : 'À suivre ', counter),
        h('strong', next.type === 'episode' ? `${episodeCode(next)} · ${next.title}` : next.title),
        h('div.actions-row',
          h('button.btn.primary.small', { onclick: playNext }, icon('play'), 'Lire maintenant'),
          h('button.btn.ghost.small', { onclick: () => { nextDismissed = true; hideNextCard(); } }, 'Masquer'))));
    nextCard.hidden = false;
    clearInterval(nextTimer);
    if (left !== null) {
      nextTimer = setInterval(() => {
        if (video.paused) return;
        left -= 1;
        paint();
        if (left <= 0) playNext();
      }, 1000);
    }
  }
  function hideNextCard() { nextCard.hidden = true; clearInterval(nextTimer); }

  function playNext() {
    if (!session || !session.next) return;
    clearInterval(nextTimer);
    report('stopped', true, { ended: true });
    finished = true;
    if (group) {
      group.send('item', { item_id: session.next.id, position: 0 });
      location.replace(`#/play/${session.next.id}?group=${group.id}`);
      return;
    }
    location.replace(`#/play/${session.next.id}${ctx.query.get('playlist') ? `?playlist=${ctx.query.get('playlist')}` : ''}`);
  }

  function goBack() {
    if (history.length > 1) history.back();
    else location.hash = `#/item/${item ? (item.type === 'episode' ? item.show_id : item.id) : ''}`;
  }

  // ------------------------------------------------------------ menus
  function openMenu(kind) {
    if (!menu.hidden && menu.dataset.kind === kind) { menu.hidden = true; return; }
    menu.dataset.kind = kind;
    const opt = (label, active, onPick, hint) => h('button.menu-opt', { class: active ? 'menu-opt on' : 'menu-opt',
      onclick: () => { menu.hidden = true; onPick(); } }, h('span', label), hint ? h('span.muted.small', hint) : null,
    active ? icon('check') : null);
    const restart = () => start(pos());
    chapterPanel.hidden = true;
    if (kind === 'group') {
      clear(menu, group ? group.menu(opt) : createGroupMenu(opt, {
        itemId: item.id, position: pos, beforeLeave: () => { finished = true; report('stopped', true); } }));
    } else if (kind === 'tracks') {
      const audio = session ? session.audio : item.audio;
      const subs = session ? session.subtitles : item.subtitles;
      clear(menu,
        h('div.menu-col', h('h4', 'Audio'), audio.length ? audio.map((a) => opt(a.title, audioSel && a.id === audioSel.id,
          () => { audioSel = a; subSel = fixForced(subSel, a, subs); rememberTracks(); restart(); })) : h('p.muted.small', 'Piste unique')),
        h('div.menu-col', h('h4', 'Sous-titres'),
          opt('Aucun', !subSel, () => { subSel = null; rememberTracks(); restart(); }),
          menuSubs(subs, audioSel).map((s) => opt(s.title, subSel && s.id === subSel.id, () => { subSel = s; rememberTracks(); restart(); },
            s.forced ? SIGNS_HINT : (s.overlay || s.pgs ? null : 'incrusté')))));
    } else {
      const q = [['original', 'Originale'], ['1080p', '1080p'], ['720p', '720p'], ['480p', '480p']];
      const modeLabel = { direct: 'Lecture directe', copy: 'Remux (vidéo copiée)', transcode: 'Transcodage' }[session.mode] || '';
      const cols = [h('div.menu-col', h('h4', 'Qualité'), q.map(([v, l]) => opt(l, quality === v, () => { quality = v; restart(); })),
        h('p.menu-info', h('strong', modeLabel), session.reasons && session.reasons.length ? ` — ${session.reasons.join(', ')}` : '',
          session.mode === 'transcode' ? h('br') : null, session.mode === 'transcode' ? 'Le processeur du serveur travaille pour cette lecture.' : null))];
      if (isAdmin && item.type === 'episode') {
        const setMarker = async (which) => {
          const cur = (session.markers && session.markers.intro) || {};
          const draft = { start: cur.start ?? 0, end: cur.end ?? 0, [which]: pos() };
          if (draft.end <= draft.start) {
            session.markers = { ...session.markers, intro: { ...draft, end: draft.start + 1 } };
            toast(which === 'start' ? 'Début noté, place-toi à la fin de l’intro' : 'La fin doit être après le début', which === 'start' ? 'info' : 'bad');
            return;
          }
          try {
            await put(`/api/admin/markers/${item.id}`, { kind: 'intro', start: draft.start, end: draft.end });
            session.markers = { ...session.markers, intro: { ...draft, source: 'manual' } };
            updateIntroMark();
            toast(`Intro : ${fmtTime(draft.start)} → ${fmtTime(draft.end)}`, 'ok');
          } catch (e) { toast(e.message, 'bad'); }
        };
        cols.push(h('div.menu-col', h('h4', 'Intro (admin)'),
          opt('Début de l’intro ici', false, () => setMarker('start')),
          opt('Fin de l’intro ici', false, () => setMarker('end')),
          opt('Effacer le marqueur', false, async () => {
            await put(`/api/admin/markers/${item.id}`, { kind: 'intro', clear: true });
            session.markers = { ...session.markers, intro: undefined };
            updateIntroMark();
          })));
      }
      clear(menu, cols);
    }
    menu.hidden = false;
  }

  // ------------------------------------------------------------ chapitres
  function openChapters() {
    if (!chapterPanel.hidden) { chapterPanel.hidden = true; return; }
    menu.hidden = true;
    const p = pos();
    const cur = chapters.findIndex((c) => p >= c.start && p < c.end);
    const strip = chapterStrip(chapters, { current: cur, onPick: (c) => { chapterPanel.hidden = true; seekTo(c.start + 1); } });
    clear(chapterPanel,
      h('div.chapter-panel-head', h('h3', 'Sélection du chapitre'),
        cur >= 0 ? h('span.muted.small', `En cours : ${chapterTitle(chapters[cur], cur)}`) : null,
        h('div.grow'),
        h('button.icon-btn', { 'aria-label': 'Fermer', onclick: () => { chapterPanel.hidden = true; } }, icon('close'))),
      strip);
    chapterPanel.hidden = false;
    const on = strip.querySelector('.chapter-card.on') || strip.firstElementChild;
    if (on) requestAnimationFrame(() => { on.scrollIntoView({ block: 'nearest', inline: 'center' }); on.focus({ preventScroll: true }); });
  }

  function setupMediaSession() {
    if (!('mediaSession' in navigator) || !window.MediaMetadata) return;
    const art = item.type === 'episode' ? (item.show_thumb || item.thumb) : item.thumb;
    navigator.mediaSession.metadata = new window.MediaMetadata({
      title: item.type === 'episode' ? `${episodeCode(item)} · ${item.title}` : item.title,
      artist: item.type === 'episode' ? item.show : String(item.year || ''),
      artwork: art ? [{ src: img(art, 300, 450), sizes: '300x450', type: 'image/jpeg' }] : [],
    });
    navigator.mediaSession.setActionHandler('seekbackward', () => seekBy(-10000));
    navigator.mediaSession.setActionHandler('seekforward', () => seekBy(30000));
    if (session.next) navigator.mediaSession.setActionHandler('nexttrack', playNext);
  }

  // ------------------------------------------------------------ events
  const togglePlay = () => {
    if (group) {
      if (group.waitingWhilePlaying) { group.send('pause', { position: pos() }); return; }
      if (video.paused) { group.send('play', { position: pos() }); rebuffering = false; video.play().catch(() => {}); }
      else { group.send('pause', { position: pos() }); video.pause(); }
      return;
    }
    if (rebuffering) { rebuffering = false; paintPlay(); return; } // reste en pause : la reprise auto est annulée
    if (video.paused) video.play().catch(() => {}); else video.pause();
  };
  const toggleFull = () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else player.requestFullscreen().catch(() => {});
  };
  const wake = () => {
    player.classList.remove('idle');
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => { if (!video.paused && menu.hidden && chapterPanel.hidden) player.classList.add('idle'); }, HIDE_AFTER);
  };

  playBtn.addEventListener('click', togglePlay);
  fullBtn.addEventListener('click', toggleFull);
  backBtn.addEventListener('click', goBack);
  nextBtn.addEventListener('click', playNext);
  subsBtn.addEventListener('click', (e) => { e.stopPropagation(); openMenu('tracks'); });
  chaptersBtn.addEventListener('click', (e) => { e.stopPropagation(); openChapters(); });
  groupBtn.addEventListener('click', (e) => { e.stopPropagation(); openMenu('group'); });
  cogBtn.addEventListener('click', (e) => { e.stopPropagation(); openMenu('settings'); });
  skipBtn.addEventListener('click', () => { const i = session.markers.intro; introSkipped = true; skipBtn.hidden = true; seekTo(i.end); });
  muteBtn.addEventListener('click', () => { video.muted = !video.muted; });
  volume.addEventListener('input', () => { video.volume = Number(volume.value); video.muted = video.volume === 0; });
  video.addEventListener('volumechange', () => {
    muteBtn.replaceChildren(icon(video.muted || video.volume === 0 ? 'mute' : 'volume'));
    volume.value = video.muted ? 0 : video.volume;
    store('seance.volume', String(video.volume));
    store('seance.muted', video.muted ? '1' : '0');
  });
  video.addEventListener('click', () => {
    if (!menu.hidden || !chapterPanel.hidden) { menu.hidden = true; chapterPanel.hidden = true; return; }
    togglePlay();
  });
  video.addEventListener('dblclick', toggleFull);
  video.addEventListener('play', () => { paintPlay(); wake(); });
  video.addEventListener('pause', () => { paintPlay(); wake(); if (!rebuffering) report('paused', true); });
  video.addEventListener('waiting', () => { loader.hidden = false; onStall(); });
  video.addEventListener('playing', () => { loader.hidden = true; switching = false; everPlayed = true; subs.sync(base); });
  video.addEventListener('canplay', () => { if (!rebuffering && !switching) loader.hidden = true; });
  video.addEventListener('timeupdate', () => {
    const p = pos();
    if (group) group.drift(p);
    paintTime();
    subs.paint(p);
    paintMarkers(p);
    // saut au-delà de ce qui est déjà extrait : on le dit plutôt que d'afficher un écran sans sous-titres
    subsPending.hidden = subs.until === null || p <= subs.until;
    if (!video.paused) report('playing');
  });
  video.addEventListener('progress', paintTime);
  video.addEventListener('ended', () => {
    // the transcoded stream can be a little shorter than the file: treat as finished
    report('stopped', true, { ended: true });
    finished = true;
    if (session.next && prefs.autoplay_next && !nextDismissed) playNext();
    else { paintPlay(); player.classList.remove('idle'); }
  });
  document.addEventListener('fullscreenchange', onFullscreen);
  function onFullscreen() { fullBtn.replaceChildren(icon(document.fullscreenElement ? 'exitfull' : 'full')); }

  // timeline: hover preview + click/drag to seek
  const ratioAt = (e) => {
    const r = timeline.getBoundingClientRect();
    return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  };
  timeline.addEventListener('pointermove', (e) => {
    const ratio = ratioAt(e);
    const t = ratio * duration();
    hoverTip.hidden = false;
    previewTime.textContent = fmtTime(t);
    const ch = chapters.find((c) => t >= c.start && t < c.end);
    previewChapter.textContent = ch && (ch.title || chapters.length > 1) ? (ch.title || `Chapitre ${chapters.indexOf(ch) + 1}`) : '';
    if (trick) {
      // vignette tirée de la planche d'aperçus (grille cols x rows)
      const n = Math.min(trick.count - 1, Math.floor(t / 1000 / trick.interval));
      const perSheet = trick.cols * trick.rows;
      const sheet = Math.floor(n / perSheet) + 1;
      const k = n % perSheet;
      previewImg.hidden = false;
      previewImg.style.width = `${trick.w}px`;
      previewImg.style.height = `${trick.h}px`;
      previewImg.style.backgroundImage = `url("/api/items/${item.id}/trickplay/${sheet}.jpg")`;
      previewImg.style.backgroundPosition = `-${(k % trick.cols) * trick.w}px -${Math.floor(k / trick.cols) * trick.h}px`;
      previewImg.style.backgroundSize = `${trick.w * trick.cols}px ${trick.h * trick.rows}px`;
    }
    const r = timeline.getBoundingClientRect();
    const half = (trick ? trick.w : 60) / 2 + 4;
    const x = Math.min(r.width - half, Math.max(half, ratio * r.width));
    hoverTip.style.left = `${x}px`;
    if (scrubbing) played.style.width = `${ratio * 100}%`;
  });
  timeline.addEventListener('pointerleave', () => { if (!scrubbing) hoverTip.hidden = true; });
  timeline.addEventListener('pointerdown', (e) => {
    scrubbing = true;
    timeline.setPointerCapture(e.pointerId);
    played.style.width = `${ratioAt(e) * 100}%`;
  });
  timeline.addEventListener('pointerup', (e) => {
    if (!scrubbing) return;
    scrubbing = false;
    hoverTip.hidden = true;
    seekTo(ratioAt(e) * duration());
  });
  timeline.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') e.stopPropagation();
    if (e.key === 'ArrowLeft') seekBy(-10000);
    if (e.key === 'ArrowRight') seekBy(10000);
  });

  player.addEventListener('pointermove', wake);
  player.addEventListener('click', (e) => { if (!menu.contains(e.target) && e.target !== video) wake(); });
  const onKey = (e) => {
    if (e.target.closest && e.target.closest('input, select, textarea')) return;
    wake();
    if (!chapterPanel.hidden && ['ArrowLeft', 'ArrowRight', 'Enter', ' '].includes(e.key)) {
      // dans la sélection de chapitre, les flèches passent d'une vignette à l'autre
      const cards = [...chapterPanel.querySelectorAll('.chapter-card')];
      let i = cards.indexOf(document.activeElement);
      if (e.key === 'Enter' || e.key === ' ') { if (i >= 0) { e.preventDefault(); cards[i].click(); } return; }
      e.preventDefault();
      if (i < 0) i = Math.max(0, cards.findIndex((c) => c.classList.contains('on')));
      else i = Math.min(cards.length - 1, Math.max(0, i + (e.key === 'ArrowRight' ? 1 : -1)));
      cards[i].focus();
      cards[i].scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
      return;
    }
    switch (e.key) {
      case ' ': case 'k': e.preventDefault(); togglePlay(); break;
      case 'ArrowLeft': e.preventDefault(); seekBy(-10000); break;
      case 'ArrowRight': e.preventDefault(); seekBy(10000); break;
      case 'ArrowUp': e.preventDefault(); video.volume = Math.min(1, video.volume + 0.1); break;
      case 'ArrowDown': e.preventDefault(); video.volume = Math.max(0, video.volume - 0.1); break;
      case 'f': toggleFull(); break;
      case 'm': video.muted = !video.muted; break;
      case 's': if (!skipBtn.hidden) skipBtn.click(); break;
      case 'n': if (session && session.next) playNext(); break;
      case 'c': if (chapters.length > 1) openChapters(); break;
      case 'Escape':
        if (!chapterPanel.hidden) chapterPanel.hidden = true;
        else if (!menu.hidden) menu.hidden = true;
        else if (!document.fullscreenElement) goBack();
        break;
      default:
    }
  };
  document.addEventListener('keydown', onKey);
  const onHide = () => report(document.visibilityState === 'hidden' ? 'paused' : 'playing', true);
  document.addEventListener('visibilitychange', onHide);
  const onUnload = () => report('stopped', true);
  window.addEventListener('pagehide', onUnload);

  await prepare(startAt);
  if (destroyed || !ctx.isCurrent()) return () => {};
  await start(startAt);
  wake();

  return () => {
    if (Date.now() - startedAt > 3000 && !switching) report('stopped', true);
    if (session) post('/api/stop', { stream: session.stream }, { keepalive: true }).catch(() => {});
    destroyed = true;
    if (group) group.dispose();
    clearInterval(nextTimer);
    clearTimeout(idleTimer);
    document.removeEventListener('keydown', onKey);
    document.removeEventListener('visibilitychange', onHide);
    document.removeEventListener('fullscreenchange', onFullscreen);
    window.removeEventListener('pagehide', onUnload);
    subs.destroy();
    if (hls) hls.destroy();
    video.removeAttribute('src');
    video.load();
    if (document.fullscreenElement && !location.hash.startsWith('#/play/')) document.exitFullscreen().catch(() => {});
  };
}
