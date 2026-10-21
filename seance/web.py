"""Minimal HTTP framework on top of the standard library (no dependencies)."""
import http.server
import json
import logging
import mimetypes
import os
import re
import socketserver
import traceback
import urllib.parse

log = logging.getLogger("seance.web")

MAX_BODY = 1024 * 1024


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
            if length > MAX_BODY:
                raise HTTPError(413, "Requête trop volumineuse")
            self._body = self.handler.rfile.read(length) if length else b""
        return self._body

    def json(self):
        if not self.body:
            return {}
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
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
        self.before = []  # callables(req, route_opts)
        self.static_dir = None
        self.teardown = []

    def route(self, method, pattern, **opts):
        regex = re.compile("^" + pattern + "$")

        def deco(fn):
            self.routes.append((method, regex, fn, opts))
            return fn
        return deco

    def dispatch(self, req):
        if req.path.startswith("/api/"):
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
        return self.serve_static(req.path)

    def serve_static(self, path):
        root = os.path.realpath(self.static_dir)
        rel = urllib.parse.unquote(path).lstrip("/") or "index.html"
        full = os.path.realpath(os.path.join(root, rel))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            if "." in os.path.basename(rel):
                raise HTTPError(404, "Fichier introuvable")
            full = os.path.join(root, "index.html")  # SPA fallback
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            data = f.read()
        cache = "no-cache" if full.endswith(".html") else "public, max-age=300"
        if "/vendor/" in full:
            cache = "public, max-age=604800"
        return Response(data, 200, ctype, {"Cache-Control": cache})


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
        "script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "worker-src 'self' blob:; frame-src https://www.youtube-nocookie.com; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'"
    ),
}


def make_handler(app, trust_proxy=False):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Seance"
        sys_version = ""

        def log_message(self, fmt, *args):
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _client_ip(self):
            ip = self.client_address[0]
            if trust_proxy:
                fwd = self.headers.get("X-Forwarded-For")
                if fwd:
                    ip = fwd.split(",")[0].strip()
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
                self.send_header(k, v)
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

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return  # le navigateur a coupé la connexion (saut, changement de page) : normal
        super().handle_error(request, client_address)
