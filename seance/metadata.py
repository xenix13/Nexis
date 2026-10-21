"""Métadonnées : fichiers .nfo locaux et TMDB (The Movie Database)."""
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .names import fold, sort_title

log = logging.getLogger("seance.meta")

TMDB_API = os.environ.get("SEANCE_TMDB_API", "https://api.themoviedb.org/3")
TMDB_IMG = os.environ.get("SEANCE_TMDB_IMG", "https://image.tmdb.org/t/p")


class MetaError(Exception):
    pass


class TMDB:
    def __init__(self, key, language="fr-FR", country="FR"):
        self.key = (key or "").strip()
        self.language = language
        self.country = country
        self._cache = {}

    @property
    def enabled(self):
        return bool(self.key)

    def get(self, path, lang=None, **params):
        params = {k: v for k, v in params.items() if v not in (None, "")}
        params["language"] = lang or self.language
        headers = {"Accept": "application/json", "User-Agent": "Seance/2"}
        if self.key.startswith("eyJ"):
            headers["Authorization"] = "Bearer " + self.key
        else:
            params["api_key"] = self.key
        url = f"{TMDB_API}{path}?{urllib.parse.urlencode(params)}"
        if url in self._cache:
            return self._cache[url]
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
                    data = json.loads(r.read())
                self._cache[url] = data
                if len(self._cache) > 500:
                    self._cache.clear()
                return data
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:
                    time.sleep(2 + attempt * 2)
                    continue
                if e.code == 401:
                    raise MetaError("Clé TMDB refusée (401)")
                if e.code == 404:
                    raise MetaError("Introuvable sur TMDB")
                raise MetaError(f"TMDB a répondu {e.code}")
            except (urllib.error.URLError, OSError, ValueError) as e:
                if attempt < 2:
                    time.sleep(1)
                    continue
                raise MetaError(f"TMDB injoignable ({getattr(e, 'reason', e)})")

    def check(self):
        self.get("/configuration")

    def search(self, kind, title, year=None):
        if kind == "movie":
            r = self.get("/search/movie", query=title, year=year, include_adult="false")
        else:
            r = self.get("/search/tv", query=title, first_air_date_year=year, include_adult="false")
        out = []
        for x in r.get("results", [])[:10]:
            date = x.get("release_date") or x.get("first_air_date") or ""
            out.append({"tmdb_id": x["id"], "title": x.get("title") or x.get("name"),
                        "original_title": x.get("original_title") or x.get("original_name"),
                        "year": int(date[:4]) if date[:4].isdigit() else None,
                        "overview": x.get("overview") or "", "poster": x.get("poster_path"),
                        "popularity": x.get("popularity") or 0})
        return out

    def best_match(self, kind, title, year=None):
        res = self.search(kind, title, year)
        if not res and year:
            res = self.search(kind, title, None)
        if not res:
            # « Titre - Sous-titre » ou titre japonais romanisé : on essaie la première partie
            short = re.split(r"\s[-:–]\s", title)[0]
            if short != title:
                res = self.search(kind, short, year) or self.search(kind, short, None)
        if not res:
            return None
        t = fold(title)
        for r in res:  # titre identique + année identique d'abord
            if year and r["year"] == year and t in (fold(r["title"] or ""), fold(r["original_title"] or "")):
                return r["tmdb_id"]
        if year:
            for r in res:
                if r["year"] and abs(r["year"] - year) <= 1:
                    return r["tmdb_id"]
        return res[0]["tmdb_id"]


# ---------------------------------------------------------------- NFO
def _text(root, tag):
    el = root.find(tag)
    return el.text.strip() if el is not None and el.text else None


