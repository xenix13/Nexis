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


def main(argv=None):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(prog="seance", description="Séance — serveur multimédia auto-hébergé")
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
    server = Server((args.host, args.port), make_handler(app, ctx.trust_proxy))
    stop = threading.Event()
    threading.Thread(target=janitor, args=(ctx, stop), daemon=True).start()
    ctx.watcher.start()
    threading.Timer(60, ctx.prefetch_missing_subtitles).start()

    def shutdown(*_):
        stop.set()
        ctx.watcher.stop()
        ctx.subtitles.stop()
        ctx.transcoder.stop_all()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    logging.getLogger("seance").info("Séance écoute sur http://%s:%d", args.host, args.port)
    server.serve_forever()
    server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
