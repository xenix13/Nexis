// Raccourcis clavier du lecteur : touches par défaut, modifiables sur chaque appareil (Paramètres → Cet appareil),
// et l'aide affichée par la touche « ? ».
import { clear, h, modal } from './ui.js';
import { tr } from './i18n.js';

const KEY = 'nexis-keys';
export const ACTIONS = [
  ['play', 'Lecture / pause', ' '],
  ['back', 'Reculer de 10 s', 'ArrowLeft'],
  ['forward', 'Avancer de 10 s', 'ArrowRight'],
  ['volup', 'Monter le son', 'ArrowUp'],
  ['voldown', 'Baisser le son', 'ArrowDown'],
  ['full', 'Plein écran', 'f'],
  ['mute', 'Couper le son', 'm'],
  ['skip', 'Passer l’intro', 's'],
  ['next', 'Épisode suivant', 'n'],
  ['chapters', 'Chapitres', 'c'],
  ['mini', 'Réduire le lecteur', 'i'],
  ['slower', 'Ralentir', '<'],
  ['faster', 'Accélérer', '>'],
];

function custom() {
  try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch { return {}; }
}

export function bindings() {
  const mine = custom();
  return Object.fromEntries(ACTIONS.map(([id, , def]) => [id, mine[id] || def]));
}

export function setBinding(id, key) {
  const mine = custom();
  if (key === null) delete mine[id]; else mine[id] = key;
  try { localStorage.setItem(KEY, JSON.stringify(mine)); } catch { /* stockage indisponible */ }
}

export function resetBindings() {
  try { localStorage.removeItem(KEY); } catch { /* stockage indisponible */ }
}

// action liée à une touche (« k » reste aussi lecture / pause, comme sur YouTube)
export function actionFor(key) {
  const b = bindings();
  const hit = Object.entries(b).find(([, k]) => k === key || (k.length === 1 && key.length === 1 && k.toLowerCase() === key.toLowerCase() && !/[<>?]/.test(k)));
  if (hit) return hit[0];
  return key === 'k' ? 'play' : null;
}

export const keyLabel = (k) => ({ ' ': tr('Espace'), ArrowLeft: '←', ArrowRight: '→', ArrowUp: '↑', ArrowDown: '↓', Escape: tr('Échap') })[k] || k.toUpperCase();

export function shortcutsHelp() {
  const b = bindings();
  const rows = (list) => h('dl.keys-list', list.flatMap(([label, k]) => [h('dt', h('kbd', k)), h('dd', label)]));
  modal(tr('Raccourcis clavier'), h('div.keys-help',
    h('h3', tr('Partout')),
    rows([['/', tr('Rechercher')], ['?', tr('Afficher cette aide')], [tr('Échap'), tr('Revenir en arrière')]]),
    h('h3', tr('Dans le lecteur')),
    rows(ACTIONS.map(([id, label]) => [tr(label), keyLabel(b[id])])),
    h('p.muted.small', tr('Les touches du lecteur se changent dans Paramètres → Cet appareil.'))),
  [{ label: tr('Fermer'), primary: true, value: true }]);
}

// panneau de réglage : une ligne par action, « Changer » attend la prochaine touche
export function shortcutsPanel() {
  const list = h('div.keys-edit');
  const paint = () => {
    const b = bindings();
    const mine = custom();
    clear(list, ACTIONS.map(([id, label]) => {
      const btn = h('button.btn.small', { type: 'button' }, tr('Changer'));
      btn.addEventListener('click', () => {
        btn.textContent = tr('Appuyez sur une touche…');
        const grab = (e) => {
          e.preventDefault();
          e.stopPropagation();
          document.removeEventListener('keydown', grab, true);
          if (e.key !== 'Escape' && e.key !== 'Tab') setBinding(id, e.key);
          paint();
        };
        document.addEventListener('keydown', grab, true);
      });
      return h('div.keys-row', h('span.grow', tr(label)), h('kbd', keyLabel(b[id])), btn,
        mine[id] ? h('button.btn.small.ghost', { type: 'button', onclick: () => { setBinding(id, null); paint(); } }, tr('Par défaut')) : null);
    }));
  };
  paint();
  return h('section.panel', h('h2', tr('Raccourcis clavier du lecteur')),
    h('p.muted.small', tr('Propres à cet appareil. Échap annule le changement en cours ; la touche ? affiche l’aide partout dans Noxa.')),
    list, h('div.actions-row', h('button.btn.small', { type: 'button', onclick: () => { resetBindings(); paint(); } }, tr('Tout remettre par défaut'))));
}
