import { post } from '../api.js';
import { clear, fmtTime, h, icon, img, select, toast } from '../ui.js';
import { deviceId } from '../device.js';
import { tr } from '../i18n.js';

// Télécommande : pilote, depuis cet appareil (typiquement le téléphone), la lecture en cours sur un autre
// appareil du même compte (ordinateur, TV, tablette). La liste se met à jour en direct.
export async function renderRemote(main, ctx) {
  document.title = `${tr('Télécommande')} · Noxa`;
  const list = h('div.remote-list');
  const panel = h('div.remote-panel');
  clear(main, h('div.page-head', h('h1', tr('Télécommande'))),
    h('p.muted', tr('Lancez une vidéo sur un ordinateur, une TV ou une tablette connecté à votre compte : elle apparaît ici, et se pilote depuis cet appareil.')),
    list, panel);
  let devices = [], chosen = null, tick = null, at = 0;
  const es = new EventSource(`/api/remote/watch?exclude=${deviceId()}`);
  es.onmessage = (ev) => {
    try { devices = JSON.parse(ev.data); } catch { return; }
    at = Date.now();
    if (!devices.some((d) => d.id === chosen)) chosen = devices.length ? devices[0].id : null;
    paint();
  };
  ctx.setCleanup(() => { es.close(); clearInterval(tick); });

  const send = (action, value) => post(`/api/remote/${chosen}/cmd`, value === undefined ? { action } : { action, value })
    .catch((e) => toast(e.message, 'bad'));

  function paint() {
    clear(list, devices.length > 1 ? h('div.tabs', devices.map((d) => h('button.tab', { type: 'button',
      class: d.id === chosen ? 'tab active' : 'tab', onclick: () => { chosen = d.id; paint(); } }, icon('tv'), d.name))) : null);
    const d = devices.find((x) => x.id === chosen);
    clearInterval(tick);
    if (!d) {
      clear(panel, h('div.empty', h('p', tr('Aucun appareil ne lit de vidéo pour l’instant.')),
        h('p.muted.small', tr('La liste se met à jour toute seule.'))));
      return;
    }
    const s = d.state;
    const time = h('span.remote-time');
    const bar = h('input.remote-seek', { type: 'range', min: 0, max: Math.max(1, s.duration), step: 1000, value: s.position,
      'aria-label': tr('Position') });
    const now = () => Math.min(s.duration || Infinity, s.position + (s.playing ? Date.now() - at : 0));
    const paintTime = () => {
      if (document.activeElement !== bar) bar.value = now();
      time.textContent = `${fmtTime(Number(bar.value))} / ${fmtTime(s.duration)}`;
    };
    bar.addEventListener('input', paintTime);
    bar.addEventListener('change', () => send('seek', Number(bar.value)));
    tick = setInterval(paintTime, 1000);
    const big = (ic, label, action, value) => h('button.remote-btn', { type: 'button', 'aria-label': label, title: label,
      onclick: () => send(action, value) }, icon(ic));
    const vol = h('input', { type: 'range', min: 0, max: 100, value: Math.round((s.muted ? 0 : s.volume ?? 1) * 100), 'aria-label': tr('Volume'),
      onchange: (e) => send('volume', Number(e.target.value) / 100) });
    const audio = (s.audio || []).length > 1 ? select(s.audio.map((a) => [String(a.id), a.title]), String(s.audio_selected ?? ''),
      { 'aria-label': tr('Audio'), onchange: (e) => send('audio', Number(e.target.value)) }) : null;
    const subs = (s.subtitles || []).length ? select([['0', tr('Aucun')], ...s.subtitles.map((t) => [String(t.id), t.title])],
      String(s.subtitle_selected || 0), { 'aria-label': tr('Sous-titres'), onchange: (e) => send('subtitle', Number(e.target.value)) }) : null;
    const speed = select([0.75, 1, 1.25, 1.5, 1.75, 2].map((v) => [String(v), v === 1 ? tr('Normale') : `×${String(v).replace('.', ',')}`]),
      String(s.speed || 1), { 'aria-label': tr('Vitesse'), onchange: (e) => send('speed', Number(e.target.value)) });
    clear(panel, h('div.remote-card',
      h('div.remote-now', s.thumb ? h('img', { src: img(s.thumb, 320, 180), alt: '' }) : null,
        h('div', h('div.muted.small', d.name), h('strong', s.title), s.subtitle ? h('div.muted', s.subtitle) : null)),
      bar, time,
      h('div.remote-buttons',
        big('rewind', tr('Reculer de 10 s'), 'seekby', -10000),
        h('button.remote-btn.main', { type: 'button', 'aria-label': s.playing ? tr('Pause') : tr('Lecture'),
          onclick: () => send(s.playing ? 'pause' : 'play') }, icon(s.playing ? 'pause' : 'play')),
        big('forward', tr('Avancer de 30 s'), 'seekby', 30000),
        s.has_next ? big('next', tr('Épisode suivant'), 'next') : null),
      h('div.remote-row', h('button.icon-btn', { type: 'button', 'aria-label': tr('Couper le son'), onclick: () => send('mute') },
        icon(s.muted ? 'mute' : 'volume')), vol),
      h('div.remote-selects',
        audio ? h('label.filter', h('span', tr('Audio')), audio) : null,
        subs ? h('label.filter', h('span', tr('Sous-titres')), subs) : null,
        h('label.filter', h('span', tr('Vitesse')), speed)),
      h('button.btn.ghost', { type: 'button', onclick: () => send('stop') }, icon('close'), tr('Arrêter la lecture'))));
    paintTime();
  }
  paint();
}
