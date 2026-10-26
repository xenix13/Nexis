"""Suggestions locales, sans service extérieur : à partir de ce que l'utilisateur a regardé récemment
(genres, réalisateurs, acteurs), on cherche dans ses bibliothèques les films et séries qu'il n'a pas encore vus."""
import json
import math
import threading
import time

from .catalog import CARD_SQL, MIN_RESUME_MS, RK
from .web import HTTPError

RECENT_DAYS = 90         # fenêtre de « ce que j'ai regardé récemment »
HALF_LIFE_DAYS = 21      # un visionnage d'il y a 3 semaines compte moitié moins
LIKE_WEIGHT = 1.5        # un « j'aime » compte comme un visionnage récent appuyé
DISLIKE_WEIGHT = 0.8     # un « je n'aime pas » éloigne les titres qui lui ressemblent
CACHE_SECONDS = 300
ROW_SIZE = 24
MIN_ROW = 4              # en dessous, la rangée n'est pas affichée


def _names(people, key, limit):
    out = []
    for p in (people.get(key) or [])[:limit]:
        name = p.get("name") if isinstance(p, dict) else p
        if isinstance(name, str) and name.strip():
            out.append(name.strip())
    return out


def features(row):
    """Genres et personnes d'un film ou d'une série."""
    try:
        genres = [g for g in json.loads(row["genres"] or "[]") if isinstance(g, str)]
    except (TypeError, ValueError):
        genres = []
    try:
        people = json.loads(row["people"] or "{}")
        if not isinstance(people, dict):
            people = {}
    except (TypeError, ValueError):
        people = {}
    return set(genres), set(_names(people, "directors", 3)), set(_names(people, "cast", 8))


def similarity(a, b):
    """Proximité entre deux titres : genres en commun, même réalisateur, acteurs en commun."""
    ga, da, ca = a
    gb, db_, cb = b
    score = 0.0
    if ga and gb:
        score += 3 * len(ga & gb) / len(ga | gb)
    score += 2 * len(da & db_)
    score += 0.8 * min(len(ca & cb), 4)
    return score


