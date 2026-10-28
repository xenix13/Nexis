"""Fédération : plusieurs serveurs Nexis d'un même propriétaire, liés entre eux.

- Jumelage : un serveur crée une invitation (son adresse, l'empreinte de son certificat, un jeton à usage unique
  valable 15 minutes). L'autre la colle, vérifie l'empreinte du serveur qu'il contacte et reçoit un secret partagé.
  Ensuite tout passe par un port dédié (8421) en TLS : chaque serveur épingle le certificat de l'autre (pas
  d'autorité de certification, pas de nom de domaine) et présente le secret à chaque requête.
- Tunnel : un serveur qui peut joindre l'autre lui garde quelques connexions ouvertes, par lesquelles l'autre lui
  envoie ses requêtes. Un seul des deux doit donc être joignable : rien à ouvrir sur la box de l'autre.
- Comptes : répliqués dans les deux sens ; la modification la plus récente l'emporte, une suppression est définitive.
- Catalogue : les bibliothèques de chaque serveur sont recopiées chez l'autre (fiches ; affiches relayées).
- Lecture : le serveur auquel l'appareil est connecté relaie le flux du serveur qui a le fichier, et lui renvoie la
  progression ; la progression enregistrée chez l'un revient chez l'autre.
"""
import base64
import hashlib
import hmac
import http.client
import json
import logging
import os
import queue
import re
import secrets
import select
import socket
import ssl
import subprocess
import threading
import time
import urllib.parse

from . import __version__, i18n
from .auth import USERNAME, find_user
from .names import fold
from .catalog import CARD_SQL, SID, stream_lists
from .web import HTTPError, Response, TLSServer, json_response, make_handler

log = logging.getLogger("seance.fed")

DEFAULT_PORT = 8421
INVITE_PREFIX = "NEXIS1-"
INVITE_TTL = 15 * 60
SYNC_EVERY = 15          # s entre deux synchronisations avec chaque serveur lié
IDLE_TUNNELS = 4         # connexions de tunnel gardées ouvertes vers chaque serveur lié joignable
TUNNEL_TTL = 240         # s : une connexion de tunnel inutilisée est renouvelée
CATALOG_PAGE = 400
PROGRESS_PAGE = 1000
HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ADDRESS = re.compile(r"^(\[[0-9a-fA-F:.]{2,45}\]|[A-Za-z0-9.-]{1,253})(:\d{1,5})?$")
USER_FIELDS = ("username", "display_name", "password_hash", "is_admin", "all_libraries", "disabled", "avatar_color",
               "pin_hash", "managed", "home", "max_age", "allow_unrated", "avatar_ver", "kids", "totp_enabled",
               "totp_secret", "totp_recovery", "language")
ITEM_LOCAL = {"id", "library_id", "parent_id", "show_id", "key", "fseq", "peer_id", "remote_id", "src_lib", "src_parent",
              "src_show", "alias_of"}
ITEM_SUB = re.compile(r"^/api/items/(\d{1,12})/(prepare|subtitles/.+|fonts/.+|trickplay(?:/.+)?|chapters/.+)$")
STREAM = re.compile(rf"^/api/stream/{SID}/(.+)$")
WATCHED = re.compile(r"^/api/items/(\d{1,12})/watched$")
ADMIN_ITEM = re.compile(r"^/api/admin/(?:identify|refresh|markers)/(\d{1,12})$")
ADMIN_STREAM = re.compile(rf"^/api/admin/streams/{SID}$")
# ce qu'un serveur lié peut demander au nom d'un compte : la lecture des éléments de ce serveur, rien d'autre
PEER_USER_PATHS = re.compile(r"^/api/(play|progress|stop|stream/.+|items/\d{1,12}/(prepare|subtitles/.+|fonts/.+|"
                             r"trickplay(/.+)?|chapters/.+|watched))$")


class FedError(Exception):
    pass


def split_address(address):
    u = urllib.parse.urlsplit("//" + address)
    return u.hostname, u.port or DEFAULT_PORT


def valid_address(v):
    v = str(v or "").strip()
    if v.startswith(("http://", "https://")):
        v = v.split("://", 1)[1]
    v = v.rstrip("/")
    if not v:
        return None
    if not ADDRESS.match(v) or (":" in v and not v.endswith("]") and not 0 < int(v.rsplit(":", 1)[1]) < 65536):
        raise HTTPError(400, "Adresse invalide : nom ou adresse IP, suivi du port si besoin (ex. 192.168.1.20:8421)")
    return v


def encode_invite(data):
    raw = json.dumps(data, separators=(",", ":")).encode()
    return INVITE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_invite(text):
    text = "".join(str(text or "").split())
    if not text.startswith(INVITE_PREFIX):
        raise HTTPError(400, "Invitation invalide : copiez-la en entier depuis l'autre serveur")
    body = text[len(INVITE_PREFIX):]
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        ok = (isinstance(data, dict) and HEX32.match(str(data.get("id"))) and HEX64.match(str(data.get("f")))
              and isinstance(data.get("t"), str) and 20 <= len(data["t"]) <= 100 and data.get("a"))
    except (ValueError, TypeError):
        ok = False
    if not ok:
        raise HTTPError(400, "Invitation invalide : copiez-la en entier depuis l'autre serveur")
    data["a"] = valid_address(data["a"])
    return data


class Pool:
    """Connexions de tunnel ouvertes par un serveur lié, en attente d'une requête de notre part."""

    def __init__(self):
        self.cond = threading.Condition()
        self.idle = []

    def offer(self, sock):
        """Fil de la connexion entrante : attend qu'une requête s'en serve (puis qu'elle soit finie), ou l'expiration."""
        entry = {"sock": sock, "born": time.monotonic(), "done": threading.Event(), "taken": False}
        with self.cond:
            self.idle.append(entry)
            self.cond.notify_all()
        if entry["done"].wait(TUNNEL_TTL):
            return
        with self.cond:
            if not entry["taken"]:
                if entry in self.idle:
                    self.idle.remove(entry)
                return
        entry["done"].wait()

    def take(self, timeout):
        end = time.monotonic() + timeout
        with self.cond:
            while True:
                now = time.monotonic()
                while self.idle:
                    e = self.idle.pop()
                    if now - e["born"] < TUNNEL_TTL - 15:
                        e["taken"] = True
                        return e["sock"], e["done"].set
                    e["done"].set()  # trop ancienne : l'autre serveur va la renouveler
                if now >= end:
                    return None
                self.cond.wait(end - now)

    def count(self):
        with self.cond:
            return len(self.idle)


