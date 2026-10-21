"""Images des éléments : TMDB ou fichiers locaux, sinon capture de la vidéo. Redimensionnées et mises en cache."""
import hashlib
import logging
import os
import threading
import urllib.request

from . import media
from .metadata import TMDB_IMG

log = logging.getLogger("seance.images")
_dl_lock = threading.Lock()


class Images:
    def __init__(self, ctx):
        self.ctx = ctx
        self.cache = os.path.join(ctx.data_dir, "cache", "img")
        self.orig = os.path.join(ctx.data_dir, "cache", "orig")
        os.makedirs(self.cache, exist_ok=True)
        os.makedirs(self.orig, exist_ok=True)

    def _download(self, tmdb_path, kind):
        name = "tmdb_" + tmdb_path.strip("/").replace("/", "_")
        dest = os.path.join(self.orig, name)
        if os.path.isfile(dest) and os.path.getsize(dest) > 0:
            return dest
        size = "w1280" if kind == "backdrop" else ("w780" if kind == "poster" else "w780")
        url = f"{TMDB_IMG}/{size}{tmdb_path}"
        req = urllib.request.Request(url, headers={"User-Agent": "Seance/2"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(15 * 1024 * 1024)
        tmp = dest + f".{threading.get_ident()}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        return dest

    def _source(self, spec, kind):
        if not spec:
            return None
        if spec.startswith("local:"):
            p = spec[6:]
            return p if os.path.isfile(p) else None
        if spec.startswith("tmdb:"):
            try:
                return self._download(spec[5:], kind)
            except Exception as e:
                log.debug("Image TMDB %s : %s", spec, e)
                return None
        return None

    def _first_episode(self, show_id):
        return self.ctx.db.one("SELECT e.* FROM items e JOIN items s ON s.id=e.parent_id WHERE e.show_id=? AND "
                               "e.type='episode' ORDER BY s.idx=0, s.idx, e.idx LIMIT 1", (show_id,))

    def resolve(self, item, kind):
        """-> ('file', chemin) ou ('frame', fichier vidéo, instant) ou None."""
        db = self.ctx.db
        t = item["type"]
        show = db.one("SELECT * FROM items WHERE id=?", (item["show_id"],)) if item["show_id"] else None
        if kind == "thumb":
            chain = []
            if t in ("movie", "show"):
                chain = [(item["poster"], "poster")]
            elif t == "season":
                chain = [(item["poster"], "poster"), (show and show["poster"], "poster")]
            elif t == "episode":
                chain = [(item["still"], "still")]
        else:
            chain = [(item["backdrop"], "backdrop")] if t in ("movie", "show") else [(show and show["backdrop"], "backdrop")]
        for spec, k in chain:
            src = self._source(spec, k)
            if src:
                return ("file", src)
        # repli : une image de la vidéo
        video = None
        if t in ("movie", "episode"):
            video = item
        elif t in ("show", "season"):
            video = self._first_episode(item["show_id"] or item["id"])
        if video and video["key"] and os.path.isfile(video["key"]):
            dur = (video["duration_ms"] or 0) / 1000
            return ("frame", video["key"], max(1.0, dur * (0.2 if t == "movie" else 0.15)) if dur else 10.0)
        return None

    def get(self, item, kind, w, h):
        src = self.resolve(item, kind)
        if not src:
            return None
        tag = hashlib.sha1(f"{item['id']}|{kind}|{item['img_version']}|{src}|{w}|{h}".encode()).hexdigest()
        dest = os.path.join(self.cache, tag + ".jpg")
        if os.path.isfile(dest):
            return dest
        try:
            if src[0] == "file":
                media.resize_image(src[1], dest, w, h)
            else:
                media.grab_frame(src[1], dest, src[2], w, h)
        except media.MediaError as e:
            log.debug("Image %s/%s : %s", item["id"], kind, e)
            return None
        return dest
