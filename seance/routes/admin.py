"""Administration : utilisateurs, bibliothèques, scan, réglages, webhook, lectures, partages, identification."""
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time

from .. import __version__ as VERSION, media, mountd, transcoder, update
from ..auth import USERNAME, USERNAME_RULE, find_user, hash_password, normalize_username, validate_password
from ..catalog import HEX, RK, SID
from ..metadata import MetaError
from ..scanner import like_prefix
from ..transcoder import DOWNMIX_ALGOS, system_memory
from ..web import HTTPError, Response

log = logging.getLogger("seance.admin")
IN_DOCKER = os.environ.get("SEANCE_DOCKER") == "1"


def register(app, ctx, x):
    db = ctx.db
    # outils du catalogue partagé (seance/catalog.py)
    hash_pin, item_row, stop_stream, user_view = x.hash_pin, x.item_row, x.stop_stream, x.user_view

    # -------------------------------------------------------- admin users
    def admin_user_view(u):
        v = user_view(u)
        v.update(all_libraries=bool(u["all_libraries"]), disabled=bool(u["disabled"]), created_at=u["created_at"],
                 allow_unrated=bool(u["allow_unrated"]),
                 libraries=[r["section_id"] for r in db.all(
                     "SELECT section_id FROM user_libraries WHERE user_id=?", (u["id"],))],
                 last_seen=db.scalar("SELECT MAX(last_seen) FROM sessions WHERE user_id=?", (u["id"],)))
        return v

    @app.route("GET", "/api/admin/users", auth="admin")
    def admin_users(req):
        return [admin_user_view(u) for u in db.all("SELECT * FROM users ORDER BY username COLLATE NOCASE")]

    def apply_user_fields(uid, b, creating=False):
        fields = {}
        if "display_name" in b:
            name = str(b["display_name"] or "").strip()
            if not 1 <= len(name) <= 50:
                raise HTTPError(400, "Nom affiché : 1 à 50 caractères")
            fields["display_name"] = name
        for k in ("is_admin", "all_libraries", "disabled"):
            if k in b:
                fields[k] = 1 if b[k] else 0
        if "avatar_color" in b:
            if not (isinstance(b["avatar_color"], str) and HEX.match(b["avatar_color"])):
                raise HTTPError(400, "Couleur invalide")
            fields["avatar_color"] = b["avatar_color"]
        if b.get("password"):
            validate_password(b["password"])
            fields["password_hash"] = hash_password(b["password"])
        for k in ("managed", "home", "allow_unrated"):
            if k in b:
                fields[k] = 1 if b[k] else 0
        if "max_age" in b:
            if b["max_age"] not in (None, 0, 7, 10, 12, 13, 14, 16, 17, 18):
                raise HTTPError(400, "Restriction d'âge invalide")
            fields["max_age"] = b["max_age"]
        if "pin" in b:
            fields["pin_hash"] = hash_pin(b["pin"])
        cur = db.one("SELECT is_admin, managed, password_hash FROM users WHERE id=?", (uid,))
        managed = fields.get("managed", cur["managed"])
        if managed and fields.get("is_admin", cur["is_admin"]):
            raise HTTPError(400, "Un profil géré (sans mot de passe) ne peut pas être administrateur")
        if managed and fields.get("managed") and "password_hash" not in fields:
            fields["password_hash"] = "!"  # plus de connexion par mot de passe : uniquement via « Changer de profil »
        if not managed and cur["password_hash"] == "!" and "password_hash" not in fields:
            raise HTTPError(400, "Un mot de passe est requis pour ce compte")
        if fields:
            sets = ",".join(f"{k}=?" for k in fields)
            db.run(f"UPDATE users SET {sets} WHERE id=?", [*fields.values(), uid])
        if "libraries" in b:
            libs = b["libraries"]
            if not isinstance(libs, list) or not all(isinstance(x, str) and x.isdigit() for x in libs):
                raise HTTPError(400, "Bibliothèques invalides")
            db.run("DELETE FROM user_libraries WHERE user_id=?", (uid,))
            for sid in set(libs):
                db.run("INSERT INTO user_libraries(user_id,section_id) VALUES(?,?)", (uid, sid))
        if not creating and (fields.get("disabled") or "password_hash" in fields):
            db.run("DELETE FROM sessions WHERE user_id=?", (uid,))

    @app.route("POST", "/api/admin/users", auth="admin")
    def admin_create_user(req):
        b = req.json()
        username = normalize_username(b.get("username"))
        if not USERNAME.match(username):
            raise HTTPError(400, USERNAME_RULE)
        if not b.get("managed"):
            validate_password(b.get("password"))
        if find_user(db, username):
            raise HTTPError(409, "Cet identifiant existe déjà")
        with db.tx():
            uid = db.run("INSERT INTO users(username,display_name,password_hash,created_at) VALUES(?,?,?,?)",
                         (username, username, "!", int(time.time()))).lastrowid
            b.setdefault("display_name", username)
            apply_user_fields(uid, b, creating=True)
        return admin_user_view(db.one("SELECT * FROM users WHERE id=?", (uid,)))

    def other_admins(uid):
        return db.scalar("SELECT COUNT(*) FROM users WHERE is_admin=1 AND disabled=0 AND id<>?", (uid,))

    @app.route("PUT", r"/api/admin/users/(?P<uid>\d{1,10})", auth="admin")
    def admin_update_user(req):
        uid = int(req.params["uid"])
        u = db.one("SELECT * FROM users WHERE id=?", (uid,))
        if not u:
            raise HTTPError(404, "Utilisateur introuvable")
        b = req.json()
        b.pop("username", None)
        losing_admin = ("is_admin" in b and not b["is_admin"]) or b.get("disabled")
        if losing_admin and u["is_admin"]:
            if uid == req.user["id"]:
                raise HTTPError(400, "Impossible de retirer ses propres droits d'administration ou de désactiver son propre compte")
            if not other_admins(uid):
                raise HTTPError(400, "Il doit rester au moins un administrateur actif")
        with db.tx():
            apply_user_fields(uid, b)
        return admin_user_view(db.one("SELECT * FROM users WHERE id=?", (uid,)))

    @app.route("DELETE", r"/api/admin/users/(?P<uid>\d{1,10})", auth="admin")
    def admin_delete_user(req):
        uid = int(req.params["uid"])
        if uid == req.user["id"]:
            raise HTTPError(400, "Impossible de supprimer son propre compte")
        u = db.one("SELECT is_admin FROM users WHERE id=?", (uid,))
        if not u:
            raise HTTPError(404, "Utilisateur introuvable")
        if u["is_admin"] and not other_admins(uid):
            raise HTTPError(400, "Il doit rester au moins un administrateur actif")
        x.remove_avatar(uid)
        for s in db.all("SELECT id FROM streams WHERE user_id=?", (uid,)):
            stop_stream(s["id"])
        db.run("DELETE FROM users WHERE id=?", (uid,))
        return {"ok": True}

    # ----------------------------------------------------- admin libraries
    def library_view(l):
        counts = {r["type"]: r["n"] for r in db.all(
            "SELECT type, COUNT(*) AS n FROM items WHERE library_id=? GROUP BY type", (l["id"],))}
        unmatched = db.scalar("SELECT COUNT(*) FROM items WHERE library_id=? AND type IN ('movie','show') "
                              "AND meta_state IN ('nomatch','error')", (l["id"],))
        return {"id": str(l["id"]), "name": l["name"], "kind": l["kind"], "paths": json.loads(l["paths"]),
                "scanned_at": l["scanned_at"], "counts": counts, "unmatched": unmatched,
                "paths_ok": [os.path.isdir(p) for p in json.loads(l["paths"])]}

    def validate_library(b):
        name = str(b.get("name") or "").strip()
        if not 1 <= len(name) <= 40:
            raise HTTPError(400, "Nom : 1 à 40 caractères")
        if b.get("kind") not in ("movie", "show"):
            raise HTTPError(400, "Type invalide")
        paths = b.get("paths")
        if not isinstance(paths, list) or not paths or len(paths) > 20:
            raise HTTPError(400, "Au moins un dossier est requis")
        clean = []
        for p in paths:
            p = os.path.normpath(str(p).strip())
            if not os.path.isabs(p):
                raise HTTPError(400, f"Chemin absolu attendu : {p}")
            if not os.path.isdir(p):
                raise HTTPError(400, f"Dossier introuvable depuis le serveur : {p}")
            if p not in clean:
                clean.append(p)
        return name, b["kind"], clean

    @app.route("GET", "/api/admin/libraries", auth="admin")
    def admin_libraries(req):
        return {"libraries": [library_view(l) for l in db.all("SELECT * FROM libraries ORDER BY position, name")],
                "scan": ctx.scanner.status()}

    @app.route("POST", "/api/admin/libraries", auth="admin")
    def admin_library_create(req):
        name, kind, paths = validate_library(req.json())
        pos = (db.scalar("SELECT MAX(position) FROM libraries") or 0) + 1
        lid = db.run("INSERT INTO libraries(name,kind,paths,position,created_at) VALUES(?,?,?,?,?)",
                     (name, kind, json.dumps(paths), pos, int(time.time()))).lastrowid
        db.release()
        ctx.scanner.start([lid])
        return library_view(db.one("SELECT * FROM libraries WHERE id=?", (lid,)))

    @app.route("PUT", r"/api/admin/libraries/(?P<lid>\d{1,6})", auth="admin")
    def admin_library_update(req):
        lid = int(req.params["lid"])
        l = db.one("SELECT * FROM libraries WHERE id=?", (lid,))
        if not l:
            raise HTTPError(404, "Bibliothèque introuvable")
        name, kind, paths = validate_library(req.json())
        if kind != l["kind"] and db.scalar("SELECT COUNT(*) FROM items WHERE library_id=?", (lid,)):
            db.run("DELETE FROM items WHERE library_id=?", (lid,))
        db.run("UPDATE libraries SET name=?, kind=?, paths=? WHERE id=?", (name, kind, json.dumps(paths), lid))
        db.release()
        ctx.scanner.start([lid])
        return library_view(db.one("SELECT * FROM libraries WHERE id=?", (lid,)))

    @app.route("DELETE", r"/api/admin/libraries/(?P<lid>\d{1,6})", auth="admin")
    def admin_library_delete(req):
        if ctx.scanner.status()["running"]:
            raise HTTPError(409, "Un scan est en cours. Réessayez une fois terminé.")
        db.run("DELETE FROM libraries WHERE id=?", (int(req.params["lid"]),))
        db.run("DELETE FROM user_libraries WHERE section_id=?", (req.params["lid"],))
        return {"ok": True}

    @app.route("POST", "/api/admin/scan", auth="admin")
    def admin_scan(req):
        b = req.json()
        lib = b.get("library")
        ids = [int(lib)] if lib and str(lib).isdigit() else None
        db.release()
        if not ctx.scanner.start(ids, refresh_metadata=bool(b.get("refresh_metadata"))):
            raise HTTPError(409, "Un scan est déjà en cours")
        return ctx.scanner.status()

    @app.route("GET", "/api/admin/scan", auth="admin")
    def admin_scan_status(req):
        return ctx.scanner.status()

    @app.route("GET", "/api/admin/browse", auth="admin")
    def admin_browse(req):
        path = os.path.normpath(req.arg("path") or "/")
        if not os.path.isabs(path) or not os.path.isdir(path):
            raise HTTPError(400, "Dossier introuvable")
        try:
            dirs = sorted((d for d in os.listdir(path) if not d.startswith(".") and
                           os.path.isdir(os.path.join(path, d))), key=str.lower)[:500]
        except OSError as e:
            raise HTTPError(400, f"Lecture impossible : {e.strerror}")
        return {"path": path, "parent": os.path.dirname(path) if path != "/" else None, "dirs": dirs}

    # ---------------------------------------------------- admin settings
    def webhook_folders(payload):
        """Dossiers cités par Radarr / Sonarr (vus depuis leur machine)."""
        out = []
        for obj, key in (("movie", "folderPath"), ("series", "path")):
            v = (payload.get(obj) or {}).get(key) if isinstance(payload.get(obj), dict) else None
            if isinstance(v, str) and v:
                out.append(v)
        for obj in ("movieFile", "episodeFile"):
            f = payload.get(obj)
            if isinstance(f, dict) and isinstance(f.get("path"), str) and f["path"]:
                out.append(os.path.dirname(f["path"]))
        for f in payload.get("episodeFiles") or []:
            if isinstance(f, dict) and isinstance(f.get("path"), str):
                out.append(os.path.dirname(f["path"]))
        return out[:50]

    def local_dirs(remote):
        """Retrouve le dossier local correspondant (chemins différents entre Radarr et Nexis) en comparant
        la fin du chemin : /movies/Inception (2010) -> /mnt/films/Inception (2010)."""
        # « .. » refusé : le webhook ne doit pas pouvoir désigner un dossier hors des bibliothèques
        parts = [p for p in remote.replace("\\", "/").split("/") if p and p not in (".", "..")]
        found = []
        for lib in db.all("SELECT id, paths FROM libraries"):
            for root in json.loads(lib["paths"]):
                real_root = os.path.realpath(root)
                for k in range(len(parts), 0, -1):
                    cand = os.path.join(root, *parts[-k:])
                    if not os.path.realpath(cand).startswith(os.path.join(real_root, "")):
                        continue
                    if os.path.isdir(cand):
                        found.append((lib["id"], cand))
                        break
                    # dossier supprimé : on le retrouve par les fichiers indexés dessous
                    like = like_prefix(cand)
                    if db.scalar("SELECT 1 FROM items WHERE library_id=? AND key LIKE ? ESCAPE '\\' LIMIT 1",
                                 (lib["id"], like)):
                        found.append((lib["id"], cand))
                        break
        return found

    def webhook_scope(payload):
        scope = {}
        for remote in webhook_folders(payload):
            for lib_id, d in local_dirs(remote):
                dirs = scope.setdefault(lib_id, set())
                dirs.add(d)
                dirs.add(os.path.dirname(d))
                for dp, dn, _ in os.walk(d):  # nouveau dossier : ses sous-dossiers (saisons…) aussi
                    dirs.update(os.path.join(dp, x) for x in dn)
        return scope

    @app.route("POST", "/api/hooks/scan", auth="webhook")
    def hook_scan(req):
        """Webhook pour Radarr / Sonarr / script : scan limité aux dossiers cités (regroupé sur 10 s)."""
        try:
            payload = req.json() or {}
        except HTTPError:
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        event = payload.get("eventType", "")
        if event == "Test":
            return {"ok": True, "message": "Nexis a bien reçu le test"}
        scope = webhook_scope(payload)
        ctx.watcher.request_scan(f"webhook {event}".strip(), scope=scope or None)
        return {"ok": True, "partial": bool(scope)}

    @app.route("GET", "/api/hooks/scan", auth="webhook")
    def hook_scan_get(req):
        ctx.watcher.request_scan("webhook")
        return {"ok": True}

    @app.route("POST", "/api/admin/webhook/regenerate", auth="admin")
    def admin_webhook_regenerate(req):
        db.set_setting("webhook_token", secrets.token_urlsafe(24))
        return admin_settings(req)

    @app.route("GET", "/api/admin/settings", auth="admin")
    def admin_settings(req):
        max_streams, max_transcodes = ctx.limits()
        return {"watch_interval": ctx.setting_int("watch_interval", 60),
                "subs_prefetch": db.setting("subs_prefetch", "1") == "1",
                "trickplay": db.setting("trickplay", "1") == "1",
                "downloads": db.setting("downloads", "1") == "1",
                "downmix_algo": db.setting("downmix_algo", "none"),
                "downmix_boost": float(db.setting("downmix_boost", "2") or 2),
                "subs_queue": len(ctx.subtitles.urgent) + len(ctx.subtitles.idle),
                "webhook_path": f"/api/hooks/scan?token={db.setting('webhook_token')}",
                "watch_events": ctx.watcher.events[:8],"tmdb_set": bool(db.setting("tmdb_key")), "language": db.setting("meta_language", "fr-FR"),
                "max_streams": max_streams,
                "max_transcodes": max_transcodes, "cpus": os.cpu_count(), "ffmpeg": media.available(),
                "hw_engine": db.setting("hw_engine", "none"), "hw_device": db.setting("hw_device", "/dev/dri/renderD128"),
                "hw_devices": transcoder.hw_devices(), "version": VERSION}

    @app.route("PUT", "/api/admin/settings", auth="admin")
    def admin_settings_put(req):
        b = req.json()
        if "tmdb_key" in b:
            key = str(b.get("tmdb_key") or "").strip()
            if key:
                if len(key) > 400 or not re.match(r"^[A-Za-z0-9._-]+$", key):
                    raise HTTPError(400, "Clé TMDB invalide")
                from ..metadata import TMDB
                try:
                    TMDB(key).check()
                except MetaError as e:
                    raise HTTPError(400, str(e))
            db.set_setting("tmdb_key", key or None)
        if "language" in b:
            if not re.match(r"^[a-z]{2}-[A-Z]{2}$", str(b["language"])):
                raise HTTPError(400, "Langue invalide (ex. fr-FR)")
            db.set_setting("meta_language", b["language"])
            db.set_setting("meta_country", b["language"][-2:])
        if "downmix_algo" in b:
            if b["downmix_algo"] not in DOWNMIX_ALGOS:
                raise HTTPError(400, "Algorithme de mixage inconnu")
            db.set_setting("downmix_algo", b["downmix_algo"])
        if "downmix_boost" in b:
            try:
                boost = float(b["downmix_boost"])
            except (TypeError, ValueError):
                raise HTTPError(400, "Gain invalide")
            if not 0.5 <= boost <= 4:
                raise HTTPError(400, "Gain : entre 0,5 et 4")
            db.set_setting("downmix_boost", f"{boost:g}")
        if "trickplay" in b:
            db.set_setting("trickplay", "1" if b["trickplay"] else "0")
            if b["trickplay"]:
                threading.Thread(target=ctx.prefetch_missing_subtitles, daemon=True).start()
        if "downloads" in b:
            db.set_setting("downloads", "1" if b["downloads"] else "0")
        if "subs_prefetch" in b:
            db.set_setting("subs_prefetch", "1" if b["subs_prefetch"] else "0")
            if b["subs_prefetch"]:
                for r in db.all("SELECT id, key, probe, file_size, file_mtime, duration_ms FROM items WHERE type IN ('movie','episode') "
                                "ORDER BY added_at DESC"):
                    if ctx.subtitles.has_work(r):
                        ctx.subtitles.enqueue(r["id"])
        if "hw_engine" in b:
            engine, device = b["hw_engine"], b.get("hw_device") or db.setting("hw_device", "/dev/dri/renderD128")
            if engine not in transcoder.HW_ENGINES:
                raise HTTPError(400, "Moteur de transcodage inconnu")
            if not re.match(r"^/dev/dri/renderD\d{3}$", str(device)):
                raise HTTPError(400, "Périphérique invalide (attendu : /dev/dri/renderD128…)")
            if engine != "none":
                try:  # on n'enregistre qu'un moteur qui marche vraiment sur cette machine
                    transcoder.hw_test({"engine": engine, "device": device}, ctx.transcoder.work_dir)
                except media.MediaError as e:
                    raise HTTPError(400, f"Test du transcodage matériel en échec : {e}")
            db.set_setting("hw_engine", engine)
            db.set_setting("hw_device", device)
        for k, lo, hi in (("max_streams", 1, 50), ("max_transcodes", 0, 16),
                          ("watch_interval", 0, 3600)):
            if k in b:
                try:
                    v = int(b[k])
                except (TypeError, ValueError):
                    raise HTTPError(400, f"Valeur invalide : {k}")
                if not lo <= v <= hi:
                    raise HTTPError(400, f"{k} doit être entre {lo} et {hi}")
                db.set_setting(k, v)
        return admin_settings(req)

    @app.route("GET", "/api/admin/streams", auth="admin")
    def admin_streams(req):
        rows = db.all("SELECT s.id, s.title, s.quality, s.position_ms, s.state, s.created_at, s.last_seen, s.params, "
                      "u.display_name AS user FROM streams s JOIN users u ON u.id=s.user_id "
                      "WHERE s.last_seen > ? ORDER BY s.created_at DESC", (int(time.time()) - 180,))
        for r in rows:
            p = json.loads(r.pop("params") or "{}")
            r["mode"] = p.get("mode")
            r["reasons"] = p.get("reasons", [])
            r.update(ctx.transcoder.stats(r["id"]))
            if p.get("video_codec"):
                r["source"] = f"{str(p['video_codec']).upper()} {p.get('src_height') or '?'}p"
        return rows

    @app.route("GET", "/api/admin/system", auth="admin")
    def admin_system(req):
        """Charge du serveur, pour comprendre une lecture qui saccade (mémoire, swap, ffmpeg en cours)."""
        load = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)
        return {**system_memory(), **ctx.transcoder.processes(), "cpus": os.cpu_count(), "load": round(load[0], 2)}

    @app.route("DELETE", f"/api/admin/streams/{SID}", auth="admin")
    def admin_stop_stream(req):
        stop_stream(req.params["sid"])
        return {"ok": True}

    @app.route("PUT", f"/api/admin/markers/{RK}", auth="admin")
    def admin_markers(req):
        rk = req.params["rk"]
        b = req.json()
        kind = b.get("kind")
        if kind not in ("intro", "credits"):
            raise HTTPError(400, "Type de marqueur invalide")
        if b.get("clear"):
            db.run("DELETE FROM markers WHERE rating_key=? AND kind=?", (rk, kind))
            return {"ok": True}
        try:
            start, end = int(b["start"]), int(b["end"])
        except (KeyError, TypeError, ValueError):
            raise HTTPError(400, "Début et fin requis (ms)")
        if not 0 <= start < end:
            raise HTTPError(400, "La fin doit être après le début")
        db.run("INSERT INTO markers(rating_key,kind,start_ms,end_ms,source) VALUES(?,?,?,?, 'manual') "
               "ON CONFLICT(rating_key,kind) DO UPDATE SET start_ms=excluded.start_ms, end_ms=excluded.end_ms, "
               "source='manual'", (rk, kind, start, end))
        return {"ok": True}

    # ---------------------------------------------------- mise à jour de Nexis
    @app.route("GET", "/api/admin/update", auth="admin")
    def admin_update(req):
        db.release()  # appels réseau : pas de connexion à la base gardée pendant ce temps
        out = {"current": VERSION}
        if req.arg("refresh") == "1":
            update.forget()  # bouton « Rechercher une mise à jour » : on ne se fie pas au résultat gardé 30 min
        try:
            out.update(update.check_latest())
            out["available"] = update.newer(out["latest"], VERSION)
            ctx.note_latest(out["latest"])
        except update.UpdateError as e:
            out["check_error"] = str(e)
        if IN_DOCKER:
            out.update(can_update=False, update_error="Avec Docker, la mise à jour passe par l'image : docker compose pull && docker compose up -d")
            return out
        try:
            out.update(mountd.call("update_status", sock_path=os.environ.get("SEANCE_MOUNTD_SOCK"), timeout=10))
            out["can_update"] = True
        except mountd.MountError as e:
            out.update(can_update=False, update_error=str(e))
        return out

    @app.route("POST", "/api/admin/update", auth="admin")
    def admin_update_start(req):
        log.warning("Mise à jour de Nexis lancée par %s", req.user["username"])
        return mounts("update")

    # ---------------------------------------------------- admin partages SMB
    def mounts(cmd, args=None):
        try:
            return mountd.call(cmd, args, sock_path=os.environ.get("SEANCE_MOUNTD_SOCK"))
        except mountd.MountError as e:
            raise HTTPError(503 if "pas démarré" in str(e) else 400, str(e))

    @app.route("GET", "/api/admin/shares", auth="admin")
    def admin_shares(req):
        db.release()
        try:
            st = mountd.call("status", sock_path=os.environ.get("SEANCE_MOUNTD_SOCK"), timeout=20)
        except mountd.MountError as e:
            if IN_DOCKER:
                e = "Avec Docker, les partages se montent sur l'hôte puis se transmettent au conteneur (volumes du docker-compose.yml)"
            return {"available": False, "error": str(e), "shares": [], "env": None}
        libs = db.all("SELECT id, name, paths FROM libraries")
        for sh in st["shares"]:
            sh["libraries"] = [l["name"] for l in libs if any(
                p == sh["path"] or p.startswith(sh["path"] + os.sep) for p in json.loads(l["paths"]))]
        st["available"] = True
        return st

    @app.route("POST", "/api/admin/shares/discover", auth="admin")
    def admin_shares_discover(req):
        b = req.json()
        db.release()
        return mounts("discover", {k: b.get(k) for k in ("host", "username", "password", "domain", "version", "reuse")})

    @app.route("POST", "/api/admin/shares", auth="admin")
    def admin_shares_add(req):
        b = req.json()
        db.release()
        return mounts("add", {k: b.get(k) for k in ("name", "host", "share", "subdir", "username", "password",
                                                     "domain", "version")})

    @app.route("POST", r"/api/admin/shares/(?P<name>[a-z0-9_-]{1,32})/remount", auth="admin")
    def admin_shares_remount(req):
        db.release()
        return mounts("remount", {"name": req.params["name"]})

    @app.route("DELETE", r"/api/admin/shares/(?P<name>[a-z0-9_-]{1,32})", auth="admin")
    def admin_shares_remove(req):
        db.release()
        return mounts("remove", {"name": req.params["name"]})

    # ------------------------------------------------- admin identification
    def root_item(r):
        if r["type"] in ("season", "episode"):
            return db.one("SELECT * FROM items WHERE id=?", (r["show_id"],))
        return db.one("SELECT * FROM items WHERE id=?", (r["id"],))

    @app.route("GET", f"/api/admin/identify/{RK}", auth="admin")
    def admin_identify_search(req):
        r = root_item(item_row(req.params["rk"]))
        tmdb = ctx.metadata.tmdb()
        if not tmdb.enabled:
            raise HTTPError(400, "Une clé TMDB est nécessaire (Paramètres → Métadonnées)")
        q = (req.arg("q") or r["title"]).strip()[:100]
        year = req.arg("year")
        try:
            res = tmdb.search("movie" if r["type"] == "movie" else "tv", q, int(year) if year and year.isdigit() else None)
        except MetaError as e:
            raise HTTPError(502, str(e))
        for x in res:
            x["poster"] = f"/api/admin/tmdb-img?p={x['poster']}" if x.get("poster") else None
        return {"query": q, "results": res, "current": r["tmdb_id"]}

    @app.route("POST", f"/api/admin/identify/{RK}", auth="admin")
    def admin_identify(req):
        r = root_item(item_row(req.params["rk"]))
        tid = req.json().get("tmdb_id")
        if not isinstance(tid, int) or tid <= 0:
            raise HTTPError(400, "Identifiant TMDB invalide")
        db.run("UPDATE items SET locked=1, tmdb_id=? WHERE id=?", (tid, r["id"]))
        try:
            ctx.metadata.refresh(db.one("SELECT * FROM items WHERE id=?", (r["id"],)), tmdb_id=tid)
        except MetaError as e:
            raise HTTPError(502, str(e))
        return {"ok": True, "id": str(r["id"])}

    @app.route("POST", f"/api/admin/refresh/{RK}", auth="admin")
    def admin_refresh(req):
        r = root_item(item_row(req.params["rk"]))
        try:
            state = ctx.metadata.refresh(r)
        except MetaError as e:
            raise HTTPError(502, str(e))
        return {"ok": True, "state": state}

    @app.route("GET", "/api/admin/tmdb-img", auth="admin")
    def admin_tmdb_img(req):
        p = req.arg("p") or ""
        if not re.match(r"^/[A-Za-z0-9]{5,60}\.(jpg|png)$", p):
            raise HTTPError(400, "Image invalide")
        try:
            src = ctx.images._download(p, "poster")
            dest = os.path.join(ctx.images.cache, "tmdbsearch_" + hashlib.sha1(p.encode()).hexdigest() + ".jpg")
            if not os.path.isfile(dest):
                media.resize_image(src, dest, 140, 210)
        except Exception:
            raise HTTPError(404, "Image indisponible")
        with open(dest, "rb") as f:
            return Response(f.read(), 200, "image/jpeg", {"Cache-Control": "private, max-age=86400"})

