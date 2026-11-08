"""Photothèque : les photos d'un dossier (et de ses sous-dossiers, qui deviennent des albums).

- Scan : chaque sous-dossier est un album, chaque image une photo. La date de prise de vue et l'orientation
  viennent des données EXIF (JPEG, TIFF), sinon de la date du fichier.
- Affichage : miniatures recadrées pour la grille, image entière (redimensionnée, remise à l'endroit) pour la
  visionneuse, et fichier d'origine à télécharger. Rien n'est jamais écrit dans les dossiers de photos.
"""
import json
import logging
import os
import re
import struct
import subprocess
import time
from datetime import datetime

from . import media

log = logging.getLogger("seance.photos")

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".tif", ".tiff", ".bmp"}
CLIP_EXT = {".mp4", ".mov", ".m4v", ".3gp", ".webm", ".mkv", ".avi", ".mts"}  # vidéos du téléphone, dans les albums
CLIP_MIME = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".3gp": "video/3gpp", ".webm": "video/webm",
             ".mkv": "video/x-matroska", ".avi": "video/x-msvideo", ".mts": "video/mp2t"}
SKIP_DIRS = {"@eadir", "#recycle", "$recycle.bin", "lost+found", ".trickplay", "@__thumb", ".thumbnails"}
RECENT_SECONDS = 45
VIEW_SIZES = (640, 1280, 1920, 2560)
TIMELINE_PAGE = 200
MAP_MAX = 50000  # points affichés sur la carte

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
            elif typ == 5 and 1 <= count <= 4:  # RATIONAL (coordonnées GPS : degrés, minutes, secondes)
                start = u32(p + 8)
                if start + 8 * count <= len(data):
                    out[tag] = tuple(u32(start + 8 * k) / (u32(start + 8 * k + 4) or 1) for k in range(count))
            elif typ == 2:                   # ASCII
                start = p + 8 if count <= 4 else u32(p + 8)
                if start + count <= len(data):
                    out[tag] = data[start:start + count].split(b"\0", 1)[0].decode("latin-1", "replace").strip()
        return out
    try:
        ifd0 = entries(u32(4))
        sub = entries(ifd0[0x8769]) if isinstance(ifd0.get(0x8769), int) else {}
        gps = entries(ifd0[0x8825]) if isinstance(ifd0.get(0x8825), int) else {}
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
    lat, lon = _deg(gps.get(2), gps.get(1)), _deg(gps.get(4), gps.get(3))
    if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0, 0):
        out["lat"], out["lon"] = round(lat, 6), round(lon, 6)
    return out


def _deg(dms, ref):
    """(degrés, minutes, secondes) EXIF et « N »/« S »/« E »/« W » -> degrés décimaux, ou None."""
    if not isinstance(dms, tuple) or not dms:
        return None
    v = dms[0] + (dms[1] / 60 if len(dms) > 1 else 0) + (dms[2] / 3600 if len(dms) > 2 else 0)
    return -v if isinstance(ref, str) and ref[:1].upper() in ("S", "W") else v


ISO6709 = re.compile(r"^([+-]\d{1,2}(?:\.\d+)?)([+-]\d{1,3}(?:\.\d+)?)")


def is_clip(path):
    return os.path.splitext(path)[1].lower() in CLIP_EXT


def read_clip_info(path):
    """Vidéo d'un album : dimensions, durée, date de tournage (ffprobe) -> {v: 1, w, h, dur, taken}."""
    info = {"v": 1}
    try:
        out = subprocess.run([media.FFPROBE, "-v", "error", "-print_format", "json", "-show_entries",
                              "format=duration:format_tags=creation_time,location,com.apple.quicktime.location.ISO6709:"
                              "stream=codec_type,width,height:stream_tags=rotate",
                              path], capture_output=True, timeout=60).stdout
        d = json.loads(out or b"{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return info
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    if v.get("width") and v.get("height"):
        info["w"], info["h"] = int(v["width"]), int(v["height"])
        if str((v.get("tags") or {}).get("rotate", "0")) in ("90", "270", "-90"):
            info["w"], info["h"] = info["h"], info["w"]
    fmt = d.get("format") or {}
    try:
        info["dur"] = int(float(fmt.get("duration") or 0) * 1000)
    except ValueError:
        pass
    tags = fmt.get("tags") or {}
    when = str(tags.get("creation_time") or "")
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", when) and not when.startswith("1970"):
        info["taken"] = when[:19]
    m = ISO6709.match(str(tags.get("com.apple.quicktime.location.ISO6709") or tags.get("location") or ""))
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0, 0):
            info["lat"], info["lon"] = round(lat, 6), round(lon, 6)
    return info


