"""Alertes pour les administrateurs : ce qui demande d'intervenir sur le serveur.

Vérifié toutes les 5 minutes (en tâche de fond, sans gêner les requêtes) :
- disque presque plein (données de Noxa, sauvegardes, dossiers des bibliothèques) ;
- dossier d'une bibliothèque inaccessible ou vide alors qu'il contenait des titres (partage réseau démonté) ;
- serveur lié injoignable depuis plus de 15 minutes ;
et, au moment où cela arrive : tâche planifiée en échec.

Chaque alerte part en notification push vers les administrateurs (une fois, puis une fois par jour si le
problème dure) et s'affiche dans Paramètres → Vue d'ensemble jusqu'à ce que le problème disparaisse.
"""
import json
import logging
import os
import shutil
import threading
import time

from .i18n import tr

log = logging.getLogger("seance.health")

EVERY = 300
REMIND = 86400
PEER_GRACE = 900
KINDS = ("disk", "folder", "peer", "task")
DEFAULTS = {"disk": True, "folder": True, "peer": True, "task": True, "disk_gb": 10}


class Health:
    def __init__(self, ctx):
        self.ctx = ctx
        self.last = 0
        self.running = threading.Lock()

    def conf(self):
        try:
            c = json.loads(self.ctx.db.setting("alerts") or "{}")
        except ValueError:
            c = {}
        return {**DEFAULTS, **{k: v for k, v in c.items() if k in DEFAULTS}}

    def set_conf(self, b):
        c = self.conf()
        for k in KINDS:
            if k in b:
                c[k] = bool(b[k])
        if "disk_gb" in b:
            v = b["disk_gb"]
            if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 5000:
                raise ValueError("disk_gb")
            c["disk_gb"] = v
        self.ctx.db.set_setting("alerts", json.dumps(c))
        for k in KINDS:  # type d'alerte coupé : ses alertes en cours disparaissent
            if not c[k]:
                self.ctx.db.run("UPDATE alerts SET resolved_at=? WHERE kind=? AND resolved_at IS NULL", (int(time.time()), k))
        return c

    # ------------------------------------------------------------ alertes
    def raise_alert(self, key, kind, title, body="", url="#/admin/dashboard"):
        """Problème constaté : enregistré, et notifié aux administrateurs s'il est nouveau (ou rappel quotidien)."""
        if not self.conf().get(kind):
            return False
        db = self.ctx.db
        now = int(time.time())
        cur = db.one("SELECT * FROM alerts WHERE key=?", (key,))
        fresh = not cur or cur["resolved_at"] is not None
        if fresh:
            db.run("INSERT INTO alerts(key, kind, title, body, url, created_at, notified_at) VALUES(?,?,?,?,?,?,?) "
                   "ON CONFLICT(key) DO UPDATE SET kind=excluded.kind, title=excluded.title, body=excluded.body, "
                   "url=excluded.url, created_at=excluded.created_at, notified_at=excluded.notified_at, resolved_at=NULL",
                   (key, kind, title, body, url, now, now))
        else:
            db.run("UPDATE alerts SET title=?, body=? WHERE key=?", (title, body, key))
            if now - cur["notified_at"] < REMIND:
                return False
            db.run("UPDATE alerts SET notified_at=? WHERE key=?", (now, key))
        log.warning("Alerte : %s — %s", title, body)
        for u in db.all("SELECT id, language FROM users WHERE is_admin=1 AND disabled=0"):
            lang = u["language"]
            self.ctx.push.notify(u["id"], tr(title, lang), tr(body, lang) if body else "", url, tag=f"alert-{key}"[:60])
        return True

    def resolve(self, key):
        self.ctx.db.run("UPDATE alerts SET resolved_at=? WHERE key=? AND resolved_at IS NULL", (int(time.time()), key))

    def active(self):
        return self.ctx.db.all("SELECT key, kind, title, body, url, created_at FROM alerts WHERE resolved_at IS NULL "
                               "ORDER BY created_at DESC")

    # ------------------------------------------------------------ vérifications
    def tick(self):
        """Appelé par le nettoyage périodique : vérifie en tâche de fond, au plus toutes les 5 minutes."""
        if time.time() - self.last < EVERY or not self.running.acquire(blocking=False):
            return
        self.last = time.time()

        def run():
            try:
                self.check()
            except Exception:
                log.exception("Vérification de l'état du serveur")
            finally:
                self.ctx.db.release()
                self.running.release()
        # un partage réseau figé peut bloquer longtemps une lecture de dossier : jamais dans le fil du nettoyage
        threading.Thread(target=run, daemon=True, name="health").start()

    def check(self):
        c = self.conf()
        if c["disk"]:
            self.check_disks(c["disk_gb"])
        if c["folder"]:
            self.check_folders()
        if c["peer"]:
            self.check_peers()

    def check_disks(self, min_gb):
        from . import backup
        db = self.ctx.db
        paths = [("data", self.ctx.data_dir), ("backups", backup.directory(self.ctx))]
        for lib in db.all("SELECT name, paths FROM libraries WHERE peer_id IS NULL"):
            for p in json.loads(lib["paths"] or "[]"):
                paths.append((lib["name"], p))
        seen = {}
        for label, p in paths:
            if not p or not os.path.isdir(p):
                continue
            try:
                dev = os.stat(p).st_dev
                if dev in seen:
                    continue
                u = shutil.disk_usage(p)
            except OSError:
                continue
            seen[dev] = p
            key = f"disk:{dev}"
            free_gb = u.free / 1e9
            # les bibliothèques sont en lecture seule : un disque de films plein ne gêne pas Noxa, sauf le sien
            if label in ("data", "backups") and (free_gb < min_gb or u.free < u.total * 0.03):
                self.raise_alert(key, "disk", "Disque presque plein",
                                 f"{p} : {free_gb:.1f} Go libres sur {u.total / 1e9:.0f} Go", "#/admin/backups")
            else:
                self.resolve(key)

    def check_folders(self):
        db = self.ctx.db
        for lib in db.all("SELECT id, name, paths FROM libraries WHERE peer_id IS NULL"):
            has_items = db.scalar("SELECT 1 FROM items WHERE library_id=? LIMIT 1", (lib["id"],))
            for p in json.loads(lib["paths"] or "[]"):
                key = f"folder:{lib['id']}:{p}"
                try:
                    ok = os.path.isdir(p) and (not has_items or any(True for _ in os.scandir(p)))
                except OSError:
                    ok = False
                if ok:
                    self.resolve(key)
                else:
                    self.raise_alert(key, "folder", "Dossier de bibliothèque inaccessible",
                                     f"{lib['name']} : {p} (partage réseau démonté ?)", "#/admin/libraries")

    def check_peers(self):
        db = self.ctx.db
        now = int(time.time())
        for p in db.all("SELECT id, name, online, last_seen, error FROM peers"):
            key = f"peer:{p['id']}"
            if not p["online"] and now - (p["last_seen"] or 0) > PEER_GRACE:
                self.raise_alert(key, "peer", "Serveur lié injoignable",
                                 f"{p['name']}" + (f" : {p['error']}" if p["error"] else ""), "#/admin/federation")
            else:
                self.resolve(key)

    def task_finished(self, task, status, summary):
        """Fin d'une tâche planifiée : alerte si elle a échoué, et l'alerte précédente disparaît si elle réussit."""
        key = f"task:{task.id}"
        if status == "error":
            self.raise_alert(key, "task", "Tâche en échec", f"{tr(task.name)} : {summary}", "#/admin/tasks")
        elif status == "ok":
            self.resolve(key)


def register(app, ctx, x):
    from .web import HTTPError

    def view():
        return {"conf": ctx.health.conf(), "alerts": [dict(a, title=tr(a["title"]), body=tr(a["body"]) if a["body"] else "")
                                                      for a in ctx.health.active()]}

    @app.route("GET", "/api/admin/alerts", auth="admin")
    def alerts(req):
        return view()

    @app.route("PUT", "/api/admin/alerts", auth="admin")
    def alerts_conf(req):
        try:
            ctx.health.set_conf(req.json())
        except ValueError:
            raise HTTPError(400, "Seuil d'espace libre invalide (1 à 5000 Go)")
        return view()

    @app.route("POST", "/api/admin/alerts/check", auth="admin")
    def alerts_check(req):
        ctx.health.check()
        return view()

    @app.route("POST", "/api/admin/alerts/test", auth="admin")
    def alerts_test(req):
        n = ctx.push.notify(req.user["id"], tr("Alerte de test"), tr("Les alertes du serveur arriveront ainsi."),
                            "#/admin/dashboard", tag="alert-test")
        return {"sent": n}
