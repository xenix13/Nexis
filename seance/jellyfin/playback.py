"""Lecture depuis les clients Jellyfin : décision de lecture, flux direct et HLS, sous-titres, progression.

Accès aux flux sans jeton dans l'adresse (le lecteur d'Android TV n'en ajoute pas) :
- lecture directe : l'étiquette (ETag) de la source est une capacité signée, liée au jeton du client ;
- HLS : l'identifiant de la session de lecture (aléatoire, 128 bits) fait partie du chemin.
"""
import hmac
import json
import os
import re
import secrets
import time

from .. import media
from ..catalog import EXTERNAL_BASE
from ..subtitles import parse_subtitles
from ..transcoder import QUALITY, Busy, StreamError, plan
from ..web import HTTPError, Response
from .common import GUID_RE, K_ITEM, K_SEGMENT, TICKS, guid, no_content, unguid
from .dto import Builder, probe_of
from .profile import (IMAGE_FORMATS, SUB_FORMATS, can_direct_play, container_name, quality_for, stream_caps,
                      subtitle_method)

I = rf"(?P<iid>{GUID_RE})"
SID = r"(?P<sid>[A-Za-z0-9_-]{16,40})"
MIME = {"mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime", "mkv": "video/x-matroska",
        "webm": "video/webm", "avi": "video/x-msvideo", "ts": "video/mp2t", "wmv": "video/x-ms-wmv",
        "asf": "video/x-ms-asf"}