def read_info(path):
    """-> {w, h, o, taken, make, model, lat, lon} (ce qu'on a pu lire ; rien d'obligatoire). gv : lecture qui
    comprend les coordonnées GPS (les photos analysées avant sont relues une fois)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in CLIP_EXT:
        return {**read_clip_info(path), "gv": 1}
    info = {"gv": 1}
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
                if name.startswith(".") or os.path.splitext(name)[1].lower() not in PHOTO_EXT | CLIP_EXT:
                    continue
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                files.append((root, full, st.st_size, int(st.st_mtime)))
    existing = {(r["type"], r["key"]): r for r in db.all(
        "SELECT id, type, key, file_size, file_mtime, probe FROM items WHERE library_id=? AND type IN ('album','photo') "
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
            if '"gv":' not in (old["probe"] or ""):  # analysée avant la lecture du GPS : relue une fois
                info = read_info(path)
                old_info = json.loads(old["probe"] or "{}")
                old_info.update({k: info[k] for k in ("lat", "lon", "gv") if k in info})
                db.run("UPDATE items SET probe=? WHERE id=?", (json.dumps(old_info, separators=(",", ":")), old["id"]))
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
        if is_clip(key):  # vidéo : une image prise au début (1 s, ou le début si elle est plus courte)
            dur = (json.loads(item["probe"] or "{}").get("dur") or 0) / 1000
            media.grab_frame(key, dest, min(1.0, dur / 2) if dur else 0.0, w, h)
            return dest
        media.resize_image(key, dest, w, h, crop=crop, orient=ORIENT.get(orientation(item)))
    except media.MediaError as e:
        log.debug("Photo %s : %s", item["id"], e)
        return None
    return dest


MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
        ".heic": "image/heic", ".heif": "image/heif", ".tif": "image/tiff", ".tiff": "image/tiff", ".bmp": "image/bmp"}


def mime(path):
    ext = os.path.splitext(path)[1].lower()
    return MIME.get(ext) or CLIP_MIME.get(ext, "application/octet-stream")


# ------------------------------------------------------------------ routes
def register(app, ctx, x):
    import secrets
    from urllib.parse import quote
    from .web import HTTPError, Response
    db = ctx.db
    SUBTREE = ("WITH RECURSIVE t(id) AS (SELECT ? UNION ALL SELECT i.id FROM items i JOIN t ON i.parent_id=t.id "
               "WHERE i.type='album') ")

    def photo_card(r, favs=(), base=None):
        """Carte d'une photo (ou d'une vidéo) ; base : adresses d'un album partagé par lien (sans compte)."""
        info = json.loads(r["probe"] or "{}")
        v = r["img_version"]
        rid = str(r["id"])
        c = {"id": rid, "type": "photo", "title": r["title"], "taken": r["released"],
             "w": info.get("w"), "h": info.get("h"), "camera": " ".join(filter(None, (info.get("make"), info.get("model")))) or None,
             "size": r["file_size"], "gif": r["key"].lower().endswith(".gif"), "video": bool(info.get("v")),
             "duration": info.get("dur"), "fav": r["id"] in favs}
        if not base and isinstance(info.get("lat"), (int, float)):
            c.update(lat=info["lat"], lon=info.get("lon"))  # pas dans un album partagé par lien : le lieu reste privé
        if base:
            c.update(thumb_url=f"{base}/img/{rid}?k=thumb", view_url=f"{base}/img/{rid}?k=view",
                     stream_url=f"{base}/stream/{rid}", original_url=f"{base}/original/{rid}")
        else:
            c.update(thumb=f"/library/metadata/{rid}/thumb/{v}", view_url=f"/api/photos/{rid}/view",
                     stream_url=f"/api/photos/{rid}/stream", original_url=f"/api/photos/{rid}/original")
        return c

    def album_card(r, base=None):
        n = db.scalar(SUBTREE + "SELECT COUNT(*) FROM items p WHERE p.type='photo' AND p.parent_id IN (SELECT id FROM t)", (r["id"],))
        c = {"id": str(r["id"]), "type": "album", "title": r["title"], "count": n}
        cov = cover(db, r["id"]) if base else None
        if base:
            c["thumb_url"] = f"{base}/img/{cov['id']}?k=thumb" if cov else None
        else:
            c["thumb"] = f"/library/metadata/{r['id']}/thumb/{r['img_version']}"
        return c

    def favs_of(uid):
        return {r["item_id"] for r in db.all("SELECT item_id FROM photo_favs WHERE user_id=?", (uid,))}

    def photo_library(user, sid):
        x.require_section(user, sid)
        lib = db.one("SELECT * FROM libraries WHERE id=?", (int(sid),))
        if not lib or lib["kind"] != "photo":
            raise HTTPError(404, "Introuvable")
        return lib

    def listing(sid, album, view, start, size, favs=(), fav_uid=None, root=None, base=None):
        """Contenu d'un album (sous-albums et photos), toutes les photos par date, ou les favoris d'une personne.
        root : album partagé par lien, dont on ne sort pas."""
        if view == "timeline" or view == "favorites":
            cond, args = ("library_id=?", [int(sid)])
            if view == "favorites":
                cond += " AND id IN (SELECT item_id FROM photo_favs WHERE user_id=?)"
                args.append(fav_uid)
            total = db.scalar(f"SELECT COUNT(*) FROM items WHERE type='photo' AND {cond}", args)
            rows = db.all(f"SELECT * FROM items WHERE type='photo' AND {cond} ORDER BY released DESC, id DESC LIMIT ? OFFSET ?",
                          [*args, size, start])
            return {"total": total, "photos": [photo_card(r, favs, base) for r in rows], "albums": [], "path": []}
        path = []
        a = None
        if album or root:
            aid = int(album) if album and str(album).isdigit() else (root or 0)
            a = db.one("SELECT * FROM items WHERE id=? AND library_id=? AND type='album'", (aid, int(sid)))
            if not a or (root and not db.scalar(SUBTREE + "SELECT 1 FROM t WHERE id=?", (root, a["id"]))):
                raise HTTPError(404, "Album introuvable")
            p = a
            while p:
                path.insert(0, {"id": str(p["id"]), "title": p["title"]})
                if root and p["id"] == root:
                    break
                p = db.one("SELECT * FROM items WHERE id=? AND type='album'", (p["parent_id"],)) if p["parent_id"] else None
            cond, args = "parent_id=?", [a["id"]]
        else:
            cond, args = "parent_id IS NULL AND library_id=?", [int(sid)]
        albums = [album_card(r, base) for r in db.all(f"SELECT * FROM items WHERE type='album' AND {cond} ORDER BY sort_title", args)] \
            if start == 0 else []
        total = db.scalar(f"SELECT COUNT(*) FROM items WHERE type='photo' AND {cond}", args)
        rows = db.all(f"SELECT * FROM items WHERE type='photo' AND {cond} ORDER BY released, sort_title LIMIT ? OFFSET ?",
                      [*args, size, start])
        return {"total": total, "albums": albums, "photos": [photo_card(r, favs, base) for r in rows], "path": path}

    def map_points(sid):
        """Photos et vidéos qui portent des coordonnées GPS -> {points: [[id, lat, lon], …]} (pour la carte)."""
        rows = db.all("SELECT id, probe FROM items WHERE library_id=? AND type='photo' AND probe LIKE '%\"lat\":%' "
                      "ORDER BY released DESC, id DESC LIMIT ?", (int(sid), MAP_MAX))
        pts = []
        for r in rows:
            info = json.loads(r["probe"] or "{}")
            if isinstance(info.get("lat"), (int, float)) and isinstance(info.get("lon"), (int, float)):
                pts.append([str(r["id"]), info["lat"], info["lon"]])
        return {"points": pts, "total": len(pts)}

    def photos_by_ids(sid, ids, favs):
        """Les photos d'un endroit de la carte (au plus 500), de la plus récente à la plus ancienne."""
        wanted = [int(i) for i in str(ids).split(",")[:500] if i.isdigit()]
        if not wanted:
            return []
        rows = db.all(f"SELECT * FROM items WHERE type='photo' AND library_id=? AND id IN ({','.join('?' * len(wanted))}) "
                      "ORDER BY released DESC, id DESC", (int(sid), *wanted))
        return [photo_card(r, favs) for r in rows]

    @app.route("GET", r"/api/libraries/(?P<sid>\d{1,6})/photos")
    def library_photos(req):
        """Un album (ses sous-albums et ses photos), toutes les photos de la plus récente à la plus ancienne, ou
        les favoris de la personne."""
        sid = req.params["sid"]
        photo_library(req.user, sid)
        uid = req.user["id"]
        if req.arg("view") == "map":
            return map_points(sid)
        if req.arg("ids") is not None:
            return {"photos": photos_by_ids(sid, req.arg("ids"), favs_of(uid))}
        return listing(sid, req.arg("album"), req.arg("view"), req.int_arg("start", 0, 0),
                       req.int_arg("size", TIMELINE_PAGE, 1, 500), favs_of(uid), uid)

    def photo_or_404(req):
        r = db.one("SELECT * FROM items WHERE id=? AND type='photo'", (int(req.params["rk"]),))
        if not r:
            raise HTTPError(404, "Photo introuvable")
        x.require_section(req.user, str(r["library_id"]))
        return r

    # ------------------------------------------------------------ fichiers (visionneuse, vidéo, original)
    def view_response(r, w):
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

    def file_response(req, path, attachment=False):
        """Fichier d'origine, par plages (vidéo : avance rapide dans le navigateur)."""
        if not os.path.isfile(path):
            raise HTTPError(404, "Fichier introuvable : le partage est-il monté ?")
        db.release()
        size = os.path.getsize(path)
        start, end, status = 0, size - 1, 200
        rng = (req.headers.get("Range") or "").strip()
        if rng.startswith("bytes="):
            a, _, b = rng[6:].split(",")[0].partition("-")
            try:
                if a.strip():
                    start, end = int(a), min(int(b), size - 1) if b.strip() else size - 1
                elif b.strip():
                    start = max(0, size - int(b))
            except ValueError:
                raise HTTPError(416, "Plage invalide")
            if start > end or start >= size:
                return Response(b"", 416, "text/plain", {"Content-Range": f"bytes */{size}"})
            status = 206
        f = open(path, "rb")
        f.seek(start)
        length = end - start + 1

        def gen():
            left = length
            while left > 0:
                chunk = f.read(min(262144, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk
        headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, no-store"}
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        if attachment:
            headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(os.path.basename(path).replace('"', ""))
        return Response(stream=gen(), status=status, content_type=mime(path), length=length, headers=headers, on_close=f.close)

    @app.route("GET", r"/api/photos/(?P<rk>\d{1,12})/view")
    def photo_view(req):
        """Image entière pour la visionneuse : redimensionnée à l'écran, remise à l'endroit (GIF animés tels quels)."""
        return view_response(photo_or_404(req), req.int_arg("w", 1920, 100, 4000))

    @app.route("GET", r"/api/photos/(?P<rk>\d{1,12})/stream")
    def photo_stream(req):
        r = photo_or_404(req)
        if not is_clip(r["key"]):
            raise HTTPError(404, "Ce n'est pas une vidéo")
        return file_response(req, r["key"])

    @app.route("GET", r"/api/photos/(?P<rk>\d{1,12})/original")
    def photo_original(req):
        return file_response(req, photo_or_404(req)["key"], attachment=True)

    @app.route("PUT", r"/api/photos/(?P<rk>\d{1,12})/favorite")
    def photo_favorite(req):
        r = photo_or_404(req)
        if req.json().get("on"):
            db.run("INSERT OR IGNORE INTO photo_favs(user_id,item_id,added_at) VALUES(?,?,?)", (req.user["id"], r["id"], int(time.time())))
        else:
            db.run("DELETE FROM photo_favs WHERE user_id=? AND item_id=?", (req.user["id"], r["id"]))
        return {"fav": bool(req.json().get("on"))}

    # ------------------------------------------------------------ album partagé par lien (sans compte)
    def album_or_404(req):
        a = db.one("SELECT * FROM items WHERE id=? AND type='album'", (int(req.params["aid"]),))
        if not a:
            raise HTTPError(404, "Album introuvable")
        x.require_section(req.user, str(a["library_id"]))
        return a

    def share_view(s):
        return {"token": s["token"], "url": f"/#/share/{s['token']}", "expires_at": s["expires_at"], "created_at": s["created_at"],
                "by": s.get("by")}

    @app.route("GET", r"/api/photos/albums/(?P<aid>\d{1,12})/shares")
    def album_shares(req):
        a = album_or_404(req)
        return [share_view(s) for s in db.all("SELECT s.*, u.display_name AS by FROM photo_shares s JOIN users u ON u.id=s.user_id "
                                              "WHERE s.album_id=? AND s.expires_at>? ORDER BY s.created_at DESC", (a["id"], int(time.time())))]

    @app.route("POST", r"/api/photos/albums/(?P<aid>\d{1,12})/shares")
    def album_share_create(req):
        a = album_or_404(req)
        days = req.json().get("days", 7)
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 90:
            raise HTTPError(400, "Durée invalide (1 à 90 jours)")
        now = int(time.time())
        token = secrets.token_urlsafe(18)
        db.run("INSERT INTO photo_shares(token,album_id,user_id,created_at,expires_at) VALUES(?,?,?,?,?)",
               (token, a["id"], req.user["id"], now, now + days * 86400))
        return share_view({"token": token, "created_at": now, "expires_at": now + days * 86400, "by": req.user["display_name"]})

    @app.route("DELETE", r"/api/photos/shares/(?P<token>[A-Za-z0-9_-]{20,40})")
    def album_share_delete(req):
        s = db.one("SELECT s.*, i.library_id FROM photo_shares s JOIN items i ON i.id=s.album_id WHERE s.token=?", (req.params["token"],))
        if not s or (s["user_id"] != req.user["id"] and not req.user["is_admin"]):
            raise HTTPError(404, "Lien introuvable")
        db.run("DELETE FROM photo_shares WHERE token=?", (s["token"],))
        return {"ok": True}

    def shared(req):
        s = db.one("SELECT s.*, i.library_id, i.title FROM photo_shares s JOIN items i ON i.id=s.album_id WHERE s.token=? AND s.expires_at>?",
                   (req.params["token"], int(time.time())))
        if not s:
            ctx.throttle.check("share:" + req.client_ip)
            ctx.throttle.fail("share:" + req.client_ip)  # liens essayés au hasard : freinés comme les mots de passe
            raise HTTPError(404, "Ce lien a expiré ou n'existe pas")
        return s

    def shared_photo(req, s):
        r = db.one("SELECT * FROM items WHERE id=? AND type='photo'", (int(req.params["rk"]),))
        if not r or not db.scalar(SUBTREE + "SELECT 1 FROM t WHERE id=?", (s["album_id"], r["parent_id"] or 0)):
            raise HTTPError(404, "Photo introuvable")
        return r

    TOKEN = r"(?P<token>[A-Za-z0-9_-]{20,40})"

    @app.route("GET", f"/api/share/{TOKEN}", auth=None)
    def share_album(req):
        s = shared(req)
        out = listing(s["library_id"], req.arg("album"), None, req.int_arg("start", 0, 0), req.int_arg("size", TIMELINE_PAGE, 1, 500),
                      root=s["album_id"], base=f"/api/share/{s['token']}")
        out.update(title=s["title"], expires_at=s["expires_at"])
        return out

    @app.route("GET", f"/api/share/{TOKEN}/img/(?P<rk>\\d{{1,12}})", auth=None)
    def share_img(req):
        s = shared(req)
        r = shared_photo(req, s)
        if req.arg("k") == "view":
            return view_response(r, req.int_arg("w", 1920, 100, 4000))
        w = max(80, min(640, req.int_arg("w", 240)))
        path = render(ctx, r, w, w, crop=True)
        if not path:
            raise HTTPError(404, "Image illisible")
        with open(path, "rb") as f:
            return Response(f.read(), 200, "image/jpeg", {"Cache-Control": "private, max-age=86400"})

    @app.route("GET", f"/api/share/{TOKEN}/stream/(?P<rk>\\d{{1,12}})", auth=None)
    def share_stream(req):
        r = shared_photo(req, shared(req))
        if not is_clip(r["key"]):
            raise HTTPError(404, "Ce n'est pas une vidéo")
        return file_response(req, r["key"])

    @app.route("GET", f"/api/share/{TOKEN}/original/(?P<rk>\\d{{1,12}})", auth=None)
    def share_original(req):
        return file_response(req, shared_photo(req, shared(req))["key"], attachment=True)
