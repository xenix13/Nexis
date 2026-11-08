// Installer Nexis sur cet appareil : l'application native (Windows, macOS, Linux, Android, Android TV — le fichier
// vient de ce serveur) ou l'application web (invite de Chrome / Edge / Android ; Safari et Firefox : le chemin
// dans leur menu). Rien à proposer dans l'application native elle-même.
import { h, icon, modal } from './ui.js';
import { tr } from './i18n.js';

let prompt = null;
const listeners = new Set();
const notify = () => listeners.forEach((fn) => fn());

export const standalone = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
// application native Nexis : bureau (window.nexisNative) ou Android (passerelle window.NexisAndroid)
export function nativeApp() {
  if (window.nexisNative) return window.nexisNative;
  const a = window.NexisAndroid;
  if (!a) return null;
  let info = {};
  try { info = JSON.parse(a.info()); } catch { /* ancienne passerelle */ }
  return { ...info, changeServer: () => a.changeServer(), checkUpdate: () => a.checkUpdate() };
}
const ios = () => /iphone|ipad|ipod/i.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1); // iPad qui se présente comme un Mac
const firefox = () => /firefox|fxios/i.test(navigator.userAgent) && !/seamonkey/i.test(navigator.userAgent);
const android = () => /android/i.test(navigator.userAgent);
const windows = () => /windows/i.test(navigator.userAgent);

export function setupInstall() {
  if ('serviceWorker' in navigator && window.isSecureContext) {
    navigator.serviceWorker.register('/sw.js').catch((e) => console.warn('Service worker :', e));
  }
  window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); prompt = e; notify(); });
  window.addEventListener('appinstalled', () => { prompt = null; notify(); });
}

// L'entrée « Installer l'application » a-t-elle un sens ici ? (toujours, sauf dans une application déjà installée)
export function canInstall() {
  return !nativeApp() && !standalone();
}

// l'application web s'installe-t-elle ici ? (invite du navigateur, ou chemin dans son menu)
export function webAppPossible() {
  if (standalone() || nativeApp() || !window.isSecureContext) return false;
  return Boolean(prompt) || ios() || (firefox() && (android() || windows())) || !firefox();
}

export function onInstallChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }

// Choix proposé par « Installer l'application » : l'application native (conseillée) ou l'application web
export async function install() {
  const { PLATFORMS, detectPlatform } = await import('./views/apps.js');
  const { platformLogo } = await import('./platforms.js');
  const here = detectPlatform();
  const known = PLATFORMS.find((x) => x.id === here && !x.web);
  // l'application de cette plateforme est-elle publiée pour ce serveur ?
  let published = new Set();
  try { published = new Set((await (await import('./api.js')).get('/api/apps')).apps.filter((a) => a.available).map((a) => a.id)); } catch { /* ignore */ }
  const p = known && published.has(known.files[0][0]) ? known : null;
  const close = () => document.querySelector('.modal-overlay')?.remove();
  const options = [];
  if (p) {
    const file = p.files[0][0];
    options.push(h('div.install-choice.recommended',
      h('span.app-logo', platformLogo(p.logo, 30), p.tv ? h('span.app-tv', icon('tv')) : null),
      h('div.grow', h('strong', tr('Application Nexis pour {os}', { os: p.name })), h('span.install-tag', tr('Conseillé')),
        h('p.muted.small', tr('Un vrai programme installé : lecteur et téléchargements sans les limites du navigateur, et son interface suit les mises à jour du serveur.'))),
      h('a.btn.primary', { href: `/apps/${file}`, download: '', onclick: () => setTimeout(() => { close(); location.hash = `#/apps`; }, 400) },
        icon('download'), tr('Télécharger'))));
  } else if (ios()) {
    options.push(h('p.small.muted', tr('Apple ne permet pas d’installer une application en dehors de l’App Store : sur iPhone et iPad, Nexis s’installe comme application web.')));
  }
  const webOk = webAppPossible();
  options.push(h('div.install-choice',
    h('span.app-logo', icon('install')),
    h('div.grow', h('strong', tr('Application web')),
      h('p.muted.small', webOk ? tr('Plus légère, installée par le navigateur. Le navigateur fixe la place disponible pour les téléchargements.')
        : tr('Ce navigateur ne sait pas installer d’application web sur cet appareil : prenez l’application native.'))),
    webOk ? h('button.btn', { type: 'button', onclick: () => { close(); installWebApp(); } }, tr('Installer')) : null));
  options.push(h('p.small', h('a', { href: '#/apps', onclick: close }, tr('Toutes les applications : Windows, macOS, Linux, Android, Android TV, iPhone et iPad'))));
  modal(tr('Installer Nexis'), h('div.install-choices', ...options), [{ label: tr('Fermer'), value: false }]);
}

