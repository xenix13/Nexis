"""Sous-titres téléchargés depuis OpenSubtitles.com, sans outil à installer à côté (Bazarr…).

- Recherche : par l'empreinte du fichier (« moviehash » : taille + 64 Ko du début et de la fin, qui retrouve
  les sous-titres faits pour cette sortie précise), et par l'identifiant TMDB du film ou de la série (saison
  et épisode). Les résultats calés sur le fichier passent en premier.
- Téléchargement : un compte OpenSubtitles (gratuit : 20 sous-titres par jour) et une clé d'API (« consumer »,
  créée sur opensubtitles.com, rubrique API). Le compteur du jour est gardé pour ne pas dépasser le quota.
- Les fichiers sont écrits dans data/subtitles/<titre>/ (jamais dans les dossiers de médias, montés en
  lecture seule) et s'ajoutent aux sous-titres externes du titre ; un nouveau scan les garde.
"""
import json
import logging
import os
import re
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__ as VERSION

log = logging.getLogger("seance.opensubs")

API = os.environ.get("SEANCE_OPENSUBTITLES_API", "https://api.opensubtitles.com/api/v1")
USER_AGENT = f"Nexis v{VERSION}"
# Nexis (ISO 639-2) -> OpenSubtitles (ISO 639-1, avec variantes pour le portugais et le chinois)
TO_OS = {"fra": "fr", "eng": "en", "spa": "es", "deu": "de", "ita": "it", "por": "pt-PT", "jpn": "ja", "kor": "ko",
         "zho": "zh-CN", "rus": "ru", "ara": "ar", "nld": "nl", "pol": "pl", "tur": "tr", "swe": "sv", "dan": "da",
         "nor": "no", "fin": "fi", "hin": "hi", "tha": "th", "ces": "cs", "ell": "el", "heb": "he", "hun": "hu",
         "ron": "ro", "ukr": "uk", "vie": "vi", "ind": "id", "cat": "ca", "bul": "bg", "hrv": "hr", "srp": "sr"}
FROM_OS = {v: k for k, v in TO_OS.items()}
FROM_OS.update({"pt-BR": "por", "pb": "por", "pt": "por", "zh-TW": "zho", "zh": "zho", "ze": "zho"})
LANGS = tuple(TO_OS)
DOWNLOAD_HOST = re.compile(r"^(?:[a-z0-9-]+\.)*opensubtitles\.(?:com|org)$")
MAX_FILE = 4 * 1024 * 1024
FAIL_RETRY = 7 * 86400   # un titre sans résultat est recherché de nouveau au bout d'une semaine


class SubsError(Exception):
    pass


def moviehash(path):
    """Empreinte OpenSubtitles d'un fichier vidéo (16 chiffres hexadécimaux), ou None."""
    try:
        size = os.path.getsize(path)
        if size < 131072:
            return None
        h = size
        with open(path, "rb") as f:
            for pos in (0, size - 65536):
                f.seek(pos)
                buf = f.read(65536)
                for (v,) in struct.iter_unpack("<Q", buf):
                    h = (h + v) & 0xFFFFFFFFFFFFFFFF
        return f"{h:016x}"
    except OSError:
        return None


