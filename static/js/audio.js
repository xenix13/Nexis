// Lecteur audio de l'application (livres audio, podcasts) : une barre en bas de l'écran, qui continue pendant
// qu'on navigue. Un livre en plusieurs fichiers se lit à la suite comme un seul (position et chapitres sur la
// durée totale). Vitesse, minuterie, chapitres ; commandes de l'écran verrouillé (MediaSession).
import { post } from './api.js';
import { fmtTime, h, icon, store, toast } from './ui.js';
import { tr } from './i18n.js';

const SPEEDS = [0.8, 1, 1.1, 1.25, 1.5, 1.75, 2];
const REPORT_EVERY = 15000;

let cur = null;      // ce qui est chargé : { kind, id, title, subtitle, cover, files, chapters, duration, progressUrl, offsets }
let bar = null;
let audio = null;
let fileIndex = 0;
let lastReport = 0;
let sleepAt = 0, sleepChapter = false;
let pendingSeek = null;
const listeners = new Set();

const totalPos = () => (cur ? (cur.offsets[fileIndex] || 0) + Math.round((audio.currentTime || 0) * 1000) : 0);
const totalDur = () => (cur ? cur.duration || cur.offsets[cur.offsets.length - 1] || 0 : 0);
const chapterAt = (ms) => (cur && cur.chapters ? cur.chapters.findIndex((c) => ms >= c.s && ms < c.e) : -1);

export function nowPlaying() { return cur ? { kind: cur.kind, id: cur.id, playing: !audio.paused } : null; }
export function onAudio(fn) { listeners.add(fn); return () => listeners.delete(fn); }
const notify = () => listeners.forEach((fn) => { try { fn(nowPlaying()); } catch { /* vue fermée */ } });

function ensure() {
  if (bar) return;
  audio = new Audio();
  audio.preload = 'auto';
  const cover = h('img.ab-cover', { alt: '' });
  const title = h('div.ab-title');
  const sub = h('div.ab-sub.muted');
  const playBtn = h('button.ab-btn.ab-play', { type: 'button', 'aria-label': tr('Lecture/Pause'), onclick: () => toggle() }, icon('play'));
  const back = h('button.ab-btn', { type: 'button', 'aria-label': tr('Reculer'), onclick: () => seek(totalPos() - (cur.kind === 'book' ? 30000 : 15000)) }, '−30');
  const fwd = h('button.ab-btn', { type: 'button', 'aria-label': tr('Avancer de 30 s'), onclick: () => seek(totalPos() + 30000) }, '+30');
  const range = h('input.ab-range', { type: 'range', min: 0, max: 1000, value: 0, 'aria-label': tr('Position') });
  const time = h('span.ab-time.muted');
  const speedBtn = h('button.ab-btn.ab-speed', { type: 'button', title: tr('Vitesse'), onclick: () => {
    const i = (SPEEDS.indexOf(audio.playbackRate) + 1) % SPEEDS.length;
    setSpeed(SPEEDS[i]);
  } });
  const sleepBtn = h('button.ab-btn', { type: 'button', title: tr('Minuterie'), onclick: () => cycleSleep() }, icon('clock'));
  const chapBtn = h('button.ab-btn', { type: 'button', title: tr('Chapitres'), onclick: () => chapterMenu() }, icon('list'));
  const close = h('button.ab-btn', { type: 'button', 'aria-label': tr('Fermer'), onclick: () => stop() }, '×');
  const menu = h('div.ab-menu', { hidden: true });
  bar = h('div.audio-bar', { hidden: true, role: 'region', 'aria-label': tr('Lecteur audio') },
    h('div.ab-main', cover, h('div.ab-text', title, sub),
      h('div.ab-controls', back, playBtn, fwd), h('div.ab-extra', speedBtn, sleepBtn, chapBtn, close)),
    h('div.ab-seek', range, time), menu);
  bar.refs = { cover, title, sub, playBtn, range, time, speedBtn, sleepBtn, chapBtn, menu };
  document.body.append(bar, audio);
  let dragging = false;
  range.addEventListener('input', () => { dragging = true; time.textContent = label(range.value / 1000 * totalDur()); });
  range.addEventListener('change', () => { dragging = false; seek(range.value / 1000 * totalDur()); });
  audio.addEventListener('timeupdate', () => {
    if (!cur) return;
    const p = totalPos(), d = totalDur();
    if (!dragging && d) range.value = Math.round((p / d) * 1000);
    if (!dragging) time.textContent = label(p);
    if (sleepAt && Date.now() >= sleepAt) { audio.pause(); clearSleep(); toast(tr('Minuterie : lecture arrêtée')); }
    if (sleepChapter && cur.chapters) {
      const c = cur.chapters[sleepChapter.index];
      if (c && p >= c.e - 400) { audio.pause(); clearSleep(); toast(tr('Fin du chapitre : lecture arrêtée')); }
    }
    if (Date.now() - lastReport > REPORT_EVERY && !audio.paused) report();
    paintChapter();
  });
  audio.addEventListener('play', () => { paintPlay(); notify(); });
  audio.addEventListener('pause', () => { paintPlay(); report(); notify(); });
  audio.addEventListener('loadedmetadata', () => {
    if (pendingSeek != null) { audio.currentTime = pendingSeek / 1000; pendingSeek = null; }
  });
  audio.addEventListener('ended', () => {
    if (cur && fileIndex < cur.files.length - 1) { load(fileIndex + 1, 0, true); return; }
    report({ finished: true });
    paintPlay();
    notify();
  });
  audio.addEventListener('error', () => { if (cur) toast(tr('Lecture impossible de ce fichier audio'), 'bad'); });
  window.addEventListener('pagehide', () => report({}, true));
  document.addEventListener('visibilitychange', () => { if (document.hidden) report({}, true); });
  if ('mediaSession' in navigator) {
    const ms = navigator.mediaSession;
    const set = (a, fn) => { try { ms.setActionHandler(a, fn); } catch { /* action non prise en charge */ } };
    set('play', () => audio.play());
    set('pause', () => audio.pause());
    set('seekbackward', (e) => seek(totalPos() - ((e && e.seekOffset) || 30) * 1000));
    set('seekforward', (e) => seek(totalPos() + ((e && e.seekOffset) || 30) * 1000));
    set('seekto', (e) => seek(e.seekTime * 1000));
    set('previoustrack', () => jumpChapter(-1));
    set('nexttrack', () => jumpChapter(1));
  }
}

