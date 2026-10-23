"""Bibliothèques, accueil, recherche, fiches, images et historique."""
import json
import time

from .. import collections_api
from ..catalog import CARD_SQL, EPISODE_ORDER, IMG_PATH, LANG, MIN_RESUME_MS, RK, SORTS, WATCHED_RATIO, stream_lists
from ..names import fold
from ..web import HTTPError, Response


def register(app, ctx, x):
    db = ctx.db
    # outils du catalogue partagé (seance/catalog.py)
    age_filter, allowed_set, attach_state, card = x.age_filter, x.allowed_set, x.attach_state, x.card
    chapters_for, get_prefs, leaves, libraries_all = x.chapters_for, x.get_prefs, x.leaves, x.libraries_all
    markers_for, next_episode, require_item, require_section = x.markers_for, x.next_episode, x.require_item, x.require_section
    set_watched, track_prefs, track_scope, visible = x.set_watched, x.track_prefs, x.track_scope, x.visible

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
        rows = db.all(CARD_SQL + where + f" ORDER BY {order} LIMIT ? OFFSET ?", [*args, size, start])
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
            rows = db.all(CARD_SQL + "WHERE i.library_id=? AND i.type='movie'" + af +
                          " ORDER BY i.added_at DESC, i.id DESC LIMIT 24", (int(lib["id"]), *aa))
            return [card(r) for r in rows]
        latest = db.all("SELECT show_id, MAX(added_at) AS t FROM items WHERE library_id=? AND type='episode' "
                        "GROUP BY show_id ORDER BY t DESC LIMIT 24", (int(lib["id"]),))
        ids = [l["show_id"] for l in latest]
        shows = {r["id"]: r for r in db.all(CARD_SQL + f"WHERE i.id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
        out = []
        for l in latest:
            show = shows.get(l["show_id"])
            if not show or not visible(user, show):
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
        for r in rows:
            if r["section_id"] not in allowed:
                continue
            row = db.one(CARD_SQL + "WHERE i.id=?", (int(r["rating_key"]),))
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
            rows = db.all(CARD_SQL + f"WHERE i.type=? AND i.library_id IN ({marks}) AND i.search LIKE ? ESCAPE '\\'"
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
        watched = bool(req.json().get("watched"))
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
            raise HTTPError(404, "Pas d'image")
        with open(path, "rb") as f:
            return Response(f.read(), 200, "image/jpeg", {"Cache-Control": "private, max-age=604800, immutable"})

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

