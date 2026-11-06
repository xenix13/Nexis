// Installation de Nexis comme application (web app) : invite native de Chrome / Edge / Android ; Safari et
// Firefox n'en ont pas : on montre le chemin (menu du navigateur). Rien quand c'est déjà installé.
import { h, icon, modal } from './ui.js';
import { tr } from './i18n.js';

let prompt = null;
const listeners = new Set();
const notify = () => listeners.forEach((fn) => fn());

export const standalone = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
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

// L'entrée « Installer l'application » a-t-elle un sens ici ?
export function canInstall() {
  if (standalone()) return false;
  return Boolean(prompt) || ((ios() || firefox()) && window.isSecureContext);
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
  // iPhone, iPad (Safari, et depuis iOS 16.4 Firefox ou Chrome) : pas d'invite, on montre le chemin
  modal(tr('Installer Nexis'), h('div.install-steps',
    h('p', tr('Sur iPhone et iPad, l’installation se fait depuis le bouton Partager du navigateur :')),
    h('ol',
      h('li', tr('Appuyer sur '), h('strong', tr('Partager')), ' ', icon('share'), tr(' en bas de l’écran (en haut sur iPad).')),
      h('li', tr('Choisir '), h('strong', tr('Sur l’écran d’accueil')), '.'),
      h('li', tr('Appuyer sur '), h('strong', tr('Ajouter')), tr('. Nexis s’ouvre ensuite en plein écran.')))),
  ok);
}
