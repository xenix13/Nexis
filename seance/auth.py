"""Passwords, sessions, login throttling."""
import base64
import hashlib
import hmac
import re
import secrets
import threading
import unicodedata
import time

from .web import HTTPError

COOKIE = "seance_session"
SESSION_DAYS = 30
SCRYPT = dict(n=2 ** 14, r=8, p=1)


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **SCRYPT)
    return "scrypt${n}${r}${p}${s}${h}".format(
        s=base64.b64encode(salt).decode(), h=base64.b64encode(digest).decode(), **SCRYPT)


def verify_password(password, stored):
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt),
                              n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# A fixed hash so that unknown usernames take as long as wrong passwords.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


USERNAME = re.compile(r"^[\w.-]{2,32}$")  # lettres (accents compris), chiffres, . _ -
USERNAME_RULE = "Identifiant : 2 à 32 caractères (lettres, accents compris, chiffres, . _ -)"


def normalize_username(name):
    """Forme unique d'un identifiant (« é » saisi en un ou deux caractères selon le clavier)."""
    return unicodedata.normalize("NFC", str(name or "")).strip()


def find_user(db, username):
    """Compte par identifiant, sans tenir compte des majuscules, accents compris (« Léa » = « LÉA »)."""
    username = normalize_username(username)
    if not username:
        return None
    user = db.one("SELECT * FROM users WHERE username=?", (username,))
    if user or username.isascii():
        return user
    key = username.casefold()
    return next((u for u in db.all("SELECT * FROM users") if u["username"].casefold() == key), None)


def check_credentials(db, username, password):
    user = find_user(db, username)
    if not user:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user["password_hash"]) or user["disabled"]:
        return None
    return user


def validate_password(password):
    if not isinstance(password, str) or len(password) < 8:
        raise HTTPError(400, "Le mot de passe doit faire au moins 8 caractères")
    if len(password) > 256:
        raise HTTPError(400, "Mot de passe trop long")


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db, req, user_id, secure):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    db.run("INSERT INTO sessions(token_hash,user_id,created_at,expires_at,last_seen,user_agent,ip) "
           "VALUES(?,?,?,?,?,?,?)",
           (_token_hash(token), user_id, now, now + SESSION_DAYS * 86400, now,
            (req.headers.get("User-Agent") or "")[:200], req.client_ip))
    req.set_cookies.append(_cookie(token, SESSION_DAYS * 86400, secure))


def _cookie(value, max_age, secure):
    c = f"{COOKIE}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
    return c + "; Secure" if secure else c


def clear_session(db, req, secure):
    token = req.cookies.get(COOKIE)
    if token:
        db.run("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))
    req.set_cookies.append(_cookie("", 0, secure))


def load_user(db, req):
    token = req.cookies.get(COOKIE)
    if not token or len(token) > 100:
        return None
    h = _token_hash(token)
    now = int(time.time())
    row = db.one("SELECT u.*, s.last_seen AS s_last_seen FROM sessions s JOIN users u ON u.id=s.user_id "
                 "WHERE s.token_hash=? AND s.expires_at>?", (h, now))
    if not row or row["disabled"]:
        return None
    if now - row["s_last_seen"] > 300:
        db.run("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now, h))
    req.session_hash = h
    for k in ("password_hash", "s_last_seen", "totp_secret", "totp_pending", "totp_recovery"):
        row.pop(k, None)  # secrets : jamais gardés dans la requête
    return row


class Throttle:
    """Counts failed logins per IP and per username in a sliding window."""

    def __init__(self, limit=8, window=600):
        self.limit = limit
        self.window = window
        self.fails = {}
        self.lock = threading.Lock()

    def _recent(self, key, now):
        lst = [t for t in self.fails.get(key, []) if now - t < self.window]
        self.fails[key] = lst
        return lst

    def check(self, *keys):
        now = time.time()
        with self.lock:
            for k in keys:
                if len(self._recent(k, now)) >= self.limit:
                    raise HTTPError(429, "Trop de tentatives. Réessayez dans quelques minutes.")

    def fail(self, *keys):
        now = time.time()
        with self.lock:
            for k in keys:
                self._recent(k, now).append(now)
            if len(self.fails) > 10000:
                self.fails = {k: v for k, v in self.fails.items() if v and now - v[-1] < self.window}

    def reset(self, *keys):
        with self.lock:
            for k in keys:
                self.fails.pop(k, None)
