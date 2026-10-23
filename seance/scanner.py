"""Scan des bibliothèques : fichiers -> films / séries / saisons / épisodes."""
import json
import re
import logging
import os
import threading
import time

from . import media
from .names import (IMAGE_NAMES, SUB_EXT, VIDEO_EXT, clean_title, fold, parse_episode, parse_movie,
                    sort_title, sub_language)

log = logging.getLogger("seance.scan")
RECENT_SECONDS = 45


SKIP_DIRS = ("@eadir", "#recycle", "$recycle.bin", "lost+found", ".trickplay")


def _videos_in(dirpath, filenames, videos):
    for f in sorted(filenames):
        if f.startswith("."):
            continue
        ext = os.path.splitext(f)[1].lower()
        if ext not in VIDEO_EXT:
            continue
        full = os.path.join(dirpath, f)
        try:
            st = os.stat(full)
        except OSError:
            continue
        if "sample" in f.lower() and st.st_size < 150 * 1024 * 1024:
            continue
        videos.append((full, st.st_size, int(st.st_mtime)))


def walk_videos(root):
    """-> liste de (chemin, taille, mtime) et dict dossier -> fichiers (pour sous-titres/images)."""
    videos, dir_files = [], {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d.lower() not in SKIP_DIRS)
        dir_files[dirpath] = filenames
        _videos_in(dirpath, filenames, videos)
    return videos, dir_files


def list_dirs(dirs):
    """Scan partiel : seulement les fichiers DIRECTEMENT dans ces dossiers (les sous-dossiers nouveaux
    font eux-mêmes partie de la liste)."""
    videos, dir_files = [], {}
    for d in sorted(dirs):
        try:
            with os.scandir(d) as it:
                names = [e.name for e in it if not e.is_dir(follow_symlinks=True)]
        except OSError:
            continue
        dir_files[d] = names
        _videos_in(d, names, videos)
    return videos, dir_files


def external_subs(video, dir_files):
    d = os.path.dirname(video)
    vbase = os.path.splitext(os.path.basename(video))[0]
    out = []
    for f in sorted(dir_files.get(d, [])):
        ext = os.path.splitext(f)[1].lower()
        if ext not in SUB_EXT or ext == ".sub":
            continue
        if not f.startswith(vbase):
            # sous-titres dans un sous-dossier « Subs » ou nom différent : seulement si un seul film dans le dossier
            continue
        lang, forced, sdh = sub_language(f, video)
        codec = {".srt": "subrip", ".ass": "ass", ".ssa": "ass", ".vtt": "webvtt"}[ext]
        out.append({"path": os.path.join(d, f), "codec": codec, "lang": lang, "forced": forced, "sdh": sdh})
    for sub in ("subs", "Subs", "subtitles", "Subtitles"):
        sd = os.path.join(d, sub)
        for f in sorted(dir_files.get(sd, [])):
            ext = os.path.splitext(f)[1].lower()
            if ext in (".srt", ".ass", ".ssa", ".vtt"):
                lang, forced, sdh = sub_language(f, f)
                lang = lang or sub_language("x." + f, "x")[0]
                out.append({"path": os.path.join(sd, f), "codec": "ass" if ext in (".ass", ".ssa") else
                            ("webvtt" if ext == ".vtt" else "subrip"), "lang": lang, "forced": forced, "sdh": sdh})
    return out


def local_image(folder, kind, files, prefix=None):
    """Cherche poster.jpg / fanart.jpg (ou <prefix>-poster.jpg) dans un dossier."""
    names = IMAGE_NAMES[kind]
    lower = {f.lower(): f for f in files}
    cands = []
    if prefix:
        cands += [f"{prefix}-{n}" for n in names] + [f"{prefix}.{n}" for n in names]
    cands += list(names)
    for c in cands:
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            f = lower.get((c + ext).lower())
            if f:
                return "local:" + os.path.join(folder, f)
    return None


EXTRA_DIRS = {"behind the scenes": "behindthescenes", "deleted scenes": "deleted", "featurettes": "featurette",
              "interviews": "interview", "scenes": "scene", "shorts": "short", "trailers": "trailer", "other": "other",
              "extras": "other", "bonus": "other", "making of": "behindthescenes", "bandes-annonces": "trailer",
              "bande-annonce": "trailer", "scènes coupées": "deleted", "scenes coupees": "deleted"}
