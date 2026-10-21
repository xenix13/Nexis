"""Séance HTTP API."""
import concurrent.futures
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid

from . import plex as plexmod
from .auth import (Throttle, check_credentials, clear_session, create_session, hash_password,
                   load_user, validate_password, verify_password)
from .db import DB
from .plex import Plex, PlexError
from .subtitles import parse_subtitles
from .web import App, HTTPError, Response

log = logging.getLogger("seance")

VERSION = "1.0.0"
RK = r"(?P<rk>\d{1,12})"
QUALITIES = {
    "original": {"maxVideoBitrate": 100000, "videoResolution": "3840x2160", "videoQuality": 100},
    "1080p": {"maxVideoBitrate": 10000, "videoResolution": "1920x1080", "videoQuality": 100},
    "720p": {"maxVideoBitrate": 4000, "videoResolution": "1280x720", "videoQuality": 75},
    "480p": {"maxVideoBitrate": 1500, "videoResolution": "854x480", "videoQuality": 60},
}
PROFILE_EXTRA = ("add-transcode-target(type=videoProfile&context=streaming&protocol=hls&container=mpegts"
                 "&videoCodec=h264&audioCodec=aac,mp3&replace=true)")
TEXT_SUBS = {"srt", "subrip", "vtt", "webvtt"}
SORTS = {"title": "titleSort", "added": "addedAt:desc", "year": "year:desc",
         "rating": "audienceRating:desc", "release": "originallyAvailableAt:desc"}
WATCHED_RATIO = 0.90
MIN_RESUME_MS = 30_000
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
LANG = re.compile(r"^[a-z]{2,3}$")
USERNAME = re.compile(r"^[A-Za-z0-9._-]{2,32}$")
HLS_SUB = re.compile(r"^session/[A-Za-z0-9_-]{1,64}/[A-Za-z0-9_./-]{1,200}$")
IMG_PATH = re.compile(r"^/library/metadata/(\d{1,12})/[A-Za-z]{2,20}(?:/\d{1,14})?$")
LANG_ALIASES = {"fre": "fra", "ger": "deu", "chi": "zho", "dut": "nld", "gre": "ell", "per": "fas",
                "cze": "ces", "rum": "ron", "slo": "slk", "ice": "isl", "arm": "hye", "geo": "kat",
                "fr": "fra", "en": "eng", "ja": "jpn", "de": "deu", "es": "spa", "it": "ita"}

DEFAULT_PREFS = {
    "quality": "original",
    "audio_lang": "fra",
    "sub_mode": "foreign",
    "sub_lang": "fra",
    "sub_size": 100,
    "autoplay_next": True,
    "auto_skip_intro": False,
    "accent": "#e0a84f",
    "theme": "dark",
    "poster_size": "m",
    "hidden_libraries": [],
    "show_continue": True,
}


def norm_lang(code):
    code = (code or "").lower()
    return LANG_ALIASES.get(code, code)


def _bool(v):
    return bool(v) and v not in ("0", "false")


def validate_prefs(data):
    out = {}
    for k, v in data.items():
        if k not in DEFAULT_PREFS:
            continue
        ok = False
        if k == "quality":
            ok = v in QUALITIES
        elif k in ("audio_lang", "sub_lang"):
            ok = isinstance(v, str) and (v == "" or LANG.match(v))
        elif k == "sub_mode":
            ok = v in ("never", "always", "foreign")
        elif k == "sub_size":
            ok = isinstance(v, int) and 60 <= v <= 200
        elif k in ("autoplay_next", "auto_skip_intro", "show_continue"):
            ok = isinstance(v, bool)
        elif k == "accent":
            ok = isinstance(v, str) and HEX.match(v)
        elif k == "theme":
            ok = v in ("dark", "oled", "light")
        elif k == "poster_size":
            ok = v in ("s", "m", "l")
        elif k == "hidden_libraries":
            ok = isinstance(v, list) and len(v) <= 100 and all(isinstance(x, str) and x.isdigit() for x in v)
        if not ok:
            raise HTTPError(400, f"Préférence invalide : {k}")
        out[k] = v
    return out


def card(m):
    """Compact, client-safe view of a Plex metadata object."""
    t = m.get("type")
    c = {
        "id": str(m.get("ratingKey")),
        "type": t,
        "title": m.get("title"),
        "year": m.get("year"),
        "thumb": m.get("thumb"),
        "art": m.get("art"),
        "duration": m.get("duration"),
        "section": str(m["librarySectionID"]) if m.get("librarySectionID") is not None else None,
        "rating": m.get("audienceRating") or m.get("rating"),
        "content_rating": m.get("contentRating"),
        "added": m.get("addedAt"),
    }
    if t == "episode":
        c.update(show=m.get("grandparentTitle"), show_id=str(m.get("grandparentRatingKey") or ""),
                 season_id=str(m.get("parentRatingKey") or ""), season=m.get("parentIndex"),
                 episode=m.get("index"), show_thumb=m.get("grandparentThumb"),
                 show_art=m.get("grandparentArt"), aired=m.get("originallyAvailableAt"))
    elif t == "season":
        c.update(show=m.get("parentTitle"), show_id=str(m.get("parentRatingKey") or ""),
                 index=m.get("index"), leaves=m.get("leafCount"), show_thumb=m.get("parentThumb"))
    elif t == "show":
        c.update(leaves=m.get("leafCount"), seasons=m.get("childCount"))
    return c


def stream_info(m):
    try:
        part = m["Media"][0]["Part"][0]
    except (KeyError, IndexError, TypeError):
        return None, [], []
    audio, subs = [], []
    for s in part.get("Stream", []):
        if s.get("streamType") == 2:
            audio.append({"id": s.get("id"), "lang": norm_lang(s.get("languageCode")),
                          "title": s.get("extendedDisplayTitle") or s.get("displayTitle") or "Piste audio",
                          "codec": s.get("codec"), "channels": s.get("channels"),
                          "selected": bool(s.get("selected")), "default": bool(s.get("default"))})
        elif s.get("streamType") == 3:
            codec = (s.get("codec") or "").lower()
            subs.append({"id": s.get("id"), "lang": norm_lang(s.get("languageCode")),
                         "title": s.get("extendedDisplayTitle") or s.get("displayTitle") or "Sous-titres",
                         "codec": codec, "forced": bool(s.get("forced")),
                         "external": bool(s.get("key")),
                         "overlay": bool(s.get("key")) and codec in TEXT_SUBS,
                         "selected": bool(s.get("selected"))})
    return part, audio, subs