def register(app, ctx, x):
    db = ctx.db
    age_filter, allowed_set, attach_state, card, visible = x.age_filter, x.allowed_set, x.attach_state, x.card, x.visible
    cache, lock = {}, threading.Lock()

    def seen_titles(uid):
        """Films vus ou commencés, séries dont au moins un épisode a été vu ou commencé : jamais proposés."""
        ids = set()
        for r in db.all("SELECT rating_key, type, show_key FROM progress WHERE user_id=? "
                        "AND (watched=1 OR position_ms>=? OR view_count>0)", (uid, MIN_RESUME_MS)):
            if r["show_key"]:
                ids.add(int(r["show_key"]))
            elif str(r["rating_key"]).isdigit():
                ids.add(int(r["rating_key"]))
        for r in db.all("SELECT h.rating_key FROM history h WHERE h.user_id=?", (uid,)):
            if str(r["rating_key"]).isdigit():
                ids.add(int(r["rating_key"]))
        if ids:  # épisodes de l'historique -> leur série
            for chunk in _chunks(sorted(ids), 900):
                marks = ",".join("?" * len(chunk))
                for r in db.all(f"SELECT show_id FROM items WHERE id IN ({marks}) AND show_id IS NOT NULL", chunk):
                    ids.add(r["show_id"])
        return ids

    def seeds(uid):
        """Titres regardés récemment (film, ou série de l'épisode), avec un poids qui baisse avec le temps."""
        since = int(time.time()) - RECENT_DAYS * 86400
        events = db.all("SELECT rating_key, watched_at AS t FROM history WHERE user_id=? AND watched_at>? "
                        "ORDER BY watched_at DESC LIMIT 400", (uid, since))
        events += db.all("SELECT rating_key, updated_at AS t FROM progress WHERE user_id=? AND updated_at>? "
                         "AND position_ms>=? AND watched=0 ORDER BY updated_at DESC LIMIT 100", (uid, since, MIN_RESUME_MS))
        keys = sorted({int(e["rating_key"]) for e in events if str(e["rating_key"]).isdigit()})
        to_title = {}
        for chunk in _chunks(keys, 900):
            marks = ",".join("?" * len(chunk))
            for r in db.all(f"SELECT id, type, show_id FROM items WHERE id IN ({marks})", chunk):
                if r["type"] == "movie":
                    to_title[r["id"]] = r["id"]
                elif r["type"] == "episode" and r["show_id"]:
                    to_title[r["id"]] = r["show_id"]
        now = time.time()
        weight, last = {}, {}
        for e in events:
            tid = to_title.get(int(e["rating_key"])) if str(e["rating_key"]).isdigit() else None
            if not tid:
                continue
            w = 0.5 ** ((now - e["t"]) / 86400 / HALF_LIFE_DAYS)
            weight[tid] = weight.get(tid, 0) + w
            last[tid] = max(last.get(tid, 0), e["t"])
        # une série regardée d'une traite ne doit pas écraser tout le reste
        weight = {k: min(v, 2.5) for k, v in weight.items()}
        return weight, last

    def opinions(uid):
        """{titre: avis} ; avis 1 = j'aime, -1 = je n'aime pas, 0 = ne plus suggérer."""
        return {r["item_id"]: r["value"] for r in db.all("SELECT item_id, value FROM ratings WHERE user_id=?", (uid,))}

    def candidates(user, exclude):
        allowed = sorted(int(i) for i in allowed_set(user))
        if not allowed:
            return []
        af, aa = age_filter(user)
        marks = ",".join("?" * len(allowed))
        rows = db.all("SELECT i.id, i.type, i.genres, i.people, i.rating, i.added_at, COALESCE(sh.age, i.age) AS eff_age "
                      "FROM items i LEFT JOIN items sh ON sh.id=i.show_id "
                      f"WHERE i.type IN ('movie','show') AND i.library_id IN ({marks})" + af,
                      [*allowed, *aa])
        return [r for r in rows if r["id"] not in exclude]

    def cards(user, ids):
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = {r["id"]: r for r in db.all(CARD_SQL + f"WHERE i.id IN ({marks})", ids)}
        out = [card(rows[i]) for i in ids if i in rows and visible(user, rows[i])]
        return attach_state(user["id"], out)

    def compute(user):
        uid = user["id"]
        weight, last = seeds(uid)
        ops = opinions(uid)
        for tid, v in ops.items():
            if v == 1:
                weight[tid] = max(weight.get(tid, 0), LIKE_WEIGHT)
        dislikes = [tid for tid, v in ops.items() if v == -1]
        if not weight:
            return {"suggestions": [], "because": []}
        seed_ids = sorted(weight)
        marks = ",".join("?" * len(seed_ids))
        seed_rows = {r["id"]: r for r in db.all(f"SELECT id, title, genres, people FROM items WHERE id IN ({marks})", seed_ids)}
        seed_feat = {i: features(r) for i, r in seed_rows.items()}
        # profil : genres, réalisateurs et acteurs pondérés par la fraîcheur des visionnages
        genre_w, dir_w, cast_w = {}, {}, {}
        for i, (g, d, c) in seed_feat.items():
            w = weight[i]
            for name in g:
                genre_w[name] = genre_w.get(name, 0) + w / math.sqrt(len(g))
            for name in d:
                dir_w[name] = dir_w.get(name, 0) + w
            for name in c:
                cast_w[name] = cast_w.get(name, 0) + w
        if dislikes:  # ce qui ressemble à un titre non aimé recule
            marks_d = ",".join("?" * len(dislikes))
            for r in db.all(f"SELECT id, genres, people FROM items WHERE id IN ({marks_d})", dislikes):
                g, d, c = features(r)
                for name in g:
                    genre_w[name] = genre_w.get(name, 0) - DISLIKE_WEIGHT / math.sqrt(len(g))
                for name in d:
                    dir_w[name] = dir_w.get(name, 0) - DISLIKE_WEIGHT
                for name in c:
                    cast_w[name] = cast_w.get(name, 0) - DISLIKE_WEIGHT / 2
        top = max(genre_w.values(), default=1) or 1
        if top <= 0:
            top = 1
        pool = candidates(user, seen_titles(uid) | set(weight) | set(ops))
        feats = {r["id"]: features(r) for r in pool}
        scored = []
        for r in pool:
            g, d, c = feats[r["id"]]
            s = sum(genre_w.get(n, 0) for n in g) / top / (math.sqrt(len(g)) if g else 1)
            s += 1.5 * sum(min(dir_w.get(n, 0), 2) for n in d)
            s += 0.4 * sum(min(cast_w.get(n, 0), 2) for n in c)
            if s <= 0:
                continue
            s += 0.03 * (r["rating"] or 0)  # à score égal, le mieux noté d'abord
            scored.append((s, r["added_at"] or 0, r["id"]))
        scored.sort(reverse=True)
        suggestions = [i for _, _, i in scored[:ROW_SIZE]]

        # « Parce que tu as regardé … » : les deux titres les plus récents qui ont assez de voisins
        # (ce qui est déjà en tête des suggestions passe après, pour varier ce qu'on voit)
        because, used, shown = [], set(), set(suggestions[:8])
        for sid in sorted(last, key=last.get, reverse=True)[:6]:
            if len(because) == 2 or sid not in seed_feat:
                continue
            near = sorted(((similarity(seed_feat[sid], feats[r["id"]]), r["id"]) for r in pool), reverse=True)
            ids = [i for s, i in near if s >= 1.2 and i not in used]
            ids = ([i for i in ids if i not in shown] + [i for i in ids if i in shown])[:ROW_SIZE]
            if len(ids) >= MIN_ROW:
                because.append((sid, ids))
                used.update(ids[:6])
        return {"suggestions": suggestions, "because": because}

    def suggestions(user):
        """Rangées « Suggestions » et « Parce que vous avez regardé… » de l'accueil."""
        stamp = (db.scalar("SELECT MAX(id) FROM history WHERE user_id=?", (user["id"],)),
                 db.scalar("SELECT MAX(updated_at) FROM progress WHERE user_id=? AND position_ms>=?", (user["id"], MIN_RESUME_MS)),
                 db.scalar("SELECT COUNT(*) FROM items WHERE type IN ('movie','show')"),
                 tuple(sorted(allowed_set(user))), user.get("max_age"),
                 db.scalar("SELECT MAX(updated_at) FROM ratings WHERE user_id=?", (user["id"],)))
        key = user["id"]
        with lock:
            hit = cache.get(key)
        if hit and hit[0] == stamp and time.time() - hit[1] < CACHE_SECONDS:
            raw = hit[2]
        else:
            raw = compute(user)
            with lock:
                cache[key] = (stamp, time.time(), raw)
        out = {"suggestions": cards(user, raw["suggestions"]) if len(raw["suggestions"]) >= MIN_ROW else [], "because": []}
        for sid, ids in raw["because"]:
            seed = cards(user, [sid])
            items = cards(user, ids)
            if seed and len(items) >= MIN_ROW:
                out["because"].append({"seed": seed[0], "items": items})
        return out

    def similar_to(user, row):
        """Titres proches d'un film ou d'une série (fiche) : pas besoin d'historique."""
        if row["type"] in ("episode", "season") and row["show_id"]:
            row = db.one("SELECT * FROM items WHERE id=?", (row["show_id"],))
        if not row or row["type"] not in ("movie", "show"):
            return []
        base = features(row)
        hidden = {i for i, v in opinions(user["id"]).items() if v <= 0}
        pool = candidates(user, {row["id"]} | hidden)
        near = sorted(((similarity(base, features(r)), r["rating"] or 0, r["id"]) for r in pool), reverse=True)
        return cards(user, [i for s, _, i in near if s >= 1.2][:ROW_SIZE])

    x.suggestions = suggestions
    x.similar_to = similar_to

    def title_of(row):
        """Les avis portent sur un film ou une série (un épisode renvoie à sa série)."""
        if row["type"] in ("episode", "season") and row["show_id"]:
            return row["show_id"]
        return row["id"] if row["type"] in ("movie", "show") else None

    def my_opinion(user, row):
        tid = title_of(row)
        return db.scalar("SELECT value FROM ratings WHERE user_id=? AND item_id=?", (user["id"], tid)) if tid else None
    x.my_opinion = my_opinion

    @app.route("PUT", f"/api/items/{RK}/opinion")
    def item_opinion(req):
        row = x.require_item(req.user, req.params["rk"])
        tid = title_of(row)
        if not tid:
            raise HTTPError(400, "Avis possible sur un film ou une série uniquement")
        v = req.json().get("value")
        if v is None:
            db.run("DELETE FROM ratings WHERE user_id=? AND item_id=?", (req.user["id"], tid))
        elif v in (1, -1, 0) and not isinstance(v, bool):
            db.run("INSERT INTO ratings(user_id,item_id,value,updated_at) VALUES(?,?,?,?) "
                   "ON CONFLICT(user_id,item_id) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                   (req.user["id"], tid, v, int(time.time())))
        else:
            raise HTTPError(400, "Avis invalide")
        with lock:
            cache.pop(req.user["id"], None)  # suggestions recalculées avec ce nouvel avis
        return {"value": v}

    @app.route("GET", f"/api/items/{RK}/similar")
    def item_similar(req):
        return {"items": similar_to(req.user, x.require_item(req.user, req.params["rk"]))}


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
