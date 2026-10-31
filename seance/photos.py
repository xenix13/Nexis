"""Photothèque : les photos d'un dossier (et de ses sous-dossiers, qui deviennent des albums).

- Scan : chaque sous-dossier est un album, chaque image une photo. La date de prise de vue et l'orientation
  viennent des données EXIF (JPEG, TIFF), sinon de la date du fichier.
- Affichage : miniatures recadrées pour la grille, image entière (redimensionnée, remise à l'endroit) pour la
  visionneuse, et fichier d'origine à télécharger. Rien n'est jamais écrit dans les dossiers de photos.
"""
import json
import logging
import os
import struct
import time
from datetime import datetime

from . import media

log = logging.getLogger("seance.photos")

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".tif", ".tiff", ".bmp"}
SKIP_DIRS = {"@eadir", "#recycle", "$recycle.bin", "lost+found", ".trickplay", "@__thumb", ".thumbnails"}
RECENT_SECONDS = 45
VIEW_SIZES = (640, 1280, 1920, 2560)
TIMELINE_PAGE = 200

# orientation EXIF -> filtres ffmpeg qui remettent l'image à l'endroit
ORIENT = {2: "hflip", 3: "hflip,vflip", 4: "vflip", 5: "transpose=0", 6: "transpose=1", 7: "transpose=3", 8: "transpose=2"}


# ------------------------------------------------------------------ EXIF
def _tiff(data):
    """Bloc TIFF (EXIF) -> {orientation, date, make, model}."""
    if len(data) < 8 or data[:2] not in (b"II", b"MM"):
        return {}
    e = "<" if data[:2] == b"II" else ">"

    def u16(o):
        return struct.unpack_from(e + "H", data, o)[0]

    def u32(o):
        return struct.unpack_from(e + "I", data, o)[0]

    def entries(off):
        out = {}
        if off <= 0 or off + 2 > len(data):
            return out
        for i in range(min(u16(off), 300)):
            p = off + 2 + i * 12
            if p + 12 > len(data):
                break
            tag, typ, count = u16(p), u16(p + 2), u32(p + 4)
            if typ == 3 and count == 1:      # SHORT
                out[tag] = u16(p + 8)
            elif typ == 4 and count == 1:    # LONG
                out[tag] = u32(p + 8)
            elif typ == 2:                   # ASCII
                start = p + 8 if count <= 4 else u32(p + 8)
                if start + count <= len(data):
                    out[tag] = data[start:start + count].split(b"\0", 1)[0].decode("latin-1", "replace").strip()
        return out
    try:
        ifd0 = entries(u32(4))
        sub = entries(ifd0[0x8769]) if isinstance(ifd0.get(0x8769), int) else {}
    except struct.error:
        return {}
    out = {}
    if isinstance(ifd0.get(0x0112), int) and 1 <= ifd0[0x0112] <= 8:
        out["o"] = ifd0[0x0112]
    date = sub.get(0x9003) or sub.get(0x9004) or ifd0.get(0x0132)
    if isinstance(date, str):
        try:
            out["taken"] = datetime.strptime(date[:19], "%Y:%m:%d %H:%M:%S").strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            pass
    for tag, k in ((0x010F, "make"), (0x0110, "model")):
        if isinstance(ifd0.get(tag), str) and ifd0[tag]:
            out[k] = ifd0[tag][:60]
    return out


def read_info(path):
    """-> {w, h, o, taken, make, model} (ce qu'on a pu lire ; rien d'obligatoire)."""
    ext = os.path.splitext(path)[1].lower()
    info = {}
    try:
        with open(path, "rb") as f:
            head = f.read(256 * 1024)
    except OSError:
        return info
    if ext in (".jpg", ".jpeg") and head[:2] == b"\xff\xd8":
        p = 2
        while p + 4 <= len(head):
            if head[p] != 0xFF:
                break
            marker, length = head[p + 1], struct.unpack_from(">H", head, p + 2)[0]
            seg = head[p + 4:p + 2 + length]
            if marker == 0xE1 and seg[:6] == b"Exif\0\0":
                info.update(_tiff(seg[6:]))
            elif 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC) and len(seg) >= 5:
                info["h"], info["w"] = struct.unpack_from(">HH", seg, 1)
                break
            elif marker == 0xDA:
                break
            p += 2 + length
    elif ext in (".tif", ".tiff"):
        info.update(_tiff(head))
    elif ext == ".png" and head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
        info["w"], info["h"] = struct.unpack_from(">II", head, 16)
    if info.get("o") in (5, 6, 7, 8) and info.get("w"):
        info["w"], info["h"] = info["h"], info["w"]  # dimensions affichées (photo prise en portrait)
    return info