class Ctx:
    def __init__(self, data_dir, static_dir, cookie_secure=False, trust_proxy=False):
        self.data_dir = data_dir
        self.static_dir = static_dir
        self.cookie_secure = cookie_secure
        self.trust_proxy = trust_proxy
        os.makedirs(os.path.join(data_dir, "cache", "img"), exist_ok=True)
        self.db = DB(os.path.join(data_dir, "seance.db"))
        self.throttle = Throttle()
        self.pins = {}
        self.pin_tokens = {}
        self.lock = threading.Lock()
        self.last_ping = {}
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=6)
        self._plex = None
        self._plex_sig = None
        client_id = self.db.setting("client_id")
        if not client_id:
            client_id = "seance-" + uuid.uuid4().hex
            self.db.set_setting("client_id", client_id)
        self.client_id = client_id
        self.setup_code = None
        if not self.db.scalar("SELECT COUNT(*) FROM users"):
            self.setup_code = secrets.token_hex(4).upper()
            path = os.path.join(data_dir, "setup-code.txt")
            with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
                f.write(self.setup_code + "\n")
            log.warning("Premier lancement : code d'installation = %s (aussi dans %s)", self.setup_code, path)
        self.db.release()

    @property
    def plex(self):
        db = self.db
        sig = (db.setting("plex_url"), db.setting("plex_token"), db.setting("plex_verify_tls", "1"))
        with self.lock:
            if self._plex is None or sig != self._plex_sig:
                self._plex = Plex(sig[0], sig[1], self.client_id, verify_tls=sig[2] == "1")
                self._plex_sig = sig
            return self._plex

    def stop_transcode(self, plex_session, wait=False):
        p = self.plex

        def run():
            try:
                with p.open("/video/:/transcode/universal/stop", {"session": plex_session}, timeout=10) as r:
                    r.read()
            except Exception:
                pass
        if wait:
            run()  # the old transcode must be gone before the next one starts (CPU, session limits)
        else:
            self.pool.submit(run)