def read_nfo(path):
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    out = {
        "title": _text(root, "title"), "original_title": _text(root, "originaltitle"),
        "summary": _text(root, "plot") or _text(root, "outline"), "tagline": _text(root, "tagline"),
        "content_rating": _text(root, "mpaa"), "studio": _text(root, "studio"),
        "released": _text(root, "premiered") or _text(root, "aired") or _text(root, "releasedate"),
        "genres": [g.text.strip() for g in root.findall("genre") if g.text],
        "directors": [g.text.strip() for g in root.findall("director") if g.text][:5],
        "cast": [{"name": _text(a, "name"), "role": _text(a, "role")} for a in root.findall("actor")][:20],
    }
    y = _text(root, "year")
    out["year"] = int(y) if y and y.isdigit() else (int(out["released"][:4]) if out["released"] and
                                                       out["released"][:4].isdigit() else None)
    r = _text(root, "rating") or _text(root, "ratings/rating/value")
    try:
        out["rating"] = round(float(r), 1) if r else None
    except ValueError:
        out["rating"] = None
    tmdb = None
    for u in root.findall("uniqueid"):
        if (u.get("type") or "").lower() == "tmdb" and u.text and u.text.strip().isdigit():
            tmdb = int(u.text.strip())
    t = _text(root, "tmdbid")
    out["tmdb_id"] = tmdb or (int(t) if t and t.isdigit() else None)
    return out


def nfo_for(item):
    key = item["key"] or ""
    if item["type"] == "movie":
        base = os.path.splitext(key)[0]
        for p in (base + ".nfo", os.path.join(os.path.dirname(key), "movie.nfo")):
            if os.path.isfile(p):
                return read_nfo(p)
    elif item["type"] == "show" and not key.startswith("name:"):
        p = os.path.join(key, "tvshow.nfo")
        if os.path.isfile(p):
            return read_nfo(p)
    elif item["type"] == "episode":
        p = os.path.splitext(key)[0] + ".nfo"
        if os.path.isfile(p):
            return read_nfo(p)
    return None


