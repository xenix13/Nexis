"""Outils communs de l'API compatible Jellyfin : identifiants, authentification, objets renvoyés aux clients.

Identifiants : Jellyfin utilise des GUID (32 caractères hexadécimaux). Séance les dérive de ses propres
numéros : 5eace0 + type (2) + numéro (24), ce qui les rend stables et réversibles. Les genres, personnes et
studios (qui n'ont pas de numéro chez Séance) reçoivent un GUID tiré de leur nom.
"""
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import urllib.parse
from datetime import datetime, timezone

from ..web import HTTPError, Response

JF_VERSION = "10.11.11"
PREFIX = "5eace0"
K_USER, K_LIB, K_ITEM, K_GENRE, K_PERSON, K_SEGMENT, K_STUDIO, K_ROOT = 1, 2, 3, 4, 5, 6, 7, 8
TYPES = {"movie": "Movie", "show": "Series", "season": "Season", "episode": "Episode", "extra": "Video"}
KINDS = {"movie": "movie", "series": "show", "season": "season", "episode": "episode", "video": "extra",
         "trailer": "extra"}
EXTRA_TYPES = {"trailer": "Trailer", "featurette": "Featurette", "behindthescenes": "BehindTheScenes",
               "deleted": "DeletedScene", "interview": "Interview", "scene": "Scene", "short": "Short",
               "other": "Clip"}
TICKS = 10_000  # 1 ms = 10 000 « ticks » .NET
AUTH_RE = re.compile(r'([\w-]+)="([^"]*)"')
GUID_RE = r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}"
LANG2 = {"fra": "fr", "fre": "fr", "eng": "en", "jpn": "ja", "deu": "de", "ger": "de", "spa": "es", "ita": "it",
         "por": "pt", "kor": "ko", "zho": "zh", "chi": "zh", "rus": "ru", "ara": "ar", "nld": "nl", "dut": "nl",
         "pol": "pl", "tur": "tr", "swe": "sv", "dan": "da", "nor": "no", "fin": "fi", "hin": "hi", "tha": "th"}


def guid(kind, n):
    return f"{PREFIX}{kind:02x}{int(n):024x}"


def unguid(value, kind):
    v = (value or "").replace("-", "").lower()
    if len(v) != 32 or not v.startswith(PREFIX) or not re.fullmatch(r"[0-9a-f]{32}", v):
        return None
    if int(v[6:8], 16) != kind:
        return None
    return int(v[8:], 16)


def kind_of(value):
    v = (value or "").replace("-", "").lower()
    if len(v) != 32 or not v.startswith(PREFIX) or not re.fullmatch(r"[0-9a-f]{32}", v):
        return None
    return int(v[6:8], 16)


def name_guid(kind, name):
    return guid(kind, int(hashlib.sha1(name.strip().lower().encode()).hexdigest()[:20], 16))


def iso(ts):
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


def date_iso(day):
    if not day or not re.match(r"^\d{4}-\d{2}-\d{2}$", str(day)):
        return None
    return f"{day}T00:00:00.0000000Z"


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def no_content():
    return Response(b"", 204, "text/plain")


def result(items, start=0, total=None):
    return {"Items": items, "TotalRecordCount": len(items) if total is None else total, "StartIndex": start}