class Federation:
    def __init__(self, ctx, port=None):
        self.ctx = ctx
        self.dir = os.path.join(ctx.data_dir, "federation")
        self.cert = os.path.join(self.dir, "cert.pem")
        self.key = os.path.join(self.dir, "key.pem")
        self.port = int(port if port is not None else os.environ.get("SEANCE_FED_PORT", DEFAULT_PORT))
        self.host = os.environ.get("SEANCE_FED_HOST") or os.environ.get("SEANCE_HOST") or "0.0.0.0"
        self.handler = None
        self.server = None
        self.x = None
        self.lock = threading.Lock()
        self.pools = {}         # id du serveur lié -> Pool (tunnel ouvert par lui)
        self.tunnels = {}       # id -> connexions de tunnel que nous lui gardons ouvertes (en attente)
        self.tunnel_fail = {}   # id -> instant du dernier échec d'ouverture
        self.status = {}        # id -> état affiché (en ligne, liaison, latence)
        self.proxied = {}       # flux relayé -> (id du serveur lié, id de l'utilisateur, id local de l'élément)
        self.queues = {}        # id -> file des envois en arrière-plan (progression, arrêts), dans l'ordre
        self.sync_locks = {}
        self.orphans = {}       # éléments reçus avant leur parent : id local -> (parent, série) chez le serveur lié
        self.avatar_todo = {}   # id -> comptes dont l'avatar est à récupérer
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self._fp = None
        self._has_peers = None
        self._columns = None

    # ------------------------------------------------------------------ identité
    def uid(self):
        db = self.ctx.db
        v = db.setting("server_uid")
        if not v:
            v = secrets.token_hex(16)
            db.set_setting("server_uid", v)
        return v

    def name(self):
        return self.ctx.db.setting("server_name") or (socket.gethostname() or "Nexis")[:40]

    def ready(self):
        return os.path.isfile(self.cert) and os.path.isfile(self.key)

    def ensure_cert(self):
        """Certificat propre à ce serveur, créé au premier jumelage (clé EC P-256, valable 100 ans)."""
        if self.ready():
            return
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        key_tmp, cert_tmp = self.key + ".tmp", self.cert + ".tmp"
        cmd = ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
               "-keyout", key_tmp, "-out", cert_tmp, "-days", "36500", "-subj", f"/CN=Nexis {self.uid()[:12]}"]
        old = os.umask(0o077)
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        except FileNotFoundError:
            raise FedError("openssl est introuvable sur le serveur : installez-le (apt install openssl)")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            log.error("Certificat de fédération : %s", getattr(e, "stderr", e))
            raise FedError("Création du certificat du serveur impossible")
        finally:
            os.umask(old)
        os.chmod(key_tmp, 0o600)
        os.replace(key_tmp, self.key)
        os.replace(cert_tmp, self.cert)
        self._fp = None

    def fingerprint(self):
        if not self._fp and self.ready():
            with open(self.cert) as f:
                self._fp = hashlib.sha256(ssl.PEM_cert_to_DER_cert(f.read())).hexdigest()
        return self._fp

    def local_address(self):
        """Adresse de ce serveur proposée dans les invitations (réglable par l'administrateur)."""
        manual = self.ctx.db.setting("fed_address")
        if manual:
            return manual
        ip = "127.0.0.1"
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("192.0.2.1", 9))  # aucune donnée envoyée : sert à connaître l'adresse locale de sortie
                ip = s.getsockname()[0]
        except OSError:
            pass
        return f"{ip}:{self.port}"

    # --------------------------------------------------------------- démarrage
    def attach(self, app, x):
        self.x = x
        self.handler = make_handler(app, fed=self)

    def listen(self):
        with self.lock:
            if self.server:
                return
            self.ensure_cert()
            sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            sctx.minimum_version = ssl.TLSVersion.TLSv1_2
            sctx.load_cert_chain(self.cert, self.key)
            try:
                self.server = TLSServer((self.host, self.port), self.handler, sctx)
            except OSError as e:
                raise FedError(f"Port {self.port} indisponible pour les serveurs liés : {e.strerror or e}")
            self.port = self.server.server_address[1]
            threading.Thread(target=self.server.serve_forever, daemon=True, name="fed-listen").start()
            log.info("Serveurs liés : écoute TLS sur le port %d", self.port)

    def has_peers(self):
        if self._has_peers is None:
            self._has_peers = bool(self.ctx.db.scalar("SELECT 1 FROM peers LIMIT 1"))
        return self._has_peers

    def start(self, loop=True):
        try:
            if self.ready() or self.has_peers():
                self.listen()
        except FedError as e:
            log.error("%s", e)
        finally:
            self.ctx.db.release()
        if loop:
            threading.Thread(target=self.run, daemon=True, name="fed-sync").start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        if self.server:
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.sync_all()
            except Exception:
                log.exception("Synchronisation des serveurs liés")
            finally:
                self.ctx.db.release()
            self.wake.wait(SYNC_EVERY)
            self.wake.clear()

    def sync_all(self):
        if not self.has_peers():
            return
        live = {r["id"] for r in self.ctx.db.all("SELECT id FROM streams")}
        with self.lock:
            for sid in [s for s in self.proxied if s not in live]:
                self.proxied.pop(sid, None)
        for p in self.peers():
            if p["address"]:
                self.spawn_tunnels(p["id"])
            self.sync(p["id"])

    # ------------------------------------------------------------------ serveurs liés
    def peers(self):
        return self.ctx.db.all("SELECT * FROM peers ORDER BY name COLLATE NOCASE")

    def peer(self, pid):
        p = self.ctx.db.one("SELECT * FROM peers WHERE id=?", (pid,))
        if not p:
            raise FedError("Serveur lié introuvable")
        return p

    def authenticate(self, headers):
        puid, key = headers.get("X-Fed-Peer") or "", headers.get("X-Fed-Key") or ""
        if not HEX32.match(puid) or not key:
            return None
        p = self.ctx.db.one("SELECT * FROM peers WHERE uid=?", (puid,))
        if p and hmac.compare_digest(key.encode(), p["secret"].encode()):
            return p
        return None

    def user_for(self, req):
        """Compte au nom duquel le serveur lié fait la requête (même compte, répliqué)."""
        uid = req.headers.get("X-Fed-User") or ""
        if not HEX32.match(uid):
            return None
        u = self.ctx.db.one("SELECT * FROM users WHERE uid=? AND disabled=0", (uid,))
        if u:
            for k in ("password_hash", "totp_secret", "totp_pending", "totp_recovery"):
                u.pop(k, None)
        return u

    # ------------------------------------------------------------------ connexions sortantes
    def _client_context(self):
        c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE  # la confiance vient de l'empreinte épinglée, vérifiée juste après
        c.minimum_version = ssl.TLSVersion.TLSv1_2
        return c

    def _tls_connect(self, peer, timeout):
        host, port = split_address(peer["address"])
        raw = socket.create_connection((host, port), timeout=timeout)
        try:
            s = self._client_context().wrap_socket(raw)
        except (OSError, ssl.SSLError):
            raw.close()
            raise
        der = s.getpeercert(binary_form=True)
        if not der or not hmac.compare_digest(hashlib.sha256(der).hexdigest(), peer["fingerprint"]):
            s.close()
            raise FedError("Certificat inattendu : ce n'est pas le serveur jumelé")
        return s

    def _open(self, peer, timeout):
        """Connexion vers un serveur lié : tunnel qu'il nous a ouvert, sinon connexion directe."""
        pool = self.pools.get(peer.get("id"))
        got = pool.take(0) if pool else None
        if got:
            return got[0], got[1], "tunnel"
        error = None
        if peer.get("address"):
            try:
                return self._tls_connect(peer, min(timeout, 15)), None, "direct"
            except FedError:
                raise
            except OSError as e:
                error = e
        if pool:
            got = pool.take(min(timeout, 10))
            if got:
                return got[0], got[1], "tunnel"
        if error:
            raise FedError(f"Serveur injoignable ({peer['address']}) : {error.strerror or error}")
        raise FedError("Serveur injoignable : il n'a pas d'adresse et n'a pas ouvert de tunnel")

    def request(self, peer, method, path, body=None, headers=None, user=None, timeout=30):
        sock, release, via = self._open(peer, timeout)
        sock.settimeout(timeout)
        conn = http.client.HTTPConnection("nexis-peer", timeout=timeout)
        conn.sock = sock
        h = {"X-Fed-Peer": self.uid(), "X-Fed-Key": peer.get("secret") or "", "Connection": "close",
             "X-Lang": i18n.current()}
        if user:
            h["X-Fed-User"] = user["uid"]
        if body is not None and not isinstance(body, bytes):
            body = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})

        def close():
            try:
                conn.close()
            finally:
                if release:
                    release()
        try:
            conn.request(method, path, body=body, headers=h)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as e:
            close()
            raise FedError(f"Le serveur lié ne répond pas : {e}")
        if peer.get("id"):
            self.status.setdefault(peer["id"], {})["via"] = via
        return resp, close

    def call(self, peer, method, path, data=None, user=None, timeout=30):
        resp, close = self.request(peer, method, path, data, user=user, timeout=timeout)
        try:
            raw = resp.read(256 * 1024 * 1024)
        except (OSError, http.client.HTTPException) as e:
            raise FedError(f"Le serveur lié ne répond pas : {e}")
        finally:
            close()
        try:
            return resp.status, json.loads(raw or b"{}")
        except ValueError:
            raise FedError("Réponse inattendue du serveur lié")

    def call_ok(self, peer, method, path, data=None, user=None, timeout=30):
        status, out = self.call(peer, method, path, data, user, timeout)
        if status != 200:
            raise FedError(out.get("error") if isinstance(out, dict) and out.get("error") else f"HTTP {status}")
        return out

    def send_later(self, peer_id, method, path, data, user):
        """Progression, arrêts, « vu » : envoyés au serveur lié dans l'ordre, sans faire attendre l'appareil."""
        with self.lock:
            q = self.queues.get(peer_id)
            if q is None:
                q = self.queues[peer_id] = queue.Queue(500)
                threading.Thread(target=self._sender, args=(peer_id, q), daemon=True).start()
        try:
            q.put_nowait((method, path, data, {"uid": user["uid"]}))
        except queue.Full:
            pass

    def _sender(self, peer_id, q):
        while not self.stop_event.is_set():
            method, path, data, user = q.get()
            try:
                self.call(self.peer(peer_id), method, path, data, user=user, timeout=20)
            except FedError as e:
                log.debug("Envoi au serveur lié %s : %s", peer_id, e)
            finally:
                self.ctx.db.release()

    # ------------------------------------------------------------------ tunnel
    def pool_for(self, pid):
        with self.lock:
            return self.pools.setdefault(pid, Pool())

    def spawn_tunnels(self, pid):
        with self.lock:
            if self.stop_event.is_set() or time.time() - self.tunnel_fail.get(pid, 0) < 15:
                return
            n = IDLE_TUNNELS - self.tunnels.get(pid, 0)
            self.tunnels[pid] = self.tunnels.get(pid, 0) + max(0, n)
        for _ in range(max(0, n)):
            threading.Thread(target=self._tunnel, args=(pid,), daemon=True, name="fed-tunnel").start()

    def _tunnel(self, pid):
        """Connexion ouverte vers un serveur lié, pour qu'il nous envoie ses requêtes (il n'a pas à nous joindre)."""
        counted = True
        sock = None
        try:
            peer = self.peer(pid)
            self.ctx.db.release()
            if not peer["address"]:
                return
            sock = self._tls_connect(peer, 15)
            sock.sendall((f"GET /fed/reverse HTTP/1.1\r\nHost: nexis\r\nX-Fed-Peer: {self.uid()}\r\n"
                          f"X-Fed-Key: {peer['secret']}\r\n\r\n").encode())
            self.ctx.db.release()
            head = b""
            while b"\r\n\r\n" not in head:
                c = sock.recv(1)
                if not c or len(head) > 4096:
                    raise FedError("Tunnel refusé")
                head += c
            if not head.startswith(b"HTTP/1.1 101"):
                raise FedError("Tunnel refusé : " + head.split(b"\r\n", 1)[0].decode(errors="replace"))
            sock.settimeout(None)
            if not sock.pending():
                ready, _, _ = select.select([sock], [], [], TUNNEL_TTL + 60)
                if not ready:
                    return
            # une requête arrive : on rouvre tout de suite une connexion d'avance, puis on la traite
            with self.lock:
                self.tunnels[pid] = self.tunnels.get(pid, 1) - 1
            counted = False
            self.spawn_tunnels(pid)
            self.handler(sock, split_address(peer["address"]), None)
        except (FedError, OSError, ssl.SSLError) as e:
            with self.lock:
                self.tunnel_fail[pid] = time.time()
            log.debug("Tunnel vers le serveur lié %s : %s", pid, e)
        except Exception:
            log.exception("Tunnel vers le serveur lié %s", pid)
        finally:
            if counted:
                with self.lock:
                    self.tunnels[pid] = max(0, self.tunnels.get(pid, 1) - 1)
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
            self.ctx.db.release()
            if not counted and not self.stop_event.is_set():
                self.spawn_tunnels(pid)

    # ------------------------------------------------------------------ jumelage
    def invite(self, address=None):
        self.listen()
        db = self.ctx.db
        token = secrets.token_urlsafe(24)
        now = int(time.time())
        db.run("DELETE FROM fed_invites WHERE expires_at<?", (now,))
        db.run("INSERT INTO fed_invites(token_hash, expires_at) VALUES(?,?)",
               (hashlib.sha256(token.encode()).hexdigest(), now + INVITE_TTL))
        address = valid_address(address) or self.local_address()
        return {"invite": encode_invite({"id": self.uid(), "n": self.name(), "a": address, "f": self.fingerprint(),
                                         "t": token}),
                "address": address, "expires_at": now + INVITE_TTL}

    def join(self, text, address=None):
        """Ce serveur rejoint celui qui a créé l'invitation."""
        data = decode_invite(text)
        address = valid_address(address)
        db = self.ctx.db
        if data["id"] == self.uid():
            raise HTTPError(400, "Cette invitation vient de ce serveur : collez-la sur l'autre serveur")
        if db.scalar("SELECT 1 FROM peers WHERE uid=?", (data["id"],)):
            raise HTTPError(409, "Ce serveur est déjà lié")
        try:
            self.listen()
        except FedError as e:
            raise HTTPError(500, str(e))
        target = {"address": data["a"], "fingerprint": data["f"], "secret": ""}
        payload = {"token": data["t"], "uid": self.uid(), "name": self.name(), "fingerprint": self.fingerprint(),
                   "address": address, "version": __version__, "users": self.users_payload()}
        db.release()
        try:
            status, r = self.call(target, "POST", "/fed/join", payload, timeout=60)
        except (FedError, OSError) as e:
            raise HTTPError(502, f"Impossible de joindre {data['a']} : {e}")
        if status != 200:
            raise HTTPError(status if 400 <= status < 500 else 502, r.get("error") or "Jumelage refusé")
        if not (HEX32.match(str(r.get("uid"))) and isinstance(r.get("secret"), str) and len(r["secret"]) >= 32):
            raise HTTPError(502, "Réponse inattendue du serveur lié")
        with db.tx():
            pid = db.run("INSERT INTO peers(uid,name,address,fingerprint,secret,created_at,version) VALUES(?,?,?,?,?,?,?)",
                         (r["uid"], str(r.get("name") or "Nexis")[:40], data["a"], data["f"], r["secret"], int(time.time()),
                          str(r.get("version") or "")[:20])).lastrowid
        self._has_peers = True
        peer = self.peer(pid)
        self.avatar_todo.setdefault(pid, []).extend(self.apply_users(peer, r.get("users") or [], [], mode="adopt"))
        log.warning("Serveur lié ajouté : %s (%s)", peer["name"], peer["address"])
        self.spawn_tunnels(pid)
        self.wake.set()
        return peer

    def accept_join(self, req):
        """Demande d'adhésion d'un autre serveur, avec le jeton d'une invitation de ce serveur."""
        ctx, db = self.ctx, self.ctx.db
        ctx.throttle.check("fedjoin:" + req.client_ip)
        b = req.json()
        token = str(b.get("token") or "")
        row = db.one("SELECT * FROM fed_invites WHERE token_hash=? AND expires_at>?",
                     (hashlib.sha256(token.encode()).hexdigest(), int(time.time())))
        if not row:
            ctx.throttle.fail("fedjoin:" + req.client_ip)
            raise HTTPError(403, "Invitation invalide ou expirée : créez-en une nouvelle")
        uid, fp = str(b.get("uid") or ""), str(b.get("fingerprint") or "")
        if not HEX32.match(uid) or not HEX64.match(fp):
            raise HTTPError(400, "Demande de jumelage invalide")
        if uid == self.uid():
            raise HTTPError(400, "Cette invitation vient de ce serveur : collez-la sur l'autre serveur")
        if db.scalar("SELECT 1 FROM peers WHERE uid=?", (uid,)):
            raise HTTPError(409, "Ce serveur est déjà lié")
        address = valid_address(b.get("address"))
        secret = secrets.token_urlsafe(32)
        with db.tx():
            db.run("DELETE FROM fed_invites WHERE token_hash=?", (row["token_hash"],))
            pid = db.run("INSERT INTO peers(uid,name,address,fingerprint,secret,created_at,version) VALUES(?,?,?,?,?,?,?)",
                         (uid, str(b.get("name") or "Nexis")[:40], address, fp, secret, int(time.time()),
                          str(b.get("version") or "")[:20])).lastrowid
        self._has_peers = True
        peer = self.peer(pid)
        users = b.get("users") if isinstance(b.get("users"), list) else []
        self.avatar_todo.setdefault(pid, []).extend(self.apply_users(peer, users, [], mode="keep"))
        log.warning("Serveur lié ajouté : %s (%s)", peer["name"], address or "par tunnel")
        self.wake.set()
        return {"uid": self.uid(), "name": self.name(), "version": __version__, "secret": secret,
                "users": self.users_payload()}

    def remove(self, pid, notify=True):
        peer = self.peer(pid)
        if notify:
            try:
                self.ctx.db.release()
                self.call(peer, "POST", "/fed/leave", {}, timeout=5)
            except (FedError, OSError):
                pass
        db = self.ctx.db
        db.run("DELETE FROM items WHERE peer_id=?", (pid,))  # y compris ceux rangés dans nos bibliothèques (fusion)
        db.run("DELETE FROM peers WHERE id=?", (pid,))  # et ses bibliothèques recopiées
        self._has_peers = None
        with self.lock:
            for sid in [s for s, v in self.proxied.items() if v[0] == pid]:
                self.proxied.pop(sid)
            self.status.pop(pid, None)
            pool = self.pools.pop(pid, None)
        if pool:
            while pool.take(0):
                pass
        log.warning("Serveur lié retiré : %s", peer["name"])

    # ------------------------------------------------------------------ synchronisation
    def sync(self, pid):
        with self.lock:
            lk = self.sync_locks.setdefault(pid, threading.Lock())
        if not lk.acquire(blocking=False):
            return
        db = self.ctx.db
        st = {"checked": int(time.time())}
        try:
            peer = self.peer(pid)
            db.release()
            t0 = time.monotonic()
            hello = self.call_ok(peer, "GET", "/fed/hello", timeout=15)
            st.update(online=True, latency=int((time.monotonic() - t0) * 1000), fails=0)
            self.set_online(pid, True)
            self.pull_users(peer)
            self.pull_catalog(peer)
            self.pull_progress(peer)
            for lid, uid, ver in self.avatar_todo.pop(pid, []):
                self.fetch_avatar(peer, lid, uid, ver)
            db.run("UPDATE peers SET last_seen=?, version=?, name=?, error=NULL WHERE id=?",
                   (int(time.time()), str(hello.get("version") or "")[:20], str(hello.get("name") or peer["name"])[:40], pid))
        except (FedError, OSError, ValueError, KeyError, TypeError, http.client.HTTPException) as e:
            fails = self.status.get(pid, {}).get("fails", 0) + 1
            st.update(online=False, fails=fails)
            if fails >= 2:  # deux échecs de suite (une trentaine de secondes) : ses titres sont masqués
                self.set_online(pid, False)
            log.info("Serveur lié %s : %s", pid, e)
            try:
                db.run("UPDATE peers SET error=? WHERE id=?", (str(e)[:300], pid))
            except Exception:
                pass
        finally:
            with self.lock:
                self.status.setdefault(pid, {}).update(st)
            db.release()
            lk.release()

    def _snapshot(self):
        """Lecture cohérente (une transaction) : le numéro de changement rendu couvre exactement ce qui est envoyé."""
        c = self.ctx.db.conn()
        c.execute("BEGIN")
        return c

    # comptes ---------------------------------------------------------------
    def user_payload(self, u):
        db = self.ctx.db
        d = {f: u[f] for f in USER_FIELDS}
        d.update(uid=u["uid"], rev=u["rev"] or 0, created_at=u["created_at"])
        libs = [r["uid"] for r in db.all("SELECT l.uid FROM user_libraries ul JOIN libraries l ON l.id=ul.section_id "
                                         "WHERE ul.user_id=?", (u["id"],))]
        libs += [r["uid"] for r in db.all("SELECT uid FROM fed_pending_libs WHERE user_id=?", (u["id"],))]
        d["libraries"] = sorted(set(libs))
        return d

    def users_payload(self):
        return [self.user_payload(u) for u in self.ctx.db.all("SELECT * FROM users")]

    def users_feed(self, since):
        self._backfill("users")
        db = self.ctx.db
        c = self._snapshot()
        try:
            until = db.scalar("SELECT n FROM fed_clock")
            rows = db.all("SELECT * FROM users WHERE fseq>? OR ?=0", (since, since))
            gone = [r["ref"] for r in db.all("SELECT ref FROM fed_gone WHERE kind='user' AND seq>?", (since,))]
            users = [self.user_payload(u) for u in rows]
        finally:
            c.execute("COMMIT")
        return {"users": users, "gone": gone, "until": until}

    def pull_users(self, peer):
        data = self.call_ok(peer, "GET", f"/fed/users?since={int(peer['users_cursor'])}")
        todo = self.apply_users(peer, data.get("users") or [], data.get("gone") or [])
        self.ctx.db.run("UPDATE peers SET users_cursor=? WHERE id=?", (int(data["until"]), peer["id"]))
        for lid, uid, ver in todo:
            self.fetch_avatar(peer, lid, uid, ver)

    def apply_users(self, peer, users, gone, mode="lww"):
        """Comptes reçus d'un serveur lié. mode : « lww » (le plus récent gagne), « keep » (adhésion d'un serveur :
        nos comptes gardent leurs données), « adopt » (nous rejoignons : les comptes de même identifiant prennent
        celles du serveur rejoint). -> avatars à récupérer [(id local, uid, version)]."""
        db = self.ctx.db
        avatars, logout = [], []
        with db.tx():
            db.run("UPDATE fed_clock SET applying=1")
            try:
                for u in users:
                    if not (isinstance(u, dict) and HEX32.match(str(u.get("uid"))) and isinstance(u.get("username"), str)
                            and USERNAME.match(u["username"]) and isinstance(u.get("password_hash"), str)):
                        continue
                    if db.scalar("SELECT 1 FROM fed_gone WHERE kind='user' AND ref=?", (u["uid"],)):
                        continue  # supprimé ici : la suppression l'emporte
                    rev = int(u.get("rev") or 0)
                    local = db.one("SELECT * FROM users WHERE uid=?", (u["uid"],))
                    force = False
                    if not local:
                        same = find_user(db, u["username"])
                        if same:
                            # même identifiant sur les deux serveurs : un seul compte. Les deux serveurs font le même
                            # choix (le plus petit uid) ; à l'adhésion, celui du serveur rejoint.
                            if mode == "keep" or (mode == "lww" and same["uid"] < u["uid"]):
                                continue
                            db.run("UPDATE users SET uid=? WHERE id=?", (u["uid"], same["id"]))
                            local = dict(same, uid=u["uid"])
                            force = mode == "adopt"
                    if local and not force and (local["rev"] or 0) >= rev:
                        continue
                    fields = {f: u.get(f) for f in USER_FIELDS}
                    for f in ("is_admin", "all_libraries", "disabled", "managed", "home", "allow_unrated", "kids",
                              "totp_enabled"):
                        fields[f] = 1 if u.get(f, f in ("all_libraries", "home", "allow_unrated")) else 0
                    fields["display_name"] = str(fields["display_name"] or u["username"])[:50]
                    fields["avatar_color"] = str(fields["avatar_color"] or "#e0a84f")[:7]
                    fields["language"] = i18n.valid(fields["language"]) or None
                    if fields["max_age"] is not None and not isinstance(fields["max_age"], int):
                        fields["max_age"] = None
                    if local:
                        if mode == "adopt" and local["is_admin"]:
                            fields["is_admin"] = 1  # on ne perd pas l'administration de ce serveur en le liant
                        other = find_user(db, fields["username"])
                        if other and other["id"] != local["id"]:
                            continue  # identifiant pris par un autre compte ici : réglé au prochain changement
                        sets = ",".join(f"{k}=?" for k in fields)
                        db.run(f"UPDATE users SET {sets}, rev=? WHERE id=?", [*fields.values(), rev, local["id"]])
                        lid = local["id"]
                        if mode == "lww" and (fields["password_hash"] != local["password_hash"] or fields["disabled"]):
                            logout.append(lid)
                        old_avatar = local["avatar_ver"]
                    else:
                        cols = ["uid", "rev", "created_at", *fields]
                        lid = db.run(f"INSERT INTO users({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                                     [u["uid"], rev, int(u.get("created_at") or time.time()), *fields.values()]).lastrowid
                        old_avatar = None
                    db.run("DELETE FROM user_libraries WHERE user_id=?", (lid,))
                    db.run("DELETE FROM fed_pending_libs WHERE user_id=?", (lid,))
                    for luid in u.get("libraries") or []:
                        if not isinstance(luid, str) or not HEX32.match(luid):
                            continue
                        lib = db.scalar("SELECT id FROM libraries WHERE uid=?", (luid,))
                        if lib:
                            db.run("INSERT OR IGNORE INTO user_libraries(user_id,section_id) VALUES(?,?)", (lid, str(lib)))
                        else:  # bibliothèque pas encore reçue (catalogue d'un serveur lié) : posée à son arrivée
                            db.run("INSERT OR IGNORE INTO fed_pending_libs(user_id,uid) VALUES(?,?)", (lid, luid))
                    if fields["avatar_ver"] != old_avatar or (fields["avatar_ver"] and not self.has_avatar(lid)):
                        avatars.append((lid, u["uid"], fields["avatar_ver"]))
                for uid in gone:
                    row = db.one("SELECT id FROM users WHERE uid=?", (str(uid),))
                    if row:
                        db.run("DELETE FROM users WHERE id=?", (row["id"],))
                        self.remove_avatar_file(row["id"])
                for lid in logout:
                    db.run("DELETE FROM sessions WHERE user_id=?", (lid,))
            finally:
                db.run("UPDATE fed_clock SET applying=0")
        return avatars

    def avatar_path(self, lid):
        return os.path.join(self.ctx.data_dir, "avatars", f"{int(lid)}.jpg")

    def has_avatar(self, lid):
        return os.path.isfile(self.avatar_path(lid))

    def remove_avatar_file(self, lid):
        try:
            os.remove(self.avatar_path(lid))
        except OSError:
            pass

    def fetch_avatar(self, peer, lid, uid, ver):
        if not ver:
            self.remove_avatar_file(lid)
            return
        try:
            resp, close = self.request(peer, "GET", f"/fed/avatar/{uid}", timeout=20)
            try:
                data = resp.read(2 * 1024 * 1024) if resp.status == 200 else None
            finally:
                close()
        except (FedError, OSError, http.client.HTTPException):
            return
        if data:
            os.makedirs(os.path.dirname(self.avatar_path(lid)), exist_ok=True)
            tmp = self.avatar_path(lid) + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, self.avatar_path(lid))

    # catalogue -------------------------------------------------------------
    def _backfill(self, table):
        """Numéros de changement des lignes modifiées quand aucun serveur n'était lié (séries avant saisons
        avant épisodes : un élément arrive toujours après son parent)."""
        db = self.ctx.db
        if table == "items":
            where, order, key = "peer_id IS NULL AND fseq IS NULL", \
                "CASE type WHEN 'show' THEN 0 WHEN 'season' THEN 1 ELSE 2 END, id", "id"
        else:  # comptes, progression
            where, order, key = "fseq IS NULL", "rowid", "rowid"
        if not db.scalar(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1"):
            return
        with db.tx():
            n = db.scalar("SELECT n FROM fed_clock")
            ids = [r["k"] for r in db.all(f"SELECT {key} AS k FROM {table} WHERE {where} ORDER BY {order}")]
            db.conn().executemany(f"UPDATE {table} SET fseq=? WHERE {key}=?", [(n + i + 1, k) for i, k in enumerate(ids)])
            db.run("UPDATE fed_clock SET n=?", (n + len(ids),))

    def catalog_feed(self, since, limit):
        self._backfill("items")
        db = self.ctx.db
        c = self._snapshot()
        try:
            clock = db.scalar("SELECT n FROM fed_clock")
            rows = db.all("SELECT * FROM items WHERE peer_id IS NULL AND fseq>? ORDER BY fseq LIMIT ?", (since, limit))
            more = len(rows) == limit
            until = rows[-1]["fseq"] if more else clock
            gone = [int(r["ref"]) for r in db.all("SELECT ref FROM fed_gone WHERE kind='item' AND seq>? AND seq<=?",
                                                  (since, until))]
            libs = db.all("SELECT id, uid, name, kind, position FROM libraries WHERE peer_id IS NULL")
        finally:
            c.execute("COMMIT")
        items = [{k: v for k, v in r.items() if k not in ITEM_LOCAL or k in ("id", "library_id", "parent_id", "show_id")}
                 for r in rows]
        return {"libraries": libs, "items": items, "gone": gone, "until": until, "more": more}

    def item_columns(self):
        if self._columns is None:
            self._columns = {r["name"] for r in self.ctx.db.all("PRAGMA table_info(items)")} - ITEM_LOCAL
        return self._columns

    def local_item(self, pid, remote):
        if not remote:
            return None
        return self.ctx.db.scalar("SELECT id FROM items WHERE peer_id=? AND remote_id=?", (pid, int(remote)))

    def pull_catalog(self, peer):
        cursor = int(peer["catalog_cursor"])
        for _ in range(200):
            data = self.call_ok(peer, "GET", f"/fed/catalog?since={cursor}&limit={CATALOG_PAGE}", timeout=120)
            self.apply_catalog(peer, data)
            cursor = int(data["until"])
            self.ctx.db.run("UPDATE peers SET catalog_cursor=? WHERE id=?", (cursor, peer["id"]))
            self.ctx.db.release()
            if not data.get("more"):
                break

    def apply_catalog(self, peer, data):
        db = self.ctx.db
        pid = peer["id"]
        cols = self.item_columns()
        orphans = self.orphans.setdefault(pid, {})
        with db.tx():
            seen = set()
            for l in data.get("libraries") or []:
                rid = int(l["id"])
                seen.add(rid)
                kind = l.get("kind") if l.get("kind") in ("movie", "show") else "movie"
                name = str(l.get("name") or "?")[:40]
                luid = str(l.get("uid") or "")
                if not HEX32.match(luid):
                    continue
                cur = db.scalar("SELECT id FROM libraries WHERE peer_id=? AND remote_id=?", (pid, rid))
                if cur:
                    db.run("UPDATE libraries SET name=?, kind=?, position=?, uid=? WHERE id=?",
                           (name, kind, 1000 + int(l.get("position") or 0), luid, cur))
                else:
                    lid = db.run("INSERT INTO libraries(name,kind,paths,position,created_at,uid,peer_id,remote_id) "
                                 "VALUES(?,?,'[]',?,?,?,?,?)",
                                 (name, kind, 1000 + int(l.get("position") or 0), int(time.time()), luid, pid, rid)).lastrowid
                    # comptes qui avaient déjà accès à cette bibliothèque (reçus avant elle)
                    db.run("UPDATE fed_clock SET applying=1")
                    db.run("INSERT OR IGNORE INTO user_libraries(user_id,section_id) SELECT user_id, ? FROM fed_pending_libs "
                           "WHERE uid=?", (str(lid), luid))
                    db.run("DELETE FROM fed_pending_libs WHERE uid=?", (luid,))
                    db.run("UPDATE fed_clock SET applying=0")
            if data.get("libraries") is not None:
                for r in db.all("SELECT id, remote_id FROM libraries WHERE peer_id=?", (pid,)):
                    if r["remote_id"] not in seen:
                        db.run("DELETE FROM items WHERE src_lib=?", (r["id"],))  # y compris ceux rangés ailleurs (fusion)
                        db.run("DELETE FROM libraries WHERE id=?", (r["id"],))
            libmap = {r["remote_id"]: r["id"] for r in db.all("SELECT id, remote_id FROM libraries WHERE peer_id=?", (pid,))}
            for it in data.get("items") or []:
                rid = int(it["id"])
                lib = libmap.get(int(it.get("library_id") or 0))
                if not lib:
                    continue
                vals = {k: v for k, v in it.items() if k in cols}
                parent, show = self.local_item(pid, it.get("parent_id")), self.local_item(pid, it.get("show_id"))
                # place d'origine ; la place affichée (fusion, serveur éteint) est décidée par relink()
                vals.update(src_lib=lib, src_parent=parent, src_show=show)
                if vals.get("meta_state") == "pending":
                    vals["meta_state"] = "done"  # les métadonnées se cherchent chez le serveur d'origine
                cur = self.local_item(pid, rid)
                if cur:
                    sets = ",".join(f"{k}=?" for k in vals)
                    db.run(f"UPDATE items SET {sets} WHERE id=?", [*vals.values(), cur])
                else:
                    vals.update(peer_id=pid, remote_id=rid, library_id=lib, parent_id=parent, show_id=show)
                    for k, default in (("title", "?"), ("sort_title", "?"), ("search", ""), ("type", "movie"),
                                       ("added_at", int(time.time())), ("updated_at", int(time.time()))):
                        if vals.get(k) is None:
                            vals[k] = default
                    cur = db.run(f"INSERT INTO items({','.join(vals)}) VALUES({','.join('?' * len(vals))})",
                                 list(vals.values())).lastrowid
                if (it.get("parent_id") and not parent) or (it.get("show_id") and not show):
                    orphans[cur] = (it.get("parent_id"), it.get("show_id"))
            gone = [int(g) for g in data.get("gone") or []]
            for i in range(0, len(gone), 500):
                chunk = gone[i:i + 500]
                db.run(f"DELETE FROM items WHERE peer_id=? AND remote_id IN ({','.join('?' * len(chunk))})", [pid, *chunk])
            for lid, (rp, rs) in list(orphans.items()):
                parent, show = self.local_item(pid, rp), self.local_item(pid, rs)
                if (parent or not rp) and (show or not rs):
                    db.run("UPDATE items SET src_parent=?, src_show=? WHERE id=?", (parent, show, lid))
                    orphans.pop(lid)
        self.relink(pid)

    # fusion ----------------------------------------------------------------
    def relink_all(self):
        if not self.has_peers():
            return
        for p in self.ctx.db.all("SELECT id FROM peers"):
            self.relink(p["id"])

    def relink(self, pid):
        """Place affichée des éléments reçus d'un serveur lié.

        - Bibliothèque non fusionnée : telle quelle (une bibliothèque de plus).
        - Fusionnée avec une bibliothèque de ce serveur : un titre que nous avons aussi (même identifiant TMDB, IMDb,
          TVDB, ou même titre et même année) est masqué et devient une seconde copie du nôtre (alias_of) ; une saison
          ou un épisode qui nous manque se range sous notre série. Le reste rejoint notre bibliothèque.
        - Serveur injoignable : tout retourne dans sa bibliothèque, masquée ; nos titres restent.
        """
        db = self.ctx.db
        peer = db.one("SELECT online FROM peers WHERE id=?", (pid,))
        if not peer:
            return
        changes = []
        for lib in db.all("SELECT l.id, l.kind, l.merge_into, t.kind AS target_kind FROM libraries l "
                          "LEFT JOIN libraries t ON t.id=l.merge_into AND t.peer_id IS NULL WHERE l.peer_id=?", (pid,)):
            rows = db.all("SELECT id, type, idx, title, year, tmdb_id, imdb_id, tvdb_id, library_id, parent_id, show_id, "
                          "alias_of, src_parent, src_show FROM items WHERE src_lib=?", (lib["id"],))
            target = lib["merge_into"] if peer["online"] and lib["target_kind"] == lib["kind"] else None
            alias = {}
            if target:
                index = {}
                for r in db.all("SELECT id, type, title, year, tmdb_id, imdb_id, tvdb_id FROM items WHERE library_id=? "
                                "AND peer_id IS NULL AND type IN ('movie','show')", (target,)):
                    for key in self._match_keys(r):
                        index.setdefault(key, r)
                for r in rows:
                    if r["type"] not in ("movie", "show"):
                        continue
                    for key in self._match_keys(r):
                        hit = index.get(key)
                        # même titre et même année, mais deux films différents chez TMDB (remake) : pas le même
                        if hit and not (key[1] == "title" and r["tmdb_id"] and hit["tmdb_id"]
                                        and r["tmdb_id"] != hit["tmdb_id"]):
                            alias[r["id"]] = hit["id"]
                            break
                for kind in ("season", "episode"):
                    for r in rows:
                        if r["type"] != kind or r["src_parent"] not in alias or r["idx"] is None:
                            continue
                        hit = db.scalar("SELECT id FROM items WHERE parent_id=? AND type=? AND idx=? AND peer_id IS NULL "
                                        "ORDER BY id LIMIT 1", (alias[r["src_parent"]], kind, r["idx"]))
                        if hit:
                            alias[r["id"]] = hit
            for r in rows:
                if not peer["online"] or not target:
                    want = (lib["id"], r["src_parent"], r["src_show"], None)
                elif r["id"] in alias:
                    want = (lib["id"], r["src_parent"], r["src_show"], alias[r["id"]])
                else:
                    want = (target, alias.get(r["src_parent"], r["src_parent"]), alias.get(r["src_show"], r["src_show"]), None)
                if want != (r["library_id"], r["parent_id"], r["show_id"], r["alias_of"]):
                    changes.append((*want, r["id"]))
        if changes:
            with db.tx():
                db.conn().executemany("UPDATE items SET library_id=?, parent_id=?, show_id=?, alias_of=? WHERE id=?", changes)

    @staticmethod
    def _match_keys(r):
        keys = []
        for col in ("tmdb_id", "imdb_id", "tvdb_id"):
            if r[col]:
                keys.append((r["type"], col, str(r[col])))
        if r["title"]:
            keys.append((r["type"], "title", fold(r["title"]), r["year"]))
        return keys

    def set_online(self, pid, online):
        db = self.ctx.db
        cur = db.scalar("SELECT online FROM peers WHERE id=?", (pid,))
        if cur is not None and bool(cur) != online:
            db.run("UPDATE peers SET online=? WHERE id=?", (1 if online else 0, pid))
            log.warning("Serveur lié %s %s", pid, "de nouveau joignable" if online else "injoignable : ses titres sont masqués")
            self.relink(pid)

    # progression -----------------------------------------------------------
    def progress_feed(self, since, limit):
        self._backfill("progress")
        db = self.ctx.db
        c = self._snapshot()
        try:
            clock = db.scalar("SELECT n FROM fed_clock")
            rows = db.all("SELECT p.rating_key, p.position_ms, p.duration_ms, p.watched, p.view_count, p.updated_at, "
                          "p.last_watched_at, p.fseq, u.uid AS user FROM progress p JOIN users u ON u.id=p.user_id "
                          "JOIN items i ON i.id=p.rating_key WHERE i.peer_id IS NULL AND p.fseq>? ORDER BY p.fseq LIMIT ?",
                          (since, limit))
        finally:
            c.execute("COMMIT")
        more = len(rows) == limit
        return {"rows": rows, "until": rows[-1]["fseq"] if more else clock, "more": more}

    def pull_progress(self, peer):
        cursor = int(peer["progress_cursor"])
        for _ in range(100):
            data = self.call_ok(peer, "GET", f"/fed/progress?since={cursor}&limit={PROGRESS_PAGE}", timeout=60)
            self.apply_progress(peer, data.get("rows") or [])
            cursor = int(data["until"])
            self.ctx.db.run("UPDATE peers SET progress_cursor=? WHERE id=?", (cursor, peer["id"]))
            self.ctx.db.release()
            if not data.get("more"):
                break

    def apply_progress(self, peer, rows):
        db, x = self.ctx.db, self.x
        users = {}
        with db.tx():
            for r in rows:
                if r["user"] not in users:
                    users[r["user"]] = db.scalar("SELECT id FROM users WHERE uid=?", (str(r["user"]),))
                uid = users[r["user"]]
                item = self.local_item(peer["id"], r["rating_key"])
                if item:  # titre fusionné avec l'un des nôtres : la progression va sur le nôtre
                    item = db.scalar("SELECT COALESCE(alias_of, id) FROM items WHERE id=?", (item,))
                if not uid or not item:
                    continue
                rk = str(item)
                cur = db.one("SELECT updated_at FROM progress WHERE user_id=? AND rating_key=?", (uid, rk))
                if cur and cur["updated_at"] > int(r["updated_at"] or 0):
                    continue  # plus récent ici (regardé depuis ce serveur)
                if not cur:
                    row = db.one(CARD_SQL + "WHERE i.id=?", (item,))
                    x.upsert_meta(uid, x.card(row))
                db.run("UPDATE progress SET position_ms=?, duration_ms=?, watched=?, view_count=?, updated_at=?, "
                       "last_watched_at=? WHERE user_id=? AND rating_key=?",
                       (int(r["position_ms"] or 0), int(r["duration_ms"] or 0), 1 if r["watched"] else 0,
                        int(r["view_count"] or 0), int(r["updated_at"] or 0), r["last_watched_at"], uid, rk))

    # ------------------------------------------------------------------ relais (appareil -> serveur qui a le fichier)
    def mirror(self, rk):
        return self.ctx.db.one("SELECT id, peer_id, remote_id, type FROM items WHERE id=? AND peer_id IS NOT NULL",
                               (int(rk),))

    def image(self, item, kind, w, h):
        tag = hashlib.sha1(f"fed|{item['peer_id']}|{item['remote_id']}|{kind}|{item['img_version']}|{w}|{h}".encode()).hexdigest()
        dest = os.path.join(self.ctx.data_dir, "cache", "img", tag + ".jpg")
        if os.path.isfile(dest):
            return dest
        try:
            peer = self.peer(item["peer_id"])
            self.ctx.db.release()
            resp, close = self.request(peer, "GET", f"/fed/img/{int(item['remote_id'])}/{kind}?w={w}&h={h}", timeout=30)
            try:
                data = resp.read(20 * 1024 * 1024) if resp.status == 200 else None
            finally:
                close()
        except (FedError, OSError, http.client.HTTPException) as e:
            log.debug("Image d'un serveur lié : %s", e)
            return None
        if not data:
            return None
        tmp = dest + f".{threading.get_ident()}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        return dest

    def relay(self, req, peer_id, path):
        peer = self.peer(peer_id)
        qs = urllib.parse.urlencode(req.query, doseq=True)
        if qs:
            path += "?" + qs
        headers = {k: req.headers[k] for k in ("Range", "If-None-Match") if req.headers.get(k)}
        body = req.body if req.method in ("POST", "PUT") else None
        self.ctx.db.release()
        try:
            resp, close = self.request(peer, req.method, path, body, headers=headers, user=req.user, timeout=90)
        except FedError as e:
            raise HTTPError(502, str(e))
        out = {k: resp.getheader(k) for k in ("Content-Range", "Accept-Ranges", "Cache-Control", "ETag",
                                               "Content-Encoding") if resp.getheader(k)}
        length = resp.getheader("Content-Length")

        def gen():
            while True:
                chunk = resp.read(262144)
                if not chunk:
                    break
                yield chunk
        return Response(stream=gen(), status=resp.status, content_type=resp.getheader("Content-Type") or "application/octet-stream",
                        length=int(length) if length and length.isdigit() else None, headers=out, on_close=close)

    def active_source(self, user_id, rk):
        """Copie d'un serveur lié en cours de lecture par ce compte pour cet élément (la plus récente)."""
        with self.lock:
            for e in reversed(list(self.proxied.values())):
                if e[1] == user_id and e[2] == int(rk):
                    return e
        return None

    def intercept(self, req):
        """Requête d'un appareil connecté à ce serveur, portant sur un élément d'un serveur lié : relayée.
        -> Response, ou None si la route locale s'en charge (éventuellement après avoir prévenu le serveur lié)."""
        if not req.user or not self.has_peers():
            return None
        path, x = req.path, self.x
        if req.method == "GET":
            m = ITEM_SUB.match(path)
            if m:
                r = self.mirror(m.group(1))
                if r:
                    x.require_item(req.user, m.group(1))
                    return self.relay(req, r["peer_id"], f"/api/items/{r['remote_id']}/{m.group(2)}")
                # titre fusionné lu depuis la copie d'un serveur lié : sous-titres, aperçus, chapitres de cette copie
                e = self.active_source(req.user["id"], m.group(1))
                if e:
                    x.require_item(req.user, m.group(1))
                    return self.relay(req, e[0], f"/api/items/{e[3]}/{m.group(2)}")
                return None
            m = STREAM.match(path)
            if m:
                entry = self.proxied.get(m.group("sid"))
                if not entry:
                    return None
                if entry[1] != req.user["id"]:
                    raise HTTPError(404, "Flux expiré")
                now = int(time.time())
                self.ctx.db.run("UPDATE streams SET last_seen=? WHERE id=? AND last_seen<?", (now, m.group("sid"), now - 15))
                return self.relay(req, entry[0], path)
            return None
        if req.method == "POST" and path == "/api/play":
            return self.play(req)
        if req.method == "POST" and path == "/api/progress":
            b = req.json()
            e = self.proxied.get(str(b.get("stream") or ""))
            if e and str(e[2]) == str(b.get("id")):
                self.send_later(e[0], "POST", "/api/progress", dict(b, id=str(e[3])), req.user)
            elif str(b.get("id") or "").isdigit():
                r = self.mirror(b["id"])
                if r:
                    fwd = dict(b, id=str(r["remote_id"]))
                    fwd.pop("stream", None)
                    self.send_later(r["peer_id"], "POST", "/api/progress", fwd, req.user)
            return None
        if req.method == "POST" and path == "/api/stop":
            entry = self.proxied.pop(str(req.json().get("stream") or ""), None)
            if entry:
                self.send_later(entry[0], "POST", "/api/stop", req.json(), req.user)
            return None
        if req.method == "DELETE":
            m = ADMIN_STREAM.match(path)
            entry = self.proxied.pop(m.group("sid"), None) if m else None
            if entry:
                owner = self.ctx.db.one("SELECT uid FROM users WHERE id=?", (entry[1],))
                if owner:
                    self.send_later(entry[0], "POST", "/api/stop", {"stream": m.group("sid")}, owner)
            return None
        m = WATCHED.match(path)
        if m and req.method == "POST":
            r = self.mirror(m.group(1))
            if r:
                self.send_later(r["peer_id"], "POST", f"/api/items/{r['remote_id']}/watched", req.json(), req.user)
            return None
        if req.method == "POST" and path in ("/api/downloads/options", "/api/downloads"):
            b = req.json()
            if str(b.get("id") or "").isdigit() and self.mirror(b["id"]):
                raise HTTPError(400, "Les titres d'un serveur lié ne se téléchargent pas encore pour le hors ligne")
            return None
        m = ADMIN_ITEM.match(path)
        if m and self.mirror(m.group(1)):
            raise HTTPError(400, "Cet élément vient d'un serveur lié : modifiez-le sur ce serveur")
        return None

    # choix de la copie ------------------------------------------------------
    def sources(self, row):
        """Copies lisibles d'un titre : la nôtre (si le fichier est là) et celles des serveurs liés joignables."""
        if row["peer_id"] is not None:
            return [row]
        out = [row] if row["key"] and os.path.isfile(row["key"]) else []
        return out + self.ctx.db.all("SELECT i.* FROM items i JOIN peers p ON p.id=i.peer_id WHERE i.alias_of=? "
                                     "AND p.online=1 ORDER BY i.id", (row["id"],))

    @staticmethod
    def _tracks(row):
        return stream_lists(json.loads(row["probe"] or "{}"))

    def rank(self, req, row, cands, b):
        """La copie qui a la langue voulue (audio, puis sous-titres demandés) si une seule l'a, sinon la meilleure
        qualité (définition, puis débit) ; à égalité, la nôtre."""
        x, uid = self.x, req.user["id"]
        audio0, subs0 = self._tracks(row)
        tp = x.track_prefs(uid, row) or {}
        want_a = next((a["lang"] for a in audio0 if a["id"] == b.get("audio")), None) \
            or (tp.get("audio_lang") if tp.get("audio_lang") not in (None, "", "off") else None) \
            or x.get_prefs(uid).get("audio_lang") or None
        want_s = next((t["lang"] for t in subs0 if t["id"] == b.get("subtitle")), None) if b.get("subtitle") else None

        def score(c):
            audio, subs = self._tracks(c)
            probe = json.loads(c["probe"] or "{}")
            video = probe.get("video") or {}
            return (not want_a or any(a["lang"] == want_a for a in audio),
                    not want_s or any(t["lang"] == want_s for t in subs),
                    int(video.get("height") or 0), int(probe.get("bitrate") or 0), c["peer_id"] is None)
        return sorted(cands, key=score, reverse=True)

    def remap(self, b, row, c):
        """Pistes choisies sur la fiche (celles de notre copie) -> les mêmes langues dans la copie retenue."""
        if c["id"] == row["id"]:
            return b
        audio0, subs0 = self._tracks(row)
        audio, subs = self._tracks(c)
        out = dict(b)
        a = next((t for t in audio0 if t["id"] == b.get("audio")), None)
        if b.get("audio") is not None:
            same = [t for t in audio if a and t["lang"] == a["lang"]]
            same.sort(key=lambda t: (t["codec"] == a["codec"], t["channels"] == a["channels"]), reverse=True)
            out["audio"] = same[0]["id"] if same else None
        t0 = next((t for t in subs0 if t["id"] == b.get("subtitle")), None) if b.get("subtitle") else None
        if b.get("subtitle"):
            same = [t for t in subs if t0 and t["lang"] == t0["lang"] and bool(t["forced"]) == bool(t0["forced"])]
            same.sort(key=lambda t: t["codec"] == t0["codec"], reverse=True)
            out["subtitle"] = same[0]["id"] if same else 0
        return out

    def _drop_proxied(self, sid, user):
        entry = self.proxied.pop(sid, None) if sid else None
        if entry:
            self.send_later(entry[0], "POST", "/api/stop", {"stream": sid}, user)
        return entry

    def play(self, req):
        b = req.json()
        rk = str(b.get("id") or "")
        old = str(b.get("replace") or "")
        if not rk.isdigit():
            return None
        x, db = self.x, self.ctx.db
        r = db.one("SELECT id, peer_id FROM items WHERE id=?", (int(rk),))
        if not r or (r["peer_id"] is None and not db.scalar("SELECT 1 FROM items WHERE alias_of=? LIMIT 1", (r["id"],))):
            self._drop_proxied(old, req.user)  # flux relayé remplacé par une lecture locale : on arrête l'autre
            return None
        row = x.require_item(req.user, rk)
        cands = self.sources(row)
        # même titre relancé (saut, changement de piste ou de qualité) : même copie, ses pistes
        e = self.proxied.get(old) if old else None
        sticky = None
        if e and e[2] == row["id"]:
            sticky = next((c for c in cands if c["peer_id"] == e[0] and c["remote_id"] == e[3]), None)
        elif old and db.scalar("SELECT 1 FROM streams WHERE id=? AND rating_key=?", (old, rk)):
            sticky = next((c for c in cands if c["peer_id"] is None), None)
        order = [sticky] + [c for c in cands if c is not sticky] if sticky else self.rank(req, row, cands, b)
        if not order and row["peer_id"] is None:
            return None  # aucune copie : la route locale explique (fichier introuvable)
        error = None
        for c in order:
            body = b if c is sticky else self.remap(b, row, c)
            if c["peer_id"] is None:
                self._drop_proxied(old, req.user)
                req._body = json.dumps(body).encode()
                return None
            try:
                return self.play_remote(req, row, c, body, old)
            except HTTPError as ex:
                log.info("Lecture depuis le serveur lié %s impossible : %s", c["peer_id"], ex.message)
                error = ex
        raise error or HTTPError(503, "Aucune copie de ce titre n'est disponible pour l'instant")

    def play_remote(self, req, row, c, body, old):
        x, db = self.x, self.ctx.db
        uid = req.user["id"]
        peer = self.peer(c["peer_id"])
        fwd = dict(body, id=str(c["remote_id"]))
        fwd.pop("playlist", None)
        e = self.proxied.get(old) if old else None
        if old and not e:
            x.stop_stream(old, uid)  # flux local remplacé par celui du serveur lié
            fwd.pop("replace", None)
        elif e and e[0] != peer["id"]:
            self._drop_proxied(old, req.user)
            fwd.pop("replace", None)
        nxt = x.next_episode(row)
        if body.get("playlist"):
            nxt = x.playlist_next(req.user, body["playlist"], row["id"]) if str(body["playlist"]).isdigit() else None
        db.release()
        try:
            status, data = self.call(peer, "POST", "/api/play", fwd, user=req.user, timeout=90)
        except FedError as ex:
            raise HTTPError(502, str(ex))
        if status != 200:
            raise HTTPError(status, data.get("error") or "Lecture impossible",
                            {k: v for k, v in data.items() if k != "error"})
        sid = str(data.get("stream") or "")
        if not re.match(rf"^{SID}$", sid):
            raise HTTPError(502, "Réponse inattendue du serveur lié")
        with self.lock:
            if e and e[0] == peer["id"]:
                self.proxied.pop(old, None)  # remplacé chez lui par le nouveau flux
            self.proxied[sid] = (peer["id"], uid, row["id"], c["remote_id"])
        if old and e:
            db.run("DELETE FROM streams WHERE id=? AND user_id=?", (old, uid))
        rk = str(row["id"])
        card = x.card(row)
        remote_prefix, local_prefix = f"/api/items/{c['remote_id']}/", f"/api/items/{rk}/"
        for k in ("subtitle_overlay", "subtitle_ass", "subtitle_pgs"):
            if data.get(k):
                data[k] = data[k].replace(remote_prefix, local_prefix, 1)
        data["item"] = card
        data["next"] = x.card(nxt) if nxt else None
        now = int(time.time())
        title = card["title"] if card["type"] == "movie" else f"{card.get('show')} · S{card.get('season')}E{card.get('episode')}"
        params = {"mode": data.get("mode"), "reasons": data.get("reasons") or [], "peer": peer["name"]}
        db.run("INSERT OR REPLACE INTO streams(id,user_id,plex_session,rating_key,params,title,quality,position_ms,"
               "created_at,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (sid, uid, sid, rk, json.dumps(params), title, data.get("quality"), int(data.get("offset") or 0), now, now))
        if row["type"] != "extra":
            x.upsert_meta(uid, card)
        return json_response(data)

    # ------------------------------------------------------------------ état pour l'administration
    def view(self):
        db = self.ctx.db
        peers = []
        for p in self.peers():
            st = self.status.get(p["id"], {})
            pool = self.pools.get(p["id"])
            peers.append({
                "id": p["id"], "name": p["name"], "address": p["address"], "fingerprint": p["fingerprint"],
                "created_at": p["created_at"], "last_seen": p["last_seen"], "version": p["version"],
                "error": i18n.tr(p["error"]) if p["error"] else None, "online": st.get("online"),
                "latency": st.get("latency"), "via": st.get("via"),
                "tunnel_in": pool.count() if pool else 0, "tunnel_out": self.tunnels.get(p["id"], 0),
                "libraries": [{"id": str(l["id"]), "name": l["name"], "kind": l["kind"], "count": l["n"],
                               "merged": l["n_alias"], "merge_into": str(l["merge_into"]) if l["merge_into"] else None}
                              for l in db.all(
                    "SELECT l.id, l.name, l.kind, l.merge_into, (SELECT COUNT(*) FROM items i WHERE i.src_lib=l.id AND "
                    "i.type IN ('movie','show')) AS n, (SELECT COUNT(*) FROM items i WHERE i.src_lib=l.id AND "
                    "i.type IN ('movie','show') AND i.alias_of IS NOT NULL) AS n_alias "
                    "FROM libraries l WHERE l.peer_id=? ORDER BY l.position", (p["id"],))]})
        return {"local_libraries": [{"id": str(l["id"]), "name": l["name"], "kind": l["kind"]} for l in db.all(
                    "SELECT id, name, kind FROM libraries WHERE peer_id IS NULL ORDER BY position, name")],"name": self.name(), "uid": self.uid(), "fingerprint": self.fingerprint(), "port": self.port,
                "listening": bool(self.server), "address": self.local_address(),
                "address_manual": db.setting("fed_address") or "", "peers": peers}


def register(app, ctx, x):
    db = ctx.db
    fed = ctx.fed
    fed.attach(app, x)

    # ------------------------------------------------------------ entre serveurs (port des serveurs liés)
    @app.route("POST", "/fed/join", auth="join")
    def fed_join(req):
        return fed.accept_join(req)

    @app.route("GET", "/fed/hello", auth="peer")
    def fed_hello(req):
        return {"uid": fed.uid(), "name": fed.name(), "version": __version__, "time": int(time.time())}

    @app.route("GET", "/fed/reverse", auth="peer")
    def fed_reverse(req):
        """Tunnel : le serveur lié nous laisse cette connexion pour que nous lui envoyions nos requêtes."""
        hd = req.handler
        hd.wfile.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: nexis-tunnel\r\nConnection: Upgrade\r\n\r\n")
        hd.wfile.flush()
        pool = fed.pool_for(req.fed_peer["id"])
        db.release()
        pool.offer(hd.connection)
        resp = Response()
        resp.hijacked = True
        return resp

    @app.route("GET", "/fed/users", auth="peer")
    def fed_users(req):
        return fed.users_feed(req.int_arg("since", 0, 0))

    @app.route("GET", r"/fed/avatar/(?P<uid>[0-9a-f]{32})", auth="peer")
    def fed_avatar(req):
        u = db.one("SELECT id FROM users WHERE uid=?", (req.params["uid"],))
        try:
            with open(fed.avatar_path(u["id"]) if u else "", "rb") as f:
                return Response(f.read(), 200, "image/jpeg")
        except OSError:
            raise HTTPError(404, "Introuvable")

    @app.route("GET", "/fed/catalog", auth="peer")
    def fed_catalog(req):
        return fed.catalog_feed(req.int_arg("since", 0, 0), req.int_arg("limit", CATALOG_PAGE, 1, 2000))

    @app.route("GET", "/fed/progress", auth="peer")
    def fed_progress(req):
        return fed.progress_feed(req.int_arg("since", 0, 0), req.int_arg("limit", PROGRESS_PAGE, 1, 5000))

    @app.route("GET", r"/fed/img/(?P<rk>\d{1,12})/(?P<kind>thumb|art)", auth="peer")
    def fed_img(req):
        r = db.one("SELECT * FROM items WHERE id=? AND peer_id IS NULL", (int(req.params["rk"]),))
        if not r:
            raise HTTPError(404, "Introuvable")
        w, h = req.int_arg("w", 300, 40, 1920), req.int_arg("h", 450, 40, 1920)
        path = ctx.images.get(r, req.params["kind"], w, h)
        if not path:
            raise HTTPError(404, "Pas d'image")
        with open(path, "rb") as f:
            return Response(f.read(), 200, "image/jpeg")

    @app.route("POST", "/fed/leave", auth="peer")
    def fed_leave(req):
        fed.remove(req.fed_peer["id"], notify=False)
        return {"ok": True}

    # ------------------------------------------------------------ administration
    def checked(fn, *args):
        try:
            return fn(*args)
        except FedError as e:
            raise HTTPError(404 if "introuvable" in str(e) else 502, str(e))

    @app.route("GET", "/api/admin/federation", auth="admin")
    def admin_federation(req):
        return fed.view()

    @app.route("PUT", "/api/admin/federation", auth="admin")
    def admin_federation_put(req):
        b = req.json()
        if "name" in b:
            name = str(b["name"] or "").strip()
            if not 1 <= len(name) <= 40:
                raise HTTPError(400, "Nom du serveur : 1 à 40 caractères")
            db.set_setting("server_name", name)
        if "address" in b:
            db.set_setting("fed_address", valid_address(b["address"]))
        fed.wake.set()
        return fed.view()

    @app.route("POST", "/api/admin/federation/invite", auth="admin")
    def admin_federation_invite(req):
        return checked(fed.invite, req.json().get("address"))

    @app.route("POST", "/api/admin/federation/join", auth="admin")
    def admin_federation_join(req):
        b = req.json()
        peer = fed.join(b.get("invite"), b.get("address"))
        db.release()
        fed.sync(peer["id"])
        return fed.view()

    @app.route("PUT", r"/api/admin/federation/peers/(?P<pid>\d{1,6})", auth="admin")
    def admin_federation_peer(req):
        pid = int(req.params["pid"])
        checked(fed.peer, pid)
        db.run("UPDATE peers SET address=? WHERE id=?", (valid_address(req.json().get("address")), pid))
        with fed.lock:
            fed.tunnel_fail.pop(pid, None)
        fed.wake.set()
        return fed.view()

    @app.route("PUT", r"/api/admin/federation/libraries/(?P<lid>\d{1,6})", auth="admin")
    def admin_federation_library(req):
        """Bibliothèque reçue d'un serveur lié : nouvelle bibliothèque, ou fusionnée avec l'une des nôtres."""
        lib = db.one("SELECT * FROM libraries WHERE id=? AND peer_id IS NOT NULL", (int(req.params["lid"]),))
        if not lib:
            raise HTTPError(404, "Bibliothèque introuvable")
        target = req.json().get("merge_into")
        if target not in (None, "", 0, "0"):
            t = db.one("SELECT id, kind FROM libraries WHERE id=? AND peer_id IS NULL", (int(target) if str(target).isdigit() else 0,))
            if not t:
                raise HTTPError(404, "Bibliothèque introuvable")
            if t["kind"] != lib["kind"]:
                raise HTTPError(400, "Fusion impossible entre une bibliothèque de films et une de séries")
            target = t["id"]
            # qui a accès à notre bibliothèque a accès à ce qui y est fusionné (et le serveur lié l'apprend)
            db.run("INSERT OR IGNORE INTO user_libraries(user_id, section_id) SELECT user_id, ? FROM user_libraries "
                   "WHERE section_id=?", (str(lib["id"]), str(target)))
        else:
            target = None
        db.run("UPDATE libraries SET merge_into=? WHERE id=?", (target, lib["id"]))
        fed.relink(lib["peer_id"])
        return fed.view()

    @app.route("POST", r"/api/admin/federation/peers/(?P<pid>\d{1,6})/sync", auth="admin")
    def admin_federation_sync(req):
        pid = int(req.params["pid"])
        checked(fed.peer, pid)
        db.release()
        fed.sync(pid)
        return fed.view()

    @app.route("DELETE", r"/api/admin/federation/peers/(?P<pid>\d{1,6})", auth="admin")
    def admin_federation_remove(req):
        checked(fed.remove, int(req.params["pid"]))
        return fed.view()
