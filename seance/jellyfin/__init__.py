"""Compatibilité avec les applications Jellyfin (Jellyfin Media Player sur Windows, Android TV, téléphones…).

Nexis se présente comme un serveur Jellyfin 10.11 : les applications officielles s'y connectent avec un
compte Nexis, parcourent les bibliothèques et lisent les vidéos (lecture directe, remux ou transcodage HLS),
avec la reprise, les vus, les favoris (« Ma liste »), les sous-titres et le saut d'intro.

Désactivée par défaut : l'administrateur l'active dans Administration > Applications Jellyfin.
L'administration du serveur reste dans Nexis (les comptes n'apparaissent jamais administrateurs côté Jellyfin).
"""
from ..web import HTTPError
from . import library, playback, system, webclient
from .common import JF

JELLYFIN_ROOTS = (
    "Artists", "Audio", "Auth", "Backup", "Branding", "Channels", "ClientLog", "Collections", "Devices",
    "DisplayPreferences", "Dlna", "Environment", "FallbackFont", "GetUtcTime", "Genres", "Images", "Items",
    "Library", "LiveTv", "Localization", "Lyrics", "MediaInfo", "MediaSegments", "Movies", "MusicGenres",
    "Notifications", "Packages", "Persons", "Playback", "PlayingItems", "Playlists", "Plugins", "Providers",
    "QuickConnect", "Repositories", "ScheduledTasks", "Search", "Sessions", "Shows", "socket", "Songs", "Startup",
    "Studios", "SyncPlay", "System", "Tmdb", "Trailers", "Trickplay", "UserFavoriteItems", "UserItems",
    "UserPlayedItems", "Users", "UserViews", "Videos", "Years", "embywebsocket",
)


def register(app, ctx, x):
    jf = JF(ctx, x)
    ctx.jf = jf
    db = ctx.db
    web_dir = webclient.find_web_dir()
    app.compat_enabled = jf.enabled
    app.compat_static = ("/web", lambda req: webclient.serve(req, web_dir))
    for module in (system, library, playback):
        module.register(app, ctx, x, jf)

    # le reste de l'API Jellyfin (fonctions absentes de Nexis) : une erreur JSON, jamais la page de Nexis
    def not_found(req):
        jf.require_enabled()
        raise HTTPError(404, "Not found")
    rest = r"/(?:{})(?:/.*)?".format("|".join(JELLYFIN_ROOTS))
    for method in ("GET", "POST", "PUT", "DELETE"):
        app.compat_route(method, rest)(not_found)

    # ------------------------------------------------------------ administration (côté Nexis)
    @app.route("GET", "/api/admin/jellyfin", auth="admin")
    def jf_admin(req):
        rows = db.all("SELECT t.token_hash, t.app, t.device, t.device_id, t.version, t.ip, t.created_at, t.last_used, "
                      "u.username FROM jf_tokens t JOIN users u ON u.id=t.user_id WHERE t.kind='session' "
                      "ORDER BY t.last_used DESC LIMIT 200")
        return {"enabled": jf.enabled(), "server_id": jf.server_id(), "server_name": jf.server_name(),
                "web_client": {"installed": bool(web_dir), "version": webclient.version_of(web_dir)},
                "discovery": bool(getattr(ctx, "jf_discovery", None)),
                "devices": [{"id": r["token_hash"][:16], "app": r["app"], "version": r["version"],
                             "device": r["device"], "user": r["username"], "ip": r["ip"],
                             "created_at": r["created_at"], "last_used": r["last_used"]} for r in rows]}

    @app.route("PUT", "/api/admin/jellyfin", auth="admin")
    def jf_admin_set(req):
        b = req.json()
        if "enabled" in b:
            if not isinstance(b["enabled"], bool):
                raise HTTPError(400, "Valeur invalide")
            db.set_setting("jellyfin_api", "1" if b["enabled"] else "0")
            if not b["enabled"]:
                for s in db.all("SELECT id FROM streams WHERE plex_session LIKE 'jf:%'"):
                    x.stop_stream(s["id"])
        return jf_admin(req)

    @app.route("DELETE", "/api/admin/jellyfin/devices/(?P<tid>[0-9a-f]{16})", auth="admin")
    def jf_admin_revoke(req):
        tid = req.params["tid"]
        for s in db.all("SELECT id FROM streams WHERE plex_session=?", ("jf:" + tid,)):
            x.stop_stream(s["id"])
        db.run("DELETE FROM jf_tokens WHERE substr(token_hash,1,16)=?", (tid,))
        return {"ok": True}

    def revoke_user(uid):
        """Mot de passe changé, compte désactivé : les applications Jellyfin de ce compte sont déconnectées."""
        for t in db.all("SELECT token_hash FROM jf_tokens WHERE user_id=?", (uid,)):
            for s in db.all("SELECT id FROM streams WHERE plex_session=?", ("jf:" + t["token_hash"][:16],)):
                x.stop_stream(s["id"])
        db.run("DELETE FROM jf_tokens WHERE user_id=?", (uid,))
    x.jf_revoke_user = revoke_user
