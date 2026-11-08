"""Statistiques de l'administration : qui regarde quoi, combien de temps, avec quel débit (estimé), comment
(lecture directe, copie, conversion), et les titres que personne n'a jamais regardés (pour faire de la place).

Les lectures viennent du journal play_log, rempli par la base elle-même à la fin de chaque lecture (déclencheur sur
la table streams). Débit estimé = débit de la vidéo envoyée (source, ou cible d'une conversion) + 192 kbit/s de son.
"""
import time

AUDIO_KBPS = 192
NEVER_AFTER_DAYS = 90  # « jamais regardé » : seulement les titres ajoutés il y a plus de 3 mois


def register(app, ctx, x):
    db = ctx.db

    def bytes_expr(t=""):
        return f"SUM((COALESCE({t}kbps, 0) + {AUDIO_KBPS}) * 1000 / 8 * {t}seconds)"

    @app.route("GET", "/api/admin/stats", auth="admin")
    def admin_stats(req):
        days = req.int_arg("days", 30, 1, 3650)
        now = int(time.time())
        since = now - days * 86400
        tot = db.one(f"SELECT COUNT(*) AS plays, COALESCE(SUM(seconds), 0) AS seconds, COALESCE({bytes_expr()}, 0) AS bytes, "
                     "COALESCE(SUM(mode = 'transcode'), 0) AS transcodes, COUNT(DISTINCT user_id) AS users "
                     "FROM play_log WHERE ended >= ?", (since,))
        modes = {r["mode"] or "?": {"plays": r["n"], "seconds": r["s"]} for r in db.all(
            "SELECT mode, COUNT(*) AS n, SUM(seconds) AS s FROM play_log WHERE ended >= ? GROUP BY mode", (since,))}
        users = [dict(r) for r in db.all(
            f"SELECT u.id, u.display_name AS name, COUNT(*) AS plays, SUM(l.seconds) AS seconds, {bytes_expr('l.')} AS bytes, "
            "SUM(l.mode = 'transcode') AS transcodes, COUNT(DISTINCT l.rating_key) AS titles, MAX(l.ended) AS last "
            "FROM play_log l JOIN users u ON u.id = l.user_id WHERE l.ended >= ? GROUP BY u.id ORDER BY seconds DESC", (since,))]
        for u in users:
            u["id"] = str(u["id"])
        # heures par jour (jours sans lecture compris), à l'heure du serveur
        per_day = {r["d"]: r["s"] for r in db.all(
            "SELECT date(ended, 'unixepoch', 'localtime') AS d, SUM(seconds) AS s FROM play_log WHERE ended >= ? GROUP BY d", (since,))}
        chart_days = min(days, 90)
        series = []
        for i in range(chart_days - 1, -1, -1):
            d = time.strftime("%Y-%m-%d", time.localtime(now - i * 86400))
            series.append({"day": d, "seconds": per_day.get(d, 0)})
        # titres les plus regardés : un épisode compte pour sa série
        raw = db.all("SELECT rating_key, MAX(title) AS title, COUNT(*) AS plays, SUM(seconds) AS seconds "
                     "FROM play_log WHERE ended >= ? GROUP BY rating_key", (since,))
        who = {}
        for r in db.all("SELECT rating_key, user_id FROM play_log WHERE ended >= ? GROUP BY rating_key, user_id", (since,)):
            who.setdefault(str(r["rating_key"]), set()).add(r["user_id"])
        ids = [int(r["rating_key"]) for r in raw if str(r["rating_key"]).isdigit()]
        info = {}
        for i in range(0, len(ids), 900):
            chunk = ids[i:i + 900]
            for r in db.all("SELECT i.id, i.type, i.title, i.year, i.show_id, s.title AS show FROM items i "
                            f"LEFT JOIN items s ON s.id = i.show_id WHERE i.id IN ({','.join('?' * len(chunk))})", chunk):
                info[str(r["id"])] = r
        top = {}
        for r in raw:
            if r["rating_key"] == "private":
                continue  # lectures en mode privé : comptées dans les totaux, jamais nommées
            it = info.get(str(r["rating_key"]))
            key = str(it["show_id"]) if it and it["show_id"] else str(r["rating_key"])
            title = (it["show"] if it and it["show_id"] else it["title"] if it else r["title"]) or "?"
            t = top.setdefault(key, {"id": key, "title": title, "year": it["year"] if it and not it["show_id"] else None,
                                     "show": bool(it and it["show_id"]), "plays": 0, "seconds": 0, "viewers": set()})
            t["plays"] += r["plays"]
            t["seconds"] += r["seconds"]
            t["viewers"] |= who.get(str(r["rating_key"]), set())
        top_list = sorted(top.values(), key=lambda t: t["seconds"], reverse=True)[:15]
        for t in top_list:
            t["viewers"] = len(t["viewers"])
        return {"days": days, "since": since, "totals": dict(tot), "modes": modes, "users": users, "per_day": series,
                "top": top_list, "never": never_watched(now)}

    def never_watched(now):
        """Films et séries de ce serveur que personne n'a commencés, ajoutés il y a plus de 3 mois, les plus gros
        d'abord (place à récupérer sur le NAS)."""
        seen = set()
        for r in db.all("SELECT DISTINCT rating_key AS k FROM progress WHERE watched = 1 OR position_ms > 0 OR view_count > 0"):
            seen.add(str(r["k"]))
        for r in db.all("SELECT DISTINCT show_key AS k FROM progress WHERE show_key IS NOT NULL"):
            seen.add(str(r["k"]))
        for r in db.all("SELECT DISTINCT rating_key AS k FROM history"):
            seen.add(str(r["k"]))
        for r in db.all("SELECT DISTINCT rating_key AS k FROM play_log"):
            seen.add(str(r["k"]))
        # un épisode vu : sa série aussi
        ep_ids = [int(k) for k in seen if k.isdigit()]
        for i in range(0, len(ep_ids), 900):
            chunk = ep_ids[i:i + 900]
            for r in db.all(f"SELECT DISTINCT show_id FROM items WHERE show_id IS NOT NULL AND id IN ({','.join('?' * len(chunk))})", chunk):
                seen.add(str(r["show_id"]))
        sizes = {r["show_id"]: r["s"] for r in db.all(
            "SELECT show_id, SUM(COALESCE(file_size, 0)) AS s FROM items WHERE type = 'episode' AND peer_id IS NULL GROUP BY show_id")}
        rows = db.all("SELECT i.id, i.type, i.title, i.year, i.added_at, i.file_size, l.name AS library FROM items i "
                      "JOIN libraries l ON l.id = i.library_id WHERE i.type IN ('movie', 'show') AND i.peer_id IS NULL "
                      "AND l.kind IN ('movie', 'show') AND i.added_at < ?", (now - NEVER_AFTER_DAYS * 86400,))
        out = []
        for r in rows:
            if str(r["id"]) in seen:
                continue
            size = (r["file_size"] or 0) if r["type"] == "movie" else sizes.get(r["id"], 0)
            out.append({"id": str(r["id"]), "type": r["type"], "title": r["title"], "year": r["year"], "added_at": r["added_at"],
                        "library": r["library"], "size": size})
        out.sort(key=lambda t: t["size"], reverse=True)
        return {"count": len(out), "size": sum(t["size"] for t in out), "items": out[:50], "after_days": NEVER_AFTER_DAYS}

