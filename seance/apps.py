"""Applications natives (Windows, macOS, Linux, Android, Android TV) distribuées par le serveur lui-même.

L'intégration continue construit les applications et les joint à la publication GitHub de chaque version (et à la
pré-version « apps-test » pour la branche test). Ce serveur lit la liste des fichiers de la publication qui le
concerne, puis :
  - /api/apps : ce qui s'installe, pour la page Applications et le bouton « Installer » ;
  - /apps/<plateforme> : le fichier à installer — servi depuis la copie gardée par ce serveur, sinon redirigé vers
    GitHub le temps que la copie se fasse (en arrière-plan, au premier téléchargement) ;
  - /apps/desktop/<fichier> : de quoi se mettre à jour pour l'application de bureau (latest*.yml d'electron-updater).

Les applications affichent l'interface de ce serveur : elle suit donc chaque mise à jour du serveur. Le programme
lui-même (lecteur, téléchargements, fenêtre) change rarement ; sa version est indépendante de celle du serveur.
"""
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request

from . import __version__ as VERSION, update
from .web import HTTPError, Response

log = logging.getLogger("seance.apps")

# plateforme -> fichier joint à la publication (noms fixes, voir .github/workflows/tests.yml)
APPS = {
    "windows": "Noxa-Setup.exe",
    "macos": "Noxa.dmg",
    "linux-appimage": "Noxa.AppImage",
    "linux-deb": "noxa-desktop.deb",
    "android": "noxa-android.apk",
    "tv": "noxa-tv.apk",
}
MANIFEST = "noxa-apps.json"  # versions des applications (écrit par l'intégration continue)
# mises à jour de l'application de bureau : fichiers lus par electron-updater
DESKTOP = re.compile(r"^(latest(-mac|-linux)?\.yml|Noxa-Setup\.exe(\.blockmap)?|Noxa\.AppImage|Noxa-mac\.zip(\.blockmap)?)$")
REFRESH = 6 * 3600
API = "https://api.github.com"


