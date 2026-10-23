"""Client web officiel de Jellyfin (jellyfin-web), servi sous /web/.

Jellyfin Media Player (Windows, macOS, Linux) et l'application Android pour téléphone affichent l'interface
web fournie par le serveur : sans elle, ils ne peuvent pas se connecter. Nexis ne l'embarque pas dans son
dépôt ; deploy/install.sh la récupère depuis le dépôt APT signé de Jellyfin.
"""
import mimetypes
import os
import urllib.parse

from ..web import HTTPError, Response

CANDIDATES = ("/opt/seance/jellyfin-web", "/usr/share/jellyfin/web", "/usr/lib/jellyfin/bin/jellyfin-web")
# l'interface Jellyfin a besoin de styles et de scripts en ligne, de workers et de WebAssembly (sous-titres ASS)
WEB_CSP = ("default-src 'self'; img-src 'self' data: blob: https:; media-src 'self' blob: data:; "
           "script-src 'self' 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval' blob:; style-src 'self' 'unsafe-inline'; "
           "font-src 'self' data:; connect-src 'self' ws: wss: data: blob:; worker-src 'self' blob:; "
           "frame-src 'self' https://www.youtube.com https://www.youtube-nocookie.com; frame-ancestors 'self'; "
           "base-uri 'self'; object-src 'none'")


def find_web_dir():
    env = os.environ.get("SEANCE_JELLYFIN_WEB")
    for d in ([env] if env else []) + list(CANDIDATES):
        if d and os.path.isfile(os.path.join(d, "index.html")):
            return os.path.realpath(d)
    return None


def version_of(root):
    if not root:
        return None
    try:
        with open(os.path.join(root, "seance-version.txt"), encoding="utf-8") as f:
            return f.read().strip()[:40] or None
    except OSError:
        return None


def serve(req, root):
    if not root:
        raise HTTPError(404, "Le client web Jellyfin n'est pas installé (relance deploy/install.sh)")
    path = urllib.parse.unquote(req.path)
    if path.lower() in ("/web", "/web/"):
        rel = "index.html"
    else:
        rel = path[5:].lstrip("/") or "index.html"
    full = os.path.realpath(os.path.join(root, rel))
    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        raise HTTPError(404, "Not found")
    ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
        ctype += "; charset=utf-8"
    with open(full, "rb") as f:
        data = f.read()
    cache = "no-cache" if full.endswith((".html", ".json")) else "public, max-age=604800"
    if path.lower() == "/web":
        return Response(b"", 302, "text/plain", {"Location": "/web/"})
    return Response(data, 200, ctype, {"Cache-Control": cache, "Content-Security-Policy": WEB_CSP,
                                       "X-Frame-Options": "SAMEORIGIN", "Cross-Origin-Opener-Policy": None})
