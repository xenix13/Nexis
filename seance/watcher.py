"""Détection des changements dans les bibliothèques.

Sur un partage SMB, Linux n'est pas prévenu des fichiers ajoutés depuis une autre machine
(inotify ne voit que les écritures locales). On compare donc régulièrement les dates de
modification des DOSSIERS (ajout, suppression ou renommage d'un fichier les change) : c'est
très léger, même sur des milliers de dossiers. Après un changement, on attend que les dossiers
soient stables (copie terminée) puis on rescanne uniquement la bibliothèque concernée.
"""
import hashlib
import json
import logging
import os
import threading
import time

log = logging.getLogger("seance.watch")
SKIP = {"@eadir", "#recycle", "$recycle.bin", "lost+found", ".trickplay"}


def fingerprint(paths):
    h = hashlib.sha1()
    for root in paths:
        try:
            h.update(f"{root}\0{os.stat(root).st_mtime_ns}\n".encode())
        except OSError:
            h.update(f"absent:{root}\n".encode())
            continue
        stack = [root]
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        if e.name.startswith(".") or e.name.lower() in SKIP:
                            continue
                        try:
                            if e.is_dir(follow_symlinks=True):
                                h.update(f"{e.path}\0{e.stat().st_mtime_ns}\n".encode())
                                stack.append(e.path)
                        except OSError:
                            continue
            except OSError:
                h.update(f"illisible:{d}\n".encode())
    return h.hexdigest()


class Watcher:
    def __init__(self, ctx):
        self.ctx = ctx
        self.prints = {}
        self.pending = {}     # library id -> instant du changement détecté
        self.last_check = {}
        self.force_at = None  # webhook : scan complet demandé
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.events = []      # derniers déclenchements (affichés dans l'admin)

    def interval(self):
        return self.ctx.setting_int("watch_interval", 60)

    def request_scan(self, reason="webhook", delay=10):
        with self.lock:
            self.force_at = time.time() + delay
            self._event(f"Scan demandé ({reason})")

    def defer(self, library_id):
        """Le scanner a ignoré des fichiers en cours de copie : on repassera."""
        with self.lock:
            self.pending[library_id] = time.time()

    def _event(self, text):
        self.events.insert(0, {"t": int(time.time()), "text": text})
        del self.events[20:]

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.stopped.set()

    def _loop(self):
        while not self.stopped.wait(5):
            try:
                self.tick()
            except Exception:
                log.exception("Surveillance des bibliothèques")
            finally:
                self.ctx.db.release()

    def tick(self, now=None):
        now = now or time.time()
        scanner = self.ctx.scanner
        if self.force_at and now >= self.force_at:
            if scanner.start():
                with self.lock:
                    self.force_at = None
                    self.pending.clear()
            return
        interval = self.interval()
        if interval <= 0:
            return
        libs = self.ctx.db.all("SELECT id, name, paths FROM libraries")
        known = {l["id"] for l in libs}
        for k in list(self.prints):
            if k not in known:
                self.prints.pop(k, None)
                self.pending.pop(k, None)
        due = []
        for lib in libs:
            if now - self.last_check.get(lib["id"], 0) < interval:
                continue
            self.last_check[lib["id"]] = now
            fp = fingerprint(json.loads(lib["paths"]))
            old = self.prints.get(lib["id"])
            self.prints[lib["id"]] = fp
            if old is None:
                continue  # premier passage : référence
            if fp != old:
                with self.lock:
                    self.pending[lib["id"]] = now
                log.info("Changement détecté dans « %s »", lib["name"])
                continue  # on attend un passage stable (copie en cours ?)
            if lib["id"] in self.pending:
                due.append(lib)
        if due and not scanner.status()["running"]:
            ids = [l["id"] for l in due]
            if scanner.start(ids):
                with self.lock:
                    for i in ids:
                        self.pending.pop(i, None)
                    self._event("Nouveautés détectées : scan de " + ", ".join(l["name"] for l in due))
