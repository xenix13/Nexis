"""Séance — API HTTP (serveur multimédia autonome)."""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time

import types

from . import collections_api, discover, jellyfin, media, mountd
from .auth import (Throttle, check_credentials, clear_session, create_session, hash_password,
                   load_user, validate_password, verify_password)
from .db import DB
from .images import Images
from .markers import from_chapters as chapter_markers
from .metadata import Metadata, MetaError
from .names import LANG_WORDS, fold
from .ratings import age_of
from .scanner import EXTRA_LABELS, Scanner
from .subcache import SubtitleCache, text_tracks
from .subtitles import parse_subtitles
from .transcoder import QUALITY, Busy, StreamError, Transcoder, plan
from .watcher import Watcher
from .web import App, HTTPError, Response

log = logging.getLogger("seance")

VERSION = "2.6.0"
RK = r"(?P<rk>\d{1,12})"
SID = r"(?P<sid>[A-Za-z0-9_-]{8,40})"
SORTS = {"title": "i.sort_title COLLATE NOCASE", "added": "i.added_at DESC", "year": "i.year DESC, i.sort_title",
         "rating": "i.rating DESC, i.sort_title", "release": "i.released DESC, i.sort_title"}
WATCHED_RATIO = 0.90
MIN_RESUME_MS = 30_000
EXTERNAL_BASE = 1000  # identifiants des sous-titres externes
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
LANG = re.compile(r"^[a-z]{2,3}$")
USERNAME = re.compile(r"^[A-Za-z0-9._-]{2,32}$")
IMG_PATH = re.compile(r"^/library/metadata/(\d{1,12})/(thumb|art)/\d{1,14}$")
LANG_NAMES = {"fra": "Français", "eng": "Anglais", "jpn": "Japonais", "deu": "Allemand", "spa": "Espagnol",
              "ita": "Italien", "por": "Portugais", "kor": "Coréen", "zho": "Chinois", "rus": "Russe",
              "ara": "Arabe", "nld": "Néerlandais", "pol": "Polonais", "tur": "Turc", "swe": "Suédois",
              "dan": "Danois", "nor": "Norvégien", "fin": "Finnois", "hin": "Hindi", "tha": "Thaï"}
CODEC_NAMES = {"aac": "AAC", "ac3": "Dolby Digital", "eac3": "Dolby Digital+", "dts": "DTS", "truehd": "TrueHD",
               "flac": "FLAC", "opus": "Opus", "mp3": "MP3", "vorbis": "Vorbis", "subrip": "SRT", "ass": "ASS",
               "ssa": "SSA", "webvtt": "WebVTT", "mov_text": "Texte", "hdmv_pgs_subtitle": "PGS",
               "dvd_subtitle": "VobSub", "dvb_subtitle": "DVB"}

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


def validate_prefs(data):
    out = {}
    for k, v in data.items():
        if k not in DEFAULT_PREFS:
            continue
        ok = False
        if k == "quality":
            ok = v in QUALITY
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


ITEM_SQL = ("SELECT i.*, p.idx AS parent_idx, COALESCE(sh.age, i.age) AS eff_age, "
            "((SELECT COUNT(*) FROM items x WHERE x.show_id=i.id AND x.type='episode') + "
            "(SELECT COUNT(*) FROM items x WHERE x.parent_id=i.id AND x.type='episode')) AS leaves, "
            "(SELECT COUNT(*) FROM items y WHERE y.parent_id=i.id AND y.type='season') AS child_count "
            "FROM items i LEFT JOIN items p ON p.id=i.parent_id LEFT JOIN items sh ON sh.id=i.show_id ")
AGE_EXPR = "COALESCE(sh.age, i.age)"
EPISODE_ORDER = "ORDER BY (p.idx=0), p.idx, i.idx, i.id"


def img(item_id, kind, version):
    return f"/library/metadata/{item_id}/{kind}/{version}"


def stream_lists(probe):
    audio, subs = [], []
    for a in probe.get("audio", []):
        lang = a.get("lang") or ""
        name = LANG_NAMES.get(lang, lang.upper() if lang else "Inconnu")
        ch = a.get("channels") or 2
        layout = "Stéréo" if ch == 2 else "Mono" if ch == 1 else f"{ch - 1}.1"
        label = f"{name} ({CODEC_NAMES.get(a.get('codec'), str(a.get('codec')).upper())} {layout})"
        if a.get("title") and fold(a["title"]) not in fold(label):
            label += f" — {a['title']}"
        audio.append({"id": a["index"], "lang": lang, "title": label, "codec": a.get("codec"), "channels": ch,
                      "default": a.get("default", False), "selected": False})
    for s in probe.get("subs", []):
        codec = s.get("codec") or ""
        lang = s.get("lang") or ""
        label = LANG_NAMES.get(lang, lang.upper() if lang else "Inconnu")
        extra = [CODEC_NAMES.get(codec, codec.upper())]
        if s.get("forced"):
            extra.append("forcés")
        label += f" ({', '.join(extra)})"
        if s.get("title") and fold(s["title"]) not in fold(label):
            label += f" — {s['title']}"
        overlay = codec in media.TEXT_SUB_CODECS
        burn = codec in media.IMAGE_SUB_CODECS
        if not overlay and not burn:
            continue
        subs.append({"id": s["index"], "lang": lang, "title": label, "codec": codec, "forced": s.get("forced", False),
                     "external": False, "overlay": overlay, "ass": codec in ("ass", "ssa"), "selected": False})
    for n, e in enumerate(probe.get("external", [])):
        lang = e.get("lang") or ""
        label = LANG_NAMES.get(lang, lang.upper() if lang else "Inconnu")
        bits = [CODEC_NAMES.get(e["codec"], e["codec"].upper()), "externe"]
        if e.get("forced"):
            bits.append("forcés")
        if e.get("sdh"):
            bits.append("SDH")
        subs.append({"id": EXTERNAL_BASE + n, "lang": lang, "title": f"{label} ({', '.join(bits)})",
                     "codec": e["codec"], "forced": e.get("forced", False), "external": True, "overlay": True,
                     "ass": e["codec"] in ("ass", "ssa"), "selected": False})
    return audio, subs


