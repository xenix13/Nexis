"""Nexis — assemblage du serveur : contexte partagé (base, scanner, lecture…) et application HTTP.

Les routes sont rangées par thème dans seance/routes/ (comptes, bibliothèque, lecture, administration) et
dans les modules des fonctions (collections_api, discover, tasks, livetv, syncplay).
"""
import hmac
import logging
import os
import secrets
import threading
import time
import types

from . import __version__, catalog, collections_api, discover, federation, i18n, livetv, media, notify, suggest, syncplay, tasks, update
from .auth import Throttle, load_user
from .chapters import ChapterImages
from .db import DB
from .downloads import Downloads
from .images import Images
from .introdetect import FingerprintCache
from .metadata import Metadata
from .ratings import age_of
from .routes import account, admin, library
from .routes import media as media_routes
from .scanner import Scanner
from .subcache import SubtitleCache, pgs_tracks, text_tracks
from .transcoder import Transcoder
from .watcher import Watcher
from .web import App, HTTPError

log = logging.getLogger("seance")


class Ctx:
    def __init__(self, data_dir, static_dir, cookie_secure=False, trust_proxy=False):
        self.data_dir = data_dir
        self.static_dir = static_dir
        self.cookie_secure = cookie_secure
        self.trust_proxy = trust_proxy
        for d in ("cache/img", "cache/orig", "cache/subs"):
            os.makedirs(os.path.join(data_dir, d), exist_ok=True)
        self.db = DB(os.path.join(data_dir, "seance.db"))
        self.throttle = Throttle()
        self.lock = threading.Lock()
        self.metadata = Metadata(self)
        self.images = Images(self)
        self.scanner = Scanner(self)
        self.transcoder = Transcoder(os.path.join(data_dir, "transcodes"), self.limits, self.hw)
        self.downloads = Downloads(self)
        self.subtitles = SubtitleCache(self)
        self.chapter_images = ChapterImages(self)
        self.fingerprints = FingerprintCache(os.path.join(data_dir, "cache", "fingerprints"))
        self.watcher = Watcher(self)
        self.tasks = tasks.TaskManager(self)
        self.live = livetv.LiveTV(self)
        self.together = syncplay.Groups(self)
        self.fed = federation.Federation(self)
        for r in self.db.all("SELECT id, content_rating FROM items WHERE content_rating IS NOT NULL AND age IS NULL"):
            age = age_of(r["content_rating"])
            if age is not None:
                self.db.run("UPDATE items SET age=? WHERE id=?", (age, r["id"]))
        i18n.set_server_default(i18n.default_language(self.db))
        if not self.db.setting("webhook_token"):
            self.db.set_setting("webhook_token", secrets.token_urlsafe(24))
        self.sub_locks = {}
        self.update_latest = None  # version plus récente trouvée sur GitHub (indicateur pour les administrateurs)
        self.update_checked = 0
        self.setup_code = None
        if not self.db.scalar("SELECT COUNT(*) FROM users"):
            self.setup_code = secrets.token_hex(4).upper()
            path = os.path.join(data_dir, "setup-code.txt")
            with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
                f.write(self.setup_code + "\n")
            log.warning("Premier lancement : code d'installation = %s (aussi dans %s)", self.setup_code, path)
        if not media.available():
            log.error("ffmpeg/ffprobe introuvables : installe-les (apt install ffmpeg)")
        self.db.release()

    def note_latest(self, latest):
        self.update_latest = latest if latest and update.newer(latest, __version__) else None

    def check_update(self):
        """Vérification en arrière-plan (toutes les 6 h) : l'indicateur de la barre du haut."""
        try:
            self.note_latest(update.check_latest()["latest"])
        except update.UpdateError as e:
            log.debug("Vérification de mise à jour : %s", e)

    def setting_int(self, key, default):
        try:
            return int(self.db.setting(key, default))
        except (TypeError, ValueError):
            return default

    def limits(self):
        cpus = os.cpu_count() or 2
        return (self.setting_int("max_streams", 6),
                self.setting_int("max_transcodes", 1 if cpus <= 2 else 2))

    def viewing(self):
        """Quelqu'un regarde-t-il en ce moment (lecture signalée il y a moins de 40 s) ?"""
        try:
            return bool(self.db.scalar("SELECT 1 FROM streams WHERE last_seen > ? LIMIT 1", (int(time.time()) - 40,)))
        finally:
            self.db.release()

    def hw(self):
        """Transcodage matériel choisi par l'administrateur, ou None (processeur)."""
        engine = self.db.setting("hw_engine", "none")
        if engine not in ("nvenc", "vaapi"):
            return None
        return {"engine": engine, "device": self.db.setting("hw_device", "/dev/dri/renderD128")}

    def on_library_changed(self):
        try:
            self.fed.relink_all()  # nouveaux titres : fusion avec ceux des serveurs liés
        except Exception:
            log.exception("Fusion avec les serveurs liés")
        try:
            self.tasks.after_scan(self.scanner.status().get("added", 0))
        except Exception:
            log.exception("Tâches après le scan")

    def prefetch_missing_subtitles(self):
        """Au démarrage : met en file les fichiers dont les sous-titres internes ne sont pas encore prêts."""
        if self.db.setting("subs_prefetch", "1") != "1" and self.db.setting("trickplay", "1") != "1":
            return
        n = 0
        for r in self.db.all("SELECT id, key, probe, file_size, file_mtime, duration_ms FROM items WHERE type IN ('movie','episode') "
                             "AND peer_id IS NULL ORDER BY added_at DESC"):
            if self.subtitles.has_work(r):
                self.subtitles.enqueue(r["id"])
                n += 1
        if n:
            log.info("%d fichier(s) en file pour la préparation des sous-titres", n)
        self.db.release()

    def on_new_media(self, item_id, info):
        """Nouveau fichier (ou fichier remplacé) : ses sous-titres sont exportés dès le scan, en tâche de fond."""
        if (self.db.setting("subs_prefetch", "1") == "1" and (text_tracks(info) or pgs_tracks(info))) or \
                (self.db.setting("trickplay", "1") == "1" and info.get("video")):
            self.subtitles.enqueue(item_id)