const label = (p) => {
  const d = totalDur();
  const left = Math.max(0, d - p) / (audio.playbackRate || 1);
  return `${fmtTime(p, d >= 3600000)} / −${fmtTime(left, left >= 3600000)}`;
};

function paintPlay() {
  bar.refs.playBtn.replaceChildren(icon(audio.paused ? 'play' : 'pause'));
}

function paintChapter() {
  const i = chapterAt(totalPos());
  const c = i >= 0 && cur.chapters.length > 1 ? cur.chapters[i] : null;
  bar.refs.sub.textContent = c ? `${cur.subtitle ? `${cur.subtitle} · ` : ''}${c.t}` : cur.subtitle || '';
}

function setSpeed(v) {
  audio.playbackRate = v;
  store(`audio-speed:${cur.kind}`, String(v));
  bar.refs.speedBtn.textContent = `×${String(v).replace('.', ',')}`;
}

function clearSleep() {
  sleepAt = 0;
  sleepChapter = false;
  sleepStep = 0;
  bar.refs.sleepBtn.classList.remove('on');
  bar.refs.sleepBtn.title = tr('Minuterie');
}

// minuterie : désactivée -> 15 -> 30 -> 45 -> 60 min -> fin du chapitre (livre) -> désactivée
let sleepStep = 0;
function cycleSleep() {
  const steps = [0, 15, 30, 45, 60, ...(cur.chapters && cur.chapters.length > 1 ? ['chapter'] : [])];
  sleepStep = (sleepStep + 1) % steps.length;
  const step = steps[sleepStep];
  sleepAt = 0;
  sleepChapter = false;
  bar.refs.sleepBtn.classList.toggle('on', !!step);
  if (!step) { toast(tr('Minuterie désactivée')); return; }
  if (step === 'chapter') {
    sleepChapter = { index: chapterAt(totalPos()) };
    toast(tr('Arrêt à la fin du chapitre'));
    return;
  }
  sleepAt = Date.now() + step * 60000;
  toast(tr('Arrêt dans {n} min', { n: step }));
}