class Ctx:
    def __init__(self, data_dir, static_dir, cookie_secure=False, trust_proxy=False):
        self.data_dir = data_dir
        self.static_dir = static_dir
        self.cookie_secure = cookie_secure
        self.trust_proxy = trust_proxy
        for d in ("cache/img", "cache/orig", "cache/subs"):
            os.makedirs(os.path.join(data_dir, d), exist_ok=True)
        self.db = DB(os.path.join(data_dir, "seance.db"))
        self.throttle = Throttle()
        self.lock = threading.Lock()
        self.metadata = Metadata(self)
        self.images = Images(self)
        self.scanner = Scanner(self)
        self.transcoder = Transcoder(os.path.join(data_dir, "transcodes"), self.limits)
        self.subtitles = SubtitleCache(self)
        self.watcher = Watcher(self)
        for r in self.db.all("SELECT id, content_rating FROM items WHERE content_rating IS NOT NULL AND age IS NULL"):
            age = age_of(r["content_rating"])
            if age is not None:
                self.db.run("UPDATE items SET age=? WHERE id=?", (age, r["id"]))
        if not self.db.setting("webhook_token"):
            self.db.set_setting("webhook_token", secrets.token_urlsafe(24))
        self.sub_locks = {}
        self.setup_code = None
        if not self.db.scalar("SELECT COUNT(*) FROM users"):
            self.setup_code = secrets.token_hex(4).upper()
            path = os.path.join(data_dir, "setup-code.txt")
            with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
                f.write(self.setup_code + "\n")
            log.warning("Premier lancement : code d'installation = %s (aussi dans %s)", self.setup_code, path)
        if not media.available():
            log.error("ffmpeg/ffprobe introuvables : installe-les (apt install ffmpeg)")
        self.db.release()

    def setting_int(self, key, default):
        try:
            return int(self.db.setting(key, default))
        except (TypeError, ValueError):
            return default

    def limits(self):
        cpus = os.cpu_count() or 2
        return (self.setting_int("max_streams", 6),
                self.setting_int("max_transcodes", 1 if cpus <= 2 else 2))

    def on_library_changed(self):
        pass

    def prefetch_missing_subtitles(self):
        """Au démarrage : met en file les fichiers dont les sous-titres internes ne sont pas encore prêts."""
        if self.db.setting("subs_prefetch", "1") != "1" and self.db.setting("trickplay", "1") != "1":
            return
        n = 0
        for r in self.db.all("SELECT id, probe, file_mtime, duration_ms FROM items WHERE type IN ('movie','episode') "
                             "ORDER BY added_at DESC"):
            if self.subtitles.has_work(r):
                self.subtitles.enqueue(r["id"])
                n += 1
        if n:
            log.info("%d fichier(s) en file pour la préparation des sous-titres", n)
        self.db.release()

    def on_new_media(self, item_id, info):
        """Nouveau fichier (ou fichier remplacé) : on prépare ses sous-titres internes en tâche de fond."""
        if (self.db.setting("subs_prefetch", "1") == "1" and text_tracks(info)) or \
                (self.db.setting("trickplay", "1") == "1" and info.get("video")):
            self.subtitles.enqueue(item_id)