# ------------------------------------------------------------------ scan
def scan(scanner, lib):
    """Scan complet d'une photothèque (les sous-dossiers sont des albums)."""
    db = scanner.ctx.db
    paths = [p for p in json.loads(lib["paths"]) if p]
    reachable = [p for p in paths if os.path.isdir(p)]
    for p in paths:
        if p not in reachable:
            scanner._err(f"Dossier inaccessible : {p}")
    if paths and not reachable:
        return  # partage non monté : surtout ne rien supprimer
    scanner._set(phase="Recherche des photos", done=0, total=0)
    files, folders = [], []
    for root in reachable:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d.lower() not in SKIP_DIRS)
            if dirpath != root:
                folders.append((root, dirpath))
            for name in sorted(filenames):
                if name.startswith(".") or os.path.splitext(name)[1].lower() not in PHOTO_EXT:
                    continue
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                files.append((root, full, st.st_size, int(st.st_mtime)))
    existing = {(r["type"], r["key"]): r for r in db.all(
        "SELECT id, type, key, file_size, file_mtime FROM items WHERE library_id=? AND type IN ('album','photo') "
        "AND peer_id IS NULL", (lib["id"],))}
    now = int(time.time())
    albums = {}
    for root, d in folders:  # de haut en bas : le parent existe avant ses sous-albums
        parent = albums.get(os.path.dirname(d))
        old = existing.get(("album", d))
        if old:
            albums[d] = old["id"]
            db.run("UPDATE items SET parent_id=? WHERE id=? AND COALESCE(parent_id,0)<>COALESCE(?,0)", (parent, old["id"], parent))
        else:
            albums[d] = scanner._insert(library_id=lib["id"], type="album", key=d, title=os.path.basename(d),
                                        parent_id=parent, meta_state="done", added_at=now)
    seen = {("album", d) for _, d in folders}
    deferred, deferred_dirs = 0, set()
    scanner._set(phase="Analyse des photos", done=0, total=len(files))
    for n, (root, path, size, mtime) in enumerate(files, 1):
        if n % 50 == 0:
            scanner._set(done=n)
        seen.add(("photo", path))
        if time.time() - mtime < RECENT_SECONDS:
            deferred += 1  # en cours de copie : au prochain passage
            deferred_dirs.add(os.path.dirname(path))
            continue
        old = existing.get(("photo", path))
        parent = albums.get(os.path.dirname(path))
        if old and old["file_size"] == size and old["file_mtime"] == mtime:
            continue
        info = read_info(path)
        taken = info.pop("taken", None) or datetime.fromtimestamp(mtime).strftime("%Y-%m-%dT%H:%M:%S")
        fields = dict(parent_id=parent, released=taken, year=int(taken[:4]), file_size=size, file_mtime=mtime,
                      probe=json.dumps(info, separators=(",", ":")))
        try:
            if old:
                db.run("UPDATE items SET parent_id=?, released=?, year=?, file_size=?, file_mtime=?, probe=?, "
                       "img_version=img_version+1, updated_at=? WHERE id=?", (*fields.values(), now, old["id"]))
            else:
                scanner._insert(library_id=lib["id"], type="photo", key=path, title=os.path.splitext(os.path.basename(path))[0],
                                meta_state="done", added_at=scanner._added_at(lib, mtime), **fields)
        except Exception as e:
            log.warning("Photo %s : %s", path, e)
            scanner._err(f"{os.path.basename(path)} : {e}")
    scanner._set(done=len(files))
    gone = [r["id"] for k, r in existing.items() if k not in seen
            and any(r["key"].startswith(os.path.join(r0, "")) for r0 in reachable)]
    for i in range(0, len(gone), 500):
        chunk = gone[i:i + 500]
        db.run(f"DELETE FROM items WHERE id IN ({','.join('?' * len(chunk))})", chunk)
    if gone:
        with scanner.lock:
            scanner.state["removed"] += len(gone)
    # albums sans photo (ni sous-album qui en a) : retirés
    while db.run("DELETE FROM items WHERE library_id=? AND type='album' AND peer_id IS NULL AND NOT EXISTS "
                 "(SELECT 1 FROM items c WHERE c.parent_id=items.id)", (lib["id"],)).rowcount:
        pass
    db.run("UPDATE libraries SET scanned_at=? WHERE id=?", (int(time.time()), lib["id"]))
    if deferred:
        watcher = getattr(scanner.ctx, "watcher", None)
        if watcher:
            watcher.defer(lib["id"], deferred_dirs)


