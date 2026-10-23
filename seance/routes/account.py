"""Comptes : installation, connexion, préférences, profils (« Qui regarde ? ») et code PIN."""
import json
import os
import re
import time

from .. import __version__ as VERSION, media
from ..auth import check_credentials, clear_session, create_session, hash_password, validate_password, verify_password
from ..catalog import HEX, USERNAME, validate_prefs
from ..web import HTTPError


def register(app, ctx, x):
    db = ctx.db
    # outils du catalogue partagé (seance/catalog.py)
    allowed_set, get_prefs, libraries_all = x.allowed_set, x.get_prefs, x.libraries_all

    # ---------------------------------------------------------- public / auth
    @app.route("GET", "/api/status", auth=None)
    def status(req):
        return {"setup": db.scalar("SELECT COUNT(*) FROM users") == 0,
                "libraries": db.scalar("SELECT COUNT(*) FROM libraries"),
                "ffmpeg": media.available(), "version": VERSION}

    @app.route("POST", "/api/setup", auth="setup")
    def setup(req):
        if db.scalar("SELECT COUNT(*) FROM users"):
            raise HTTPError(409, "Déjà configuré")
        b = req.json()
        username = (b.get("username") or "").strip()
        if not USERNAME.match(username):
            raise HTTPError(400, "Identifiant : 2 à 32 caractères (lettres, chiffres, . _ -)")
        validate_password(b.get("password"))
        with db.tx():
            if db.scalar("SELECT COUNT(*) FROM users"):
                raise HTTPError(409, "Déjà configuré")
            uid = db.run("INSERT INTO users(username,display_name,password_hash,is_admin,created_at) "
                         "VALUES(?,?,?,1,?)", (username, (b.get("display_name") or username)[:50],
                                               hash_password(b["password"]), int(time.time()))).lastrowid
        ctx.setup_code = None
        try:
            os.remove(os.path.join(ctx.data_dir, "setup-code.txt"))
        except OSError:
            pass
        create_session(db, req, uid, ctx.cookie_secure)
        return {"ok": True}

    @app.route("POST", "/api/login", auth=None)
    def login(req):
        b = req.json()
        username = str(b.get("username") or "").strip()[:64]
        password = str(b.get("password") or "")[:256]
        keys = ("ip:" + req.client_ip, "u:" + username.lower())
        ctx.throttle.check(*keys)
        user = check_credentials(db, username, password)
        if not user:
            ctx.throttle.fail(*keys)
            raise HTTPError(401, "Identifiant ou mot de passe incorrect")
        ctx.throttle.reset(keys[1])
        create_session(db, req, user["id"], ctx.cookie_secure)
        return {"ok": True}

    @app.route("POST", "/api/logout", auth=None)
    def logout(req):
        clear_session(db, req, ctx.cookie_secure)
        return {"ok": True}

    # ------------------------------------------------------------------- me
    def user_view(u):
        return {"id": u["id"], "username": u["username"], "display_name": u["display_name"],
                "is_admin": bool(u["is_admin"]), "avatar_color": u["avatar_color"],
                "managed": bool(u.get("managed")), "has_pin": bool(u.get("pin_hash")),
                "max_age": u.get("max_age"), "home": bool(u.get("home", 1))}

    @app.route("GET", "/api/me")
    def me(req):
        allowed = allowed_set(req.user)
        warning = None if media.available() else "ffmpeg n'est pas installé sur le serveur : la lecture est impossible."
        return {"user": user_view(req.user), "prefs": get_prefs(req.user["id"]),
                "libraries": [l for l in libraries_all() if l["id"] in allowed], "plex_error": warning,
                "features": {"discover": bool(db.setting("tmdb_key")),
                             "requests": bool(db.setting("seerr_url") and db.setting("seerr_key")),
                             "live": x.live_available(req.user)}}

    @app.route("PUT", "/api/me/prefs")
    def me_prefs(req):
        p = get_prefs(req.user["id"])
        p.update(validate_prefs(req.json()))
        db.run("INSERT INTO prefs(user_id,data) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",
               (req.user["id"], json.dumps(p)))
        return p

    @app.route("PUT", "/api/me/profile")
    def me_profile(req):
        b = req.json()
        name = str(b.get("display_name") or "").strip()
        color = b.get("avatar_color")
        if not 1 <= len(name) <= 50:
            raise HTTPError(400, "Nom affiché : 1 à 50 caractères")
        if not (isinstance(color, str) and HEX.match(color)):
            raise HTTPError(400, "Couleur invalide")
        db.run("UPDATE users SET display_name=?, avatar_color=? WHERE id=?", (name, color, req.user["id"]))
        return {"ok": True}

    @app.route("PUT", "/api/me/password")
    def me_password(req):
        b = req.json()
        row = db.one("SELECT password_hash FROM users WHERE id=?", (req.user["id"],))
        if not verify_password(str(b.get("current") or ""), row["password_hash"]):
            raise HTTPError(400, "Mot de passe actuel incorrect")
        validate_password(b.get("new"))
        db.run("UPDATE users SET password_hash=? WHERE id=?", (hash_password(b["new"]), req.user["id"]))
        db.run("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (req.user["id"], req.session_hash))
        return {"ok": True}

    # ------------------------------------------------------------ profils
    def hash_pin(pin):
        if pin in (None, ""):
            return None
        if not (isinstance(pin, str) and re.match(r"^\d{4,8}$", pin)):
            raise HTTPError(400, "Le code PIN doit faire 4 à 8 chiffres")
        return hash_password(pin)

    @app.route("GET", "/api/profiles")
    def profiles(req):
        if not req.user.get("home"):
            return []
        rows = db.all("SELECT * FROM users WHERE home=1 AND disabled=0 ORDER BY managed, display_name COLLATE NOCASE")
        out = []
        for u in rows:
            v = user_view(u)
            v["current"] = u["id"] == req.user["id"]
            # sans PIN, un compte complet (avec mot de passe) ou administrateur demande son mot de passe
            v["needs"] = "pin" if u["pin_hash"] else ("none" if u["managed"] and not u["is_admin"] else "password")
            out.append(v)
        return out

    def less_restricted(target, current):
        """Le profil visé voit-il plus de choses que le profil actuel (âge, contenus non classés, bibliothèques) ?"""
        if target["max_age"] is None or target["max_age"] > current["max_age"]:
            return True
        if target["allow_unrated"] and not current.get("allow_unrated"):
            return True
        if target["all_libraries"] and not current.get("all_libraries"):
            return True
        if not target["all_libraries"]:
            mine = {r["section_id"] for r in db.all("SELECT section_id FROM user_libraries WHERE user_id=?",
                                                     (current["id"],))}
            theirs = {r["section_id"] for r in db.all("SELECT section_id FROM user_libraries WHERE user_id=?",
                                                       (target["id"],))}
            return not current.get("all_libraries") and not theirs <= mine
        return False

    @app.route("POST", r"/api/profiles/(?P<uid>\d{1,10})/switch")
    def profile_switch(req):
        uid = int(req.params["uid"])
        if not req.user.get("home"):
            raise HTTPError(403, "Ce compte ne fait pas partie du foyer")
        target = db.one("SELECT * FROM users WHERE id=? AND home=1 AND disabled=0", (uid,))
        if not target:
            raise HTTPError(404, "Profil introuvable")
        if uid != req.user["id"]:
            b = req.json()
            key = f"profile:{uid}"
            ctx.throttle.check(key, "ip:" + req.client_ip)
            if target["pin_hash"]:
                ok = verify_password(str(b.get("pin") or "")[:8], target["pin_hash"])
            elif target["managed"] and not target["is_admin"]:
                # profil ouvert (sans PIN) : un profil limité par âge ne peut pas s'en servir pour lever sa limite
                if req.user.get("max_age") is not None and less_restricted(target, req.user):
                    raise HTTPError(403, "Ce profil n'a pas de code PIN : un adulte doit en définir un, ou l'ouvrir lui-même")
                ok = True
            else:
                ok = verify_password(str(b.get("password") or "")[:256], target["password_hash"])
            if not ok:
                ctx.throttle.fail(key, "ip:" + req.client_ip)
                raise HTTPError(400, "Code PIN incorrect" if target["pin_hash"] else "Mot de passe incorrect")
            ctx.throttle.reset(key)
            clear_session(db, req, ctx.cookie_secure)
            req.set_cookies.clear()
            create_session(db, req, uid, ctx.cookie_secure)
        return {"ok": True, "user": user_view(target)}

    @app.route("PUT", "/api/me/pin")
    def me_pin(req):
        b = req.json()
        if not req.user["managed"]:
            row = db.one("SELECT password_hash FROM users WHERE id=?", (req.user["id"],))
            if not verify_password(str(b.get("password") or ""), row["password_hash"]):
                raise HTTPError(400, "Mot de passe actuel incorrect")
        db.run("UPDATE users SET pin_hash=? WHERE id=?", (hash_pin(b.get("pin")), req.user["id"]))
        return {"ok": True, "has_pin": bool(b.get("pin"))}

    x.__dict__.update(user_view=user_view, hash_pin=hash_pin)
