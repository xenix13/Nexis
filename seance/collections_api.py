"""Collections, playlists et liste « À regarder »."""
import json
import re
import time

from .names import sort_title
from .web import HTTPError, Response

CID = r"(?P<cid>\d{1,10})"
PID = r"(?P<pid>\d{1,10})"
COLL_IMG = re.compile(r"^/library/collections/(\d{1,10})/(thumb|art)/\d{1,14}$")


def coll_img(c, kind):
    return f"/library/collections/{c['id']}/{kind}/{c['img_version']}"


def register(app, ctx, x):
    """x : outils partagés de l'API (card, attach_state, require_item, visible, age_filter…)."""
    db = ctx.db

    # ------------------------------------------------------------ collections
    def coll_card(c, count=None):
        return {"id": str(c["id"]), "type": "collection", "title": c["title"], "summary": c["summary"],
                "thumb": coll_img(c, "thumb"), "art": coll_img(c, "art"), "section": str(c["library_id"]),
                "kind": c["kind"], "leaves": count}

    def coll_items(user, cid):
        af, aa = x.age_filter(user)
        rows = db.all(x.ITEM_SQL + "JOIN collection_items ci ON ci.item_id=i.id WHERE ci.collection_id=?" + af +
                      " ORDER BY i.year IS NULL, i.year, i.sort_title", (cid, *aa))
        return [r for r in rows if x.visible(user, r)]

    def require_collection(user, cid):
        c = db.one("SELECT * FROM collections WHERE id=?", (int(cid),))
        if not c:
            raise HTTPError(404, "Collection introuvable")
        x.require_section(user, c["library_id"])
        return c

    @app.route("GET", r"/api/libraries/(?P<sid>\d{1,6})/collections")
    def library_collections(req):
        sid = req.params["sid"]
        x.require_section(req.user, sid)
        out = []
        for c in db.all("SELECT * FROM collections WHERE library_id=? ORDER BY sort_title", (int(sid),)):
            n = len(coll_items(req.user, c["id"]))
            if n:  # une collection vide (ou entièrement masquée pour ce profil) n'est pas affichée
                out.append(coll_card(c, n))
        return out

    @app.route("GET", f"/api/collections/{CID}")
    def collection(req):
        c = require_collection(req.user, req.params["cid"])
        items = x.attach_state(req.user["id"], [x.card(r) for r in coll_items(req.user, c["id"])])
        if not items:
            raise HTTPError(404, "Collection introuvable")
        out = coll_card(c, len(items))
        out["items"] = items
        out["library"] = next((l for l in x.libraries_all() if l["id"] == str(c["library_id"])), None)
        return out

    def collections_of(user, item_id):
        """Collections contenant cet élément, avec les autres éléments (pour la fiche)."""
        out = []
        for c in db.all("SELECT c.* FROM collections c JOIN collection_items ci ON ci.collection_id=c.id "
                        "WHERE ci.item_id=? ORDER BY c.title", (int(item_id),)):
            items = x.attach_state(user["id"], [x.card(r) for r in coll_items(user, c["id"])])
            if items:
                cc = coll_card(c, len(items))
                cc["items"] = items
                out.append(cc)
        return out
    x.collections_of = collections_of

    def collection_image(req, m):
        c = require_collection(req.user, m.group(1))
        w = max(40, min(1920, round(req.int_arg("w", 300) / 20) * 20))
        h = max(40, min(1920, round(req.int_arg("h", 450) / 20) * 20))
        path = ctx.images.get_collection(c, m.group(2), w, h)
        if not path:
            raise HTTPError(404, "Pas d'image")
        with open(path, "rb") as f:
            return Response(f.read(), 200, "image/jpeg", {"Cache-Control": "private, max-age=604800, immutable"})
    x.collection_image = collection_image

    # admin
    @app.route("POST", "/api/admin/collections", auth="admin")
    def admin_collection_create(req):
        b = req.json()
        title = str(b.get("title") or "").strip()
        if not 1 <= len(title) <= 100:
            raise HTTPError(400, "Titre : 1 à 100 caractères")
        item = x.require_item(req.user, str(b.get("item_id") or "0")) if b.get("item_id") else None
        lib = item["library_id"] if item else b.get("library_id")
        if not lib or not db.scalar("SELECT 1 FROM libraries WHERE id=?", (int(lib),)):
            raise HTTPError(400, "Bibliothèque invalide")
        cid = db.run("INSERT INTO collections(library_id,title,sort_title,kind,created_at) VALUES(?,?,?,'manual',?)",
                     (int(lib), title, sort_title(title), int(time.time()))).lastrowid
        if item:
            db.run("INSERT OR IGNORE INTO collection_items(collection_id,item_id) VALUES(?,?)", (cid, item["id"]))
        return {"id": str(cid)}

    @app.route("PUT", f"/api/admin/collections/{CID}", auth="admin")
    def admin_collection_update(req):
        c = require_collection(req.user, req.params["cid"])
        b = req.json()
        title = str(b.get("title") or c["title"]).strip()
        if not 1 <= len(title) <= 100:
            raise HTTPError(400, "Titre : 1 à 100 caractères")
        summary = b.get("summary", c["summary"])
        if summary is not None and len(str(summary)) > 4000:
            raise HTTPError(400, "Résumé trop long")
        db.run("UPDATE collections SET title=?, sort_title=?, summary=? WHERE id=?",
               (title, sort_title(title), summary, c["id"]))
        return {"ok": True}

    @app.route("DELETE", f"/api/admin/collections/{CID}", auth="admin")
    def admin_collection_delete(req):
        c = require_collection(req.user, req.params["cid"])
        db.run("DELETE FROM collections WHERE id=?", (c["id"],))
        return {"ok": True}

    @app.route("GET", r"/api/admin/collections/for/(?P<rk>\d{1,12})", auth="admin")
    def admin_collections_for(req):
        r = x.require_item(req.user, req.params["rk"])
        rows = db.all("SELECT c.id, c.title, c.kind, EXISTS(SELECT 1 FROM collection_items ci WHERE ci.collection_id=c.id "
                      "AND ci.item_id=?) AS member FROM collections c WHERE c.library_id=? ORDER BY c.sort_title",
                      (r["id"], r["library_id"]))
        return [{"id": str(c["id"]), "title": c["title"], "kind": c["kind"], "member": bool(c["member"])} for c in rows]

    @app.route("POST", f"/api/admin/collections/{CID}/items", auth="admin")
    def admin_collection_add(req):
        c = require_collection(req.user, req.params["cid"])
        r = x.require_item(req.user, str(req.json().get("item_id") or "0"))
        if r["library_id"] != c["library_id"] or r["type"] not in ("movie", "show"):
            raise HTTPError(400, "Seuls les films et séries de la même bibliothèque peuvent y être ajoutés")
        db.run("INSERT OR IGNORE INTO collection_items(collection_id,item_id) VALUES(?,?)", (c["id"], r["id"]))
        return {"ok": True}

    @app.route("DELETE", f"/api/admin/collections/{CID}/items/(?P<rk>\\d{{1,12}})", auth="admin")
    def admin_collection_remove(req):
        c = require_collection(req.user, req.params["cid"])
        db.run("DELETE FROM collection_items WHERE collection_id=? AND item_id=?", (c["id"], int(req.params["rk"])))
        return {"ok": True}

    # -------------------------------------------------------------- playlists
    def own_playlist(req, pid):
        p = db.one("SELECT * FROM playlists WHERE id=? AND user_id=?", (int(pid), req.user["id"]))
        if not p:
            raise HTTPError(404, "Playlist introuvable")
        return p

    def playlist_entries(user, pid):
        rows = db.all(x.ITEM_SQL.replace("SELECT i.*,", "SELECT i.*, pe.id AS entry_id, pe.position AS entry_pos,") +
                      "JOIN playlist_items pe ON pe.item_id=i.id WHERE pe.playlist_id=? ORDER BY pe.position, pe.id",
                      (pid,))
        allowed = x.allowed_set(user)
        return [r for r in rows if str(r["library_id"]) in allowed and x.visible(user, r)]
    x.playlist_entries = playlist_entries

    def playlist_view(user, p, with_items=False):
        entries = playlist_entries(user, p["id"])
        v = {"id": str(p["id"]), "title": p["title"], "count": len(entries),
             "duration": sum(e["duration_ms"] or 0 for e in entries), "updated_at": p["updated_at"],
             "thumbs": [x.card(e).get("show_thumb") or x.card(e)["thumb"] for e in entries[:4]]}
        if with_items:
            cards = []
            for e in entries:
                c = x.card(e)
                c["entry_id"] = str(e["entry_id"])
                cards.append(c)
            v["items"] = x.attach_state(user["id"], cards)
        return v

    @app.route("GET", "/api/playlists")
    def playlists(req):
        return [playlist_view(req.user, p) for p in
                db.all("SELECT * FROM playlists WHERE user_id=? ORDER BY updated_at DESC", (req.user["id"],))]

    @app.route("POST", "/api/playlists")
    def playlist_create(req):
        b = req.json()
        title = str(b.get("title") or "").strip()
        if not 1 <= len(title) <= 100:
            raise HTTPError(400, "Titre : 1 à 100 caractères")
        if db.scalar("SELECT COUNT(*) FROM playlists WHERE user_id=?", (req.user["id"],)) >= 200:
            raise HTTPError(400, "Trop de playlists")
        now = int(time.time())
        pid = db.run("INSERT INTO playlists(user_id,title,created_at,updated_at) VALUES(?,?,?,?)",
                     (req.user["id"], title, now, now)).lastrowid
        if b.get("item_id"):
            add_to_playlist(req, pid, str(b["item_id"]))
        return {"id": str(pid)}

    @app.route("GET", f"/api/playlists/{PID}")
    def playlist(req):
        return playlist_view(req.user, own_playlist(req, req.params["pid"]), with_items=True)

    @app.route("PUT", f"/api/playlists/{PID}")
    def playlist_update(req):
        p = own_playlist(req, req.params["pid"])
        b = req.json()
        if "title" in b:
            title = str(b.get("title") or "").strip()
            if not 1 <= len(title) <= 100:
                raise HTTPError(400, "Titre : 1 à 100 caractères")
            db.run("UPDATE playlists SET title=?, updated_at=? WHERE id=?", (title, int(time.time()), p["id"]))
        if "order" in b:
            order = b["order"]
            if not isinstance(order, list) or not all(isinstance(e, str) and e.isdigit() for e in order):
                raise HTTPError(400, "Ordre invalide")
            mine = {r["id"] for r in db.all("SELECT id FROM playlist_items WHERE playlist_id=?", (p["id"],))}
            with db.tx():
                for n, e in enumerate(order):
                    if int(e) in mine:
                        db.run("UPDATE playlist_items SET position=? WHERE id=?", (n, int(e)))
        return {"ok": True}

    @app.route("DELETE", f"/api/playlists/{PID}")
    def playlist_delete(req):
        p = own_playlist(req, req.params["pid"])
        db.run("DELETE FROM playlists WHERE id=?", (p["id"],))
        return {"ok": True}

    def add_to_playlist(req, pid, rk):
        r = x.require_item(req.user, rk)
        if r["type"] == "show":
            targets = x.leaves(r["id"])
        elif r["type"] == "season":
            targets = db.all(x.ITEM_SQL + "WHERE i.parent_id=? AND i.type='episode' " + x.EPISODE_ORDER, (r["id"],))
        elif r["type"] in ("movie", "episode"):
            targets = [r]
        else:
            raise HTTPError(400, "Élément non pris en charge")
        pos = db.scalar("SELECT MAX(position) FROM playlist_items WHERE playlist_id=?", (pid,))
        pos = (pos if pos is not None else -1) + 1
        if (db.scalar("SELECT COUNT(*) FROM playlist_items WHERE playlist_id=?", (pid,)) or 0) + len(targets) > 5000:
            raise HTTPError(400, "Playlist trop longue")
        with db.tx():
            for t in targets:
                db.run("INSERT INTO playlist_items(playlist_id,item_id,position) VALUES(?,?,?)", (pid, t["id"], pos))
                pos += 1
            db.run("UPDATE playlists SET updated_at=? WHERE id=?", (int(time.time()), pid))
        return len(targets)

    @app.route("POST", f"/api/playlists/{PID}/items")
    def playlist_add(req):
        p = own_playlist(req, req.params["pid"])
        n = add_to_playlist(req, p["id"], str(req.json().get("item_id") or "0"))
        return {"ok": True, "added": n}

    @app.route("DELETE", f"/api/playlists/{PID}/items/(?P<eid>\\d{{1,12}})")
    def playlist_remove(req):
        p = own_playlist(req, req.params["pid"])
        db.run("DELETE FROM playlist_items WHERE id=? AND playlist_id=?", (int(req.params["eid"]), p["id"]))
        return {"ok": True}

    def playlist_next(user, pid, item_id):
        """Élément suivant dans une playlist (pour l'enchaînement dans le lecteur)."""
        p = db.one("SELECT id FROM playlists WHERE id=? AND user_id=?", (int(pid), user["id"]))
        if not p:
            return None
        entries = playlist_entries(user, p["id"])
        ids = [e["id"] for e in entries]
        if item_id not in ids:
            return None
        i = ids.index(item_id)
        return entries[i + 1] if i + 1 < len(entries) else None
    x.playlist_next = playlist_next

    # -------------------------------------------------------------- À regarder
    def watch_target(r):
        if r["type"] in ("episode", "season", "extra"):
            return db.one("SELECT * FROM items WHERE id=?", (r["show_id"],))
        return r

    @app.route("POST", r"/api/items/(?P<rk>\d{1,12})/watchlist")
    def watchlist_toggle(req):
        r = watch_target(x.require_item(req.user, req.params["rk"]))
        if req.json().get("on", True):
            db.run("INSERT OR IGNORE INTO watchlist(user_id,item_id,added_at) VALUES(?,?,?)",
                   (req.user["id"], r["id"], int(time.time())))
        else:
            db.run("DELETE FROM watchlist WHERE user_id=? AND item_id=?", (req.user["id"], r["id"]))
        return {"ok": True}

    def watchlist_items(user, limit=500):
        af, aa = x.age_filter(user)
        allowed = x.allowed_set(user)
        rows = db.all(x.ITEM_SQL + "JOIN watchlist w ON w.item_id=i.id WHERE w.user_id=?" + af +
                      " ORDER BY w.added_at DESC LIMIT ?", (user["id"], *aa, limit))
        return x.attach_state(user["id"], [x.card(r) for r in rows if str(r["library_id"]) in allowed])
    x.watchlist_items = watchlist_items

    @app.route("GET", "/api/watchlist")
    def watchlist(req):
        return watchlist_items(req.user)

    def in_watchlist(user, r):
        t = watch_target(r)
        return bool(t and db.scalar("SELECT 1 FROM watchlist WHERE user_id=? AND item_id=?", (user["id"], t["id"])))
    x.in_watchlist = in_watchlist
