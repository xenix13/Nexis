"""Notifications push (Web Push) : nouvel épisode d'une série suivie, titre demandé arrivé, essai.

- Clés du serveur (VAPID, courbe P-256) créées avec openssl au premier usage, dans <données>/push.
- Les messages partent SANS contenu : rien à chiffrer, seul l'en-tête signé (JWT ES256) prouve qu'ils viennent
  de ce serveur. Le service worker de l'appareil, réveillé, demande alors à Nexis ce qu'il doit afficher.
- Seuls les services push des navigateurs sont contactés (Google, Mozilla, Apple, Microsoft) : une adresse
  d'abonnement quelconque ne peut pas servir à faire appeler une machine du réseau par le serveur.
"""
import base64
import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("seance.push")

PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com",
              "web.push.apple.com", "notify.windows.com")
CONTACT = "https://github.com/xenix13/Nexis"
KEEP_MESSAGES = 20


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def valid_endpoint(url):
    u = urllib.parse.urlsplit(str(url or ""))
    host = (u.hostname or "").lower()
    return (u.scheme == "https" and len(url) <= 1000 and not u.username and not u.port and
            any(host == h or host.endswith("." + h) for h in PUSH_HOSTS))


def _der_to_raw(sig):
    """Signature ECDSA DER (openssl) -> r || s sur 32 octets chacun (format JWT ES256)."""
    if sig[0] != 0x30:
        raise ValueError("signature")
    i = 2 if sig[1] < 0x80 else 2 + (sig[1] & 0x7F)
    out = b""
    for _ in range(2):
        if sig[i] != 0x02:
            raise ValueError("signature")
        n = sig[i + 1]
        v = sig[i + 2:i + 2 + n].lstrip(b"\0")
        out += v.rjust(32, b"\0")
        i += 2 + n
    return out


class Push:
    def __init__(self, ctx):
        self.ctx = ctx
        self.dir = os.path.join(ctx.data_dir, "push")
        self.key = os.path.join(self.dir, "vapid.pem")
        self._pub = None
        self.lock = threading.Lock()
        self.messages = {}  # compte -> derniers messages (ce que l'appareil affiche une fois réveillé)

    # ------------------------------------------------------------ clés
    def ensure_key(self):
        with self.lock:
            if os.path.isfile(self.key):
                return True
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
            tmp = self.key + ".tmp"
            old = os.umask(0o077)
            try:
                subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", tmp],
                               check=True, capture_output=True, timeout=30)
                os.replace(tmp, self.key)
            except (OSError, subprocess.SubprocessError) as e:
                log.error("Clés des notifications push : %s", e)
                return False
            finally:
                os.umask(old)
            return True

    def public_key(self):
        """Clé publique (point P-256 non compressé, base64url) que le navigateur associe à l'abonnement."""
        if self._pub is None and self.ensure_key():
            try:
                der = subprocess.run(["openssl", "ec", "-in", self.key, "-pubout", "-outform", "DER"],
                                     check=True, capture_output=True, timeout=30).stdout
                self._pub = b64url(der[-65:])
            except (OSError, subprocess.SubprocessError) as e:
                log.error("Clé publique des notifications push : %s", e)
        return self._pub

    def _jwt(self, endpoint):
        u = urllib.parse.urlsplit(endpoint)
        head = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
        body = b64url(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": CONTACT},
                                 separators=(",", ":")).encode())
        unsigned = f"{head}.{body}".encode()
        sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", self.key], input=unsigned,
                             check=True, capture_output=True, timeout=30).stdout
        return f"{head}.{body}.{b64url(_der_to_raw(sig))}"

    # ------------------------------------------------------------ envoi
    def _send_one(self, sub):
        db = self.ctx.db
        try:
            req = urllib.request.Request(sub["endpoint"], data=b"", method="POST", headers={
                "TTL": "86400", "Urgency": "normal", "Content-Length": "0",
                "Authorization": f"vapid t={self._jwt(sub['endpoint'])}, k={self.public_key()}"})
            with urllib.request.urlopen(req, timeout=15) as r:
                r.read(1024)
            db.run("UPDATE push_subs SET failures=0, last_ok=? WHERE id=?", (int(time.time()), sub["id"]))
            return True
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):  # abonnement révoqué (notifications refusées, appli désinstallée)
                db.run("DELETE FROM push_subs WHERE id=?", (sub["id"],))
            else:
                db.run("UPDATE push_subs SET failures=failures+1 WHERE id=?", (sub["id"],))
                log.info("Notification push refusée (%s) : %s", e.code, e.read(300))
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            db.run("UPDATE push_subs SET failures=failures+1 WHERE id=?", (sub["id"],))
            log.info("Notification push impossible : %s", e)
        finally:
            db.run("DELETE FROM push_subs WHERE failures>=20")
        return False

    def notify(self, uid, title, body, url="#/", tag="nexis"):
        """Message pour toutes les inscriptions de ce compte (en arrière-plan)."""
        msg = {"title": title[:120], "body": body[:240], "url": url, "tag": tag, "at": int(time.time())}
        with self.lock:
            lst = self.messages.setdefault(uid, [])
            lst.append(msg)
            del lst[:-KEEP_MESSAGES]
        subs = self.ctx.db.all("SELECT * FROM push_subs WHERE user_id=?", (uid,))
        if not subs or not self.public_key():
            return 0
        threading.Thread(target=self._send_all, args=(subs,), daemon=True, name="push").start()
        return len(subs)

    def _send_all(self, subs):
        try:
            for s in subs:
                self._send_one(s)
        finally:
            self.ctx.db.release()

    def pending(self, uid, since):
        with self.lock:
            return [m for m in self.messages.get(uid, []) if m["at"] >= since]

    # ------------------------------------------------------------ nouveaux épisodes (après un scan)
    def after_scan(self, x):
        db = self.ctx.db
        uids = [r["user_id"] for r in db.all("SELECT DISTINCT user_id FROM push_subs")]
        for uid in uids:
            user = db.one("SELECT * FROM users WHERE id=? AND disabled=0", (uid,))
            if not user:
                continue
            last = user.get("push_seen") or 0
            fresh = [(sh, [e for e in eps if (e["added_at"] or 0) > last]) for sh, eps in x.new_episodes(user)]
            fresh = [(sh, eps) for sh, eps in fresh if eps]
            if not fresh:
                continue
            db.run("UPDATE users SET push_seen=? WHERE id=?", (max(e["added_at"] or 0 for _, eps in fresh for e in eps), uid))
            from .i18n import tr
            if len(fresh) == 1:
                sh, eps = fresh[0]
                e = sorted(eps, key=lambda e: ((e.get("parent_idx") or 0), e["idx"] or 0))[0]
                title = tr("Nouvel épisode") if len(eps) == 1 else tr("{n} nouveaux épisodes").format(n=len(eps))
                body = f"{sh['title']} · S{e.get('parent_idx') or 0}E{e['idx'] or 0} · {e['title']}" if len(eps) == 1 else sh["title"]
                self.notify(uid, title, body, f"#/item/{sh['id']}", tag=f"show-{sh['id']}")
            else:
                n = sum(len(eps) for _, eps in fresh)
                self.notify(uid, tr("{n} nouveaux épisodes").format(n=n), ", ".join(sh["title"] for sh, _ in fresh[:4]),
                            "#/", tag="episodes")