def build_app(ctx):
    app = App()
    app.static_dir = ctx.static_dir
    db = ctx.db
    app.teardown.append(db.release)

    # ------------------------------------------------------------------ hooks
    def setup_allowed(req):
        if req.user and req.user["is_admin"]:
            return True
        code = req.headers.get("X-Setup-Code") or ""
        return bool(ctx.setup_code) and hmac.compare_digest(code.strip().upper(), ctx.setup_code)

    def before(req, opts):
        if req.method != "GET" and req.headers.get("X-Seance") != "1":
            raise HTTPError(403, "En-tête de requête manquant")
        req.user = load_user(db, req)
        auth = opts.get("auth", "user")
        if auth == "user" and not req.user:
            raise HTTPError(401, "Connexion requise")
        if auth == "admin" and not (req.user and req.user["is_admin"]):
            raise HTTPError(403 if req.user else 401, "Réservé aux administrateurs")
        if auth == "setup" and not setup_allowed(req):
            raise HTTPError(403, "Code d'installation invalide")
    app.before.append(before)

    def plex_call(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except PlexError as e:
            raise HTTPError(e.status, str(e))

    # -------------------------------------------------------------- helpers
    def video_sections():
        return [s for s in plex_call(ctx.plex.sections) if s["type"] in ("movie", "show")]

    def allowed_ids(user):
        ids = [s["id"] for s in video_sections()]
        if user["is_admin"] or user["all_libraries"]:
            return ids
        mine = {r["section_id"] for r in db.all("SELECT section_id FROM user_libraries WHERE user_id=?",
                                                 (user["id"],))}
        return [i for i in ids if i in mine]

    def allowed_set(user):
        return set(allowed_ids(user))

    def require_section(user, section):
        if str(section) not in allowed_set(user):
            raise HTTPError(404, "Introuvable")

    def require_item(user, rk):
        sec = plex_call(ctx.plex.section_for, rk)
        require_section(user, sec)

    def get_prefs(uid):
        raw = db.scalar("SELECT data FROM prefs WHERE user_id=?", (uid,))
        p = dict(DEFAULT_PREFS)
        if raw:
            try:
                p.update(json.loads(raw))
            except json.JSONDecodeError:
                pass
        return p

    def attach_state(uid, cards):
        if not cards:
            return cards
        keys = list({c["id"] for c in cards})
        state = {}
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for r in db.all(f"SELECT rating_key,position_ms,duration_ms,watched FROM progress "
                            f"WHERE user_id=? AND rating_key IN ({marks})", [uid, *chunk]):
                state[r["rating_key"]] = r
            for col in ("show_key", "season_key"):
                for r in db.all(f"SELECT {col} AS k, COUNT(*) AS n FROM progress WHERE user_id=? AND watched=1 "
                                f"AND {col} IN ({marks}) GROUP BY {col}", [uid, *chunk]):
                    state.setdefault("count:" + r["k"], 0)
                    state["count:" + r["k"]] += r["n"]
        for c in cards:
            if c["type"] in ("show", "season"):
                n = state.get("count:" + c["id"], 0)
                c["watched_count"] = n
                c["watched"] = bool(c.get("leaves")) and n >= c["leaves"]
            else:
                s = state.get(c["id"])
                c["watched"] = bool(s and s["watched"])
                c["position"] = s["position_ms"] if s else 0
        return cards

    def upsert_meta(uid, c):
        now = int(time.time())
        db.run("INSERT INTO progress(user_id,rating_key,updated_at,type,show_key,season_key,section_id,meta,duration_ms) "
               "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,rating_key) DO UPDATE SET "
               "type=excluded.type, show_key=excluded.show_key, season_key=excluded.season_key, "
               "section_id=excluded.section_id, meta=excluded.meta, "
               "duration_ms=CASE WHEN excluded.duration_ms>0 THEN excluded.duration_ms ELSE progress.duration_ms END",
               (uid, c["id"], now, c["type"], c.get("show_id") or None, c.get("season_id") or None,
                c.get("section"), json.dumps(c, ensure_ascii=False), c.get("duration") or 0))

    def set_watched(uid, c, watched):
        upsert_meta(uid, c)
        now = int(time.time())
        if watched:
            db.run("UPDATE progress SET watched=1, position_ms=0, view_count=view_count+1, "
                   "last_watched_at=?, updated_at=? WHERE user_id=? AND rating_key=?", (now, now, uid, c["id"]))
            recent = db.scalar("SELECT MAX(watched_at) FROM history WHERE user_id=? AND rating_key=?",
                               (uid, c["id"]))
            if not recent or now - recent > 3 * 3600:
                db.run("INSERT INTO history(user_id,rating_key,watched_at,section_id,meta) VALUES(?,?,?,?,?)",
                       (uid, c["id"], now, c.get("section"), json.dumps(c, ensure_ascii=False)))
        else:
            db.run("UPDATE progress SET watched=0, position_ms=0, updated_at=? WHERE user_id=? AND rating_key=?",
                   (now, uid, c["id"]))

    def markers_for(m):
        out = {}
        for mk in m.get("Marker", []) or []:
            if mk.get("type") in ("intro", "credits") and mk.get("type") not in out:
                out[mk["type"]] = {"start": mk.get("startTimeOffset"), "end": mk.get("endTimeOffset"),
                                   "source": "plex"}
        for r in db.all("SELECT kind,start_ms,end_ms,source FROM markers WHERE rating_key=?",
                        (str(m.get("ratingKey")),)):
            out[r["kind"]] = {"start": r["start_ms"], "end": r["end_ms"], "source": r["source"]}
        return out

    def next_episode(ep):
        show = ep.get("grandparentRatingKey")
        if not show:
            return None
        leaves = plex_call(ctx.plex.all_leaves, str(show))
        ids = [str(x.get("ratingKey")) for x in leaves]
        try:
            i = ids.index(str(ep.get("ratingKey")))
        except ValueError:
            return None
        return leaves[i + 1] if i + 1 < len(leaves) else None

    # ---------------------------------------------------------- public / auth
    @app.route("GET", "/api/status", auth=None)
    def status(req):
        users = db.scalar("SELECT COUNT(*) FROM users")
        return {"setup": users == 0, "plex": ctx.plex.configured, "version": VERSION}

    def resolve_token(body, allow_stored=False):
        token = (body.get("plex_token") or "").strip()
        pin_id = str(body.get("pin_id") or "")
        if not token and pin_id:
            entry = ctx.pin_tokens.get(pin_id)
            token = entry[1] if entry else ""
        if not token and allow_stored:
            token = db.setting("plex_token") or ""
        if not token:
            raise HTTPError(400, "Jeton Plex manquant")
        if len(token) > 200:
            raise HTTPError(400, "Jeton Plex invalide")
        return token

    def test_plex(url, token, verify):
        url = (url or "").strip().rstrip("/")
        if not re.match(r"^https?://[^\s/]+(:\d+)?$", url):
            raise HTTPError(400, "Adresse Plex invalide (ex. http://192.168.1.10:32400)")
        p = Plex(url, token, ctx.client_id, verify_tls=verify)
        try:
            ident = p.identity()
            sections = p.sections()
        except PlexError as e:
            raise HTTPError(400, str(e))
        return url, {"name": ident.get("friendlyName"), "version": ident.get("version"),
                     "libraries": sections}

    @app.route("POST", "/api/plex/test", auth="setup")
    def plex_test(req):
        b = req.json()
        token = resolve_token(b, allow_stored=bool(req.user))
        _, info = test_plex(b.get("plex_url"), token, b.get("verify_tls", True) is not False)
        return info

    @app.route("POST", "/api/plex/pin", auth="setup")
    def plex_pin(req):
        try:
            pin = plexmod.create_pin(ctx.client_id)
        except PlexError as e:
            raise HTTPError(502, str(e))
        with ctx.lock:
            now = time.time()
            for k in [k for k, v in ctx.pins.items() if now - v > 900]:
                ctx.pins.pop(k, None)
            ctx.pins[str(pin["id"])] = now
        return {"id": pin["id"], "auth_url": pin["auth_url"]}

    @app.route("GET", r"/api/plex/pin/(?P<pid>\d{1,15})", auth="setup")
    def plex_pin_check(req):
        pid = req.params["pid"]
        if pid not in ctx.pins:
            raise HTTPError(404, "Demande expirée, recommence")
        try:
            token = plexmod.check_pin(ctx.client_id, pid)
        except PlexError as e:
            raise HTTPError(502, str(e))
        if token:
            ctx.pin_tokens[pid] = (time.time(), token)
        return {"authorized": bool(token)}

    @app.route("POST", "/api/setup", auth="setup")
    def setup(req):
        if db.scalar("SELECT COUNT(*) FROM users"):
            raise HTTPError(409, "Déjà configuré")
        b = req.json()
        username = (b.get("username") or "").strip()
        if not USERNAME.match(username):
            raise HTTPError(400, "Identifiant : 2 à 32 caractères (lettres, chiffres, . _ -)")
        validate_password(b.get("password"))
        verify = b.get("verify_tls", True) is not False
        url, info = test_plex(b.get("plex_url"), resolve_token(b), verify)
        token = resolve_token(b)
        with db.tx():
            if db.scalar("SELECT COUNT(*) FROM users"):
                raise HTTPError(409, "Déjà configuré")
            cur = db.run("INSERT INTO users(username,display_name,password_hash,is_admin,created_at) "
                         "VALUES(?,?,?,1,?)", (username, (b.get("display_name") or username)[:50],
                                               hash_password(b["password"]), int(time.time())))
            uid = cur.lastrowid
            db.set_setting("plex_url", url)
            db.set_setting("plex_token", token)
            db.set_setting("plex_verify_tls", "1" if verify else "0")
        ctx.setup_code = None
        ctx.pin_tokens.clear()
        try:
            os.remove(os.path.join(ctx.data_dir, "setup-code.txt"))
        except OSError:
            pass
        create_session(db, req, uid, ctx.cookie_secure)
        return {"ok": True, "server": info["name"]}

    @app.route("POST", "/api/login", auth=None)
    def login(req):
        b = req.json()
        username = str(b.get("username") or "").strip()[:64]
        password = str(b.get("password") or "")[:256]
        keys = ("ip:" + req.client_ip, "u:" + username.lower())
        ctx.throttle.check(*keys)
        user = check_credentials(db, username, password)
        if not user:
            ctx.throttle.fail(*keys)
            raise HTTPError(401, "Identifiant ou mot de passe incorrect")
        ctx.throttle.reset(keys[1])
        create_session(db, req, user["id"], ctx.cookie_secure)
        return {"ok": True}

    @app.route("POST", "/api/logout", auth=None)
    def logout(req):
        clear_session(db, req, ctx.cookie_secure)
        return {"ok": True}

    # ------------------------------------------------------------------- me
    def user_view(u):
        return {"id": u["id"], "username": u["username"], "display_name": u["display_name"],
                "is_admin": bool(u["is_admin"]), "avatar_color": u["avatar_color"]}

    @app.route("GET", "/api/me")
    def me(req):
        out = {"user": user_view(req.user), "prefs": get_prefs(req.user["id"]), "libraries": [],
               "plex_error": None}
        try:
            allowed = allowed_set(req.user)
            out["libraries"] = [s for s in video_sections() if s["id"] in allowed]
        except HTTPError as e:
            out["plex_error"] = e.message
        return out

    @app.route("PUT", "/api/me/prefs")
    def me_prefs(req):
        p = get_prefs(req.user["id"])
        p.update(validate_prefs(req.json()))
        db.run("INSERT INTO prefs(user_id,data) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",
               (req.user["id"], json.dumps(p)))
        return p

    @app.route("PUT", "/api/me/profile")
    def me_profile(req):
        b = req.json()
        name = str(b.get("display_name") or "").strip()
        color = b.get("avatar_color")
        if not 1 <= len(name) <= 50:
            raise HTTPError(400, "Nom affiché : 1 à 50 caractères")
        if not (isinstance(color, str) and HEX.match(color)):
            raise HTTPError(400, "Couleur invalide")
        db.run("UPDATE users SET display_name=?, avatar_color=? WHERE id=?", (name, color, req.user["id"]))
        return {"ok": True}

    @app.route("PUT", "/api/me/password")
    def me_password(req):
        b = req.json()
        row = db.one("SELECT password_hash FROM users WHERE id=?", (req.user["id"],))
        if not verify_password(str(b.get("current") or ""), row["password_hash"]):
            raise HTTPError(400, "Mot de passe actuel incorrect")
        validate_password(b.get("new"))
        db.run("UPDATE users SET password_hash=? WHERE id=?", (hash_password(b["new"]), req.user["id"]))
        db.run("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (req.user["id"], req.session_hash))
        return {"ok": True}

    # -------------------------------------------------------------- library
    @app.route("GET", "/api/libraries")
    def libraries(req):
        allowed = allowed_set(req.user)
        return [s for s in video_sections() if s["id"] in allowed]

    def section_type(sid):
        for s in video_sections():
            if s["id"] == sid:
                return s["type"]
        raise HTTPError(404, "Bibliothèque introuvable")

    @app.route("GET", r"/api/libraries/(?P<sid>\d{1,6})/items")
    def library_items(req):
        sid = req.params["sid"]
        require_section(req.user, sid)
        stype = section_type(sid)
        params = {
            "type": 1 if stype == "movie" else 2,
            "sort": SORTS.get(req.arg("sort", "title"), "titleSort"),
            "X-Plex-Container-Start": req.int_arg("start", 0, 0),
            "X-Plex-Container-Size": req.int_arg("size", 60, 1, 200),
        }
        genre = req.arg("genre")
        if genre:
            if not genre.isdigit():
                raise HTTPError(400, "Genre invalide")
            params["genre"] = genre
        mc = plex_call(ctx.plex.get, f"/library/sections/{sid}/all", params)
        items = []
        for m in mc.get("Metadata", []):
            m.setdefault("librarySectionID", sid)
            ctx.plex.remember(m)
            items.append(card(m))
        attach_state(req.user["id"], items)
        return {"total": mc.get("totalSize", mc.get("size", len(items))), "items": items}

    @app.route("GET", r"/api/libraries/(?P<sid>\d{1,6})/genres")
    def library_genres(req):
        sid = req.params["sid"]
        require_section(req.user, sid)
        mc = plex_call(ctx.plex.get, f"/library/sections/{sid}/genre")
        out = []
        for d in mc.get("Directory", []):
            key = str(d.get("key") or "")
            m = re.search(r"genre=(\d+)", key)
            gid = m.group(1) if m else key
            if gid.isdigit():
                out.append({"id": gid, "title": d.get("title")})
        return out

    def recent_row(section):
        sid = section["id"]
        if section["type"] == "movie":
            mc = ctx.plex.get(f"/library/sections/{sid}/all", {
                "type": 1, "sort": "addedAt:desc", "X-Plex-Container-Start": 0, "X-Plex-Container-Size": 24})
            items = []
            for m in mc.get("Metadata", []):
                m.setdefault("librarySectionID", sid)
                ctx.plex.remember(m)
                items.append(card(m))
            return items
        mc = ctx.plex.get(f"/library/sections/{sid}/all", {
            "type": 4, "sort": "addedAt:desc", "X-Plex-Container-Start": 0, "X-Plex-Container-Size": 120})
        seen, items = set(), []
        for m in mc.get("Metadata", []):
            m.setdefault("librarySectionID", sid)
            ctx.plex.remember(m)
            show = str(m.get("grandparentRatingKey") or "")
            if not show or show in seen:
                continue
            seen.add(show)
            items.append({"id": show, "type": "show", "title": m.get("grandparentTitle"),
                          "thumb": m.get("grandparentThumb"), "art": m.get("grandparentArt"),
                          "section": sid, "latest": {"season": m.get("parentIndex"), "episode": m.get("index")},
                          "leaves": None})
            if len(items) >= 24:
                break
        return items

    def continue_row(user, allowed):
        uid = user["id"]
        rows = db.all("SELECT rating_key,position_ms,duration_ms,updated_at,meta,section_id,show_key FROM progress "
                      "WHERE user_id=? AND position_ms>=? AND (duration_ms=0 OR position_ms < duration_ms*?) "
                      "ORDER BY updated_at DESC LIMIT 30", (uid, MIN_RESUME_MS, WATCHED_RATIO))
        items, shows_in_progress = [], set()
        for r in rows:
            if r["section_id"] not in allowed:
                continue
            c = json.loads(r["meta"])
            c.update(position=r["position_ms"], duration=r["duration_ms"] or c.get("duration"),
                     watched=False, _t=r["updated_at"])
            items.append(c)
            if r["show_key"]:
                shows_in_progress.add(r["show_key"])
        recent_shows = db.all(
            "SELECT show_key, MAX(last_watched_at) AS t FROM progress WHERE user_id=? AND watched=1 "
            "AND show_key IS NOT NULL AND last_watched_at > ? GROUP BY show_key ORDER BY t DESC LIMIT 12",
            (uid, int(time.time()) - 60 * 86400))
        watched = {}
        for r in recent_shows:
            if r["show_key"] in shows_in_progress:
                continue
            for w in db.all("SELECT rating_key,last_watched_at FROM progress WHERE user_id=? AND show_key=? "
                            "AND watched=1", (uid, r["show_key"])):
                watched[w["rating_key"]] = w["last_watched_at"] or 0

        def next_up(show_key, t):
            try:
                leaves = ctx.plex.all_leaves(show_key)
            except PlexError:
                return None
            last_i = None
            best = -1
            for i, ep in enumerate(leaves):
                wt = watched.get(str(ep.get("ratingKey")))
                if wt is not None and wt >= best:
                    best, last_i = wt, i
            if last_i is None:
                return None
            for ep in leaves[last_i + 1:]:
                if str(ep.get("ratingKey")) not in watched:
                    c = card(ep)
                    if c.get("section") not in allowed:
                        return None
                    c.update(next_up=True, position=0, watched=False, _t=t)
                    return c
            return None

        jobs = [ctx.pool.submit(next_up, r["show_key"], r["t"]) for r in recent_shows
                if r["show_key"] not in shows_in_progress]
        for j in jobs:
            c = j.result()
            if c:
                items.append(c)
        items.sort(key=lambda c: c.pop("_t", 0), reverse=True)
        return items[:24]

    @app.route("GET", "/api/home")
    def home(req):
        user = req.user
        prefs = get_prefs(user["id"])
        allowed = allowed_set(user)
        sections = [s for s in video_sections() if s["id"] in allowed and s["id"] not in prefs["hidden_libraries"]]
        jobs = [(s, ctx.pool.submit(recent_row, s)) for s in sections]
        rows = []
        for s, j in jobs:
            try:
                items = attach_state(user["id"], j.result())
            except PlexError as e:
                log.warning("Ligne %s : %s", s["title"], e)
                continue
            if items:
                rows.append({"library": s, "items": items})
        cont = continue_row(user, allowed) if prefs["show_continue"] else []
        hero = None
        for r in rows:
            for c in r["items"]:
                if c.get("art"):
                    hero = c
                    break
            if hero:
                break
        return {"continue": cont, "rows": rows, "hero": hero}

    @app.route("GET", "/api/search")
    def search(req):
        q = (req.arg("q") or "").strip()
        if len(q) < 2:
            return {"movies": [], "shows": [], "episodes": []}
        allowed = allowed_set(req.user)
        mc = plex_call(ctx.plex.get, "/hubs/search", {"query": q[:100], "limit": 20, "includeCollections": 0})
        out = {"movies": [], "shows": [], "episodes": []}
        bucket = {"movie": "movies", "show": "shows", "episode": "episodes"}
        for hub in mc.get("Hub", []):
            for m in hub.get("Metadata", []) or []:
                b = bucket.get(m.get("type"))
                if not b:
                    continue
                sec = m.get("librarySectionID")
                if sec is None:
                    try:
                        sec = ctx.plex.section_for(m.get("ratingKey"))
                    except PlexError:
                        continue
                if str(sec) not in allowed:
                    continue
                m["librarySectionID"] = sec
                ctx.plex.remember(m)
                out[b].append(card(m))
        for v in out.values():
            attach_state(req.user["id"], v)
        return out

    @app.route("GET", f"/api/items/{RK}")
    def item(req):
        rk = req.params["rk"]
        m = plex_call(ctx.plex.metadata, rk, markers=True)
        require_section(req.user, m.get("librarySectionID"))
        uid = req.user["id"]
        d = card(m)
        d.update(
            summary=m.get("summary"), tagline=m.get("tagline"), studio=m.get("studio"),
            released=m.get("originallyAvailableAt"),
            genres=[g.get("tag") for g in m.get("Genre", []) or []],
            directors=[g.get("tag") for g in m.get("Director", []) or []],
            cast=[{"name": r.get("tag"), "role": r.get("role")} for r in (m.get("Role", []) or [])[:20]],
        )
        attach_state(uid, [d])
        if d["type"] in ("movie", "episode"):
            part, audio, subs = stream_info(m)
            media = (m.get("Media") or [{}])[0]
            d.update(audio=audio, subtitles=subs, markers=markers_for(m), media={
                "resolution": media.get("videoResolution"), "video_codec": media.get("videoCodec"),
                "audio_codec": media.get("audioCodec"), "container": media.get("container"),
                "bitrate": media.get("bitrate")})
            if d["type"] == "episode":
                nxt = next_episode(m)
                d["next"] = attach_state(uid, [card(nxt)])[0] if nxt else None
        elif d["type"] == "show":
            seasons = [card(s) for s in plex_call(ctx.plex.children, rk) if s.get("type") == "season"]
            for s in seasons:
                s["section"] = d["section"]
            d["seasons"] = attach_state(uid, seasons)
            leaves = plex_call(ctx.plex.all_leaves, rk)
            eps = attach_state(uid, [card(e) for e in leaves])
            target = None
            for e in eps:
                if e["position"] > 0 and not e["watched"]:
                    target = e
                    break
            if not target:
                last_watched = max((i for i, e in enumerate(eps) if e["watched"]), default=-1)
                for e in eps[last_watched + 1:]:
                    if not e["watched"]:
                        target = e
                        break
            d["play_target"] = target or (eps[0] if eps else None)
        elif d["type"] == "season":
            d["show_id"] = str(m.get("parentRatingKey") or "")
        return d

    @app.route("GET", f"/api/items/{RK}/children")
    def item_children(req):
        rk = req.params["rk"]
        require_item(req.user, rk)
        out = []
        for m in plex_call(ctx.plex.children, rk):
            c = card(m)
            c["summary"] = m.get("summary")
            out.append(c)
        return attach_state(req.user["id"], out)

    @app.route("POST", f"/api/items/{RK}/watched")
    def item_watched(req):
        rk = req.params["rk"]
        watched = bool(req.json().get("watched"))
        m = plex_call(ctx.plex.metadata, rk)
        require_section(req.user, m.get("librarySectionID"))
        uid = req.user["id"]
        if m.get("type") == "show":
            targets = plex_call(ctx.plex.all_leaves, rk)
        elif m.get("type") == "season":
            targets = plex_call(ctx.plex.children, rk)
        elif m.get("type") in ("movie", "episode"):
            targets = [m]
        else:
            raise HTTPError(400, "Type non pris en charge")
        with db.tx():
            for t in targets:
                c = card(t)
                c["section"] = c["section"] or str(m.get("librarySectionID"))
                set_watched(uid, c, watched)
        return {"ok": True, "count": len(targets)}

    @app.route("GET", f"/api/items/{RK}/subtitles/(?P<sub>\\d{{1,12}})")
    def item_subtitles(req):
        rk, sub = req.params["rk"], int(req.params["sub"])
        m = plex_call(ctx.plex.metadata, rk)
        require_section(req.user, m.get("librarySectionID"))
        stream = None
        for media in m.get("Media", []) or []:
            for part in media.get("Part", []) or []:
                for s in part.get("Stream", []) or []:
                    if s.get("id") == sub and s.get("streamType") == 3 and s.get("key"):
                        stream = s
        if not stream or not str(stream["key"]).startswith("/library/streams/"):
            raise HTTPError(404, "Sous-titres introuvables")
        with plex_call(ctx.plex.open, stream["key"]) as r:
            raw = r.read(5 * 1024 * 1024)
        return {"cues": parse_subtitles(raw)}

    # ------------------------------------------------------------- images
    @app.route("GET", "/api/img")
    def image(req):
        p = req.arg("p") or ""
        m = IMG_PATH.match(p)
        if not m:
            raise HTTPError(400, "Image invalide")
        require_item(req.user, m.group(1))
        w = round(req.int_arg("w", 300, 40, 1920) / 20) * 20
        h = round(req.int_arg("h", 450, 40, 1920) / 20) * 20
        key = hashlib.sha1(f"{p}|{w}|{h}".encode()).hexdigest()
        path = os.path.join(ctx.data_dir, "cache", "img", key + ".jpg")
        headers = {"Cache-Control": "private, max-age=86400"}
        try:
            if time.time() - os.path.getmtime(path) < 14 * 86400:
                with open(path, "rb") as f:
                    return Response(f.read(), 200, "image/jpeg", headers)
        except OSError:
            pass
        with plex_call(ctx.plex.open, "/photo/:/transcode", {
                "width": w, "height": h, "minSize": 1, "upscale": 1, "url": p, "format": "jpeg"}) as r:
            data = r.read(8 * 1024 * 1024)
            ctype = r.headers.get("Content-Type", "image/jpeg")
        if not ctype.startswith("image/"):
            raise HTTPError(502, "Image invalide")
        tmp = path + f".{threading.get_ident()}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return Response(data, 200, ctype, headers)

    # ----------------------------------------------------------- playback
    def stop_stream(sid, uid=None, wait=False):
        row = db.one("SELECT * FROM streams WHERE id=?", (sid,))
        if row and (uid is None or row["user_id"] == uid):
            db.run("DELETE FROM streams WHERE id=?", (sid,))
            ctx.stop_transcode(row["plex_session"], wait=wait)
            ctx.last_ping.pop(row["plex_session"], None)

    @app.route("POST", "/api/play")
    def play(req):
        b = req.json()
        rk = str(b.get("id") or "")
        if not rk.isdigit():
            raise HTTPError(400, "Identifiant invalide")
        uid = req.user["id"]
        m = plex_call(ctx.plex.metadata, rk, markers=True)
        require_section(req.user, m.get("librarySectionID"))
        if m.get("type") not in ("movie", "episode"):
            raise HTTPError(400, "Cet élément n'est pas lisible")
        part, audio, subs = stream_info(m)
        if not part:
            raise HTTPError(404, "Aucun fichier pour cet élément")
        prefs = get_prefs(uid)
        quality = b.get("quality") or prefs["quality"]
        if quality not in QUALITIES:
            raise HTTPError(400, "Qualité invalide")
        duration = int(m.get("duration") or 0)
        try:
            offset = max(0, int(b.get("offset") or 0))
        except (TypeError, ValueError):
            raise HTTPError(400, "Position invalide")
        if duration:
            offset = min(offset, max(0, duration - 5000))
        audio_ids = {a["id"] for a in audio}
        sub_map = {s["id"]: s for s in subs}
        audio_id = b.get("audio")
        if audio_id is not None and audio_id not in audio_ids:
            raise HTTPError(400, "Piste audio invalide")
        sub_id = b.get("subtitle")
        if sub_id not in (None, 0) and sub_id not in sub_map:
            raise HTTPError(400, "Sous-titres invalides")
        sub = sub_map.get(sub_id) if sub_id else None
        overlay = bool(sub and sub["overlay"])
        sel = {"allParts": 1, "subtitleStreamID": sub["id"] if (sub and not overlay) else 0}
        if audio_id is not None:
            sel["audioStreamID"] = audio_id
        plex_call(ctx.plex.put, f"/library/parts/{int(part['id'])}", sel)

        if b.get("replace"):
            stop_stream(str(b["replace"]), uid, wait=True)
        plex_session = uuid.uuid4().hex
        params = {
            "hasMDE": 1, "path": f"/library/metadata/{rk}", "mediaIndex": 0, "partIndex": 0,
            "protocol": "hls", "fastSeek": 1, "directPlay": 0, "directStream": 1, "directStreamAudio": 1,
            "subtitleSize": prefs["sub_size"], "audioBoost": 100, "location": "lan", "subtitles": "burn",
            "autoAdjustQuality": 0, "mediaBufferSize": 102400, "session": plex_session,
            "offset": round(offset / 1000, 3), "X-Plex-Session-Identifier": plex_session,
            "X-Plex-Client-Profile-Extra": PROFILE_EXTRA, **QUALITIES[quality],
        }
        sid = secrets.token_urlsafe(12)
        now = int(time.time())
        c = card(m)
        title = c["title"] if c["type"] == "movie" else f"{c.get('show')} · S{c.get('season')}E{c.get('episode')}"
        db.run("INSERT INTO streams(id,user_id,plex_session,rating_key,params,title,quality,position_ms,created_at,last_seen) "
               "VALUES(?,?,?,?,?,?,?,?,?,?)", (sid, uid, plex_session, rk, json.dumps(params), title, quality,
                                              offset, now, now))
        upsert_meta(uid, c)
        nxt = next_episode(m) if c["type"] == "episode" else None
        return {
            "stream": sid,
            "url": f"/api/hls/{sid}/start.m3u8",
            "offset": offset,
            "duration": duration,
            "quality": quality,
            "item": c,
            "audio": audio,
            "subtitles": subs,
            "audio_selected": audio_id,
            "subtitle_selected": sub["id"] if sub else 0,
            "subtitle_overlay": f"/api/items/{rk}/subtitles/{sub['id']}" if overlay else None,
            "markers": markers_for(m),
            "next": card(nxt) if nxt else None,
        }

    def sanitize_playlist(text, sid):
        prefix = f"/api/hls/{sid}/"
        text = re.sub(r"https?://[^\s\"/]+/video/:/transcode/universal/", prefix, text)
        text = re.sub(r"(?<![\w/:])/video/:/transcode/universal/", prefix, text)
        text = re.sub(r"([?&])X-Plex-Token=[^&\s\"]*&?", r"\1", text)
        return text

    @app.route("GET", r"/api/hls/(?P<sid>[A-Za-z0-9_-]{8,40})/(?P<sub>.+)")
    def hls(req):
        sid, sub = req.params["sid"], req.params["sub"]
        st = db.one("SELECT * FROM streams WHERE id=? AND user_id=?", (sid, req.user["id"]))
        if not st:
            raise HTTPError(404, "Flux expiré")
        params = json.loads(st["params"])
        headers = {"X-Plex-Session-Identifier": st["plex_session"],
                   "X-Plex-Client-Profile-Extra": PROFILE_EXTRA}
        if sub == "start.m3u8":
            path, query = "/video/:/transcode/universal/start.m3u8", params
        elif HLS_SUB.match(sub) and ".." not in sub:
            path = "/video/:/transcode/universal/" + sub
            query = {k: v for k, v in req.query.items() if k.lower() != "x-plex-token"}
        else:
            raise HTTPError(404, "Introuvable")
        now = int(time.time())
        if now - st["last_seen"] >= 15:
            db.run("UPDATE streams SET last_seen=? WHERE id=?", (now, sid))
        r = plex_call(ctx.plex.open, path, query, timeout=120, extra_headers=headers)
        ctype = r.headers.get("Content-Type", "application/octet-stream")
        if sub.endswith(".m3u8") or "mpegurl" in ctype.lower():
            with r:
                text = r.read(4 * 1024 * 1024).decode("utf-8", "replace")
            return Response(sanitize_playlist(text, sid), 200, "application/vnd.apple.mpegurl",
                            {"Cache-Control": "no-store"})
        length = r.headers.get("Content-Length")

        def gen():
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                yield chunk
        return Response(stream=gen(), status=200, content_type=ctype,
                        length=int(length) if length and length.isdigit() else None,
                        headers={"Cache-Control": "no-store"}, on_close=r.close)

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
        uid = req.user["id"]
        require_item(req.user, rk)
        row = db.one("SELECT * FROM progress WHERE user_id=? AND rating_key=?", (uid, rk))
        if not row:
            upsert_meta(uid, card(plex_call(ctx.plex.metadata, rk)))
            row = db.one("SELECT * FROM progress WHERE user_id=? AND rating_key=?", (uid, rk))
        dur = dur or row["duration_ms"]
        finished = bool(b.get("ended")) or (dur > 0 and pos >= dur * WATCHED_RATIO)
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
        sid = str(b.get("stream") or "")
        if sid:
            st = db.one("SELECT plex_session FROM streams WHERE id=? AND user_id=?", (sid, uid))
            if st:
                if state == "stopped":
                    stop_stream(sid, uid)
                else:
                    db.run("UPDATE streams SET position_ms=?, state=?, last_seen=? WHERE id=?",
                           (pos, state, now, sid))
                    ps = st["plex_session"]
                    if now - ctx.last_ping.get(ps, 0) > 45:
                        ctx.last_ping[ps] = now
                        p = ctx.plex

                        def ping():
                            try:
                                with p.open("/video/:/transcode/universal/ping", {"session": ps}, timeout=10) as r:
                                    r.read()
                            except Exception:
                                pass
                        ctx.pool.submit(ping)
        return {"ok": True, "watched": watched_now}

    @app.route("POST", "/api/stop")
    def stop(req):
        stop_stream(str(req.json().get("stream") or ""), req.user["id"])
        return {"ok": True}

    # ------------------------------------------------------------ history
    @app.route("GET", "/api/history")
    def history(req):
        allowed = allowed_set(req.user)
        start = req.int_arg("start", 0, 0)
        size = req.int_arg("size", 50, 1, 200)
        rows = db.all("SELECT id,rating_key,watched_at,section_id,meta FROM history WHERE user_id=? "
                      "ORDER BY watched_at DESC LIMIT ? OFFSET ?", (req.user["id"], size, start))
        total = db.scalar("SELECT COUNT(*) FROM history WHERE user_id=?", (req.user["id"],))
        items = []
        for r in rows:
            c = json.loads(r["meta"])
            c.update(history_id=r["id"], watched_at=r["watched_at"],
                     available=r["section_id"] in allowed)
            if not c["available"]:
                c["thumb"] = c["art"] = c["show_thumb"] = None
            items.append(c)
        stats = db.one("SELECT COUNT(*) AS n, COALESCE(SUM(json_extract(meta,'$.duration')),0) AS ms "
                       "FROM history WHERE user_id=?", (req.user["id"],))
        return {"total": total, "items": items, "stats": stats}

    @app.route("DELETE", r"/api/history/(?P<hid>\d{1,12})")
    def history_delete(req):
        db.run("DELETE FROM history WHERE id=? AND user_id=?", (int(req.params["hid"]), req.user["id"]))
        return {"ok": True}

    # -------------------------------------------------------------- admin
    def admin_user_view(u):
        v = user_view(u)
        v.update(all_libraries=bool(u["all_libraries"]), disabled=bool(u["disabled"]),
                 created_at=u["created_at"],
                 libraries=[r["section_id"] for r in db.all(
                     "SELECT section_id FROM user_libraries WHERE user_id=?", (u["id"],))],
                 last_seen=db.scalar("SELECT MAX(last_seen) FROM sessions WHERE user_id=?", (u["id"],)))
        return v

    @app.route("GET", "/api/admin/users", auth="admin")
    def admin_users(req):
        return [admin_user_view(u) for u in db.all("SELECT * FROM users ORDER BY username COLLATE NOCASE")]

    def apply_user_fields(uid, b, creating=False):
        fields = {}
        if "display_name" in b:
            name = str(b["display_name"] or "").strip()
            if not 1 <= len(name) <= 50:
                raise HTTPError(400, "Nom affiché : 1 à 50 caractères")
            fields["display_name"] = name
        for k in ("is_admin", "all_libraries", "disabled"):
            if k in b:
                fields[k] = 1 if b[k] else 0
        if "avatar_color" in b:
            if not (isinstance(b["avatar_color"], str) and HEX.match(b["avatar_color"])):
                raise HTTPError(400, "Couleur invalide")
            fields["avatar_color"] = b["avatar_color"]
        if b.get("password"):
            validate_password(b["password"])
            fields["password_hash"] = hash_password(b["password"])
        if fields:
            sets = ",".join(f"{k}=?" for k in fields)
            db.run(f"UPDATE users SET {sets} WHERE id=?", [*fields.values(), uid])
        if "libraries" in b:
            libs = b["libraries"]
            if not isinstance(libs, list) or not all(isinstance(x, str) and x.isdigit() for x in libs):
                raise HTTPError(400, "Bibliothèques invalides")
            db.run("DELETE FROM user_libraries WHERE user_id=?", (uid,))
            for sid in set(libs):
                db.run("INSERT INTO user_libraries(user_id,section_id) VALUES(?,?)", (uid, sid))
        if not creating and (fields.get("disabled") or "password_hash" in fields):
            db.run("DELETE FROM sessions WHERE user_id=?", (uid,))

    @app.route("POST", "/api/admin/users", auth="admin")
    def admin_create_user(req):
        b = req.json()
        username = str(b.get("username") or "").strip()
        if not USERNAME.match(username):
            raise HTTPError(400, "Identifiant : 2 à 32 caractères (lettres, chiffres, . _ -)")
        validate_password(b.get("password"))
        if db.scalar("SELECT 1 FROM users WHERE username=?", (username,)):
            raise HTTPError(409, "Cet identifiant existe déjà")
        with db.tx():
            uid = db.run("INSERT INTO users(username,display_name,password_hash,created_at) VALUES(?,?,?,?)",
                         (username, username, "!", int(time.time()))).lastrowid
            b.setdefault("display_name", username)
            apply_user_fields(uid, b, creating=True)
        return admin_user_view(db.one("SELECT * FROM users WHERE id=?", (uid,)))

    def other_admins(uid):
        return db.scalar("SELECT COUNT(*) FROM users WHERE is_admin=1 AND disabled=0 AND id<>?", (uid,))

    @app.route("PUT", r"/api/admin/users/(?P<uid>\d{1,10})", auth="admin")
    def admin_update_user(req):
        uid = int(req.params["uid"])
        u = db.one("SELECT * FROM users WHERE id=?", (uid,))
        if not u:
            raise HTTPError(404, "Utilisateur introuvable")
        b = req.json()
        b.pop("username", None)
        losing_admin = ("is_admin" in b and not b["is_admin"]) or b.get("disabled")
        if losing_admin and u["is_admin"]:
            if uid == req.user["id"]:
                raise HTTPError(400, "Tu ne peux pas retirer tes propres droits ni te désactiver")
            if not other_admins(uid):
                raise HTTPError(400, "Il doit rester au moins un administrateur actif")
        with db.tx():
            apply_user_fields(uid, b)
        return admin_user_view(db.one("SELECT * FROM users WHERE id=?", (uid,)))

    @app.route("DELETE", r"/api/admin/users/(?P<uid>\d{1,10})", auth="admin")
    def admin_delete_user(req):
        uid = int(req.params["uid"])
        if uid == req.user["id"]:
            raise HTTPError(400, "Tu ne peux pas supprimer ton propre compte")
        u = db.one("SELECT is_admin FROM users WHERE id=?", (uid,))
        if not u:
            raise HTTPError(404, "Utilisateur introuvable")
        if u["is_admin"] and not other_admins(uid):
            raise HTTPError(400, "Il doit rester au moins un administrateur actif")
        for s in db.all("SELECT id FROM streams WHERE user_id=?", (uid,)):
            stop_stream(s["id"])
        db.run("DELETE FROM users WHERE id=?", (uid,))
        return {"ok": True}

    @app.route("GET", "/api/admin/libraries", auth="admin")
    def admin_libraries(req):
        return video_sections()

    @app.route("GET", "/api/admin/settings", auth="admin")
    def admin_settings(req):
        out = {"plex_url": db.setting("plex_url"), "token_set": bool(db.setting("plex_token")),
               "verify_tls": db.setting("plex_verify_tls", "1") == "1", "server": None, "error": None,
               "version": VERSION}
        try:
            ident = ctx.plex.identity()
            out["server"] = {"name": ident.get("friendlyName"), "version": ident.get("version")}
        except PlexError as e:
            out["error"] = str(e)
        return out

    @app.route("PUT", "/api/admin/settings", auth="admin")
    def admin_settings_put(req):
        b = req.json()
        verify = b.get("verify_tls", True) is not False
        token = resolve_token(b, allow_stored=True)
        url, info = test_plex(b.get("plex_url"), token, verify)
        db.set_setting("plex_url", url)
        db.set_setting("plex_token", token)
        db.set_setting("plex_verify_tls", "1" if verify else "0")
        ctx.pin_tokens.clear()
        return {"ok": True, "server": info["name"]}

    @app.route("GET", "/api/admin/streams", auth="admin")
    def admin_streams(req):
        return db.all("SELECT s.id, s.title, s.quality, s.position_ms, s.state, s.created_at, s.last_seen, "
                      "u.display_name AS user FROM streams s JOIN users u ON u.id=s.user_id "
                      "WHERE s.last_seen > ? ORDER BY s.created_at DESC", (int(time.time()) - 180,))

    @app.route("DELETE", r"/api/admin/streams/(?P<sid>[A-Za-z0-9_-]{8,40})", auth="admin")
    def admin_stop_stream(req):
        stop_stream(req.params["sid"])
        return {"ok": True}

    @app.route("PUT", f"/api/admin/markers/{RK}", auth="admin")
    def admin_markers(req):
        rk = req.params["rk"]
        b = req.json()
        kind = b.get("kind")
        if kind not in ("intro", "credits"):
            raise HTTPError(400, "Type de marqueur invalide")
        if b.get("clear"):
            db.run("DELETE FROM markers WHERE rating_key=? AND kind=?", (rk, kind))
            return {"ok": True}
        try:
            start, end = int(b["start"]), int(b["end"])
        except (KeyError, TypeError, ValueError):
            raise HTTPError(400, "Début et fin requis (ms)")
        if not 0 <= start < end:
            raise HTTPError(400, "La fin doit être après le début")
        db.run("INSERT INTO markers(rating_key,kind,start_ms,end_ms,source) VALUES(?,?,?,?, 'manual') "
               "ON CONFLICT(rating_key,kind) DO UPDATE SET start_ms=excluded.start_ms, end_ms=excluded.end_ms, "
               "source='manual'", (rk, kind, start, end))
        return {"ok": True}

    return app


def janitor(ctx, stop_event):
    """Stops abandoned transcodes and purges expired sessions / old cache."""
    last_cache = 0
    while not stop_event.wait(60):
        db = ctx.db
        try:
            now = int(time.time())
            for s in db.all("SELECT id, plex_session FROM streams WHERE last_seen < ?", (now - 300,)):
                db.run("DELETE FROM streams WHERE id=?", (s["id"],))
                ctx.stop_transcode(s["plex_session"])
            db.run("DELETE FROM sessions WHERE expires_at < ?", (now,))
            with ctx.lock:
                for k in [k for k, v in ctx.pin_tokens.items() if now - v[0] > 900]:
                    ctx.pin_tokens.pop(k, None)
            if now - last_cache > 86400:
                last_cache = now
                d = os.path.join(ctx.data_dir, "cache", "img")
                for name in os.listdir(d):
                    p = os.path.join(d, name)
                    try:
                        if now - os.path.getmtime(p) > 30 * 86400:
                            os.remove(p)
                    except OSError:
                        pass
        except Exception:
            log.exception("Erreur du nettoyage périodique")
        finally:
            db.release()
