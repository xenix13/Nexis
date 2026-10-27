// Suivi d'une mise à jour lancée depuis l'administration : progression par étapes, attente du redémarrage
// (le serveur, ou le proxy devant lui, ne répond plus quelques secondes), puis rechargement automatique.
// L'état est gardé dans sessionStorage : si la page est rechargée pendant l'installation, le suivi reprend.
import { get } from './api.js';
import { h, toast } from './ui.js';
import { tr } from './i18n.js';

const KEY = 'nexis-update';
const LIMIT = 15 * 60 * 1000;

function saved() {
  try {
    const v = JSON.parse(sessionStorage.getItem(KEY) || 'null');
    return v && Date.now() - v.at < LIMIT ? v : null;
  } catch { return null; }
}
function save(v) { try { if (v) sessionStorage.setItem(KEY, JSON.stringify(v)); else sessionStorage.removeItem(KEY); } catch { /* ignore */ } }

export const updatePending = () => !!saved();

let overlay = null;

// Avant de recharger : on retélécharge chaque fichier de l'interface (le cache du navigateur pourrait sinon
// resservir l'ancienne version), et on prévient le service worker qu'il y en a un nouveau.
async function refreshShell() {
  try {
    const code = await (await fetch('/sw.js', { cache: 'reload' })).text();
    const m = /^const SHELL = (.*);$/m.exec(code);
    const files = m ? JSON.parse(m[1]) : [];
    await Promise.all(files.map((u) => fetch(u, { cache: 'reload' }).catch(() => null)));
    const reg = navigator.serviceWorker && await navigator.serviceWorker.getRegistration();
    if (reg) await reg.update();
  } catch { /* au pire, rechargement simple */ }
}

// from : version installée au lancement
export function followUpdate(from) {
  const st = saved() || { from, at: Date.now() };
  save(st);
  if (overlay) return;
  const bar = h('div.update-bar-fill');
  const pct = h('span.update-pct', '0 %');
  const step = h('p.update-step', tr('Démarrage…'));
  const log = h('pre.log');
  const details = h('details.update-log', h('summary.small', tr('Journal de l’installation')), log);
  const actions = h('div.modal-actions');
  overlay = h('div.update-overlay', { role: 'dialog', 'aria-modal': 'true', 'aria-label': tr('Mise à jour de Nexis') },
    h('div.update-box', h('h2', tr('Mise à jour de Nexis')),
      h('p.muted.small', tr('Cette page se rechargera automatiquement à la fin de l’installation.')),
      h('div.update-bar', bar), h('div.update-row', step, pct), details, actions));
  document.body.append(overlay);

  let percent = 0;
  let seenRunning = false;
  const show = (p, text) => {
    percent = Math.max(percent, p);
    bar.style.width = `${percent}%`;
    pct.textContent = `${Math.round(percent)} %`;
    step.textContent = text;
  };
  const finish = async (text) => {
    save(null);
    show(100, text);
    await refreshShell();
    setTimeout(() => location.reload(), 600);
  };
  const fail = (text) => {
    save(null);
    step.textContent = text;
    overlay.classList.add('failed');
    details.open = true;
    actions.replaceChildren(h('button.btn', { onclick: () => { overlay.remove(); overlay = null; } }, tr('Fermer')));
  };
  show(2, tr('Démarrage…'));

  const tick = async () => {
    let up = null;
    try { up = await get('/api/status'); } catch { /* redémarrage en cours */ }
    if (up && up.version !== st.from) { finish(tr('Nexis {version} est installé. Rechargement…', { version: up.version })); return; }
    if (up) {
      try {
        const u = await get('/api/admin/update');
        if (u.log) log.textContent = u.log.join('\n');
        if (u.running) seenRunning = true;
        // (un journal « terminé » vu avant que l'installation n'ait démarré est celui de la fois précédente)
        if (u.done && !u.running && seenRunning) {
          if (u.ok) { finish(tr('Mise à jour terminée. Rechargement…')); return; }
          fail(tr('La mise à jour a échoué : le détail est dans le journal ci-dessous.'));
          return;
        }
        if (u.running) show(u.percent || 3, `${u.step || tr('Installation')}…`);
      } catch { /* l'assistant système redémarre lui aussi */ }
    } else {
      seenRunning = true; // serveur coupé : c'est bien l'installation en cours qui le redémarre
      show(85, tr('Redémarrage de Nexis…'));
    }
    if (Date.now() - st.at > LIMIT) {
      fail(tr('Mise à jour non terminée après 15 minutes. Le journal se trouve dans /var/log/seance-update.log sur le serveur.'));
      return;
    }
    setTimeout(tick, 2000);
  };
  setTimeout(tick, 1500);
}

// Au chargement de la page : reprend le suivi d'une mise à jour en cours, ou annonce celle qui vient de finir
export async function resumeUpdate(status) {
  const st = saved();
  if (!st) return false;
  if (status && status.version !== st.from) {
    save(null);
    toast(tr('Nexis {version} est installé', { version: status.version }), 'ok');
    return false;
  }
  followUpdate(st.from);
  return true;
}
