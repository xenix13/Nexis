"""Notifications de nouveaux épisodes et bilan de visionnage, par utilisateur.

Une série est « suivie » dès qu'un de ses épisodes a été regardé (ou commencé), ou qu'elle est dans « À regarder ».
Ses épisodes ajoutés depuis moins de NEW_DAYS jours et pas encore vus font l'objet d'une notification ; elle est
« non lue » tant que l'utilisateur n'a pas ouvert la liste après l'ajout.
"""
import collections
import datetime
import json
import time

from .catalog import CARD_SQL, MIN_RESUME_MS
from .i18n import tr

NEW_DAYS = 30
MAX_NOTIFS = 30
WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre"]


def register(app, ctx, x):
    db = ctx.db

    def followed(uid):
        ids = {int(r["show_key"]) for r in db.all(
            "SELECT DISTINCT show_key FROM progress WHERE user_id=? AND show_key IS NOT NULL "
            "AND (watched=1 OR position_ms>=? OR view_count>0)", (uid, MIN_RESUME_MS)) if str(r["show_key"]).isdigit()}
        ids |= {r["item_id"] for r in db.all(
            "SELECT w.item_id FROM watchlist w JOIN items i ON i.id=w.item_id WHERE w.user_id=? AND i.type='show'", (uid,))}
        return sorted(ids)

    def new_episodes(user):
        """[(série, [épisodes nouveaux non vus])] pour les séries suivies, du plus récent au plus ancien."""
        uid = user["id"]
        shows = followed(uid)
        if not shows:
            return []
        since = int(time.time()) - NEW_DAYS * 86400
        marks = ",".join("?" * len(shows))
        eps = db.all(CARD_SQL + f"WHERE i.type='episode' AND i.show_id IN ({marks}) AND i.added_at>? "
                     "AND NOT EXISTS (SELECT 1 FROM progress p WHERE p.user_id=? AND p.rating_key=CAST(i.id AS TEXT) "
                     "AND p.watched=1) " + "ORDER BY i.added_at DESC, i.id", (*shows, since, uid))
        by_show = collections.OrderedDict()
        for e in eps:
            if x.visible(user, e):
                by_show.setdefault(e["show_id"], []).append(e)
        if not by_show:
            return []
        marks = ",".join("?" * len(by_show))
        show_rows = {r["id"]: r for r in db.all(CARD_SQL + f"WHERE i.id IN ({marks})", list(by_show))}
        allowed = x.allowed_set(user)
        out = []
        for sid, items in by_show.items():
            sh = show_rows.get(sid)
            if sh and str(sh["library_id"]) in allowed and x.visible(user, sh):
                out.append((sh, items))
        return out[:MAX_NOTIFS]

    def unread_count(user):
        seen = db.scalar("SELECT notif_seen FROM users WHERE id=?", (user["id"],)) or 0
        return sum(1 for _, eps in new_episodes(user) if max(e["added_at"] or 0 for e in eps) > seen)
    x.unread_notifications = unread_count

    @app.route("GET", "/api/notifications")
    def notifications(req):
        user = req.user
        seen = db.scalar("SELECT notif_seen FROM users WHERE id=?", (user["id"],)) or 0
        out = []
        for sh, eps in new_episodes(user):
            first = sorted(eps, key=lambda e: ((e.get("parent_idx") or 0), e["idx"] or 0))[0]
            newest = max(e["added_at"] or 0 for e in eps)
            out.append({"show": x.card(sh), "count": len(eps), "episode": x.card(first), "added_at": newest,
                        "unread": newest > seen})
        return {"items": out, "unread": sum(1 for n in out if n["unread"])}

    @app.route("POST", "/api/notifications/seen")
    def notifications_seen(req):
        db.run("UPDATE users SET notif_seen=? WHERE id=?", (int(time.time()), req.user["id"]))
        return {"ok": True}

    # ------------------------------------------------------------------ bilan
    @app.route("GET", "/api/history/recap")
    def recap(req):
        """Bilan d'une année (ou d'un mois) : temps passé, titres, genres, jour préféré."""
        now = datetime.datetime.now()
        year = req.int_arg("year", now.year, 2000, 2100)
        month = req.int_arg("month", 0, 0, 12)
        start = datetime.datetime(year, month or 1, 1)
        end = (datetime.datetime(year + (month == 12), (month % 12) + 1, 1) if month
               else datetime.datetime(year + 1, 1, 1))
        rows = db.all("SELECT rating_key, watched_at, meta FROM history WHERE user_id=? AND watched_at>=? AND watched_at<? "
                      "ORDER BY watched_at", (req.user["id"], int(start.timestamp()), int(end.timestamp())))
        if not rows:
            return {"year": year, "month": month, "empty": True}
        ms = films = episodes = 0
        per_title = collections.Counter()
        title_card = {}
        per_month = [0] * 12
        per_weekday = [0] * 7
        days = set()
        for r in rows:
            try:
                m = json.loads(r["meta"] or "{}")
            except ValueError:
                m = {}
            dur = int(m.get("duration") or 0)
            ms += dur
            t = datetime.datetime.fromtimestamp(r["watched_at"])
            per_month[t.month - 1] += dur
            per_weekday[t.weekday()] += dur
            days.add(t.date())
            if m.get("type") == "episode":
                episodes += 1
                key = m.get("show_id")
            else:
                films += 1
                key = m.get("id") or r["rating_key"]
            if key:
                per_title[str(key)] += max(dur, 1)
        # titres les plus regardés (encore visibles pour cet utilisateur)
        top = []
        for key, _ in per_title.most_common(12):
            if not str(key).isdigit():
                continue
            row = db.one(CARD_SQL + "WHERE i.id=?", (int(key),))
            if row and x.visible(req.user, row) and str(row["library_id"]) in x.allowed_set(req.user):
                c = x.card(row)
                c["watched_ms"] = per_title[key]
                top.append(c)
            if len(top) == 5:
                break
        # genres : ceux des titres regardés, pondérés par le temps passé
        genres = collections.Counter()
        keys = [k for k in per_title if str(k).isdigit()]
        for chunk in range(0, len(keys), 900):
            part = keys[chunk:chunk + 900]
            for r in db.all(f"SELECT id, genres FROM items WHERE id IN ({','.join('?' * len(part))})", part):
                try:
                    gs = json.loads(r["genres"] or "[]")
                except ValueError:
                    gs = []
                for g in gs[:3]:
                    if isinstance(g, str):
                        genres[g] += per_title[str(r["id"])]
        # plus longue série de jours consécutifs avec au moins un visionnage
        streak = best = 0
        prev = None
        for d in sorted(days):
            streak = streak + 1 if prev and (d - prev).days == 1 else 1
            best = max(best, streak)
            prev = d
        busiest = max(range(7), key=lambda i: per_weekday[i])
        return {"year": year, "month": month, "empty": False,
                "label": f"{tr(MONTHS[month - 1])} {year}" if month else str(year),
                "hours": round(ms / 3_600_000, 1), "minutes": round(ms / 60_000), "films": films, "episodes": episodes, "days": len(days),
                "streak": best, "weekday": tr(WEEKDAYS[busiest]) if per_weekday[busiest] else None,
                "months": [round(v / 3_600_000, 1) for v in per_month] if not month else None,
                "top": top, "genres": [g for g, _ in genres.most_common(5)]}

    @app.route("GET", "/api/history/years")
    def recap_years(req):
        rows = db.all("SELECT DISTINCT CAST(strftime('%Y', watched_at, 'unixepoch', 'localtime') AS INTEGER) AS y "
                      "FROM history WHERE user_id=? ORDER BY y DESC", (req.user["id"],))
        return [r["y"] for r in rows if r["y"]]