function chapterMenu() {
  const m = bar.refs.menu;
  if (!m.hidden) { m.hidden = true; return; }
  const here = chapterAt(totalPos());
  m.replaceChildren(...cur.chapters.map((c, i) => h('button.ab-chapter', { type: 'button', class: i === here ? 'ab-chapter on' : 'ab-chapter',
    onclick: () => { m.hidden = true; seek(c.s + 1); if (audio.paused) audio.play(); } },
  h('span', c.t), h('span.muted', fmtTime(c.s, totalDur() >= 3600000)))));
  m.hidden = false;
  const on = m.querySelector('.on');
  if (on) on.scrollIntoView({ block: 'center' });
}

function jumpChapter(dir) {
  if (!cur.chapters || cur.chapters.length < 2) { seek(totalPos() + dir * 30000); return; }
  let i = chapterAt(totalPos());
  if (dir < 0 && totalPos() - cur.chapters[Math.max(0, i)].s > 4000) dir = 0;  // « précédent » : début du chapitre d'abord
  i = Math.max(0, Math.min(cur.chapters.length - 1, i + dir));
  seek(cur.chapters[i].s + 1);
}

function load(index, ms, autoplay) {
  fileIndex = index;
  const f = cur.files[index];
  if (audio.dataset.src !== f.url) {
    audio.dataset.src = f.url;
    audio.src = f.url;
    pendingSeek = ms;
  } else {
    audio.currentTime = ms / 1000;
  }
  audio.playbackRate = Number(store(`audio-speed:${cur.kind}`)) || 1;
  if (autoplay) audio.play().catch(() => paintPlay());
}

function seek(ms) {
  if (!cur) return;
  ms = Math.max(0, Math.min(ms, totalDur() - 500));
  let i = cur.offsets.findIndex((o, n) => ms >= o && (n === cur.offsets.length - 1 || ms < cur.offsets[n + 1]));
  if (i < 0 || i >= cur.files.length) i = cur.files.length - 1;
  load(i, ms - cur.offsets[i], !audio.paused);
  report();
}

function toggle() {
  if (!cur) return;
  if (audio.paused) audio.play().catch((e) => toast(e.message, 'bad')); else audio.pause();
}

function report(extra = {}, keepalive = false) {
  if (!cur || !cur.progressUrl) return;
  lastReport = Date.now();
  const p = totalPos();
  if (p < 1000 && !extra.finished) return;
  post(cur.progressUrl, { position: p, duration: totalDur(), ...extra }, { keepalive }).catch(() => {});
}

export function stop() {
  if (!cur) return;
  report();
  audio.pause();
  audio.removeAttribute('src');
  audio.dataset.src = '';
  audio.load();
  cur = null;
  bar.hidden = true;
  document.documentElement.classList.remove('has-audio');
  clearSleep();
  notify();
}

// item : { kind: 'book' | 'podcast', id, title, subtitle, cover, files: [{url, duration}], chapters?, duration?, position?, progressUrl }
export function playAudio(item, at = null) {
  ensure();
  if (cur && cur.kind === item.kind && cur.id === item.id) {
    if (at != null) seek(at);
    if (audio.paused) audio.play().catch(() => {});
    return;
  }
  if (cur) stop();
  const offsets = [0];
  for (const f of item.files) offsets.push(offsets[offsets.length - 1] + (f.duration || 0));
  cur = { ...item, offsets, duration: item.duration || offsets[offsets.length - 1] };
  const r = bar.refs;
  r.cover.src = item.cover || '';
  r.cover.hidden = !item.cover;
  r.title.textContent = item.title;
  r.chapBtn.hidden = !(item.chapters && item.chapters.length > 1);
  r.menu.hidden = true;
  bar.hidden = false;
  document.documentElement.classList.add('has-audio');
  setSpeed(Number(store(`audio-speed:${item.kind}`)) || 1);
  paintChapter();
  if ('mediaSession' in navigator && window.MediaMetadata) {
    navigator.mediaSession.metadata = new MediaMetadata({ title: item.title, artist: item.subtitle || '',
      artwork: item.cover ? [{ src: new URL(item.cover, location.href).href, sizes: '512x512' }] : [] });
  }
  const start = at != null ? at : item.position || 0;
  let i = offsets.findIndex((o, n) => start >= o && start < offsets[n + 1]);
  if (i < 0) i = 0;
  load(i, start - offsets[i], true);
  notify();
}
