"""« Découvrir » : catalogues des services de streaming (via TMDB) et demandes envoyées à Seerr.

- Les catalogues (Netflix, Prime Video, Disney+, Canal+…) viennent de TMDB : ce qui est proposé en
  abonnement dans le pays choisi. Chaque titre indique s'il est déjà dans Séance ou déjà demandé.
- « Demander » transmet la demande à Seerr (ex-Overseerr / Jellyseerr) au nom de l'utilisateur, qui
  la fait télécharger par Radarr / Sonarr. Séance retrouve le compte Seerr de chacun grâce à l'API
  compatible Jellyfin (et l'importe dans Seerr au besoin).
- « À regarder » accepte aussi les titres absents de la bibliothèque : ils sont demandés
  automatiquement (si activé) et rejoignent la liste habituelle dès leur arrivée.
"""
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from .metadata import TMDB_IMG, MetaError
from .web import HTTPError, Response

log = logging.getLogger("seance.discover")

# Services mis en avant (identifiants TMDB des fournisseurs), dans cet ordre s'ils existent dans le pays
FEATURED = [8, 119, 337, 381, 1899, 350, 531, 283, 415]
FALLBACK_NAMES = {8: "Netflix", 119: "Prime Video", 337: "Disney+", 381: "Canal+", 1899: "Max", 350: "Apple TV+",
                  531: "Paramount+", 283: "Crunchyroll", 415: "ADN"}
IMG_PATH = re.compile(r"^/[A-Za-z0-9_-]{4,80}\.(?:jpg|jpeg|png|svg)$")
IMG_SIZES = {"poster": "w342", "backdrop": "w1280", "logo": "w154", "still": "w300"}
STATUS = {2: "pending", 3: "processing", 4: "partial", 5: "available", 6: "blocked", 7: "deleted"}
SORTS = {
    "movie": {"popular": "popularity.desc", "recent": "primary_release_date.desc", "rating": "vote_average.desc"},
    "tv": {"popular": "popularity.desc", "recent": "first_air_date.desc", "rating": "vote_average.desc"},
}


class TTLCache:
    def __init__(self, ttl, size=400):
        self.ttl, self.size = ttl, size
        self.data = {}
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            v = self.data.get(key)
            if v and time.time() - v[0] < self.ttl:
                return v[1]
        return None

    def put(self, key, value):
        with self.lock:
            if len(self.data) > self.size:
                self.data.clear()
            self.data[key] = (time.time(), value)
        return value

    def clear(self):
        with self.lock:
            self.data.clear()


class SeerrError(Exception):
    pass


class Seerr:
    def __init__(self, url, key):
        self.url = (url or "").rstrip("/")
        self.key = key or ""

    @property
    def enabled(self):
        return bool(self.url and self.key)

    def call(self, method, path, body=None, user=None, timeout=15):
        if not self.enabled:
            raise SeerrError("Seerr n'est pas configuré")
        headers = {"X-Api-Key": self.key, "Accept": "application/json", "User-Agent": "Seance/2"}
        if user:
            headers["X-API-User"] = str(int(user))
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + "/api/v1" + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read(8 * 1024 * 1024)
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read(65536)).get("message") or ""
            except Exception:
                msg = ""
            if e.code in (401, 403) and not msg:
                msg = "clé d'API refusée" if e.code == 401 else "accès refusé"
            raise SeerrError(f"Seerr a répondu {e.code}{' : ' + msg if msg else ''}") from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise SeerrError(f"Seerr injoignable ({getattr(e, 'reason', e)})") from None
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            raise SeerrError("Réponse de Seerr illisible") from None


