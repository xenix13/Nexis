// Nexis pour Windows, macOS et Linux.
// La fenêtre affiche l'interface du serveur Nexis choisi au premier lancement : elle suit donc chaque mise à jour du
// serveur. Le moteur est celui de l'application (Chromium), le même partout : pas de différence entre navigateurs.
// Le programme lui-même se met à jour depuis le serveur (/apps/desktop, fichiers publiés avec chaque version).
const { app, BrowserWindow, Menu, dialog, ipcMain, net, powerSaveBlocker, shell } = require('electron');
const fs = require('fs');
const path = require('path');
const { strings, fill } = require('./strings');

const CONFIG = path.join(app.getPath('userData'), 'nexis.json');
const readConfig = () => { try { return JSON.parse(fs.readFileSync(CONFIG, 'utf8')); } catch { return {}; } };
const writeConfig = (c) => { fs.mkdirSync(path.dirname(CONFIG), { recursive: true }); fs.writeFileSync(CONFIG, JSON.stringify(c, null, 1)); };
let config = readConfig();
const originOf = (u) => { try { return new URL(u).origin; } catch { return null; } };

// Serveur en http sur le réseau local : traité comme sûr par le moteur (service worker, téléchargements hors ligne),
// ce qu'aucun navigateur ne fait. Seulement pour ce serveur-là, choisi par la personne.
if (config.server && config.server.startsWith('http://') && originOf(config.server)) {
  app.commandLine.appendSwitch('unsafely-treat-insecure-origin-as-secure', originOf(config.server));
}

let win = null;
let T = strings('fr');

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });
}

function createWindow() {
  const b = config.bounds || {};
  win = new BrowserWindow({
    width: b.width || 1280, height: b.height || 800, x: b.x, y: b.y, minWidth: 720, minHeight: 480,
    backgroundColor: '#0b0b0e', title: 'Nexis', autoHideMenuBar: true, show: false,
    icon: path.join(__dirname, 'build', 'icon.png'),
    // backgroundThrottling : fenêtre réduite ou cachée, la page continue au même rythme (téléchargements)
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, sandbox: true, spellcheck: false,
      backgroundThrottling: false },
  });
  if (config.maximized) win.maximize();
  win.once('ready-to-show', () => win.show());
  win.on('close', () => {
    config = { ...readConfig(), bounds: win.getNormalBounds(), maximized: win.isMaximized() };
    writeConfig(config);
  });
  const home = originOf(config.server);
  // liens vers un autre site : dans le navigateur de l'ordinateur ; le reste reste dans l'application
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (home && originOf(url) === home) return { action: 'allow' };
    if (/^https?:/.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (e, url) => {
    if (url.startsWith('file:') || (home && originOf(url) === home)) return;
    e.preventDefault();
    if (/^https?:/.test(url)) shell.openExternal(url);
  });
  win.webContents.on('did-fail-load', (_e, code, desc, url, isMain) => {
    if (!isMain || code === -3) return; // -3 : chargement interrompu (navigation suivante)
    win.loadFile('setup.html', { query: { offline: config.server || '', error: desc || String(code) } });
  });
  // F11 / Cmd+Ctrl+F : plein écran ; F5 / Ctrl+R : recharger
  win.webContents.on('before-input-event', (e, input) => {
    if (input.type !== 'keyDown') return;
    if (input.key === 'F11') { win.setFullScreen(!win.isFullScreen()); e.preventDefault(); }
    if (input.key === 'F5' || ((input.control || input.meta) && input.key.toLowerCase() === 'r')) { win.webContents.reload(); e.preventDefault(); }
  });
  if (config.server) win.loadURL(config.server);
  else win.loadFile('setup.html');
}

// ------------------------------------------------------------------ choix du serveur
async function probe(base) {
  const res = await net.fetch(`${base}/api/status`, { headers: { 'X-Seance': '1' }, signal: AbortSignal.timeout(8000) });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const j = await res.json();
  if (!j || typeof j.version !== 'string') throw new Error('not nexis');
  return j;
}

async function setServer(raw) {
  let s = String(raw || '').trim().replace(/\/+$/, '');
  if (!s) return { ok: false, error: T.notNexis };
  const candidates = /^https?:\/\//i.test(s) ? [s] : [`https://${s}`, `http://${s}`];
  let last = null;
  for (const c of candidates) {
    if (!originOf(c)) continue;
    try {
      await probe(c);
      const insecureBefore = (config.server || '').startsWith('http://') ? originOf(config.server) : null;
      config = { ...readConfig(), server: originOf(c) + (new URL(c).pathname.replace(/\/+$/, '')) };
      writeConfig(config);
      // un serveur http doit être déclaré au démarrage du moteur : on relance l'application
      if (config.server.startsWith('http://') && originOf(config.server) !== insecureBefore) {
        app.relaunch();
        app.exit(0);
        return { ok: true };
      }
      win.loadURL(config.server);
      setupUpdates();
      return { ok: true };
    } catch (e) {
      last = e;
    }
  }
  return { ok: false, error: last && last.message === 'not nexis' ? T.notNexis : fill(T.unreachable, { error: last ? last.message : '?' }) };
}

