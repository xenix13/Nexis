"""Suivi sur Trakt et Letterboxd : ce qu'on a vu, les notes et la liste « À regarder ».

Trakt (api.trakt.tv) :
- l'administrateur enregistre une fois une application Trakt (identifiant et secret) ;
- chaque personne relie son compte depuis Paramètres → Mon compte → Trakt et Letterboxd, avec un code à saisir
  sur trakt.tv (pas de mot de passe à donner à Nexis) ;
- toutes les 6 heures (et à la demande) : ce qui est vu d'un côté devient vu de l'autre, les notes (1 à 5 étoiles
  ici, 1 à 10 sur Trakt) et la liste « À regarder » s'ajoutent des deux côtés. Rien n'est jamais retiré : un
  « non vu » ou une note retirée ne se propage pas.

Letterboxd n'a pas d'API publique : export d'un CSV au format d'import de Letterboxd (films vus, dates, notes),
et import de l'export de Letterboxd (ZIP ou CSV : films vus, notes, liste « À regarder »), reconnu par titre et
année.
"""
import csv
import io
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

from . import __version__ as VERSION
from .names import fold

log = logging.getLogger("seance.tracking")

API = os.environ.get("SEANCE_TRAKT_API", "https://api.trakt.tv")
OOB = "urn:ietf:wg:oauth:2.0:oob"
BATCH = 400
MAX_IMPORT = 20 * 1024 * 1024


class TraktError(Exception):
    pass