def register(app, ctx, x):
    db = ctx.db
    tmdb_cache = TTLCache(6 * 3600)
    status_cache = TTLCache(60, size=4)
    users_cache = TTLCache(300, size=4)
    img_dir = os.path.join(ctx.data_dir, "cache", "discover")
    os.makedirs(img_dir, exist_ok=True)
    pool = concurrent.futures.ThreadPoolExecutor(6, thread_name_prefix="discover")

    def tmdb():
        t = ctx.metadata.tmdb()
        if not t.enabled:
            raise HTTPError(409, "Ajoute une clé TMDB (Administration → Réglages) pour découvrir les catalogues")
        return t

    def region():
        return (db.setting("meta_country", "FR") or "FR").upper()

    def tget(path, **params):
        t = tmdb()
        key = json.dumps([path, t.language, sorted(params.items())])
        hit = tmdb_cache.get(key)
        if hit is not None:
            return hit
        try:
            return tmdb_cache.put(key, t.get(path, **params))
        except MetaError as e:
            raise HTTPError(502, str(e))

    def seerr():
        return Seerr(db.setting("seerr_url"), db.setting("seerr_key"))

    # ------------------------------------------------------------ statuts
    def seerr_statuses():
        s = seerr()
        if not s.enabled:
            return {}
        hit = status_cache.get("all")
        if hit is not None:
            return hit
        out = {}
        try:
            for page in range(25):
                r = s.call("GET", f"/media?take=500&skip={page * 500}&sort=modified")
                for m in r.get("results", []):
                    if m.get("tmdbId") and m.get("mediaType") in ("movie", "tv"):
                        st = STATUS.get(m.get("status"))
                        if st:
                            out[(m["mediaType"], int(m["tmdbId"]))] = st
                if (r.get("pageInfo") or {}).get("pages", 1) <= page + 1:
                    break
        except SeerrError as e:
            log.info("Statuts Seerr indisponibles : %s", e)
        return status_cache.put("all", out)

    def library_matches(user, pairs):
        """{(type, tmdb_id): item_id} pour les titres déjà dans les bibliothèques visibles par l'utilisateur."""
        ids = sorted({t for _, t in pairs})
        if not ids:
            return {}
        allowed = sorted(x.allowed_set(user), key=int)
        if not allowed:
            return {}
        age_sql, age_args = x.age_filter(user)
        rows = db.all(x.ITEM_SQL + f"WHERE i.type IN ('movie','show') AND i.tmdb_id IN ({','.join('?' * len(ids))}) "
                      f"AND i.library_id IN ({','.join('?' * len(allowed))})" + age_sql,
                      ids + [int(a) for a in allowed] + age_args)
        return {("movie" if r["type"] == "movie" else "tv", r["tmdb_id"]): str(r["id"]) for r in rows}

    def wished(user):
        return {(r["media_type"], r["tmdb_id"]): r for r in
                db.all("SELECT * FROM wishlist WHERE user_id=?", (user["id"],))}

    def card(res, kind=None):
        mt = kind or res.get("media_type")
        if mt not in ("movie", "tv") or not res.get("id"):
            return None
        d = res.get("release_date") if mt == "movie" else res.get("first_air_date")
        return {"media_type": mt, "tmdb_id": int(res["id"]), "title": res.get("title") or res.get("name") or "",
                "original_title": res.get("original_title") or res.get("original_name") or "",
                "year": int(d[:4]) if d and d[:4].isdigit() else None, "poster": res.get("poster_path"),
                "backdrop": res.get("backdrop_path"), "overview": res.get("overview") or "",
                "rating": round(res["vote_average"], 1) if res.get("vote_average") else None,
                "popularity": res.get("popularity") or 0}

    def decorate(user, cards):
        cards = [c for c in cards if c]
        pairs = [(c["media_type"], c["tmdb_id"]) for c in cards]
        lib = library_matches(user, pairs)
        st = seerr_statuses()
        wl = wished(user)
        for c in cards:
            k = (c["media_type"], c["tmdb_id"])
            c["item_id"] = lib.get(k)
            c["status"] = "available" if c["item_id"] else st.get(k)
            if c["status"] == "available" and not c["item_id"]:
                c["status"] = "processing"  # disponible pour Seerr mais pas encore scanné (ou pas visible ici)
            c["wished"] = k in wl
        return cards

    # ------------------------------------------------------------ fournisseurs
    def providers():
        out = {}
        for kind in ("movie", "tv"):
            try:
                r = tget(f"/watch/providers/{kind}", watch_region=region())
            except HTTPError:
                continue
            for p in r.get("results", []):
                pid = p.get("provider_id")
                if pid in FEATURED and pid not in out:
                    out[pid] = {"id": pid, "name": FALLBACK_NAMES.get(pid) or p.get("provider_name"),
                                "logo": p.get("logo_path")}
        return [out[p] for p in FEATURED if p in out]

    def browse(kind, provider, sort="popular", page=1):
        params = {"watch_region": region(), "with_watch_monetization_types": "flatrate|free|ads",
                  "sort_by": SORTS[kind].get(sort, "popularity.desc"), "page": page, "include_adult": "false"}
        if provider:
            params["with_watch_providers"] = str(provider)
        today = date.today().isoformat()
        if sort == "recent":
            params["primary_release_date.lte" if kind == "movie" else "first_air_date.lte"] = today
            params["vote_count.gte"] = 5
        if sort == "rating":
            params["vote_count.gte"] = 300
        r = tget(f"/discover/{kind}", **params)
        return [card(res, kind) for res in r.get("results", [])], int(r.get("total_pages") or 1)

    @app.route("GET", "/api/discover/home")
    def discover_home(req):
        user = req.user
        provs = providers()
        jobs = {pool.submit(browse, k, p["id"]): (p["id"], k) for p in provs for k in ("movie", "tv")}
        trend = pool.submit(tget, "/trending/all/week")
        rows = {}
        for f, (pid, kind) in jobs.items():
            try:
                rows.setdefault(pid, {})[kind] = f.result()[0]
            except HTTPError:
                rows.setdefault(pid, {})[kind] = []
        try:
            trending = [card(r) for r in trend.result().get("results", [])]
        except HTTPError:
            trending = []
        out_rows = []
        for p in provs:
            mixed = []
            mv, tv = rows.get(p["id"], {}).get("movie", []), rows.get(p["id"], {}).get("tv", [])
            for i in range(max(len(mv), len(tv))):  # films et séries en alternance
                mixed += [c for c in (tv[i] if i < len(tv) else None, mv[i] if i < len(mv) else None) if c]
            out_rows.append({**p, "items": decorate(user, mixed[:24])})
        return {"region": region(), "seerr": seerr().enabled, "trending": decorate(user, trending[:20]),
                "providers": out_rows}

    @app.route("GET", "/api/discover/browse")
    def discover_browse(req):
        kind = req.arg("type") or "movie"
        if kind not in ("movie", "tv"):
            raise HTTPError(400, "Type invalide")
        provider = req.int_arg("provider", 0, lo=0)
        page = req.int_arg("page", 1, lo=1, hi=50)
        cards, pages = browse(kind, provider or None, req.arg("sort") or "popular", page)
        prov = next((p for p in providers() if p["id"] == provider), None)
        return {"page": page, "pages": min(pages, 50), "provider": prov, "results": decorate(req.user, cards)}

    @app.route("GET", "/api/discover/search")
    def discover_search(req):
        q = (req.arg("q") or "").strip()[:100]
        if len(q) < 2:
            return {"results": []}
        r = tget("/search/multi", query=q, include_adult="false", page=1)
        cards = [card(x_) for x_ in r.get("results", [])]
        return {"results": decorate(req.user, cards)}

    @app.route("GET", "/api/discover/title/(?P<kind>movie|tv)/(?P<tid>\\d{1,9})")
    def discover_title(req):
        kind, tid = req.params["kind"], int(req.params["tid"])
        d = tget(f"/{kind}/{tid}", append_to_response="videos,watch/providers,credits",
                 include_video_language=f"{tmdb().language[:2]},en,null")
        c = decorate(req.user, [card(d, kind)])[0]
        provs = ((d.get("watch/providers") or {}).get("results") or {}).get(region()) or {}
        streams = [{"id": p.get("provider_id"), "name": p.get("provider_name"), "logo": p.get("logo_path")}
                   for p in (provs.get("flatrate") or [])[:8]]
        vids = [v for v in (d.get("videos") or {}).get("results", []) if v.get("site") == "YouTube" and v.get("key")]
        vids.sort(key=lambda v: (v.get("type") != "Trailer", v.get("iso_639_1") != tmdb().language[:2]))
        trailer = vids[0]["key"] if vids and re.match(r"^[A-Za-z0-9_-]{6,20}$", vids[0]["key"]) else None
        seasons = [{"number": s.get("season_number"), "name": s.get("name"), "episodes": s.get("episode_count"),
                    "year": int(s["air_date"][:4]) if (s.get("air_date") or "")[:4].isdigit() else None}
                   for s in d.get("seasons", []) if s.get("season_number")] if kind == "tv" else []
        runtime = d.get("runtime") or (d.get("episode_run_time") or [None])[0]
        cast = [p.get("name") for p in (d.get("credits") or {}).get("cast", [])[:8] if p.get("name")]
        c.update(genres=[g.get("name") for g in d.get("genres", [])], runtime=runtime, streams=streams,
                 trailer=trailer, seasons=seasons, cast=cast, tagline=d.get("tagline") or "",
                 can_request=seerr().enabled and c["status"] not in ("available", "pending", "processing"))
        return c

    # ------------------------------------------------------------ demandes
    def seerr_user(user):
        """Compte Seerr de cet utilisateur (identifiant Jellyfin = GUID Séance), importé au besoin."""
        s = seerr()
        want = x.jf_guid(1, user["id"])
        users = users_cache.get("users")
        if users is None:
            r = s.call("GET", "/user?take=1000&skip=0")
            users = {str(u.get("jellyfinUserId") or "").replace("-", "").lower(): u.get("id")
                     for u in r.get("results", []) if u.get("jellyfinUserId")}
            users_cache.put("users", users)
        if want in users:
            return users[want]
        try:
            created = s.call("POST", "/user/import-from-jellyfin", {"jellyfinUserIds": [want]})
        except SeerrError as e:
            log.info("Import du compte %s dans Seerr impossible : %s", user["username"], e)
            return None
        users_cache.clear()
        for u in created if isinstance(created, list) else []:
            if str(u.get("jellyfinUserId") or "").replace("-", "").lower() == want:
                return u.get("id")
        return None

    def send_request(user, kind, tid, seasons=None):
        s = seerr()
        if not s.enabled:
            raise HTTPError(409, "Les demandes ne sont pas configurées (Administration → Demandes)")
        body = {"mediaType": kind, "mediaId": int(tid)}
        if kind == "tv":
            body["seasons"] = seasons if seasons else "all"
        try:
            uid = seerr_user(user)
            r = s.call("POST", "/request", body, user=uid)
        except SeerrError as e:
            raise HTTPError(502, str(e))
        status_cache.clear()
        st = STATUS.get(((r.get("media") or {}).get("status"))) or ("processing" if r.get("status") == 2 else "pending")
        return {"ok": True, "status": st, "as_user": bool(uid)}

    @app.route("POST", "/api/discover/request")
    def discover_request(req):
        b = req.json()
        kind = b.get("type")
        if kind not in ("movie", "tv"):
            raise HTTPError(400, "Type invalide")
        tid = int(b.get("tmdb_id") or 0)
        if tid <= 0:
            raise HTTPError(400, "Titre invalide")
        seasons = b.get("seasons")
        if seasons is not None and not (isinstance(seasons, list) and all(isinstance(n, int) and 0 < n < 1000
                                                                          for n in seasons)):
            raise HTTPError(400, "Saisons invalides")
        r = send_request(req.user, kind, tid, seasons or None)
        db.run("UPDATE wishlist SET requested_at=? WHERE user_id=? AND media_type=? AND tmdb_id=?",
               (int(time.time()), req.user["id"], kind, tid))
        return r

    # ------------------------------------------------------------ « À regarder » hors bibliothèque
    @app.route("GET", "/api/wishlist")
    def wishlist(req):
        rows = db.all("SELECT * FROM wishlist WHERE user_id=? ORDER BY added_at DESC", (req.user["id"],))
        cards = [{"media_type": r["media_type"], "tmdb_id": r["tmdb_id"], "title": r["title"], "year": r["year"],
                  "poster": r["poster"], "requested": bool(r["requested_at"])} for r in rows]
        return decorate(req.user, cards)

    @app.route("POST", "/api/wishlist")
    def wishlist_set(req):
        b = req.json()
        kind = b.get("type")
        tid = int(b.get("tmdb_id") or 0)
        if kind not in ("movie", "tv") or tid <= 0:
            raise HTTPError(400, "Titre invalide")
        uid = req.user["id"]
        if not b.get("on"):
            db.run("DELETE FROM wishlist WHERE user_id=? AND media_type=? AND tmdb_id=?", (uid, kind, tid))
            return {"ok": True, "wished": False}
        d = tget(f"/{kind}/{tid}")
        c = decorate(req.user, [card(d, kind)])[0]
        if c["item_id"]:
            # déjà dans la bibliothèque : c'est la liste « À regarder » habituelle
            db.run("INSERT OR IGNORE INTO watchlist(user_id,item_id,added_at) VALUES(?,?,?)",
                   (uid, int(c["item_id"]), int(time.time())))
            return {"ok": True, "wished": True, "item_id": c["item_id"]}
        db.run("INSERT OR IGNORE INTO wishlist(user_id,media_type,tmdb_id,title,year,poster,added_at) "
               "VALUES(?,?,?,?,?,?,?)", (uid, kind, tid, c["title"][:200], c["year"], c["poster"], int(time.time())))
        out = {"ok": True, "wished": True, "requested": False}
        if seerr().enabled and db.setting("seerr_auto_request", "1") == "1" \
                and c["status"] not in ("pending", "processing", "available", "partial"):
            try:
                out.update(send_request(req.user, kind, tid), requested=True)
                db.run("UPDATE wishlist SET requested_at=? WHERE user_id=? AND media_type=? AND tmdb_id=?",
                       (int(time.time()), uid, kind, tid))
            except HTTPError as e:
                out["request_error"] = e.message
        return out

    # ------------------------------------------------------------ images TMDB (relais + cache)
    @app.route("GET", "/api/discover/img")
    def discover_img(req):
        p = req.arg("p") or ""
        kind = req.arg("k") or "poster"
        if not IMG_PATH.match(p) or kind not in IMG_SIZES:
            raise HTTPError(400, "Image invalide")
        size = IMG_SIZES[kind]
        name = hashlib.sha1(f"{size}{p}".encode()).hexdigest() + os.path.splitext(p)[1]
        dest = os.path.join(img_dir, name)
        if not os.path.isfile(dest):
            try:
                rq = urllib.request.Request(f"{TMDB_IMG}/{size}{p}", headers={"User-Agent": "Seance/2"})
                with urllib.request.urlopen(rq, timeout=20) as r:
                    data = r.read(8 * 1024 * 1024)
            except (urllib.error.URLError, OSError):
                raise HTTPError(404, "Image indisponible")
            tmp = dest + f".{threading.get_ident()}.tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
            trim_images()
        ctype = {".png": "image/png", ".svg": "image/svg+xml"}.get(os.path.splitext(p)[1], "image/jpeg")
        with open(dest, "rb") as f:
            return Response(f.read(), 200, ctype, {"Cache-Control": "private, max-age=2592000, immutable"})

    trim_lock = threading.Lock()

    def trim_images(limit=4000):
        if not trim_lock.acquire(blocking=False):
            return
        try:
            files = [os.path.join(img_dir, f) for f in os.listdir(img_dir) if not f.endswith(".tmp")]
            if len(files) <= limit:
                return
            files.sort(key=lambda f: os.path.getatime(f))
            for f in files[:len(files) - int(limit * 0.8)]:
                try:
                    os.remove(f)
                except OSError:
                    pass
        finally:
            trim_lock.release()

    # ------------------------------------------------------------ administration
    def admin_view():
        s = seerr()
        return {"seerr_url": db.setting("seerr_url") or "", "seerr_key_set": bool(db.setting("seerr_key")),
                "auto_request": db.setting("seerr_auto_request", "1") == "1", "region": region(),
                "jellyfin_api": db.setting("jellyfin_api", "0") == "1", "enabled": s.enabled}

    @app.route("GET", "/api/admin/requests", auth="admin")
    def admin_requests(req):
        return admin_view()

    @app.route("PUT", "/api/admin/requests", auth="admin")
    def admin_requests_set(req):
        b = req.json()
        if "seerr_url" in b:
            url = str(b.get("seerr_url") or "").strip().rstrip("/")
            if url and not re.match(r"^https?://[^\s/]+(?::\d+)?(?:/[^\s]*)?$", url):
                raise HTTPError(400, "Adresse invalide (ex. http://192.168.1.20:5055)")
            if url.endswith("/api/v1"):
                url = url[:-7]
            db.set_setting("seerr_url", url or None)
        if "seerr_key" in b:
            key = str(b.get("seerr_key") or "").strip()
            if key and not re.match(r"^[A-Za-z0-9+/=_-]{16,200}$", key):
                raise HTTPError(400, "Clé d'API invalide")
            db.set_setting("seerr_key", key or None)
        if "auto_request" in b:
            db.set_setting("seerr_auto_request", "1" if b["auto_request"] else "0")
        status_cache.clear()
        users_cache.clear()
        return admin_view()

    @app.route("POST", "/api/admin/requests/test", auth="admin")
    def admin_requests_test(req):
        try:
            me = seerr().call("GET", "/auth/me")
            st = seerr().call("GET", "/settings/public")
        except SeerrError as e:
            raise HTTPError(502, str(e))
        server = "Jellyfin" if st.get("mediaServerType") == 2 else ("Plex" if st.get("mediaServerType") == 1 else
                                                                    "Emby" if st.get("mediaServerType") == 3 else "?")
        return {"ok": True, "user": me.get("displayName") or me.get("username") or me.get("email"),
                "title": st.get("applicationTitle") or "Seerr", "media_server": server}
