// Web player: HLS stream proxied through Séance, custom controls, resume,
// per-user track preferences, "skip intro" banner and next-episode countdown.
import { get, post, put } from './api.js';
import { state } from './app.js';
import { clear, episodeCode, errorBox, fmtTime, h, icon, img, spinner, toast } from './ui.js';

const REPORT_EVERY = 10000;
const HIDE_AFTER = 3000;

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch { /* storage unavailable */ }
  return null;
}

function pickAudio(audio, prefs) {
  if (!audio.length) return null;
  const want = prefs.audio_lang;
  return (want && audio.find((a) => a.lang === want)) || audio.find((a) => a.selected) || audio.find((a) => a.default) || audio[0];
}

function pickSubtitle(subs, audio, prefs) {
  if (prefs.sub_mode === 'never' || !subs.length) return null;
  const lang = prefs.sub_lang;
  const inLang = subs.filter((s) => !lang || s.lang === lang);
  const best = inLang.find((s) => !s.forced && s.overlay) || inLang.find((s) => !s.forced) || inLang[0];
  if (prefs.sub_mode === 'always') return best || null;
  // "foreign": subtitles only when the audio is not in the user's language
  if (audio && prefs.audio_lang && audio.lang && audio.lang !== prefs.audio_lang) return best || null;
  return subs.find((s) => s.forced && audio && s.lang === audio.lang) || null;
}

