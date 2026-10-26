// Navigation à la télécommande, au clavier et à la manette (Android TV, PC avec manette).
// Les flèches mènent à l'élément le plus proche dans leur direction (comme la navigation spatiale d'Android TV),
// OK / Entrée ouvre, Retour / Échap ferme ce qui est ouvert ou revient en arrière. Une manette est traduite en
// ces mêmes touches : croix ou stick gauche = flèches, A = OK, B = retour, Start = lecture / pause.

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea, [tabindex="0"]';
const KEYS = { ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right' };

// Télécommande de TV ou manette : on adapte quelques comportements (lecteur : haut / bas montrent les commandes)
export const remoteMode = () => document.documentElement.classList.contains('remote');
import { device } from './device.js';

const markRemote = () => { if (device().remote !== 'off') document.documentElement.classList.add('remote'); };
if (/android tv|googletv|smart-?tv|aft[a-z]|bravia|crkey|tizen|web0s|nexisapp/i.test(navigator.userAgent)) markRemote();

const visible = (el) => {
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return false;
  const st = getComputedStyle(el);
  return st.visibility !== 'hidden' && st.display !== 'none' && !el.closest('[hidden], [inert]');
};

// Zone où chercher : fenêtre ouverte, menu ouvert, sinon toute la page
function scope() {
  const modals = document.querySelectorAll('.modal-overlay');
  if (modals.length) return modals[modals.length - 1];
  const menu = [...document.querySelectorAll('.more-list, .user-menu, .player-menu, .chapter-panel')].find((m) => !m.hidden && visible(m));
  if (menu && menu.contains(document.activeElement)) return menu;
  return document.querySelector('.player:not(.mini)') || document.body;
}

// L'élément le plus proche dans la direction : distance dans l'axe, l'écart sur le côté compte trois fois plus
function nearest(from, dir, candidates) {
  const a = from.getBoundingClientRect();
  const ax = a.left + a.width / 2;
  const ay = a.top + a.height / 2;
  let best = null;
  let bestScore = Infinity;
  for (const el of candidates) {
    if (el === from || from.contains(el)) continue;
    const b = el.getBoundingClientRect();
    const bx = b.left + b.width / 2;
    const by = b.top + b.height / 2;
    let main;
    let side;
    if (dir === 'left' || dir === 'right') {
      main = dir === 'right' ? b.left - a.right : a.left - b.right;
      if ((dir === 'right' ? bx <= ax : bx >= ax)) continue;
      // gauche / droite restent sur la même ligne (au bout d'une rangée, on ne saute pas ailleurs)
      if (b.bottom <= a.top + 2 || b.top >= a.bottom - 2) continue;
      side = Math.abs(by - ay) * 0.2;
    } else {
      main = dir === 'down' ? b.top - a.bottom : a.top - b.bottom;
      if ((dir === 'down' ? by <= ay : by >= ay)) continue;
      side = Math.max(0, Math.max(b.left, a.left) - Math.min(b.right, a.right)) * 0.5 || Math.abs(bx - ax) * 0.05;
    }
    const score = Math.max(main, -a.height / 2) + side * 3;
    if (score < bestScore) { bestScore = score; best = el; }
  }
  return best;
}

function firstTarget(root) {
  // premier élément sensé : contenu de la page plutôt que la barre du haut
  const main = root.querySelector('.content') || root;
  return [...main.querySelectorAll(FOCUSABLE)].find(visible) || [...root.querySelectorAll(FOCUSABLE)].find(visible);
}

function move(dir) {
  const root = scope();
  const cur = document.activeElement && document.activeElement !== document.body && root.contains(document.activeElement)
    ? document.activeElement : null;
  const target = cur ? nearest(cur, dir, [...root.querySelectorAll(FOCUSABLE)].filter(visible)) : firstTarget(root);
  if (!target) return false;
  target.focus({ preventScroll: true });
  target.scrollIntoView({ block: dir === 'up' || dir === 'down' ? 'center' : 'nearest', inline: 'nearest', behavior: 'smooth' });
  return true;
}

function back() {
  const open = [...document.querySelectorAll('.more-list, .user-menu')].find((m) => !m.hidden);
  if (open) { open.hidden = true; return; }
  if (document.querySelector('.modal-overlay, .player')) return; // fenêtres et lecteur gèrent leur propre « retour »
  if (location.hash && location.hash !== '#/' && history.length > 1) history.back();
}

function onKey(e) {
  if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
  const dir = KEYS[e.key];
  const t = e.target;
  const typing = t.closest && t.closest('input:not([type="checkbox"]):not([type="range"]), textarea');
  if (dir) {
    // dans un champ de texte, gauche / droite déplacent le curseur ; haut / bas en sortent.
    // Une liste déroulante se traite comme un bouton : OK l'ouvre, les flèches passent à côté.
    if (typing && (dir === 'left' || dir === 'right')) return;
    if (document.querySelector('.player:not(.mini)')) return; // le lecteur a ses propres flèches (voir player.js)
    if (t.closest && t.closest('.timeline, .row-track input')) return;
    if (move(dir)) e.preventDefault();
    return;
  }
  if ((e.key === 'Backspace' && !typing) || e.key === 'BrowserBack' || e.key === 'GoBack') {
    e.preventDefault();
    back();
  }
}
document.addEventListener('keydown', onKey);

// Appli Android TV : la touche Retour de la télécommande arrive ici. -> true si Nexis l'a prise en charge,
// false si l'on est déjà à l'accueil (l'appli propose alors de quitter).
window.nexisBack = () => {
  const menuOpen = [...document.querySelectorAll('.more-list, .user-menu')].some((m) => !m.hidden);
  if (menuOpen) { back(); return true; }
  if (document.querySelector('.modal-overlay, .player:not(.mini)')) {
    (document.activeElement || document.body).dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    return true;
  }
  if (location.hash && location.hash !== '#/' && location.hash !== '#') { history.back(); return true; }
  return false;
};
// le lecteur demande la navigation spatiale dans ses commandes
export { move as moveFocus };

// ------------------------------------------------------------------ manette (Gamepad API)
// Les événements fabriqués ne déclenchent pas les actions par défaut du navigateur : A appelle donc click().
const PAD = { 12: 'ArrowUp', 13: 'ArrowDown', 14: 'ArrowLeft', 15: 'ArrowRight', 0: 'Enter', 1: 'Escape', 9: ' ' };
const held = {};
let polling = false;

function press(key) {
  markRemote();
  const el = document.activeElement || document.body;
  const ev = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
  el.dispatchEvent(ev);
  if (ev.defaultPrevented) return;
  if (key === 'Enter' && el !== document.body) el.click();
  if (key === 'Escape') back();
}

function poll() {
  const pads = navigator.getGamepads ? [...navigator.getGamepads()].filter(Boolean) : [];
  if (!pads.length) { polling = false; return; }
  const now = performance.now();
  for (const pad of pads) {
    const axes = pad.axes || [];
    const stick = { 12: axes[1] < -0.6, 13: axes[1] > 0.6, 14: axes[0] < -0.6, 15: axes[0] > 0.6 };
    for (const [btn, key] of Object.entries(PAD)) {
      const down = (pad.buttons[btn] && pad.buttons[btn].pressed) || stick[btn];
      const id = `${pad.index}:${btn}`;
      if (!down) { delete held[id]; continue; }
      const repeat = key.startsWith('Arrow');
      if (!held[id]) { held[id] = now + 380; press(key); } // premier appui, puis répétition si on maintient
      else if (repeat && now >= held[id]) { held[id] = now + 110; press(key); }
    }
  }
  requestAnimationFrame(poll);
}
window.addEventListener('gamepadconnected', () => {
  markRemote();
  if (!polling) { polling = true; requestAnimationFrame(poll); }
});
