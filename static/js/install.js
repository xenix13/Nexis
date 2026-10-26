// Installation de Nexis comme application (web app) : invite native de Chrome / Edge / Android,
// instructions pour iPhone et iPad (Safari n'a pas d'invite), rien quand c'est déjà installé.
import { h, icon, modal } from './ui.js';

let prompt = null;
const listeners = new Set();
const notify = () => listeners.forEach((fn) => fn());

export const standalone = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
const ios = () => /iphone|ipad|ipod/i.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1); // iPad qui se présente comme un Mac

export function setupInstall() {
  if ('serviceWorker' in navigator && window.isSecureContext) {
    navigator.serviceWorker.register('/sw.js').catch((e) => console.warn('Service worker :', e));
  }
  window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); prompt = e; notify(); });
  window.addEventListener('appinstalled', () => { prompt = null; notify(); });
}

// L'entrée « Installer l'application » a-t-elle un sens ici ?
export function canInstall() {
  if (standalone()) return false;
  return Boolean(prompt) || (ios() && window.isSecureContext);
}

export function onInstallChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }

export async function install() {
  if (prompt) {
    const p = prompt;
    prompt = null;
    await p.prompt();
    notify();
    return;
  }
  // Safari (iPhone, iPad) : pas d'invite, on montre le chemin
  modal('Installer Nexis', h('div.install-steps',
    h('p', 'Sur iPhone et iPad, l’installation se fait depuis Safari :'),
    h('ol',
      h('li', 'Appuyer sur ', h('strong', 'Partager'), ' ', icon('share'), ' en bas de l’écran (en haut sur iPad).'),
      h('li', 'Choisir ', h('strong', 'Sur l’écran d’accueil'), '.'),
      h('li', 'Appuyer sur ', h('strong', 'Ajouter'), '. Nexis s’ouvre ensuite en plein écran.'))),
  [{ label: 'Compris', primary: true, value: true }]);
}
