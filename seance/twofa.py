"""Double authentification : codes à usage unique (TOTP, RFC 6238), compatibles avec Google Authenticator,
Aegis, 2FAS, Microsoft Authenticator, Bitwarden…

- Chaque utilisateur qui se connecte avec un mot de passe peut l'activer pour son compte ; un profil géré
  (sans mot de passe, ouvert depuis « Changer de profil ») ne le peut pas.
- 10 codes de secours (usage unique, stockés hachés) permettent de se connecter sans le téléphone.
- Un administrateur peut la désactiver pour un autre compte ; en dernier recours, la commande
  `nexis reset-2fa <identifiant>` sur le serveur (voir __main__.py).
"""
import base64
import hashlib
import hmac
import json
import secrets
import struct
import threading
import time
import urllib.parse

STEP = 30            # secondes par code
DIGITS = 6
WINDOW = 1           # tolérance : un code d'avance ou de retard (horloge du téléphone décalée)
TICKET_SECONDS = 300
TICKET_TRIES = 5
RECOVERY_COUNT = 10
ISSUER = "Nexis"


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret):
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def code_at(secret, step):
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    o = digest[-1] & 0x0F
    n = (struct.unpack(">I", digest[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(n).zfill(DIGITS)


def check_code(secret, code, last_step=0, now=None):
    """-> pas de temps du code s'il est valide (et pas déjà utilisé), sinon None."""
    code = "".join(c for c in str(code or "") if c.isdigit())
    if len(code) != DIGITS or not secret:
        return None
    current = int((now if now is not None else time.time()) // STEP)
    for step in range(current - WINDOW, current + WINDOW + 1):
        if step > (last_step or 0) and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def otpauth_uri(secret, account):
    label = urllib.parse.quote(f"{ISSUER}:{account}", safe=":@")
    return f"otpauth://totp/{label}?secret={secret}&issuer={ISSUER}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


# ------------------------------------------------------------------ codes de secours
def _hash_recovery(code):
    return hashlib.sha256(code.replace("-", "").strip().lower().encode()).hexdigest()


def new_recovery_codes():
    """-> (codes à montrer une fois, hachés à enregistrer)"""
    codes = []
    for _ in range(RECOVERY_COUNT):
        raw = base64.b32encode(secrets.token_bytes(5)).decode().lower()  # 8 caractères
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes, json.dumps([_hash_recovery(c) for c in codes])


def use_recovery_code(stored_json, code):
    """-> nouvelle liste (JSON) si le code de secours est valide, sinon None"""
    cleaned = "".join(c for c in str(code or "") if c.isalnum())
    if len(cleaned) != 8:
        return None
    try:
        hashes = json.loads(stored_json or "[]")
    except ValueError:
        return None
    h = _hash_recovery(cleaned)
    for i, x in enumerate(hashes):
        if hmac.compare_digest(x, h):
            return json.dumps(hashes[:i] + hashes[i + 1:])
    return None


def recovery_left(stored_json):
    try:
        return len(json.loads(stored_json or "[]"))
    except ValueError:
        return 0


# ------------------------------------------------------------------ connexion en deux étapes
class Tickets:
    """Mot de passe vérifié, code attendu : un jeton court (5 min, 5 essais) remplace la session."""

    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()

    def create(self, uid):
        t = secrets.token_urlsafe(24)
        now = time.time()
        with self.lock:
            self.items = {k: v for k, v in self.items.items() if v["expires"] > now}
            self.items[t] = {"uid": uid, "expires": now + TICKET_SECONDS, "tries": 0}
        return t

    def peek(self, ticket):
        with self.lock:
            v = self.items.get(str(ticket or ""))
            if not v or v["expires"] < time.time():
                return None
            return v["uid"]

    def failed(self, ticket):
        with self.lock:
            v = self.items.get(ticket)
            if v:
                v["tries"] += 1
                if v["tries"] >= TICKET_TRIES:
                    self.items.pop(ticket, None)

    def consume(self, ticket):
        with self.lock:
            self.items.pop(ticket, None)


def verify_second_factor(db, user, code):
    """Code TOTP ou code de secours pour ce compte. -> 'totp' | 'recovery' | None (et l'enregistre)."""
    step = check_code(user.get("totp_secret"), code, user.get("totp_last_step") or 0)
    if step:
        db.run("UPDATE users SET totp_last_step=? WHERE id=?", (step, user["id"]))
        return "totp"
    left = use_recovery_code(user.get("totp_recovery"), code)
    if left is not None:
        db.run("UPDATE users SET totp_recovery=? WHERE id=?", (left, user["id"]))
        return "recovery"
    return None


def disable(db, uid):
    db.run("UPDATE users SET totp_enabled=0, totp_secret=NULL, totp_pending=NULL, totp_recovery=NULL, "
           "totp_last_step=0 WHERE id=?", (uid,))
