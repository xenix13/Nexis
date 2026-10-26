"""Minimal HTTP framework on top of the standard library (no dependencies)."""
import gzip
import hashlib
import http.server
import json
import logging
import mimetypes
import os
import re
import socketserver
import threading
import time
import traceback
import urllib.parse

log = logging.getLogger("seance.web")

MAX_BODY = 1024 * 1024
IDLE_TIMEOUT = 120   # s sans rien recevoir ni pouvoir envoyer : connexion fermée (clients lents, « slowloris »)


class HTTPError(Exception):
    def __init__(self, status, message="Erreur", data=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.data = data


class Request:
    def __init__(self, handler, method, path, query, headers, client_ip):
        self.handler = handler
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.client_ip = client_ip
        self.user = None
        self.session_hash = None
        self.params = {}
        self._body = None
        self.set_cookies = []

    def arg(self, name, default=None):
        v = self.query.get(name)
        return v[0] if v else default

    def int_arg(self, name, default=0, lo=None, hi=None):
        try:
            v = int(self.arg(name, default))
        except (TypeError, ValueError):
            raise HTTPError(400, f"Paramètre invalide : {name}")
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return v

    @property
    def body(self):
        if self._body is None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise HTTPError(400, "Content-Length invalide")
            if length < 0:
                raise HTTPError(400, "Content-Length invalide")
            if length > MAX_BODY:
                raise HTTPError(413, "Requête trop volumineuse")
            self._body = self.handler.rfile.read(length) if length else b""
        return self._body

    def json(self):
        if not self.body:
            return {}
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            raise HTTPError(400, "JSON invalide")
        if not isinstance(data, dict):
            raise HTTPError(400, "Objet JSON attendu")
        return data

    @property
    def cookies(self):
        out = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                out[k] = v
        return out


class Response:
    def __init__(self, body=b"", status=200, content_type="application/octet-stream",
                 headers=None, stream=None, length=None, on_close=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.body = body
        self.status = status
        self.headers = {"Content-Type": content_type}
        if headers:
            self.headers.update(headers)
        self.stream = stream
        self.length = length
        self.on_close = on_close


def json_response(data, status=200):
    return Response(json.dumps(data, ensure_ascii=False, separators=(",", ":")), status,
                    "application/json; charset=utf-8", {"Cache-Control": "no-store"})


class App:
    def __init__(self):
        self.routes = []
        self.plain_paths = set()  # routes hors /api/ (adresses fixes, ex. /tv), le reste est l'interface
        self.before = []  # callables(req, route_opts)
        self.static_dir = None
        self.teardown = []

    def route(self, method, pattern, **opts):
        regex = re.compile("^" + pattern + "$")
        if not pattern.startswith("/api/"):
            self.plain_paths.add(pattern)

        def deco(fn):
            self.routes.append((method, regex, fn, opts))
            return fn
        return deco

    def dispatch(self, req):
        if req.path.startswith("/api/") or req.path in self.plain_paths:
            allowed = False
            for method, regex, fn, opts in self.routes:
                m = regex.match(req.path)
                if not m:
                    continue
                allowed = True
                if method != req.method:
                    continue
                req.params = m.groupdict()
                for hook in self.before:
                    hook(req, opts)
                result = fn(req)
                if isinstance(result, Response):
                    return result
                return json_response(result if result is not None else {"ok": True})
            if allowed:
                raise HTTPError(405, "Méthode non autorisée")
            raise HTTPError(404, "Introuvable")
        if req.method not in ("GET", "HEAD"):
            raise HTTPError(405, "Méthode non autorisée")
        return self.serve_static(req.path, req)

    def serve_static(self, path, req=None):
        root = os.path.realpath(self.static_dir)
        rel = urllib.parse.unquote(path).lstrip("/") or "index.html"
        # /v/<empreinte>/js/… : le code de l'interface, à une adresse qui change avec chaque version (voir ui_version)
        stamped = None
        m = STAMPED.match(rel)
        if m:
            stamped, rel = m.group(1), m.group(2)
        full = os.path.realpath(os.path.join(root, rel))
        if rel == "sw.js" and os.path.isfile(full):
            return self.service_worker(root, full, req)
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            if "." in os.path.basename(rel):
                raise HTTPError(404, "Fichier introuvable")
            full = os.path.join(root, "index.html")  # SPA fallback
        if full == os.path.join(root, "index.html"):
            return self.index_page(root, full, req)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        st = os.stat(full)
        # empreinte du fichier : après le délai de cache, le navigateur demande « inchangé ? » (304, rien à renvoyer)
        etag = f'W/"{st.st_size:x}-{st.st_mtime_ns:x}"'
        # interface (HTML, JS, CSS) : toujours revérifiée (réponse 304 légère si rien n'a changé), sinon le
        # navigateur garde l'ancienne version après une mise à jour du serveur. Images : 5 min sans redemander.
        cache = "no-cache" if full.endswith((".html", ".js", ".css", ".webmanifest")) else "public, max-age=300"
        headers = {"Cache-Control": cache, "ETag": etag}
        if "/vendor/" in full:
            headers["Cache-Control"] = "public, max-age=604800"
        if stamped and stamped == ui_version(root):
            # adresse propre à cette version : gardée telle quelle par le navigateur (et par un proxy qui
            # mettrait les .js en cache) ; la version suivante aura d'autres adresses
            headers["Cache-Control"] = "public, max-age=31536000, immutable"
        if "/vendor/jassub/" in full:
            # le moteur de sous-titres (libass compilé en WebAssembly) tourne dans un worker qui a besoin
            # de compiler du WebAssembly ; cette politique ne vaut que pour ses propres fichiers
            headers["Content-Security-Policy"] = JASSUB_CSP
        compressible = ctype.startswith(COMPRESSIBLE)
        if compressible:
            headers["Vary"] = "Accept-Encoding"
        if req and req.headers.get("If-None-Match") == etag:
            return Response(b"", 304, ctype, headers)
        data = _gzipped(full, etag) if compressible and req and "gzip" in (req.headers.get("Accept-Encoding") or "") else None
        if data is not None:
            headers["Content-Encoding"] = "gzip"
        else:
            with open(full, "rb") as f:
                data = f.read()
        return Response(data, 200, ctype, headers)


    @staticmethod
    def index_page(root, full, req):
        """index.html qui charge le code et le style de l'interface à des adresses propres à cette version.
        Sans cela, un proxy (Nginx Proxy Manager « Cache Assets » : expires 30d) ou un cache trop zélé garde
        l'ancien code après une mise à jour, jusqu'à un Ctrl+F5."""
        version = ui_version(root)
        st = os.stat(full)
        etag = f'W/"ix-{version}-{st.st_mtime_ns:x}"'
        headers = {"Cache-Control": "no-cache", "ETag": etag, "Vary": "Accept-Encoding"}
        ctype = "text/html; charset=utf-8"
        if req and req.headers.get("If-None-Match") == etag:
            return Response(b"", 304, ctype, headers)
        with open(full, encoding="utf-8") as f:
            html = f.read()
        html = html.replace('href="/css/', f'href="/v/{version}/css/').replace('src="/js/', f'src="/v/{version}/js/')
        data = html.encode()
        if req and "gzip" in (req.headers.get("Accept-Encoding") or ""):
            data = gzip.compress(data, 6)
            headers["Content-Encoding"] = "gzip"
        return Response(data, 200, ctype, headers)

    @staticmethod
    def service_worker(root, full, req):
        """sw.js avec la liste des fichiers de l'interface et une version qui change dès que l'un d'eux change
        (le navigateur installe alors le nouveau service worker et recharge son cache)."""
        shell = ["/", "/manifest.webmanifest", "/css/app.css", "/favicon.png", "/img/nexis-logo.webp"]
        for sub, ext in (("js", ".js"), ("fonts", ".woff"), ("icons", ".png")):
            for dirpath, _, files in os.walk(os.path.join(root, sub)):
                shell += ["/" + os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, "/")
                          for f in sorted(files) if f.endswith(ext)]
        shell = [u for u in shell if os.path.isfile(os.path.join(root, "index.html" if u == "/" else u.lstrip("/")))]
        stamp = []
        for u in [*shell, "/sw.js"]:
            st = os.stat(os.path.join(root, "index.html" if u == "/" else u.lstrip("/")))
            stamp.append(f"{u}:{st.st_size}:{st.st_mtime_ns}")
        version = hashlib.sha1("|".join(stamp).encode()).hexdigest()[:12]
        ui = ui_version(root)  # le code et le style sont chargés depuis /v/<empreinte>/ (voir index_page)
        shell = [f"/v/{ui}{u}" if u.startswith(("/js/", "/css/")) else u for u in shell]
        etag = f'"sw-{version}"'
        headers = {"Cache-Control": "no-cache", "ETag": etag}
        if req and req.headers.get("If-None-Match") == etag:
            return Response(b"", 304, "application/javascript; charset=utf-8", headers)
        with open(full, encoding="utf-8") as f:
            code = f.read()
        code = re.sub(r"^const VERSION = .*$", f"const VERSION = '{version}';", code, count=1, flags=re.M)
        code = re.sub(r"^const SHELL = .*$", "const SHELL = " + json.dumps(shell) + ";", code, count=1, flags=re.M)
        return Response(code, 200, "application/javascript; charset=utf-8", headers)


STAMPED = re.compile(r"^v/([0-9a-f]{12})/((?:js|css)/.+)$")
_ui = {"at": 0.0, "version": None}


def ui_version(root):
    """Empreinte du code et du style de l'interface (taille et date de chaque fichier), revue toutes les 2 s."""
    now = time.monotonic()
    if _ui["version"] and now - _ui["at"] < 2:
        return _ui["version"]
    stamp = []
    for sub in ("js", "css"):
        for dirpath, _, files in os.walk(os.path.join(root, sub)):
            for f in sorted(files):
                st = os.stat(os.path.join(dirpath, f))
                stamp.append(f"{dirpath}/{f}:{st.st_size}:{st.st_mtime_ns}")
    _ui.update(at=now, version=hashlib.sha1("|".join(sorted(stamp)).encode()).hexdigest()[:12])
    return _ui["version"]


COMPRESSIBLE = ("text/", "application/javascript", "application/json", "application/wasm", "image/svg+xml",
                "application/manifest+json")
_gz_lock = threading.Lock()
_gz_cache = {}  # (chemin, empreinte) -> fichier compressé : on ne recompresse pas hls.js à chaque visite


def _gzipped(full, etag):
    key = (full, etag)
    with _gz_lock:
        data = _gz_cache.get(key)
    if data is None:
        with open(full, "rb") as f:
            raw = f.read()
        if len(raw) < 1400:
            return None
        data = gzip.compress(raw, 6)
        with _gz_lock:
            if sum(len(v) for v in _gz_cache.values()) > 32 * 1024 * 1024:
                _gz_cache.clear()  # shortcut: vidé en bloc ; une LRU si l'interface grossit beaucoup
            _gz_cache[key] = data
    return data


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
        "script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "worker-src 'self' blob:; frame-src https://www.youtube-nocookie.com; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'; object-src 'none'"
    ),
}


