"""Catalogue vu par les clients Jellyfin : bibliothèques, éléments, reprises, à suivre, séries, images."""
import json
import random
import time

from ..catalog import MIN_RESUME_MS, WATCHED_RATIO
from ..names import fold
from ..web import HTTPError, Response
from .common import (GUID_RE, K_GENRE, K_ITEM, K_LIB, K_PERSON, K_ROOT, K_STUDIO, KINDS, TICKS, TYPES,
                     guid, kind_of, result, unguid)
from .dto import Builder

U = rf"(?P<uid>Me|{GUID_RE})"
I = rf"(?P<iid>{GUID_RE})"
PLAYABLE = ("movie", "episode")
SORTS = {
    "sortname": "i.sort_title COLLATE NOCASE", "name": "i.title COLLATE NOCASE", "datecreated": "i.added_at",
    "premieredate": "i.released", "productionyear": "i.year", "communityrating": "i.rating",
    "criticrating": "i.rating", "runtime": "i.duration_ms", "indexnumber": "i.idx", "parentindexnumber": "p.idx",
    "seriessortname": "sh.sort_title COLLATE NOCASE", "officialrating": "i.content_rating",
    "dateplayed": "pr.last_watched_at", "playcount": "pr.view_count", "isfolder": "(i.type IN ('show','season'))",
    "dateLastContentAdded".lower(): "(CASE WHEN i.type='show' THEN (SELECT MAX(e.added_at) FROM items e "
                                    "WHERE e.show_id=i.id) ELSE i.added_at END)",
    "airedepisodeorder": "i.idx", "random": "RANDOM()",
}
LIKE_ESCAPE = " ESCAPE '\\'"


