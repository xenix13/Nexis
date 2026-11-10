"""Stockages externes des sauvegardes : S3 (AWS et compatibles : Backblaze B2, Wasabi, Scaleway, OVH, MinIO…),
Google Drive et OneDrive.

- S3 : clé d'accès et secret ; requêtes signées (AWS Signature v4), adressage par chemin (accepté partout).
- Google Drive et OneDrive : l'administrateur crée une application chez Google (type « TV et appareils à
  saisie limitée ») ou chez Microsoft (application publique), puis relie le compte par un code saisi sur
  google.com/device ou microsoft.com/devicelogin. Noxa ne garde qu'un jeton de renouvellement, et n'a accès
  qu'à ses propres fichiers (Google : drive.file ; OneDrive : dossier de l'application).
"""
import datetime
import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

log = logging.getLogger("seance.storage")

GOOGLE_OAUTH = os.environ.get("SEANCE_GOOGLE_OAUTH", "https://oauth2.googleapis.com")
GOOGLE_API = os.environ.get("SEANCE_GOOGLE_API", "https://www.googleapis.com")
MS_LOGIN = os.environ.get("SEANCE_MS_LOGIN", "https://login.microsoftonline.com/common/oauth2/v2.0")
MS_GRAPH = os.environ.get("SEANCE_MS_GRAPH", "https://graph.microsoft.com/v1.0")
GOOGLE_SCOPE = "https://www.googleapis.com/auth/drive.file"
MS_SCOPE = "Files.ReadWrite.AppFolder offline_access"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
CHUNK = 10 * 1024 * 1024  # OneDrive : morceaux multiples de 320 Kio
KINDS = ("s3", "gdrive", "onedrive")


class StorageError(Exception):
    pass


def _http(method, url, data=None, headers=None, timeout=120):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read(64 * 1024 * 1024)
    except urllib.error.HTTPError as e:
        detail = e.read(4000)
        return e.code, dict(e.headers), detail
    except (urllib.error.URLError, OSError) as e:
        raise StorageError(f"Stockage injoignable : {getattr(e, 'reason', e)}")


def _form(d):
    return urllib.parse.urlencode(d).encode()


def _err(status, body, what):
    msg = None
    try:
        j = json.loads(body or b"{}")
        e = j.get("error")
        msg = j.get("error_description") or (e.get("message") if isinstance(e, dict) else e)
    except (ValueError, AttributeError):
        m = re.search(rb"<Message>([^<]{1,300})</Message>", body or b"")
        msg = m.group(1).decode("utf-8", "replace") if m else None
    return StorageError(f"{what} : {msg or 'erreur'} ({status})")


class Storage:
    """Interface commune : upload(path, name), files() -> [{id, name, size, modified}], download(f, dest), delete(f)."""

    def __init__(self, conf, save):
        self.conf = conf     # dict gardé dans les réglages (secrets compris, jamais renvoyés à l'interface)
        self.save = save     # callable(conf) : enregistre une mise à jour (jeton renouvelé…)

    def check(self):
        self.files()