JASSUB_CSP = ("default-src 'self'; script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval'; connect-src 'self'; "
              "img-src 'self' data: blob:; font-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("application/manifest+json", ".webmanifest")


def make_handler(app, trust_proxy=False, hsts=False):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Seance"
        sys_version = ""
        timeout = IDLE_TIMEOUT

        def log_message(self, fmt, *args):
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _client_ip(self):
            ip = self.client_address[0]
            if trust_proxy:
                # la DERNIÈRE adresse est celle ajoutée par notre reverse proxy ; les précédentes viennent du
                # client et peuvent être inventées (contournement du blocage après mots de passe erronés)
                fwd = self.headers.get("X-Forwarded-For")
                if fwd:
                    ip = fwd.split(",")[-1].strip() or ip
            return ip

        def _handle(self, method):
            parsed = urllib.parse.urlsplit(self.path)
            req = Request(self, method if method != "HEAD" else "GET", parsed.path,
                          urllib.parse.parse_qs(parsed.query), self.headers, self._client_ip())
            try:
                resp = app.dispatch(req)
            except HTTPError as e:
                payload = {"error": e.message}
                if e.data:
                    payload.update(e.data)
                resp = json_response(payload, e.status)
            except Exception:
                log.error("Erreur sur %s %s\n%s", method, parsed.path, traceback.format_exc())
                resp = json_response({"error": "Erreur interne du serveur"}, 500)
            finally:
                for fn in app.teardown:
                    try:
                        fn()
                    except Exception:
                        pass
            try:
                self._write(resp, req, head=(method == "HEAD"))
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                self.close_connection = True
            except Exception as e:  # upstream failed mid-stream: drop the connection
                log.debug("Flux interrompu sur %s : %s", parsed.path, e)
                self.close_connection = True
            finally:
                if resp.on_close:
                    try:
                        resp.on_close()
                    except Exception:
                        pass

        def _write(self, resp, req, head=False):
            # Drain an unread request body so keep-alive stays in sync.
            if req._body is None and req.method in ("POST", "PUT", "DELETE"):
                try:
                    req.body
                except HTTPError:
                    self.close_connection = True
            self.send_response(resp.status)
            for k, v in SECURITY_HEADERS.items():
                if k not in resp.headers:
                    self.send_header(k, v)
            if hsts:  # derrière un reverse proxy HTTPS (SEANCE_COOKIE_SECURE=1)
                self.send_header("Strict-Transport-Security", "max-age=31536000")
            for k, v in resp.headers.items():
                self.send_header(k, v)
            for c in req.set_cookies:
                self.send_header("Set-Cookie", c)
            if resp.stream is None:
                self.send_header("Content-Length", str(len(resp.body)))
                self.end_headers()
                if not head:
                    self.wfile.write(resp.body)
                return
            if resp.length is not None:
                self.send_header("Content-Length", str(resp.length))
                self.end_headers()
                if not head:
                    for chunk in resp.stream:
                        self.wfile.write(chunk)
                return
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            if head:
                return
            for chunk in resp.stream:
                if chunk:
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
            self.wfile.write(b"0\r\n\r\n")

        def do_GET(self):
            self._handle("GET")

        def do_HEAD(self):
            self._handle("HEAD")

        def do_POST(self):
            self._handle("POST")

        def do_PUT(self):
            self._handle("PUT")

        def do_DELETE(self):
            self._handle("DELETE")

    return Handler


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64
    max_connections = 256   # au-delà, les nouvelles connexions sont refusées au lieu d'épuiser la mémoire

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(self.max_connections)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return  # le navigateur a coupé la connexion (saut, changement de page) : normal
        super().handle_error(request, client_address)
