// Illustrations des avatars (dessins originaux, viewBox 0 0 100 100, sans fond : le fond est choisi à part).
// Contenu fixe écrit dans le code : utilisé tel quel pour dessiner l'aperçu et l'image envoyée au serveur.
import { MOTIFS } from './avatars.js';
import { tr } from './i18n.js';

const OUT = 'stroke="#1d1d27" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"';

export const ART = {
  // ---------------------------------------------------------------- Cinéma
  popcorn: [tr('Pop-corn'), 'cinema', `
    <g ${OUT}>
      <circle cx="34" cy="34" r="10" fill="#fff6dd"/><circle cx="48" cy="26" r="11" fill="#fffaf0"/><circle cx="62" cy="33" r="10" fill="#fff3cf"/>
      <circle cx="42" cy="38" r="9" fill="#ffe9a8"/><circle cx="56" cy="39" r="9" fill="#fffaf0"/>
      <path d="M27 44h46l-7 42H34z" fill="#e5383b"/>
      <path d="M38 44l3 42M50 44v42M62 44l-3 42" stroke="#fff" stroke-width="5"/>
      <path d="M27 44h46l-7 42H34z" fill="none"/>
    </g>`],
  clap: [tr('Clap'), 'cinema', `
    <g ${OUT}>
      <rect x="18" y="42" width="64" height="40" rx="4" fill="#2b2d42"/>
      <path d="M18 42l60-14 4 12-62 14z" fill="#f1faee" transform="rotate(-6 18 42)"/>
      <path d="M28 38.5l8 9M42 35l8 9M56 31.5l8 9M70 28l7 8" stroke="#2b2d42" stroke-width="5" transform="rotate(-6 18 42)"/>
      <rect x="18" y="42" width="64" height="12" fill="#f1faee"/>
      <path d="M28 42l-6 12M44 42l-6 12M60 42l-6 12M76 42l-6 12" stroke="#2b2d42" stroke-width="5"/>
      <path d="M28 64h30M28 72h20" stroke="#f1faee" stroke-width="3"/>
    </g>`],
  bobine: [tr('Bobine'), 'cinema', `
    <g ${OUT}>
      <circle cx="46" cy="48" r="30" fill="#8d99ae"/>
      <circle cx="46" cy="48" r="6" fill="#2b2d42"/>
      <circle cx="46" cy="30" r="8" fill="#2b2d42"/><circle cx="46" cy="66" r="8" fill="#2b2d42"/>
      <circle cx="28" cy="48" r="8" fill="#2b2d42"/><circle cx="64" cy="48" r="8" fill="#2b2d42"/>
      <path d="M70 66c8 6 14 10 16 20" fill="none" stroke="#2b2d42" stroke-width="6"/>
    </g>`],
  ticket: [tr('Ticket'), 'cinema', `
    <g ${OUT} transform="rotate(-14 50 50)">
      <path d="M16 32h68v12a6 6 0 0 0 0 12v12H16V56a6 6 0 0 0 0-12z" fill="#ffd166"/>
      <path d="M62 32v36" stroke-dasharray="4 4"/>
      <path d="M26 44h26M26 52h20M26 60h24" stroke="#c1121f" stroke-width="3"/>
      <path d="M73 44l2.5 5 5.5.8-4 3.9.9 5.5-4.9-2.6-4.9 2.6.9-5.5-4-3.9 5.5-.8z" fill="#c1121f" stroke="none"/>
    </g>`],
  lunettes3d: [tr('Lunettes 3D'), 'cinema', `
    <g ${OUT}>
      <path d="M14 40h72v8l-4 18H58l-4-12h-8l-4 12H18l-4-18z" fill="#f8f9fa"/>
      <rect x="20" y="44" width="24" height="18" rx="3" fill="#e63946"/>
      <rect x="56" y="44" width="24" height="18" rx="3" fill="#00b4d8"/>
      <path d="M14 42L8 34M86 42l6-8" fill="none"/>
    </g>`],
  camera: [tr('Caméra'), 'cinema', `
    <g ${OUT}>
      <circle cx="34" cy="28" r="12" fill="#495057"/><circle cx="58" cy="28" r="12" fill="#495057"/>
      <circle cx="34" cy="28" r="4" fill="#ced4da"/><circle cx="58" cy="28" r="4" fill="#ced4da"/>
      <rect x="18" y="42" width="52" height="32" rx="5" fill="#343a40"/>
      <path d="M70 52l16-8v28l-16-8z" fill="#495057"/>
      <circle cx="30" cy="52" r="3" fill="#e63946" stroke="none"/>
      <path d="M34 74l-8 14M54 74l8 14" fill="none"/>
    </g>`],
  projecteur: [tr('Projecteur'), 'cinema', `
    <g ${OUT}>
      <path d="M58 46L92 30v44L58 58z" fill="#fff3b0" stroke="none" opacity=".75"/>
      <rect x="14" y="40" width="44" height="26" rx="5" fill="#6c757d"/>
      <circle cx="58" cy="52" r="8" fill="#ffd60a"/>
      <circle cx="26" cy="30" r="10" fill="#495057"/><circle cx="46" cy="30" r="10" fill="#495057"/>
      <path d="M24 66l-6 18M48 66l6 18" fill="none"/>
    </g>`],
  fauteuil: [tr('Fauteuil rouge'), 'cinema', `
    <g ${OUT}>
      <path d="M28 20h44a6 6 0 0 1 6 6v34H22V26a6 6 0 0 1 6-6z" fill="#c1121f"/>
      <path d="M22 56h56v16H22z" fill="#9d0208"/>
      <rect x="12" y="44" width="12" height="30" rx="4" fill="#780000"/>
      <rect x="76" y="44" width="12" height="30" rx="4" fill="#780000"/>
      <path d="M30 72v12M70 72v12" fill="none"/>
      <path d="M36 32h28" stroke="#f4a3a8" stroke-width="3"/>
    </g>`],

  // ---------------------------------------------------------------- Séries et TV
  teleretro: [tr('Télé rétro'), 'tv', `
    <g ${OUT}>
      <path d="M38 22L28 10M62 22l10-12" fill="none"/>
      <rect x="14" y="22" width="72" height="54" rx="10" fill="#c08552"/>
      <rect x="22" y="30" width="44" height="38" rx="8" fill="#4cc9f0"/>
      <path d="M30 40c6-4 12-4 16 2" stroke="#fff" stroke-width="3" fill="none"/>
      <circle cx="76" cy="40" r="4" fill="#3d2c2e"/><circle cx="76" cy="54" r="4" fill="#3d2c2e"/>
      <path d="M26 76l-4 10M74 76l4 10" fill="none"/>
    </g>`],
  telecommande: [tr('Télécommande'), 'tv', `
    <g ${OUT} transform="rotate(-18 50 50)">
      <rect x="34" y="12" width="32" height="78" rx="12" fill="#343a40"/>
      <circle cx="50" cy="28" r="7" fill="#e63946"/>
      <circle cx="42" cy="46" r="4" fill="#adb5bd"/><circle cx="58" cy="46" r="4" fill="#adb5bd"/>
      <circle cx="42" cy="58" r="4" fill="#adb5bd"/><circle cx="58" cy="58" r="4" fill="#adb5bd"/>
      <rect x="42" y="68" width="16" height="12" rx="3" fill="#4cc9f0"/>
    </g>`],
  canape: [tr('Canapé'), 'tv', `
    <g ${OUT}>
      <rect x="22" y="28" width="56" height="30" rx="8" fill="#6a4c93"/>
      <rect x="12" y="44" width="18" height="30" rx="6" fill="#553c7a"/>
      <rect x="70" y="44" width="18" height="30" rx="6" fill="#553c7a"/>
      <rect x="28" y="52" width="44" height="18" rx="4" fill="#8a6bbf"/>
      <path d="M18 74v8M82 74v8" fill="none"/>
      <rect x="36" y="34" width="14" height="14" rx="3" fill="#ffd166" transform="rotate(-8 43 41)"/>
    </g>`],
  ecran: [tr('Écran'), 'tv', `
    <g ${OUT}>
      <rect x="10" y="20" width="80" height="50" rx="5" fill="#212529"/>
      <rect x="15" y="25" width="70" height="40" rx="2" fill="#4361ee"/>
      <path d="M44 36v18l15-9z" fill="#fff"/>
      <path d="M40 80h20M50 70v10" fill="none" stroke-width="4"/>
    </g>`],
  tasse: [tr('Chocolat chaud'), 'tv', `
    <g ${OUT}>
      <path d="M38 16c-4 6 4 8 0 14M50 14c-4 6 4 8 0 14M62 16c-4 6 4 8 0 14" fill="none" stroke="#fff" stroke-width="3" opacity=".8"/>
      <path d="M22 36h48v28a16 16 0 0 1-16 16H38a16 16 0 0 1-16-16z" fill="#f4f1de"/>
      <path d="M70 44h6a8 8 0 0 1 0 16h-6" fill="none" stroke-width="5"/>
      <ellipse cx="46" cy="36" rx="24" ry="5" fill="#7f4f24"/>
      <path d="M30 56c6 4 26 4 32 0" stroke="#e07a5f" stroke-width="4" fill="none"/>
    </g>`],
  parabole: [tr('Antenne'), 'tv', `
    <g ${OUT}>
      <path d="M24 30a34 34 0 0 0 44 44z" fill="#e9ecef"/>
      <path d="M46 52l14-14" fill="none"/><circle cx="62" cy="36" r="5" fill="#ffb703"/>
      <path d="M44 66l-6 20h24l-6-20" fill="#adb5bd"/>
      <path d="M70 22c4 2 6 4 8 8M74 14c7 3 11 7 14 14" fill="none" stroke="#fff" stroke-width="3"/>
    </g>`],

  // ---------------------------------------------------------------- Musique
  casque: [tr('Casque audio'), 'musique', `
    <g ${OUT}>
      <path d="M18 60V48a32 32 0 0 1 64 0v12" fill="none" stroke-width="7" stroke="#1d1d27"/>
      <path d="M18 60V48a32 32 0 0 1 64 0v12" fill="none" stroke-width="3" stroke="#f72585"/>
      <rect x="12" y="52" width="18" height="30" rx="8" fill="#f72585"/>
      <rect x="70" y="52" width="18" height="30" rx="8" fill="#f72585"/>
      <rect x="22" y="56" width="8" height="22" rx="3" fill="#7209b7"/><rect x="70" y="56" width="8" height="22" rx="3" fill="#7209b7"/>
    </g>`],
  vinyle: [tr('Vinyle'), 'musique', `
    <g ${OUT}>
      <circle cx="50" cy="50" r="36" fill="#1d1d27"/>
      <circle cx="50" cy="50" r="28" fill="none" stroke="#3a3a4a" stroke-width="2"/>
      <circle cx="50" cy="50" r="20" fill="none" stroke="#3a3a4a" stroke-width="2"/>
      <circle cx="50" cy="50" r="12" fill="#ff9f1c"/><circle cx="50" cy="50" r="2.5" fill="#1d1d27"/>
      <path d="M30 30a28 28 0 0 1 12-7" fill="none" stroke="#fff" stroke-width="3" opacity=".6"/>
    </g>`],
  micro: [tr('Micro'), 'musique', `
    <g ${OUT}>
      <rect x="36" y="12" width="28" height="42" rx="14" fill="#adb5bd"/>
      <path d="M40 24h20M40 32h20M40 40h20" stroke="#6c757d" stroke-width="2"/>
      <path d="M28 44a22 22 0 0 0 44 0" fill="none" stroke-width="4"/>
      <path d="M50 66v14M38 86h24" fill="none" stroke-width="5"/>
    </g>`],
  cassette: [tr('Cassette'), 'musique', `
    <g ${OUT}>
      <rect x="12" y="24" width="76" height="52" rx="6" fill="#06d6a0"/>
      <rect x="22" y="32" width="56" height="24" rx="4" fill="#f1faee"/>
      <circle cx="36" cy="44" r="6" fill="#1d1d27"/><circle cx="64" cy="44" r="6" fill="#1d1d27"/>
      <path d="M42 44h16" stroke-width="3"/>
      <path d="M28 76l4-12h36l4 12" fill="#118ab2"/>
    </g>`],
  enceinte: [tr('Enceinte'), 'musique', `
    <g ${OUT}>
      <rect x="24" y="12" width="52" height="76" rx="8" fill="#3d405b"/>
      <circle cx="50" cy="32" r="10" fill="#81b29a"/><circle cx="50" cy="32" r="4" fill="#1d1d27"/>
      <circle cx="50" cy="64" r="16" fill="#81b29a"/><circle cx="50" cy="64" r="6" fill="#1d1d27"/>
    </g>`],
  notes: [tr('Notes'), 'musique', `
    <g ${OUT}>
      <path d="M40 70V24l36-8v46" fill="none" stroke-width="5"/>
      <path d="M40 24l36-8v10l-36 8z" fill="#1d1d27"/>
      <ellipse cx="32" cy="72" rx="10" ry="8" fill="#ffbe0b" transform="rotate(-20 32 72)"/>
      <ellipse cx="68" cy="64" rx="10" ry="8" fill="#ffbe0b" transform="rotate(-20 68 64)"/>
    </g>`],

  // ---------------------------------------------------------------- Personnages (originaux)
  chat3d: [tr('Chat cinéphile'), 'perso', `
    <g ${OUT}>
      <path d="M22 40L20 14l20 14M78 40l2-26-20 14" fill="#f4a261"/>
      <ellipse cx="50" cy="56" rx="32" ry="28" fill="#f4a261"/>
      <path d="M18 50h64v6l-3 10H56l-3-8h-6l-3 8H21l-3-10z" fill="#f8f9fa"/>
      <rect x="23" y="53" width="20" height="11" rx="2" fill="#e63946"/><rect x="57" y="53" width="20" height="11" rx="2" fill="#00b4d8"/>
      <path d="M46 72l4 3 4-3" fill="none"/><path d="M50 75v4" fill="none"/>
      <path d="M24 74l-10 2M24 78l-9 5M76 74l10 2M76 78l9 5" fill="none" stroke-width="1.6"/>
    </g>`],
  renard: [tr('Renard mélomane'), 'perso', `
    <g ${OUT}>
      <path d="M20 46L18 14l22 18M80 46l2-32-22 18" fill="#e76f51"/>
      <path d="M18 44c0-14 14-20 32-20s32 6 32 20c0 22-18 38-32 38S18 66 18 44z" fill="#e76f51"/>
      <path d="M50 82c-8 0-18-8-22-20 8 2 14 6 22 6s14-4 22-6c-4 12-14 20-22 20z" fill="#fff"/>
      <circle cx="38" cy="52" r="4" fill="#1d1d27"/><circle cx="62" cy="52" r="4" fill="#1d1d27"/>
      <ellipse cx="50" cy="66" rx="4" ry="3" fill="#1d1d27"/>
      <path d="M12 54V44a38 38 0 0 1 76 0v10" fill="none" stroke-width="5"/>
      <rect x="6" y="48" width="12" height="20" rx="5" fill="#2a9d8f"/><rect x="82" y="48" width="12" height="20" rx="5" fill="#2a9d8f"/>
    </g>`],
  panda: [tr('Panda gourmand'), 'perso', `
    <g ${OUT}>
      <circle cx="24" cy="28" r="11" fill="#1d1d27"/><circle cx="76" cy="28" r="11" fill="#1d1d27"/>
      <ellipse cx="50" cy="50" rx="32" ry="30" fill="#fff"/>
      <ellipse cx="38" cy="48" rx="8" ry="10" fill="#1d1d27" transform="rotate(-20 38 48)"/>
      <ellipse cx="62" cy="48" rx="8" ry="10" fill="#1d1d27" transform="rotate(20 62 48)"/>
      <circle cx="39" cy="47" r="2.5" fill="#fff" stroke="none"/><circle cx="61" cy="47" r="2.5" fill="#fff" stroke="none"/>
      <ellipse cx="50" cy="62" rx="5" ry="3.5" fill="#1d1d27"/>
      <path d="M58 70h26l-4 22H62z" fill="#e5383b"/><path d="M66 70l1 22M76 70l-1 22" stroke="#fff" stroke-width="3"/>
      <circle cx="64" cy="66" r="5" fill="#fff6dd"/><circle cx="72" cy="63" r="5" fill="#fffaf0"/><circle cx="80" cy="67" r="5" fill="#fff3cf"/>
    </g>`],
  hibou: [tr('Hibou noctambule'), 'perso', `
    <g ${OUT}>
      <path d="M22 30l8 10M78 30l-8 10" fill="none"/>
      <ellipse cx="50" cy="56" rx="30" ry="32" fill="#8d6e63"/>
      <ellipse cx="50" cy="66" rx="18" ry="18" fill="#d7ccc8"/>
      <circle cx="38" cy="44" r="12" fill="#fff"/><circle cx="62" cy="44" r="12" fill="#fff"/>
      <circle cx="40" cy="45" r="5" fill="#1d1d27"/><circle cx="60" cy="45" r="5" fill="#1d1d27"/>
      <path d="M46 54l4 6 4-6z" fill="#ffb703"/>
      <path d="M78 18l2 4 4 .6-3 2.8.7 4.2-3.7-2-3.7 2 .7-4.2-3-2.8 4-.6z" fill="#ffd60a"/>
    </g>`],
  robot: [tr('Robot projectionniste'), 'perso', `
    <g ${OUT}>
      <path d="M50 16v8" fill="none"/><circle cx="50" cy="13" r="4" fill="#ef476f"/>
      <rect x="22" y="24" width="56" height="44" rx="10" fill="#8ecae6"/>
      <rect x="30" y="34" width="40" height="18" rx="9" fill="#023047"/>
      <circle cx="40" cy="43" r="4" fill="#90e0ef" stroke="none"/><circle cx="60" cy="43" r="4" fill="#90e0ef" stroke="none"/>
      <path d="M40 60h20" fill="none"/>
      <rect x="16" y="38" width="6" height="16" rx="2" fill="#219ebc"/><rect x="78" y="38" width="6" height="16" rx="2" fill="#219ebc"/>
      <rect x="32" y="72" width="36" height="16" rx="5" fill="#219ebc"/>
    </g>`],
  chien: [tr('Chien du canapé'), 'perso', `
    <g ${OUT}>
      <path d="M22 30c-10 4-12 24-4 32l10-6z" fill="#6d4c41"/><path d="M78 30c10 4 12 24 4 32l-10-6z" fill="#6d4c41"/>
      <ellipse cx="50" cy="52" rx="28" ry="30" fill="#d4a373"/>
      <ellipse cx="50" cy="66" rx="14" ry="11" fill="#faedcd"/>
      <circle cx="40" cy="46" r="4" fill="#1d1d27"/><circle cx="60" cy="46" r="4" fill="#1d1d27"/>
      <ellipse cx="50" cy="60" rx="6" ry="4" fill="#1d1d27"/>
      <path d="M50 64v5M44 70c3 3 9 3 12 0" fill="none"/>
      <path d="M62 30l12-10 6 6-12 10z" fill="#343a40"/><circle cx="74" cy="22" r="2" fill="#e63946" stroke="none"/>
    </g>`],
  lapin: [tr('Lapin réalisateur'), 'perso', `
    <g ${OUT}>
      <ellipse cx="38" cy="22" rx="7" ry="18" fill="#f1faee"/><ellipse cx="62" cy="22" rx="7" ry="18" fill="#f1faee"/>
      <ellipse cx="38" cy="22" rx="3" ry="12" fill="#ffafcc" stroke="none"/><ellipse cx="62" cy="22" rx="3" ry="12" fill="#ffafcc" stroke="none"/>
      <ellipse cx="50" cy="58" rx="28" ry="26" fill="#f1faee"/>
      <path d="M22 44c8-8 48-8 56 0v4H22z" fill="#1d1d27"/><path d="M26 40h48" stroke="#e63946" stroke-width="3"/>
      <circle cx="40" cy="56" r="4" fill="#1d1d27"/><circle cx="60" cy="56" r="4" fill="#1d1d27"/>
      <path d="M47 66l3 3 3-3" fill="#ffafcc"/><path d="M44 72c4 3 8 3 12 0" fill="none"/>
    </g>`],
  pieuvre: [tr('Pieuvre DJ'), 'perso', `
    <g ${OUT}>
      <path d="M22 50a28 28 0 0 1 56 0v10H22z" fill="#b5179e"/>
      <path d="M22 58c-4 10-10 14-14 14M34 60c0 12-4 20-8 24M46 60c0 12 0 20-2 26M56 60c2 12 2 20 6 26M68 60c4 12 6 18 12 22M78 56c6 8 10 12 14 12" fill="none" stroke="#b5179e" stroke-width="7"/>
      <circle cx="40" cy="44" r="5" fill="#fff"/><circle cx="60" cy="44" r="5" fill="#fff"/>
      <circle cx="41" cy="45" r="2.5" fill="#1d1d27"/><circle cx="61" cy="45" r="2.5" fill="#1d1d27"/>
      <path d="M44 54c4 3 8 3 12 0" fill="none"/>
      <path d="M24 34V30a26 26 0 0 1 52 0v4" fill="none" stroke-width="4"/>
    </g>`],
};

