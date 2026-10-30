"""Comptes : installation, connexion, préférences, profils (« Qui regarde ? ») et code PIN."""
import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import time

from .. import __version__ as VERSION, i18n, media, twofa, update
from ..auth import (USERNAME, USERNAME_RULE, check_credentials, clear_session, create_session, hash_password,
                    normalize_username, validate_password, verify_password)
from ..catalog import HEX, validate_prefs
from ..web import HTTPError, Response


log = logging.getLogger("seance.account")
AVATAR_MAX = 500 * 1024


def avatar_url(u):
    ver = u.get("avatar_ver") if hasattr(u, "get") else None
    return f"/api/users/{u['id']}/avatar?v={ver}" if ver else None


def register(app, ctx, x):
    db = ctx.db
    # outils du catalogue partagé (seance/catalog.py)
    allowed_set, get_prefs, libraries_all = x.allowed_set, x.get_prefs, x.libraries_all

    # ---------------------------------------------------------- public / auth
    @app.route("GET", "/api/status", auth=None)
    def status(req):
        return {"setup": db.scalar("SELECT COUNT(*) FROM users") == 0,
                "libraries": db.scalar("SELECT COUNT(*) FROM libraries"),
                "ffmpeg": media.available(), "version": VERSION,
                "language": i18n.default_language(db), "languages": i18n.LANGUAGES}

    @app.route("GET", "/tv", auth=None)
    def tv_app(req):
        """Adresse courte à taper dans l'appli Downloader de la TV : renvoie vers le dernier APK publié."""
        try:
            owner, repo, _ = update.read_source()
        except update.UpdateError:
            owner, repo = "xenix13", "Nexis"
        return Response(b"", 302, "text/plain",
                        {"Location": f"https://github.com/{owner}/{repo}/releases/latest/download/nexis-tv.apk",
                         "Cache-Control": "no-store"})

    @app.route("POST", "/api/setup", auth="setup")
    def setup(req):
        if db.scalar("SELECT COUNT(*) FROM users"):
            raise HTTPError(409, "Déjà configuré")
        b = req.json()
        username = normalize_username(b.get("username"))
        if not USERNAME.match(username):
            raise HTTPError(400, USERNAME_RULE)
        validate_password(b.get("password"))
        lang = i18n.valid(b.get("language"))
        with db.tx():
            if db.scalar("SELECT COUNT(*) FROM users"):
                raise HTTPError(409, "Déjà configuré")
            if lang:  # langue choisie à l'installation : celle du serveur, donc des comptes à venir
                db.set_setting("default_language", lang)
                i18n.set_server_default(lang)
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
        keys = ("ip:" + req.client_ip, "u:" + normalize_username(username).casefold())
        ctx.throttle.check(*keys)
        user = check_credentials(db, username, password)
        if not user:
            ctx.throttle.fail(*keys)
            raise HTTPError(401, "Identifiant ou mot de passe incorrect")
        ctx.throttle.reset(keys[1])
        if user.get("totp_enabled"):
            # mot de passe correct : le code de l'application d'authentification est encore demandé
            return {"two_factor": True, "ticket": tickets.create(user["id"])}
        create_session(db, req, user["id"], ctx.cookie_secure)
        return {"ok": True}

    tickets = twofa.Tickets()

    @app.route("POST", "/api/login/2fa", auth=None)
    def login_second_factor(req):
        b = req.json()
        ticket = str(b.get("ticket") or "")[:64]
        uid = tickets.peek(ticket)
        if not uid:
            raise HTTPError(401, "Délai dépassé : saisissez à nouveau votre mot de passe")
        keys = ("ip:" + req.client_ip, f"2fa:{uid}")
        ctx.throttle.check(*keys)
        user = db.one("SELECT * FROM users WHERE id=? AND disabled=0", (uid,))
        how = twofa.verify_second_factor(db, user, b.get("code")) if user and user["totp_enabled"] else None
        if not how:
            ctx.throttle.fail(*keys)
            tickets.failed(ticket)
            raise HTTPError(401, "Code incorrect")
        tickets.consume(ticket)
        ctx.throttle.reset(keys[1])
        create_session(db, req, uid, ctx.cookie_secure)
        left = twofa.recovery_left(db.scalar("SELECT totp_recovery FROM users WHERE id=?", (uid,)))
        return {"ok": True, "recovery_used": how == "recovery", "recovery_left": left}

    @app.route("POST", "/api/logout", auth=None)
    def logout(req):
        clear_session(db, req, ctx.cookie_secure)
        return {"ok": True}

    # ------------------------------------------------------------------- me
    def user_view(u):
        return {"id": u["id"], "username": u["username"], "display_name": u["display_name"],
                "is_admin": bool(u["is_admin"]), "avatar_color": u["avatar_color"],
                "managed": bool(u.get("managed")), "has_pin": bool(u.get("pin_hash")),
                "max_age": u.get("max_age"), "home": bool(u.get("home", 1)), "kids": bool(u.get("kids")),
                "avatar": avatar_url(u), "two_factor": bool(u.get("totp_enabled")),
                "language": i18n.valid(u.get("language")) or i18n.default_language(db)}

    @app.route("GET", "/api/me")
    def me(req):
        allowed = allowed_set(req.user)
        warning = None if media.available() else i18n.tr("ffmpeg n'est pas installé sur le serveur : la lecture est impossible.")
        return {"user": user_view(req.user), "prefs": get_prefs(req.user["id"]),
                "libraries": [l for l in libraries_all() if l["id"] in allowed], "warning": warning,
                "features": {"discover": bool(db.setting("tmdb_key") or ctx.fed.provider("tmdb")),
                             "requests": bool(db.setting("seerr_url") and db.setting("seerr_key") or ctx.fed.provider("seerr")),
                             "live": x.live_available(req.user),
                             "downloads": db.setting("downloads", "1") == "1"},
                "update": ctx.update_latest if req.user["is_admin"] else None,
                "notifications": x.unread_notifications(req.user)}

    @app.route("PUT", "/api/me/prefs")
    def me_prefs(req):
        p = get_prefs(req.user["id"])
        p.update(validate_prefs(req.json()))
        db.run("INSERT INTO prefs(user_id,data) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",
               (req.user["id"], json.dumps(p)))
        return p

    @app.route("PUT", "/api/me/language")
    def me_language(req):
        lang = i18n.valid(req.json().get("language"))
        if not lang:
            raise HTTPError(400, "Langue invalide")
        db.run("UPDATE users SET language=? WHERE id=?", (lang, req.user["id"]))
        i18n.set_lang(lang)
        return {"language": lang}

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

    # ------------------------------------------------------------ avatar en image
    # Le navigateur envoie une image déjà recadrée (256 × 256) ; le serveur la réencode avec ffmpeg :
    # seule une vraie image passe, et les métadonnées (EXIF, position GPS des photos) disparaissent.
    avatar_dir = os.path.join(ctx.data_dir, "avatars")
    DEMUXERS = {b"\xff\xd8\xff": "jpeg_pipe", b"\x89PNG": "png_pipe", b"RIFF": "webp_pipe"}

    @app.route("PUT", "/api/me/avatar")
    def me_avatar(req):
        raw = req.json().get("image")
        try:
            data = base64.b64decode(str(raw or "").split(",")[-1], validate=True)
        except ValueError:
            raise HTTPError(400, "Image illisible")
        if not 100 <= len(data) <= AVATAR_MAX:
            raise HTTPError(400, "Image trop lourde (500 Ko au maximum)")
        demuxer = next((d for magic, d in DEMUXERS.items() if data.startswith(magic)), None)
        if not demuxer or (demuxer == "webp_pipe" and data[8:12] != b"WEBP"):
            raise HTTPError(400, "Format non pris en charge (JPEG, PNG ou WebP)")
        os.makedirs(avatar_dir, exist_ok=True)
        uid = req.user["id"]
        dest = os.path.join(avatar_dir, f"{uid}.jpg")
        with tempfile.TemporaryDirectory(dir=avatar_dir) as tmp:
            src, out = os.path.join(tmp, "in"), os.path.join(tmp, "out.jpg")
            with open(src, "wb") as f:
                f.write(data)
            cmd = [media.FFMPEG, "-hide_banner", "-loglevel", "error", *media.LOCAL_ONLY, "-f", demuxer, "-i", src,
                   "-frames:v", "1", "-vf", "scale=256:256:force_original_aspect_ratio=increase,crop=256:256",
                   "-map_metadata", "-1", "-q:v", "3", "-y", out]
            try:
                p = subprocess.run(cmd, capture_output=True, timeout=20, stdin=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                raise HTTPError(500, "Traitement de l'image impossible")
            if p.returncode != 0 or not os.path.isfile(out) or os.path.getsize(out) < 100:
                raise HTTPError(400, "Image illisible")
            os.replace(out, dest)
        ver = int(time.time())
        db.run("UPDATE users SET avatar_ver=? WHERE id=?", (ver, uid))
        return {"avatar": avatar_url({"id": uid, "avatar_ver": ver})}

    @app.route("DELETE", "/api/me/avatar")
    def me_avatar_delete(req):
        remove_avatar(req.user["id"])
        return {"ok": True}

    @app.route("GET", r"/api/users/(?P<uid>\d{1,10})/avatar")
    def user_avatar(req):
        try:
            with open(os.path.join(avatar_dir, f"{int(req.params['uid'])}.jpg"), "rb") as f:
                data = f.read()
        except OSError:
            raise HTTPError(404, "Pas d'image")
        # adresse versionnée (?v=…) : une nouvelle image a une nouvelle adresse
        return Response(data, 200, "image/jpeg", {"Cache-Control": "private, max-age=31536000, immutable"})

    def remove_avatar(uid):
        try:
            os.remove(os.path.join(avatar_dir, f"{int(uid)}.jpg"))
        except OSError:
            pass
        db.run("UPDATE users SET avatar_ver=NULL WHERE id=?", (uid,))
    x.remove_avatar = remove_avatar

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
            v["needs_code"] = bool(u["totp_enabled"])
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
                    raise HTTPError(403, "Ce profil n'a pas de code PIN : il doit en définir un, ou être ouvert avec son mot de passe")
                ok = True
            else:
                ok = verify_password(str(b.get("password") or "")[:256], target["password_hash"])
            if not ok:
                ctx.throttle.fail(key, "ip:" + req.client_ip)
                raise HTTPError(400, "Code PIN incorrect" if target["pin_hash"] else "Mot de passe incorrect")
            if target["totp_enabled"] and not twofa.verify_second_factor(db, target, b.get("code")):
                ctx.throttle.fail(key, "ip:" + req.client_ip)
                raise HTTPError(400, "Code de double authentification incorrect")
            ctx.throttle.reset(key)
            clear_session(db, req, ctx.cookie_secure)
            req.set_cookies.clear()
            create_session(db, req, uid, ctx.cookie_secure)
        return {"ok": True, "user": user_view(target)}

    # ------------------------------------------------------------ double authentification
    def two_factor_account(req):
        u = db.one("SELECT * FROM users WHERE id=?", (req.user["id"],))
        if u["managed"] or u["password_hash"] == "!":
            raise HTTPError(403, "La double authentification concerne les comptes qui se connectent avec un mot de passe")
        return u

    @app.route("GET", "/api/me/2fa")
    def me_2fa(req):
        u = db.one("SELECT * FROM users WHERE id=?", (req.user["id"],))
        return {"available": not u["managed"] and u["password_hash"] != "!", "enabled": bool(u["totp_enabled"]),
                "recovery_left": twofa.recovery_left(u["totp_recovery"]) if u["totp_enabled"] else 0}

    @app.route("POST", "/api/me/2fa/setup")
    def me_2fa_setup(req):
        u = two_factor_account(req)
        b = req.json()
        key = f"pw:{u['id']}"
        ctx.throttle.check(key)
        if not verify_password(str(b.get("password") or "")[:256], u["password_hash"]):
            ctx.throttle.fail(key)
            raise HTTPError(400, "Mot de passe incorrect")
        if u["totp_enabled"]:
            raise HTTPError(409, "La double authentification est déjà activée")
        secret = twofa.new_secret()
        db.run("UPDATE users SET totp_pending=? WHERE id=?", (secret, u["id"]))
        return {"secret": secret, "uri": twofa.otpauth_uri(secret, u["username"])}

    @app.route("POST", "/api/me/2fa/enable")
    def me_2fa_enable(req):
        u = two_factor_account(req)
        step = twofa.check_code(u["totp_pending"], req.json().get("code"))
        if not u["totp_pending"] or not step:
            raise HTTPError(400, "Code incorrect : vérifiez l'heure du téléphone et saisissez le code affiché")
        codes, hashed = twofa.new_recovery_codes()
        db.run("UPDATE users SET totp_enabled=1, totp_secret=totp_pending, totp_pending=NULL, totp_last_step=?, "
               "totp_recovery=? WHERE id=?", (step, hashed, u["id"]))
        # les autres sessions de ce compte (autres appareils) devront passer par la double authentification
        db.run("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (u["id"], req.session_hash))
        log.warning("Double authentification activée pour %s", u["username"])
        return {"enabled": True, "recovery_codes": codes}

    @app.route("POST", "/api/me/2fa/disable")
    def me_2fa_disable(req):
        u = two_factor_account(req)
        b = req.json()
        key = f"pw:{u['id']}"
        ctx.throttle.check(key)
        if not verify_password(str(b.get("password") or "")[:256], u["password_hash"]) or \
                not twofa.verify_second_factor(db, u, b.get("code")):
            ctx.throttle.fail(key)
            raise HTTPError(400, "Mot de passe ou code incorrect")
        twofa.disable(db, u["id"])
        log.warning("Double authentification désactivée par %s", u["username"])
        return {"enabled": False}

    @app.route("POST", "/api/me/2fa/recovery")
    def me_2fa_recovery(req):
        u = two_factor_account(req)
        if not u["totp_enabled"]:
            raise HTTPError(400, "La double authentification n'est pas activée")
        key = f"pw:{u['id']}"
        ctx.throttle.check(key)
        if not twofa.verify_second_factor(db, u, req.json().get("code")):
            ctx.throttle.fail(key)
            raise HTTPError(400, "Code incorrect")
        codes, hashed = twofa.new_recovery_codes()
        db.run("UPDATE users SET totp_recovery=? WHERE id=?", (hashed, u["id"]))
        return {"recovery_codes": codes}

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
