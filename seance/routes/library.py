"""Bibliothèques, accueil, recherche, fiches, images et historique."""
import json
import time

from .. import collections_api, i18n
from ..catalog import CARD_SQL, EPISODE_ORDER, IMG_PATH, LANG, LANG_NAMES, MIN_RESUME_MS, RK, SORTS, WATCHED_RATIO, stream_lists
from ..names import fold
from ..web import HTTPError, Response


def register(app, ctx, x):
    db = ctx.db
    # outils du catalogue partagé (seance/catalog.py)
    age_filter, allowed_set, attach_state, card = x.age_filter, x.allowed_set, x.attach_state, x.card
    chapters_for, get_prefs, leaves, libraries_all = x.chapters_for, x.get_prefs, x.leaves, x.libraries_all
    markers_for, next_episode, require_item, require_section = x.markers_for, x.next_episode, x.require_item, x.require_section
    set_watched, undo_watched, track_prefs, track_scope, visible = x.set_watched, x.undo_watched, x.track_prefs, x.track_scope, x.visible

    # -------------------------------------------------------------- library
    @app.route("GET", "/api/libraries")
    def libraries(req):
        allowed = allowed_set(req.user)
        return [l for l in libraries_all() if l["id"] in allowed]

    # ------------------------------------------------------------ filtres de bibliothèque
    HEIGHT = "CAST(json_extract({a}.probe, '$.video.height') AS INTEGER)"
    RES = {"4k": HEIGHT + " >= 1800", "1080p": HEIGHT + " BETWEEN 900 AND 1799", "720p": HEIGHT + " BETWEEN 650 AND 899",
           "sd": HEIGHT + " < 650"}
    DURATION = {"short": "i.duration_ms < 5400000", "medium": "i.duration_ms BETWEEN 5400000 AND 8999999",
                "long": "i.duration_ms >= 9000000"}

    def media_cond(kind, expr):
        """Condition sur le fichier : celui du film, ou au moins un épisode de la série."""
        if kind == "movie":
            return expr.format(a="i")
        return f"EXISTS (SELECT 1 FROM items e WHERE e.show_id=i.id AND e.type='episode' AND {expr.format(a='e')})"

    def library_filters(req, kind):
        """Filtres de la page d'une bibliothèque (paramètres de l'adresse) -> (SQL, arguments)."""
        sql, args = "", []
        uid = req.user["id"]
        genre = req.arg("genre")
        if genre:
            if len(genre) > 80:
                raise HTTPError(400, "Genre invalide")
            sql += " AND EXISTS (SELECT 1 FROM json_each(i.genres) g WHERE g.value=?)"
            args.append(genre)
        seen = ("EXISTS (SELECT 1 FROM progress pr WHERE pr.user_id=? AND pr.rating_key=CAST({a}.id AS TEXT) "
                "AND pr.watched=1)")
        watched = req.arg("watched")
        if watched in ("unwatched", "watched", "inprogress"):
            if kind == "movie":
                started = ("EXISTS (SELECT 1 FROM progress pr WHERE pr.user_id=? AND pr.rating_key=CAST(i.id AS TEXT) "
                           f"AND pr.watched=0 AND pr.position_ms>={MIN_RESUME_MS})")
                sql += {"unwatched": f" AND NOT {seen.format(a='i')}", "watched": f" AND {seen.format(a='i')}",
                        "inprogress": f" AND {started}"}[watched]
                args.append(uid)
            else:
                left = f"EXISTS (SELECT 1 FROM items e WHERE e.show_id=i.id AND e.type='episode' AND NOT {seen.format(a='e')})"
                done = f"EXISTS (SELECT 1 FROM items e WHERE e.show_id=i.id AND e.type='episode' AND {seen.format(a='e')})"
                sql += {"unwatched": f" AND NOT {done}", "watched": f" AND NOT {left}",
                        "inprogress": f" AND {done} AND {left}"}[watched]
                args += [uid] if watched != "inprogress" else [uid, uid]
        decade = req.int_arg("decade", 0, 0, 2100)
        if decade >= 1900:
            sql += " AND i.year BETWEEN ? AND ?"
            args += [decade - decade % 10, decade - decade % 10 + 9]
        if kind == "movie" and req.arg("duration") in DURATION:
            sql += " AND " + DURATION[req.arg("duration")]
        if req.arg("res") in RES:
            sql += " AND " + media_cond(kind, RES[req.arg("res")])
        if req.arg("hdr") == "1":
            sql += " AND " + media_cond(kind, "json_extract({a}.probe, '$.video.transfer') IN ('smpte2084', 'arib-std-b67')")
        for key, path in (("audio", "audio"), ("subs", "subs")):
            lang = req.arg(key)
            if lang:
                if not LANG.match(lang):
                    raise HTTPError(400, "Langue invalide")
                expr = (f"EXISTS (SELECT 1 FROM json_each(json_extract({{a}}.probe, '$.{path}')) t "
                        "WHERE json_extract(t.value, '$.lang') = ?)")
                if key == "subs":  # sous-titres externes aussi (Film.fr.srt)
                    expr = ("(" + expr + " OR EXISTS (SELECT 1 FROM json_each(json_extract({a}.probe, '$.external')) t "
                            "WHERE json_extract(t.value, '$.lang') = ?))")
                sql += " AND " + media_cond(kind, expr)
                args += [lang] * (2 if key == "subs" else 1)
        rating = req.int_arg("rating", 0, 0, 10)
        if rating:
            sql += " AND i.rating >= ?"
            args.append(rating)
        return sql, args

    facets_cache = {}

    @app.route("GET", r"/api/libraries/(?P<sid>\d{1,6})/facets")
    def library_facets(req):
        """Valeurs proposées par les filtres : décennies, langues audio et de sous-titres présentes."""
        sid = req.params["sid"]
        require_section(req.user, sid)
        lib = db.one("SELECT kind, scanned_at FROM libraries WHERE id=?", (int(sid),))
        key = (sid, lib["scanned_at"], db.scalar("SELECT COUNT(*) FROM items WHERE library_id=?", (int(sid),)))
        hit = facets_cache.get(sid)
        if hit and hit[0] == key:
            return hit[1]
        media_type = "movie" if lib["kind"] == "movie" else "episode"
        root = "movie" if lib["kind"] == "movie" else "show"
        decades = [r["d"] for r in db.all("SELECT DISTINCT (year / 10) * 10 AS d FROM items WHERE library_id=? AND type=? "
                                          "AND year IS NOT NULL ORDER BY d DESC", (int(sid), root))]

        def langs(path):
            return [r["l"] for r in db.all(
                f"SELECT json_extract(t.value, '$.lang') AS l, COUNT(*) AS n FROM items i, json_each(json_extract(i.probe, '$.{path}')) t "
                "WHERE i.library_id=? AND i.type=? GROUP BY l HAVING l IS NOT NULL AND l<>'' ORDER BY n DESC LIMIT 30",
                (int(sid), media_type)) if LANG.match(r["l"] or "")]
        subs = langs("subs")
        subs += [l for l in langs("external") if l not in subs]
        out = {"decades": decades, "audio": [{"id": l, "title": i18n.tr(LANG_NAMES.get(l, l.upper()))} for l in langs("audio")],
               "subs": [{"id": l, "title": i18n.tr(LANG_NAMES.get(l, l.upper()))} for l in subs]}
        facets_cache[sid] = (key, out)
        return out

    @app.route("GET", r"/api/libraries/(?P<sid>\d{1,6})/items")
    def library_items(req):
        sid = req.params["sid"]
        require_section(req.user, sid)
        lib = db.one("SELECT kind FROM libraries WHERE id=?", (int(sid),))
        kind = "movie" if lib["kind"] == "movie" else "show"
        where = "WHERE i.library_id=? AND i.type=?"
        args = [int(sid), kind]
        fsql, fargs = library_filters(req, kind)
        where += fsql
        args += fargs
        start = req.int_arg("start", 0, 0)
        size = req.int_arg("size", 60, 1, 200)
        order = SORTS.get(req.arg("sort", "title"), SORTS["title"])
        if req.arg("sort") == "mine":  # ma note (étoiles), les titres notés d'abord
            order = "(SELECT st.stars FROM stars st WHERE st.user_id=? AND st.item_id=i.id) DESC NULLS LAST, i.sort_title"
        af, aa = age_filter(req.user)
        where += af
        args += aa
        total = db.scalar(f"SELECT COUNT(*) FROM items i LEFT JOIN items sh ON sh.id=i.show_id {where}", args)
        order_args = [req.user["id"]] if req.arg("sort") == "mine" else []
        rows = db.all(CARD_SQL + where + f" ORDER BY {order} LIMIT ? OFFSET ?", [*args, *order_args, size, start])
        items = attach_state(req.user["id"], [card(r) for r in rows])
        return {"total": total, "items": items}

    @app.route("GET", r"/api/libraries/(?P<sid>\d{1,6})/genres")
    def library_genres(req):
        sid = req.params["sid"]
        require_section(req.user, sid)
        rows = db.all("SELECT g.value AS name, COUNT(*) AS n FROM items i, json_each(i.genres) g "
                      "WHERE i.library_id=? AND i.type IN ('movie','show') GROUP BY g.value ORDER BY g.value",
                      (int(sid),))
        hidden = set(get_prefs(req.user["id"]).get("hidden_genres") or [])
        return [{"id": r["name"], "title": r["name"], "count": r["n"]} for r in rows if r["name"] not in hidden]

    @app.route("GET", "/api/me/genres")
    def my_genres(req):
        """Tous les genres des bibliothèques de ce compte (choix des genres à masquer), y compris ceux déjà masqués."""
        libs = [int(i) for i in x.allowed_ids(req.user)]
        if not libs:
            return []
        rows = db.all("SELECT g.value AS name, COUNT(*) AS n FROM items i, json_each(i.genres) g WHERE i.library_id IN "
                      f"({','.join('?' * len(libs))}) AND i.type IN ('movie','show') GROUP BY g.value ORDER BY g.value", libs)
        return [{"name": r["name"], "count": r["n"]} for r in rows]

    def recent_row(lib, user):
        if lib["type"] == "movie":
            af, aa = age_filter(user)
            rows = db.all(CARD_SQL + "WHERE i.library_id=? AND i.type='movie'" + af +
                          " ORDER BY i.added_at DESC, i.id DESC LIMIT 24", (int(lib["id"]), *aa))
            return [card(r) for r in rows]
        hidden = x.hidden_ids(user)
        latest = db.all("SELECT show_id, MAX(added_at) AS t FROM items WHERE library_id=? AND type='episode' "
                        "GROUP BY show_id ORDER BY t DESC LIMIT 24", (int(lib["id"]),))
        ids = [l["show_id"] for l in latest]
        shows = {r["id"]: r for r in db.all(CARD_SQL + f"WHERE i.id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
        out = []
        for l in latest:
            show = shows.get(l["show_id"])
            if not show or not visible(user, show) or show["id"] in hidden:
                continue
            c = card(show)
            ep = db.one(CARD_SQL + "WHERE i.show_id=? AND i.type='episode' ORDER BY i.added_at DESC, p.idx DESC, "
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
        hidden = x.hidden_ids(user)
        for r in rows:
            if r["section_id"] not in allowed:
                continue
            row = db.one(CARD_SQL + "WHERE i.id=?", (int(r["rating_key"]),))
            if not row or not visible(user, row) or (row["show_id"] or row["id"]) in hidden:
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
            if r["show_key"] in shows_in_progress or not r["show_key"].isdigit() or int(r["show_key"]) in hidden:
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
            if lib["id"] not in allowed or lib["id"] in prefs["hidden_libraries"] or lib["type"] in ("photo", "audiobook"):
                continue
            items = attach_state(user["id"], recent_row(lib, user))
            if items:
                rows.append({"library": lib, "items": items})
        cont = continue_row(user, allowed) if prefs["show_continue"] else []
        hero = rows[0]["items"][0] if rows and rows[0]["items"] else None
        choice = prefs.get("home_hero") or "auto"
        if choice == "random":  # un film ou une série au hasard (le même pendant une heure)
            libs = [int(l["library"]["id"]) for l in rows] or [0]
            af, aa = age_filter(user)
            pick = db.one(CARD_SQL + f"WHERE i.type IN ('movie','show') AND i.library_id IN ({','.join('?' * len(libs))}) "
                          "AND i.backdrop IS NOT NULL AND i.backdrop<>''" + af + " ORDER BY (i.id * 2654435761 + ?) % 1000003 LIMIT 1",
                          [*libs, *aa, int(time.time() // 3600)])
            hero = attach_state(user["id"], [card(pick)])[0] if pick else hero
        elif choice == "watchlist":
            wl = x.watchlist_items(user, 1)
            hero = wl[0] if wl else hero
        sugg = x.suggestions(user) if not {"suggest", "because"} <= set(prefs["home_hidden"]) else {"suggestions": [], "because": []}
        hidden_rows = set(prefs["home_hidden"])
        return {"continue": cont, "rows": rows, "hero": hero, "hero_mode": choice, "watchlist": x.watchlist_items(user, 24),
                "suggestions": sugg["suggestions"], "because": sugg["because"],
                "short": short_row(user, allowed) if "short" not in hidden_rows else [],
                "unfinished": unfinished_row(user, allowed) if "unfinished" not in hidden_rows else []}

    def movie_libs(allowed):
        return [int(l["id"]) for l in libraries_all() if l["id"] in allowed and l["type"] == "movie"]

    def short_row(user, allowed):
        """Films de moins de 1 h 30 pas encore vus, les mieux notés d'abord (renouvelés chaque jour)."""
        libs = movie_libs(allowed)
        if not libs:
            return []
        af, aa = age_filter(user)
        rows = db.all(CARD_SQL + f"WHERE i.type='movie' AND i.library_id IN ({','.join('?' * len(libs))}) AND i.duration_ms "
                      "BETWEEN 1200000 AND 5399999 AND NOT EXISTS (SELECT 1 FROM progress pr WHERE pr.user_id=? AND "
                      "pr.rating_key=CAST(i.id AS TEXT) AND pr.watched=1)" + af +
                      " ORDER BY (COALESCE(i.rating, 5) >= 6.5) DESC, (i.id * 2654435761 + ?) % 1000003 LIMIT 24",
                      [*libs, user["id"], *aa, int(time.time() // 86400)])
        return attach_state(user["id"], [card(r) for r in rows])

    def unfinished_row(user, allowed):
        """Séries commencées puis laissées de côté (rien de regardé depuis 2 mois, au-delà de « Reprendre et à
        suivre ») : l'épisode où reprendre."""
        uid, now = user["id"], int(time.time())
        hidden = x.hidden_ids(user)
        recent = db.all("SELECT show_key, MAX(last_watched_at) AS t FROM progress WHERE user_id=? AND watched=1 AND "
                        "show_key IS NOT NULL GROUP BY show_key HAVING t BETWEEN ? AND ? ORDER BY t DESC LIMIT 40",
                        (uid, now - 365 * 86400, now - 60 * 86400))
        out = []
        for r in recent:
            if not str(r["show_key"]).isdigit():
                continue
            seen = {w["rating_key"] for w in db.all("SELECT rating_key FROM progress WHERE user_id=? AND show_key=? AND watched=1",
                                                     (uid, r["show_key"]))}
            eps = leaves(r["show_key"])
            nxt = next((e for e in eps if str(e["id"]) not in seen), None)
            if not nxt or str(nxt["library_id"]) not in allowed or not visible(user, nxt) or int(r["show_key"]) in hidden:
                continue
            out.append(card(nxt))
            if len(out) >= 24:
                break
        return attach_state(uid, out)

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
        af, aa = age_filter(req.user)
        year = int(q) if q.isdigit() and len(q) == 4 and 1900 <= int(q) <= 2100 else None
        for typ, bucket, limit in (("movie", "movies", 30), ("show", "shows", 30), ("episode", "episodes", 20)):
            match = "i.search LIKE ? ESCAPE '\\'" + (" OR i.year=?" if year and typ != "episode" else "")
            rows = db.all(CARD_SQL + f"WHERE i.type=? AND i.library_id IN ({marks}) AND ({match})"
                          + af + f" ORDER BY (i.search LIKE ? ESCAPE '\\') DESC, i.sort_title LIMIT ?",
                          [typ, *allowed, like, *([year] if year and typ != "episode" else []), *aa, q + "%", limit])
            out[bucket] = attach_state(req.user["id"], [card(r) for r in rows])
        # personnes (distribution, réalisation) et genres dont le nom correspond
        people = {}
        for r in db.all("SELECT DISTINCT json_extract(c.value, '$.name') AS n FROM items i, "
                        "json_each(json_extract(i.people, '$.cast')) c WHERE i.type IN ('movie','show') "
                        f"AND i.library_id IN ({marks}) LIMIT 20000", allowed):
            if r["n"] and q in fold(r["n"]):
                people[r["n"]] = None
        for r in db.all("SELECT DISTINCT d.value AS n FROM items i, json_each(json_extract(i.people, '$.directors')) d "
                        f"WHERE i.type IN ('movie','show') AND i.library_id IN ({marks}) LIMIT 5000", allowed):
            if r["n"] and q in fold(r["n"]):
                people[r["n"]] = None
        out["people"] = sorted(people, key=lambda n: (not fold(n).startswith(q), n))[:12]
        out["genres"] = [{"genre": r["g"], "library": str(r["library_id"]), "count": r["n"]} for r in db.all(
            "SELECT g.value AS g, i.library_id, COUNT(*) AS n FROM items i, json_each(i.genres) g WHERE i.type IN "
            f"('movie','show') AND i.library_id IN ({marks}) GROUP BY g.value, i.library_id ORDER BY n DESC", allowed)
            if q in fold(r["g"] or "") and r["g"] not in set(get_prefs(req.user["id"]).get("hidden_genres") or [])][:8]
        if not (out["movies"] or out["shows"] or out["episodes"] or out["people"]):
            # rien d'exact : titres proches (faute de frappe, lettre en trop ou en moins)
            import difflib
            pool = db.all(CARD_SQL.replace("SELECT i.id,", "SELECT i.search, i.id,", 1) +
                          f"WHERE i.type IN ('movie','show') AND i.library_id IN ({marks})" + af, [*allowed, *aa])
            scored = []
            for r in pool:
                words = (r["search"] or "").split()
                best = max([difflib.SequenceMatcher(None, q, r["search"] or "").ratio()] +
                           [difflib.SequenceMatcher(None, q, " ".join(words[k:k + len(q.split())])).ratio()
                            for k in range(len(words))])
                if best >= 0.75:
                    scored.append((best, r))
            scored.sort(key=lambda s: -s[0])
            near = [card(r) for _, r in scored[:20]]
            out["movies"] = attach_state(req.user["id"], [c for c in near if c["type"] == "movie"])
            out["shows"] = attach_state(req.user["id"], [c for c in near if c["type"] == "show"])
            out["approximate"] = bool(near)
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
        d["opinion"] = x.my_opinion(req.user, r)
        d["stars"], d["hidden"] = x.my_stars(req.user, r), x.is_hidden(req.user, r)
        d["trailer"] = r["trailer"]
        if d["type"] in ("movie", "show"):
            extras = db.all(CARD_SQL + "WHERE i.parent_id=? AND i.type='extra' ORDER BY i.extra_kind!='trailer', i.sort_title",
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
            # stockage froid : copie(s) ailleurs ; « cold_only » : le fichier n'est plus que là (lecture un peu plus lente)
            if not r["peer_id"]:
                n = db.scalar("SELECT COUNT(*) FROM cold_copies WHERE item_id=? AND verified_at IS NOT NULL", (r["id"],))
                d["archived"] = bool(n)
                d["cold_only"] = bool(n) and ctx.cold.local_missing(r)
        elif d["type"] == "show":
            seasons = db.all(CARD_SQL + "WHERE i.parent_id=? AND i.type='season' ORDER BY (i.idx=0), i.idx", (r["id"],))
            d["seasons"] = attach_state(uid, [card(s) for s in seasons])
            eps = attach_state(uid, [card(e) for e in leaves(r["id"])])
            target = next((e for e in eps if e["position"] > 0 and not e["watched"]), None)
            if not target:
                last_watched = max((i for i, e in enumerate(eps) if e["watched"]), default=-1)
                target = next((e for e in eps[last_watched + 1:] if not e["watched"]), None)
            d["play_target"] = target or (eps[0] if eps else None)
            d["track_prefs"] = track_prefs(uid, r)
            if d["play_target"]:
                trow = db.one("SELECT * FROM items WHERE id=?", (int(d["play_target"]["id"]),))
                if ctx.subtitles.has_work(trow):
                    ctx.subtitles.enqueue(trow["id"], urgent=True)  # l'épisode à lancer : sous-titres préparés d'avance
                d["audio"], d["subtitles"] = stream_lists(json.loads(trow["probe"] or "{}"))
        elif d["type"] == "season":
            d["show_id"] = str(r["show_id"])
        if req.user["is_admin"] and not r["peer_id"] and d["type"] in ("movie", "show", "season", "episode"):
            root = r["show_id"] if d["type"] in ("season", "episode") else r["id"]
            d["kept"] = bool(db.scalar("SELECT 1 FROM cold_keep WHERE item_id=?", (root,)))  # « toujours garder sur le disque »
        return d

    @app.route("GET", f"/api/items/{RK}/children")
    def item_children(req):
        r = require_item(req.user, req.params["rk"])
        if r["type"] == "show":
            rows = db.all(CARD_SQL + "WHERE i.parent_id=? AND i.type='season' ORDER BY (i.idx=0), i.idx", (r["id"],))
        else:
            rows = db.all(CARD_SQL + "WHERE i.parent_id=? " + EPISODE_ORDER, (r["id"],))
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
        body = req.json()
        watched = bool(body.get("watched"))
        if body.get("undo") and not watched and r["type"] in ("movie", "episode"):
            # « Annuler » juste après « marquer comme vu » : on remet la reprise et on efface la vue ajoutée
            undo_watched(req.user["id"], card(r), body.get("position"))
            return {"ok": True}
        if r["type"] == "show":
            targets = leaves(r["id"])
        elif r["type"] == "season":
            targets = db.all(CARD_SQL + "WHERE i.parent_id=? AND i.type='episode' " + EPISODE_ORDER, (r["id"],))
        else:
            targets = [r]
        with db.tx():
            for t in targets:
                set_watched(req.user["id"], card(t), watched)
        return {"ok": True, "count": len(targets)}


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
            # gardé 10 min par le navigateur : il ne la redemande pas à chaque page
            return Response(json.dumps({"error": i18n.tr("Pas d'image")}), 404, "application/json; charset=utf-8",
                            {"Cache-Control": "private, max-age=600"})
        with open(path, "rb") as f:
            return Response(f.read(), 200, "image/jpeg", {"Cache-Control": "private, max-age=604800, immutable"})

    # ------------------------------------------------------------ personnes (acteurs, réalisation)
    PEOPLE_MATCH = ("(EXISTS (SELECT 1 FROM json_each(json_extract(i.people, '$.cast')) c "
                    "WHERE json_extract(c.value, '$.name') = ?) OR EXISTS (SELECT 1 FROM "
                    "json_each(json_extract(i.people, '$.directors')) d WHERE d.value = ?))")

    @app.route("GET", "/api/person")
    def person(req):
        """Films et séries du serveur où joue (ou que réalise) cette personne."""
        name = (req.arg("name") or "").strip()
        if not 1 <= len(name) <= 120:
            raise HTTPError(400, "Nom invalide")
        allowed = sorted(int(i) for i in allowed_set(req.user))
        if not allowed:
            return {"name": name, "photo": None, "items": []}
        af, aa = age_filter(req.user)
        rows = db.all(CARD_SQL.replace("SELECT i.id,", "SELECT i.people, i.id,", 1) + "WHERE i.type IN ('movie','show') "
                      f"AND i.library_id IN ({','.join('?' * len(allowed))}) AND " + PEOPLE_MATCH + af +
                      " ORDER BY COALESCE(i.year, 0) DESC, i.sort_title LIMIT 200", [*allowed, name, name, *aa])
        photo, items = None, []
        for r in rows:
            c = card(r)
            people = json.loads(r["people"] or "{}")
            role = next((p for p in people.get("cast", []) if p.get("name") == name), None)
            if role:
                c["role"] = role.get("role") or ""
                photo = photo or role.get("photo")
            if name in people.get("directors", []):
                c["director"] = True
            items.append(c)
        return {"name": name, "photo": photo, "items": attach_state(req.user["id"], items)}

    # ------------------------------------------------------------ au hasard
    @app.route("GET", "/api/random")
    def random_pick(req):
        """Un titre au hasard : un épisode d'une série (?show=), ou un film / une série d'une bibliothèque
        (?library=, ?genre=), de préférence pas encore vu."""
        uid = req.user["id"]
        af, aa = age_filter(req.user)
        unseen = ("NOT EXISTS (SELECT 1 FROM progress pr WHERE pr.user_id=? AND pr.rating_key=CAST(i.id AS TEXT) "
                  "AND pr.watched=1)")
        show = req.arg("show")
        if show:
            s = require_item(req.user, show)
            if s["type"] != "show":
                raise HTTPError(400, "Ce n'est pas une série")
            exclude = req.int_arg("not", 0, 0)
            for cond in (unseen, "1"):
                r = db.one(CARD_SQL + f"WHERE i.show_id=? AND i.type='episode' AND i.id<>? AND {cond} ORDER BY random() LIMIT 1",
                           [s["id"], exclude, *([uid] if cond == unseen else [])])
                if r:
                    return card(r)
            raise HTTPError(404, "Aucun épisode")
        sid = req.arg("library") or ""
        require_section(req.user, sid)
        lib = db.one("SELECT kind FROM libraries WHERE id=?", (int(sid),))
        if not lib or lib["kind"] in ("photo", "audiobook"):
            raise HTTPError(404, "Introuvable")
        kind = "movie" if lib["kind"] == "movie" else "show"
        where, args = "WHERE i.library_id=? AND i.type=?", [int(sid), kind]
        genre = req.arg("genre")
        if genre:
            where += " AND EXISTS (SELECT 1 FROM json_each(i.genres) g WHERE g.value=?)"
            args.append(genre)
        for cond in ((unseen,) if kind == "movie" else ()) + ("1",):
            r = db.one(CARD_SQL + where + af + f" AND {cond} ORDER BY random() LIMIT 1",
                       [*args, *aa, *([uid] if cond == unseen else [])])
            if r:
                return card(r)
        raise HTTPError(404, "Rien ici pour ce filtre.")

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
            row = db.one(CARD_SQL + "WHERE i.id=?", (int(r["rating_key"]),))
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