def build_app(ctx):
    """Assemble l'application : contrôles d'accès communs, catalogue partagé, puis les routes par thème."""
    x = types.SimpleNamespace()  # outils partagés entre les modules de routes
    app = App()
    app.static_dir = ctx.static_dir
    db = ctx.db
    app.teardown.append(db.release)
    # langue des messages : d'abord celle de l'interface (en-tête), précisée par le compte une fois connu
    app.begin = lambda req: i18n.set_lang((req.headers.get("X-Lang") or "").strip())
    app.translate = i18n.tr
    app.default_lang = lambda: i18n.default_language(db)

    def setup_allowed(req):
        if req.user and req.user["is_admin"]:
            return True
        code = req.headers.get("X-Setup-Code") or ""
        return bool(ctx.setup_code) and hmac.compare_digest(code.strip().upper(), ctx.setup_code)

    def before(req, opts):
        auth = opts.get("auth", "user")
        if auth == "webhook":
            token = req.arg("token") or ""
            if not hmac.compare_digest(token, db.setting("webhook_token") or "\0"):
                raise HTTPError(403, "Jeton invalide")
            return
        # routes entre serveurs liés : uniquement sur leur port, par un serveur jumelé (ou avec une invitation)
        if auth == "peer" and not req.fed_peer or auth == "join" and not req.fed_listener:
            raise HTTPError(404, "Introuvable")
        if auth in ("peer", "join"):
            return
        if req.fed_peer:
            # requête d'un serveur lié au nom d'un compte (le même, répliqué) : la lecture d'un élément de ce
            # serveur depuis l'autre. Pas de cookie ni d'en-tête anti-CSRF : la requête est signée par le serveur.
            if not federation.PEER_USER_PATHS.match(req.path):
                raise HTTPError(403, "Non autorisé entre serveurs liés")
            req.user = ctx.fed.user_for(req)
        else:
            # protection CSRF : un autre site ne peut pas ajouter cet en-tête sans l'accord du serveur (CORS)
            if req.method != "GET" and req.headers.get("X-Seance") != "1":
                raise HTTPError(403, "En-tête de requête manquant")
            req.user = load_user(db, req)
        i18n.set_lang(i18n.request_language(req, db))
        if auth == "user" and not req.user:
            raise HTTPError(401, "Connexion requise")
        if auth == "admin" and not (req.user and req.user["is_admin"]):
            raise HTTPError(403 if req.user else 401, "Réservé aux administrateurs")
        if auth == "setup":
            ctx.throttle.check("setup:" + req.client_ip)
            if not setup_allowed(req):
                ctx.throttle.fail("setup:" + req.client_ip)
                raise HTTPError(403, "Code d'installation invalide")
        if not req.fed_peer:
            # élément d'un serveur lié : la requête lui est relayée (ou il est prévenu)
            return ctx.fed.intercept(req)
    app.before.append(before)

    catalog.install(ctx, x)
    for module in (account, library, media_routes, admin, collections_api, discover, tasks, livetv, syncplay, suggest, notify,
                   federation):
        module.register(app, ctx, x)
    return app


def janitor(ctx, stop_event):
    """Arrête les lectures abandonnées et purge les sessions (le reste est dans les tâches planifiées)."""
    while not stop_event.wait(30):
        db = ctx.db
        try:
            now = int(time.time())
            for sid in ctx.transcoder.idle(150):
                ctx.transcoder.stop(sid)
            db.run("DELETE FROM streams WHERE last_seen < ?", (now - 600,))
            db.run("DELETE FROM sessions WHERE expires_at < ?", (now,))
            ctx.live.reap()
            ctx.together.reap()
            ctx.downloads.reap()
            if time.time() - ctx.update_checked > 6 * 3600:
                ctx.update_checked = time.time()
                threading.Thread(target=ctx.check_update, daemon=True).start()
        except Exception:
            log.exception("Erreur du nettoyage périodique")
        finally:
            db.release()
