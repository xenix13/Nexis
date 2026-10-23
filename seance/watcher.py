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


MAX_PARTIAL = 500  # au-delà de ce nombre de dossiers touchés, un scan complet est plus simple


def snapshot(paths):
    """-> {dossier: date de modification} pour tous les dossiers des bibliothèques (fichiers non lus)."""
    snap = {}
    for root in paths:
        try:
            snap[root] = os.stat(root).st_mtime_ns
        except OSError:
            continue  # partage non monté : ses dossiers « disparaissent » mais le scanner ne supprime rien
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
                                snap[e.path] = e.stat().st_mtime_ns
                                stack.append(e.path)
                        except OSError:
                            continue
            except OSError:
                continue
    return snap


def fingerprint(paths):
    """Empreinte globale (compatibilité)."""
    h = hashlib.sha1()
    for d, m in sorted(snapshot(paths).items()):
        h.update(f"{d}\0{m}\n".encode())
    return h.hexdigest()


class Watcher:
    def __init__(self, ctx):
        self.ctx = ctx
        self.prints = {}      # bibliothèque -> {dossier: date}
        self.pending = {}     # bibliothèque -> {"t": instant, "dirs": dossiers à revoir, ou None = tout}
        self.last_check = {}
        self.force_at = None  # webhook : scan demandé
        self.force_scope = None  # {bibliothèque: dossiers} ; None = scan complet
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.events = []      # derniers déclenchements (affichés dans l'admin)

    def interval(self):
        return self.ctx.setting_int("watch_interval", 60)

    def request_scan(self, reason="webhook", delay=10, scope=None):
        """scope : {bibliothèque: dossiers} pour ne revoir que ces dossiers (sinon scan complet)."""
        with self.lock:
            full = self.force_at is not None and self.force_scope is None
            if scope and not full:
                merged = dict(self.force_scope or {})
                for lib, dirs in scope.items():
                    merged[lib] = set(merged.get(lib, set())) | set(dirs)
                self.force_scope = merged
            else:
                self.force_scope = None
            self.force_at = time.time() + delay
            what = ", ".join(os.path.basename(d) or d for dirs in (scope or {}).values() for d in sorted(dirs))[:200]
            self._event(f"Scan demandé ({reason})" + (f" : {what}" if scope else ""))

    def defer(self, library_id, dirs=None):
        """Le scanner a ignoré des fichiers en cours de copie : on repassera (sur ces dossiers seulement)."""
        with self.lock:
            self._add_pending(library_id, dirs)

    def _add_pending(self, library_id, dirs):
        cur = self.pending.get(library_id)
        if cur is None:
            cur = {"t": time.time(), "dirs": set()}
            self.pending[library_id] = cur
        cur["t"] = time.time()
        if dirs is None or cur["dirs"] is None:
            cur["dirs"] = None
        else:
            cur["dirs"] |= set(dirs)
            if len(cur["dirs"]) > MAX_PARTIAL:
                cur["dirs"] = None

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
            scope = self.force_scope
            ok = scanner.start(list(scope), scope=scope) if scope else scanner.start()
            if ok:
                with self.lock:
                    self.force_at = None
                    self.force_scope = None
                    for lib in (scope or self.pending).copy():
                        self.pending.pop(lib, None)
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
            snap = snapshot(json.loads(lib["paths"]))
            old = self.prints.get(lib["id"])
            self.prints[lib["id"]] = snap
            if old is None:
                continue  # premier passage : référence
            if snap != old:
                changed = {d for d, m in snap.items() if old.get(d) != m} | {d for d in old if d not in snap}
                with self.lock:
                    self._add_pending(lib["id"], changed)
                log.info("Changement détecté dans « %s » (%d dossier(s))", lib["name"], len(changed))
                continue  # on attend un passage stable (copie en cours ?)
            if lib["id"] in self.pending:
                due.append(lib)
        if due and not scanner.status()["running"]:
            ids = [l["id"] for l in due]
            with self.lock:
                scope = {l["id"]: set(self.pending[l["id"]]["dirs"]) for l in due
                         if self.pending.get(l["id"], {}).get("dirs") is not None}
            if scanner.start(ids, scope=scope or None):
                with self.lock:
                    for i in ids:
                        self.pending.pop(i, None)
                    parts = []
                    for l in due:
                        n = len(scope[l["id"]]) if l["id"] in scope else None
                        parts.append(f"{l['name']} ({n} dossier(s))" if n is not None else f"{l['name']} (complet)")
                    self._event("Nouveautés détectées : scan de " + ", ".join(parts))
