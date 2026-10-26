"""Images des chapitres (sélection de chapitre du lecteur et de la fiche).

Une image par chapitre, prise un peu après son début : le tout début d'un chapitre est souvent un
fondu au noir. Si l'image obtenue est presque uniforme (noir, carton), on en reprend une plus loin.
Les images suivent le FICHIER (taille + date), comme les sous-titres : un fichier déplacé les retrouve.
"""
import json
import logging
import os
import threading

from . import media

log = logging.getLogger("seance.chapters")

W, H = 400, 225
FLAT_BYTES = 4500   # un JPEG 400x225 plus petit que ça est presque uni (écran noir, fondu)


def chapter_list(item):
    probe = item["probe"]
    probe = (json.loads(probe or "{}") if isinstance(probe, str) else probe) or {}
    return [c for c in probe.get("chapters") or [] if (c.get("end") or 0) > (c.get("start") or 0)]


class ChapterImages:
    def __init__(self, ctx):
        self.ctx = ctx
        self.base = os.path.join(ctx.data_dir, "cache", "chapters")
        os.makedirs(self.base, exist_ok=True)
        self.locks = {}
        self.lock = threading.Lock()

    def folder(self, item):
        size = item["file_size"] if "file_size" in item.keys() else None
        key = f"f{size}_{item['file_mtime'] or 0}" if size else f"{item['id']}_{item['file_mtime'] or 0}"
        return os.path.join(self.base, key)

    def path(self, item, n):
        return os.path.join(self.folder(item), f"{int(n)}.jpg")

    def done(self, item, n):
        """Image faite, ou impossible à faire (chapitre après la fin de la vidéo…) : rien à refaire."""
        dest = self.path(item, n)
        return os.path.isfile(dest) or os.path.isfile(dest + ".none")

    def has_all(self, item):
        chapters = chapter_list(item)
        return len(chapters) < 2 or all(self.done(item, n) for n in range(len(chapters)))

    def _lock(self, item):
        with self.lock:
            return self.locks.setdefault(item["id"], threading.Lock())

    def get(self, item, n):
        """Chemin de l'image du chapitre n (fabriquée à la demande si besoin), ou None."""
        chapters = chapter_list(item)
        if not 0 <= n < len(chapters):
            return None
        dest = self.path(item, n)
        if os.path.isfile(dest):
            return dest
        if os.path.isfile(dest + ".none"):
            return None
        with self._lock(item):
            if not self.done(item, n):
                self._grab(item, chapters[n], dest)
        return dest if os.path.isfile(dest) else None

    def extract_all(self, item, cancelled=None):
        """Toutes les images d'un fichier ; renvoie le nombre d'images fabriquées."""
        chapters = chapter_list(item)
        if len(chapters) < 2:
            return 0
        made = 0
        with self._lock(item):
            for n, c in enumerate(chapters):
                if cancelled and cancelled():
                    break
                dest = self.path(item, n)
                if self.done(item, n):
                    continue
                if self._grab(item, c, dest):
                    made += 1
        return made

    def _grab(self, item, chapter, dest):
        if not item["key"] or not os.path.isfile(item["key"]) or media.is_strm(item["key"]):
            return False
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        start, end = float(chapter["start"]), float(chapter["end"])
        probe = item["probe"]
        probe = (json.loads(probe or "{}") if isinstance(probe, str) else probe) or {}
        duration = float(probe.get("duration") or 0) or (item.get("duration_ms") or 0) / 1000
        if duration > 2:
            # chapitres annoncés au-delà de la fin réelle de la vidéo (fréquent en fin de fichier)
            end = min(end, duration - 1.0)
            start = min(start, max(0.0, end - 1.0))
        length = max(0.0, end - start)
        tries = [start + min(20.0, max(2.0, length * 0.15)), start + length * 0.5, start + length * 0.8, start]
        best, best_size = None, -1
        for at in tries:
            at = max(0.0, min(at, end - 0.5)) if length > 1 else start
            tmp = f"{dest}.try.jpg"
            try:
                media.grab_frame(item["key"], tmp, at, W, H)
            except (media.MediaError, OSError) as e:
                log.debug("Image de chapitre de %s à %.0f s : %s", item["key"], at, e)
                continue
            size = os.path.getsize(tmp)
            if size > best_size:
                if best:
                    os.remove(best)
                best, best_size = f"{dest}.best.jpg", size
                os.replace(tmp, best)
            else:
                os.remove(tmp)
            if best_size >= FLAT_BYTES:
                break
        if not best:
            # aucune image possible : noté, pour ne pas réessayer à chaque passage de la tâche
            open(dest + ".none", "w").close()
            log.info("Pas d'image pour un chapitre de %s (%.0f s)", item["key"], start)
            return False
        os.replace(best, dest)
        return True