// Application web : invite du navigateur, sinon le chemin dans son menu
export async function installWebApp() {
  if (prompt) {
    const p = prompt;
    prompt = null;
    await p.prompt();
    notify();
    return;
  }
  const ok = [{ label: tr('Compris'), primary: true, value: true }];
  if (firefox() && !ios()) {
    // Firefox : pas d'invite déclenchable par le site, l'installation passe par son menu
    const steps = android()
      ? [h('p', tr('Dans Firefox pour Android :')),
        h('ol',
          h('li', tr('Ouvrir le menu '), h('strong', '⋮'), tr(' (en haut ou en bas à droite).')),
          h('li', tr('Choisir '), h('strong', tr('Ajouter à l’écran d’accueil')), tr(' (ou « Installer »).')),
          h('li', tr('Confirmer avec '), h('strong', tr('Ajouter')), tr('. Nexis s’ouvre ensuite en plein écran, comme une application.')))]
      : windows()
        ? [h('p', tr('Dans Firefox pour Windows (version 143 ou plus récente) :')),
          h('ol',
            h('li', tr('Cliquer sur l’icône '), h('strong', tr('Ajouter à la barre des tâches')), tr(' à droite de la barre d’adresse.')),
            h('li', tr('Nexis s’ouvre alors dans sa propre fenêtre, épinglée à la barre des tâches.'))),
          h('p.muted.small', tr('Pas d’icône ? Mettez Firefox à jour, ou installez Nexis depuis Edge ou Chrome (menu ⋯ → Applications → Installer).'))]
        : [h('p', tr('Firefox ne sait pas encore installer une application web sur cet ordinateur.')),
          h('p', tr('Pour avoir Nexis dans sa propre fenêtre, ouvrez-le dans Chrome, Edge ou Chromium, puis bouton « Installer » de la barre d’adresse. Dans Firefox, vous pouvez épingler l’onglet (clic droit sur l’onglet → Épingler l’onglet).'))];
    modal(tr('Installer Nexis'), h('div.install-steps', ...steps), ok);
    return;
  }
  if (ios()) {
    // iPhone, iPad (Safari, et depuis iOS 16.4 Firefox ou Chrome) : pas d'invite, on montre le chemin
    modal(tr('Installer Nexis'), h('div.install-steps',
      h('p', tr('Sur iPhone et iPad, l’installation se fait depuis le bouton Partager du navigateur :')),
      h('ol',
        h('li', tr('Appuyer sur '), h('strong', tr('Partager')), ' ', icon('share'), tr(' en bas de l’écran (en haut sur iPad).')),
        h('li', tr('Choisir '), h('strong', tr('Sur l’écran d’accueil')), '.'),
        h('li', tr('Appuyer sur '), h('strong', tr('Ajouter')), tr('. Nexis s’ouvre ensuite en plein écran.')))),
    ok);
    return;
  }
  // Chrome, Edge sans invite (déjà refusée, ou critères pas encore remplis) : le menu du navigateur
  modal(tr('Installer Nexis'), h('div.install-steps',
    h('p', tr('Dans Chrome ou Edge : menu ⋮ (ou ⋯) → « Caster, enregistrer et partager » → « Installer la page en tant qu’application », ou l’icône d’installation à droite de la barre d’adresse.'))), ok);
}