export async function renderPlayer(root, rk, ctx) {
  const prefs = state.me.prefs;
  const isAdmin = state.me.user.is_admin;
  const video = h('video.video', { playsinline: true, preload: 'auto' });
  const subLayer = h('div.sub-layer', { style: { fontSize: `${(prefs.sub_size / 100) * 2.4}vmin` } });
  const loader = h('div.player-loader', spinner());
  const subsWait = h('div.subs-wait', { hidden: true });
  const errorLayer = h('div.player-error', { hidden: true });
  const titleEl = h('div.player-title');
  const timeEl = h('span.time');
  const played = h('div.tl-played');
  const buffered = h('div.tl-buffered');
  const introMark = h('div.tl-intro', { hidden: true });
  const hoverTip = h('div.tl-tip', { hidden: true });
  const timeline = h('div.timeline', { role: 'slider', 'aria-label': 'Position', tabindex: 0 },
    h('div.tl-track', buffered, introMark, played, h('div.tl-thumb')), hoverTip);
  const playBtn = h('button.ctl', { 'aria-label': 'Lecture/Pause' }, icon('play'));
  const muteBtn = h('button.ctl', { 'aria-label': 'Couper le son' }, icon('volume'));
  const volume = h('input.volume', { type: 'range', min: 0, max: 1, step: 0.05, 'aria-label': 'Volume' });
  const nextBtn = h('button.ctl', { 'aria-label': 'Épisode suivant', hidden: true }, icon('next'));
  const subsBtn = h('button.ctl', { 'aria-label': 'Audio et sous-titres' }, icon('subs'));
  const cogBtn = h('button.ctl', { 'aria-label': 'Qualité' }, icon('cog'));
  const fullBtn = h('button.ctl', { 'aria-label': 'Plein écran' }, icon('full'));
  const menu = h('div.player-menu', { hidden: true });
  const skipBtn = h('button.skip-btn', { hidden: true }, icon('skip'), 'Passer l’intro');
  const nextCard = h('div.next-card', { hidden: true });
  const backBtn = h('button.ctl.back', { 'aria-label': 'Retour' }, icon('back'));

  const controls = h('div.controls',
    timeline,
    h('div.ctl-row',
      playBtn,
      h('button.ctl', { 'aria-label': 'Reculer de 10 secondes', onclick: () => seekBy(-10000) }, icon('rewind')),
      h('button.ctl', { 'aria-label': 'Avancer de 30 secondes', onclick: () => seekBy(30000) }, icon('forward')),
      h('div.vol', muteBtn, volume), timeEl, h('div.grow'), nextBtn, subsBtn, cogBtn, fullBtn));
  const top = h('div.player-top', backBtn, titleEl);
  const player = h('div.player', video, subLayer, loader, subsWait, errorLayer, top, skipBtn, nextCard, menu, controls);
  clear(root, player);

  // ------------------------------------------------------------ state
  let item, session = null, hls = null, base = 0, cues = [], cueIdx = -1;
  let audioSel = null, subSel = null, quality = prefs.quality;
  let lastReport = 0, introSkipped = false, nextTimer = null, nextDismissed = false, destroyed = false;
  let idleTimer = null, scrubbing = false, startedAt = 0;
  let switching = true, finished = false; // no progress reports while a stream is (re)starting or after the end

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
  if (!['movie', 'episode'].includes(item.type)) { location.replace(`#/item/${rk}`); return () => {}; }
  document.title = `${item.type === 'episode' ? `${item.show} ${episodeCode(item)}` : item.title} · Séance`;
  titleEl.append(h('strong', item.type === 'episode' ? item.show : item.title),
    item.type === 'episode' ? h('span', `${episodeCode(item)} · ${item.title}`) : (item.year ? h('span', String(item.year)) : null));
  const poster = item.type === 'episode' ? (item.show_art || item.art) : item.art;
  if (poster) video.poster = img(poster, 1280, 720);

  audioSel = pickAudio(item.audio || [], prefs);
  subSel = pickSubtitle(item.subtitles || [], audioSel, prefs);
  let startAt = 0;
  if (ctx.query.get('t')) startAt = Number(ctx.query.get('t')) || 0;
  else if (ctx.query.get('resume') && item.position && !item.watched) startAt = item.position;

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
  const HLS_CONFIG = {
    maxBufferLength: 40, backBufferLength: 90, startPosition: 0,
    manifestLoadPolicy: slow(60000, 90000, 2),
    playlistLoadPolicy: slow(60000, 90000, 4),
    fragLoadPolicy: slow(60000, 120000, 6),
  };
  let startSeq = 0, autoRetries = 0, forceHls = false;

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
        audio: audioSel ? audioSel.id : null, subtitle: subSel ? subSel.id : 0, replace: previous || undefined });
    } catch (e) { if (seq === startSeq) showError(e.message, true); return; }
    if (destroyed || seq !== startSeq) {
      // a newer seek (or leaving the player) superseded this one
      post('/api/stop', { stream: s.stream }).catch(() => {});
      return;
    }
    session = s;
    base = session.offset;
    introSkipped = false;
    nextBtn.hidden = !session.next;
    updateIntroMark();
    // la lecture ne démarre qu'une fois les sous-titres prêts (pas de trou au début de l'épisode)
    const subsReady = loadSubtitles(seq);
    const autoplay = async () => {
      await subsReady;
      if (seq !== startSeq || destroyed) return;
      video.play().catch(() => { loader.hidden = true; paintPlay(); });
    };
    if (session.mode === 'direct') {
      // le navigateur lit le fichier tel quel : position réelle = currentTime
      base = 0;
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
      hls = new window.Hls(HLS_CONFIG);
      hls.on(window.Hls.Events.ERROR, (_, data) => {
        if (!data.fatal || seq !== startSeq) return;
        if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) { hls.recoverMediaError(); return; }
        console.warn('[seance] erreur HLS', data.type, data.details, data.response && data.response.code);
        const at = Math.max(base, pos());
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
      video.src = session.url;
      video.addEventListener('loadedmetadata', autoplay, { once: true });
    } else {
      showError('Ce navigateur ne lit pas le HLS et hls.js est absent (static/vendor/hls.min.js). Relance le script d’installation.');
      return;
    }
    startedAt = Date.now();
    setupMediaSession();
  }

  function loadSubtitles(seq) {
    cues = [];
    cueIdx = -1;
    subLayer.replaceChildren();
    subsWait.hidden = true;
    if (!session.subtitle_overlay) return Promise.resolve();
    const url = session.subtitle_overlay;
    return new Promise((resolve) => {
      let released = false;
      const release = () => {
        if (released) return;
        released = true;
        subsWait.hidden = true;
        resolve();
      };
      const poll = async () => {
        if (seq !== startSeq || destroyed) { release(); return; }
        try {
          const r = await get(url);
          if (seq !== startSeq || destroyed) { release(); return; }
          if (r && r.pending) {
            if (!released) {
              const pct = Math.round((r.progress || 0) * 100);
              clear(subsWait, h('div.subs-wait-box',
                h('strong', 'Préparation des sous-titres…'),
                h('div.bar', h('div.bar-fill', { style: { width: `${Math.max(3, pct)}%` } })),
                h('p.small', pct ? `${pct} % — lecture du fichier sur le NAS` : 'Lecture du fichier sur le NAS'),
                h('button.btn.ghost.small', { onclick: release }, 'Lancer sans attendre')));
              subsWait.hidden = false;
            }
            setTimeout(poll, 1000);
            return;
          }
          cues = r.cues || [];
          cueIdx = -1;
          if (released && cues.length) toast('Sous-titres prêts');
          release();
        } catch (e) {
          toast(`Sous-titres : ${e.message}`, 'bad');
          release();
        }
      };
      poll();
    });
  }

  function showError(msg, retry = false, at = null) {
    loader.hidden = true;
    errorLayer.hidden = false;
    clear(errorLayer, h('div.error-box', h('p', msg),
      h('div.actions-row',
        retry ? h('button.btn.primary', { onclick: () => { autoRetries = 0; start(at ?? pos()); } }, 'Réessayer') : null,
        h('button.btn', { onclick: goBack }, 'Retour'))));
  }

  function seekTo(ms) {
    const d = duration();
    ms = Math.max(0, d ? Math.min(ms, d - 3000) : ms);
    if (session && session.mode === 'direct') { video.currentTime = ms / 1000; return; }
    const rel = (ms - base) / 1000;
    const b = video.buffered;
    for (let i = 0; i < b.length; i++) {
      if (rel >= b.start(i) - 0.5 && rel <= b.end(i) + 0.5) {
        video.currentTime = Math.max(0, rel);
        return;
      }
    }
    report('paused', true);
    start(ms);
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
    playBtn.replaceChildren(icon(video.paused ? 'play' : 'pause'));
    player.classList.toggle('paused', video.paused);
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

  function paintSubtitles(p) {
    if (!cues.length) return;
    if (cueIdx >= 0 && cues[cueIdx] && p >= cues[cueIdx].s && p <= cues[cueIdx].e) return;
    let lo = 0, hi = cues.length - 1, found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (cues[mid].s <= p) { found = mid; lo = mid + 1; } else hi = mid - 1;
    }
    let idx = -1;
    for (let i = found; i >= 0 && i >= found - 3; i--) if (p <= cues[i].e) { idx = i; break; }
    if (idx === cueIdx) return;
    cueIdx = idx;
    subLayer.replaceChildren(...(idx >= 0 ? [h('span.sub-line', cues[idx].t)] : []));
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
      next.thumb ? h('img', { src: img(next.thumb, 320, 180), alt: '' }) : null,
      h('div.next-body', h('div.muted.small', 'Épisode suivant ', counter),
        h('strong', `${episodeCode(next)} · ${next.title}`),
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
    location.replace(`#/play/${session.next.id}`);
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
    if (kind === 'tracks') {
      const audio = session ? session.audio : item.audio;
      const subs = session ? session.subtitles : item.subtitles;
      clear(menu,
        h('div.menu-col', h('h4', 'Audio'), audio.length ? audio.map((a) => opt(a.title, audioSel && a.id === audioSel.id,
          () => { audioSel = a; restart(); })) : h('p.muted.small', 'Piste unique')),
        h('div.menu-col', h('h4', 'Sous-titres'),
          opt('Aucun', !subSel, () => { subSel = null; restart(); }),
          subs.map((s) => opt(s.title, subSel && s.id === subSel.id, () => { subSel = s; restart(); },
            s.overlay ? null : 'incrusté'))));
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
  const togglePlay = () => { if (video.paused) video.play().catch(() => {}); else video.pause(); };
  const toggleFull = () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else player.requestFullscreen().catch(() => {});
  };
  const wake = () => {
    player.classList.remove('idle');
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => { if (!video.paused && menu.hidden) player.classList.add('idle'); }, HIDE_AFTER);
  };

  playBtn.addEventListener('click', togglePlay);
  fullBtn.addEventListener('click', toggleFull);
  backBtn.addEventListener('click', goBack);
  nextBtn.addEventListener('click', playNext);
  subsBtn.addEventListener('click', (e) => { e.stopPropagation(); openMenu('tracks'); });
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
  video.addEventListener('click', () => { if (!menu.hidden) { menu.hidden = true; return; } togglePlay(); });
  video.addEventListener('dblclick', toggleFull);
  video.addEventListener('play', () => { paintPlay(); wake(); });
  video.addEventListener('pause', () => { paintPlay(); wake(); report('paused', true); });
  video.addEventListener('waiting', () => { loader.hidden = false; });
  video.addEventListener('playing', () => { loader.hidden = true; switching = false; });
  video.addEventListener('canplay', () => { loader.hidden = true; });
  video.addEventListener('timeupdate', () => {
    const p = pos();
    paintTime();
    paintSubtitles(p);
    paintMarkers(p);
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
    hoverTip.hidden = false;
    hoverTip.textContent = fmtTime(ratio * duration());
    hoverTip.style.left = `${ratio * 100}%`;
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
      case 'Escape': if (!menu.hidden) menu.hidden = true; else if (!document.fullscreenElement) goBack(); break;
      default:
    }
  };
  document.addEventListener('keydown', onKey);
  const onHide = () => report(document.visibilityState === 'hidden' ? 'paused' : 'playing', true);
  document.addEventListener('visibilitychange', onHide);
  const onUnload = () => report('stopped', true);
  window.addEventListener('pagehide', onUnload);

  await start(startAt);
  wake();

  return () => {
    if (Date.now() - startedAt > 3000 && !switching) report('stopped', true);
    if (session) post('/api/stop', { stream: session.stream }, { keepalive: true }).catch(() => {});
    destroyed = true;
    clearInterval(nextTimer);
    clearTimeout(idleTimer);
    document.removeEventListener('keydown', onKey);
    document.removeEventListener('visibilitychange', onHide);
    document.removeEventListener('fullscreenchange', onFullscreen);
    window.removeEventListener('pagehide', onUnload);
    if (hls) hls.destroy();
    video.removeAttribute('src');
    video.load();
    if (document.fullscreenElement && !location.hash.startsWith('#/play/')) document.exitFullscreen().catch(() => {});
  };
}