EXTRA_SUFFIX = re.compile(r"[-_. ](trailer|featurette|behindthescenes|deleted|interview|scene|short|other|"
                          r"bande-annonce)$", re.I)
EXTRA_LABELS = {"trailer": "Bande-annonce", "featurette": "Featurette", "behindthescenes": "Coulisses",
                "deleted": "Scène coupée", "interview": "Interview", "scene": "Scène", "short": "Court métrage",
                "other": "Bonus"}


def extra_kind(path, root):
    rel = os.path.relpath(path, root).split(os.sep)
    for d in rel[:-1]:
        k = EXTRA_DIRS.get(d.lower())
        if k:
            return k
    m = EXTRA_SUFFIX.search(os.path.splitext(rel[-1])[0])
    if m:
        k = m.group(1).lower()
        return "trailer" if k == "bande-annonce" else k
    return None


def extra_owner_dir(path, root):
    parts = os.path.relpath(path, root).split(os.sep)
    folder = root
    for d in parts[:-1]:
        if d.lower() in EXTRA_DIRS:
            break
        folder = os.path.join(folder, d)
    return folder


def extra_title(path):
    base = os.path.splitext(os.path.basename(path))[0]
    base = EXTRA_SUFFIX.sub("", base)
    t, _ = clean_title(base)
    return t or base


def like_prefix(folder):
    esc = folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return esc + os.sep + "%"