def ms_vtt(ms):
    h, rem = divmod(int(ms), 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def to_vtt(cues):
    out = ["WEBVTT", ""]
    for c in cues:
        text = c["t"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        out += [f"{ms_vtt(c['s'])} --> {ms_vtt(c['e'])}" + (" line:10%" if c.get("top") else ""), text, ""]
    return "\n".join(out)


def to_srt(cues):
    out = []
    for n, c in enumerate(cues, 1):
        out += [str(n), f"{ms_vtt(c['s']).replace('.', ',')} --> {ms_vtt(c['e']).replace('.', ',')}", c["t"], ""]
    return "\n".join(out)


def to_json(cues):
    return {"TrackEvents": [{"Id": str(n), "Text": c["t"], "StartPositionTicks": c["s"] * TICKS,
                             "EndPositionTicks": c["e"] * TICKS} for n, c in enumerate(cues)]}


def register(app, ctx, x, jf):
    db = ctx.db
    route = app.compat_route

    def device_tag(user):
        return "jf:" + user["t_hash"][:16]

    def stream_cap(user, item_id):
        return user["t_hash"][:12] + jf.mac("direct", item_id, user["t_hash"], size=24)

    def cap_user(tag, item_id):
        """Utilisateur d'une capacité de lecture directe (ETag), ou None."""
        if not tag or not re.fullmatch(r"[0-9a-f]{36}", tag):
            return None
        prefix = tag[:12]
        rows = db.all("SELECT token_hash FROM jf_tokens WHERE token_hash >= ? AND token_hash < ?",
                      (prefix, prefix + "g"))
        for r in rows:
            if hmac.compare_digest(stream_cap({"t_hash": r["token_hash"]}, item_id), tag):
                u = db.one("SELECT u.*, t.token_hash AS t_hash FROM jf_tokens t JOIN users u ON u.id=t.user_id "
                           "WHERE t.token_hash=?", (r["token_hash"],))
                if u and not u["disabled"]:
                    u.pop("password_hash", None)
                    return u
        return None

    # ------------------------------------------------------------ choix des pistes par défaut
    def default_tracks(user, row, probe):
        prefs = x.get_prefs(user["id"])
        tp = x.track_prefs(user["id"], row) or {}
        audios = probe.get("audio") or []
        want_audio = tp.get("audio_lang") or prefs.get("audio_lang") or ""
        audio = next((a for a in audios if want_audio and a.get("lang") == want_audio), None) \
            or next((a for a in audios if a.get("default")), None) or (audios[0] if audios else None)
        subs = [s for s in probe.get("subs") or [] if (s.get("codec") or "") in media.TEXT_SUB_CODECS | media.IMAGE_SUB_CODECS]
        subs = [dict(s, _i=s["index"]) for s in subs] + \
            [dict(e, _i=EXTERNAL_BASE + n) for n, e in enumerate(probe.get("external") or [])]
        sub_lang = tp.get("sub_lang") if tp.get("sub_lang") not in (None, "") else prefs.get("sub_lang") or ""
        mode = prefs.get("sub_mode") or "foreign"
        chosen = None
        if sub_lang != "off":
            same = [s for s in subs if s.get("lang") == sub_lang]
            full = next((s for s in same if not s.get("forced")), None)
            forced = next((s for s in same if s.get("forced")), None)
            if mode == "always":
                chosen = full or forced
            elif mode == "foreign":
                chosen = (full or forced) if (audio or {}).get("lang") not in ("", None, sub_lang) else forced
            else:
                chosen = forced
        return (audio or {}).get("index"), chosen["_i"] if chosen else -1

    # ------------------------------------------------------------ PlaybackInfo
    def playback_info(req, user):
        iid = req.params["iid"]
        row = jf.item_by_guid(user, iid)
        if not row or row["type"] not in ("movie", "episode", "extra"):
            raise HTTPError(404, "Item not found")
        body = jf.body(req) if req.method == "POST" else {}

        def p(name, default=None):
            for k, v in body.items():
                if k.lower() == name.lower() and v is not None:
                    return v
            return jf.q(req, name, default)

        def pint(name, default=None):
            v = p(name)
            try:
                return int(v) if v is not None and v != "" else default
            except (TypeError, ValueError):
                return default

        def pbool(name, default=True):
            v = p(name)
            if v is None:
                return default
            return v if isinstance(v, bool) else str(v).lower() == "true"
        profile = body.get("DeviceProfile") if isinstance(body.get("DeviceProfile"), dict) else None
        probe = probe_of(row)
        b = Builder(jf, user)
        source = b.media_source(row, probe)
        play_sid = secrets.token_urlsafe(16)
        response = {"MediaSources": [source], "PlaySessionId": play_sid}
        if not row["key"] or not os.path.isfile(row["key"]):
            response["ErrorCode"] = "NoCompatibleStream"
            return response
        default_audio, default_sub = default_tracks(user, row, probe)
        audio_index = pint("AudioStreamIndex")
        if audio_index not in {a["index"] for a in probe.get("audio") or []}:
            audio_index = default_audio
        sub_index = pint("SubtitleStreamIndex")
        valid_subs = {s["Index"]: s for s in source["MediaStreams"] if s["Type"] == "Subtitle"}
        if sub_index is None or (sub_index != -1 and sub_index not in valid_subs):
            sub_index = default_sub
        source["DefaultAudioStreamIndex"] = audio_index
        source["DefaultSubtitleStreamIndex"] = sub_index
        source["ETag"] = stream_cap(user, row["id"])
        audio = next((a for a in probe.get("audio") or [] if a["index"] == audio_index), None)
        if profile is None:
            # simple demande d'informations (sans profil d'appareil) : rien n'est préparé
            for s in valid_subs.values():
                s["DeliveryMethod"] = "External"
                s["DeliveryUrl"] = sub_url(user, row, s)
            return response
        max_bitrate = pint("MaxStreamingBitrate") or pint("MaxStreamingBitrate", 0) or \
            (profile.get("MaxStreamingBitrate") if isinstance(profile.get("MaxStreamingBitrate"), int) else 0)
        container = container_name(probe, row["key"])
        direct_ok, why = can_direct_play(profile, probe, row["key"], audio, max_bitrate)
        direct_ok = direct_ok and pbool("EnableDirectPlay")
        # sous-titres : comment chaque piste arrive à l'appareil
        burn = None
        for s in valid_subs.values():
            fmt = (s["Codec"] or "").lower()
            method, out_fmt = subtitle_method(profile, fmt, "direct" if direct_ok else "hls", container)
            s["DeliveryMethod"] = method
            if method == "External":
                s["DeliveryUrl"] = sub_url(user, row, s, out_fmt)
            if s["Index"] == sub_index and method == "Encode":
                if fmt in IMAGE_FORMATS and s["Index"] < EXTERNAL_BASE:
                    burn = s["Index"]
                    direct_ok = False
                else:
                    s["DeliveryMethod"] = "External"
                    s["DeliveryUrl"] = sub_url(user, row, s, "vtt")
        if direct_ok:
            source.update(SupportsDirectPlay=True, SupportsDirectStream=False, SupportsTranscoding=False)
            register_stream(user, row, play_sid, {"mode": "direct", "reasons": []}, "original")
            return response
        if not pbool("EnableTranscoding"):
            source.update(SupportsDirectPlay=False, SupportsDirectStream=False, SupportsTranscoding=False)
            response["ErrorCode"] = "NoCompatibleStream"
            return response
        caps = stream_caps(profile, probe, audio, pint("MaxAudioChannels"), pbool("AllowVideoStreamCopy"),
                           pbool("AllowAudioStreamCopy"))
        if not pbool("EnableDirectStream"):
            caps["video"] = set()
        quality = quality_for(max_bitrate, probe, QUALITY)
        p_ = plan(probe, audio_index, burn, quality, caps, db.setting("downmix_algo", "none"),
                  float(db.setting("downmix_boost", "2") or 2))
        if p_["mode"] == "direct":
            p_["mode"] = "copy"
        start_ms = max(0, (pint("StartTimeTicks", 0) or 0) // TICKS)
        duration = int(row["duration_ms"] or 0)
        if duration:
            start_ms = min(start_ms, max(0, duration - 5000))
        try:
            kf = x.keyframes_for(row) if p_["mode"] == "copy" else None
            ctx.transcoder.start(play_sid, row["key"], p_, start_ms, duration, kf, lazy=True)
        except Busy:
            source.update(SupportsDirectPlay=False, SupportsDirectStream=False, SupportsTranscoding=False)
            response["ErrorCode"] = "RateLimitExceeded"
            return response
        register_stream(user, row, play_sid, p_, quality)
        source.update(
            SupportsDirectPlay=False, SupportsDirectStream=False, SupportsTranscoding=True,
            TranscodingUrl=f"/videos/{guid(K_ITEM, row['id'])}/hls/{play_sid}/main.m3u8?PlaySessionId={play_sid}"
                           f"&MediaSourceId={guid(K_ITEM, row['id'])}",
            TranscodingSubProtocol="hls", TranscodingContainer="mp4")
        if p_["mode"] == "transcode":
            source["TranscodeReasons"] = ["VideoCodecNotSupported"]
        return response

    def register_stream(user, row, sid, p_, quality):
        now = int(time.time())
        c = x.card(row)
        title = c["title"] if c["type"] != "episode" else f"{c.get('show')} · S{c.get('season')}E{c.get('episode')}"
        db.run("INSERT INTO streams(id,user_id,plex_session,rating_key,params,title,quality,position_ms,created_at,"
               "last_seen) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (sid, user["id"], device_tag(user), str(row["id"]), json.dumps(p_), title, quality, 0, now, now))
        if row["type"] != "extra":
            x.upsert_meta(user["id"], c)
        if ctx.subtitles.missing(row):
            ctx.subtitles.ensure_now(row)

    def sub_url(user, row, s, fmt=None):
        """Adresse d'une piste de sous-titres : signée (pas de jeton dans l'adresse)."""
        fmt = fmt or ("vtt" if s["IsTextSubtitleStream"] else "sup")
        if fmt in IMAGE_FORMATS:
            fmt = "sup"
        iid = guid(K_ITEM, row["id"])
        return f"/Videos/{iid}/{iid}/Subtitles/{s['Index']}/0/Stream.{fmt}?Tag={stream_cap(user, row['id'])}"

    for method in ("GET", "POST"):
        route(method, f"/Items/{I}/PlaybackInfo")(lambda req: playback_info(req, jf.query_user(req)))

    # ------------------------------------------------------------ lecture directe du fichier
    def direct_stream(req):
        iid = req.params["iid"]
        n = unguid(iid, K_ITEM)
        if n is None:
            raise HTTPError(404, "Item not found")
        user = jf.token_user(req, required=False) or cap_user(jf.q(req, "Tag") or "", n)
        if not user:
            jf.require_enabled()
            raise HTTPError(401, "Unauthorized")
        row = jf.item_by_guid(user, iid)
        if not row or row["type"] not in ("movie", "episode", "extra") or not row["key"] or not os.path.isfile(row["key"]):
            raise HTTPError(404, "Item not found")
        if (jf.q(req, "Static") or "true").lower() != "true":
            raise HTTPError(400, "Only static streams are supported")
        db.release()
        resp = x.file_response(req, row["key"])
        resp.headers["Content-Type"] = MIME.get(container_name(probe_of(row), row["key"]), "application/octet-stream")
        return resp

    route("GET", rf"/Videos/{I}/stream(?:\.(?P<ext>\w{{1,5}}))?")(direct_stream)
    route("GET", rf"/Videos/{I}/(?P<file>[^/]{{1,80}})/stream(?:\.(?P<ext>\w{{1,5}}))?")(direct_stream)

    # ------------------------------------------------------------ HLS
    def hls_stream(req):
        sid = req.params["sid"]
        st = db.one("SELECT * FROM streams WHERE id=?", (sid,))
        n = unguid(req.params["iid"], K_ITEM)
        if not jf.enabled() or not st or n is None or st["rating_key"] != str(n) or not st["plex_session"].startswith("jf:"):
            raise HTTPError(404, "Stream not found")
        now = int(time.time())
        if now - st["last_seen"] >= 10:
            db.run("UPDATE streams SET last_seen=? WHERE id=?", (now, sid))
        if not ctx.transcoder.get(sid) or ctx.transcoder.get(sid).proc is None:
            # le client consomme ce flux : ses autres lectures (épisode précédent…) s'arrêtent
            for other in db.all("SELECT id FROM streams WHERE plex_session=? AND id<>?", (st["plex_session"], sid)):
                x.stop_stream(other["id"])
        return st

    @route("GET", rf"/videos/{I}/hls/{SID}/main\.m3u8")
    def jf_hls_playlist(req):
        sid = req.params["sid"]
        hls_stream(req)
        try:
            text = ctx.transcoder.playlist(sid)
        except StreamError as e:
            raise HTTPError(404, str(e))
        return Response(text, 200, "application/vnd.apple.mpegurl", {"Cache-Control": "no-store"})

    @route("GET", rf"/videos/{I}/hls/{SID}/init\.mp4")
    def jf_hls_init(req):
        sid = req.params["sid"]
        hls_stream(req)
        db.release()
        try:
            path = ctx.transcoder.init(sid)
        except StreamError as e:
            raise HTTPError(502, str(e))
        with open(path, "rb") as f:
            return Response(f.read(), 200, "video/mp4", {"Cache-Control": "no-store"})

    @route("GET", rf"/videos/{I}/hls/{SID}/(?P<seg>\d{{1,6}}\.m4s)")
    def jf_hls_segment(req):
        sid = req.params["sid"]
        hls_stream(req)
        db.release()
        try:
            path = ctx.transcoder.segment(sid, req.params["seg"])
        except StreamError as e:
            raise HTTPError(404, str(e))
        with open(path, "rb") as f:
            return Response(f.read(), 200, "video/iso.segment", {"Cache-Control": "no-store"})

    @route("DELETE", "/Videos/ActiveEncodings")
    def jf_stop_encoding(req):
        user = jf.token_user(req)
        sid = jf.q(req, "PlaySessionId") or ""
        if sid:
            x.stop_stream(sid, user["id"])
        return no_content()

    # ------------------------------------------------------------ sous-titres
    def subtitle(req):
        n = unguid(req.params["iid"], K_ITEM)
        user = jf.token_user(req, required=False) or (cap_user(jf.q(req, "Tag") or "", n) if n is not None else None)
        if not user:
            raise HTTPError(401, "Unauthorized")
        row = jf.item_by_guid(user, req.params["iid"])
        if not row:
            raise HTTPError(404, "Item not found")
        index = int(req.params["index"])
        fmt = req.params["fmt"].lower()
        probe = probe_of(row)
        db.release()
        if index < EXTERNAL_BASE:
            s = next((s for s in probe.get("subs") or [] if s["index"] == index), None)
            if not s:
                raise HTTPError(404, "Subtitle not found")
            if SUB_FORMATS.get(s["codec"]) in IMAGE_FORMATS:
                data = ctx.subtitles.read_sup(row, index)
                if data is None:
                    job = ctx.subtitles.ensure_now(row)
                    if job:
                        ctx.subtitles.wait(job, 120)
                    data = ctx.subtitles.read_sup(row, index)
                if data is None:
                    raise HTTPError(404, "Subtitle not ready")
                return Response(data, 200, "application/octet-stream", {"Cache-Control": "private, max-age=3600"})
            if fmt in ("ass", "ssa") and s["codec"] in ("ass", "ssa"):
                data = ctx.subtitles.read_ass(row, index)
                if data is not None:
                    return Response(data, 200, "text/x-ssa; charset=utf-8", {"Cache-Control": "private, max-age=3600"})
            raw = ctx.subtitles.read(row, index)
            if raw is None:
                job = ctx.subtitles.ensure_now(row)
                if job:
                    ctx.subtitles.wait(job, 120)
                raw = ctx.subtitles.read(row, index)
            if raw is None:
                part = ctx.subtitles.partial(row, index, "srt")
                cues = parse_subtitles(part[0]) if part else []
            else:
                data = json.loads(raw)
                cues = data.get("cues", []) if isinstance(data, dict) else data
        else:
            ext = probe.get("external") or []
            if index - EXTERNAL_BASE >= len(ext):
                raise HTTPError(404, "Subtitle not found")
            e = ext[index - EXTERNAL_BASE]
            try:
                if fmt in ("ass", "ssa") and e["codec"] in ("ass", "ssa"):
                    with open(e["path"], "rb") as f:
                        return Response(f.read(30 * 1024 * 1024), 200, "text/x-ssa; charset=utf-8")
                if e["codec"] in ("subrip", "webvtt"):
                    with open(e["path"], "rb") as f:
                        raw = f.read(10 * 1024 * 1024)
                else:
                    raw = media.extract_subtitle(e["path"])
            except (OSError, media.MediaError):
                raise HTTPError(404, "Subtitle not found")
            cues = parse_subtitles(raw)
        if fmt == "js":
            return to_json(cues)
        if fmt == "srt":
            return Response(to_srt(cues), 200, "application/x-subrip; charset=utf-8")
        return Response(to_vtt(cues), 200, "text/vtt; charset=utf-8")

    sub_path = r"/Subtitles/(?P<index>\d{1,6})(?:/(?P<start>\d{1,15}))?/Stream\.(?P<fmt>\w{1,8})"
    route("GET", rf"/Videos/{I}/(?P<msid>{GUID_RE}){sub_path}")(subtitle)
    route("GET", rf"/Videos/{I}{sub_path}")(subtitle)

    # ------------------------------------------------------------ aperçus (trickplay) et segments
    @route("GET", rf"/Videos/{I}/Trickplay/(?P<width>\d{{1,5}})/(?P<n>\d{{1,5}})\.jpg")
    def jf_trickplay(req):
        user = jf.token_user(req)
        row = jf.item_by_guid(user, req.params["iid"])
        if not row:
            raise HTTPError(404, "Item not found")
        path = os.path.join(ctx.subtitles.trick_dir(row), f"{int(req.params['n']) + 1}.jpg")
        try:
            with open(path, "rb") as f:
                return Response(f.read(), 200, "image/jpeg", {"Cache-Control": "private, max-age=604800"})
        except OSError:
            raise HTTPError(404, "Not found")

    @route("GET", rf"/Videos/{I}/Trickplay/(?P<width>\d{{1,5}})/tiles\.m3u8")
    def jf_trickplay_playlist(req):
        jf.token_user(req)
        raise HTTPError(404, "Not found")

    @route("GET", rf"/MediaSegments/{I}")
    def jf_segments(req):
        user = jf.token_user(req)
        row = jf.item_by_guid(user, req.params["iid"])
        if not row or row["type"] not in ("movie", "episode"):
            return {"Items": [], "TotalRecordCount": 0, "StartIndex": 0}
        wanted = {t.lower() for t in jf.qlist(req, "IncludeSegmentTypes")}
        out = []
        for n, (kind, jtype) in enumerate((("intro", "Intro"), ("credits", "Outro"))):
            m = x.markers_for(row).get(kind)
            if not m or (wanted and jtype.lower() not in wanted):
                continue
            out.append({"Id": guid(K_SEGMENT, row["id"] * 4 + n), "ItemId": guid(K_ITEM, row["id"]), "Type": jtype,
                        "StartTicks": int(m["start"]) * TICKS, "EndTicks": int(m["end"]) * TICKS})
        return {"Items": out, "TotalRecordCount": len(out), "StartIndex": 0}

    # ------------------------------------------------------------ progression de lecture
    def report(req, user, state, body=None):
        b = body if body is not None else jf.body(req)
        iid = b.get("ItemId") or req.params.get("iid") or ""
        row = jf.item_by_guid(user, str(iid))
        if not row or row["type"] not in ("movie", "episode", "extra"):
            return no_content()
        try:
            ticks = int(b.get("PositionTicks") if b.get("PositionTicks") is not None
                        else jf.q(req, "PositionTicks", -1))
        except (TypeError, ValueError):
            ticks = -1
        sid = str(b.get("PlaySessionId") or jf.q(req, "PlaySessionId") or "")
        if not sid:  # lecture directe sans identifiant : la session de cet appareil pour cet élément
            r = db.one("SELECT id FROM streams WHERE plex_session=? AND rating_key=? ORDER BY created_at DESC",
                       (device_tag(user), str(row["id"])))
            sid = r["id"] if r else ""
        if b.get("IsPaused") and state == "playing":
            state = "paused"
        if ticks < 0:
            # pas de position (certains arrêts) : on garde la dernière connue
            st = db.one("SELECT position_ms FROM streams WHERE id=? AND user_id=?", (sid, user["id"])) if sid else None
            if state == "stopped" and sid:
                x.stop_stream(sid, user["id"])
            if not st:
                return no_content()
            ticks = st["position_ms"] * TICKS
        pos = ticks // TICKS
        x.record_progress(user, row, pos, int(row["duration_ms"] or 0), state, sid, False)
        return no_content()

    @route("POST", "/Sessions/Playing")
    def jf_playing(req):
        return report(req, jf.token_user(req), "playing")

    @route("POST", "/Sessions/Playing/Progress")
    def jf_progress(req):
        return report(req, jf.token_user(req), "playing")

    @route("POST", "/Sessions/Playing/Stopped")
    def jf_stopped(req):
        return report(req, jf.token_user(req), "stopped")

    @route("POST", "/Sessions/Playing/Ping")
    def jf_ping(req):
        user = jf.token_user(req)
        sid = jf.q(req, "PlaySessionId") or ""
        if sid and db.scalar("SELECT 1 FROM streams WHERE id=? AND user_id=?", (sid, user["id"])):
            ctx.transcoder.touch(sid)
            db.run("UPDATE streams SET last_seen=? WHERE id=?", (int(time.time()), sid))
        return no_content()

    # routes historiques (/Users/{id}/PlayingItems/{itemId}) et 10.9+ (/PlayingItems/{itemId})
    def legacy_report(state):
        def handler(req):
            user = jf.target_user(req, req.params.get("uid") or "")
            body = {"ItemId": req.params["iid"], "PositionTicks": jf.q(req, "PositionTicks"),
                    "PlaySessionId": jf.q(req, "PlaySessionId"), "IsPaused": jf.qbool(req, "IsPaused", False)}
            if body["PositionTicks"] is None:
                body.pop("PositionTicks")
            return report(req, user, state, body)
        return handler
    U = rf"(?:Users/(?P<uid>Me|{GUID_RE})/)?"
    route("POST", rf"/{U}PlayingItems/{I}")(legacy_report("playing"))
    route("POST", rf"/{U}PlayingItems/{I}/Progress")(legacy_report("playing"))
    route("DELETE", rf"/{U}PlayingItems/{I}")(legacy_report("stopped"))

    @route("GET", "/Playback/BitrateTest")
    def jf_bitrate_test(req):
        jf.token_user(req)
        size = jf.qint(req, "Size", 102400, 1, 10 * 1024 * 1024)
        return Response(os.urandom(size), 200, "application/octet-stream", {"Cache-Control": "no-store"})