# ------------------------------------------------------------------ S3
class S3(Storage):
    def _target(self, key=""):
        c = self.conf
        endpoint = (c.get("endpoint") or f"https://s3.{c.get('region') or 'us-east-1'}.amazonaws.com").rstrip("/")
        u = urllib.parse.urlparse(endpoint)
        path = u.path.rstrip("/") + f"/{c['bucket']}" + (f"/{urllib.parse.quote(key, safe='/-_.~')}" if key else "")
        return u.scheme, u.netloc, path

    def _signed(self, method, key="", query=None, body_hash="UNSIGNED-PAYLOAD", extra=None):
        c = self.conf
        scheme, host, path = self._target(key)
        region = c.get("region") or "us-east-1"
        now = datetime.datetime.now(datetime.timezone.utc)
        amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        q = "&".join(f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(str(v), safe='-_.~')}"
                     for k, v in sorted((query or {}).items()))
        headers = {"host": host, "x-amz-date": amz_date, "x-amz-content-sha256": body_hash, **(extra or {})}
        signed = ";".join(sorted(headers))
        canonical = "\n".join([method, path, q, "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)), signed, body_hash])
        scope = f"{day}/{region}/s3/aws4_request"
        to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        k = ("AWS4" + c["secret_key"]).encode()
        for part in (day, region, "s3", "aws4_request"):
            k = hmac.new(k, part.encode(), hashlib.sha256).digest()
        sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
        headers["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={c['access_key']}/{scope}, SignedHeaders={signed}, "
                                    f"Signature={sig}")
        headers.pop("host")
        return f"{scheme}://{host}{path}" + (f"?{q}" if q else ""), headers

    def _key(self, name):
        prefix = (self.conf.get("prefix") or "").strip("/")
        return f"{prefix}/{name}" if prefix else name

    def upload(self, path, name):
        size = os.path.getsize(path)
        url, h = self._signed("PUT", self._key(name), extra={"content-length": str(size)})
        h["Content-Type"] = "application/octet-stream"
        with open(path, "rb") as f:
            st, _, body = _http("PUT", url, f, h, timeout=3600)
        if st not in (200, 201):
            raise _err(st, body, "Envoi vers S3")

    def files(self):
        out, token = [], None
        prefix = (self.conf.get("prefix") or "").strip("/")
        while True:
            q = {"list-type": "2", "prefix": f"{prefix}/" if prefix else ""}
            if token:
                q["continuation-token"] = token
            url, h = self._signed("GET", query=q, body_hash=hashlib.sha256(b"").hexdigest())
            st, _, body = _http("GET", url, None, h)
            if st != 200:
                raise _err(st, body, "Liste S3")
            root = ET.fromstring(body)
            ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
            for c in root.findall(f"{ns}Contents"):
                key = c.findtext(f"{ns}Key") or ""
                name = key.rsplit("/", 1)[-1]
                out.append({"id": key, "name": name, "size": int(c.findtext(f"{ns}Size") or 0),
                            "modified": c.findtext(f"{ns}LastModified") or ""})
            token = root.findtext(f"{ns}NextContinuationToken")
            if not token or (root.findtext(f"{ns}IsTruncated") or "").lower() != "true":
                return out

    def download(self, f, dest):
        url, h = self._signed("GET", f["id"], body_hash=hashlib.sha256(b"").hexdigest())
        _stream(url, h, dest, "Téléchargement depuis S3")

    def delete(self, f):
        url, h = self._signed("DELETE", f["id"], body_hash=hashlib.sha256(b"").hexdigest())
        st, _, body = _http("DELETE", url, None, h)
        if st not in (200, 204):
            raise _err(st, body, "Suppression S3")


def _stream(url, headers, dest, what):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as out:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except urllib.error.HTTPError as e:
        raise _err(e.code, e.read(4000), what)
    except (urllib.error.URLError, OSError) as e:
        raise StorageError(f"{what} : {getattr(e, 'reason', e)}")


# ------------------------------------------------------------------ comptes reliés par code (Google, Microsoft)
class OAuthStorage(Storage):
    def device_start(self):
        raise NotImplementedError

    def device_poll(self):
        raise NotImplementedError

    def linked(self):
        return bool(self.conf.get("refresh_token"))

    def _store_tokens(self, d):
        self.conf["access_token"] = d["access_token"]
        self.conf["expires_at"] = int(time.time()) + int(d.get("expires_in") or 3600) - 120
        if d.get("refresh_token"):
            self.conf["refresh_token"] = d["refresh_token"]
        self.conf.pop("device", None)
        self.save(self.conf)

    def token(self):
        if not self.linked():
            raise StorageError("Compte non relié : reliez-le d'abord")
        if self.conf.get("access_token") and self.conf.get("expires_at", 0) > time.time():
            return self.conf["access_token"]
        st, _, body = _http("POST", self.token_url, _form(self.refresh_form()),
                            {"Content-Type": "application/x-www-form-urlencoded"})
        if st != 200:
            raise StorageError("Le compte refuse l'accès : reliez-le de nouveau")
        self._store_tokens(json.loads(body))
        return self.conf["access_token"]

    def _poll(self, form):
        dev = self.conf.get("device")
        if not dev:
            return "none"
        if time.time() > dev["expires"]:
            self.conf.pop("device", None)
            self.save(self.conf)
            return "expired"
        st, _, body = _http("POST", self.token_url, _form(form(dev["device_code"])),
                            {"Content-Type": "application/x-www-form-urlencoded"})
        try:
            d = json.loads(body or b"{}")
        except ValueError:
            d = {}
        if st == 200 and d.get("access_token"):
            self._store_tokens(d)
            return "linked"
        err = d.get("error")
        if err in ("authorization_pending", "slow_down"):
            return "pending"
        self.conf.pop("device", None)
        self.save(self.conf)
        return {"access_denied": "denied", "expired_token": "expired"}.get(err, "error")

    def _device(self, url, form):
        st, _, body = _http("POST", url, _form(form), {"Content-Type": "application/x-www-form-urlencoded"})
        if st != 200:
            raise _err(st, body, "Liaison du compte")
        d = json.loads(body)
        self.conf["device"] = {"device_code": d["device_code"], "expires": time.time() + int(d.get("expires_in") or 900)}
        self.save(self.conf)
        return {"user_code": d["user_code"], "verification_url": d.get("verification_url") or d.get("verification_uri"),
                "interval": int(d.get("interval") or 5)}


class GoogleDrive(OAuthStorage):
    token_url = GOOGLE_OAUTH + "/token"

    def refresh_form(self):
        return {"client_id": self.conf["client_id"], "client_secret": self.conf["client_secret"],
                "refresh_token": self.conf["refresh_token"], "grant_type": "refresh_token"}

    def device_start(self):
        return self._device(GOOGLE_OAUTH + "/device/code", {"client_id": self.conf["client_id"], "scope": GOOGLE_SCOPE})

    def device_poll(self):
        return self._poll(lambda code: {"client_id": self.conf["client_id"], "client_secret": self.conf["client_secret"],
                                        "device_code": code, "grant_type": DEVICE_GRANT})

    def _auth(self):
        return {"Authorization": f"Bearer {self.token()}"}

    def folder(self):
        """Dossier « Noxa » du Drive (créé par Noxa : drive.file n'y voit que ses propres fichiers)."""
        if self.conf.get("folder_id"):
            return self.conf["folder_id"]
        name = self.conf.get("folder") or "Noxa"
        q = urllib.parse.urlencode({"q": f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
                                    "fields": "files(id)"})
        st, _, body = _http("GET", f"{GOOGLE_API}/drive/v3/files?{q}", None, self._auth())
        found = json.loads(body).get("files") if st == 200 else None
        if found:
            fid = found[0]["id"]
        else:
            st, _, body = _http("POST", f"{GOOGLE_API}/drive/v3/files",
                                json.dumps({"name": name, "mimeType": "application/vnd.google-apps.folder"}).encode(),
                                {**self._auth(), "Content-Type": "application/json"})
            if st != 200:
                raise _err(st, body, "Création du dossier Google Drive")
            fid = json.loads(body)["id"]
        self.conf["folder_id"] = fid
        self.save(self.conf)
        return fid

    def upload(self, path, name):
        meta = json.dumps({"name": name, "parents": [self.folder()]}).encode()
        size = os.path.getsize(path)
        st, h, body = _http("POST", f"{GOOGLE_API}/upload/drive/v3/files?uploadType=resumable", meta,
                            {**self._auth(), "Content-Type": "application/json; charset=UTF-8",
                             "X-Upload-Content-Length": str(size)})
        location = h.get("Location") or h.get("location")
        if st != 200 or not location:
            raise _err(st, body, "Envoi vers Google Drive")
        with open(path, "rb") as f:
            st, _, body = _http("PUT", location, f, {"Content-Length": str(size)}, timeout=3600)
        if st not in (200, 201):
            raise _err(st, body, "Envoi vers Google Drive")

    def files(self):
        q = urllib.parse.urlencode({"q": f"'{self.folder()}' in parents and trashed=false", "pageSize": 1000,
                                    "fields": "files(id,name,size,modifiedTime)"})
        st, _, body = _http("GET", f"{GOOGLE_API}/drive/v3/files?{q}", None, self._auth())
        if st != 200:
            raise _err(st, body, "Liste Google Drive")
        return [{"id": f["id"], "name": f["name"], "size": int(f.get("size") or 0), "modified": f.get("modifiedTime") or ""}
                for f in json.loads(body).get("files") or []]

    def download(self, f, dest):
        _stream(f"{GOOGLE_API}/drive/v3/files/{urllib.parse.quote(f['id'])}?alt=media", self._auth(), dest,
                "Téléchargement depuis Google Drive")

    def delete(self, f):
        st, _, body = _http("DELETE", f"{GOOGLE_API}/drive/v3/files/{urllib.parse.quote(f['id'])}", None, self._auth())
        if st not in (200, 204):
            raise _err(st, body, "Suppression Google Drive")


class OneDrive(OAuthStorage):
    token_url = MS_LOGIN + "/token"

    def refresh_form(self):
        return {"client_id": self.conf["client_id"], "refresh_token": self.conf["refresh_token"],
                "grant_type": "refresh_token", "scope": MS_SCOPE}

    def device_start(self):
        return self._device(MS_LOGIN + "/devicecode", {"client_id": self.conf["client_id"], "scope": MS_SCOPE})

    def device_poll(self):
        return self._poll(lambda code: {"client_id": self.conf["client_id"], "device_code": code, "grant_type": DEVICE_GRANT})

    def _auth(self):
        return {"Authorization": f"Bearer {self.token()}"}

    def upload(self, path, name):
        st, _, body = _http("POST", f"{MS_GRAPH}/me/drive/special/approot:/{urllib.parse.quote(name)}:/createUploadSession",
                            json.dumps({"item": {"@microsoft.graph.conflictBehavior": "replace"}}).encode(),
                            {**self._auth(), "Content-Type": "application/json"})
        if st != 200:
            raise _err(st, body, "Envoi vers OneDrive")
        url = json.loads(body)["uploadUrl"]
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            pos = 0
            while pos < size or size == 0:
                chunk = f.read(CHUNK)
                end = pos + len(chunk) - 1
                st, _, body = _http("PUT", url, chunk, {"Content-Length": str(len(chunk)),
                                                        "Content-Range": f"bytes {pos}-{end}/{size}"}, timeout=600)
                if st not in (200, 201, 202):
                    raise _err(st, body, "Envoi vers OneDrive")
                pos = end + 1
                if size == 0:
                    break

    def files(self):
        out, url = [], f"{MS_GRAPH}/me/drive/special/approot/children?$select=id,name,size,lastModifiedDateTime&$top=500"
        while url:
            st, _, body = _http("GET", url, None, self._auth())
            if st != 200:
                raise _err(st, body, "Liste OneDrive")
            d = json.loads(body)
            out += [{"id": f["id"], "name": f["name"], "size": int(f.get("size") or 0),
                     "modified": f.get("lastModifiedDateTime") or ""} for f in d.get("value") or [] if "file" in f or "size" in f]
            url = d.get("@odata.nextLink")
        return out

    def download(self, f, dest):
        _stream(f"{MS_GRAPH}/me/drive/items/{urllib.parse.quote(f['id'])}/content", self._auth(), dest,
                "Téléchargement depuis OneDrive")

    def delete(self, f):
        st, _, body = _http("DELETE", f"{MS_GRAPH}/me/drive/items/{urllib.parse.quote(f['id'])}", None, self._auth())
        if st not in (200, 204):
            raise _err(st, body, "Suppression OneDrive")


def make(kind, conf, save):
    return {"s3": S3, "gdrive": GoogleDrive, "onedrive": OneDrive}[kind](conf, save)


def valid_conf(kind, b, old=None):
    """Réglages envoyés par l'administration -> conf (les secrets vides gardent l'ancienne valeur)."""
    old = dict(old or {})
    def val(k, pattern, limit=300, secret=False):
        v = str(b.get(k) if b.get(k) is not None else "").strip()
        if not v and k in old:
            return old[k]
        if not v or len(v) > limit or not re.match(pattern, v):
            raise StorageError(f"Réglage invalide : {k}")
        return v
    if kind == "s3":
        conf = {"endpoint": str(b.get("endpoint") or old.get("endpoint") or "").strip().rstrip("/"),
                "region": val("region", r"^[a-z0-9-]{2,40}$", 40),
                "bucket": val("bucket", r"^[a-z0-9][a-z0-9.\-]{1,62}$", 63),
                "prefix": str(b.get("prefix") if b.get("prefix") is not None else old.get("prefix", "nexis")).strip("/")[:200],
                "access_key": val("access_key", r"^[\x21-\x7e]{3,200}$", 200),
                "secret_key": val("secret_key", r"^[\x21-\x7e]{3,300}$", 300, True)}
        if conf["endpoint"] and not re.match(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?(/[A-Za-z0-9._~\-]+)*$", conf["endpoint"]):
            raise StorageError("Adresse S3 invalide (ex. https://s3.eu-west-3.amazonaws.com)")
        return conf
    if kind == "gdrive":
        conf = {**old, "client_id": val("client_id", r"^[A-Za-z0-9._\-]{10,200}$"),
                "client_secret": val("client_secret", r"^[\x21-\x7e]{6,200}$", 200, True),
                "folder": str(b.get("folder") or old.get("folder") or "Noxa").strip()[:100] or "Noxa"}
        if conf["client_id"] != old.get("client_id"):
            for k in ("refresh_token", "access_token", "folder_id"):
                conf.pop(k, None)
        return conf
    if kind == "onedrive":
        conf = {**old, "client_id": val("client_id", r"^[0-9a-fA-F-]{20,60}$", 60)}
        if conf["client_id"] != old.get("client_id"):
            for k in ("refresh_token", "access_token"):
                conf.pop(k, None)
        return conf
    raise StorageError("Type de stockage inconnu")


SECRET_KEYS = ("secret_key", "client_secret", "refresh_token", "access_token", "device")


def public_conf(conf):
    out = {k: v for k, v in conf.items() if k not in SECRET_KEYS and k != "expires_at"}
    out["secrets_set"] = sorted(k for k in SECRET_KEYS if conf.get(k) and k not in ("device", "access_token"))
    return out