ipcMain.handle('nexis:set-server', (_e, url) => setServer(url));
ipcMain.handle('nexis:change-server', () => { win.loadFile('setup.html', { query: { change: '1', current: config.server || '' } }); });
ipcMain.handle('nexis:retry', () => { if (config.server) win.loadURL(config.server); });
ipcMain.handle('nexis:check-update', () => checkUpdates(true));
// téléchargement en cours : l'ordinateur ne se met pas en veille (l'écran, lui, peut s'éteindre)
let blocker = null;
ipcMain.on('nexis:downloads', (_e, json) => {
  let active = 0;
  let percent = 0;
  try { ({ active = 0, percent = 0 } = JSON.parse(json)); } catch { /* ignore */ }
  if (active > 0 && blocker === null) blocker = powerSaveBlocker.start('prevent-app-suspension');
  if (active <= 0 && blocker !== null) { powerSaveBlocker.stop(blocker); blocker = null; }
  if (win) win.setProgressBar(active > 0 ? Math.max(0.01, percent / 100) : -1);
});
ipcMain.on('nexis:info', (e) => { e.returnValue = { version: app.getVersion(), os: process.platform, server: config.server || null, strings: T }; });

// ------------------------------------------------------------------ mises à jour du programme
// Windows (installateur) et Linux (AppImage) : téléchargées depuis le serveur et installées au redémarrage.
// macOS et .deb : sans signature Apple ou paquet géré par le système, on propose le téléchargement.
let updater = null;
function canSelfUpdate() {
  return process.platform === 'win32' || (process.platform === 'linux' && !!process.env.APPIMAGE);
}

function setupUpdates() {
  if (!config.server || !app.isPackaged) return;
  if (updater) { updater.setFeedURL({ provider: 'generic', url: `${config.server}/apps/desktop` }); checkUpdates(false); return; }
  if (canSelfUpdate()) {
    try {
      ({ autoUpdater: updater } = require('electron-updater'));
      updater.setFeedURL({ provider: 'generic', url: `${config.server}/apps/desktop` });
      updater.autoDownload = true;
      updater.on('update-downloaded', async () => {
        const r = await dialog.showMessageBox(win, { type: 'info', buttons: [T.restart, T.later], defaultId: 0, cancelId: 1,
          message: T.updateReady, detail: T.updateReadyDetail });
        if (r.response === 0) updater.quitAndInstall();
      });
      updater.on('error', () => { /* serveur sans applications publiées : on réessaiera */ });
    } catch { updater = null; }
  }
  checkUpdates(false);
}

const newer = (a, b) => {
  const pa = String(a).split('.').map(Number); const pb = String(b).split('.').map(Number);
  for (let i = 0; i < 3; i++) if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) > (pb[i] || 0);
  return false;
};

async function checkUpdates(manual) {
  if (!config.server) return;
  if (updater) {
    try { const r = await updater.checkForUpdates(); if (manual && (!r || !r.isUpdateAvailable)) dialog.showMessageBox(win, { message: fill(T.upToDate, { version: app.getVersion() }) }); } catch { /* ignore */ }
    return;
  }
  try {
    const res = await net.fetch(`${config.server}/api/apps`, { signal: AbortSignal.timeout(10000) });
    const j = await res.json();
    const latest = j && j.versions && j.versions.desktop;
    if (latest && newer(latest, app.getVersion())) {
      const id = process.platform === 'darwin' ? 'macos' : 'linux-deb';
      const r = await dialog.showMessageBox(win, { type: 'info', buttons: [T.download, T.later], defaultId: 0, cancelId: 1,
        message: fill(T.updateAvailable, { version: latest }), detail: T.updateManual });
      if (r.response === 0) shell.openExternal(`${config.server}/apps/${id}`);
    } else if (manual) {
      dialog.showMessageBox(win, { message: fill(T.upToDate, { version: app.getVersion() }) });
    }
  } catch { /* hors ligne */ }
}

// ------------------------------------------------------------------ menu
function buildMenu() {
  const mac = process.platform === 'darwin';
  const template = [
    ...(mac ? [{ role: 'appMenu' }] : []),
    { label: T.menuServer, submenu: [
      { label: T.changeServer, click: () => win && win.loadFile('setup.html', { query: { change: '1', current: config.server || '' } }) },
      { label: T.menuCheckUpdate, click: () => checkUpdates(true) },
      { type: 'separator' },
      mac ? { role: 'close' } : { role: 'quit' },
    ] },
    { label: T.menuEdit, submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] },
    { label: T.menuView, submenu: [{ role: 'reload' }, { role: 'togglefullscreen' }, { type: 'separator' }, { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { type: 'separator' }, { role: 'toggleDevTools' }] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.whenReady().then(() => {
  T = strings(app.getLocale());
  buildMenu();
  createWindow();
  setupUpdates();
  setInterval(() => checkUpdates(false), 6 * 3600 * 1000);
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
