"""Livres audio : bibliothèque de type « audiobook ».

Rangement reconnu :
- un dossier par livre (« Auteur/Titre/ » ou « Titre/ »), avec un ou plusieurs fichiers audio (MP3, M4B, M4A,
  AAC, FLAC, OGG, Opus) lus à la suite : chaque fichier est alors un chapitre, sauf s'il en a lui-même ;
- un fichier .m4b seul (le format des livres audio), avec ses chapitres intégrés.
Titre, auteur, narrateur, année et résumé viennent des étiquettes des fichiers (album, artiste, compositeur…),
à défaut des noms de dossiers. La couverture : cover.jpg / folder.jpg à côté, sinon l'image intégrée au fichier.

La lecture se fait dans le lecteur audio de l'application (vitesse, minuterie, chapitres), fichier par fichier ;
la position est gardée comme pour une vidéo, dans la durée totale du livre.
"""
import hashlib
import json
import logging
import os
import re
import subprocess
import time

from . import media
from .names import fold, sort_title

log = logging.getLogger("seance.books")

AUDIO_EXT = {".mp3", ".m4b", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wav"}
MIME = {".mp3": "audio/mpeg", ".m4b": "audio/mp4", ".m4a": "audio/mp4", ".aac": "audio/aac", ".flac": "audio/flac",
        ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/wav"}
COVERS = ("cover", "folder", "front", "poster", "albumart")
SKIP_DIRS = {"@eadir", "#recycle", "$recycle.bin", "lost+found"}
NUM = re.compile(r"(\d+)")
RECENT_SECONDS = 45


def natural(name):
    return [int(p) if p.isdigit() else p.lower() for p in NUM.split(name)]


def probe_audio(path):
    """-> {dur (ms), tags, chapters [{t, s, e}], pic (index du flux image intégré)}."""
    try:
        out = subprocess.run([media.FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_chapters",
                              "-show_entries", "stream=index,codec_type,disposition", path],
                             capture_output=True, timeout=60).stdout
        d = json.loads(out or b"{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"dur": 0, "tags": {}, "chapters": []}
    fmt = d.get("format") or {}
    try:
        dur = int(float(fmt.get("duration") or 0) * 1000)
    except ValueError:
        dur = 0
    tags = {str(k).lower(): str(v) for k, v in (fmt.get("tags") or {}).items()}
    chapters = []
    for c in d.get("chapters") or []:
        try:
            s, e = int(float(c["start_time"]) * 1000), int(float(c["end_time"]) * 1000)
        except (KeyError, ValueError):
            continue
        chapters.append({"t": str((c.get("tags") or {}).get("title") or "")[:200], "s": s, "e": e})
    pic = next((s["index"] for s in d.get("streams") or [] if s.get("codec_type") == "video"
                and (s.get("disposition") or {}).get("attached_pic")), None)
    return {"dur": dur, "tags": tags, "chapters": chapters, "pic": pic}


def describe(folder, files, root):
    """Livre à partir de ses fichiers (dans l'ordre) -> champs de l'élément et son analyse."""
    infos = [probe_audio(f) for f in files]
    tags = next((i["tags"] for i in infos if i["tags"]), {})
    rel = os.path.relpath(folder, root)
    parts = [] if rel == "." else rel.split(os.sep)
    base = os.path.splitext(os.path.basename(files[0]))[0] if len(files) == 1 and (not parts or
                                                                                   files[0].lower().endswith(".m4b")) else None
    title = tags.get("album") or (tags.get("title") if len(files) == 1 else None) or base or (parts[-1] if parts else "")
    author = tags.get("album_artist") or tags.get("artist") or (parts[-2] if len(parts) >= 2 else "")
    if not author and " - " in (title or ""):
        author, title = [p.strip() for p in title.split(" - ", 1)]
    narrator = tags.get("composer") or tags.get("narrator") or tags.get("performer") or ""
    year = None
    m = re.search(r"(1[5-9]\d\d|20\d\d)", tags.get("date") or tags.get("year") or "")
    if m:
        year = int(m.group(1))
    chapters, offset, file_list = [], 0, []
    for f, i in zip(files, infos):
        file_list.append({"p": f, "d": i["dur"]})
        if i["chapters"] and (len(files) == 1 or len(i["chapters"]) > 1):
            chapters += [{"t": c["t"] or f"{len(chapters) + 1}", "s": offset + c["s"], "e": offset + c["e"]} for c in i["chapters"]]
        else:
            name = i["tags"].get("title") or os.path.splitext(os.path.basename(f))[0]
            chapters.append({"t": name[:200], "s": offset, "e": offset + i["dur"]})
        offset += i["dur"]
    cover = None
    names = {n.lower(): n for n in os.listdir(folder)} if os.path.isdir(folder) else {}
    for stem in COVERS:
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            if stem + ext in names:
                cover = os.path.join(folder, names[stem + ext])
                break
        if cover:
            break
    if not cover:
        pic = next(((f, i["pic"]) for f, i in zip(files, infos) if i["pic"] is not None), None)
        cover = {"embedded": pic[0], "stream": pic[1]} if pic else None
    summary = tags.get("description") or tags.get("comment") or tags.get("synopsis") or None
    return {"title": (title or "Sans titre")[:300], "year": year, "summary": summary[:5000] if summary else None,
            "duration_ms": offset,
            "probe": {"book": 1, "files": file_list, "chapters": chapters, "author": author[:200], "narrator": narrator[:200],
                      "cover": cover, "genre": tags.get("genre") or ""}}


def scan(scanner, lib):
    db = scanner.ctx.db
    paths = [p for p in json.loads(lib["paths"]) if p]
    reachable = [p for p in paths if os.path.isdir(p)]
    for p in paths:
        if p not in reachable:
            scanner._err(f"Dossier inaccessible : {p}")
    if paths and not reachable:
        return
    scanner._set(phase="Recherche des livres audio", done=0, total=0)
    books = []  # (racine, clé, dossier, fichiers)
    for root in reachable:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
            dirnames[:] = sorted((d for d in dirnames if not d.startswith(".") and d.lower() not in SKIP_DIRS), key=natural)
            audio = sorted((f for f in filenames if not f.startswith(".") and os.path.splitext(f)[1].lower() in AUDIO_EXT),
                           key=natural)
            if not audio:
                continue
            full = [os.path.join(dirpath, f) for f in audio]
            m4b = [f for f in full if f.lower().endswith(".m4b")]
            if dirpath == root or len(m4b) > 1:
                # fichiers posés à la racine, ou plusieurs .m4b dans un dossier : un livre par fichier
                for f in full:
                    books.append((root, f, dirpath, [f]))
            else:
                books.append((root, dirpath, dirpath, full))
    existing = {r["key"]: r for r in db.all("SELECT id, key, file_size, file_mtime FROM items WHERE library_id=? "
                                            "AND type='book' AND peer_id IS NULL", (lib["id"],))}
    scanner._set(phase="Analyse des livres audio", done=0, total=len(books))
    now, seen = int(time.time()), set()
    for n, (root, key, folder, files) in enumerate(books, 1):
        scanner._set(done=n)
        seen.add(key)
        try:
            sts = [os.stat(f) for f in files]
        except OSError:
            continue
        size, mtime = sum(s.st_size for s in sts), int(max(s.st_mtime for s in sts))
        if time.time() - mtime < RECENT_SECONDS:
            continue  # en cours de copie
        old = existing.get(key)
        if old and old["file_size"] == size and old["file_mtime"] == mtime:
            continue
        d = describe(folder, files, root)
        fields = dict(title=d["title"], year=d["year"], summary=d["summary"], duration_ms=d["duration_ms"],
                      file_size=size, file_mtime=mtime, probe=json.dumps(d["probe"], ensure_ascii=False), meta_state="local")
        try:
            if old:
                db.run("UPDATE items SET title=?, sort_title=?, search=?, year=?, summary=?, duration_ms=?, file_size=?, "
                       "file_mtime=?, probe=?, img_version=img_version+1, updated_at=? WHERE id=?",
                       (d["title"], sort_title(d["title"]), fold(f"{d['title']} {d['probe']['author']}"), d["year"],
                        d["summary"], d["duration_ms"], size, mtime, fields["probe"], now, old["id"]))
            else:
                iid = scanner._insert(library_id=lib["id"], type="book", key=key, added_at=scanner._added_at(lib, mtime), **fields)
                db.run("UPDATE items SET search=? WHERE id=?", (fold(f"{d['title']} {d['probe']['author']}"), iid))
        except Exception as e:
            log.warning("Livre audio %s : %s", key, e)
            scanner._err(f"{os.path.basename(key)} : {e}")
    gone = [r["id"] for k, r in existing.items() if k not in seen and any(k.startswith(os.path.join(r0, "")) for r0 in reachable)]
    for i in range(0, len(gone), 500):
        chunk = gone[i:i + 500]
        db.run(f"DELETE FROM items WHERE id IN ({','.join('?' * len(chunk))})", chunk)
    if gone:
        with scanner.lock:
            scanner.state["removed"] += len(gone)
    db.run("UPDATE libraries SET scanned_at=? WHERE id=?", (int(time.time()), lib["id"]))


def cover_path(ctx, item, w, h):
    """Couverture redimensionnée (JPEG en cache), ou None."""
    info = json.loads(item["probe"] or "{}")
    cov = info.get("cover")
    if not cov:
        return None
    tag = hashlib.sha1(f"book|{item['id']}|{item['img_version']}|{w}|{h}".encode()).hexdigest()
    dest = os.path.join(ctx.data_dir, "cache", "img", tag + ".jpg")
    if os.path.isfile(dest):
        return dest
    try:
        if isinstance(cov, str):
            media.resize_image(cov, dest, w, h, crop=True)
        else:
            raw = dest + ".src.jpg"
            media.run([media.FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", cov["embedded"], "-map", f"0:{int(cov['stream'])}",
                       "-frames:v", "1", raw], timeout=60)
            try:
                media.resize_image(raw, dest, w, h, crop=True)
            finally:
                try:
                    os.remove(raw)
                except OSError:
                    pass
    except (media.MediaError, OSError, KeyError, ValueError) as e:
        log.debug("Couverture %s : %s", item["id"], e)
        return None
    return dest


# ------------------------------------------------------------------ routes
def register(app, ctx, x):
    from .catalog import RK
    from .web import HTTPError, Response
    db = ctx.db

    def book_card(r, prog=None):
        info = json.loads(r["probe"] or "{}")
        c = {"id": str(r["id"]), "type": "book", "title": r["title"], "author": info.get("author") or "",
             "narrator": info.get("narrator") or "", "year": r["year"], "duration": r["duration_ms"],
             "thumb": f"/library/metadata/{r['id']}/thumb/{r['img_version']}" if info.get("cover") else None,
             "chapters": len(info.get("chapters") or []), "library": str(r["library_id"]), "added_at": r["added_at"]}
        if prog:
            c.update(position=prog["position_ms"], finished=bool(prog["watched"]), listened_at=prog["updated_at"])
        return c

    def progress_map(uid, ids):
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        return {int(r["rating_key"]): r for r in db.all(
            f"SELECT * FROM progress WHERE user_id=? AND rating_key IN ({marks})", (uid, *[str(i) for i in ids]))}

    def book_or_404(req):
        r = x.require_item(req.user, req.params["rk"])
        if r["type"] != "book":
            raise HTTPError(404, "Livre introuvable")
        return r

    @app.route("GET", r"/api/libraries/(?P<sid>\d{1,6})/books")
    def library_books(req):
        sid = req.params["sid"]
        x.require_section(req.user, sid)
        sort = req.arg("sort") or "title"
        order = {"title": "sort_title", "added": "added_at DESC, id DESC", "year": "COALESCE(year,0) DESC, sort_title"}.get(sort, "sort_title")
        rows = db.all(f"SELECT * FROM items WHERE library_id=? AND type='book' ORDER BY {order}", (int(sid),))
        prog = progress_map(req.user["id"], [r["id"] for r in rows])
        cards = [book_card(r, prog.get(r["id"])) for r in rows]
        if sort == "author":
            cards.sort(key=lambda c: (fold(c["author"]) or "~", fold(c["title"])))
        reading = sorted((c for c in cards if c.get("position") and not c.get("finished")),
                         key=lambda c: -(c.get("listened_at") or 0))
        return {"books": cards, "reading": reading[:12]}

    @app.route("GET", f"/api/books/{RK}")
    def book(req):
        r = book_or_404(req)
        info = json.loads(r["probe"] or "{}")
        prog = progress_map(req.user["id"], [r["id"]]).get(r["id"])
        c = book_card(r, prog)
        c.update(summary=r["summary"], genre=info.get("genre") or "", chapter_list=info.get("chapters") or [],
                 files=[{"n": n, "duration": f["d"], "url": f"/api/books/{r['id']}/file/{n}",
                         "mime": MIME.get(os.path.splitext(f["p"])[1].lower(), "audio/mpeg")}
                        for n, f in enumerate(info.get("files") or [])])
        return c

    @app.route("GET", f"/api/books/{RK}/file/(?P<n>\\d{{1,4}})")
    def book_file(req):
        r = book_or_404(req)
        files = json.loads(r["probe"] or "{}").get("files") or []
        n = int(req.params["n"])
        if n >= len(files) or not os.path.isfile(files[n]["p"]):
            raise HTTPError(404, "Fichier introuvable : le partage est-il monté ?")
        db.release()
        return x.file_response(req, files[n]["p"], MIME.get(os.path.splitext(files[n]["p"])[1].lower(), "audio/mpeg"))

    @app.route("POST", f"/api/books/{RK}/progress")
    def book_progress(req):
        r = book_or_404(req)
        b = req.json()
        try:
            pos = max(0, int(b.get("position") or 0))
        except (TypeError, ValueError):
            raise HTTPError(400, "Position invalide")
        dur = r["duration_ms"] or 0
        finished = bool(b.get("finished")) or bool(dur and pos >= dur - min(30000, dur // 100))  # générique de fin
        now, uid = int(time.time()), req.user["id"]
        meta = json.dumps(book_card(r), ensure_ascii=False)
        db.run("INSERT INTO progress(user_id, rating_key, position_ms, duration_ms, watched, updated_at, last_watched_at, type, "
               "section_id, meta) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id, rating_key) DO UPDATE SET "
               "position_ms=excluded.position_ms, duration_ms=excluded.duration_ms, watched=MAX(progress.watched, excluded.watched), "
               "updated_at=excluded.updated_at, last_watched_at=CASE WHEN excluded.watched=1 THEN excluded.last_watched_at "
               "ELSE progress.last_watched_at END, type='book'",
               (uid, str(r["id"]), 0 if finished else pos, dur, 1 if finished else 0, now, now if finished else None, "book",
                str(r["library_id"]), meta))
        if b.get("restart"):
            db.run("UPDATE progress SET watched=0, position_ms=0 WHERE user_id=? AND rating_key=?", (uid, str(r["id"])))
        return {"ok": True, "finished": bool(finished)}