class OpenSubtitles:
    def __init__(self, ctx):
        self.ctx = ctx
        self.lock = threading.Lock()
        self.token = None          # (jeton, serveur, expiration)

    # ------------------------------------------------------------ réglages
    def conf(self):
        db = self.ctx.db
        try:
            langs = [l for l in json.loads(db.setting("os_langs") or '["fra"]') if l in TO_OS]
        except ValueError:
            langs = ["fra"]
        return {"key": db.setting("os_key") or "", "user": db.setting("os_user") or "",
                "password": db.setting("os_pass") or "", "langs": langs, "hi": db.setting("os_hi") == "1",
                "remaining": self.ctx.setting_int("os_remaining", -1), "reset": self.ctx.setting_int("os_reset", 0)}

    def configured(self):
        c = self.conf()
        return bool(c["key"] and c["user"] and c["password"])

    def quota_left(self):
        """Téléchargements restants aujourd'hui (None : inconnu)."""
        c = self.conf()
        if c["remaining"] < 0 or (c["reset"] and time.time() > c["reset"]):
            return None
        return c["remaining"]

    def forget_login(self):
        with self.lock:
            self.token = None

    # ------------------------------------------------------------ HTTP
    def _request(self, method, path, params=None, body=None, auth=False, base=None):
        c = self.conf()
        if not c["key"]:
            raise SubsError("Clé d'API OpenSubtitles manquante")
        headers = {"Api-Key": c["key"], "User-Agent": USER_AGENT, "Accept": "application/json"}
        if auth:
            token, host = self.login()
            headers["Authorization"] = f"Bearer {token}"
            if host and base is None and API.startswith("https://api.opensubtitles.com"):
                base = f"https://{host}/api/v1"  # compte VIP : serveur dédié indiqué à la connexion
        url = (base or API) + path
        if params:
            url += "?" + urllib.parse.urlencode(sorted(params.items()))  # ordre fixe : demandé par l'API (cache)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read(2 * 1024 * 1024) or b"{}")
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read(65536) or b"{}")
            except ValueError:
                detail = {}
            msg = detail.get("message") or detail.get("errors") or e.reason
            if isinstance(msg, list):
                msg = ", ".join(map(str, msg))
            if e.code == 401:
                self.forget_login()
                raise SubsError("OpenSubtitles refuse l'identifiant, le mot de passe ou la clé d'API")
            if e.code == 406 or e.code == 429:
                raise SubsError(f"Quota OpenSubtitles atteint : {msg}")
            raise SubsError(f"OpenSubtitles : {msg} ({e.code})")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise SubsError(f"OpenSubtitles injoignable : {getattr(e, 'reason', e)}")

    def login(self):
        with self.lock:
            if self.token and self.token[2] > time.time():
                return self.token[0], self.token[1]
        c = self.conf()
        if not (c["user"] and c["password"]):
            raise SubsError("Compte OpenSubtitles manquant")
        r = self._request("POST", "/login", body={"username": c["user"], "password": c["password"]})
        token = r.get("token")
        if not token:
            raise SubsError("OpenSubtitles n'a pas accepté la connexion")
        host = r.get("base_url") or ""
        host = host if re.match(r"^[a-z0-9.-]+\.opensubtitles\.(?:com|org)$", host) else ""
        allowed = (r.get("user") or {}).get("allowed_downloads")
        if isinstance(allowed, int) and self.quota_left() is None:
            self.ctx.db.set_setting("os_remaining", allowed)
        with self.lock:
            self.token = (token, host, time.time() + 20 * 3600)
        return token, host

    def check(self):
        """Essai des réglages (connexion au compte) -> {downloads restants, niveau}."""
        self.forget_login()
        self.login()
        r = self._request("GET", "/infos/user", auth=True)
        d = r.get("data") or {}
        if isinstance(d.get("remaining_downloads"), int):
            self.ctx.db.set_setting("os_remaining", d["remaining_downloads"])
        return {"remaining": d.get("remaining_downloads"), "allowed": d.get("allowed_downloads"),
                "level": d.get("level"), "vip": bool(d.get("vip"))}

    # ------------------------------------------------------------ recherche
    def query_for(self, row):
        """Paramètres de recherche d'un film ou d'un épisode."""
        db = self.ctx.db
        q = {}
        if row["type"] == "episode":
            show = db.one("SELECT title, tmdb_id, imdb_id FROM items WHERE id=?", (row["show_id"],)) or {}
            season = db.scalar("SELECT idx FROM items WHERE id=?", (row["parent_id"],))
            if show.get("tmdb_id"):
                q["parent_tmdb_id"] = show["tmdb_id"]
            elif show.get("imdb_id"):
                q["parent_imdb_id"] = re.sub(r"\D", "", show["imdb_id"])
            else:
                q["query"] = show.get("title") or row["title"]
            if season is not None:
                q["season_number"] = season
            if row["idx"] is not None:
                q["episode_number"] = row["idx"]
        else:
            if row.get("tmdb_id"):
                q["tmdb_id"] = row["tmdb_id"]
            elif row.get("imdb_id"):
                q["imdb_id"] = re.sub(r"\D", "", row["imdb_id"])
            else:
                q["query"] = row["title"]
                if row.get("year"):
                    q["year"] = row["year"]
        if row.get("key") and os.path.isfile(row["key"]):
            h = moviehash(row["key"])
            if h:
                q["moviehash"] = h
        return q

    def search(self, row, langs):
        q = self.query_for(row)
        q["languages"] = ",".join(sorted({TO_OS[l] for l in langs if l in TO_OS}))
        r = self._request("GET", "/subtitles", q)
        out = []
        for d in r.get("data") or []:
            a = d.get("attributes") or {}
            files = a.get("files") or []
            if not files or not isinstance(files[0].get("file_id"), int):
                continue
            lang = FROM_OS.get(a.get("language") or "", "")
            if not lang:
                continue
            out.append({"file_id": files[0]["file_id"], "lang": lang, "release": str(a.get("release") or files[0].get("file_name") or "")[:200],
                        "downloads": int(a.get("download_count") or 0), "hi": bool(a.get("hearing_impaired")),
                        "forced": bool(a.get("foreign_parts_only")), "hash_match": bool(a.get("moviehash_match")),
                        "machine": bool(a.get("machine_translated") or a.get("ai_translated")),
                        "trusted": bool(a.get("from_trusted")), "fps": a.get("fps") or None,
                        "uploader": str((a.get("uploader") or {}).get("name") or "")[:60]})
        out.sort(key=lambda s: (not s["hash_match"], s["machine"], s["forced"], not s["trusted"], -s["downloads"]))
        return out

    def best(self, results, lang, hi=False):
        """Le sous-titre à prendre automatiquement pour une langue : complet (pas seulement les passages en langue
        étrangère), écrit par une personne, sans indications pour sourds et malentendants sauf si demandé."""
        cands = [s for s in results if s["lang"] == lang and not s["forced"] and not s["machine"]]
        if not hi:
            cands = [s for s in cands if not s["hi"]] or cands
        return cands[0] if cands else None

    # ------------------------------------------------------------ téléchargement
    def download(self, file_id):
        """-> contenu SRT (octets). Compte un téléchargement sur le quota du jour."""
        left = self.quota_left()
        if left is not None and left <= 0:
            raise SubsError("Quota OpenSubtitles du jour atteint : nouvel essai demain")
        r = self._request("POST", "/download", body={"file_id": int(file_id), "sub_format": "srt"}, auth=True)
        if isinstance(r.get("remaining"), int):
            self.ctx.db.set_setting("os_remaining", r["remaining"])
            reset = r.get("reset_time_utc")
            try:
                ts = int(time.mktime(time.strptime(reset[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone) if reset else 0
            except (TypeError, ValueError):
                ts = 0
            self.ctx.db.set_setting("os_reset", ts or int(time.time()) + 86400)
        link = r.get("link") or ""
        u = urllib.parse.urlparse(link)
        official = u.scheme == "https" and DOWNLOAD_HOST.match(u.hostname or "")
        api = urllib.parse.urlparse(API)
        if not official and not (API != "https://api.opensubtitles.com/api/v1" and (u.scheme, u.netloc) == (api.scheme, api.netloc)):
            raise SubsError("Adresse de téléchargement inattendue")
        try:
            with urllib.request.urlopen(urllib.request.Request(link, headers={"User-Agent": USER_AGENT}), timeout=60) as f:
                data = f.read(MAX_FILE + 1)
        except (urllib.error.URLError, OSError) as e:
            raise SubsError(f"Téléchargement impossible : {getattr(e, 'reason', e)}")
        if len(data) > MAX_FILE or b"-->" not in data[:65536]:
            raise SubsError("Le fichier reçu n'est pas un sous-titre")
        return data

    # ------------------------------------------------------------ enregistrement
    def save(self, row, result, data):
        """Écrit le sous-titre et l'ajoute aux sous-titres externes du titre -> entrée ajoutée."""
        db = self.ctx.db
        folder = os.path.join(self.ctx.data_dir, "subtitles", str(int(row["id"])))
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{result['lang']}-{int(result['file_id'])}.srt")
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
        entry = {"path": path, "codec": "subrip", "lang": result["lang"], "forced": False, "sdh": bool(result.get("hi")),
                 "dl": "opensubtitles", "file_id": int(result["file_id"]), "release": result.get("release") or ""}
        with db.tx():
            cur = db.one("SELECT probe FROM items WHERE id=?", (row["id"],))
            probe = json.loads((cur or {}).get("probe") or "{}")
            ext = [e for e in probe.get("external", []) if e.get("path") != path]
            ext.append(entry)
            probe["external"] = ext
            db.run("UPDATE items SET probe=? WHERE id=?", (json.dumps(probe), row["id"]))
            db.run("DELETE FROM subs_attempts WHERE item_id=? AND lang=?", (row["id"], result["lang"]))
        return entry

    def remove(self, row, path):
        db = self.ctx.db
        with db.tx():
            cur = db.one("SELECT probe FROM items WHERE id=?", (row["id"],))
            probe = json.loads((cur or {}).get("probe") or "{}")
            ext = probe.get("external", [])
            keep = [e for e in ext if not (e.get("dl") and e.get("path") == path)]
            if len(keep) == len(ext):
                return False
            probe["external"] = keep
            db.run("UPDATE items SET probe=? WHERE id=?", (json.dumps(probe), row["id"]))
            # pas de nouvelle recherche automatique pour cette langue : la personne n'en voulait pas
            lang = next((e["lang"] for e in ext if e.get("path") == path), "")
            db.run("INSERT INTO subs_attempts(item_id, lang, at, result) VALUES(?,?,?, 'removed') ON CONFLICT(item_id, lang) "
                   "DO UPDATE SET at=excluded.at, result='removed'", (row["id"], lang, int(time.time())))
        try:
            os.remove(path)
        except OSError:
            pass
        return True

    # ------------------------------------------------------------ automatique
    @staticmethod
    def missing_langs(probe, langs):
        """Langues voulues pour lesquelles le titre n'a aucun sous-titre complet (intégré ou externe)."""
        have = {s.get("lang") for s in probe.get("subs", []) if not s.get("forced")}
        have |= {e.get("lang") for e in probe.get("external", []) if not e.get("forced")}
        return [l for l in langs if l not in have]

    def fetch_missing(self, row, langs, hi=False):
        """Cherche et télécharge les langues manquantes d'un titre -> (téléchargés, essayés)."""
        db = self.ctx.db
        probe = json.loads(row["probe"] or "{}")
        want = self.missing_langs(probe, langs)
        if not want:
            return 0, 0
        results = self.search(row, want)
        got = 0
        for lang in want:
            pick = self.best(results, lang, hi)
            if not pick:
                db.run("INSERT INTO subs_attempts(item_id, lang, at, result) VALUES(?,?,?, 'none') ON CONFLICT(item_id, lang) "
                       "DO UPDATE SET at=excluded.at, result='none'", (row["id"], lang, int(time.time())))
                continue
            self.save(row, pick, self.download(pick["file_id"]))
            got += 1
        return got, len(want)


def keep_downloaded(old_probe, new_external):
    """Nouveau scan : les sous-titres téléchargés (encore présents) restent à la suite de ceux du dossier."""
    try:
        old = json.loads(old_probe or "{}").get("external", []) if isinstance(old_probe, str) else (old_probe or {}).get("external", [])
    except ValueError:
        old = []
    have = {e["path"] for e in new_external}
    return new_external + [e for e in old if e.get("dl") and e.get("path") not in have and os.path.isfile(e.get("path") or "")]


# ------------------------------------------------------------------ routes
def register(app, ctx, x):
    from .catalog import LANG_NAMES, RK, stream_lists
    from .i18n import tr
    from .web import HTTPError
    db = ctx.db
    osub = ctx.opensubs

    extra = {"ces": "Tchèque", "ell": "Grec", "heb": "Hébreu", "hun": "Hongrois", "ron": "Roumain", "ukr": "Ukrainien",
             "vie": "Vietnamien", "ind": "Indonésien", "cat": "Catalan", "bul": "Bulgare", "hrv": "Croate", "srp": "Serbe"}

    def names(codes):
        return {c: tr(LANG_NAMES.get(c) or extra.get(c) or c.upper()) for c in codes}

    def view():
        c = osub.conf()
        last = ctx.tasks.last("opensubs")
        return {"key_set": bool(c["key"]), "user": c["user"], "password_set": bool(c["password"]), "langs": c["langs"],
                "hi": c["hi"], "remaining": osub.quota_left(), "configured": osub.configured(), "all_langs": list(LANGS),
                "lang_names": names(LANGS), "last": last}

    @app.route("GET", "/api/admin/opensubtitles", auth="admin")
    def admin_get(req):
        return view()

    @app.route("PUT", "/api/admin/opensubtitles", auth="admin")
    def admin_put(req):
        b = req.json()
        for field, key, limit in (("key", "os_key", 200), ("user", "os_user", 100), ("password", "os_pass", 200)):
            if field in b:
                v = str(b[field] or "").strip() if field != "password" else str(b[field] or "")
                if len(v) > limit or (field == "key" and v and not re.match(r"^[A-Za-z0-9_-]+$", v)):
                    raise HTTPError(400, "Réglage OpenSubtitles invalide")
                if v or field != "password":  # mot de passe vide : on garde celui enregistré
                    db.set_setting(key, v or None)
        if "langs" in b:
            langs = b["langs"]
            if not isinstance(langs, list) or len(langs) > 6 or any(l not in TO_OS for l in langs):
                raise HTTPError(400, "Langues invalides")
            db.set_setting("os_langs", json.dumps(list(dict.fromkeys(langs))))
        if "hi" in b:
            db.set_setting("os_hi", "1" if b["hi"] else "0")
        osub.forget_login()
        out = view()
        if osub.configured() and any(k in b for k in ("key", "user", "password")):
            db.release()
            try:
                out["account"] = osub.check()
            except SubsError as e:
                raise HTTPError(400, str(e))
            out["remaining"] = osub.quota_left()
        return out

    def local_item(req):
        r = x.require_item(req.user, req.params["rk"])
        if r["type"] not in ("movie", "episode"):
            raise HTTPError(400, "Sous-titres : film ou épisode seulement")
        if r.get("peer_id"):
            raise HTTPError(400, "Titre d'un serveur lié : à chercher depuis ce serveur")
        if not osub.configured():
            raise HTTPError(400, "OpenSubtitles n'est pas configuré (Paramètres → Sous-titres et son)")
        return r

    @app.route("GET", f"/api/items/{RK}/opensubtitles")
    def search(req):
        """Sous-titres proposés pour ce fichier (les langues demandées, ou celles du réglage et du compte)."""
        r = local_item(req)
        langs = [l for l in (req.arg("langs") or "").split(",") if l in TO_OS][:6]
        if not langs:
            prefs = x.get_prefs(req.user["id"])
            langs = list(dict.fromkeys([l for l in (prefs.get("sub_lang"), *osub.conf()["langs"]) if l in TO_OS]))
        db.release()
        try:
            results = osub.search(r, langs or ["fra"])
        except SubsError as e:
            raise HTTPError(502, str(e))
        probe = json.loads(r["probe"] or "{}")
        have = [{"path": e["path"], "lang": e["lang"], "release": e.get("release") or "", "file_id": e.get("file_id")}
                for e in probe.get("external", []) if e.get("dl")]
        return {"results": results[:60], "langs": langs, "downloaded": have, "remaining": osub.quota_left(),
                "missing": osub.missing_langs(probe, langs), "lang_names": names(LANGS)}

    @app.route("POST", f"/api/items/{RK}/opensubtitles")
    def fetch(req):
        r = local_item(req)
        b = req.json()
        try:
            file_id = int(b.get("file_id"))
        except (TypeError, ValueError):
            raise HTTPError(400, "Sous-titre invalide")
        lang = b.get("lang")
        if lang not in TO_OS or not 0 < file_id < 10 ** 12:
            raise HTTPError(400, "Sous-titre invalide")
        db.release()
        try:
            data = osub.download(file_id)
        except SubsError as e:
            raise HTTPError(502, str(e))
        entry = osub.save(r, {"file_id": file_id, "lang": lang, "hi": bool(b.get("hi")),
                              "release": str(b.get("release") or "")[:200]}, data)
        row = db.one("SELECT probe FROM items WHERE id=?", (r["id"],))
        _, subs = stream_lists(json.loads(row["probe"] or "{}"))
        return {"ok": True, "subtitle": {"lang": entry["lang"], "release": entry["release"]}, "subtitles": subs,
                "remaining": osub.quota_left()}

    @app.route("DELETE", f"/api/items/{RK}/opensubtitles/(?P<fid>\\d{{1,12}})", auth="admin")
    def remove(req):
        r = x.require_item(req.user, req.params["rk"])
        probe = json.loads(r["probe"] or "{}")
        e = next((e for e in probe.get("external", []) if e.get("dl") and str(e.get("file_id")) == req.params["fid"]), None)
        if not e or not osub.remove(r, e["path"]):
            raise HTTPError(404, "Sous-titre introuvable")
        return {"ok": True}