# ---------------------------------------------------------------- apply
class Metadata:
    def __init__(self, ctx):
        self.ctx = ctx

    def tmdb(self):
        db = self.ctx.db
        return TMDB(db.setting("tmdb_key"), db.setting("meta_language", "fr-FR"),
                    db.setting("meta_country", "FR"))

    def _update(self, item_id, fields):
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields:
            return
        if "title" in fields:
            fields["sort_title"] = sort_title(fields["title"])
            fields["search"] = fold(fields["title"]) + (" " + fold(fields.get("original_title") or "")
                                                         if fields.get("original_title") else "")
        for k in ("genres",):
            if k in fields:
                fields[k] = json.dumps(fields[k], ensure_ascii=False)
        fields["updated_at"] = int(time.time())
        sets = ",".join(f"{k}=?" for k in fields)
        self.ctx.db.run(f"UPDATE items SET {sets} WHERE id=?", [*fields.values(), item_id])

    @staticmethod
    def _people(directors, cast):
        return json.dumps({"directors": directors or [], "cast": cast or []}, ensure_ascii=False)

    def apply_nfo(self, item, nfo):
        f = {k: nfo.get(k) for k in ("title", "original_title", "summary", "tagline", "content_rating", "studio",
                                      "released", "year", "rating")}
        if nfo.get("genres"):
            f["genres"] = nfo["genres"]
        if nfo.get("cast") or nfo.get("directors"):
            f["people"] = self._people(nfo.get("directors"), nfo.get("cast"))
        if item["type"] == "episode":
            f.pop("year", None)
        self._update(item["id"], f)

    def process_pending(self, progress=None):
        db = self.ctx.db
        tmdb = self.tmdb()
        roots = db.all("SELECT * FROM items WHERE meta_state='pending' AND type IN ('movie','show') ORDER BY id")
        pending_eps = db.scalar("SELECT COUNT(*) FROM items WHERE meta_state='pending' AND type IN ('season','episode')")
        total = len(roots) + (1 if pending_eps else 0)
        for n, item in enumerate(roots, 1):
            if progress:
                progress(n, total)
            try:
                self.refresh(item, tmdb)
            except MetaError as e:
                log.warning("Métadonnées de %s : %s", item["title"], e)
                db.run("UPDATE items SET meta_state='error' WHERE id=?", (item["id"],))
                if "401" in str(e):
                    break
            except Exception:
                log.exception("Métadonnées de %s", item["title"])
                db.run("UPDATE items SET meta_state='error' WHERE id=?", (item["id"],))
        # nouveaux épisodes de séries déjà identifiées
        shows = db.all("SELECT DISTINCT show_id FROM items WHERE meta_state='pending' AND type IN ('season','episode')")
        for r in shows:
            show = db.one("SELECT * FROM items WHERE id=?", (r["show_id"],))
            try:
                self.refresh_episodes(show, tmdb, only_pending=True)
            except MetaError as e:
                log.warning("Épisodes de %s : %s", show["title"] if show else r["show_id"], e)
                db.run("UPDATE items SET meta_state='error' WHERE show_id=? AND meta_state='pending'", (r["show_id"],))
        if progress:
            progress(total, total)

    def refresh(self, item, tmdb=None, tmdb_id=None):
        """(Re)charge les métadonnées d'un film ou d'une série (et de ses épisodes)."""
        db = self.ctx.db
        tmdb = tmdb or self.tmdb()
        nfo = nfo_for(item)
        if nfo:
            self.apply_nfo(item, nfo)
        kind = "movie" if item["type"] == "movie" else "tv"
        tid = tmdb_id or item["tmdb_id"] or (nfo or {}).get("tmdb_id")
        if tmdb.enabled and not tid:
            tid = tmdb.best_match(kind, item["title"], item["year"])
        if tmdb.enabled and tid:
            if kind == "movie":
                self._apply_movie(item, tmdb, tid)
            else:
                self._apply_show(item, tmdb, tid)
            state = "done"
        else:
            state = "local" if not tmdb.enabled or nfo else "nomatch"
        db.run("UPDATE items SET meta_state=? WHERE id=?", (state, item["id"]))
        if item["type"] == "show":
            fresh = db.one("SELECT * FROM items WHERE id=?", (item["id"],))
            self.refresh_episodes(fresh, tmdb)
        return state

    def _apply_movie(self, item, tmdb, tid):
        d = tmdb.get(f"/movie/{tid}", append_to_response="credits,release_dates")
        overview = d.get("overview")
        if not overview and tmdb.language != "en-US":
            overview = tmdb.get(f"/movie/{tid}", lang="en-US").get("overview")
        cert = None
        for c in (d.get("release_dates") or {}).get("results", []):
            if c.get("iso_3166_1") == tmdb.country:
                cert = next((x.get("certification") for x in c.get("release_dates", []) if x.get("certification")), None)
        crew = (d.get("credits") or {}).get("crew", [])
        cast = [{"name": c.get("name"), "role": c.get("character")} for c in (d.get("credits") or {}).get("cast", [])[:20]]
        date = d.get("release_date") or ""
        self._update(item["id"], {
            "tmdb_id": tid, "title": d.get("title") or item["title"], "original_title": d.get("original_title"),
            "summary": overview, "tagline": d.get("tagline") or None,
            "rating": round(d["vote_average"], 1) if d.get("vote_average") else None,
            "content_rating": cert, "released": date or None, "year": int(date[:4]) if date[:4].isdigit() else None,
            "studio": ", ".join(c["name"] for c in d.get("production_companies", [])[:2]) or None,
            "genres": [g["name"] for g in d.get("genres", [])],
            "people": self._people([c["name"] for c in crew if c.get("job") == "Director"][:3], cast),
        })
        self._images(item, poster=d.get("poster_path"), backdrop=d.get("backdrop_path"))

    def _images(self, item, poster=None, backdrop=None, still=None):
        """Les images locales (poster.jpg…) gardent la priorité sur celles de TMDB."""
        db = self.ctx.db
        cur = db.one("SELECT poster, backdrop, still FROM items WHERE id=?", (item["id"],))
        f = {}
        for col, val in (("poster", poster), ("backdrop", backdrop), ("still", still)):
            if val and not (cur[col] or "").startswith("local:"):
                f[col] = "tmdb:" + val
        if f:
            sets = ",".join(f"{k}=?" for k in f)
            db.run(f"UPDATE items SET {sets}, img_version=img_version+1 WHERE id=?", [*f.values(), item["id"]])

    def _apply_show(self, item, tmdb, tid):
        d = tmdb.get(f"/tv/{tid}", append_to_response="credits,content_ratings")
        overview = d.get("overview")
        if not overview and tmdb.language != "en-US":
            overview = tmdb.get(f"/tv/{tid}", lang="en-US").get("overview")
        cert = next((c.get("rating") for c in (d.get("content_ratings") or {}).get("results", [])
                     if c.get("iso_3166_1") == tmdb.country), None)
        cast = [{"name": c.get("name"), "role": c.get("character")} for c in (d.get("credits") or {}).get("cast", [])[:20]]
        date = d.get("first_air_date") or ""
        self._update(item["id"], {
            "tmdb_id": tid, "title": d.get("name") or item["title"], "original_title": d.get("original_name"),
            "summary": overview, "tagline": d.get("tagline") or None,
            "rating": round(d["vote_average"], 1) if d.get("vote_average") else None, "content_rating": cert,
            "released": date or None, "year": int(date[:4]) if date[:4].isdigit() else None,
            "studio": ", ".join(n["name"] for n in d.get("networks", [])[:2]) or None,
            "genres": [g["name"] for g in d.get("genres", [])],
            "people": self._people([c["name"] for c in d.get("created_by", [])][:3], cast),
        })
        self._images(item, poster=d.get("poster_path"), backdrop=d.get("backdrop_path"))
        item_seasons = {s["season_number"]: s for s in d.get("seasons", [])}
        for s in self.ctx.db.all("SELECT * FROM items WHERE parent_id=? AND type='season'", (item["id"],)):
            ts = item_seasons.get(s["idx"])
            if ts:
                self._update(s["id"], {"summary": ts.get("overview") or None,
                                       "released": ts.get("air_date") or None})
                self._images(s, poster=ts.get("poster_path"))

    def refresh_episodes(self, show, tmdb, only_pending=False):
        db = self.ctx.db
        if not show:
            return
        eps = db.all("SELECT e.*, s.idx AS season_idx FROM items e JOIN items s ON s.id=e.parent_id "
                     "WHERE e.show_id=? AND e.type='episode'" + (" AND e.meta_state='pending'" if only_pending else ""),
                     (show["id"],))
        for e in eps:
            nfo = nfo_for(e)
            if nfo:
                self.apply_nfo(e, nfo)
        tid = show["tmdb_id"]
        if tmdb.enabled and tid:
            info = tmdb.get(f"/tv/{tid}")
            counts = {s["season_number"]: s.get("episode_count") or 0 for s in info.get("seasons", [])}
            seasons_cache = {}

            def season(n):
                if n not in seasons_cache:
                    try:
                        seasons_cache[n] = {e["episode_number"]: e for e in tmdb.get(f"/tv/{tid}/season/{n}").get("episodes", [])}
                    except MetaError:
                        seasons_cache[n] = {}
                return seasons_cache[n]

            our_seasons = {e["season_idx"] for e in db.all("SELECT s.idx AS season_idx FROM items s WHERE s.parent_id=? AND type='season'", (show["id"],))}
            for e in eps:
                sn, en = e["season_idx"], e["idx"]
                # numérotation absolue (animes) : « Episode 1071 » dans une seule saison 1
                if sn == 1 and our_seasons <= {0, 1} and en > counts.get(1, 10 ** 6):
                    rest = en
                    for n in sorted(k for k in counts if k >= 1):
                        if rest <= counts[n]:
                            sn, en = n, rest
                            break
                        rest -= counts[n]
                te = season(sn).get(en)
                if te:
                    self._update(e["id"], {"title": te.get("name") or None, "summary": te.get("overview") or None,
                                           "released": te.get("air_date") or None,
                                           "rating": round(te["vote_average"], 1) if te.get("vote_average") else None})
                    self._images(e, still=te.get("still_path"))
        ids = [e["id"] for e in eps] + [s["id"] for s in db.all(
            "SELECT id FROM items WHERE parent_id=? AND type='season'", (show["id"],))]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            db.run(f"UPDATE items SET meta_state='done' WHERE id IN ({','.join('?' * len(chunk))})", chunk)
