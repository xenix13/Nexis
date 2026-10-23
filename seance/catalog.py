"""Catalogue partagé par les routes : droits d'accès, cartes des éléments, état de lecture, préférences."""
import json
import re
import threading
import time

from . import media
from .markers import from_chapters as chapter_markers
from .names import fold
from .scanner import EXTRA_LABELS
from .transcoder import QUALITY
from .web import HTTPError

RK = r"(?P<rk>\d{1,12})"
SID = r"(?P<sid>[A-Za-z0-9_-]{8,40})"
SORTS = {"title": "i.sort_title COLLATE NOCASE", "added": "i.added_at DESC", "year": "i.year DESC, i.sort_title",
         "rating": "i.rating DESC, i.sort_title", "release": "i.released DESC, i.sort_title"}
WATCHED_RATIO = 0.90
MIN_RESUME_MS = 30_000
PREPARE_WINDOW_MS = 4 * 60 * 1000  # au départ : sous-titres prêts sur les 4 minutes qui suivent
EXTERNAL_BASE = 1000  # identifiants des sous-titres externes
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
LANG = re.compile(r"^[a-z]{2,3}$")
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
    "accent": "#ffa43a",
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


# Les compteurs d'épisodes et de saisons ne sont calculés que pour les séries et les saisons (CASE est évalué
# paresseusement par SQLite) : une liste de films n'exécute aucune sous-requête.
_COUNTS = ("CASE i.type WHEN 'show' THEN (SELECT COUNT(*) FROM items x WHERE x.show_id=i.id AND x.type='episode') "
           "WHEN 'season' THEN (SELECT COUNT(*) FROM items x WHERE x.parent_id=i.id AND x.type='episode') ELSE 0 END AS leaves, "
           "CASE WHEN i.type='show' THEN (SELECT COUNT(*) FROM items y WHERE y.parent_id=i.id AND y.type='season') "
           "ELSE 0 END AS child_count ")
_JOINS = "FROM items i LEFT JOIN items p ON p.id=i.parent_id LEFT JOIN items sh ON sh.id=i.show_id "
ITEM_SQL = "SELECT i.*, p.idx AS parent_idx, COALESCE(sh.age, i.age) AS eff_age, " + _COUNTS + _JOINS
# Pour les grilles (bibliothèque, recherche, ajouts récents) : seulement ce qu'affiche une carte, sans
# l'analyse ffprobe ni la distribution, qui pèsent plusieurs Ko par élément.
CARD_SQL = ("SELECT i.id, i.type, i.title, i.year, i.idx, i.img_version, i.duration_ms, i.library_id, i.rating, "
            "i.content_rating, i.added_at, i.show_id, i.parent_id, i.released, i.extra_kind, i.age, i.summary, "
            "p.idx AS parent_idx, COALESCE(sh.age, i.age) AS eff_age, " + _COUNTS + _JOINS)
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
                     "external": False, "overlay": overlay, "ass": codec in ("ass", "ssa"),
                     "pgs": codec in ("hdmv_pgs_subtitle", "pgssub"), "selected": False})
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


def install(ctx, x):
    """Ajoute à x les outils de lecture du catalogue (fermetures sur la base de données)."""
    db = ctx.db

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
        """Repères d'intro / de générique : posés à la main, sinon chapitres nommés, sinon empreinte audio,
        sinon chapitres de la durée d'une chanson (estimation)."""
        probe = json.loads(row["probe"] or "{}")
        out = chapter_markers(probe.get("chapters") or [], probe.get("duration") or (row["duration_ms"] or 0) / 1000)
        for r in db.all("SELECT kind,start_ms,end_ms,source FROM markers WHERE rating_key=?", (str(row["id"]),)):
            cur = out.get(r["kind"])
            if r["source"] == "manual" or not cur or cur["source"] == "chapters-guess":
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
        chapters = [c for c in probe.get("chapters") or [] if (c.get("end") or 0) > (c.get("start") or 0)]
        return [{"start": int(c["start"] * 1000), "end": int(c["end"] * 1000), "title": c.get("title") or "",
                 "image": f"/api/items/{row['id']}/chapters/{n}.jpg" if len(chapters) >= 2 else None}
                for n, c in enumerate(chapters)]

    for name in (
        "age_filter", "allowed_ids", "allowed_set", "attach_state", "card", "chapters_for", "get_prefs",
        "item_row", "leaves", "libraries_all", "markers_for", "next_episode", "require_item",
        "require_section", "set_watched", "show_of", "track_prefs", "track_scope", "upsert_meta", "visible",
    ):
        setattr(x, name, locals()[name])
    x.ITEM_SQL, x.CARD_SQL, x.EPISODE_ORDER = ITEM_SQL, CARD_SQL, EPISODE_ORDER