def like(term):
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def register(app, ctx, x, jf):
    db = ctx.db
    route = app.compat_route

    def fields_of(req):
        return jf.qlist(req, "Fields")

    def builder(req, user, extra=()):
        return Builder(jf, user, [*fields_of(req), *extra])

    def libs_for(user):
        allowed = x.allowed_set(user)
        return [l for l in x.libraries_all() if l["id"] in allowed]

    def visible_row(user, row):
        return bool(row) and str(row["library_id"]) in x.allowed_set(user) and x.visible(user, row) \
            and row["type"] in TYPES

    def item_by_guid(user, value):
        n = unguid(value, K_ITEM)
        if n is None:
            return None
        row = db.one(x.ITEM_SQL + "WHERE i.id=?", (n,))
        return row if visible_row(user, row) else None

    jf.item_by_guid = item_by_guid
    jf.visible_row = visible_row

    # ------------------------------------------------------------ vues (bibliothèques)
    def lib_dto(lib, user):
        n = db.scalar("SELECT COUNT(*) FROM items WHERE library_id=? AND type IN ('movie','show')", (int(lib["id"]),))
        first = db.one("SELECT id, img_version FROM items WHERE library_id=? AND type IN ('movie','show') "
                       "ORDER BY added_at DESC LIMIT 1", (int(lib["id"]),))
        d = {"Name": lib["title"], "ServerId": jf.server_id(), "Id": guid(K_LIB, lib["id"]),
             "Etag": f"{lib['id']}", "DateCreated": None, "CanDelete": False, "CanDownload": False,
             "SortName": lib["title"], "ExternalUrls": [], "Path": None, "Taglines": [], "Genres": [],
             "PlayAccess": "Full", "RemoteTrailers": [], "ProviderIds": {}, "IsFolder": True,
             "ParentId": guid(K_ROOT, 0), "Type": "CollectionFolder", "People": [], "Studios": [],
             "GenreItems": [], "LocalTrailerCount": 0, "ChildCount": n, "SpecialFeatureCount": 0,
             "DisplayPreferencesId": guid(K_LIB, lib["id"]), "Tags": [], "PrimaryImageAspectRatio": 16 / 9,
             "CollectionType": "movies" if lib["type"] == "movie" else "tvshows", "ImageTags": {},
             "BackdropImageTags": [], "ImageBlurHashes": {}, "LocationType": "FileSystem", "MediaType": "Unknown",
             "LockedFields": [], "LockData": False,
             "UserData": {"PlaybackPositionTicks": 0, "PlayCount": 0, "IsFavorite": False, "Played": False,
                          "Key": f"lib{lib['id']}", "ItemId": guid(K_LIB, lib["id"])}}
        if first:  # vignette de la bibliothèque : l'image de fond du dernier ajout
            d["ImageTags"]["Primary"] = jf.image_tag(first["id"], "libart", first["img_version"])
        return d

    jf.lib_dto = lib_dto

    def views(req, user):
        hidden = set(x.get_prefs(user["id"]).get("hidden_libraries") or [])
        items = [lib_dto(l, user) for l in libs_for(user) if l["id"] not in hidden]
        return result(items)

    @route("GET", f"/Users/{U}/Views")
    def jf_views(req):
        return views(req, jf.target_user(req, req.params["uid"]))

    @route("GET", "/UserViews")
    def jf_user_views(req):
        return views(req, jf.query_user(req))

    @route("GET", "/UserViews/GroupingOptions")
    def jf_grouping(req):
        jf.query_user(req)
        return []

    def root_dto(user):
        return {"Name": "Media Folders", "ServerId": jf.server_id(), "Id": guid(K_ROOT, 0), "IsFolder": True,
                "Type": "UserRootFolder", "ImageTags": {}, "BackdropImageTags": [], "LocationType": "FileSystem",
                "MediaType": "Unknown", "ChildCount": len(libs_for(user)),
                "UserData": {"PlaybackPositionTicks": 0, "PlayCount": 0, "IsFavorite": False, "Played": False,
                             "Key": "root"}}

    @route("GET", f"/Users/{U}/Items/Root")
    def jf_root_legacy(req):
        return root_dto(jf.target_user(req, req.params["uid"]))

    @route("GET", "/Items/Root")
    def jf_root(req):
        return root_dto(jf.query_user(req))

    # ------------------------------------------------------------ requête d'éléments
    def query_items(req, user, forced=None):
        """GET /Items : filtres, tris et pagination de Jellyfin traduits en SQL."""
        uid = int(user["id"])
        forced = forced or {}
        b = builder(req, user)
        ids = jf.qlist(req, "Ids")
        if ids:
            rows = [item_by_guid(user, v) for v in ids[:500]]
            rows = [r for r in rows if r]
            return result(b.many(rows))
        start = jf.qint(req, "StartIndex", 0, 0)
        limit = jf.qint(req, "Limit", 0, 0, 5000)
        types = {t.lower() for t in jf.qlist(req, "IncludeItemTypes")}
        excluded = {t.lower() for t in jf.qlist(req, "ExcludeItemTypes")}
        media_types = {t.lower() for t in jf.qlist(req, "MediaTypes")}
        parent = forced.get("parent") or jf.q(req, "ParentId")
        recursive = jf.qbool(req, "Recursive", False)
        parent_type = None
        where, args = [], []
        allowed = sorted(int(a) for a in x.allowed_set(user))
        if not allowed:
            return result([])
        where.append(f"i.library_id IN ({','.join('?' * len(allowed))})")
        args += allowed
        kinds = None
        if types:
            kinds = {KINDS[t] for t in types if t in KINDS}
            if not kinds:
                return result([])
        if parent:
            pk = kind_of(parent)
            if pk == K_LIB:
                lib = unguid(parent, K_LIB)
                where.append("i.library_id=?")
                args.append(lib)
                if not recursive and kinds is None:
                    kinds = {"movie", "show"}
            elif pk == K_ITEM:
                pid = unguid(parent, K_ITEM)
                prow = item_by_guid(user, parent)
                if not prow:
                    return result([])
                parent_type = prow["type"]
                if recursive and prow["type"] == "show":
                    where.append("(i.show_id=?)")
                    args.append(pid)
                else:
                    where.append("i.parent_id=?")
                    args.append(pid)
                    if kinds is None and prow["type"] in ("movie", "show"):
                        kinds = {"season"} if prow["type"] == "show" else set()
                        if not kinds:
                            return result([])
            elif pk == K_ROOT:
                if not recursive and kinds is None:
                    return result([lib_dto(l, user) for l in libs_for(user)])
            else:
                return result([])
        elif kinds is None:
            if not recursive:
                return result([lib_dto(l, user) for l in libs_for(user)])
            kinds = {"movie", "show", "season", "episode"}
        if kinds is None:
            kinds = {"movie", "show", "season", "episode"}
        kinds -= {KINDS[t] for t in excluded if t in KINDS}
        if media_types and "video" not in media_types:
            return result([])
        if media_types:
            kinds &= {"movie", "episode", "extra"}
        if not jf.qbool(req, "IsFolder", True):
            kinds &= {"movie", "episode", "extra"}
        if jf.qbool(req, "IsFolder", False):
            kinds &= {"show", "season"}
        if "extra" in kinds and not parent:
            kinds.discard("extra")
        if not kinds:
            return result([])
        where.append(f"i.type IN ({','.join('?' * len(kinds))})")
        args += sorted(kinds)
        term = jf.q(req, "SearchTerm")
        if term:
            t = fold(term.strip())[:100]
            if len(t) < 1:
                return result([])
            where.append("i.search LIKE ?" + LIKE_ESCAPE)
            args.append(like(t))
        nsw = jf.q(req, "NameStartsWith")
        if nsw:
            if nsw.startswith("#"):
                where.append("substr(i.sort_title,1,1) NOT GLOB '[A-Za-z]'")
            else:
                where.append("i.sort_title LIKE ?" + LIKE_ESCAPE)
                args.append(like(nsw[:50])[1:])
        nlt = jf.q(req, "NameLessThan")
        if nlt:
            where.append("i.sort_title < ? COLLATE NOCASE")
            args.append(nlt[:50])
        nsg = jf.q(req, "NameStartsWithOrGreater")
        if nsg:
            where.append("i.sort_title >= ? COLLATE NOCASE")
            args.append(nsg[:50])
        genres = jf.qlist(req, "Genres", "|")
        for gid in jf.qlist(req, "GenreIds", "|") + jf.qlist(req, "GenreIds"):
            n = jf.name_of(gid, K_GENRE)
            if n:
                genres.append(n)
        if genres:
            where.append(f"EXISTS (SELECT 1 FROM json_each(i.genres) g WHERE g.value IN ({','.join('?' * len(genres))}))")
            args += genres[:50]
        persons = [jf.name_of(p, K_PERSON) for p in jf.qlist(req, "PersonIds") + jf.qlist(req, "PersonIds", "|")]
        persons = [p for p in persons if p]
        if jf.qlist(req, "PersonIds") and not persons:
            return result([])
        for p in persons[:5]:
            where.append("(EXISTS (SELECT 1 FROM json_each(i.people,'$.cast') c WHERE json_extract(c.value,'$.name')=?) "
                         "OR EXISTS (SELECT 1 FROM json_each(i.people,'$.directors') d WHERE "
                         "(CASE WHEN d.type='object' THEN json_extract(d.value,'$.name') ELSE d.value END)=?))")
            args += [p, p]
        studios = [jf.name_of(s, K_STUDIO) for s in jf.qlist(req, "StudioIds", "|") + jf.qlist(req, "StudioIds")]
        studios = [s for s in studios if s] + jf.qlist(req, "Studios", "|")
        if studios:
            where.append(f"i.studio IN ({','.join('?' * len(studios))})")
            args += studios[:20]
        years = [int(y) for y in jf.qlist(req, "Years") if y.isdigit()]
        if years:
            where.append(f"i.year IN ({','.join('?' * len(years))})")
            args += years[:100]
        ratings = jf.qlist(req, "OfficialRatings", "|")
        if ratings:
            where.append(f"i.content_rating IN ({','.join('?' * len(ratings))})")
            args += ratings[:30]
        mcr = jf.q(req, "MinCommunityRating")
        if mcr:
            try:
                where.append("i.rating >= ?")
                args.append(float(mcr))
            except ValueError:
                pass
        if jf.qbool(req, "HasTmdbId"):
            where.append("i.tmdb_id IS NOT NULL")
        idx = jf.q(req, "IndexNumber")
        if idx and idx.lstrip("-").isdigit():
            where.append("i.idx=?")
            args.append(int(idx))
        pidx = jf.q(req, "ParentIndexNumber")
        if pidx and pidx.lstrip("-").isdigit():
            where.append("p.idx=?")
            args.append(int(pidx))
        # état de lecture de l'utilisateur
        filters = {f.lower() for f in jf.qlist(req, "Filters")}
        played = (f"(CASE WHEN i.type IN ('movie','episode','extra') THEN COALESCE(pr.watched,0) "
                  f"WHEN i.type='show' THEN (leaves>0 AND (SELECT COUNT(*) FROM progress q WHERE q.user_id={uid} "
                  f"AND q.show_key=CAST(i.id AS TEXT) AND q.watched=1) >= leaves) "
                  f"ELSE (leaves>0 AND (SELECT COUNT(*) FROM progress q WHERE q.user_id={uid} "
                  f"AND q.season_key=CAST(i.id AS TEXT) AND q.watched=1) >= leaves) END)")
        favorite = f"EXISTS (SELECT 1 FROM watchlist w WHERE w.user_id={uid} AND w.item_id=i.id)"
        is_played = jf.qbool(req, "IsPlayed")
        if "isplayed" in filters or is_played is True:
            where.append(played + "=1")
        if "isunplayed" in filters or is_played is False:
            where.append(played + "=0")
        if "isfavorite" in filters or jf.qbool(req, "IsFavorite") is True or "isfavoriteorlikes" in filters:
            where.append(favorite)
        if jf.qbool(req, "IsFavorite") is False:
            where.append("NOT " + favorite)
        if "isresumable" in filters:
            where.append(f"COALESCE(pr.position_ms,0) >= {MIN_RESUME_MS} AND COALESCE(pr.watched,0)=0")
        age_sql, age_args = x.age_filter(user)
        sql = (x.ITEM_SQL + f"LEFT JOIN progress pr ON pr.user_id={uid} AND pr.rating_key=CAST(i.id AS TEXT) "
               "WHERE " + " AND ".join(where) + age_sql)
        allargs = args + age_args
        total = None
        if jf.qbool(req, "EnableTotalRecordCount", True):
            total = db.scalar("SELECT COUNT(*) FROM (" + sql + ")", allargs)
        sort_by = [s.lower() for s in jf.qlist(req, "SortBy")] or ["sortname"]
        orders = [o.lower() for o in jf.qlist(req, "SortOrder")]
        parts = []
        for n, s in enumerate(sort_by[:4]):
            col = SORTS.get(s)
            if not col:
                continue
            desc = (orders[n] if n < len(orders) else (orders[0] if orders else "ascending")).startswith("desc")
            parts.append(f"{col} {'DESC' if desc else 'ASC'}")
        if (kinds == {"episode"} or parent_type in ("show", "season")) and sort_by == ["sortname"]:
            parts = ["p.idx=0", "p.idx", "i.idx"]  # épisodes, saisons : ordre de diffusion
        parts.append("i.id")
        sql += " ORDER BY " + ", ".join(parts)
        if limit:
            sql += f" LIMIT {limit} OFFSET {start}"
        elif start:
            sql += f" LIMIT -1 OFFSET {start}"
        rows = db.all(sql, allargs)
        return result(b.many(rows), start, total if total is not None else start + len(rows))

    jf.query_items = query_items

    @route("GET", "/Items")
    def jf_items(req):
        return query_items(req, jf.query_user(req))

    @route("GET", f"/Users/{U}/Items")
    def jf_user_items(req):
        return query_items(req, jf.target_user(req, req.params["uid"]))

    # ------------------------------------------------------------ un élément
    def other_dto(user, value):
        """Personne, genre ou studio (pages de navigation des clients)."""
        k = kind_of(value)
        if k == K_LIB:
            lib = next((l for l in libs_for(user) if guid(K_LIB, l["id"]) == value.replace("-", "").lower()), None)
            return lib_dto(lib, user) if lib else None
        if k == K_ROOT:
            return root_dto(user)
        kinds = {K_PERSON: "Person", K_GENRE: "Genre", K_STUDIO: "Studio"}
        if k not in kinds:
            return None
        name = jf.name_of(value, k)
        if not name:
            return None
        return {"Name": name, "ServerId": jf.server_id(), "Id": value.replace("-", "").lower(), "Type": kinds[k],
                "IsFolder": False, "ImageTags": {}, "BackdropImageTags": [], "LocationType": "Virtual",
                "MediaType": "Unknown", "Overview": "", "ProviderIds": {}, "Genres": [], "GenreItems": [],
                "UserData": {"PlaybackPositionTicks": 0, "PlayCount": 0, "IsFavorite": False, "Played": False,
                             "Key": name}}

    def single(req, user, iid):
        row = item_by_guid(user, iid)
        if not row:
            other = other_dto(user, iid)
            if other:
                return other
            raise HTTPError(404, "Item not found")
        b = builder(req, user, ["_detail"])
        d = b.one(row)
        if row["type"] in ("movie", "show"):
            extras = db.all("SELECT extra_kind FROM items WHERE parent_id=? AND type='extra'", (row["id"],))
            d["LocalTrailerCount"] = sum(1 for e in extras if e["extra_kind"] == "trailer")
            d["SpecialFeatureCount"] = sum(1 for e in extras if e["extra_kind"] != "trailer")
        d["RemoteTrailers"] = []
        if row.get("trailer"):
            d["RemoteTrailers"] = [{"Url": f"https://www.youtube.com/watch?v={row['trailer']}", "Name": "Trailer"}] \
                if str(row["trailer"]).replace("-", "").replace("_", "").isalnum() else []
        return d

    jf.single = single

    @route("GET", f"/Items/{I}")
    def jf_item(req):
        return single(req, jf.query_user(req), req.params["iid"])

    @route("GET", f"/Users/{U}/Items/{I}")
    def jf_user_item(req):
        return single(req, jf.target_user(req, req.params["uid"]), req.params["iid"])

    @route("GET", f"/Items/{I}/Ancestors")
    def jf_ancestors(req):
        user = jf.query_user(req)
        row = item_by_guid(user, req.params["iid"])
        if not row:
            raise HTTPError(404, "Item not found")
        out = []
        b = builder(req, user)
        if row["type"] == "episode":
            out += b.many([r for r in (db.one(x.ITEM_SQL + "WHERE i.id=?", (row["parent_id"],)),
                                       db.one(x.ITEM_SQL + "WHERE i.id=?", (row["show_id"],))) if r])
        elif row["type"] == "season":
            show = db.one(x.ITEM_SQL + "WHERE i.id=?", (row["show_id"],))
            out += b.many([show] if show else [])
        lib = next((l for l in libs_for(user) if int(l["id"]) == row["library_id"]), None)
        if lib:
            out.append(lib_dto(lib, user))
        out.append(root_dto(user))
        return out

    def children_of(req, user, kind):
        row = item_by_guid(user, req.params["iid"])
        if not row:
            raise HTTPError(404, "Item not found")
        rows = db.all(x.ITEM_SQL + "WHERE i.parent_id=? AND i.type='extra' ORDER BY i.sort_title", (row["id"],))
        rows = [r for r in rows if (r["extra_kind"] == "trailer") == (kind == "trailer")]
        return builder(req, user, ["MediaSources"]).many(rows)

    @route("GET", f"/Items/{I}/LocalTrailers")
    def jf_trailers(req):
        return children_of(req, jf.query_user(req), "trailer")

    @route("GET", f"/Users/{U}/Items/{I}/LocalTrailers")
    def jf_trailers_legacy(req):
        return children_of(req, jf.target_user(req, req.params["uid"]), "trailer")

    @route("GET", f"/Items/{I}/SpecialFeatures")
    def jf_special(req):
        return children_of(req, jf.query_user(req), "special")

    @route("GET", f"/Users/{U}/Items/{I}/SpecialFeatures")
    def jf_special_legacy(req):
        return children_of(req, jf.target_user(req, req.params["uid"]), "special")

    @route("GET", f"/(?:Users/{U}/)?Items/{I}/Intros")
    def jf_intros(req):
        jf.token_user(req)
        return result([])

    @route("GET", f"/Items/{I}/Similar")
    def jf_similar(req):
        user = jf.query_user(req)
        row = item_by_guid(user, req.params["iid"])
        if not row or row["type"] not in ("movie", "show"):
            return result([])
        genres = [g for g in json.loads(row["genres"] or "[]") if isinstance(g, str)][:3]
        if not genres:
            return result([])
        limit = jf.qint(req, "Limit", 12, 1, 50)
        allowed = sorted(int(a) for a in x.allowed_set(user))
        af, aa = x.age_filter(user)
        rows = db.all(x.ITEM_SQL + f"WHERE i.type=? AND i.id<>? AND i.library_id IN ({','.join('?' * len(allowed))}) "
                      f"AND EXISTS (SELECT 1 FROM json_each(i.genres) g WHERE g.value IN ({','.join('?' * len(genres))}))"
                      + af + " ORDER BY COALESCE(i.rating,0) DESC LIMIT ?",
                      [row["type"], row["id"], *allowed, *genres, *aa, limit * 3])
        random.shuffle(rows)
        return result(builder(req, user).many(rows[:limit]))

    for path in ("ThemeSongs", "ThemeVideos"):
        app.compat_route("GET", f"/Items/{I}/{path}")(
            lambda req: (jf.token_user(req), {"Items": [], "TotalRecordCount": 0, "StartIndex": 0,
                                              "OwnerId": req.params["iid"]})[1])

    @route("GET", f"/Items/{I}/ThemeMedia")
    def jf_theme_media(req):
        jf.token_user(req)
        empty = {"Items": [], "TotalRecordCount": 0, "StartIndex": 0, "OwnerId": req.params["iid"]}
        return {"ThemeVideosResult": empty, "ThemeSongsResult": empty, "SoundtrackSongsResult": empty}

    @route("GET", f"/Items/{I}/CriticReviews")
    def jf_reviews(req):
        jf.token_user(req)
        return result([])

    @route("GET", f"/Items/{I}/InstantMix")
    def jf_mix(req):
        jf.token_user(req)
        return result([])

    @route("GET", f"/Items/{I}/ExternalIdInfos")
    def jf_ext_ids(req):
        jf.token_user(req)
        return []

    @route("GET", f"/Videos/{I}/AdditionalParts")
    def jf_parts(req):
        jf.token_user(req)
        return result([])

    # ------------------------------------------------------------ dernier ajouts, reprises, à suivre
    def latest(req, user):
        limit = jf.qint(req, "Limit", 16, 1, 100)
        parent = jf.q(req, "ParentId")
        allowed = x.allowed_set(user)
        libs = [int(a) for a in allowed]
        if parent:
            lib = unguid(parent, K_LIB)
            if lib is None or str(lib) not in allowed:
                return []
            libs = [lib]
        if not libs:
            return []
        types = {KINDS.get(t.lower()) for t in jf.qlist(req, "IncludeItemTypes")} - {None}
        af, aa = x.age_filter(user)
        marks = ",".join("?" * len(libs))
        out = []
        b = builder(req, user)
        hide_played = jf.qbool(req, "IsPlayed") is not True  # comme Jellyfin : les films vus sont masqués
        uid = int(user["id"])
        if not types or "movie" in types:
            rows = db.all(x.ITEM_SQL + f"WHERE i.library_id IN ({marks}) AND i.type='movie'" + af +
                          (f" AND NOT EXISTS (SELECT 1 FROM progress q WHERE q.user_id={uid} AND "
                           "q.rating_key=CAST(i.id AS TEXT) AND q.watched=1)" if hide_played else "") +
                          " ORDER BY i.added_at DESC, i.id DESC LIMIT ?", libs + aa + [limit])
            out += rows
        if not types or types & {"episode", "show"}:
            latest_eps = db.all(f"SELECT show_id, MAX(added_at) AS t FROM items WHERE library_id IN ({marks}) "
                                "AND type='episode' GROUP BY show_id ORDER BY t DESC LIMIT ?", libs + [limit])
            for le in latest_eps:
                show = db.one(x.ITEM_SQL + "WHERE i.id=?", (le["show_id"],))
                if show and x.visible(user, show):
                    show["_t"] = le["t"]
                    out.append(show)
        out.sort(key=lambda r: r.get("_t") or r["added_at"], reverse=True)
        return b.many(out[:limit])

    @route("GET", "/Items/Latest")
    def jf_latest(req):
        return latest(req, jf.query_user(req))

    @route("GET", f"/Users/{U}/Items/Latest")
    def jf_latest_legacy(req):
        return latest(req, jf.target_user(req, req.params["uid"]))

    def resume(req, user):
        uid = user["id"]
        limit = jf.qint(req, "Limit", 12, 1, 100)
        start = jf.qint(req, "StartIndex", 0, 0)
        media = {t.lower() for t in jf.qlist(req, "MediaTypes")}
        if media and "video" not in media:
            return result([])
        types = {KINDS.get(t.lower()) for t in jf.qlist(req, "IncludeItemTypes")} - {None}
        parent = jf.q(req, "ParentId")
        lib = unguid(parent, K_LIB) if parent else None
        rows = db.all("SELECT rating_key FROM progress WHERE user_id=? AND position_ms>=? AND watched=0 "
                      "AND (duration_ms=0 OR position_ms < duration_ms*?) ORDER BY updated_at DESC LIMIT 200",
                      (uid, MIN_RESUME_MS, WATCHED_RATIO))
        items = []
        for r in rows:
            if not r["rating_key"].isdigit():
                continue
            row = db.one(x.ITEM_SQL + "WHERE i.id=?", (int(r["rating_key"]),))
            if not visible_row(user, row) or row["type"] not in PLAYABLE:
                continue
            if types and row["type"] not in types:
                continue
            if lib is not None and row["library_id"] != lib:
                continue
            items.append(row)
        page = items[start:start + limit]
        return result(builder(req, user).many(page), start, len(items))

    @route("GET", "/UserItems/Resume")
    def jf_resume(req):
        return resume(req, jf.query_user(req))

    @route("GET", f"/Users/{U}/Items/Resume")
    def jf_resume_legacy(req):
        return resume(req, jf.target_user(req, req.params["uid"]))

    def next_up_rows(user, series=None, limit=24, rewatching=False):
        uid = user["id"]
        if series is not None:
            shows = [series]
        else:
            shows = [int(r["show_key"]) for r in db.all(
                "SELECT show_key, MAX(last_watched_at) AS t FROM progress WHERE user_id=? AND watched=1 "
                "AND show_key IS NOT NULL GROUP BY show_key ORDER BY t DESC LIMIT 60", (uid,)) if r["show_key"].isdigit()]
        out = []
        for sid in shows:
            show = db.one(x.ITEM_SQL + "WHERE i.id=?", (sid,))
            if not visible_row(user, show):
                continue
            eps = x.leaves(sid)
            watched = {w["rating_key"]: w["last_watched_at"] or 0 for w in db.all(
                "SELECT rating_key, last_watched_at FROM progress WHERE user_id=? AND show_key=? AND watched=1",
                (uid, str(sid)))}
            last_i, best = None, -1
            for i, ep in enumerate(eps):
                wt = watched.get(str(ep["id"]))
                if wt is not None and wt >= best:
                    best, last_i = wt, i
            nxt = None
            for ep in eps[(last_i + 1) if last_i is not None else 0:]:
                if str(ep["id"]) not in watched and (ep.get("idx") or 0) >= 0:
                    if eps and ep is eps[0] and last_i is None and series is None:
                        break
                    nxt = ep
                    break
            if nxt is None and series is not None and last_i is None and eps:
                nxt = eps[0]
            if nxt and x.visible(user, nxt):
                out.append(nxt)
            if len(out) >= limit:
                break
        return out

    def next_up(req, user):
        limit = jf.qint(req, "Limit", 24, 1, 100)
        series = jf.q(req, "SeriesId")
        sid = None
        if series:
            row = item_by_guid(user, series)
            if not row or row["type"] != "show":
                return result([])
            sid = row["id"]
        parent = jf.q(req, "ParentId")
        rows = next_up_rows(user, sid, limit)
        if parent and unguid(parent, K_LIB) is not None:
            lib = unguid(parent, K_LIB)
            rows = [r for r in rows if r["library_id"] == lib]
        if jf.qbool(req, "EnableResumable", True) is False:
            ud = Builder(jf, user).user_data_many(rows)
            rows = [r for r in rows if not ud[str(r["id"])]["PlaybackPositionTicks"]]
        return result(builder(req, user).many(rows))

    @route("GET", "/Shows/NextUp")
    def jf_next_up(req):
        return next_up(req, jf.query_user(req))

    @route("GET", "/Shows/Upcoming")
    def jf_upcoming(req):
        jf.query_user(req)
        return result([])

    # ------------------------------------------------------------ séries
    def show_of(req, user):
        row = item_by_guid(user, req.params["sid"])
        if not row or row["type"] != "show":
            raise HTTPError(404, "Series not found")
        return row

    @route("GET", rf"/Shows/(?P<sid>{GUID_RE})/Seasons")
    def jf_seasons(req):
        user = jf.query_user(req)
        show = show_of(req, user)
        rows = db.all(x.ITEM_SQL + "WHERE i.parent_id=? AND i.type='season' ORDER BY (i.idx=0), i.idx", (show["id"],))
        return result(builder(req, user).many(rows))

    @route("GET", rf"/Shows/(?P<sid>{GUID_RE})/Episodes")
    def jf_episodes(req):
        user = jf.query_user(req)
        show = show_of(req, user)
        sql, args = x.ITEM_SQL + "WHERE i.show_id=? AND i.type='episode'", [show["id"]]
        season = jf.q(req, "SeasonId")
        if season:
            n = unguid(season, K_ITEM)
            if n is None:
                return result([])
            sql += " AND i.parent_id=?"
            args.append(n)
        elif (jf.q(req, "Season") or "").lstrip("-").isdigit():
            sql += " AND p.idx=?"
            args.append(int(jf.q(req, "Season")))
        rows = db.all(sql + " " + x.EPISODE_ORDER, args)
        rows = [r for r in rows if x.visible(user, r)]
        adjacent = jf.q(req, "AdjacentTo")
        if adjacent:
            n = unguid(adjacent, K_ITEM)
            pos = next((i for i, r in enumerate(rows) if r["id"] == n), None)
            rows = rows[max(0, pos - 1):pos + 2] if pos is not None else []
        start_item = jf.q(req, "StartItemId")
        if start_item:
            n = unguid(start_item, K_ITEM)
            pos = next((i for i, r in enumerate(rows) if r["id"] == n), None)
            if pos is not None:
                rows = rows[pos:]
        if jf.qbool(req, "IsMissing") is True:
            rows = []
        start = jf.qint(req, "StartIndex", 0, 0)
        limit = jf.qint(req, "Limit", 0, 0, 5000)
        page = rows[start:start + limit] if limit else rows[start:]
        return result(builder(req, user).many(page), start, len(rows))

    # ------------------------------------------------------------ genres, personnes, studios, filtres
    def genre_rows(req, user):
        allowed = sorted(int(a) for a in x.allowed_set(user))
        parent = jf.q(req, "ParentId")
        lib = unguid(parent, K_LIB) if parent else None
        if lib is not None:
            if lib not in allowed:
                return []
            allowed = [lib]
        if not allowed:
            return []
        types = {KINDS.get(t.lower()) for t in jf.qlist(req, "IncludeItemTypes")} - {None}
        types = (types & {"movie", "show"}) or {"movie", "show"}
        af, aa = x.age_filter(user)
        return db.all("SELECT g.value AS name, COUNT(*) AS n FROM items i LEFT JOIN items sh ON sh.id=i.show_id, "
                      "json_each(i.genres) g WHERE i.library_id IN (" + ",".join("?" * len(allowed)) + ") "
                      f"AND i.type IN ({','.join('?' * len(types))})" + af + " GROUP BY g.value ORDER BY g.value",
                      [*allowed, *sorted(types), *aa])

    @route("GET", "/Genres")
    def jf_genres(req):
        user = jf.query_user(req)
        out = []
        for r in genre_rows(req, user):
            if not isinstance(r["name"], str):
                continue
            g = jf.remember(K_GENRE, r["name"])
            out.append({"Name": r["name"], "ServerId": jf.server_id(), "Id": g, "Type": "Genre", "IsFolder": False,
                        "ImageTags": {}, "BackdropImageTags": [], "LocationType": "Virtual", "MediaType": "Unknown",
                        "ChildCount": r["n"], "UserData": {"PlaybackPositionTicks": 0, "PlayCount": 0,
                                                           "IsFavorite": False, "Played": False, "Key": r["name"]}})
        return result(out)

    @route("GET", rf"/Genres/(?P<name>[^/]{{1,200}})")
    def jf_genre(req):
        user = jf.query_user(req)
        name = req.params["name"]
        value = jf.name_of(name, K_GENRE) if kind_of(name) == K_GENRE else name
        d = other_dto(user, jf.remember(K_GENRE, value)) if value else None
        if not d:
            raise HTTPError(404, "Genre not found")
        return d

    for path in ("/Studios", "/Persons", "/Artists", "/Artists/AlbumArtists", "/MusicGenres", "/Playlists",
                 "/Collections", "/Trailers", "/Channels"):
        app.compat_route("GET", path)(lambda req: (jf.token_user(req), result([]))[1])

    @route("GET", "/Items/Filters")
    def jf_filters(req):
        user = jf.query_user(req)
        names = [r["name"] for r in genre_rows(req, user) if isinstance(r["name"], str)]
        allowed = sorted(int(a) for a in x.allowed_set(user)) or [-1]
        marks = ",".join("?" * len(allowed))
        af, aa = x.age_filter(user)
        years = [r["year"] for r in db.all(
            f"SELECT DISTINCT i.year FROM items i LEFT JOIN items sh ON sh.id=i.show_id WHERE i.year IS NOT NULL "
            f"AND i.type IN ('movie','show') AND i.library_id IN ({marks})" + af + " ORDER BY i.year DESC",
            [*allowed, *aa])]
        ratings = [r["content_rating"] for r in db.all(
            f"SELECT DISTINCT i.content_rating FROM items i LEFT JOIN items sh ON sh.id=i.show_id WHERE "
            f"i.content_rating IS NOT NULL AND i.library_id IN ({marks})" + af + " ORDER BY i.content_rating",
            [*allowed, *aa])]
        return {"Genres": names, "Tags": [], "OfficialRatings": ratings, "Years": years}

    @route("GET", "/Items/Filters2")
    def jf_filters2(req):
        user = jf.query_user(req)
        return {"Genres": [{"Name": r["name"], "Id": jf.remember(K_GENRE, r["name"])}
                           for r in genre_rows(req, user) if isinstance(r["name"], str)], "Tags": []}

    @route("GET", "/Items/Counts")
    def jf_counts(req):
        user = jf.query_user(req)
        allowed = sorted(int(a) for a in x.allowed_set(user)) or [-1]
        counts = {r["type"]: r["n"] for r in db.all(
            f"SELECT type, COUNT(*) AS n FROM items WHERE library_id IN ({','.join('?' * len(allowed))}) GROUP BY type",
            allowed)}
        return {"MovieCount": counts.get("movie", 0), "SeriesCount": counts.get("show", 0),
                "EpisodeCount": counts.get("episode", 0), "ArtistCount": 0, "ProgramCount": 0, "TrailerCount": 0,
                "SongCount": 0, "AlbumCount": 0, "MusicVideoCount": 0, "BoxSetCount": 0, "BookCount": 0,
                "ItemCount": counts.get("movie", 0) + counts.get("show", 0) + counts.get("episode", 0)}

    @route("GET", "/Search/Hints")
    def jf_search_hints(req):
        user = jf.query_user(req)
        term = fold((jf.q(req, "SearchTerm") or "").strip())[:100]
        if not term:
            return {"SearchHints": [], "TotalRecordCount": 0}
        allowed = sorted(int(a) for a in x.allowed_set(user))
        if not allowed:
            return {"SearchHints": [], "TotalRecordCount": 0}
        types = {KINDS.get(t.lower()) for t in jf.qlist(req, "IncludeItemTypes")} - {None}
        types = (types & {"movie", "show", "episode"}) or {"movie", "show", "episode"}
        limit = jf.qint(req, "Limit", 20, 1, 100)
        af, aa = x.age_filter(user)
        rows = db.all(x.ITEM_SQL + f"WHERE i.library_id IN ({','.join('?' * len(allowed))}) "
                      f"AND i.type IN ({','.join('?' * len(types))}) AND i.search LIKE ?" + LIKE_ESCAPE + af +
                      " ORDER BY (i.search LIKE ?" + LIKE_ESCAPE + ") DESC, i.sort_title LIMIT ?",
                      [*allowed, *sorted(types), like(term), *aa, like(term)[1:], limit])
        hints = []
        b = Builder(jf, user)
        for r in rows:
            d = b.item(r)
            hints.append({"ItemId": d["Id"], "Id": d["Id"], "Name": d["Name"], "MatchedTerm": term,
                          "IndexNumber": d.get("IndexNumber"), "ProductionYear": d.get("ProductionYear"),
                          "ParentIndexNumber": d.get("ParentIndexNumber"), "PrimaryImageTag": d["ImageTags"]["Primary"],
                          "Type": d["Type"], "IsFolder": d["IsFolder"], "RunTimeTicks": d.get("RunTimeTicks"),
                          "MediaType": d["MediaType"], "Series": d.get("SeriesName"), "Artists": [],
                          "ChannelId": None, "PrimaryImageAspectRatio": d["PrimaryImageAspectRatio"]})
        return {"SearchHints": hints, "TotalRecordCount": len(hints)}

    @route("GET", "/Movies/Recommendations")
    def jf_recommendations(req):
        jf.query_user(req)
        return []

    # ------------------------------------------------------------ état de lecture : vu, favori
    def user_data(user, row):
        b = Builder(jf, user)
        return b.user_data_many([row])[str(row["id"])]

    def playable_targets(row):
        if row["type"] == "show":
            return x.leaves(row["id"])
        if row["type"] == "season":
            return db.all(x.ITEM_SQL + "WHERE i.parent_id=? AND i.type='episode' " + x.EPISODE_ORDER, (row["id"],))
        return [row] if row["type"] in PLAYABLE else []

    def mark_played(user, row, played):
        with db.tx():
            for t in playable_targets(row):
                if x.visible(user, t):
                    x.set_watched(user["id"], x.card(t), played)

    def set_favorite(user, row, on):
        if row["type"] not in ("movie", "show"):
            return
        if on:
            db.run("INSERT OR IGNORE INTO watchlist(user_id,item_id,added_at) VALUES(?,?,?)",
                   (user["id"], row["id"], int(time.time())))
        else:
            db.run("DELETE FROM watchlist WHERE user_id=? AND item_id=?", (user["id"], row["id"]))

    def item_for(req, user):
        row = item_by_guid(user, req.params["iid"])
        if not row:
            raise HTTPError(404, "Item not found")
        return row

    def played_route(user_fn):
        def handler(req):
            user = user_fn(req)
            row = item_for(req, user)
            mark_played(user, row, req.method == "POST")
            return user_data(user, db.one(x.ITEM_SQL + "WHERE i.id=?", (row["id"],)))
        return handler

    def favorite_route(user_fn):
        def handler(req):
            user = user_fn(req)
            row = item_for(req, user)
            set_favorite(user, row, req.method == "POST")
            return user_data(user, row)
        return handler

    legacy = lambda req: jf.target_user(req, req.params["uid"])  # noqa: E731
    for method in ("POST", "DELETE"):
        route(method, f"/UserPlayedItems/{I}")(played_route(jf.query_user))
        route(method, f"/Users/{U}/PlayedItems/{I}")(played_route(legacy))
        route(method, f"/UserFavoriteItems/{I}")(favorite_route(jf.query_user))
        route(method, f"/Users/{U}/FavoriteItems/{I}")(favorite_route(legacy))

    def rating_route(user_fn):
        def handler(req):
            user = user_fn(req)
            return user_data(user, item_for(req, user))
        return handler
    for method in ("POST", "DELETE"):
        route(method, f"/UserItems/{I}/Rating")(rating_route(jf.query_user))
        route(method, f"/Users/{U}/Items/{I}/Rating")(rating_route(legacy))

    @route("GET", f"/UserItems/{I}/UserData")
    def jf_user_data(req):
        user = jf.query_user(req)
        return user_data(user, item_for(req, user))

    @route("POST", f"/UserItems/{I}/UserData")
    def jf_user_data_set(req):
        user = jf.query_user(req)
        row = item_for(req, user)
        b = jf.body(req)
        if isinstance(b.get("Played"), bool):
            mark_played(user, row, b["Played"])
        if isinstance(b.get("IsFavorite"), bool):
            set_favorite(user, row, b["IsFavorite"])
        pos = b.get("PlaybackPositionTicks")
        if isinstance(pos, int) and pos >= 0 and row["type"] in PLAYABLE and not b.get("Played"):
            x.upsert_meta(user["id"], x.card(row))
            db.run("UPDATE progress SET position_ms=?, updated_at=? WHERE user_id=? AND rating_key=?",
                   (pos // TICKS, int(time.time()), user["id"], str(row["id"])))
        return user_data(user, db.one(x.ITEM_SQL + "WHERE i.id=?", (row["id"],)))

    # ------------------------------------------------------------ images
    SIZE = {"Primary": (300, 450), "Backdrop": (1280, 720), "Thumb": (640, 360), "Logo": None, "Banner": None}

    def box(req, kind, ratio):
        def val(name):
            try:
                v = int(float(jf.q(req, name) or 0))
            except ValueError:
                v = 0
            return max(0, v)
        w = val("fillWidth") or val("width") or val("maxWidth")
        h = val("fillHeight") or val("height") or val("maxHeight")
        if w and not h:
            h = round(w / ratio)
        elif h and not w:
            w = round(h * ratio)
        elif not w:
            w, h = SIZE.get(kind) or (300, 450)
            if ratio > 1 and kind == "Primary":
                w, h = 640, 360
        if w and h and abs(w / h - ratio) > 0.05 and not (jf.q(req, "fillWidth") and jf.q(req, "fillHeight")):
            h = round(w / ratio)
        clamp = lambda v: max(40, min(1920, round(v / 20) * 20))  # noqa: E731
        return clamp(w), clamp(h)

    def send(path):
        with open(path, "rb") as f:
            return Response(f.read(), 200, "image/jpeg", {"Cache-Control": "public, max-age=31536000, immutable"})

    def image(req):
        iid = req.params["iid"]
        kind = req.params["kind"].capitalize()
        k = kind_of(iid)
        tag = jf.q(req, "tag") or ""
        user = jf.token_user(req, required=False)
        if k == K_LIB:  # vignette d'une bibliothèque
            lib = unguid(iid, K_LIB)
            first = db.one("SELECT * FROM items WHERE library_id=? AND type IN ('movie','show') "
                           "ORDER BY added_at DESC LIMIT 1", (lib,))
            if not first or not (jf.tag_ok(first["id"], "libart", first["img_version"], tag) or
                                 (user and str(lib) in x.allowed_set(user))):
                raise HTTPError(404, "Image not found")
            db.release()
            w, h = box(req, "Thumb", 16 / 9)
            path = ctx.images.get(first, "art", w, h)
            if not path:
                raise HTTPError(404, "Image not found")
            return send(path)
        n = unguid(iid, K_ITEM)
        if n is None:
            raise HTTPError(404, "Image not found")
        row = db.one("SELECT * FROM items WHERE id=?", (n,))
        if not row:
            raise HTTPError(404, "Image not found")
        index = req.params.get("index")
        if kind == "Chapter":
            idx = int(index or 0)
            ok = jf.tag_ok(row["id"], f"chapter{idx}", row["img_version"], tag)
        else:
            src = "thumb" if kind == "Primary" else "art"
            ok = jf.tag_ok(row["id"], src, row["img_version"], tag)
        if not ok:
            # sans étiquette valide : il faut un jeton et le droit de voir l'élément
            full = db.one(x.ITEM_SQL + "WHERE i.id=?", (n,))
            if not user or not visible_row(user, full):
                raise HTTPError(401 if not user else 404, "Unauthorized")
        db.release()  # la fabrication de l'image peut prendre un moment (partage réseau)
        if kind == "Chapter":
            path = ctx.chapter_images.get(row, int(index or 0))
        elif kind in ("Primary", "Backdrop", "Thumb"):
            if kind == "Backdrop" and (index or "0") != "0":
                raise HTTPError(404, "Image not found")
            ratio = (16 / 9 if row["type"] in ("episode", "extra") else 2 / 3) if kind == "Primary" else 16 / 9
            w, h = box(req, kind, ratio)
            path = ctx.images.get(row, "thumb" if kind == "Primary" else "art", w, h)
        else:
            path = None
        if not path:
            raise HTTPError(404, "Image not found")
        return send(path)

    route("GET", rf"/Items/(?P<iid>{GUID_RE})/Images/(?P<kind>\w{{1,20}})(?:/(?P<index>\d{{1,4}}))?")(image)

    @route("GET", rf"/Items/(?P<iid>{GUID_RE})/Images")
    def jf_image_infos(req):
        user = jf.query_user(req)
        row = item_by_guid(user, req.params["iid"])
        if not row:
            raise HTTPError(404, "Item not found")
        v = row["img_version"]
        out = [{"ImageType": "Primary", "ImageTag": jf.image_tag(row["id"], "thumb", v), "Width": 0, "Height": 0}]
        if row["type"] in ("movie", "show"):
            out.append({"ImageType": "Backdrop", "ImageIndex": 0, "ImageTag": jf.image_tag(row["id"], "art", v),
                        "Width": 0, "Height": 0})
        return out

    @route("GET", rf"/Users/(?P<uid>{GUID_RE})/Images/(?P<kind>\w{{1,20}})")
    def jf_user_image(req):
        jf.require_enabled()
        raise HTTPError(404, "Image not found")

    route("GET", rf"/(?:Genres|Persons|Studios)/(?P<name>[^/]{{1,200}})/Images/(?P<kind>\w{{1,20}})")(jf_user_image)

