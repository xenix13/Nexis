"""Plex Media Server client (standard library only).

The Plex token never leaves the server: every call to Plex is made from here.
"""
import collections
import json
import logging
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("seance.plex")

PRODUCT = "Seance"
VERSION = "1.0"

# The LAN Plex server is contacted directly, never through an HTTP proxy.
_direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_insecure_ctx = ssl.create_default_context()
_insecure_ctx.check_hostname = False
_insecure_ctx.verify_mode = ssl.CERT_NONE
_direct_insecure_opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=_insecure_ctx))


class PlexError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def base_headers(client_id):
    return {
        "Accept": "application/json",
        "X-Plex-Product": PRODUCT,
        "X-Plex-Version": VERSION,
        "X-Plex-Client-Identifier": client_id,
        "X-Plex-Platform": "Chrome",
        "X-Plex-Platform-Version": "120.0",
        "X-Plex-Device": "Seance",
        "X-Plex-Device-Name": "Seance",
        "X-Plex-Model": "hosted",
    }


class TTLCache:
    def __init__(self, ttl, size=2000):
        self.ttl = ttl
        self.size = size
        self.data = collections.OrderedDict()
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            v = self.data.get(key)
            if v is None:
                return None
            if time.time() - v[0] > self.ttl:
                self.data.pop(key, None)
                return None
            self.data.move_to_end(key)
            return v[1]

    def set(self, key, value):
        with self.lock:
            self.data[key] = (time.time(), value)
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)

    def clear(self):
        with self.lock:
            self.data.clear()


class Plex:
    def __init__(self, url, token, client_id, verify_tls=True):
        self.url = (url or "").rstrip("/")
        self.token = token or ""
        self.client_id = client_id
        self.opener = _direct_opener if verify_tls else _direct_insecure_opener
        self.section_of = TTLCache(3600, 20000)  # rating_key -> section id
        self.cache = TTLCache(60, 500)

    @property
    def configured(self):
        return bool(self.url and self.token)

    def full_url(self, path, params=None):
        q = dict(params or {})
        url = self.url + path
        if q:
            url += ("&" if "?" in path else "?") + urllib.parse.urlencode(q, doseq=True)
        return url

    def open(self, path, params=None, method="GET", timeout=20, extra_headers=None):
        """Returns an open urllib response (caller must close it)."""
        if not self.configured:
            raise PlexError("Serveur Plex non configuré", 503)
        headers = base_headers(self.client_id)
        headers["X-Plex-Token"] = self.token
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(self.full_url(path, params), headers=headers, method=method)
        log.debug("Plex %s %s", method, path)  # never log the URL: it may carry parameters, not the token
        try:
            return self.opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            e.close()
            if e.code == 401:
                raise PlexError("Plex a refusé le jeton (401) : vérifie le token dans l'administration", 502)
            if e.code == 404:
                raise PlexError("Élément introuvable sur Plex", 404)
            raise PlexError(f"Plex a répondu {e.code}", 502)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise PlexError(f"Impossible de joindre Plex ({getattr(e, 'reason', e)})", 502)

    def get(self, path, params=None, timeout=20):
        with self.open(path, params, timeout=timeout) as r:
            raw = r.read()
        if not raw:
            return {}
        try:
            return json.loads(raw).get("MediaContainer", {})
        except (json.JSONDecodeError, AttributeError):
            raise PlexError("Réponse Plex illisible")

    def put(self, path, params=None):
        with self.open(path, params, method="PUT") as r:
            r.read()

    def cached(self, key, fn):
        v = self.cache.get(key)
        if v is None:
            v = fn()
            self.cache.set(key, v)
        return v

    # ---- high-level helpers ----
    def identity(self):
        return self.get("/", timeout=8)

    def sections(self):
        def load():
            out = []
            for d in self.get("/library/sections").get("Directory", []):
                out.append({"id": str(d.get("key")), "title": d.get("title"), "type": d.get("type"),
                            "agent": d.get("agent")})
            return out
        return self.cached("sections", load)

    def metadata(self, rating_key, markers=False):
        params = {"includeMarkers": 1, "includeChapters": 1} if markers else None
        items = self.get(f"/library/metadata/{rating_key}", params).get("Metadata", [])
        if not items:
            raise PlexError("Élément introuvable sur Plex", 404)
        self.remember(items[0])
        return items[0]

    def children(self, rating_key):
        mc = self.get(f"/library/metadata/{rating_key}/children")
        items = mc.get("Metadata", [])
        for m in items:
            if mc.get("librarySectionID") is not None:
                m.setdefault("librarySectionID", mc["librarySectionID"])
            self.remember(m)
        return items

    def all_leaves(self, show_key):
        def load():
            mc = self.get(f"/library/metadata/{show_key}/allLeaves")
            items = mc.get("Metadata", [])
            for m in items:
                if mc.get("librarySectionID") is not None:
                    m.setdefault("librarySectionID", mc["librarySectionID"])
                self.remember(m)
            return items
        return self.cached(f"leaves:{show_key}", load)

    def remember(self, m, fallback_section=None):
        sec = m.get("librarySectionID") or fallback_section
        if sec is None:
            return
        sec = str(sec)
        for k in ("ratingKey", "parentRatingKey", "grandparentRatingKey"):
            if m.get(k):
                self.section_of.set(str(m[k]), sec)

    def section_for(self, rating_key):
        sec = self.section_of.get(str(rating_key))
        if sec is None:
            m = self.metadata(rating_key)
            sec = str(m.get("librarySectionID"))
        return sec


# ---- plex.tv PIN login (used once, to obtain the token) ----

def tv_request(method, path, client_id, data=None, timeout=15):
    headers = base_headers(client_id)
    body = urllib.parse.urlencode(data).encode() if data else None
    if body:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request("https://plex.tv" + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise PlexError(f"plex.tv a répondu {e.code}")
    except (urllib.error.URLError, OSError) as e:
        raise PlexError(f"Impossible de joindre plex.tv ({getattr(e, 'reason', e)})")


def create_pin(client_id):
    d = tv_request("POST", "/api/v2/pins", client_id, {"strong": "true"})
    params = urllib.parse.urlencode({
        "clientID": client_id, "code": d["code"],
        "context[device][product]": PRODUCT,
    })
    return {"id": d["id"], "code": d["code"], "auth_url": "https://app.plex.tv/auth#?" + params}


def check_pin(client_id, pin_id):
    d = tv_request("GET", f"/api/v2/pins/{int(pin_id)}", client_id)
    return d.get("authToken")