class JF:
    """Contexte de l'API Jellyfin : base, catalogue partagé (x), réglages."""

    def __init__(self, ctx, x):
        self.ctx = ctx
        self.db = ctx.db
        self.x = x
        self.names = {}          # guid -> (genre|person|studio, nom)
        self.names_lock = threading.Lock()

    # ---------------------------------------------------------------- réglages
    def enabled(self):
        return self.db.setting("jellyfin_api", "0") == "1"

    def server_id(self):
        sid = self.db.setting("jf_server_id")
        if not sid:
            sid = secrets.token_hex(16)
            self.db.set_setting("jf_server_id", sid)
        return sid

    def server_name(self):
        return self.db.setting("server_name") or "Séance"

    def secret(self):
        s = self.db.setting("jf_secret")
        if not s:
            s = secrets.token_hex(32)
            self.db.set_setting("jf_secret", s)
        return s.encode()

    def mac(self, *parts, size=32):
        return hmac.new(self.secret(), "|".join(str(p) for p in parts).encode(), hashlib.sha256).hexdigest()[:size]

    # ---------------------------------------------------------------- requête
    @staticmethod
    def q(req, name, default=None):
        v = req.lq.get(name.lower())
        return v[0] if v and v[0] != "" else default

    @classmethod
    def qlist(cls, req, name, sep=","):
        out = []
        for v in req.lq.get(name.lower(), []):
            out += [p.strip() for p in v.split(sep) if p.strip()]
        return out

    @classmethod
    def qint(cls, req, name, default=0, lo=None, hi=None):
        try:
            v = int(float(cls.q(req, name, default)))
        except (TypeError, ValueError):
            raise HTTPError(400, f"Invalid parameter: {name}")
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return v

    @classmethod
    def qbool(cls, req, name, default=None):
        v = cls.q(req, name)
        if v is None:
            return default
        return v.lower() == "true"

    @staticmethod
    def body(req):
        if not req.body:
            return {}
        try:
            data = json.loads(req.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise HTTPError(400, "Invalid JSON")
        return data if isinstance(data, dict) else {}

    # ---------------------------------------------------------------- authentification
    def auth_fields(self, req):
        raw = req.headers.get("Authorization") or req.headers.get("X-Emby-Authorization") or ""
        fields = {}
        if raw.lower().startswith(("mediabrowser", "emby")):
            fields = {k: urllib.parse.unquote(v)[:200] for k, v in AUTH_RE.findall(raw)}
        token = (fields.get("Token") or req.headers.get("X-Emby-Token") or req.headers.get("X-MediaBrowser-Token")
                 or self.q(req, "api_key") or self.q(req, "ApiKey") or "")
        return fields, token.strip()

    def require_enabled(self):
        if not self.enabled():
            raise HTTPError(404, "Not found")

    def token_user(self, req, required=True):
        """Utilisateur du jeton Jellyfin (en-tête Authorization, X-Emby-Token ou api_key)."""
        self.require_enabled()
        cached = getattr(req, "jf_user", None)
        if cached is not None:
            return cached
        _, token = self.auth_fields(req)
        if not token or len(token) > 200:
            if not required:
                return None
            raise HTTPError(401, "Unauthorized")
        h = token_hash(token)
        row = self.db.one("SELECT u.*, t.token_hash AS t_hash, t.device_id AS t_device, t.app AS t_app, "
                          "t.device AS t_device_name, t.version AS t_version, t.last_used AS t_used "
                          "FROM jf_tokens t JOIN users u ON u.id=t.user_id WHERE t.token_hash=?", (h,))
        if not row or row["disabled"]:
            if not required:
                return None
            raise HTTPError(401, "Unauthorized")
        now = int(time.time())
        if now - row["t_used"] > 120:
            self.db.run("UPDATE jf_tokens SET last_used=?, ip=? WHERE token_hash=?", (now, req.client_ip, h))
        row.pop("password_hash", None)
        row.pop("pin_hash", None)
        req.jf_user = row
        return row

    def target_user(self, req, uid):
        """L'utilisateur nommé dans l'adresse doit être celui du jeton (un client n'agit que pour lui)."""
        u = self.token_user(req)
        if uid and uid.lower() not in ("me", "") and unguid(uid, K_USER) != u["id"]:
            raise HTTPError(403, "Forbidden")
        return u

    def query_user(self, req):
        """Paramètre userId facultatif (nouvelles routes de Jellyfin 10.9+)."""
        return self.target_user(req, self.q(req, "userId") or "")

    def issue(self, user_id, fields, ip):
        token = secrets.token_hex(16)
        now = int(time.time())
        device_id = (fields.get("DeviceId") or "")[:120]
        if device_id:  # comme Jellyfin : un appareil = une session, la précédente est remplacée
            self.db.run("DELETE FROM jf_tokens WHERE user_id=? AND device_id=? AND kind='session'",
                        (user_id, device_id))
        self.db.run("INSERT INTO jf_tokens(token_hash,kind,secret,user_id,app,device,ip,created_at,last_used,"
                    "device_id,version) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (token_hash(token), "session", None, user_id, (fields.get("Client") or "Client")[:60],
                     (fields.get("Device") or "")[:60], ip, now, now, device_id, (fields.get("Version") or "")[:30]))
        return token

    # ---------------------------------------------------------------- utilisateurs
    def user_dto(self, u):
        libs = sorted(self.x.allowed_set(u), key=int)
        prefs = self.x.get_prefs(u["id"])
        sub_mode = {"never": "None", "always": "Always", "foreign": "Smart"}.get(prefs.get("sub_mode"), "Default")
        return {
            "Name": u["username"], "ServerId": self.server_id(), "ServerName": self.server_name(),
            "Id": guid(K_USER, u["id"]), "PrimaryImageTag": None,
            "HasPassword": True, "HasConfiguredPassword": True, "HasConfiguredEasyPassword": False,
            "EnableAutoLogin": False, "LastLoginDate": iso(time.time()), "LastActivityDate": iso(time.time()),
            "Configuration": {
                "AudioLanguagePreference": prefs.get("audio_lang") or "", "PlayDefaultAudioTrack": True,
                "SubtitleLanguagePreference": prefs.get("sub_lang") or "", "DisplayMissingEpisodes": False,
                "GroupedFolders": [], "SubtitleMode": sub_mode, "DisplayCollectionsView": False,
                "EnableLocalPassword": False, "OrderedViews": [guid(K_LIB, i) for i in libs],
                "LatestItemsExcludes": [], "MyMediaExcludes": [guid(K_LIB, i) for i in prefs.get("hidden_libraries") or []
                                                               if i.isdigit()],
                "HidePlayedInLatest": True, "RememberAudioSelections": True, "RememberSubtitleSelections": True,
                "EnableNextEpisodeAutoPlay": bool(prefs.get("autoplay_next", True)), "CastReceiverId": None,
            },
            "Policy": {
                # jamais administrateur côté Jellyfin : l'administration se fait dans Séance
                "IsAdministrator": False, "IsHidden": True, "EnableCollectionManagement": False,
                "EnableSubtitleManagement": False, "EnableLyricManagement": False, "IsDisabled": bool(u["disabled"]),
                "MaxParentalRating": u.get("max_age"), "BlockedTags": [], "AllowedTags": [],
                "EnableUserPreferenceAccess": True, "AccessSchedules": [], "BlockUnratedItems": [],
                "EnableRemoteControlOfOtherUsers": False, "EnableSharedDeviceControl": False,
                "EnableRemoteAccess": True, "EnableLiveTvManagement": False, "EnableLiveTvAccess": False,
                "EnableMediaPlayback": True, "EnableAudioPlaybackTranscoding": True,
                "EnableVideoPlaybackTranscoding": True, "EnablePlaybackRemuxing": True,
                "ForceRemoteSourceTranscoding": False, "EnableContentDeletion": False,
                "EnableContentDeletionFromFolders": [], "EnableContentDownloading": False,
                "EnableSyncTranscoding": False, "EnableMediaConversion": False, "EnabledDevices": [],
                "EnableAllDevices": True, "EnabledChannels": [], "EnableAllChannels": False,
                "EnabledFolders": [guid(K_LIB, i) for i in libs],
                "EnableAllFolders": False, "InvalidLoginAttemptCount": 0, "LoginAttemptsBeforeLockout": -1,
                "MaxActiveSessions": 0, "EnablePublicSharing": False, "BlockedMediaFolders": [],
                "BlockedChannels": [], "RemoteClientBitrateLimit": 0,
                "AuthenticationProviderId": "Jellyfin.Server.Implementations.Users.DefaultAuthenticationProvider",
                "PasswordResetProviderId": "Jellyfin.Server.Implementations.Users.DefaultPasswordResetProvider",
                "SyncPlayAccess": "None",
            },
        }

    def session_dto(self, u, fields=None):
        fields = fields or {}
        now = iso(time.time())
        return {
            "PlayState": {"CanSeek": False, "IsPaused": False, "IsMuted": False, "RepeatMode": "RepeatNone",
                          "PlaybackOrder": "Default"},
            "AdditionalUsers": [], "Capabilities": {"PlayableMediaTypes": ["Video"], "SupportedCommands": [],
                                                    "SupportsMediaControl": False, "SupportsPersistentIdentifier": True},
            "RemoteEndPoint": "", "PlayableMediaTypes": ["Video"],
            "Id": self.mac("session", u["id"], fields.get("DeviceId") or u.get("t_device") or ""),
            "UserId": guid(K_USER, u["id"]), "UserName": u["username"],
            "Client": fields.get("Client") or u.get("t_app") or "", "LastActivityDate": now,
            "LastPlaybackCheckIn": now, "DeviceName": fields.get("Device") or u.get("t_device_name") or "",
            "DeviceId": fields.get("DeviceId") or u.get("t_device") or "",
            "ApplicationVersion": fields.get("Version") or u.get("t_version") or "", "IsActive": True,
            "SupportsMediaControl": False, "SupportsRemoteControl": False, "NowPlayingQueue": [],
            "NowPlayingQueueFullItems": [], "HasCustomDeviceName": False, "ServerId": self.server_id(),
            "SupportedCommands": [],
        }

    # ---------------------------------------------------------------- noms (genres, personnes, studios)
    def remember(self, kind, name):
        g = name_guid(kind, name)
        if g not in self.names:
            with self.names_lock:
                if len(self.names) > 50000:
                    self.names.clear()
                self.names[g] = name
        return g

    def name_of(self, value, kind):
        g = (value or "").replace("-", "").lower()
        if kind_of(g) != kind:
            return None
        if g in self.names:
            return self.names[g]
        # pas encore vu depuis le démarrage : on relit les genres, personnes et studios du catalogue
        for r in self.db.all("SELECT genres, people, studio FROM items WHERE type IN ('movie','show','episode')"):
            if kind == K_GENRE:
                for n in json.loads(r["genres"] or "[]"):
                    self.remember(K_GENRE, n)
            elif kind == K_STUDIO and r["studio"]:
                self.remember(K_STUDIO, r["studio"])
            elif kind == K_PERSON:
                for n, _role, _t in people_of(r):
                    self.remember(K_PERSON, n)
        return self.names.get(g)

    # ---------------------------------------------------------------- images
    def image_tag(self, item_id, kind, version):
        return self.mac("img", item_id, kind, version, size=20) + f"{int(version or 1):x}"

    def tag_ok(self, item_id, kind, version, tag):
        return bool(tag) and hmac.compare_digest(self.image_tag(item_id, kind, version), tag)


def people_of(row):
    """[(nom, rôle, type)] des métadonnées de Séance (réalisateurs puis distribution)."""
    try:
        people = json.loads(row["people"] or "{}")
    except (TypeError, ValueError):
        return []
    out = []
    for c in people.get("cast") or []:  # comme Jellyfin : la distribution, puis l'équipe
        if isinstance(c, dict) and isinstance(c.get("name"), str) and c["name"].strip():
            out.append((c["name"].strip(), c.get("role") or "", "Actor"))
    for d in people.get("directors") or []:
        name = d.get("name") if isinstance(d, dict) else d
        if isinstance(name, str) and name.strip():
            out.append((name.strip(), "", "Director"))
    return out
