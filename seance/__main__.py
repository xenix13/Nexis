"""Point d'entrée : python3 -m seance"""
import argparse
import logging
import os
import signal
import sys
import threading

from .api import Ctx, build_app, janitor
from .web import Server, make_handler


def env_bool(name, default=False):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def admin_command(argv):
    """Commandes de secours, sur le serveur (accès console) :
    reset-2fa <identifiant>  désactive la double authentification d'un compte (téléphone et codes de secours perdus)
    list-users               liste les comptes et l'état de leur double authentification"""
    from .db import DB
    from . import twofa
    ap = argparse.ArgumentParser(prog="nexis", description="Commandes d'administration de Nexis (en console)")
    ap.add_argument("--data", default=os.environ.get("SEANCE_DATA", "/var/lib/seance"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reset-2fa", help="désactiver la double authentification d'un compte")
    r.add_argument("username")
    sub.add_parser("list-users", help="lister les comptes")
    args = ap.parse_args(argv)
    path = os.path.join(args.data, "seance.db")
    if not os.path.isfile(path):
        print(f"Base introuvable : {path} (option --data)", file=sys.stderr)
        return 2
    db = DB(path)
    if args.cmd == "list-users":
        for u in db.all("SELECT username, display_name, is_admin, disabled, totp_enabled FROM users ORDER BY username"):
            flags = [f for f, on in (("administrateur", u["is_admin"]), ("désactivé", u["disabled"]),
                                     ("double authentification", u["totp_enabled"])) if on]
            print(f"{u['username']:<24} {u['display_name']:<24} {', '.join(flags)}")
        return 0
    from .auth import find_user
    u = find_user(db, args.username)
    if not u:
        print(f"Compte introuvable : {args.username} (voir : nexis list-users)", file=sys.stderr)
        return 1
    if not u["totp_enabled"]:
        print(f"La double authentification n'est pas activée pour {u['username']}.")
        return 0
    twofa.disable(db, u["id"])
    logging.getLogger("seance").warning("Double authentification de %s réinitialisée en console", u["username"])
    print(f"Double authentification désactivée pour {u['username']}. Connexion possible avec le mot de passe seul ; "
          "elle peut être réactivée dans Paramètres → Profil.")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if any(a in ("reset-2fa", "list-users") for a in argv):
        return admin_command(argv)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(prog="seance", description="Nexis — serveur multimédia auto-hébergé")
    ap.add_argument("--host", default=os.environ.get("SEANCE_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("SEANCE_PORT", "8420")))
    ap.add_argument("--data", default=os.environ.get("SEANCE_DATA", os.path.join(here, "data")))
    ap.add_argument("--static", default=os.environ.get("SEANCE_STATIC", os.path.join(here, "static")))
    ap.add_argument("--debug", action="store_true", default=env_bool("SEANCE_DEBUG"))
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    os.makedirs(args.data, mode=0o700, exist_ok=True)
    ctx = Ctx(args.data, args.static,
              cookie_secure=env_bool("SEANCE_COOKIE_SECURE"),
              trust_proxy=env_bool("SEANCE_TRUST_PROXY"))
    app = build_app(ctx)
    handler = make_handler(app, ctx.trust_proxy, hsts=ctx.cookie_secure)
    server = Server((args.host, args.port), handler)
    stop = threading.Event()
    threading.Thread(target=janitor, args=(ctx, stop), daemon=True).start()
    ctx.watcher.start()
    ctx.tasks.start()
    threading.Timer(60, ctx.prefetch_missing_subtitles).start()

    def shutdown(*_):
        stop.set()
        ctx.watcher.stop()
        ctx.tasks.stop()
        ctx.subtitles.stop()
        ctx.transcoder.stop_all()
        ctx.live.stop_all()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    logging.getLogger("seance").info("Nexis écoute sur http://%s:%d", args.host, args.port)
    server.serve_forever()
    server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