def register(app, ctx, x):
    from .web import HTTPError
    db = ctx.db
    push = ctx.push

    @app.route("GET", "/api/push/key")
    def push_key(req):
        return {"key": push.public_key(), "subscribed": bool(db.scalar(
            "SELECT 1 FROM push_subs WHERE user_id=? AND endpoint=?", (req.user["id"], req.arg("endpoint") or "")))}

    @app.route("POST", "/api/push/subscribe")
    def push_subscribe(req):
        b = req.json()
        endpoint = str(b.get("endpoint") or "")
        if not valid_endpoint(endpoint):
            raise HTTPError(400, "Service de notifications non reconnu")
        uid, now = req.user["id"], int(time.time())
        if db.scalar("SELECT COUNT(*) FROM push_subs WHERE user_id=?", (uid,)) >= 20:
            db.run("DELETE FROM push_subs WHERE id=(SELECT id FROM push_subs WHERE user_id=? ORDER BY created_at LIMIT 1)", (uid,))
        db.run("INSERT INTO push_subs(user_id,endpoint,created_at) VALUES(?,?,?) ON CONFLICT(endpoint) DO UPDATE SET "
               "user_id=excluded.user_id, failures=0", (uid, endpoint, now))
        # à partir de maintenant : pas de rafale pour les épisodes ajoutés avant l'inscription
        db.run("UPDATE users SET push_seen=? WHERE id=? AND push_seen<?", (now, uid, now - 31 * 86400))
        return {"ok": True}

    @app.route("POST", "/api/push/unsubscribe")
    def push_unsubscribe(req):
        db.run("DELETE FROM push_subs WHERE user_id=? AND endpoint=?", (req.user["id"], str(req.json().get("endpoint") or "")))
        return {"ok": True}

    @app.route("POST", "/api/push/test")
    def push_test(req):
        from .i18n import tr
        n = push.notify(req.user["id"], tr("Notifications activées"), tr("Nexis vous préviendra des nouveaux épisodes."), "#/", "test")
        if not n:
            raise HTTPError(409, "Aucun appareil inscrit aux notifications")
        return {"sent": n}

    @app.route("GET", "/api/push/pending")
    def push_pending(req):
        """Ce qu'affiche l'appareil réveillé par une notification (les messages des 10 dernières minutes)."""
        return {"messages": push.pending(req.user["id"], int(time.time()) - 600)}
