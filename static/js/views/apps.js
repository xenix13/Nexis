// Page « Applications » : Nexis à installer sur chaque appareil, avec le logo de la plateforme et les étapes
// d'installation. Les fichiers viennent de ce serveur (/apps/<plateforme>). Lisible sans être connecté.
import { get } from '../api.js';
import { platformLogo } from '../platforms.js';
import { clear, h, icon, spinner } from '../ui.js';
import { locale, tr } from '../i18n.js';
import { installWebApp, nativeApp, webAppPossible } from '../install.js';

// plateforme de cet appareil
export function detectPlatform() {
  const ua = navigator.userAgent;
  const plat = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '';
  if (/android/i.test(ua)) return /\btv\b|aft[a-z]|bravia|googletv|smart-?tv/i.test(ua) ? 'tv' : 'android';
  if (/iphone|ipad|ipod/i.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)) return 'ios';
  if (/mac/i.test(plat) || /macintosh/i.test(ua)) return 'macos';
  if (/win/i.test(plat) || /windows/i.test(ua)) return 'windows';
  if (/linux|x11/i.test(ua) && !/cros/i.test(ua)) return 'linux';
  return null;
}

const GB = 1e9;
const size = (b) => (b ? (b >= GB ? `${(b / GB).toLocaleString(locale(), { maximumFractionDigits: 1 })} ${tr('Go')}` : `${Math.round(b / 1e6)} ${tr('Mo')}`) : '');

// une entrée par plateforme : logo, nom, appareils concernés, fichiers, étapes
export const PLATFORMS = [
  { id: 'windows', logo: 'windows', name: 'Windows', devices: () => tr('Windows 10 et 11, 64 bits'), files: [['windows', tr('Installateur (.exe)')]],
    steps: () => [tr('Ouvrir le fichier téléchargé.'),
      tr('Windows peut afficher « Windows a protégé votre ordinateur » (l’application n’est pas signée par un certificat payant) : cliquer sur « Informations complémentaires », puis « Exécuter quand même ».'),
      tr('Nexis s’installe et s’ouvre. Indiquez l’adresse de ce serveur au premier lancement.')] },
  { id: 'macos', logo: 'apple', name: 'macOS', devices: () => tr('macOS 12 ou plus récent, Mac Intel et Apple Silicon'), files: [['macos', tr('Image disque (.dmg)')]],
    steps: () => [tr('Ouvrir le fichier téléchargé et glisser Nexis dans le dossier Applications.'),
      tr('Au premier lancement, macOS bloque une application qui ne vient pas de l’App Store : ouvrir Réglages Système → Confidentialité et sécurité, puis « Ouvrir quand même » en bas de la page (une seule fois).'),
      tr('Indiquez l’adresse de ce serveur au premier lancement.')] },
  { id: 'linux', logo: 'linux', name: 'Linux', devices: () => tr('Toutes distributions (AppImage) ou Debian, Ubuntu, Mint (.deb), 64 bits'),
    files: [['linux-appimage', 'AppImage'], ['linux-deb', tr('Paquet .deb')]],
    steps: () => [tr('AppImage : rendre le fichier exécutable (clic droit → Propriétés → Autoriser l’exécution, ou chmod +x Nexis.AppImage), puis l’ouvrir. Il se met à jour tout seul.'),
      tr('.deb : l’ouvrir avec l’installateur de logiciels, ou sudo apt install ./nexis-desktop.deb.')] },
  { id: 'android', logo: 'android', name: 'Android', devices: () => tr('Téléphones et tablettes, Android 8 ou plus récent'), files: [['android', tr('Application (.apk)')]],
    steps: () => [tr('Ouvrir le fichier téléchargé (notification ou dossier Téléchargements).'),
      tr('Android demande une fois d’autoriser l’installation depuis ce navigateur : « Paramètres » → « Autoriser cette source », puis revenir et « Installer ».'),
      tr('Les mises à jour sont proposées par l’application elle-même.')] },
  { id: 'tv', logo: 'android', tv: true, name: 'Android TV', devices: () => tr('Android TV, Google TV, Nvidia Shield, Chromecast avec Google TV'), files: [['tv', tr('Application (.apk)')]],
    steps: () => [tr('Sur la TV, installer l’application « Downloader » depuis le Play Store.'),
      tr('Y taper l’adresse de ce serveur suivie de /tv (par exemple {url}), puis installer.', { url: `${location.origin}/tv` })] },
  { id: 'ios', logo: 'apple', name: 'iPhone, iPad', devices: () => tr('Application web, depuis Safari'), web: true,
    steps: () => [tr('Ouvrir Nexis dans Safari, appuyer sur Partager, puis « Sur l’écran d’accueil ».'),
      tr('Apple ne permet pas d’installer une application en dehors de l’App Store : sur iPhone et iPad, Nexis s’installe comme application web. Une fois sur l’écran d’accueil, ses téléchargements sont gardés.')] },
];

export function card(p, data, here) {
  const avail = new Map((data ? data.apps : []).map((a) => [a.id, a]));
  const ver = data && data.versions ? (p.id === 'android' ? data.versions.android : ['windows', 'macos', 'linux'].includes(p.id) ? data.versions.desktop : null) : null;
  return h('section.app-card', { class: here ? 'app-card here' : 'app-card', id: `app-${p.id}` },
    h('div.app-head', h('span.app-logo', platformLogo(p.logo, 34), p.tv ? h('span.app-tv', icon('tv')) : null),
      h('div', h('h2', p.name), h('div.muted.small', p.devices())),
      here ? h('span.app-here', tr('Cet appareil')) : null),
    p.web
      ? h('div.app-files', webAppPossible() ? h('button.btn.primary', { onclick: () => installWebApp() }, icon('install'), tr('Installer l’application web')) : null)
      : h('div.app-files', p.files.map(([id, label]) => {
        const a = avail.get(id);
        return a && a.available
          ? h('a.btn', { class: here ? 'btn primary' : 'btn', href: a.url, download: a.file }, icon('download'), label, a.size ? h('span.app-size', size(a.size)) : null)
          : h('span.btn.disabled', { title: tr('Pas encore publiée pour cette version du serveur') }, label);
      }), ver ? h('span.muted.small', tr('version {v}', { v: typeof ver === 'object' ? ver.version : ver })) : null),
    h('details.app-steps', { open: here }, h('summary', tr('Installation')), h('ol', p.steps().map((s) => h('li', s)))));
}

export async function renderApps(main) {
  document.title = tr('Applications · Nexis');
  const here = detectPlatform();
  const box = h('div.app-grid', spinner());
  const n = nativeApp();
  clear(main, h('div.page-head', h('h1', tr('Applications'))),
    h('p.muted.app-lead', tr('Nexis s’installe comme un vrai programme sur votre ordinateur, téléphone, tablette ou TV. Les fichiers viennent de ce serveur ; l’interface des applications est celle du serveur et suit donc chacune de ses mises à jour.')),
    n ? h('div.status-box.ok', tr('Vous utilisez l’application Nexis {v}.', { v: n.version || '' })) : null,
    box);
  let data = null;
  try { data = await get('/api/apps'); } catch { /* serveur ancien ou GitHub injoignable : étapes seulement */ }
  const order = [...PLATFORMS].sort((a, b) => (b.id === here) - (a.id === here));
  clear(box, ...order.map((p) => card(p, data, p.id === here)));
  if (data && !data.apps.some((a) => a.available)) {
    box.before(h('div.status-box', tr('Les applications ne sont pas encore publiées pour cette version du serveur : elles le seront à la prochaine version. L’application web reste disponible.')));
  }
}