// motifs simples (première galerie), en blanc sur le fond choisi
for (const [id, d] of Object.entries(MOTIFS)) {
  ART[`motif-${id}`] = [tr(id.charAt(0).toUpperCase() + id.slice(1)), 'motifs',
    `<path d="${d}" fill="#fff" transform="translate(18 18) scale(2.66)" style="filter:drop-shadow(0 .4px .6px rgba(0,0,0,.3))"/>`];
}

export const CATEGORIES = [['cinema', tr('Cinéma')], ['tv', tr('Séries et TV')], ['musique', tr('Musique')], ['perso', tr('Personnages')],
  ['motifs', tr('Motifs')]];

export const BACKGROUNDS = [['#ff9a3d', '#e2513a'], ['#5b8def', '#2f3fbf'], ['#3dd68c', '#138a72'], ['#b07cf7', '#6435c9'],
  ['#ff6f91', '#c2185b'], ['#2dd4bf', '#0e7490'], ['#ffd23f', '#f08a24'], ['#8e9aaf', '#3d4a5c'],
  ['#f472b6', '#7c3aed'], ['#60a5fa', '#10b981'], ['#fb7185', '#f59e0b'], ['#1e293b', '#0b0b0e']];

// fond conseillé pour chaque illustration (modifiable dans le choix d'avatar)
export const DEFAULT_BG = {
  'motif-etoile': 0, 'motif-coeur': 4, 'motif-lune': 1, 'motif-eclair': 6, 'motif-note': 3, 'motif-soleil': 6,
  'motif-montagne': 2, 'motif-feuille': 9, 'motif-flamme': 10, 'motif-pellicule': 8, 'motif-couronne': 6, 'motif-patte': 7, popcorn: 1, clap: 6, bobine: 10, ticket: 3, lunettes3d: 3, camera: 6, projecteur: 1, fauteuil: 7,
  teleretro: 2, telecommande: 6, canape: 6, ecran: 0, tasse: 5, parabole: 3, casque: 6, vinyle: 4, micro: 3, cassette: 3,
  enceinte: 0, notes: 3, chat3d: 1, renard: 5, panda: 2, hibou: 1, robot: 0, chien: 9, lapin: 8, pieuvre: 6 };

let seq = 0;

export function artSvg(id, bg = null, size = 100) {
  const [, , body] = ART[id];
  const [a, b] = BACKGROUNDS[bg ?? DEFAULT_BG[id] ?? 0];
  const gid = `bg${(seq += 1)}`; // identifiant unique : plusieurs avatars dans la même page
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="${size}" height="${size}">`
    + `<defs><linearGradient id="${gid}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${a}"/><stop offset="1" stop-color="${b}"/></linearGradient></defs>`
    + `<rect width="100" height="100" fill="url(#${gid})"/><g transform="translate(6 6) scale(0.88)">${body}</g></svg>`;
}

// image JPEG 256 × 256 (data URL) pour l'envoi au serveur
export function artDataUrl(id, bg = null) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(new Blob([artSvg(id, bg, 256)], { type: 'image/svg+xml' }));
    img.onload = () => {
      const c = document.createElement('canvas');
      c.width = c.height = 256;
      c.getContext('2d').drawImage(img, 0, 0, 256, 256);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL('image/jpeg', 0.92));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error(tr('Dessin impossible'))); };
    img.src = url;
  });
}
