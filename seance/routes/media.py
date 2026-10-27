"""Lecture : préparation et sous-titres, polices, aperçus, chapitres, flux vidéo, progression."""
import json
import os
import re
import secrets
import threading
import time

from .. import media
from ..catalog import EXTERNAL_BASE, PREPARE_WINDOW_MS, RK, SID, WATCHED_RATIO, stream_lists
from ..keyframes import mkv_keyframes
from ..subtitles import parse_subtitles
from ..downloads import DownloadError
from ..transcoder import Busy, QUALITY, StreamError, plan
from ..web import HTTPError, Response


def register(app, ctx, x):
    db = ctx.db
    # outils du catalogue partagé (seance/catalog.py)
    card, chapters_for, get_prefs, markers_for = x.card, x.chapters_for, x.get_prefs, x.markers_for
    next_episode, require_item, set_watched, upsert_meta = x.next_episode, x.require_item, x.set_watched, x.upsert_meta

    def sub_lock(key):
        with ctx.lock:
            return ctx.sub_locks.setdefault(key, threading.Lock())

    @app.route("GET", f"/api/items/{RK}/prepare")
    def item_prepare(req):
        """Avant de lancer la lecture : toutes les pistes de sous-titres internes doivent être extraites."""
        r = require_item(req.user, req.params["rk"])
        if r["type"] not in ("movie", "episode", "extra"):
            raise HTTPError(400, "Élément non lisible")
        job = ctx.subtitles.ensure_now(r)
        if not job:
            return {"ready": True, "progress": 1}
        if req.arg("wait"):
            db.release()
            ctx.subtitles.wait(job, min(25, max(1, req.int_arg("wait", 10))))
        if job.done.is_set():
            # en cas d'échec on n'empêche pas la lecture : les pistes illisibles seront simplement absentes
            return {"ready": True, "progress": 1, "warning": job.error}
        # lecture progressive : on démarre dès que les sous-titres couvrent largement le début du visionnage
        # (l'extraction lit le fichier bien plus vite que le temps réel, elle reste devant)
        at = max(0, req.int_arg("at", 0))
        if job.until_ms >= at + PREPARE_WINDOW_MS or (r["duration_ms"] and job.until_ms >= r["duration_ms"] - 10000):
            return {"ready": True, "partial": True, "until": job.until_ms, "progress": round(job.progress, 3)}
        return {"ready": False, "progress": round(job.progress, 3), "until": job.until_ms}

    @app.route("GET", f"/api/items/{RK}/subtitles/(?P<sub>\\d{{1,6}})")
    def item_subtitles(req):
        r = require_item(req.user, req.params["rk"])
        sub = int(req.params["sub"])
        probe = json.loads(r["probe"] or "{}")
        json_type = "application/json; charset=utf-8"
        if sub < EXTERNAL_BASE:
            # piste interne : préparée par le cache de sous-titres (une seule lecture du fichier)
            s = next((x for x in probe.get("subs", []) if x["index"] == sub), None)
            if not s or s["codec"] not in media.TEXT_SUB_CODECS:
                raise HTTPError(404, "Sous-titres introuvables")
            data = ctx.subtitles.read(r, sub)
            if data is None:
                job = ctx.subtitles.ensure_now(r)
                if job and req.arg("wait") == "1":
                    db.release()
                    ctx.subtitles.wait(job, 900)
                data = ctx.subtitles.read(r, sub)
                if data is None:
                    part = ctx.subtitles.partial(r, sub, "srt")
                    if part:
                        return Response(json.dumps({"cues": parse_subtitles(part[0]), "partial": True,
                                                    "until": part[1]}, ensure_ascii=False), 200, json_type,
                                        {"Cache-Control": "no-store"})
                    st = ctx.subtitles.status(r)
                    if job and job.done.is_set() and job.error:
                        raise HTTPError(502, f"Lecture des sous-titres impossible : {job.error}")
                    return Response(json.dumps({"pending": True, "progress": st.get("progress", 0)}), 202,
                                    json_type, {"Cache-Control": "no-store"})
            return Response(data, 200, json_type, {"Cache-Control": "private, max-age=3600"})
        ext = probe.get("external", [])
        if sub - EXTERNAL_BASE >= len(ext):
            raise HTTPError(404, "Sous-titres introuvables")
        cache = os.path.join(ctx.data_dir, "cache", "subs", f"ext_{r['id']}_{sub}_{r['file_mtime']}.json")
        if os.path.isfile(cache):
            with open(cache, "rb") as f:
                return Response(f.read(), 200, json_type, {"Cache-Control": "private, max-age=3600"})
        with sub_lock(cache):
            if os.path.isfile(cache):
                with open(cache, "rb") as f:
                    return Response(f.read(), 200, json_type)
            e = ext[sub - EXTERNAL_BASE]
            try:
                if e["codec"] in ("subrip", "webvtt"):
                    with open(e["path"], "rb") as f:
                        raw = f.read(10 * 1024 * 1024)
                else:
                    raw = media.extract_subtitle(e["path"])
            except (OSError, media.MediaError) as ex:
                raise HTTPError(502, f"Lecture des sous-titres impossible : {ex}")
            data = json.dumps({"cues": parse_subtitles(raw)}, ensure_ascii=False).encode()
            with open(cache + ".tmp", "wb") as f:
                f.write(data)
            os.replace(cache + ".tmp", cache)
        return Response(data, 200, json_type)

    @app.route("GET", f"/api/items/{RK}/subtitles/(?P<sub>\\d{{1,6}})\\.vtt")
    def item_subtitles_vtt(req):
        """Même piste au format WebVTT, pour les lecteurs natifs (application Android TV). Une piste interne
        pas encore extraite est attendue (2 min au plus), puis servie en partie si besoin."""
        req.query = {**req.query, "wait": ["1"]}
        resp = item_subtitles(req)
        if resp.status == 202:
            raise HTTPError(503, "Sous-titres en préparation")
        try:
            cues = json.loads(resp.body).get("cues") or []
        except (ValueError, AttributeError):
            cues = []

        def ts(ms):
            ms = max(0, int(ms))
            return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"
        out = ["WEBVTT", ""]
        for c in cues:
            text = str(c.get("t") or "").replace("-->", "→").strip()
            if text:
                out += [f"{ts(c.get('s', 0))} --> {ts(c.get('e', 0))}" + (" line:10%" if c.get("top") else ""), text, ""]
        return Response("\n".join(out).encode(), 200, "text/vtt; charset=utf-8", {"Cache-Control": "private, max-age=600"})

    # -- ASS d'origine (rendu fidèle par libass dans le navigateur) et polices jointes au MKV
    ASS_FN = re.compile(rb"\\fn([^\\}]{1,100})")

    def ass_fonts(raw):
        names = set()
        in_styles = False
        fmt = None
        for line in raw.decode("utf-8-sig", "replace").splitlines():
            t = line.strip()
            if t.startswith("["):
                in_styles = "styles" in t.lower()
                fmt = None
                continue
            if in_styles and t.lower().startswith("format:"):
                fmt = [f.strip().lower() for f in t[7:].split(",")]
            elif in_styles and t.lower().startswith("style:") and fmt and "fontname" in fmt:
                parts = t[6:].split(",")
                if len(parts) > fmt.index("fontname"):
                    names.add(parts[fmt.index("fontname")].strip().lstrip("@").lower())
        for m in ASS_FN.finditer(raw):
            names.add(m.group(1).decode("utf-8", "replace").strip().lstrip("@").lower())
        return names

    def external_ass(r, sub):
        probe = json.loads(r["probe"] or "{}")
        ext = probe.get("external", [])
        if sub - EXTERNAL_BASE >= len(ext) or ext[sub - EXTERNAL_BASE]["codec"] not in ("ass", "ssa"):
            raise HTTPError(404, "Sous-titres introuvables")
        try:
            with open(ext[sub - EXTERNAL_BASE]["path"], "rb") as f:
                return f.read(30 * 1024 * 1024)
        except OSError as e:
            raise HTTPError(502, f"Lecture des sous-titres impossible : {e}")

    def raw_ass(req):
        r = require_item(req.user, req.params["rk"])
        sub = int(req.params["sub"])
        if sub >= EXTERNAL_BASE:
            return r, external_ass(r, sub), None
        data = ctx.subtitles.read_ass(r, sub)
        if data is None:
            part = ctx.subtitles.partial(r, sub, "ass")
            if part and part[0]:
                return r, part[0], part[1]
            raise HTTPError(404, "Script ASS indisponible")
        return r, data, None

    def until_headers(until):
        if until is None:
            return {"Cache-Control": "private, max-age=3600"}
        return {"Cache-Control": "no-store", "X-Seance-Partial": str(int(until))}

    @app.route("GET", f"/api/items/{RK}/subtitles/(?P<sub>\\d{{1,6}})/ass")
    def item_subtitles_ass(req):
        r, data, until = raw_ass(req)
        return Response(data, 200, "text/plain; charset=utf-8", until_headers(until))

    @app.route("GET", f"/api/items/{RK}/subtitles/(?P<sub>\\d{{1,6}})/fonts")
    def item_subtitles_fonts(req):
        r, data, _ = raw_ass(req)
        wanted = ass_fonts(data)
        index = ctx.subtitles.fonts_index(r) or []
        files = [f for f in index if wanted & set(f["names"])]
        return {"fonts": [f"/api/items/{r['id']}/fonts/{f['file']}" for f in files], "wanted": sorted(wanted)[:50],
                "embedded": len(index)}

    @app.route("GET", f"/api/items/{RK}/fonts/(?P<name>font_\\d{{1,5}}\\.(?:ttf|otf|ttc|otc|woff2?))")
    def item_font(req):
        r = require_item(req.user, req.params["rk"])
        names = {f["file"] for f in (ctx.subtitles.fonts_index(r) or [])}
        if req.params["name"] not in names:
            raise HTTPError(404, "Police introuvable")
        try:
            with open(ctx.subtitles.font_path(r, req.params["name"]), "rb") as f:
                data = f.read()
        except OSError:
            raise HTTPError(404, "Police introuvable")
        return Response(data, 200, "font/ttf", {"Cache-Control": "private, max-age=604800, immutable"})

    @app.route("GET", f"/api/items/{RK}/trickplay")
    def item_trickplay(req):
        r = require_item(req.user, req.params["rk"])
        index = ctx.subtitles.trick_index(r)
        if not index:
            if ctx.subtitles.needs_trick(r):
                ctx.subtitles.enqueue(r["id"], urgent=True)
            return {"ready": False}
        index["ready"] = True
        return index

    @app.route("GET", f"/api/items/{RK}/trickplay/(?P<n>\\d{{1,4}})\\.jpg")
    def item_trickplay_sheet(req):
        r = require_item(req.user, req.params["rk"])
        path = os.path.join(ctx.subtitles.trick_dir(r), f"{int(req.params['n'])}.jpg")
        try:
            with open(path, "rb") as f:
                return Response(f.read(), 200, "image/jpeg", {"Cache-Control": "private, max-age=604800"})
        except OSError:
            raise HTTPError(404, "Aperçu indisponible")

    @app.route("GET", f"/api/items/{RK}/chapters/(?P<n>\\d{{1,4}})\\.jpg")
    def item_chapter_image(req):
        r = require_item(req.user, req.params["rk"])
        db.release()  # la fabrication de l'image peut prendre un moment (partage réseau)
        path = ctx.chapter_images.get(r, int(req.params["n"]))
        if not path:
            raise HTTPError(404, "Image indisponible")
        with open(path, "rb") as f:
            return Response(f.read(), 200, "image/jpeg", {"Cache-Control": "private, max-age=604800"})

    # ----------------------------------------------------------- playback
    def stop_stream(sid, uid=None):
        row = db.one("SELECT id, user_id FROM streams WHERE id=?", (sid,))
        if row and (uid is None or row["user_id"] == uid):
            db.run("DELETE FROM streams WHERE id=?", (sid,))
            ctx.transcoder.stop(sid)
        elif uid is None:
            ctx.transcoder.stop(sid)

    @app.route("POST", "/api/play")
    def play(req):
        b = req.json()
        rk = str(b.get("id") or "")
        if not rk.isdigit():
            raise HTTPError(400, "Identifiant invalide")
        uid = req.user["id"]
        r = require_item(req.user, rk)
        if r["type"] not in ("movie", "episode", "extra"):
            raise HTTPError(400, "Cet élément n'est pas lisible")
        if not media.available():
            raise HTTPError(503, "ffmpeg n'est pas installé sur le serveur")
        if not r["key"] or not os.path.isfile(r["key"]):
            raise HTTPError(404, "Fichier introuvable : le partage est-il monté ?")
        probe = json.loads(r["probe"] or "{}")
        audio, subs = stream_lists(probe)
        prefs = get_prefs(uid)
        quality = b.get("quality") or prefs["quality"]
        if quality not in QUALITY:
            raise HTTPError(400, "Qualité invalide")
        duration = int(r["duration_ms"] or 0)
        try:
            offset = max(0, int(b.get("offset") or 0))
        except (TypeError, ValueError):
            raise HTTPError(400, "Position invalide")
        if duration:
            offset = min(offset, max(0, duration - 5000))
        audio_id = b.get("audio")
        if audio_id is not None and audio_id not in {a["id"] for a in audio}:
            raise HTTPError(400, "Piste audio invalide")
        sub_id = b.get("subtitle")
        sub_map = {s["id"]: s for s in subs}
        if sub_id not in (None, 0) and sub_id not in sub_map:
            raise HTTPError(400, "Sous-titres invalides")
        sub = sub_map.get(sub_id) if sub_id else None
        caps = parse_caps(b.get("caps"))
        # PGS : rendu par le navigateur quand il le peut, incrusté sinon
        pgs_client = bool(sub and sub.get("pgs") and caps.get("pgs") and not b.get("burn"))
        burn = sub["id"] if sub and not sub["overlay"] and not pgs_client else None
        if b.get("replace"):
            stop_stream(str(b["replace"]), uid)
        p = plan(probe, audio_id, burn, quality, caps, db.setting("downmix_algo", "none"),
                 float(db.setting("downmix_boost", "2") or 2))
        if p["mode"] == "direct" and (b.get("no_direct") or media.is_strm(r["key"])):
            p["mode"] = "copy"  # le navigateur a refusé la lecture directe, ou flux distant (.strm) relayé par Nexis
        sid = secrets.token_urlsafe(12)
        try:
            kf = keyframes_for(r) if p["mode"] == "copy" else None
            actual = ctx.transcoder.start(sid, r["key"], p, offset, duration, kf)
        except Busy as e:
            raise HTTPError(503, str(e))
        except media.MediaError as e:
            raise HTTPError(502, f"Lecture impossible : {e}")
        now = int(time.time())
        c = card(r)
        title = c["title"] if c["type"] == "movie" else f"{c.get('show')} · S{c.get('season')}E{c.get('episode')}"
        db.run("INSERT INTO streams(id,user_id,plex_session,rating_key,params,title,quality,position_ms,created_at,last_seen) "
               "VALUES(?,?,?,?,?,?,?,?,?,?)", (sid, uid, sid, rk, json.dumps(p), title, quality, actual, now, now))
        if r["type"] != "extra":
            upsert_meta(uid, c)
        nxt = next_episode(r)
        if b.get("playlist"):
            nxt = x.playlist_next(req.user, b["playlist"], r["id"]) if str(b["playlist"]).isdigit() else None
        if ctx.subtitles.missing(r):
            ctx.subtitles.ensure_now(r)
        elif ctx.subtitles.needs_trick(r):
            ctx.subtitles.enqueue(r["id"])  # vignettes : en tâche de fond, après la lecture
        if nxt and ctx.subtitles.missing(nxt):
            ctx.subtitles.enqueue(nxt["id"], urgent=True)  # débit plafonné pour ne pas gêner ce flux
        url = f"/api/stream/{sid}/file" if p["mode"] == "direct" else f"/api/stream/{sid}/main.m3u8"
        return {
            "stream": sid, "mode": p["mode"], "reasons": p["reasons"], "url": url, "offset": actual,
            "duration": duration, "quality": quality, "item": c, "audio": audio, "subtitles": subs,
            "audio_selected": p["audio_index"], "subtitle_selected": sub["id"] if sub else 0,
            "subtitle_overlay": f"/api/items/{rk}/subtitles/{sub['id']}" if sub and sub["overlay"] else None,
            "subtitle_ass": f"/api/items/{rk}/subtitles/{sub['id']}/ass" if sub and sub.get("ass") else None,
            "subtitle_pgs": f"/api/items/{rk}/subtitles/{sub['id']}/sup" if pgs_client else None,
            "markers": markers_for(r), "chapters": chapters_for(r), "next": card(nxt) if nxt else None,
        }

    VIDEO_CAPS = {"h264", "hevc", "hevc10", "av1", "av1_10", "vp9", "mpeg2video", "vc1"}
    AUDIO_CAPS = {"aac", "mp3", "ac3", "eac3", "flac", "opus", "dts", "truehd", "vorbis"}
    CONTAINER_CAPS = {"mp4", "mov", "webm", "matroska", "mpegts"}

    def parse_caps(c):
        """Profil de décodage déclaré par le navigateur."""
        if not isinstance(c, dict):
            return {"video": {"h264"}, "audio": {"aac", "mp3"}, "containers": {"mp4", "mov"}, "pgs": False}
        pick = lambda key, allowed: {x for x in (c.get(key) or []) if isinstance(x, str) and x in allowed}
        return {"video": pick("video", VIDEO_CAPS) or {"h264"}, "audio": pick("audio", AUDIO_CAPS) or {"aac"},
                "containers": pick("containers", CONTAINER_CAPS) or {"mp4"}, "pgs": bool(c.get("pgs")),
                # application native (Android TV) : lecture directe des MKV, son multicanal transmis tel quel
                "native": c.get("native") is True, "multichannel": c.get("multichannel") is True}

    kf_cache = {}

    def keyframes_for(r):
        key = (r["id"], r["file_mtime"])
        if key not in kf_cache:
            if len(kf_cache) > 200:
                kf_cache.clear()
            kf_cache[key] = mkv_keyframes(r["key"]) if (r["key"] or "").lower().endswith((".mkv", ".mk3d")) else None
        return kf_cache[key]

    def own_stream(req, sid):
        st = db.one("SELECT * FROM streams WHERE id=? AND user_id=?", (sid, req.user["id"]))
        if not st:
            raise HTTPError(404, "Flux expiré")
        now = int(time.time())
        if now - st["last_seen"] >= 15:
            db.run("UPDATE streams SET last_seen=? WHERE id=?", (now, sid))
        return st

    @app.route("GET", f"/api/stream/{SID}/main.m3u8")
    def stream_playlist(req):
        sid = req.params["sid"]
        own_stream(req, sid)
        try:
            text = ctx.transcoder.playlist(sid)
        except StreamError as e:
            raise HTTPError(404, str(e))
        return Response(text, 200, "application/vnd.apple.mpegurl", {"Cache-Control": "no-store"})

    def segment_response(path, ctype):
        with open(path, "rb") as f:
            data = f.read()
        return Response(data, 200, ctype, {"Cache-Control": "no-store"})

    @app.route("GET", f"/api/stream/{SID}/init.mp4")
    def stream_init(req):
        sid = req.params["sid"]
        own_stream(req, sid)
        db.release()  # l'attente peut durer : on ne garde pas la connexion SQLite
        try:
            return segment_response(ctx.transcoder.init(sid), "video/mp4")
        except StreamError as e:
            raise HTTPError(502, str(e))

    @app.route("GET", f"/api/stream/{SID}/(?P<seg>\\d{{1,6}}\\.m4s)")
    def stream_segment(req):
        sid = req.params["sid"]
        own_stream(req, sid)
        db.release()
        try:
            return segment_response(ctx.transcoder.segment(sid, req.params["seg"]), "video/iso.segment")
        except StreamError as e:
            raise HTTPError(404, str(e))

    @app.route("GET", f"/api/items/{RK}/subtitles/(?P<sub>\\d{{1,6}})/sup")
    def item_subtitles_sup(req):
        r = require_item(req.user, req.params["rk"])
        data, until = ctx.subtitles.read_sup(r, int(req.params["sub"])), None
        if data is None:
            part = ctx.subtitles.partial(r, int(req.params["sub"]), "sup")
            if not part:
                raise HTTPError(404, "Sous-titres PGS indisponibles")
            data, until = part
        return Response(data, 200, "application/octet-stream", until_headers(until))

    @app.route("GET", f"/api/stream/{SID}/file")
    def stream_file(req):
        st = own_stream(req, req.params["sid"])
        r = db.one("SELECT key FROM items WHERE id=?", (int(st["rating_key"]),))
        if not r or not os.path.isfile(r["key"]) or media.is_strm(r["key"]):
            raise HTTPError(404, "Fichier introuvable")
        return file_response(req, r["key"])

    def file_response(req, path):
        size = os.path.getsize(path)
        start, end, status = 0, size - 1, 200
        rng = (req.headers.get("Range") or "").strip()
        if rng:
            m = re.match(r"^bytes=(\d*)-(\d*)$", rng)
            if not m or (not m.group(1) and not m.group(2)):
                raise HTTPError(416, "Plage invalide")
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end or start >= size:
                return Response(b"", 416, "text/plain", {"Content-Range": f"bytes */{size}"})
            status = 206
        length = end - start + 1
        f = open(path, "rb")
        f.seek(start)

        def gen():
            left = length
            while left > 0:
                chunk = f.read(min(262144, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk
        headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        low = path.lower()
        ctype = ("video/mp4" if low.endswith((".mp4", ".m4v", ".mov")) else "video/x-matroska"
                 if low.endswith((".mkv", ".mk3d")) else "video/mp2t" if low.endswith((".ts", ".m2ts")) else "video/webm")
        return Response(stream=gen(), status=status, content_type=ctype, length=length, headers=headers,
                        on_close=f.close)

    def record_progress(user, r, pos, dur, state="playing", sid="", ended=False):
        """Position de lecture d'un utilisateur (interface de Nexis et applications). -> vu à l'instant ?"""
        uid = user["id"]
        rk = str(r["id"])
        if r["type"] == "extra":  # les bonus ne laissent ni reprise ni historique
            if sid and state == "stopped":
                stop_stream(sid, uid)
            elif sid:
                ctx.transcoder.touch(sid)
                db.run("UPDATE streams SET last_seen=? WHERE id=? AND user_id=?", (int(time.time()), sid, uid))
            return False
        row = db.one("SELECT * FROM progress WHERE user_id=? AND rating_key=?", (uid, rk))
        if not row:
            upsert_meta(uid, card(r))
            row = db.one("SELECT * FROM progress WHERE user_id=? AND rating_key=?", (uid, rk))
        dur = dur or row["duration_ms"]
        finished = bool(ended) or (dur > 0 and pos >= dur * WATCHED_RATIO)
        watched_now = False
        now = int(time.time())
        if finished:
            if row["position_ms"] > 0 or not row["watched"]:
                c = json.loads(row["meta"])
                c["id"] = rk
                set_watched(uid, c, True)
                watched_now = True
        else:
            db.run("UPDATE progress SET position_ms=?, duration_ms=?, updated_at=? WHERE user_id=? AND rating_key=?",
                   (pos, dur, now, uid, rk))
        if sid and db.scalar("SELECT 1 FROM streams WHERE id=? AND user_id=?", (sid, uid)):
            if state == "stopped":
                stop_stream(sid, uid)
            else:
                db.run("UPDATE streams SET position_ms=?, state=?, last_seen=? WHERE id=?", (pos, state, now, sid))
                ctx.transcoder.touch(sid)
        return watched_now

    @app.route("POST", "/api/progress")
    def progress(req):
        b = req.json()
        rk = str(b.get("id") or "")
        if not rk.isdigit():
            raise HTTPError(400, "Identifiant invalide")
        try:
            pos = max(0, int(b.get("position") or 0))
            dur = max(0, int(b.get("duration") or 0))
        except (TypeError, ValueError):
            raise HTTPError(400, "Position invalide")
        state = b.get("state") if b.get("state") in ("playing", "paused", "stopped") else "playing"
        r = require_item(req.user, rk)
        watched = record_progress(req.user, r, pos, dur, state, str(b.get("stream") or ""), b.get("ended"))
        return {"ok": True, "watched": watched}

    # ------------------------------------------------------------ téléchargements (hors ligne)
    JID = r"(?P<jid>[A-Za-z0-9_-]{8,20})"

    def own_job(req):
        job = ctx.downloads.get(req.params["jid"], req.user["id"])
        if not job:
            raise HTTPError(404, "Téléchargement introuvable : relancez-le")
        return job

    DL_QUALITIES = ("auto", "1080p", "720p", "480p")

    def download_plan(req, b, quality):
        """Plan de préparation d'un téléchargement pour CET appareil : on ne convertit que ce qu'il ne sait pas lire."""
        if db.setting("downloads", "1") != "1":
            raise HTTPError(403, "Les téléchargements sont désactivés par l'administrateur")
        rk = str(b.get("id") or "")
        if not rk.isdigit():
            raise HTTPError(400, "Identifiant invalide")
        r = require_item(req.user, rk)
        if r["type"] not in ("movie", "episode"):
            raise HTTPError(400, "Seuls les films et les épisodes se téléchargent")
        if not r["key"] or not os.path.isfile(r["key"]):
            raise HTTPError(404, "Fichier introuvable : le partage est-il monté ?")
        probe = json.loads(r["probe"] or "{}")
        audio, subs = stream_lists(probe)
        if quality not in DL_QUALITIES and quality not in QUALITY:
            raise HTTPError(400, "Qualité invalide")
        audio_id = b.get("audio")
        if audio_id is not None and audio_id not in {a["id"] for a in audio}:
            raise HTTPError(400, "Piste audio invalide")
        burn = b.get("burn")  # sous-titres image (PGS, VobSub) : incrustés dans la vidéo téléchargée
        if burn is not None and burn not in {s["id"] for s in subs if not s["overlay"]}:
            raise HTTPError(400, "Sous-titres à incruster invalides")
        caps = parse_caps(b.get("caps"))
        caps["containers"] = {"mp4"}  # le fichier gardé est un MP4
        p = plan(probe, audio_id, burn, "original" if quality == "auto" else quality, caps,
                 db.setting("downmix_algo", "none"), float(db.setting("downmix_boost", "2") or 2))
        if p["mode"] == "direct" and media.is_strm(r["key"]):
            p["mode"] = "copy"  # flux distant : on en fait un fichier
        duration = probe.get("duration") or (r["duration_ms"] or 0) / 1000
        if p["mode"] == "direct":
            size = r["file_size"] or 0
        elif p["mode"] == "copy":  # vidéo recopiée, son en AAC stéréo
            size = int((r["file_size"] or 0) * 0.95) if not media.is_strm(r["key"]) else 0
        else:
            size = int((p["kbps"] + 192) * 1000 / 8 * duration)
        return r, p, duration, size

    @app.route("POST", "/api/downloads/options")
    def download_options(req):
        """Pour chaque qualité : taille et conversion nécessaire ou non (le dialogue de téléchargement l'affiche)."""
        b = req.json()
        out = []
        for q in DL_QUALITIES:
            r, p, _, size = download_plan(req, b, q)
            out.append({"quality": q, "convert": p["mode"] == "transcode", "size": size,
                        "height": p["height"] if p["mode"] == "transcode" else p["src_height"], "reasons": p["reasons"]})
        return {"options": out}

    @app.route("POST", "/api/downloads")
    def download_request(req):
        b = req.json()
        r, p, duration, _ = download_plan(req, b, b.get("quality") or "auto")
        if p["mode"] == "transcode" and ctx.limits()[1] == 0:
            raise HTTPError(403, "Ce titre doit être converti pour être téléchargé, et la conversion est désactivée sur ce serveur")
        try:
            job = ctx.downloads.request(req.user["id"], r, p, duration)
        except DownloadError as e:
            raise HTTPError(429, str(e))
        out = job.view()
        out["estimate"] = job.size or ctx.downloads._estimate(job)
        return out

    @app.route("GET", f"/api/downloads/{JID}")
    def download_status(req):
        return own_job(req).view()

    @app.route("GET", f"/api/downloads/{JID}/file")
    def download_file(req):
        job = own_job(req)
        if job.state != "ready":
            raise HTTPError(409, "Pas encore prêt")
        db.release()  # transfert long : pas de connexion à la base pendant ce temps
        resp = file_response(req, job.path)
        resp.headers["Content-Type"] = "video/mp4"
        return resp

    @app.route("DELETE", f"/api/downloads/{JID}")
    def download_done(req):
        ctx.downloads.delete(req.params["jid"], req.user["id"])
        return {"ok": True}

    @app.route("POST", "/api/progress/offline")
    def progress_offline(req):
        """Positions enregistrées sans réseau, renvoyées au retour de la connexion (la plus récente gagne)."""
        items = req.json().get("items")
        if not isinstance(items, list) or len(items) > 500:
            raise HTTPError(400, "Liste invalide")
        done = 0
        for it in items:
            try:
                rk, pos, dur, at = str(int(it["id"])), max(0, int(it["position"])), max(0, int(it.get("duration") or 0)), int(it["at"])
                r = require_item(req.user, rk)
            except (KeyError, TypeError, ValueError, HTTPError):
                continue
            row = db.one("SELECT updated_at FROM progress WHERE user_id=? AND rating_key=?", (req.user["id"], rk))
            if row and row["updated_at"] > at:
                continue  # regardé depuis sur un autre appareil : on ne recule pas
            record_progress(req.user, r, pos, dur, "stopped", "", bool(it.get("ended")))
            done += 1
        return {"ok": True, "applied": done}

    @app.route("POST", "/api/stop")
    def stop(req):
        stop_stream(str(req.json().get("stream") or ""), req.user["id"])
        return {"ok": True}

    x.__dict__.update(stop_stream=stop_stream, record_progress=record_progress, file_response=file_response,
                      keyframes_for=keyframes_for)