class Scanner:
    def __init__(self, ctx):
        self.ctx = ctx
        self.lock = threading.Lock()
        self.thread = None
        self.state = {"running": False, "phase": "", "library": None, "done": 0, "total": 0,
                      "added": 0, "removed": 0, "unrecognized": [], "errors": [], "finished_at": None}

    def status(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)

    def start(self, library_ids=None, refresh_metadata=False, scope=None):
        """scope : {id de bibliothèque: dossiers à revoir} pour un scan partiel (nouveautés détectées)."""
        with self.lock:
            if self.state["running"]:
                return False
            self.state.update(running=True, phase="Préparation", library=None, done=0, total=0, added=0, deferred=0,
                              removed=0, unrecognized=[], errors=[], finished_at=None, partial=bool(scope))
        self.thread = threading.Thread(target=self._run, args=(library_ids, refresh_metadata, scope), daemon=True)
        self.thread.start()
        return True

    def _run(self, library_ids, refresh_metadata, scope=None):
        db = self.ctx.db
        try:
            libs = db.all("SELECT * FROM libraries ORDER BY position, id")
            if library_ids:
                libs = [l for l in libs if l["id"] in library_ids]
            for lib in libs:
                self._set(library=lib["name"])
                try:
                    self.scan_library(lib, (scope or {}).get(lib["id"]))
                except Exception as e:
                    log.exception("Scan de %s", lib["name"])
                    self._err(f"{lib['name']} : {e}")
            if refresh_metadata:
                ids = [l["id"] for l in libs]
                if ids:
                    db.run(f"UPDATE items SET meta_state='pending' WHERE locked=0 AND library_id IN "
                           f"({','.join('?' * len(ids))})", ids)
            self._set(phase="Métadonnées", done=0, total=0)
            self.ctx.metadata.process_pending(progress=lambda d, t: self._set(done=d, total=t))
        except Exception as e:
            log.exception("Scan")
            self._err(str(e))
        finally:
            self._set(running=False, phase="Terminé", finished_at=int(time.time()))
            self.ctx.on_library_changed()
            db.release()

    def _err(self, msg):
        with self.lock:
            if len(self.state["errors"]) < 50:
                self.state["errors"].append(msg)

    # ------------------------------------------------------------------
    def scan_library(self, lib, scope=None):
        """Scan complet, ou partiel si `scope` (dossiers modifiés, ajoutés ou supprimés) est donné."""
        db = self.ctx.db
        paths = [p for p in json.loads(lib["paths"]) if p]
        self._set(phase="Recherche des fichiers", done=0, total=0)
        files, dir_files = [], {}
        reachable = []
        for root in paths:
            if not os.path.isdir(root):
                self._err(f"Dossier inaccessible : {root}")
                continue
            reachable.append(root)
            if scope is not None:
                # chemins normalisés : « /mnt/series/../../etc » ne passe pas pour un dossier de la bibliothèque
                mine = [d for d in map(os.path.normpath, scope) if d == root or d.startswith(os.path.join(root, ""))]
                v, d = list_dirs(mine)
            else:
                v, d = walk_videos(root)
            files += [(root, *x) for x in v]
            dir_files.update(d)
        if paths and not reachable:
            # partage non monté : surtout ne rien supprimer
            return
        existing = {r["key"]: r for r in db.all(
            "SELECT id, key, file_size, file_mtime, probe, type FROM items WHERE library_id=? "
            "AND type IN ('movie','episode','extra')",
            (lib["id"],))}
        if scope is not None:
            # seuls les fichiers DIRECTEMENT dans les dossiers revus, ou sous un dossier disparu, peuvent avoir
            # changé. Surtout pas « tout ce qui est sous un dossier revu » : le dossier racine fait partie des
            # dossiers modifiés dès qu'une série y arrive, et tout le reste de la bibliothèque serait supprimé.
            scoped = set(scope)
            vanished = tuple(os.path.join(d, "") for d in scope if not os.path.isdir(d))
            existing = {k: r for k, r in existing.items()
                        if os.path.dirname(k) in scoped or (vanished and k.startswith(vanished))}
        seen = set()
        deferred = 0
        deferred_dirs = set()
        extras = []
        self._set(phase="Analyse des fichiers", done=0, total=len(files))
        for n, (root, path, size, mtime) in enumerate(files, 1):
            self._set(done=n)
            seen.add(path)
            if time.time() - mtime < RECENT_SECONDS:
                # fichier probablement en cours de copie (Radarr, Sonarr…) : on le reprendra au prochain passage
                deferred += 1
                deferred_dirs.add(os.path.dirname(path))
                continue
            kind = extra_kind(path, root)
            if kind:
                old = existing.get(path)
                if old and old["type"] != "extra":  # indexé à tort comme film/épisode par une version précédente
                    db.run("DELETE FROM items WHERE id=?", (old["id"],))
                extras.append((root, path, size, mtime, kind))
                continue
            try:
                if lib["kind"] == "movie":
                    self._movie(lib, root, path, size, mtime, existing.get(path), dir_files)
                else:
                    self._episode(lib, root, path, size, mtime, existing.get(path), dir_files)
            except media.MediaError as e:
                self._err(f"{os.path.basename(path)} : {e}")
            except Exception as e:
                log.exception("Fichier %s", path)
                self._err(f"{os.path.basename(path)} : {e}")
        gone = [r["id"] for k, r in existing.items() if k not in seen
                and any(k.startswith(os.path.join(r0, "")) for r0 in reachable)]
        for i in range(0, len(gone), 500):
            chunk = gone[i:i + 500]
            db.run(f"DELETE FROM items WHERE id IN ({','.join('?' * len(chunk))})", chunk)
        if gone:
            with self.lock:
                self.state["removed"] += len(gone)
        for root, path, size, mtime, kind in extras:
            try:
                self._extra(lib, root, path, size, mtime, kind, dir_files)
            except Exception as e:
                log.warning("Bonus %s : %s", path, e)
                self._err(f"{os.path.basename(path)} : {e}")
        # saisons et séries vides
        db.run("DELETE FROM items WHERE library_id=? AND type='season' AND NOT EXISTS "
               "(SELECT 1 FROM items e WHERE e.parent_id=items.id)", (lib["id"],))
        db.run("DELETE FROM items WHERE library_id=? AND type='show' AND NOT EXISTS "
               "(SELECT 1 FROM items s WHERE s.parent_id=items.id)", (lib["id"],))
        db.run("UPDATE libraries SET scanned_at=? WHERE id=?", (int(time.time()), lib["id"]))
        if deferred:
            log.info("%s : %d fichier(s) en cours de copie, nouveau passage prévu", lib["name"], deferred)
            with self.lock:
                self.state["deferred"] = self.state.get("deferred", 0) + deferred
            watcher = getattr(self.ctx, "watcher", None)
            if watcher:
                watcher.defer(lib["id"], deferred_dirs)

    def _extra(self, lib, root, path, size, mtime, kind, dir_files):
        """Bonus (bande-annonce, making-of…) rattaché au film ou à la série du même dossier."""
        db = self.ctx.db
        parent = None
        folder = extra_owner_dir(path, root)
        if lib["kind"] == "movie":
            for r in db.all("SELECT id, key FROM items WHERE library_id=? AND type='movie' AND key LIKE ? ESCAPE '\\'",
                            (lib["id"], like_prefix(folder))):
                if os.path.dirname(r["key"]) == folder:
                    parent = r["id"]
                    break
        else:
            show_dir = os.path.join(root, os.path.relpath(path, root).split(os.sep)[0])
            r = db.one("SELECT id FROM items WHERE library_id=? AND type='show' AND key=?", (lib["id"], show_dir))
            parent = r["id"] if r else None
        if not parent:
            with self.lock:
                if len(self.state["unrecognized"]) < 100:
                    self.state["unrecognized"].append(os.path.relpath(path, root) + " (bonus sans film/série)")
            return
        row = db.one("SELECT id, key, file_size, file_mtime, probe FROM items WHERE library_id=? AND type='extra' AND key=?",
                     (lib["id"], path))
        info = self._probe(path, row, size, mtime, dir_files)
        dur = int(info["duration"] * 1000)
        if row:
            db.run("UPDATE items SET parent_id=?, show_id=?, extra_kind=?, file_size=?, file_mtime=?, probe=?, "
                   "duration_ms=? WHERE id=?", (parent, parent, kind, size, mtime, json.dumps(info), dur, row["id"]))
            return
        title = extra_title(path)
        self._insert(library_id=lib["id"], type="extra", key=path, parent_id=parent, show_id=parent, title=title,
                     extra_kind=kind, duration_ms=dur, file_size=size, file_mtime=mtime, probe=json.dumps(info),
                     meta_state="local", added_at=int(time.time()))

    @staticmethod
    def _unchanged(row, size, mtime, info):
        if row["file_size"] != size or row["file_mtime"] != mtime or not row["probe"]:
            return False
        try:
            return json.loads(row["probe"]) == info
        except ValueError:
            return False

    def _probe(self, path, row, size, mtime, dir_files):
        info = json.loads(row["probe"]) if row and row["probe"] else None
        current = info and "attachments" in info and (not info.get("video") or "fps" in info["video"])
        if info and row["file_size"] == size and row["file_mtime"] == mtime and current:
            pass  # inchangé (les analyses d'anciennes versions sont refaites une fois)
        else:
            info = media.probe(path)
        info["external"] = external_subs(path, dir_files)
        return info

    def _insert(self, **f):
        now = int(time.time())
        f.setdefault("added_at", now)
        f["updated_at"] = now
        f["sort_title"] = sort_title(f["title"])
        f["search"] = fold(f["title"])
        cols = ",".join(f)
        cur = self.ctx.db.run(f"INSERT INTO items({cols}) VALUES({','.join('?' * len(f))})", list(f.values()))
        with self.lock:
            self.state["added"] += 1
        return cur.lastrowid

    @staticmethod
    def _added_at(lib, mtime):
        # premier scan : date du fichier (pour un « ajoutés récemment » utile) ; ensuite : maintenant
        now = int(time.time())
        if lib["scanned_at"] is None and 0 < mtime < now:
            return mtime
        return now

    def _movie(self, lib, root, path, size, mtime, row, dir_files):
        db = self.ctx.db
        info = self._probe(path, row, size, mtime, dir_files)
        dur = int(info["duration"] * 1000)
        folder = os.path.dirname(path)
        base = os.path.splitext(os.path.basename(path))[0]
        files = dir_files.get(folder, [])
        own_folder = folder != root.rstrip(os.sep)
        if own_folder:
            poster = local_image(folder, "poster", files, base)
            backdrop = local_image(folder, "backdrop", files, base)
        else:
            poster = local_image(folder, "poster", [f for f in files if f.startswith(base)], base)
            backdrop = local_image(folder, "backdrop", [f for f in files if f.startswith(base)], base)
        if row and self._unchanged(row, size, mtime, info):
            if poster or backdrop:
                db.run("UPDATE items SET poster=COALESCE(poster, ?), backdrop=COALESCE(backdrop, ?) WHERE id=? "
                       "AND (poster IS NULL OR backdrop IS NULL)", (poster, backdrop, row["id"]))
            return
        if row:
            db.run("UPDATE items SET file_size=?, file_mtime=?, probe=?, duration_ms=?, "
                   "poster=COALESCE(poster, ?), backdrop=COALESCE(backdrop, ?) WHERE id=?",
                   (size, mtime, json.dumps(info), dur, poster, backdrop, row["id"]))
            if row["file_mtime"] != mtime:
                self._new_media(row["id"], info)
            return
        title, year = parse_movie(path, root)
        new_id = self._insert(library_id=lib["id"], type="movie", key=path, title=title, year=year, duration_ms=dur,
                     file_size=size, file_mtime=mtime, probe=json.dumps(info), poster=poster, backdrop=backdrop,
                     added_at=self._added_at(lib, mtime))
        self._new_media(new_id, info)

    def _new_media(self, item_id, info):
        hook = getattr(self.ctx, "on_new_media", None)
        if hook:
            hook(item_id, info)

    def _episode(self, lib, root, path, size, mtime, row, dir_files):
        db = self.ctx.db
        parsed = parse_episode(path, root)
        if not parsed:
            with self.lock:
                if len(self.state["unrecognized"]) < 100:
                    self.state["unrecognized"].append(os.path.relpath(path, root))
            return
        info = self._probe(path, row, size, mtime, dir_files)
        dur = int(info["duration"] * 1000)
        if row and self._unchanged(row, size, mtime, info):
            return  # rien à écrire : fichier, analyse et sous-titres externes identiques
        if row:
            db.run("UPDATE items SET file_size=?, file_mtime=?, probe=?, duration_ms=? WHERE id=?",
                   (size, mtime, json.dumps(info), dur, row["id"]))
            if row["file_mtime"] != mtime:
                self._new_media(row["id"], info)
            return
        # série
        show_key = os.path.join(root, parsed["show_dir"]) if parsed["show_dir"] else "name:" + fold(parsed["show"])
        show = db.one("SELECT id FROM items WHERE library_id=? AND type='show' AND key=?", (lib["id"], show_key))
        if show:
            show_id = show["id"]
        else:
            folder = show_key if parsed["show_dir"] else None
            files = dir_files.get(folder, []) if folder else []
            show_id = self._insert(library_id=lib["id"], type="show", key=show_key, title=parsed["show"],
                                   year=parsed["year"],
                                   poster=local_image(folder, "poster", files) if folder else None,
                                   backdrop=local_image(folder, "backdrop", files) if folder else None)
        # saison
        s_key = f"{show_id}:{parsed['season']}"
        season = db.one("SELECT id FROM items WHERE library_id=? AND type='season' AND key=?", (lib["id"], s_key))
        if season:
            season_id = season["id"]
        else:
            poster = None
            if parsed["show_dir"]:
                sf = dir_files.get(show_key, [])
                poster = local_image(show_key, "poster", sf, f"season{parsed['season']:02d}")
            season_id = self._insert(library_id=lib["id"], type="season", key=s_key, parent_id=show_id,
                                     show_id=show_id, idx=parsed["season"], poster=poster,
                                     title="Spéciaux" if parsed["season"] == 0 else f"Saison {parsed['season']}")
        folder = os.path.dirname(path)
        base = os.path.splitext(os.path.basename(path))[0]
        still = None
        for ext in (".jpg", ".png"):
            if base + "-thumb" + ext in dir_files.get(folder, []):
                still = "local:" + os.path.join(folder, base + "-thumb" + ext)
        now = int(time.time())
        new_id = self._insert(library_id=lib["id"], type="episode", key=path, parent_id=season_id, show_id=show_id,
                     idx=parsed["episode"], title=f"Épisode {parsed['episode']}", duration_ms=dur,
                     file_size=size, file_mtime=mtime, probe=json.dumps(info), still=still,
                     added_at=self._added_at(lib, mtime))
        db.run("UPDATE items SET updated_at=? WHERE id IN (?,?)", (now, show_id, season_id))
        self._new_media(new_id, info)