def build_app(ctx):
    x = types.SimpleNamespace()  # outils partagés avec les modules de routes (collections_api…)
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
        if opts.get("auth") == "webhook":
            token = req.arg("token") or ""
            if not hmac.compare_digest(token, db.setting("webhook_token") or "\0"):
                raise HTTPError(403, "Jeton invalide")
            return
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

    # -------------------------------------------------------------- catalog
    def libraries_all():
        return [{"id": str(r["id"]), "title": r["name"], "type": r["kind"]}
                for r in db.all("SELECT id, name, kind FROM libraries ORDER BY position, name")]

    def allowed_ids(user):
        ids = [l["id"] for l in libraries_all()]
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

    def item_row(rk):
        row = db.one(ITEM_SQL + "WHERE i.id=?", (int(rk),))
        if not row:
            raise HTTPError(404, "Introuvable")
        return row

    def age_filter(user):
        """Clause SQL du contrôle parental (profil limité à un âge)."""
        if user.get("max_age") is None:
            return "", []
        return (f" AND ({AGE_EXPR} <= ? OR ({AGE_EXPR} IS NULL AND ?=1))",
                [user["max_age"], 1 if user.get("allow_unrated") else 0])

    def visible(user, row):
        if user.get("max_age") is None:
            return True
        age = row.get("eff_age") if "eff_age" in row else row.get("age")
        if age is None:
            return bool(user.get("allow_unrated"))
        return age <= user["max_age"]

    def require_item(user, rk):
        row = item_row(rk)
        require_section(user, row["library_id"])
        if not visible(user, row):
            raise HTTPError(404, "Introuvable")
        return row

    show_cache = {}

    def show_of(show_id):
        if not show_id:
            return None
        key = (show_id, threading.get_ident())
        r = show_cache.get(key)
        if r is None or time.time() - r[0] > 2:
            r = (time.time(), db.one("SELECT id, title, img_version, library_id FROM items WHERE id=?", (show_id,)))
            show_cache[key] = r
            if len(show_cache) > 2000:
                show_cache.clear()
        return r[1]

    def card(r):
        t = r["type"]
        v = r["img_version"]
        c = {"id": str(r["id"]), "type": t, "title": r["title"], "year": r["year"],
             "thumb": img(r["id"], "thumb", v), "art": img(r["id"], "art", v), "duration": r["duration_ms"],
             "section": str(r["library_id"]), "rating": r["rating"], "content_rating": r["content_rating"],
             "added": r["added_at"]}
        if t == "episode":
            s = show_of(r["show_id"]) or {"id": r["show_id"], "title": "?", "img_version": 1}
            c.update(show=s["title"], show_id=str(r["show_id"]), season_id=str(r["parent_id"]),
                     season=r.get("parent_idx"), episode=r["idx"], show_thumb=img(s["id"], "thumb", s["img_version"]),
                     show_art=img(s["id"], "art", s["img_version"]), aired=r["released"])
        elif t == "season":
            s = show_of(r["show_id"]) or {"id": r["show_id"], "title": "?", "img_version": 1}
            c.update(show=s["title"], show_id=str(r["show_id"]), index=r["idx"], leaves=r.get("leaves"),
                     show_thumb=img(s["id"], "thumb", s["img_version"]))
        elif t == "show":
            c.update(leaves=r.get("leaves"), seasons=r.get("child_count"))
        elif t == "extra":
            c.update(extra_kind=r.get("extra_kind"), parent_id=str(r["parent_id"]),
                     extra_label=EXTRA_LABELS.get(r.get("extra_kind") or "other", "Bonus"))
        return c

    def leaves(show_id):
        return db.all(ITEM_SQL + "WHERE i.show_id=? AND i.type='episode' " + EPISODE_ORDER, (int(show_id),))

    def next_episode(row):
        if row["type"] != "episode":
            return None
        eps = leaves(row["show_id"])
        ids = [e["id"] for e in eps]
        try:
            i = ids.index(row["id"])
        except ValueError:
            return None
        return eps[i + 1] if i + 1 < len(eps) else None

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
                    state["count:" + r["k"]] = state.get("count:" + r["k"], 0) + r["n"]
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
        if watched and c.get("type") == "movie":
            db.run("DELETE FROM watchlist WHERE user_id=? AND item_id=?", (uid, int(c["id"])))
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

    def markers_for(row):
        """Marqueurs posés à la main (prioritaires), sinon déduits des chapitres."""
        probe = json.loads(row["probe"] or "{}")
        out = chapter_markers(probe.get("chapters") or [], probe.get("duration") or (row["duration_ms"] or 0) / 1000)
        for r in db.all("SELECT kind,start_ms,end_ms,source FROM markers WHERE rating_key=?", (str(row["id"]),)):
            out[r["kind"]] = {"start": r["start_ms"], "end": r["end_ms"], "source": r["source"]}
        return out

    def track_scope(row):
        return str(row["show_id"]) if row["type"] in ("episode", "season") else str(row["id"])

    def track_prefs(uid, row):
        r = db.one("SELECT audio_lang, sub_lang, sub_forced FROM track_prefs WHERE user_id=? AND scope_id=?",
                   (uid, track_scope(row)))
        if r:
            r["sub_forced"] = bool(r["sub_forced"])
        return r

    def chapters_for(row):
        probe = json.loads(row["probe"] or "{}")
        return [{"start": int(c["start"] * 1000), "end": int(c["end"] * 1000), "title": c.get("title") or ""}
                for c in probe.get("chapters") or []]

    # ---------------------------------------------------------- public / auth
    @app.route("GET", "/api/status", auth=None)
    def status(req):
        return {"setup": db.scalar("SELECT COUNT(*) FROM users") == 0,
                "libraries": db.scalar("SELECT COUNT(*) FROM libraries"),
                "ffmpeg": media.available(), "version": VERSION}

    @app.route("POST", "/api/setup", auth="setup")
    def setup(req):
        if db.scalar("SELECT COUNT(*) FROM users"):
            raise HTTPError(409, "Déjà configuré")
        b = req.json()
        username = (b.get("username") or "").strip()
        if not USERNAME.match(username):
            raise HTTPError(400, "Identifiant : 2 à 32 caractères (lettres, chiffres, . _ -)")
        validate_password(b.get("password"))
        with db.tx():
            if db.scalar("SELECT COUNT(*) FROM users"):
                raise HTTPError(409, "Déjà configuré")
            uid = db.run("INSERT INTO users(username,display_name,password_hash,is_admin,created_at) "
                         "VALUES(?,?,?,1,?)", (username, (b.get("display_name") or username)[:50],
                                               hash_password(b["password"]), int(time.time()))).lastrowid
        ctx.setup_code = None
        try:
            os.remove(os.path.join(ctx.data_dir, "setup-code.txt"))
        except OSError:
            pass
        create_session(db, req, uid, ctx.cookie_secure)
        return {"ok": True}

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
                "is_admin": bool(u["is_admin"]), "avatar_color": u["avatar_color"],
                "managed": bool(u.get("managed")), "has_pin": bool(u.get("pin_hash")),
                "max_age": u.get("max_age"), "home": bool(u.get("home", 1))}

    @app.route("GET", "/api/me")
    def me(req):
        allowed = allowed_set(req.user)
        warning = None if media.available() else "ffmpeg n'est pas installé sur le serveur : la lecture est impossible."
        return {"user": user_view(req.user), "prefs": get_prefs(req.user["id"]),
                "libraries": [l for l in libraries_all() if l["id"] in allowed], "plex_error": warning,
                "features": {"discover": bool(db.setting("tmdb_key")),
                             "requests": bool(db.setting("seerr_url") and db.setting("seerr_key"))}}

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

    # ------------------------------------------------------------ profils
    def hash_pin(pin):
        if pin in (None, ""):
            return None
        if not (isinstance(pin, str) and re.match(r"^\d{4,8}$", pin)):
            raise HTTPError(400, "Le code PIN doit faire 4 à 8 chiffres")
        return hash_password(pin)

    @app.route("GET", "/api/profiles")
    def profiles(req):
        if not req.user.get("home"):
            return []
        rows = db.all("SELECT * FROM users WHERE home=1 AND disabled=0 ORDER BY managed, display_name COLLATE NOCASE")
        out = []
        for u in rows:
            v = user_view(u)
            v["current"] = u["id"] == req.user["id"]
            # sans PIN, un compte complet (avec mot de passe) ou administrateur demande son mot de passe
            v["needs"] = "pin" if u["pin_hash"] else ("none" if u["managed"] and not u["is_admin"] else "password")
            out.append(v)
        return out

    @app.route("POST", r"/api/profiles/(?P<uid>\d{1,10})/switch")
    def profile_switch(req):
        uid = int(req.params["uid"])
        if not req.user.get("home"):
            raise HTTPError(403, "Ce compte ne fait pas partie du foyer")
        target = db.one("SELECT * FROM users WHERE id=? AND home=1 AND disabled=0", (uid,))
        if not target:
            raise HTTPError(404, "Profil introuvable")
        if uid != req.user["id"]:
            b = req.json()
            key = f"profile:{uid}"
            ctx.throttle.check(key, "ip:" + req.client_ip)
            if target["pin_hash"]:
                ok = verify_password(str(b.get("pin") or "")[:8], target["pin_hash"])
            elif target["managed"] and not target["is_admin"]:
                ok = True
            else:
                ok = verify_password(str(b.get("password") or "")[:256], target["password_hash"])
            if not ok:
                ctx.throttle.fail(key, "ip:" + req.client_ip)
                raise HTTPError(400, "Code PIN incorrect" if target["pin_hash"] else "Mot de passe incorrect")
            ctx.throttle.reset(key)
            clear_session(db, req, ctx.cookie_secure)
            req.set_cookies.clear()
            create_session(db, req, uid, ctx.cookie_secure)
        return {"ok": True, "user": user_view(target)}

    @app.route("PUT", "/api/me/pin")
    def me_pin(req):
        b = req.json()
        if not req.user["managed"]:
            row = db.one("SELECT password_hash FROM users WHERE id=?", (req.user["id"],))
            if not verify_password(str(b.get("password") or ""), row["password_hash"]):
                raise HTTPError(400, "Mot de passe actuel incorrect")
        db.run("UPDATE users SET pin_hash=? WHERE id=?", (hash_pin(b.get("pin")), req.user["id"]))
        return {"ok": True, "has_pin": bool(b.get("pin"))}

    # -------------------------------------------------------------- library
    @app.route("GET", "/api/libraries")
    def libraries(req):
        allowed = allowed_set(req.user)
        return [l for l in libraries_all() if l["id"] in allowed]

    @app.route("GET", r"/api/libraries/(?P<sid>\d{1,6})/items")
    def library_items(req):
        sid = req.params["sid"]
        require_section(req.user, sid)
        lib = db.one("SELECT kind FROM libraries WHERE id=?", (int(sid),))
        kind = "movie" if lib["kind"] == "movie" else "show"
        where = "WHERE i.library_id=? AND i.type=?"
        args = [int(sid), kind]
        genre = req.arg("genre")
        if genre:
            if len(genre) > 80:
                raise HTTPError(400, "Genre invalide")
            where += " AND EXISTS (SELECT 1 FROM json_each(i.genres) g WHERE g.value=?)"
            args.append(genre)
        start = req.int_arg("start", 0, 0)
        size = req.int_arg("size", 60, 1, 200)
        order = SORTS.get(req.arg("sort", "title"), SORTS["title"])
        af, aa = age_filter(req.user)
        where += af
        args += aa
        total = db.scalar(f"SELECT COUNT(*) FROM items i LEFT JOIN items sh ON sh.id=i.show_id {where}", args)
        rows = db.all(ITEM_SQL + where + f" ORDER BY {order} LIMIT ? OFFSET ?", [*args, size, start])
        items = attach_state(req.user["id"], [card(r) for r in rows])
        return {"total": total, "items": items}

    @app.route("GET", r"/api/libraries/(?P<sid>\d{1,6})/genres")
    def library_genres(req):
        sid = req.params["sid"]
        require_section(req.user, sid)
        rows = db.all("SELECT g.value AS name, COUNT(*) AS n FROM items i, json_each(i.genres) g "
                      "WHERE i.library_id=? AND i.type IN ('movie','show') GROUP BY g.value ORDER BY g.value",
                      (int(sid),))
        return [{"id": r["name"], "title": r["name"], "count": r["n"]} for r in rows]

    def recent_row(lib, user):
        if lib["type"] == "movie":
            af, aa = age_filter(user)
            rows = db.all(ITEM_SQL + "WHERE i.library_id=? AND i.type='movie'" + af +
                          " ORDER BY i.added_at DESC, i.id DESC LIMIT 24", (int(lib["id"]), *aa))
            return [card(r) for r in rows]
        latest = db.all("SELECT show_id, MAX(added_at) AS t FROM items WHERE library_id=? AND type='episode' "
                        "GROUP BY show_id ORDER BY t DESC LIMIT 24", (int(lib["id"]),))
        out = []
        for l in latest:
            show = db.one(ITEM_SQL + "WHERE i.id=?", (l["show_id"],))
            if not show or not visible(user, show):
                continue
            c = card(show)
            ep = db.one(ITEM_SQL + "WHERE i.show_id=? AND i.type='episode' ORDER BY i.added_at DESC, p.idx DESC, "
                        "i.idx DESC LIMIT 1", (l["show_id"],))
            if ep and ep["added_at"] > show["added_at"] + 3600:
                c["latest"] = {"season": ep["parent_idx"], "episode": ep["idx"]}
            out.append(c)
        return out

    def continue_row(user, allowed):
        uid = user["id"]
        rows = db.all("SELECT rating_key,position_ms,duration_ms,updated_at,meta,section_id,show_key FROM progress "
                      "WHERE user_id=? AND position_ms>=? AND (duration_ms=0 OR position_ms < duration_ms*?) "
                      "ORDER BY updated_at DESC LIMIT 30", (uid, MIN_RESUME_MS, WATCHED_RATIO))
        items, shows_in_progress = [], set()
        for r in rows:
            if r["section_id"] not in allowed:
                continue
            row = db.one(ITEM_SQL + "WHERE i.id=?", (int(r["rating_key"]),))
            if not row or not visible(user, row):
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
        for r in recent_shows:
            if r["show_key"] in shows_in_progress or not r["show_key"].isdigit():
                continue
            watched = {w["rating_key"]: w["last_watched_at"] or 0 for w in db.all(
                "SELECT rating_key,last_watched_at FROM progress WHERE user_id=? AND show_key=? AND watched=1",
                (uid, r["show_key"]))}
            eps = leaves(r["show_key"])
            last_i, best = None, -1
            for i, ep in enumerate(eps):
                wt = watched.get(str(ep["id"]))
                if wt is not None and wt >= best:
                    best, last_i = wt, i
            if last_i is None:
                continue
            for ep in eps[last_i + 1:]:
                if str(ep["id"]) not in watched:
                    c = card(ep)
                    if c["section"] in allowed and visible(user, ep):
                        c.update(next_up=True, position=0, watched=False, _t=r["t"])
                        items.append(c)
                    break
        items.sort(key=lambda c: c.pop("_t", 0), reverse=True)
        return items[:24]

    @app.route("GET", "/api/home")
    def home(req):
        user = req.user
        prefs = get_prefs(user["id"])
        allowed = allowed_set(user)
        rows = []
        for lib in libraries_all():
            if lib["id"] not in allowed or lib["id"] in prefs["hidden_libraries"]:
                continue
            items = attach_state(user["id"], recent_row(lib, user))
            if items:
                rows.append({"library": lib, "items": items})
        cont = continue_row(user, allowed) if prefs["show_continue"] else []
        hero = rows[0]["items"][0] if rows and rows[0]["items"] else None
        return {"continue": cont, "rows": rows, "hero": hero, "watchlist": x.watchlist_items(user, 24)}

    @app.route("GET", "/api/search")
    def search(req):
        q = fold((req.arg("q") or "").strip())[:100]
        out = {"movies": [], "shows": [], "episodes": []}
        if len(q) < 2:
            return out
        allowed = sorted(int(i) for i in allowed_set(req.user))
        if not allowed:
            return out
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        marks = ",".join("?" * len(allowed))
        for typ, bucket, limit in (("movie", "movies", 30), ("show", "shows", 30), ("episode", "episodes", 20)):
            af, aa = age_filter(req.user)
            rows = db.all(ITEM_SQL + f"WHERE i.type=? AND i.library_id IN ({marks}) AND i.search LIKE ? ESCAPE '\\'"
                          + af + f" ORDER BY (i.search LIKE ? ESCAPE '\\') DESC, i.sort_title LIMIT ?",
                          [typ, *allowed, like, *aa, q + "%", limit])
            out[bucket] = attach_state(req.user["id"], [card(r) for r in rows])
        return out

    @app.route("GET", f"/api/items/{RK}")
    def item(req):
        r = require_item(req.user, req.params["rk"])
        uid = req.user["id"]
        d = card(r)
        people = json.loads(r["people"] or "{}")
        d.update(summary=r["summary"], tagline=r["tagline"], studio=r["studio"], released=r["released"],
                 genres=json.loads(r["genres"] or "[]"), directors=people.get("directors", []),
                 cast=people.get("cast", [])[:20], original_title=r["original_title"], tmdb_id=r["tmdb_id"],
                 meta_state=r["meta_state"])
        if d["type"] == "episode" and not d["summary"]:
            show = db.one("SELECT genres FROM items WHERE id=?", (r["show_id"],))
            d["genres"] = json.loads(show["genres"] or "[]") if show else []
        attach_state(uid, [d])
        d["in_watchlist"] = x.in_watchlist(req.user, r)
        d["trailer"] = r["trailer"]
        if d["type"] in ("movie", "show"):
            extras = db.all(ITEM_SQL + "WHERE i.parent_id=? AND i.type='extra' ORDER BY i.extra_kind!='trailer', i.sort_title",
                            (r["id"],))
            d["extras"] = [card(e) for e in extras]
            d["collections"] = x.collections_of(req.user, r["id"])
        if d["type"] in ("movie", "episode", "extra"):
            probe = json.loads(r["probe"] or "{}")
            if ctx.subtitles.has_work(r) and d["type"] != "extra":
                ctx.subtitles.enqueue(r["id"], urgent=True)  # on prépare au cas où la lecture suit
            audio, subs = stream_lists(probe)
            v = probe.get("video") or {}
            first_audio = (probe.get("audio") or [{}])[0]
            d["track_prefs"] = track_prefs(uid, r)
            d.update(audio=audio, subtitles=subs, markers=markers_for(r), chapters=chapters_for(r), media={
                "resolution": f"{v.get('height')}" if v.get("height") else None, "video_codec": v.get("codec"),
                "audio_codec": first_audio.get("codec"), "container": (probe.get("container") or "").split(",")[0],
                "bitrate": int((probe.get("bitrate") or 0) / 1000)})
            if d["type"] == "episode":
                nxt = next_episode(r)
                d["next"] = attach_state(uid, [card(nxt)])[0] if nxt else None
                if req.user["is_admin"]:
                    d["path"] = r["key"]
            elif req.user["is_admin"]:
                d["path"] = r["key"]
        elif d["type"] == "show":
            seasons = db.all(ITEM_SQL + "WHERE i.parent_id=? AND i.type='season' ORDER BY (i.idx=0), i.idx", (r["id"],))
            d["seasons"] = attach_state(uid, [card(s) for s in seasons])
            eps = attach_state(uid, [card(e) for e in leaves(r["id"])])
            target = next((e for e in eps if e["position"] > 0 and not e["watched"]), None)
            if not target:
                last_watched = max((i for i, e in enumerate(eps) if e["watched"]), default=-1)
                target = next((e for e in eps[last_watched + 1:] if not e["watched"]), None)
            d["play_target"] = target or (eps[0] if eps else None)
            d["track_prefs"] = track_prefs(uid, r)
            if d["play_target"]:
                trow = db.one("SELECT probe FROM items WHERE id=?", (int(d["play_target"]["id"]),))
                d["audio"], d["subtitles"] = stream_lists(json.loads(trow["probe"] or "{}"))
        elif d["type"] == "season":
            d["show_id"] = str(r["show_id"])
        return d

    @app.route("GET", f"/api/items/{RK}/children")
    def item_children(req):
        r = require_item(req.user, req.params["rk"])
        if r["type"] == "show":
            rows = db.all(ITEM_SQL + "WHERE i.parent_id=? AND i.type='season' ORDER BY (i.idx=0), i.idx", (r["id"],))
        else:
            rows = db.all(ITEM_SQL + "WHERE i.parent_id=? " + EPISODE_ORDER, (r["id"],))
        out = []
        for row in rows:
            c = card(row)
            c["summary"] = row["summary"]
            out.append(c)
        return attach_state(req.user["id"], out)

    @app.route("PUT", f"/api/items/{RK}/tracks")
    def item_tracks(req):
        """Langues audio / sous-titres retenues pour ce film ou cette série (par utilisateur)."""
        r = require_item(req.user, req.params["rk"])
        b = req.json()
        if b.get("reset"):
            db.run("DELETE FROM track_prefs WHERE user_id=? AND scope_id=?", (req.user["id"], track_scope(r)))
            return {"ok": True}
        audio = b.get("audio_lang")
        sub = b.get("sub_lang")
        for v in (audio, sub):
            if v is not None and not (isinstance(v, str) and (v in ("", "off") or LANG.match(v))):
                raise HTTPError(400, "Langue invalide")
        db.run("INSERT INTO track_prefs(user_id,scope_id,audio_lang,sub_lang,sub_forced,updated_at) VALUES(?,?,?,?,?,?) "
               "ON CONFLICT(user_id,scope_id) DO UPDATE SET audio_lang=COALESCE(excluded.audio_lang, track_prefs.audio_lang), "
               "sub_lang=COALESCE(excluded.sub_lang, track_prefs.sub_lang), sub_forced=excluded.sub_forced, "
               "updated_at=excluded.updated_at",
               (req.user["id"], track_scope(r), audio, sub, 1 if b.get("sub_forced") else 0, int(time.time())))
        return {"ok": True, "track_prefs": track_prefs(req.user["id"], r)}

    @app.route("POST", f"/api/items/{RK}/watched")
    def item_watched(req):
        r = require_item(req.user, req.params["rk"])
        watched = bool(req.json().get("watched"))
        if r["type"] == "show":
            targets = leaves(r["id"])
        elif r["type"] == "season":
            targets = db.all(ITEM_SQL + "WHERE i.parent_id=? AND i.type='episode' " + EPISODE_ORDER, (r["id"],))
        else:
            targets = [r]
        with db.tx():
            for t in targets:
                set_watched(req.user["id"], card(t), watched)
        return {"ok": True, "count": len(targets)}

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
        return {"ready": False, "progress": round(job.progress, 3)}

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
            return r, external_ass(r, sub)
        data = ctx.subtitles.read_ass(r, sub)
        if data is None:
            raise HTTPError(404, "Script ASS indisponible")
        return r, data

    @app.route("GET", f"/api/items/{RK}/subtitles/(?P<sub>\\d{{1,6}})/ass")
    def item_subtitles_ass(req):
        r, data = raw_ass(req)
        return Response(data, 200, "text/plain; charset=utf-8", {"Cache-Control": "private, max-age=3600"})

    @app.route("GET", f"/api/items/{RK}/subtitles/(?P<sub>\\d{{1,6}})/fonts")
    def item_subtitles_fonts(req):
        r, data = raw_ass(req)
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

    # ------------------------------------------------------------- images
    @app.route("GET", "/api/img")
    def image(req):
        cm = collections_api.COLL_IMG.match(req.arg("p") or "")
        if cm:
            return x.collection_image(req, cm)
        m = IMG_PATH.match(req.arg("p") or "")
        if not m:
            raise HTTPError(400, "Image invalide")
        r = require_item(req.user, m.group(1))
        w = max(40, min(1920, round(req.int_arg("w", 300) / 20) * 20))
        h = max(40, min(1920, round(req.int_arg("h", 450) / 20) * 20))
        path = ctx.images.get(r, m.group(2), w, h)
        if not path:
            raise HTTPError(404, "Pas d'image")
        with open(path, "rb") as f:
            return Response(f.read(), 200, "image/jpeg", {"Cache-Control": "private, max-age=604800, immutable"})

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
        burn = sub["id"] if sub and not sub["overlay"] else None
        if b.get("replace"):
            stop_stream(str(b["replace"]), uid)
        p = plan(probe, audio_id, burn, quality)
        if p["mode"] == "direct" and b.get("no_direct"):
            p["mode"] = "copy"  # le navigateur a refusé la lecture directe
        sid = secrets.token_urlsafe(12)
        try:
            actual = ctx.transcoder.start(sid, r["key"], p, offset)
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
        url = f"/api/stream/{sid}/file" if p["mode"] == "direct" else f"/api/stream/{sid}/index.m3u8"
        return {
            "stream": sid, "mode": p["mode"], "reasons": p["reasons"], "url": url, "offset": actual,
            "duration": duration, "quality": quality, "item": c, "audio": audio, "subtitles": subs,
            "audio_selected": p["audio_index"], "subtitle_selected": sub["id"] if sub else 0,
            "subtitle_overlay": f"/api/items/{rk}/subtitles/{sub['id']}" if sub and sub["overlay"] else None,
            "subtitle_ass": f"/api/items/{rk}/subtitles/{sub['id']}/ass" if sub and sub.get("ass") else None,
            "markers": markers_for(r), "chapters": chapters_for(r), "next": card(nxt) if nxt else None,
        }

    def own_stream(req, sid):
        st = db.one("SELECT * FROM streams WHERE id=? AND user_id=?", (sid, req.user["id"]))
        if not st:
            raise HTTPError(404, "Flux expiré")
        now = int(time.time())
        if now - st["last_seen"] >= 15:
            db.run("UPDATE streams SET last_seen=? WHERE id=?", (now, sid))
        return st

    @app.route("GET", f"/api/stream/{SID}/index.m3u8")
    def stream_playlist(req):
        sid = req.params["sid"]
        own_stream(req, sid)
        db.release()  # l'attente peut durer : on ne garde pas la connexion SQLite
        try:
            text = ctx.transcoder.playlist(sid)
        except StreamError as e:
            raise HTTPError(502, str(e))
        return Response(text, 200, "application/vnd.apple.mpegurl", {"Cache-Control": "no-store"})

    @app.route("GET", f"/api/stream/{SID}/(?P<seg>s\\d{{5}}\\.ts)")
    def stream_segment(req):
        sid = req.params["sid"]
        own_stream(req, sid)
        db.release()
        try:
            path = ctx.transcoder.segment(sid, req.params["seg"])
        except StreamError as e:
            raise HTTPError(404, str(e))
        with open(path, "rb") as f:
            data = f.read()
        return Response(data, 200, "video/mp2t", {"Cache-Control": "no-store"})

    @app.route("GET", f"/api/stream/{SID}/file")
    def stream_file(req):
        st = own_stream(req, req.params["sid"])
        r = db.one("SELECT key FROM items WHERE id=?", (int(st["rating_key"]),))
        if not r or not os.path.isfile(r["key"]):
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
        ctype = "video/mp4" if path.lower().endswith((".mp4", ".m4v", ".mov")) else "video/webm"
        return Response(stream=gen(), status=status, content_type=ctype, length=length, headers=headers,
                        on_close=f.close)

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
        r = require_item(req.user, rk)
        if r["type"] == "extra":  # les bonus ne laissent ni reprise ni historique
            sid = str(b.get("stream") or "")
            if sid and state == "stopped":
                stop_stream(sid, uid)
            elif sid:
                ctx.transcoder.touch(sid)
                db.run("UPDATE streams SET last_seen=? WHERE id=? AND user_id=?", (int(time.time()), sid, uid))
            return {"ok": True, "watched": False}
        row = db.one("SELECT * FROM progress WHERE user_id=? AND rating_key=?", (uid, rk))
        if not row:
            upsert_meta(uid, card(r))
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
        if sid and db.scalar("SELECT 1 FROM streams WHERE id=? AND user_id=?", (sid, uid)):
            if state == "stopped":
                stop_stream(sid, uid)
            else:
                db.run("UPDATE streams SET position_ms=?, state=?, last_seen=? WHERE id=?", (pos, state, now, sid))
                ctx.transcoder.touch(sid)
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
            row = db.one(ITEM_SQL + "WHERE i.id=?", (int(r["rating_key"]),))
            c.update(history_id=r["id"], watched_at=r["watched_at"],
                     available=bool(row) and r["section_id"] in allowed and visible(req.user, row))
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

    # -------------------------------------------------------- admin users
    def admin_user_view(u):
        v = user_view(u)
        v.update(all_libraries=bool(u["all_libraries"]), disabled=bool(u["disabled"]), created_at=u["created_at"],
                 allow_unrated=bool(u["allow_unrated"]),
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
        for k in ("managed", "home", "allow_unrated"):
            if k in b:
                fields[k] = 1 if b[k] else 0
        if "max_age" in b:
            if b["max_age"] not in (None, 0, 7, 10, 12, 13, 14, 16, 17, 18):
                raise HTTPError(400, "Restriction d'âge invalide")
            fields["max_age"] = b["max_age"]
        if "pin" in b:
            fields["pin_hash"] = hash_pin(b["pin"])
        cur = db.one("SELECT is_admin, managed, password_hash FROM users WHERE id=?", (uid,))
        managed = fields.get("managed", cur["managed"])
        if managed and fields.get("is_admin", cur["is_admin"]):
            raise HTTPError(400, "Un profil géré (sans mot de passe) ne peut pas être administrateur")
        if managed and fields.get("managed") and "password_hash" not in fields:
            fields["password_hash"] = "!"  # plus de connexion par mot de passe : uniquement via « Changer de profil »
        if not managed and cur["password_hash"] == "!" and "password_hash" not in fields:
            raise HTTPError(400, "Définis un mot de passe pour ce compte")
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
        if not b.get("managed"):
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

    # ----------------------------------------------------- admin libraries
    def library_view(l):
        counts = {r["type"]: r["n"] for r in db.all(
            "SELECT type, COUNT(*) AS n FROM items WHERE library_id=? GROUP BY type", (l["id"],))}
        unmatched = db.scalar("SELECT COUNT(*) FROM items WHERE library_id=? AND type IN ('movie','show') "
                              "AND meta_state IN ('nomatch','error')", (l["id"],))
        return {"id": str(l["id"]), "name": l["name"], "kind": l["kind"], "paths": json.loads(l["paths"]),
                "scanned_at": l["scanned_at"], "counts": counts, "unmatched": unmatched,
                "paths_ok": [os.path.isdir(p) for p in json.loads(l["paths"])]}

    def validate_library(b):
        name = str(b.get("name") or "").strip()
        if not 1 <= len(name) <= 40:
            raise HTTPError(400, "Nom : 1 à 40 caractères")
        if b.get("kind") not in ("movie", "show"):
            raise HTTPError(400, "Type invalide")
        paths = b.get("paths")
        if not isinstance(paths, list) or not paths or len(paths) > 20:
            raise HTTPError(400, "Indique au moins un dossier")
        clean = []
        for p in paths:
            p = os.path.normpath(str(p).strip())
            if not os.path.isabs(p):
                raise HTTPError(400, f"Chemin absolu attendu : {p}")
            if not os.path.isdir(p):
                raise HTTPError(400, f"Dossier introuvable depuis le serveur : {p}")
            if p not in clean:
                clean.append(p)
        return name, b["kind"], clean

    @app.route("GET", "/api/admin/libraries", auth="admin")
    def admin_libraries(req):
        return {"libraries": [library_view(l) for l in db.all("SELECT * FROM libraries ORDER BY position, name")],
                "scan": ctx.scanner.status()}

    @app.route("POST", "/api/admin/libraries", auth="admin")
    def admin_library_create(req):
        name, kind, paths = validate_library(req.json())
        pos = (db.scalar("SELECT MAX(position) FROM libraries") or 0) + 1
        lid = db.run("INSERT INTO libraries(name,kind,paths,position,created_at) VALUES(?,?,?,?,?)",
                     (name, kind, json.dumps(paths), pos, int(time.time()))).lastrowid
        db.release()
        ctx.scanner.start([lid])
        return library_view(db.one("SELECT * FROM libraries WHERE id=?", (lid,)))

    @app.route("PUT", r"/api/admin/libraries/(?P<lid>\d{1,6})", auth="admin")
    def admin_library_update(req):
        lid = int(req.params["lid"])
        l = db.one("SELECT * FROM libraries WHERE id=?", (lid,))
        if not l:
            raise HTTPError(404, "Bibliothèque introuvable")
        name, kind, paths = validate_library(req.json())
        if kind != l["kind"] and db.scalar("SELECT COUNT(*) FROM items WHERE library_id=?", (lid,)):
            db.run("DELETE FROM items WHERE library_id=?", (lid,))
        db.run("UPDATE libraries SET name=?, kind=?, paths=? WHERE id=?", (name, kind, json.dumps(paths), lid))
        db.release()
        ctx.scanner.start([lid])
        return library_view(db.one("SELECT * FROM libraries WHERE id=?", (lid,)))

    @app.route("DELETE", r"/api/admin/libraries/(?P<lid>\d{1,6})", auth="admin")
    def admin_library_delete(req):
        if ctx.scanner.status()["running"]:
            raise HTTPError(409, "Un scan est en cours, réessaie à la fin")
        db.run("DELETE FROM libraries WHERE id=?", (int(req.params["lid"]),))
        db.run("DELETE FROM user_libraries WHERE section_id=?", (req.params["lid"],))
        return {"ok": True}

    @app.route("POST", "/api/admin/scan", auth="admin")
    def admin_scan(req):
        b = req.json()
        lib = b.get("library")
        ids = [int(lib)] if lib and str(lib).isdigit() else None
        db.release()
        if not ctx.scanner.start(ids, refresh_metadata=bool(b.get("refresh_metadata"))):
            raise HTTPError(409, "Un scan est déjà en cours")
        db.set_setting("last_scan", int(time.time()))
        return ctx.scanner.status()

    @app.route("GET", "/api/admin/scan", auth="admin")
    def admin_scan_status(req):
        return ctx.scanner.status()

    @app.route("GET", "/api/admin/browse", auth="admin")
    def admin_browse(req):
        path = os.path.normpath(req.arg("path") or "/")
        if not os.path.isabs(path) or not os.path.isdir(path):
            raise HTTPError(400, "Dossier introuvable")
        try:
            dirs = sorted((d for d in os.listdir(path) if not d.startswith(".") and
                           os.path.isdir(os.path.join(path, d))), key=str.lower)[:500]
        except OSError as e:
            raise HTTPError(400, f"Lecture impossible : {e.strerror}")
        return {"path": path, "parent": os.path.dirname(path) if path != "/" else None, "dirs": dirs}

    # ---------------------------------------------------- admin settings
    @app.route("POST", "/api/hooks/scan", auth="webhook")
    def hook_scan(req):
        """Webhook pour Radarr / Sonarr / script : déclenche un scan (regroupé sur 10 s)."""
        try:
            event = (req.json() or {}).get("eventType", "")
        except HTTPError:
            event = ""
        if event == "Test":
            return {"ok": True, "message": "Séance a bien reçu le test"}
        ctx.watcher.request_scan(f"webhook {event}".strip())
        return {"ok": True}

    @app.route("GET", "/api/hooks/scan", auth="webhook")
    def hook_scan_get(req):
        ctx.watcher.request_scan("webhook")
        return {"ok": True}

    @app.route("POST", "/api/admin/webhook/regenerate", auth="admin")
    def admin_webhook_regenerate(req):
        db.set_setting("webhook_token", secrets.token_urlsafe(24))
        return admin_settings(req)

    @app.route("GET", "/api/admin/settings", auth="admin")
    def admin_settings(req):
        max_streams, max_transcodes = ctx.limits()
        return {"watch_interval": ctx.setting_int("watch_interval", 60),
                "subs_prefetch": db.setting("subs_prefetch", "1") == "1",
                "trickplay": db.setting("trickplay", "1") == "1",
                "subs_queue": len(ctx.subtitles.urgent) + len(ctx.subtitles.idle),
                "webhook_path": f"/api/hooks/scan?token={db.setting('webhook_token')}",
                "watch_events": ctx.watcher.events[:8],"tmdb_set": bool(db.setting("tmdb_key")), "language": db.setting("meta_language", "fr-FR"),
                "scan_interval": ctx.setting_int("scan_interval", 6), "max_streams": max_streams,
                "max_transcodes": max_transcodes, "cpus": os.cpu_count(), "ffmpeg": media.available(),
                "version": VERSION}

    @app.route("PUT", "/api/admin/settings", auth="admin")
    def admin_settings_put(req):
        b = req.json()
        if "tmdb_key" in b:
            key = str(b.get("tmdb_key") or "").strip()
            if key:
                if len(key) > 400 or not re.match(r"^[A-Za-z0-9._-]+$", key):
                    raise HTTPError(400, "Clé TMDB invalide")
                from .metadata import TMDB
                try:
                    TMDB(key).check()
                except MetaError as e:
                    raise HTTPError(400, str(e))
            db.set_setting("tmdb_key", key or None)
        if "language" in b:
            if not re.match(r"^[a-z]{2}-[A-Z]{2}$", str(b["language"])):
                raise HTTPError(400, "Langue invalide (ex. fr-FR)")
            db.set_setting("meta_language", b["language"])
            db.set_setting("meta_country", b["language"][-2:])
        if "trickplay" in b:
            db.set_setting("trickplay", "1" if b["trickplay"] else "0")
            if b["trickplay"]:
                threading.Thread(target=ctx.prefetch_missing_subtitles, daemon=True).start()
        if "subs_prefetch" in b:
            db.set_setting("subs_prefetch", "1" if b["subs_prefetch"] else "0")
            if b["subs_prefetch"]:
                for r in db.all("SELECT id, probe FROM items WHERE type IN ('movie','episode') ORDER BY added_at DESC"):
                    if text_tracks(json.loads(r["probe"] or "{}")):
                        ctx.subtitles.enqueue(r["id"])
        for k, lo, hi in (("scan_interval", 0, 168), ("max_streams", 1, 50), ("max_transcodes", 0, 16),
                          ("watch_interval", 0, 3600)):
            if k in b:
                try:
                    v = int(b[k])
                except (TypeError, ValueError):
                    raise HTTPError(400, f"Valeur invalide : {k}")
                if not lo <= v <= hi:
                    raise HTTPError(400, f"{k} doit être entre {lo} et {hi}")
                db.set_setting(k, v)
        return admin_settings(req)

    @app.route("GET", "/api/admin/streams", auth="admin")
    def admin_streams(req):
        rows = db.all("SELECT s.id, s.title, s.quality, s.position_ms, s.state, s.created_at, s.last_seen, s.params, "
                      "u.display_name AS user FROM streams s JOIN users u ON u.id=s.user_id "
                      "WHERE s.last_seen > ? ORDER BY s.created_at DESC", (int(time.time()) - 180,))
        for r in rows:
            p = json.loads(r.pop("params") or "{}")
            r["mode"] = p.get("mode")
            r["reasons"] = p.get("reasons", [])
            r.update(ctx.transcoder.stats(r["id"]))
        return rows

    @app.route("DELETE", f"/api/admin/streams/{SID}", auth="admin")
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

    # ---------------------------------------------------- admin partages SMB
    def mounts(cmd, args=None):
        try:
            return mountd.call(cmd, args, sock_path=os.environ.get("SEANCE_MOUNTD_SOCK"))
        except mountd.MountError as e:
            raise HTTPError(503 if "pas démarré" in str(e) else 400, str(e))

    @app.route("GET", "/api/admin/shares", auth="admin")
    def admin_shares(req):
        db.release()
        try:
            st = mountd.call("status", sock_path=os.environ.get("SEANCE_MOUNTD_SOCK"), timeout=20)
        except mountd.MountError as e:
            return {"available": False, "error": str(e), "shares": [], "env": None}
        libs = db.all("SELECT id, name, paths FROM libraries")
        for sh in st["shares"]:
            sh["libraries"] = [l["name"] for l in libs if any(
                p == sh["path"] or p.startswith(sh["path"] + os.sep) for p in json.loads(l["paths"]))]
        st["available"] = True
        return st

    @app.route("POST", "/api/admin/shares/discover", auth="admin")
    def admin_shares_discover(req):
        b = req.json()
        db.release()
        return mounts("discover", {k: b.get(k) for k in ("host", "username", "password", "domain", "version", "reuse")})

    @app.route("POST", "/api/admin/shares", auth="admin")
    def admin_shares_add(req):
        b = req.json()
        db.release()
        return mounts("add", {k: b.get(k) for k in ("name", "host", "share", "subdir", "username", "password",
                                                     "domain", "version")})

    @app.route("POST", r"/api/admin/shares/(?P<name>[a-z0-9_-]{1,32})/remount", auth="admin")
    def admin_shares_remount(req):
        db.release()
        return mounts("remount", {"name": req.params["name"]})

    @app.route("DELETE", r"/api/admin/shares/(?P<name>[a-z0-9_-]{1,32})", auth="admin")
    def admin_shares_remove(req):
        db.release()
        return mounts("remove", {"name": req.params["name"]})

    # ------------------------------------------------- admin identification
    def root_item(r):
        if r["type"] in ("season", "episode"):
            return db.one("SELECT * FROM items WHERE id=?", (r["show_id"],))
        return db.one("SELECT * FROM items WHERE id=?", (r["id"],))

    @app.route("GET", f"/api/admin/identify/{RK}", auth="admin")
    def admin_identify_search(req):
        r = root_item(item_row(req.params["rk"]))
        tmdb = ctx.metadata.tmdb()
        if not tmdb.enabled:
            raise HTTPError(400, "Ajoute d'abord une clé TMDB (Administration → Métadonnées)")
        q = (req.arg("q") or r["title"]).strip()[:100]
        year = req.arg("year")
        try:
            res = tmdb.search("movie" if r["type"] == "movie" else "tv", q, int(year) if year and year.isdigit() else None)
        except MetaError as e:
            raise HTTPError(502, str(e))
        for x in res:
            x["poster"] = f"/api/admin/tmdb-img?p={x['poster']}" if x.get("poster") else None
        return {"query": q, "results": res, "current": r["tmdb_id"]}

    @app.route("POST", f"/api/admin/identify/{RK}", auth="admin")
    def admin_identify(req):
        r = root_item(item_row(req.params["rk"]))
        tid = req.json().get("tmdb_id")
        if not isinstance(tid, int) or tid <= 0:
            raise HTTPError(400, "Identifiant TMDB invalide")
        db.run("UPDATE items SET locked=1, tmdb_id=? WHERE id=?", (tid, r["id"]))
        try:
            ctx.metadata.refresh(db.one("SELECT * FROM items WHERE id=?", (r["id"],)), tmdb_id=tid)
        except MetaError as e:
            raise HTTPError(502, str(e))
        return {"ok": True, "id": str(r["id"])}

    @app.route("POST", f"/api/admin/refresh/{RK}", auth="admin")
    def admin_refresh(req):
        r = root_item(item_row(req.params["rk"]))
        try:
            state = ctx.metadata.refresh(r)
        except MetaError as e:
            raise HTTPError(502, str(e))
        return {"ok": True, "state": state}

    @app.route("GET", "/api/admin/tmdb-img", auth="admin")
    def admin_tmdb_img(req):
        p = req.arg("p") or ""
        if not re.match(r"^/[A-Za-z0-9]{5,60}\.(jpg|png)$", p):
            raise HTTPError(400, "Image invalide")
        try:
            src = ctx.images._download(p, "poster")
            dest = os.path.join(ctx.images.cache, "tmdbsearch_" + hashlib.sha1(p.encode()).hexdigest() + ".jpg")
            if not os.path.isfile(dest):
                media.resize_image(src, dest, 140, 210)
        except Exception:
            raise HTTPError(404, "Image indisponible")
        with open(dest, "rb") as f:
            return Response(f.read(), 200, "image/jpeg", {"Cache-Control": "private, max-age=86400"})

    x.__dict__.update(card=card, attach_state=attach_state, require_item=require_item, item_row=item_row,
                      visible=visible, age_filter=age_filter, allowed_set=allowed_set, require_section=require_section,
                      leaves=leaves, libraries_all=libraries_all, ITEM_SQL=ITEM_SQL, EPISODE_ORDER=EPISODE_ORDER)
    collections_api.register(app, ctx, x)
    jellyfin.register(app, ctx, x)
    discover.register(app, ctx, x)
    return app


def janitor(ctx, stop_event):
    """Arrête les lectures abandonnées, purge sessions et cache, lance les scans planifiés."""
    last_cache = 0
    while not stop_event.wait(30):
        db = ctx.db
        try:
            now = int(time.time())
            for sid in ctx.transcoder.idle(150):
                ctx.transcoder.stop(sid)
            db.run("DELETE FROM streams WHERE last_seen < ?", (now - 600,))
            db.run("DELETE FROM sessions WHERE expires_at < ?", (now,))
            interval = ctx.setting_int("scan_interval", 6)
            if interval and db.scalar("SELECT COUNT(*) FROM libraries"):
                last = ctx.setting_int("last_scan", 0)
                if now - last > interval * 3600:
                    db.set_setting("last_scan", now)
                    ctx.scanner.start()
            if now - last_cache > 86400:
                last_cache = now
                for sub in ("img",):
                    d = os.path.join(ctx.data_dir, "cache", sub)
                    for name in os.listdir(d):
                        p = os.path.join(d, name)
                        try:
                            if now - os.path.getatime(p) > 60 * 86400:
                                os.remove(p)
                        except OSError:
                            pass
        except Exception:
            log.exception("Erreur du nettoyage périodique")
        finally:
            db.release()