def iso(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_iso(s):
    try:
        return int(datetime.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return int(time.time())


class Library:
    """Les films et séries qu'une personne voit sur ce serveur, indexés par identifiant TMDB."""

    def __init__(self, ctx, x, user):
        db = ctx.db
        allowed = sorted(int(i) for i in x.allowed_set(user) if str(i).isdigit())
        self.movies, self.shows, self.episodes, self.by_title = {}, {}, {}, {}
        if not allowed:
            return
        marks = ",".join("?" * len(allowed))
        rows = db.all("SELECT i.id, i.type, i.title, i.original_title, i.year, i.tmdb_id, i.show_id, i.idx, i.age, "
                      "p.idx AS season, COALESCE(sh.age, i.age) AS eff_age FROM items i "
                      "LEFT JOIN items p ON p.id=i.parent_id LEFT JOIN items sh ON sh.id=i.show_id "
                      f"WHERE i.library_id IN ({marks}) AND i.type IN ('movie','show','episode')", allowed)
        shows_tmdb = {}
        for r in rows:
            if not x.visible(user, r):
                continue
            if r["type"] == "movie":
                if r["tmdb_id"]:
                    self.movies.setdefault(r["tmdb_id"], r)
                for t in {r["title"], r["original_title"]} - {None, ""}:
                    self.by_title.setdefault((fold(t), r["year"]), r)
            elif r["type"] == "show":
                if r["tmdb_id"]:
                    self.shows.setdefault(r["tmdb_id"], r)
                    shows_tmdb[r["id"]] = r["tmdb_id"]
        for r in rows:
            if r["type"] == "episode" and r["show_id"] in shows_tmdb and r["season"] is not None and r["idx"] is not None:
                self.episodes.setdefault((shows_tmdb[r["show_id"]], r["season"], r["idx"]), r)
        self.tmdb_of_show = shows_tmdb
        self.ids = {r["id"]: r for r in rows}

    def movie_by_title(self, name, year):
        key = fold(name)
        for y in (year, year - 1 if year else None, year + 1 if year else None):
            r = self.by_title.get((key, y))
            if r:
                return r
        return None


class Tracking:
    def __init__(self, ctx):
        self.ctx = ctx
        self.lock = threading.Lock()
        self.pending = {}   # compte -> code d'appareil en attente de validation sur trakt.tv
        self.syncing = set()

    # ------------------------------------------------------------ réglages
    def conf(self):
        db = self.ctx.db
        return {"client_id": db.setting("trakt_client_id") or "", "client_secret": db.setting("trakt_client_secret") or ""}

    def configured(self):
        c = self.conf()
        return bool(c["client_id"] and c["client_secret"])

    def link(self, uid):
        return self.ctx.db.one("SELECT * FROM trakt_links WHERE user_id=?", (uid,))

    # ------------------------------------------------------------ HTTP
    def _req(self, method, path, body=None, token=None):
        c = self.conf()
        headers = {"Content-Type": "application/json", "trakt-api-version": "2", "trakt-api-key": c["client_id"],
                   "User-Agent": f"Nexis/{VERSION}"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read(30 * 1024 * 1024)
                return r.status, json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            return e.code, None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise TraktError(f"Trakt injoignable : {getattr(e, 'reason', e)}")

    def _ok(self, status, data, what):
        if status == 401:
            raise TraktError("Trakt refuse l'accès : reliez de nouveau le compte")
        if status == 429:
            raise TraktError("Trakt limite les requêtes : nouvel essai plus tard")
        if status >= 400 or data is None:
            raise TraktError(f"Trakt : erreur {status} ({what})")
        return data

    # ------------------------------------------------------------ liaison d'un compte (code d'appareil)
    def device_start(self, uid):
        if not self.configured():
            raise TraktError("L'application Trakt n'est pas enregistrée (administrateur)")
        st, d = self._req("POST", "/oauth/device/code", {"client_id": self.conf()["client_id"]})
        d = self._ok(st, d, "code")
        with self.lock:
            self.pending[uid] = {"device_code": d["device_code"], "expires": time.time() + int(d.get("expires_in") or 600),
                                 "interval": int(d.get("interval") or 5), "last": 0}
        return {"user_code": d["user_code"], "verification_url": d.get("verification_url") or "https://trakt.tv/activate",
                "expires_in": d.get("expires_in"), "interval": d.get("interval") or 5}

    def device_poll(self, uid):
        with self.lock:
            p = self.pending.get(uid)
        if not p:
            return "none"
        if time.time() > p["expires"]:
            self.pending.pop(uid, None)
            return "expired"
        c = self.conf()
        st, d = self._req("POST", "/oauth/device/token", {"code": p["device_code"], "client_id": c["client_id"],
                                                          "client_secret": c["client_secret"]})
        if st == 200 and d and d.get("access_token"):
            self.pending.pop(uid, None)
            self._store(uid, d)
            st2, me = self._req("GET", "/users/settings", token=d["access_token"])
            name = ((me or {}).get("user") or {}).get("username") if st2 == 200 else None
            self.ctx.db.run("UPDATE trakt_links SET username=? WHERE user_id=?", (name or "", uid))
            threading.Thread(target=self._sync_bg, args=(uid,), daemon=True).start()
            return "linked"
        if st in (400, 429):
            return "pending"
        self.pending.pop(uid, None)
        return {404: "invalid", 409: "used", 410: "expired", 418: "denied"}.get(st, "error")

    def _store(self, uid, d):
        now = int(time.time())
        expires = int(d.get("created_at") or now) + int(d.get("expires_in") or 7776000)
        self.ctx.db.run("INSERT INTO trakt_links(user_id, access_token, refresh_token, expires_at, created_at) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(user_id) DO UPDATE SET access_token=excluded.access_token, "
                        "refresh_token=excluded.refresh_token, expires_at=excluded.expires_at, last_error=NULL",
                        (uid, d["access_token"], d.get("refresh_token") or "", expires, now))

    def token(self, uid):
        link = self.link(uid)
        if not link:
            raise TraktError("Compte Trakt non relié")
        if link["expires_at"] - time.time() > 86400:
            return link["access_token"]
        c = self.conf()
        st, d = self._req("POST", "/oauth/token", {"refresh_token": link["refresh_token"], "client_id": c["client_id"],
                                                   "client_secret": c["client_secret"], "redirect_uri": OOB,
                                                   "grant_type": "refresh_token"})
        if st != 200 or not d or not d.get("access_token"):
            raise TraktError("Trakt refuse l'accès : reliez de nouveau le compte")
        self._store(uid, d)
        return d["access_token"]

    def unlink(self, uid):
        link = self.link(uid)
        if link:
            c = self.conf()
            try:
                self._req("POST", "/oauth/revoke", {"token": link["access_token"], "client_id": c["client_id"],
                                                    "client_secret": c["client_secret"]})
            except TraktError:
                pass
        self.ctx.db.run("DELETE FROM trakt_links WHERE user_id=?", (uid,))

    # ------------------------------------------------------------ synchronisation
    def _sync_bg(self, uid):
        try:
            user = self.ctx.db.one("SELECT * FROM users WHERE id=?", (uid,))
            if user:
                self.sync(user)
        except Exception as e:
            log.warning("Trakt : %s", e)
        finally:
            self.ctx.db.release()

    def sync(self, user):
        """Échange avec Trakt pour un compte -> résumé {imported, exported, ratings, watchlist}."""
        uid = user["id"]
        with self.lock:
            if uid in self.syncing:
                raise TraktError("Synchronisation déjà en cours")
            self.syncing.add(uid)
        db = self.ctx.db
        try:
            token = self.token(uid)
            out = self._sync(user, token)
            db.run("UPDATE trakt_links SET last_sync=?, last_error=NULL, last_result=? WHERE user_id=?",
                   (int(time.time()), json.dumps(out), uid))
            return out
        except TraktError as e:
            db.run("UPDATE trakt_links SET last_error=? WHERE user_id=?", (str(e), uid))
            raise
        finally:
            with self.lock:
                self.syncing.discard(uid)

    def _get(self, path, token):
        st, d = self._req("GET", path, token=token)
        return self._ok(st, d, path)

    def _sync(self, user, token):
        x, db, uid = self.ctx.x, self.ctx.db, user["id"]
        lib = Library(self.ctx, x, user)
        out = {"imported": 0, "exported": 0, "ratings_in": 0, "ratings_out": 0, "watchlist_in": 0, "watchlist_out": 0}
        # --- vus
        t_movies = {}
        for m in self._get("/sync/watched/movies", token):
            tmdb = ((m.get("movie") or {}).get("ids") or {}).get("tmdb")
            if tmdb:
                t_movies[tmdb] = parse_iso(m.get("last_watched_at"))
        t_eps = {}
        for s in self._get("/sync/watched/shows", token):
            tmdb = ((s.get("show") or {}).get("ids") or {}).get("tmdb")
            for season in s.get("seasons") or []:
                for ep in season.get("episodes") or []:
                    if tmdb:
                        t_eps[(tmdb, season.get("number"), ep.get("number"))] = parse_iso(ep.get("last_watched_at"))
        seen = {}
        for r in db.all("SELECT rating_key, last_watched_at FROM progress WHERE user_id=? AND watched=1", (uid,)):
            if str(r["rating_key"]).isdigit():
                seen[int(r["rating_key"])] = r["last_watched_at"] or 0
        for tmdb, ts in t_movies.items():
            row = lib.movies.get(tmdb)
            if row and row["id"] not in seen:
                self.mark_watched(uid, row["id"], ts)
                seen[row["id"]] = ts
                out["imported"] += 1
        for key, ts in t_eps.items():
            row = lib.episodes.get(key)
            if row and row["id"] not in seen:
                self.mark_watched(uid, row["id"], ts)
                seen[row["id"]] = ts
                out["imported"] += 1
        movies, shows = [], {}
        for iid, ts in seen.items():
            row = lib.ids.get(iid)
            if not row:
                continue
            when = iso(ts or time.time())
            if row["type"] == "movie" and row["tmdb_id"] and row["tmdb_id"] not in t_movies:
                movies.append({"ids": {"tmdb": row["tmdb_id"]}, "watched_at": when})
            elif row["type"] == "episode":
                st = lib.tmdb_of_show.get(row["show_id"])
                if st and (st, row["season"], row["idx"]) not in t_eps:
                    shows.setdefault(st, {}).setdefault(row["season"], []).append({"number": row["idx"], "watched_at": when})
        if x.get_prefs(uid).get("private_mode"):  # mode privé : rien de ce qu'on regarde ne part vers Trakt
            movies, shows = [], {}
        out["exported"] = len(movies) + sum(len(e) for s in shows.values() for e in s.values())
        self._post_batches("/sync/history", movies, shows, token)
        # --- notes
        stars = {r["item_id"]: r["stars"] for r in db.all("SELECT item_id, stars FROM stars WHERE user_id=?", (uid,))}
        t_rated = set()
        for kind, index in (("movies", lib.movies), ("shows", lib.shows)):
            for it in self._get(f"/sync/ratings/{kind}", token):
                tmdb = ((it.get(kind[:-1]) or {}).get("ids") or {}).get("tmdb")
                row = index.get(tmdb)
                if not row:
                    continue
                t_rated.add(row["id"])
                if row["id"] not in stars and isinstance(it.get("rating"), int):
                    x.set_stars(uid, row["id"], max(1, min(5, round(it["rating"] / 2))))
                    out["ratings_in"] += 1
        rate = {"movies": [], "shows": []}
        for iid, v in stars.items():
            row = lib.ids.get(iid)
            if row and row["tmdb_id"] and iid not in t_rated and row["type"] in ("movie", "show"):
                rate[row["type"] + "s"].append({"ids": {"tmdb": row["tmdb_id"]}, "rating": v * 2})
        if rate["movies"] or rate["shows"]:
            st, d = self._req("POST", "/sync/ratings", rate, token)
            self._ok(st, d, "notes")
            out["ratings_out"] = len(rate["movies"]) + len(rate["shows"])
        # --- « À regarder »
        mine = {r["item_id"] for r in db.all("SELECT item_id FROM watchlist WHERE user_id=?", (uid,))}
        t_list = set()
        for kind, index in (("movies", lib.movies), ("shows", lib.shows)):
            for it in self._get(f"/sync/watchlist/{kind}", token):
                tmdb = ((it.get(kind[:-1]) or {}).get("ids") or {}).get("tmdb")
                row = index.get(tmdb)
                if not row:
                    continue
                t_list.add(row["id"])
                if row["id"] not in mine and not (row["type"] == "movie" and row["id"] in seen):
                    db.run("INSERT OR IGNORE INTO watchlist(user_id, item_id, added_at) VALUES(?,?,?)", (uid, row["id"], int(time.time())))
                    out["watchlist_in"] += 1
        add = {"movies": [], "shows": []}
        for iid in mine - t_list:
            row = lib.ids.get(iid)
            if row and row["tmdb_id"] and row["type"] in ("movie", "show"):
                add[row["type"] + "s"].append({"ids": {"tmdb": row["tmdb_id"]}})
        if add["movies"] or add["shows"]:
            st, d = self._req("POST", "/sync/watchlist", add, token)
            self._ok(st, d, "liste")
            out["watchlist_out"] = len(add["movies"]) + len(add["shows"])
        return out

    def _post_batches(self, path, movies, shows, token):
        show_list = [{"ids": {"tmdb": st}, "seasons": [{"number": n, "episodes": eps} for n, eps in seasons.items()]}
                     for st, seasons in shows.items()]
        for i in range(0, max(len(movies), len(show_list)), BATCH):
            body = {"movies": movies[i:i + BATCH], "shows": show_list[i:i + BATCH]}
            if body["movies"] or body["shows"]:
                st, d = self._req("POST", path, body, token)
                self._ok(st, d, "historique")

    def mark_watched(self, uid, item_id, ts):
        """Vu le … (date venue de Trakt ou de Letterboxd) : même effet que « Marquer comme vu », à cette date."""
        x, db = self.ctx.x, self.ctx.db
        from .catalog import CARD_SQL
        row = db.one(CARD_SQL + "WHERE i.id=?", (item_id,))
        if not row:
            return
        with db.tx():
            x.set_watched(uid, x.card(row), True)
            db.run("UPDATE progress SET last_watched_at=? WHERE user_id=? AND rating_key=?", (ts, uid, str(item_id)))
            db.run("UPDATE history SET watched_at=? WHERE user_id=? AND rating_key=? AND watched_at>=?",
                   (ts, uid, str(item_id), int(time.time()) - 60))

    # ------------------------------------------------------------ Letterboxd
    def letterboxd_csv(self, user):
        """Films vus (et notes) au format d'import de Letterboxd."""
        db, uid = self.ctx.db, user["id"]
        lib = Library(self.ctx, self.ctx.x, user)
        stars = {r["item_id"]: r["stars"] for r in db.all("SELECT item_id, stars FROM stars WHERE user_id=?", (uid,))}
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["tmdbID", "Title", "Year", "Rating", "WatchedDate"])
        rows = db.all("SELECT rating_key, last_watched_at FROM progress WHERE user_id=? AND watched=1 AND type='movie' "
                      "ORDER BY last_watched_at", (uid,))
        for r in rows:
            m = lib.ids.get(int(r["rating_key"])) if str(r["rating_key"]).isdigit() else None
            if not m:
                continue
            day = time.strftime("%Y-%m-%d", time.localtime(r["last_watched_at"])) if r["last_watched_at"] else ""
            w.writerow([m["tmdb_id"] or "", m["title"], m["year"] or "", stars.get(m["id"], ""), day])
        return buf.getvalue().encode("utf-8")

    def letterboxd_import(self, user, data):
        """Export de Letterboxd (ZIP, ou l'un de ses CSV) -> résumé {watched, rated, watchlist, unmatched}."""
        files = {}
        if data[:2] == b"PK":
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    for n in z.namelist():
                        base = n.rsplit("/", 1)[-1].lower()
                        if base in ("watched.csv", "ratings.csv", "watchlist.csv", "diary.csv") and z.getinfo(n).file_size < MAX_IMPORT:
                            files[base] = z.read(n)
            except zipfile.BadZipFile:
                raise TraktError("Archive Letterboxd illisible")
        else:
            head = data[:2000].decode("utf-8-sig", "replace")
            files["ratings.csv" if "Rating" in head.splitlines()[0] else "watched.csv"] = data
        if not files:
            raise TraktError("Aucun fichier Letterboxd reconnu (watched.csv, ratings.csv, watchlist.csv)")
        db, uid, x = self.ctx.db, user["id"], self.ctx.x
        lib = Library(self.ctx, x, user)
        seen = {int(r["rating_key"]) for r in db.all("SELECT rating_key FROM progress WHERE user_id=? AND watched=1", (uid,))
                if str(r["rating_key"]).isdigit()}
        stars = {r["item_id"] for r in db.all("SELECT item_id FROM stars WHERE user_id=?", (uid,))}
        out = {"watched": 0, "rated": 0, "watchlist": 0, "unmatched": []}
        missed = set()

        def rows(name):
            if name not in files:
                return []
            text = files[name].decode("utf-8-sig", "replace")
            return list(csv.DictReader(io.StringIO(text)))

        def find(r):
            try:
                year = int(r.get("Year") or 0) or None
            except ValueError:
                year = None
            m = lib.movie_by_title(r.get("Name") or r.get("Title") or "", year)
            if not m and (r.get("Name") or r.get("Title")):
                missed.add(f"{r.get('Name') or r.get('Title')} ({year or '?'})")
            return m

        def day(r):
            for k in ("Watched Date", "WatchedDate", "Date"):
                v = (r.get(k) or "").strip()
                if v:
                    try:
                        return int(time.mktime(time.strptime(v[:10], "%Y-%m-%d")))
                    except ValueError:
                        pass
            return int(time.time())

        for name in ("diary.csv", "watched.csv", "ratings.csv"):
            for r in rows(name):
                m = find(r)
                if not m:
                    continue
                if name != "ratings.csv" or "watched.csv" not in files:
                    if m["id"] not in seen:
                        self.mark_watched(uid, m["id"], day(r))
                        seen.add(m["id"])
                        out["watched"] += 1
                try:
                    rating = float(r.get("Rating") or 0)
                except ValueError:
                    rating = 0
                if rating and m["id"] not in stars:
                    x.set_stars(uid, m["id"], max(1, min(5, round(rating))))
                    stars.add(m["id"])
                    out["rated"] += 1
        for r in rows("watchlist.csv"):
            m = find(r)
            if m and m["id"] not in seen:
                if db.run("INSERT OR IGNORE INTO watchlist(user_id, item_id, added_at) VALUES(?,?,?)",
                          (uid, m["id"], int(time.time()))).rowcount:
                    out["watchlist"] += 1
        out["unmatched"] = sorted(missed)[:100]
        out["unmatched_count"] = len(missed)
        return out


# ------------------------------------------------------------------ routes
def register(app, ctx, x):
    import re
    from .web import HTTPError, Response
    db = ctx.db
    tk = ctx.tracking

    def view(req):
        link = tk.link(req.user["id"])
        v = {"configured": tk.configured(), "linked": bool(link), "pending": req.user["id"] in tk.pending}
        if link:
            v.update(username=link["username"], last_sync=link["last_sync"], last_error=link["last_error"],
                     last_result=json.loads(link["last_result"] or "null"))
        if req.user["is_admin"]:
            v["client_id"] = tk.conf()["client_id"]
            v["secret_set"] = bool(tk.conf()["client_secret"])
        return v

    @app.route("GET", "/api/me/trakt")
    def me_trakt(req):
        return view(req)

    @app.route("PUT", "/api/admin/trakt", auth="admin")
    def admin_trakt(req):
        b = req.json()
        for k, setting in (("client_id", "trakt_client_id"), ("client_secret", "trakt_client_secret")):
            if k in b:
                v = str(b[k] or "").strip()
                if v and not re.match(r"^[A-Za-z0-9]{20,128}$", v):
                    raise HTTPError(400, "Identifiants Trakt invalides")
                if v or k == "client_id":
                    db.set_setting(setting, v or None)
        return view(req)

    @app.route("POST", "/api/me/trakt/link")
    def link_start(req):
        try:
            return tk.device_start(req.user["id"])
        except TraktError as e:
            raise HTTPError(502, str(e))

    @app.route("POST", "/api/me/trakt/poll")
    def link_poll(req):
        db.release()
        try:
            return {"status": tk.device_poll(req.user["id"])}
        except TraktError as e:
            raise HTTPError(502, str(e))

    @app.route("POST", "/api/me/trakt/sync")
    def sync_now(req):
        db.release()
        try:
            return {"result": tk.sync(req.user), **view(req)}
        except TraktError as e:
            raise HTTPError(502, str(e))

    @app.route("DELETE", "/api/me/trakt")
    def unlink(req):
        tk.unlink(req.user["id"])
        return view(req)

    @app.route("GET", "/api/me/letterboxd.csv")
    def lb_export(req):
        data = tk.letterboxd_csv(req.user)
        return Response(data, 200, "text/csv; charset=utf-8",
                        {"Content-Disposition": 'attachment; filename="nexis-letterboxd.csv"', "Cache-Control": "no-store"})

    @app.route("POST", "/api/me/letterboxd")
    def lb_import(req):
        try:
            length = int(req.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if not 0 < length <= MAX_IMPORT:
            raise HTTPError(413, "Fichier vide ou trop volumineux (20 Mo au plus)")
        req._body = b""
        data, left = [], length
        while left:
            chunk = req.handler.rfile.read(min(left, 1024 * 1024))
            if not chunk:
                req.handler.close_connection = True
                raise HTTPError(400, "Envoi interrompu")
            data.append(chunk)
            left -= len(chunk)
        try:
            return tk.letterboxd_import(req.user, b"".join(data))
        except TraktError as e:
            raise HTTPError(400, str(e))