# ------------------------------------------------------------------ images
def orientation(item):
    try:
        return json.loads(item["probe"] or "{}").get("o") or 1
    except ValueError:
        return 1


def cover(db, album_id):
    """Photo la plus récente d'un album (sous-albums compris), pour sa vignette."""
    return db.one("WITH RECURSIVE t(id) AS (SELECT ? UNION ALL SELECT i.id FROM items i JOIN t ON i.parent_id=t.id "
                  "WHERE i.type='album') SELECT p.* FROM items p WHERE p.type='photo' AND p.parent_id IN (SELECT id FROM t) "
                  "ORDER BY p.released DESC, p.id DESC LIMIT 1", (int(album_id),))


def render(ctx, item, w, h, crop):
    """Photo redimensionnée et remise à l'endroit (JPEG en cache) -> chemin, ou None."""
    import hashlib
    key = item["key"]
    if not key or not os.path.isfile(key):
        return None
    tag = hashlib.sha1(f"photo|{item['id']}|{item['img_version']}|{item['file_mtime']}|{w}|{h}|{crop}".encode()).hexdigest()
    dest = os.path.join(ctx.data_dir, "cache", "img", tag + ".jpg")
    if os.path.isfile(dest):
        return dest
    try:
        media.resize_image(key, dest, w, h, crop=crop, orient=ORIENT.get(orientation(item)))
    except media.MediaError as e:
        log.debug("Photo %s : %s", item["id"], e)
        return None
    return dest


MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
        ".heic": "image/heic", ".heif": "image/heif", ".tif": "image/tiff", ".tiff": "image/tiff", ".bmp": "image/bmp"}


def mime(path):
    return MIME.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