class Apps:
    def __init__(self, ctx):
        self.ctx = ctx
        self.dir = os.path.join(ctx.data_dir, "apps")
        self.lock = threading.Lock()
        self.release = None   # {"tag", "assets": {nom: {"url", "size", "updated"}}}
        self.manifest = {}
        self.checked = 0
        self.error = None
        self.copying = set()

    # ------------------------------------------------------------ publication GitHub
    def tag(self):
        try:
            _, _, branch = update.read_source()
        except update.UpdateError:
            branch = "main"
        return "apps-test" if branch == "test" else f"v{VERSION}"

    def refresh(self, force=False):
        if not force and time.time() - self.checked < REFRESH:
            return
        if not self.lock.acquire(blocking=False):
            return
        try:
            try:
                owner, repo, _ = update.read_source()
            except update.UpdateError:
                owner, repo = "xenix13", "Noxa"
            data = None
            for tag in (self.tag(), None):  # cette version, sinon la dernière publiée
                url = (f"{API}/repos/{owner}/{repo}/releases/tags/{tag}" if tag
                       else f"{API}/repos/{owner}/{repo}/releases/latest")
                try:
                    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "Noxa"})
                    with urllib.request.urlopen(req, timeout=10) as r:
                        data = json.loads(r.read(2 * 1024 * 1024))
                    if any(a.get("name") in APPS.values() for a in data.get("assets") or []):
                        break
                except (OSError, ValueError, urllib.error.HTTPError) as e:
                    self.error = str(e)[:200]
                data = None
            if data:
                assets = {a["name"]: {"url": a["browser_download_url"], "size": a.get("size") or 0, "updated": a.get("updated_at")}
                          for a in data.get("assets") or [] if isinstance(a, dict) and a.get("name")}
                self.release = {"tag": data.get("tag_name"), "assets": assets}
                self.error = None
                if MANIFEST in assets:
                    try:
                        with urllib.request.urlopen(assets[MANIFEST]["url"], timeout=10) as r:
                            self.manifest = json.loads(r.read(65536))
                    except (OSError, ValueError):
                        pass
        finally:
            self.checked = time.time()
            self.lock.release()

    def refresh_later(self):
        if time.time() - self.checked >= REFRESH and not self.lock.locked():
            threading.Thread(target=self._bg_refresh, daemon=True, name="apps").start()

    def _bg_refresh(self):
        try:
            self.refresh()
        finally:
            self.ctx.db.release()

    # ------------------------------------------------------------ copie locale
    def local(self, name):
        """Chemin de la copie locale à jour de ce fichier, ou None (et la copie démarre en arrière-plan)."""
        rel = self.release
        if not rel or name not in rel["assets"]:
            return None
        a = rel["assets"][name]
        path = os.path.join(self.dir, rel["tag"] or "release", name)
        if os.path.isfile(path) and os.path.getsize(path) == a["size"]:
            return path
        with self.lock:
            if path in self.copying:
                return None
            self.copying.add(path)
        threading.Thread(target=self._copy, args=(a["url"], path, a["size"]), daemon=True, name="apps-copy").start()
        return None

    def _copy(self, url, path, size):
        tmp = path + ".part"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            req = urllib.request.Request(url, headers={"User-Agent": "Noxa"})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                while True:
                    block = r.read(1 << 20)
                    if not block:
                        break
                    f.write(block)
            if size and os.path.getsize(tmp) != size:
                raise OSError("fichier incomplet")
            os.replace(tmp, path)
            # anciennes publications : effacées
            for d in os.listdir(self.dir):
                if os.path.join(self.dir, d) != os.path.dirname(path):
                    old = os.path.join(self.dir, d)
                    for f in os.listdir(old):
                        os.remove(os.path.join(old, f))
                    os.rmdir(old)
        except OSError as e:
            log.info("Copie de %s impossible : %s", os.path.basename(path), e)
            try:
                os.remove(tmp)
            except OSError:
                pass
        finally:
            with self.lock:
                self.copying.discard(path)

    def serve(self, req, name, download=True):
        rel = self.release
        if not rel or name not in rel["assets"]:
            raise HTTPError(404, "Application pas encore publiée pour cette version")
        path = self.local(name)
        if not path:  # copie en cours : GitHub en attendant
            return Response(b"", 302, "text/plain", {"Location": rel["assets"][name]["url"], "Cache-Control": "no-store"})
        with open(path, "rb") as f:
            data = f.read() if os.path.getsize(path) < 4 * 1024 * 1024 else None
        if data is not None:
            return Response(data, 200, _ctype(name), _disposition(name, download))
        size = os.path.getsize(path)
        f = open(path, "rb")

        def gen():
            try:
                while True:
                    block = f.read(1 << 20)
                    if not block:
                        break
                    yield block
            finally:
                f.close()
        headers = _disposition(name, download)
        return Response(stream=gen(), content_type=_ctype(name), length=size, headers=headers)

    def listing(self):
        rel = self.release or {"tag": None, "assets": {}}
        out = []
        for pid, name in APPS.items():
            a = rel["assets"].get(name)
            out.append({"id": pid, "file": name, "available": bool(a), "size": a["size"] if a else 0, "url": f"/apps/{pid}"})
        return {"release": rel["tag"], "apps": out, "versions": self.manifest, "error": self.error if not self.release else None}


def _ctype(name):
    if name.endswith(".apk"):
        return "application/vnd.android.package-archive"
    if name.endswith(".yml"):
        return "text/yaml; charset=utf-8"
    return "application/octet-stream"


def _disposition(name, download):
    h = {"Cache-Control": "no-cache"}
    if download:
        h["Content-Disposition"] = f'attachment; filename="{name}"'
    return h


def register(app, ctx, x):
    apps = ctx.apps

    @app.route("GET", "/api/apps", auth=None)
    def apps_list(req):
        """Applications à installer (page Applications, bouton « Installer ») : lisible sans être connecté."""
        if apps.release is None:
            apps.refresh()  # premier appel : on attend la réponse de GitHub (10 s au plus)
        else:
            apps.refresh_later()
        return apps.listing()

    @app.route("GET", r"/apps/(?P<pid>[a-z-]{2,20})", auth=None)
    def app_file(req):
        name = APPS.get(req.params["pid"])
        if not name:
            raise HTTPError(404, "Application inconnue")
        if apps.release is None:
            apps.refresh()
        return apps.serve(req, name)

    @app.route("GET", r"/apps/desktop/(?P<name>[A-Za-z0-9._-]{3,60})", auth=None)
    def desktop_update(req):
        """Mises à jour de l'application de bureau (fournisseur « generic » d'electron-updater)."""
        name = req.params["name"]
        if not DESKTOP.match(name):
            raise HTTPError(404, "Introuvable")
        if apps.release is None:
            apps.refresh()
        else:
            apps.refresh_later()
        return apps.serve(req, name, download=False)