# ------------------------------------------------------------------ routes
def register(app, ctx, x):
    from .web import HTTPError, Response
    db = ctx.db

    def photo_card(r):
        info = json.loads(r["probe"] or "{}")
        v = r["img_version"]
        return {"id": str(r["id"]), "type": "photo", "title": r["title"], "taken": r["released"],
                "w": info.get("w"), "h": info.get("h"), "camera": " ".join(filter(None, (info.get("make"), info.get("model")))) or None,
                "size": r["file_size"], "thumb": f"/library/metadata/{r['id']}/thumb/{v}",
                "gif": r["key"].lower().endswith(".gif")}

    def album_card(r):
        n = db.scalar("WITH RECURSIVE t(id) AS (SELECT ? UNION ALL SELECT i.id FROM items i JOIN t ON i.parent_id=t.id "
                      "WHERE i.type='album') SELECT COUNT(*) FROM items p WHERE p.type='photo' AND p.parent_id IN "
                      "(SELECT id FROM t)", (r["id"],))
        return {"id": str(r["id"]), "type": "album", "title": r["title"], "count": n,
                "thumb": f"/library/metadata/{r['id']}/thumb/{r['img_version']}"}

    def photo_library(user, sid):
        x.require_section(user, sid)
        lib = db.one("SELECT * FROM libraries WHERE id=?", (int(sid),))
        if not lib or lib["kind"] != "photo":
            raise HTTPError(404, "Introuvable")
        return lib

    @app.route("GET", r"/api/libraries/(?P<sid>\d{1,6})/photos")
    def library_photos(req):
        """Un album (ses sous-albums et ses photos), ou toutes les photos de la plus récente à la plus ancienne."""
        sid = req.params["sid"]
        photo_library(req.user, sid)
        start = req.int_arg("start", 0, 0)
        size = req.int_arg("size", TIMELINE_PAGE, 1, 500)
        if req.arg("view") == "timeline":
            total = db.scalar("SELECT COUNT(*) FROM items WHERE library_id=? AND type='photo'", (int(sid),))
            rows = db.all("SELECT * FROM items WHERE library_id=? AND type='photo' ORDER BY released DESC, id DESC "
                          "LIMIT ? OFFSET ?", (int(sid), size, start))
            return {"total": total, "photos": [photo_card(r) for r in rows], "albums": [], "path": []}
        album = req.arg("album")
        path = []
        if album:
            a = db.one("SELECT * FROM items WHERE id=? AND library_id=? AND type='album'",
                       (int(album) if album.isdigit() else 0, int(sid)))
            if not a:
                raise HTTPError(404, "Album introuvable")
            p = a
            while p:
                path.insert(0, {"id": str(p["id"]), "title": p["title"]})
                p = db.one("SELECT * FROM items WHERE id=? AND type='album'", (p["parent_id"],)) if p["parent_id"] else None
            cond, args = "parent_id=?", [a["id"]]
        else:
            cond, args = "parent_id IS NULL AND library_id=?", [int(sid)]
        albums = [album_card(r) for r in db.all(f"SELECT * FROM items WHERE type='album' AND {cond} ORDER BY sort_title", args)] \
            if start == 0 else []
        total = db.scalar(f"SELECT COUNT(*) FROM items WHERE type='photo' AND {cond}", args)
        rows = db.all(f"SELECT * FROM items WHERE type='photo' AND {cond} ORDER BY released, sort_title LIMIT ? OFFSET ?",
                      [*args, size, start])
        return {"total": total, "albums": albums, "photos": [photo_card(r) for r in rows], "path": path}

    def photo_or_404(req):
        r = db.one("SELECT * FROM items WHERE id=? AND type='photo'", (int(req.params["rk"]),))
        if not r:
            raise HTTPError(404, "Photo introuvable")
        x.require_section(req.user, str(r["library_id"]))
        return r

    @app.route("GET", r"/api/photos/(?P<rk>\d{1,12})/view")
    def photo_view(req):
        """Image entière pour la visionneuse : redimensionnée à l'écran, remise à l'endroit (GIF animés tels quels)."""
        r = photo_or_404(req)
        w = req.int_arg("w", 1920, 100, 4000)
        size = next((s for s in VIEW_SIZES if s >= w), VIEW_SIZES[-1])
        db.release()
        if r["key"].lower().endswith(".gif") and os.path.isfile(r["key"]):
            with open(r["key"], "rb") as f:
                return Response(f.read(), 200, "image/gif", {"Cache-Control": "private, max-age=604800"})
        path = render(ctx, r, size, size, crop=False)
        if not path:
            raise HTTPError(404, "Image illisible")
        with open(path, "rb") as f:
            return Response(f.read(), 200, "image/jpeg", {"Cache-Control": "private, max-age=604800, immutable"})

    @app.route("GET", r"/api/photos/(?P<rk>\d{1,12})/original")
    def photo_original(req):
        r = photo_or_404(req)
        if not os.path.isfile(r["key"]):
            raise HTTPError(404, "Fichier introuvable : le partage est-il monté ?")
        db.release()
        name = os.path.basename(r["key"]).replace('"', "")
        f = open(r["key"], "rb")
        size = os.path.getsize(r["key"])

        def gen():
            while True:
                chunk = f.read(262144)
                if not chunk:
                    break
                yield chunk
        from urllib.parse import quote
        return Response(stream=gen(), content_type=mime(r["key"]), length=size, on_close=f.close,
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
                                 "Cache-Control": "private, no-store"})
