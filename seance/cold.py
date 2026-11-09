"""Stockage froid : des copies des films et épisodes rangées ailleurs que sur les disques de la bibliothèque.

Un titre archivé garde tout ce qui le décrit dans Nexis (affiches, résumé, historique, sous-titres téléchargés) ;
seul son fichier vidéo existe aussi (ou seulement, plus tard) sur un stockage froid. Quand le fichier de la
bibliothèque n'est plus là, Nexis lit la copie froide : directement si le stockage est un dossier de la machine
(disque USB, NAS monté, partage SMB), sinon par une adresse locale que seul ffmpeg connaît (/cold/<jeton>), qui
relit le stockage morceau par morceau (WebDAV, S3, SFTP, rclone).

Plusieurs stockages peuvent coexister ; un titre peut en avoir une copie sur chacun. Les copies se font en tâche de
fond, une à la fois, débit limité pendant une lecture, et chaque copie est vérifiée (taille, morceaux relus) avant
d'être comptée.

Types :
  - local  : un dossier de cette machine (disque USB, NAS déjà monté par le système, volume Docker) ;
  - smb    : un partage SMB/CIFS monté en écriture par seance-mountd (installation par script ou paquet) ;
  - sftp   : un serveur SSH (Hetzner Storage Box, NAS…) — par curl, clé du serveur vérifiée (empreinte retenue) ;
  - webdav : Nextcloud, Synology, Hetzner Storage Box… ;
  - s3     : AWS et compatibles (Backblaze B2, Wasabi, Scaleway, OVH, MinIO), envoi en plusieurs parties ;
  - rclone : n'importe quel « remote » rclone déjà configuré (pCloud, Mega, Google Drive…), si rclone est installé ;
  - peer   : un serveur de la fédération qui accepte d'accueillir des archives (place réservée par son administrateur),
             par le canal chiffré des serveurs liés.

Cache local : un titre lu depuis un stockage en ligne est ramené dans <données>/cold-cache, avec les épisodes qui
suivent ; la place prise reste dans un budget, les plus anciens repartent d'abord (la copie froide, elle, reste).
"""
import hashlib
import http.client
import json
import logging
import os
import random
import re
import secrets
import shutil
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from . import i18n, media, mountd
from .remote_storage import S3 as S3Signer

log = logging.getLogger("seance.cold")

KINDS = ("local", "smb", "sftp", "webdav", "s3", "rclone", "peer")
SECRETS = ("password", "secret_key")
BLOCK = 1 << 20
SIDECAR_EXT = (".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup")
SAMPLES = 6              # morceaux relus pour vérifier une copie (en plus du début et de la fin)
SAMPLE = 256 * 1024
PART = 64 * 1024 * 1024  # S3 : taille d'une partie (agrandie au-delà de ~600 Go : 10 000 parties au plus)
TOKEN_TTL = 12 * 3600
SYSTEM_DIRS = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/run", "/sbin", "/sys", "/usr", "/var/lib")


class ColdError(Exception):
    pass


def safe_part(s):
    """Nom utilisable partout (SMB, Windows, S3) : sans caractères interdits, sans « .. »."""
    s = re.sub(r'[<>:"\\|?*\x00-\x1f]', "_", str(s)).strip().rstrip(".")
    return "_" if s in ("", ".", "..") else s[:200]


def safe_name(name):
    parts = [safe_part(p) for p in str(name).replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise ColdError("Nom de fichier invalide")
    return "/".join(parts)


# ====================================================================== stockages
class Backend:
    """Interface : put(name, reader, size), size(name), open(name, start, end) -> itérateur d'octets,
    delete(name), check(), free() ; local_path(name) : chemin lisible directement (dossier), sinon None."""
    remote = True

    def __init__(self, conf, cold):
        self.conf = conf
        self.cold = cold

    def local_path(self, name):
        return None

    def free(self):
        return None

    def check(self):
        data = secrets.token_bytes(4096)
        name = f".nexis-test-{secrets.token_hex(4)}"
        self.put(name, _BytesReader(data), len(data))
        try:
            if self.size(name) != len(data):
                raise ColdError("Le fichier d'essai n'a pas la bonne taille sur le stockage")
            got = b"".join(self.open(name, 100, 199))
            if got != data[100:200]:
                raise ColdError("Le fichier d'essai relu ne correspond pas")
        finally:
            try:
                self.delete(name)
            except ColdError:
                pass


class _BytesReader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def read(self, n=-1):
        n = len(self.data) - self.pos if n is None or n < 0 else n
        b = self.data[self.pos:self.pos + n]
        self.pos += len(b)
        return b


class Local(Backend):
    remote = False

    def root(self):
        return self.conf["path"]

    def local_path(self, name):
        root = os.path.realpath(self.root())
        p = os.path.realpath(os.path.join(root, safe_name(name)))
        if not p.startswith(root + os.sep):
            raise ColdError("Chemin hors du stockage")
        return p

    def _ready(self):
        if not os.path.isdir(self.root()):
            raise ColdError(i18n.tr("Dossier du stockage introuvable : {path}").format(path=self.root()))

    def put(self, name, reader, size):
        self._ready()
        p = self.local_path(name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".nexis-part"
        try:
            with open(tmp, "wb") as out:
                while True:
                    b = reader.read(BLOCK)
                    if not b:
                        break
                    out.write(b)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, p)
        except OSError as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise ColdError(i18n.tr("Écriture impossible sur le stockage : {e}").format(e=e.strerror or e))

    def size(self, name):
        p = self.local_path(name)
        return os.path.getsize(p) if os.path.isfile(p) else None

    def open(self, name, start, end):
        with open(self.local_path(name), "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                b = f.read(min(BLOCK, left))
                if not b:
                    break
                left -= len(b)
                yield b

    def delete(self, name):
        p = self.local_path(name)
        try:
            os.remove(p)
        except FileNotFoundError:
            return
        except OSError as e:
            raise ColdError(i18n.tr("Suppression impossible : {e}").format(e=e.strerror or e))
        # dossiers devenus vides, jusqu'à la racine du stockage
        root = os.path.realpath(self.root())
        d = os.path.dirname(p)
        while d.startswith(root + os.sep):
            try:
                os.rmdir(d)
            except OSError:
                break
            d = os.path.dirname(d)

    def free(self):
        try:
            return shutil.disk_usage(self.root()).free
        except OSError:
            return None


class SMB(Local):
    """Partage monté en écriture par seance-mountd sous /mnt/seance/cold-<id>."""

    def root(self):
        return self.conf["mount"]  # le sous-dossier éventuel est monté directement à cet endroit

    def _ready(self):
        if not os.path.ismount(self.conf["mount"]):
            raise ColdError("Partage SMB du stockage non monté")
        super()._ready()


def _conn(url, timeout=60):
    u = urllib.parse.urlsplit(url)
    if u.scheme == "https":
        c = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout, context=ssl.create_default_context(),
                                        blocksize=BLOCK)
    elif u.scheme == "http":
        c = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout, blocksize=BLOCK)
    else:
        raise ColdError("Adresse invalide")
    return c, (u.path or "/") + (f"?{u.query}" if u.query else "")


def _request(method, url, headers=None, body=None, timeout=60, stream=False):
    c, path = _conn(url, timeout)
    try:
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
    except (OSError, http.client.HTTPException) as e:
        c.close()
        raise ColdError(i18n.tr("Stockage injoignable : {e}").format(e=getattr(e, "strerror", None) or e))
    if stream:
        return c, r
    try:
        return r.status, dict(r.getheaders()), r.read(1 << 20)
    finally:
        c.close()


def _iter_response(c, r):
    try:
        while True:
            b = r.read(BLOCK)
            if not b:
                break
            yield b
    finally:
        c.close()


class WebDAV(Backend):
    def _auth(self):
        import base64
        tok = base64.b64encode(f"{self.conf['username']}:{self.conf.get('password', '')}".encode()).decode()
        return {"Authorization": f"Basic {tok}"}

    def url(self, name):
        return self.conf["url"].rstrip("/") + "/" + urllib.parse.quote(safe_name(name), safe="/")

    def _check(self, st, body, what):
        if st == 401:
            raise ColdError("Identifiant ou mot de passe refusé par le serveur WebDAV")
        if st == 507:
            raise ColdError("Plus de place sur le stockage")
        if st >= 400:
            raise ColdError(f"{i18n.tr(what)} : HTTP {st}")

    def _mkdirs(self, name):
        parts = safe_name(name).split("/")[:-1]
        base = self.conf["url"].rstrip("/")
        for i in range(len(parts)):
            u = base + "/" + urllib.parse.quote("/".join(parts[:i + 1])) + "/"
            st, _, body = _request("MKCOL", u, self._auth())
            if st not in (201, 405, 301, 302) and st != 200:
                self._check(st, body, "Création d'un dossier WebDAV")

    def put(self, name, reader, size):
        self._mkdirs(name)
        tmp, final = self.url(name + ".nexis-part"), self.url(name)
        st, _, body = _request("PUT", tmp, {**self._auth(), "Content-Length": str(size),
                                            "Content-Type": "application/octet-stream"}, reader, timeout=300)
        self._check(st, body, "Envoi WebDAV")
        st, _, body = _request("MOVE", tmp, {**self._auth(), "Destination": final, "Overwrite": "T"})
        if st >= 400:
            _request("DELETE", tmp, self._auth())
        self._check(st, body, "Envoi WebDAV")

    def size(self, name):
        st, h, body = _request("PROPFIND", self.url(name), {**self._auth(), "Depth": "0",
                                                            "Content-Type": "application/xml"},
                               b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getcontentlength/>'
                               b'</d:prop></d:propfind>')
        if st == 404:
            return None
        self._check(st, body, "Lecture WebDAV")
        m = re.search(rb"getcontentlength[^>]*>\s*(\d+)\s*<", body)
        return int(m.group(1)) if m else None

    def open(self, name, start, end):
        c, r = _request("GET", self.url(name), {**self._auth(), "Range": f"bytes={start}-{end}"}, timeout=60, stream=True)
        if r.status not in (200, 206):
            c.close()
            raise ColdError(f"Lecture WebDAV : HTTP {r.status}")
        it = _iter_response(c, r)
        if r.status == 200 and start:  # serveur sans Range : on saute le début
            skip = start
            for b in it:
                if skip >= len(b):
                    skip -= len(b)
                    continue
                it = _chain([b[skip:]], it)
                break
        yield from _limit(it, end - start + 1)

    def delete(self, name):
        st, _, body = _request("DELETE", self.url(name), self._auth())
        if st not in (200, 204, 404):
            self._check(st, body, "Suppression WebDAV")

    def free(self):
        try:
            st, _, body = _request("PROPFIND", self.conf["url"].rstrip("/") + "/", {**self._auth(), "Depth": "0"},
                                   b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
                                   b'<d:quota-available-bytes/></d:prop></d:propfind>', timeout=20)
        except ColdError:
            return None
        m = re.search(rb"quota-available-bytes[^>]*>\s*(\d+)\s*<", body or b"")
        return int(m.group(1)) if st < 400 and m else None


def _chain(*its):
    for it in its:
        yield from it


def _limit(it, n):
    for b in it:
        if n <= 0:
            return
        if len(b) > n:
            b = b[:n]
        n -= len(b)
        yield b


class _Slice:
    """Une partie d'un envoi S3 : au plus n octets lus dans la source."""

    def __init__(self, reader, n):
        self.reader, self.left = reader, n

    def read(self, n=-1):
        if self.left <= 0:
            return b""
        n = self.left if n is None or n < 0 else min(n, self.left)
        b = self.reader.read(n)
        self.left -= len(b)
        return b


class S3(Backend):
    def __init__(self, conf, cold):
        super().__init__(conf, cold)
        self.signer = S3Signer(conf, lambda c: None)

    def key(self, name):
        return self.signer._key(safe_name(name))

    def _send(self, method, key, query=None, body=None, extra=None, headers=None, timeout=120, stream=False):
        url, h = self.signer._signed(method, key, query=query, extra=extra)
        h.update(headers or {})
        return _request(method, url, h, body, timeout=timeout, stream=stream)

    def _fail(self, st, body, what):
        m = re.search(rb"<Message>([^<]{1,300})</Message>", body or b"")
        raise ColdError(f"{i18n.tr(what)} : {m.group(1).decode('utf-8', 'replace') if m else ''} ({st})")

    def put(self, name, reader, size):
        key = self.key(name)
        if size <= PART:
            st, _, body = self._send("PUT", key, extra={"content-length": str(size)}, body=reader,
                                     headers={"Content-Type": "application/octet-stream"}, timeout=600)
            if st not in (200, 201):
                self._fail(st, body, "Envoi S3")
            return
        part = max(PART, -(-size // 9000))
        st, _, body = self._send("POST", key, query={"uploads": ""})
        m = re.search(rb"<UploadId>([^<]+)</UploadId>", body or b"")
        if st != 200 or not m:
            self._fail(st, body, "Envoi S3")
        upload = m.group(1).decode()
        etags = []
        try:
            n, left = 1, size
            while left > 0:
                this = min(part, left)
                st, h, body = self._send("PUT", key, query={"partNumber": n, "uploadId": upload},
                                         extra={"content-length": str(this)}, body=_Slice(reader, this), timeout=900)
                if st != 200:
                    self._fail(st, body, "Envoi S3")
                etags.append((n, {k.lower(): v for k, v in h.items()}.get("etag", "")))
                n, left = n + 1, left - this
            xml = "<CompleteMultipartUpload>" + "".join(
                f"<Part><PartNumber>{i}</PartNumber><ETag>{e}</ETag></Part>" for i, e in etags) + "</CompleteMultipartUpload>"
            st, _, body = self._send("POST", key, query={"uploadId": upload}, body=xml.encode(), timeout=300)
            if st != 200 or b"<Error>" in (body or b""):
                self._fail(st, body, "Envoi S3")
        except BaseException:
            try:
                self._send("DELETE", key, query={"uploadId": upload})
            except ColdError:
                pass
            raise

    def size(self, name):
        st, h, body = self._send("HEAD", self.key(name))
        if st == 404:
            return None
        if st != 200:
            self._fail(st, body, "Lecture S3")
        return int({k.lower(): v for k, v in h.items()}.get("content-length") or 0)

    def open(self, name, start, end):
        c, r = self._send("GET", self.key(name), headers={"Range": f"bytes={start}-{end}"}, timeout=60, stream=True)
        if r.status not in (200, 206):
            body = r.read(4000)
            c.close()
            self._fail(r.status, body, "Lecture S3")
        yield from _iter_response(c, r)

    def delete(self, name):
        st, _, body = self._send("DELETE", self.key(name))
        if st not in (200, 204, 404):
            self._fail(st, body, "Suppression S3")


def curl_sftp():
    """curl de cette machine sait-il faire du SFTP ?"""
    exe = shutil.which("curl")
    if not exe:
        return False
    try:
        out = subprocess.run([exe, "-V"], capture_output=True, timeout=10).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(re.search(r"^Protocols:.*\bsftp\b", out, re.M))


def _pump(p, reader, timeout=None):
    """Envoie la source sur l'entrée du programme (curl, rclone) en lisant ses sorties à côté ; -> (sortie, erreurs).
    (communicate() refermerait l'entrée dès le premier morceau.)"""
    out, err = [], []
    readers = [threading.Thread(target=lambda: out.append(p.stdout.read()), daemon=True),
               threading.Thread(target=lambda: err.append(p.stderr.read()), daemon=True)]
    for t in readers:
        t.start()
    try:
        if reader is not None:
            while True:
                b = reader.read(BLOCK)
                if not b:
                    break
                p.stdin.write(b)
    except BrokenPipeError:
        pass  # le programme a abandonné : son message d'erreur le dira
    except BaseException:
        p.kill()
        raise
    finally:
        if p.stdin:
            try:
                p.stdin.close()
            except OSError:
                pass
    try:
        p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        raise ColdError("Délai dépassé")
    for t in readers:
        t.join(10)
    for f in (p.stdout, p.stderr):
        if f:
            f.close()
    return (out[0] if out else b""), (err[0] if err else b"")


def _curl_quote(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


class SFTP(Backend):
    """Par curl (libssh) : identifiants passés par un descripteur (jamais sur la ligne de commande), clé du serveur
    vérifiée (empreinte MD5 retenue au premier essai, confirmée par l'administrateur)."""

    def _path(self, name=""):
        base = (self.conf.get("path") or "").strip().strip("/")
        return "/".join(p for p in (base, safe_name(name) if name else "") if p)

    def url(self, name="", folder=False):
        c = self.conf
        rel = "/".join(p for p in ((c.get("path") or "").strip().strip("/"), name) if p)
        absolute = (c.get("path") or "").startswith("/")
        path = ("/" if absolute else "/~/") + urllib.parse.quote(rel, safe="/")
        if folder or not name:
            path = path.rstrip("/") + "/"
        return f"sftp://{c['host']}:{int(c.get('port') or 22)}{path}"

    def _run(self, args, stdin=None, check_key=True, timeout=None, stream=False):
        r, w = os.pipe()
        cfg = f"user = {_curl_quote(self.conf['username'] + ':' + self.conf.get('password', ''))}\n".encode()
        os.write(w, cfg)
        os.close(w)
        key = self.conf.get("hostkey") if check_key else "0" * 32
        cmd = [shutil.which("curl") or "curl", "-sS", "-K", f"/dev/fd/{r}", "--connect-timeout", "20",
               "--hostpubmd5", key or "0" * 32, *args]
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=(r,))
        finally:
            os.close(r)
        if stream:
            return p
        out, err = _pump(p, stdin, timeout)
        if p.returncode != 0:
            raise ColdError(self._explain(err.decode("utf-8", "replace")))
        return out

    @staticmethod
    def _explain(err):
        if "mismatch md5 fingerprint" in err:
            return i18n.tr("La clé du serveur SFTP a changé : vérifiez le serveur, puis testez de nouveau le stockage")
        if re.search(r"Login denied|Authentication failure|access denied", err, re.I):
            return i18n.tr("Identifiant ou mot de passe refusé par le serveur SFTP")
        if re.search(r"Could not resolve|Failed to connect|Connection refused|timed out", err, re.I):
            return i18n.tr("Serveur SFTP injoignable")
        lines = [l for l in err.strip().splitlines() if l.strip()]
        return i18n.tr("SFTP : {e}").format(e=(lines[0] if lines else "erreur")[:200])

    def fingerprint(self):
        """Empreinte MD5 de la clé du serveur (curl la donne en refusant une empreinte fausse)."""
        p = self._run(["-o", "/dev/null", self.url()], check_key=False, stream=True)
        _, err = p.communicate(timeout=40)
        p.stdout.close()
        p.stderr.close()
        m = re.search(r"Remote ([0-9a-f]{32}) is not equal", err.decode("utf-8", "replace"))
        if not m:
            raise ColdError(self._explain(err.decode("utf-8", "replace")))
        return m.group(1)

    def put(self, name, reader, size):
        self._run(["--ftp-create-dirs", "-T", "-", self.url(safe_name(name))], stdin=reader)

    def size(self, name):
        parent, _, base = safe_name(name).rpartition("/")
        try:
            out = self._run([self.url(parent, folder=True)], timeout=60)
        except ColdError as e:
            if "No such file" in str(e) or "not found" in str(e).lower():
                return None
            raise
        for line in out.decode("utf-8", "replace").splitlines():
            f = line.split(None, 8)
            if len(f) == 9 and f[8] == base and line[:1] == "-":
                return int(f[4])
        return None

    def open(self, name, start, end):
        p = self._run(["-r", f"{start}-{end}", self.url(safe_name(name))], stream=True)
        try:
            while True:
                b = p.stdout.read(BLOCK)
                if not b:
                    break
                yield b
        finally:
            if p.poll() is None:
                p.kill()
            p.wait()
            for f in (p.stdout, p.stderr):
                if f:
                    f.close()

    def delete(self, name):
        target = self._path(name)
        absolute = (self.conf.get("path") or "").startswith("/")
        try:
            self._run(["-Q", f"rm {_curl_quote(('/' if absolute else '') + target)}", "-o", "/dev/null", self.url()], timeout=60)
        except ColdError as e:
            if "No such file" not in str(e):
                raise


def rclone_exe():
    return shutil.which("rclone")


class Rclone(Backend):
    def target(self, name=""):
        remote = self.conf["remote"].rstrip("/")
        return f"{remote}/{safe_name(name)}" if name else remote

    def _cmd(self, *args):
        cmd = [rclone_exe() or "rclone", *args]
        if self.conf.get("config"):
            cmd += ["--config", self.conf["config"]]
        return cmd

    def _run(self, args, stdin=None, timeout=None):
        try:
            p = subprocess.Popen(self._cmd(*args), stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError:
            raise ColdError("rclone n'est pas installé sur ce serveur")
        out, err = _pump(p, stdin, timeout)
        if p.returncode != 0:
            lines = [l for l in err.decode("utf-8", "replace").splitlines() if l.strip()]
            raise ColdError(i18n.tr("rclone : {e}").format(e=(lines[-1] if lines else "erreur")[-200:]))
        return out

    def put(self, name, reader, size):
        self._run(["rcat", "--size", str(size), self.target(name)], stdin=reader)

    def size(self, name):
        try:
            out = self._run(["lsjson", "--stat", self.target(name)], timeout=120)
        except ColdError as e:
            if re.search(r"not found|doesn't exist|no such", str(e), re.I):
                return None
            raise
        try:
            return int(json.loads(out or b"{}").get("Size"))
        except (ValueError, TypeError):
            return None

    def open(self, name, start, end):
        try:
            p = subprocess.Popen(self._cmd("cat", "--offset", str(start), "--count", str(end - start + 1), self.target(name)),
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        except OSError:
            raise ColdError("rclone n'est pas installé sur ce serveur")
        try:
            while True:
                b = p.stdout.read(BLOCK)
                if not b:
                    break
                yield b
        finally:
            if p.poll() is None:
                p.kill()
            p.wait()
            for f in (p.stdout, p.stderr):
                if f:
                    f.close()

    def delete(self, name):
        try:
            self._run(["deletefile", self.target(name)], timeout=120)
        except ColdError as e:
            if not re.search(r"not found|doesn't exist|no such", str(e), re.I):
                raise


PEER_CHUNK = 16 * 1024 * 1024


class Peer(Backend):
    """Archives gardées par un serveur lié (canal chiffré des serveurs liés, morceaux de 16 Mo)."""

    def _fed(self):
        from .federation import FedError
        fed = self.cold.ctx.fed
        try:
            return fed, fed.peer(int(self.conf["peer"])), FedError
        except FedError as e:
            raise ColdError(str(e))

    def _req(self, method, path, body=None, headers=None, timeout=120):
        fed, peer, FedError = self._fed()
        try:
            return fed.request(peer, method, path, body=body, headers=headers, timeout=timeout)
        except FedError as e:
            raise ColdError(str(e))

    def _json(self, method, path, body=None, ok=(200,)):
        resp, close = self._req(method, path, body)
        try:
            raw = resp.read(1 << 20)
        except OSError as e:
            raise ColdError(i18n.tr("Stockage injoignable : {e}").format(e=e))
        finally:
            close()
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {}
        if resp.status == 404 and 404 in ok:
            return None
        if resp.status not in ok:
            raise ColdError(data.get("error") or f"HTTP {resp.status}")
        return data

    @staticmethod
    def _q(name):
        return urllib.parse.quote(safe_name(name), safe="")

    def put(self, name, reader, size):
        off, q = 0, self._q(name)
        while True:
            chunk = bytearray()
            while len(chunk) < PEER_CHUNK:
                b = reader.read(min(BLOCK, PEER_CHUNK - len(chunk)))
                if not b:
                    break
                chunk += b
            if chunk or not off:
                self._json("PUT", f"/fed/cold/blob?name={q}&offset={off}", bytes(chunk))
                off += len(chunk)
            if not chunk or off >= size:
                break
        self._json("POST", f"/fed/cold/commit?name={q}&size={size}")

    def size(self, name):
        r = self._json("GET", f"/fed/cold/stat?name={self._q(name)}", ok=(200, 404))
        return None if r is None else int(r.get("size") or 0)

    def open(self, name, start, end):
        resp, close = self._req("GET", f"/fed/cold/blob?name={self._q(name)}", headers={"Range": f"bytes={start}-{end}"})
        try:
            if resp.status not in (200, 206):
                raise ColdError(f"HTTP {resp.status}")
            while True:
                b = resp.read(BLOCK)
                if not b:
                    break
                yield b
        finally:
            close()

    def delete(self, name):
        self._json("DELETE", f"/fed/cold/blob?name={self._q(name)}", ok=(200, 404))

    def free(self):
        try:
            return (self._json("GET", "/fed/cold/info") or {}).get("free")
        except ColdError:
            return None


BACKENDS = {"local": Local, "smb": SMB, "sftp": SFTP, "webdav": WebDAV, "s3": S3, "rclone": Rclone, "peer": Peer}


# ====================================================================== réglages d'un stockage
def valid_conf(kind, b, old=None):
    """Réglages envoyés par l'administration -> conf (un secret laissé vide garde l'ancienne valeur)."""
    old = dict(old or {})

    def txt(k, pattern, limit=300, required=True):
        v = str(b.get(k) if b.get(k) is not None else old.get(k, "")).strip()
        if not v and not required:
            return ""
        if not v or len(v) > limit or not re.match(pattern, v):
            raise ColdError(i18n.tr("Réglage invalide : {k}").format(k=k))
        return v

    def secret(k):
        v = b.get(k)
        return old.get(k, "") if v in (None, "") else str(v)[:500]

    if kind == "local":
        path = os.path.normpath(txt("path", r"^/.{0,1000}$", 1000))
        if path == "/" or any(path == d or path.startswith(d + "/") for d in SYSTEM_DIRS):
            raise ColdError("Ce dossier ne peut pas servir de stockage")
        return {"path": path}
    if kind == "smb":
        return {"host": txt("host", r"^[A-Za-z0-9.\-\[\]:]{1,253}$", 253), "share": txt("share", r"^[^/\\]{1,80}$", 80),
                "subdir": txt("subdir", r"^[^\\]{0,200}$", 200, False).strip("/"),
                "username": txt("username", r"^[^\n\r]{1,100}$", 100), "domain": txt("domain", r"^[A-Za-z0-9._-]{0,64}$", 64, False),
                "version": txt("version", r"^(auto|1\.0|2\.0|2\.1|3\.0|3\.1\.1)$", 10, False) or "auto",
                "password": secret("password")}
    if kind == "sftp":
        port = int(b.get("port") or old.get("port") or 22)
        if not 1 <= port <= 65535:
            raise ColdError("Port invalide")
        conf = {"host": txt("host", r"^[A-Za-z0-9.\-]{1,253}$", 253), "port": port,
                "username": txt("username", r"^[A-Za-z0-9._@+-]{1,100}$", 100),
                "path": re.sub(r"^~/?", "", txt("path", r"^[^\n\r\\]{0,300}$", 300, False)) or "nexis",
                "password": secret("password"),
                "hostkey": str(b.get("hostkey") or old.get("hostkey") or "").lower()}
        if conf["hostkey"] and not re.match(r"^[0-9a-f]{32}$", conf["hostkey"]):
            raise ColdError("Empreinte de la clé du serveur invalide")
        if old.get("host") and (old.get("host"), old.get("port")) != (conf["host"], conf["port"]) and not b.get("hostkey"):
            conf["hostkey"] = ""  # autre serveur : nouvelle empreinte à confirmer
        return conf
    if kind == "webdav":
        url = txt("url", r"^https?://[^\s]{3,1000}$", 1000)
        return {"url": url.rstrip("/"), "username": txt("username", r"^[^\n\r:]{1,200}$", 200), "password": secret("password")}
    if kind == "s3":
        conf = {"endpoint": str(b.get("endpoint") if b.get("endpoint") is not None else old.get("endpoint", "")).strip().rstrip("/"),
                "region": txt("region", r"^[a-z0-9-]{2,40}$", 40),
                "bucket": txt("bucket", r"^[a-z0-9][a-z0-9.\-]{1,62}$", 63),
                "prefix": str(b.get("prefix") if b.get("prefix") is not None else old.get("prefix", "nexis-archive")).strip("/")[:200],
                "access_key": txt("access_key", r"^[\x21-\x7e]{3,200}$", 200),
                "secret_key": secret("secret_key")}
        if conf["endpoint"] and not re.match(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?(/[A-Za-z0-9._~\-]+)*$", conf["endpoint"]):
            raise ColdError("Adresse S3 invalide (ex. https://s3.eu-west-3.amazonaws.com)")
        if not conf["secret_key"]:
            raise ColdError("Clé secrète S3 requise")
        return conf
    if kind == "rclone":
        conf = {"remote": txt("remote", r"^[A-Za-z0-9_. -]{1,64}:[^\n\r]{0,300}$", 370),
                "config": txt("config", r"^/[^\n\r]{1,500}$", 500, False)}
        return conf
    if kind == "peer":
        try:
            return {"peer": int(b.get("peer") or old.get("peer") or 0)}
        except (TypeError, ValueError):
            raise ColdError("Serveur lié invalide")
    raise ColdError("Type de stockage inconnu")


def public_conf(conf):
    out = {k: v for k, v in conf.items() if k not in SECRETS}
    out["secrets_set"] = sorted(k for k in SECRETS if conf.get(k))
    return out


# ====================================================================== lecture de la source pendant une copie
class _Reader:
    """Source d'une copie : empreinte SHA-256, progression, débit limité pendant une lecture, annulation."""

    def __init__(self, it, job, cold):
        self.it = iter(it)
        self.buf = b""
        self.job = job
        self.cold = cold
        self.sha = hashlib.sha256()
        self.done = 0
        self.t0 = time.monotonic()
        self.sent_since = 0
        self.rate_at = 0
        self.rate = 0

    def read(self, n=-1):
        if self.job.get("cancel"):
            raise ColdError("Archivage annulé")
        n = BLOCK if n is None or n < 0 else n
        while len(self.buf) < n:
            try:
                self.buf += next(self.it)
            except StopIteration:
                break
        b, self.buf = self.buf[:n], self.buf[n:]
        if b:
            self.sha.update(b)
            self.done += len(b)
            self.job["done"] = self.done
            self._throttle(len(b))
        return b

    def _throttle(self, n):
        now = time.monotonic()
        if now - self.rate_at > 3:
            self.rate_at = now
            self.rate = self.cold.rate_limit()
            self.t0, self.sent_since = now, 0
        self.sent_since += n
        if self.rate:
            ahead = self.sent_since / self.rate - (now - self.t0)
            if ahead > 0:
                time.sleep(min(ahead, 2))


MIN_GAIN = 0.9  # compressé, le fichier doit faire moins de 90 % de l'original (sinon l'original est gardé)
QUALITY = {"high": (22, 21), "balanced": (26, 24), "small": (30, 27)}  # (carte graphique : cq, processeur : crf)


def compress_cmd(src, out, probe, hw, quality, height):
    """ffmpeg : vidéo en HEVC, tout le reste recopié (MKV)."""
    from .transcoder import hw_input
    v = probe["video"]
    vi = v["index"]
    ten = "10" in (v.get("pix_fmt") or "")
    cq, crf = QUALITY.get(quality, QUALITY["balanced"])
    cmd = [media.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-progress", "pipe:1", "-y"]
    if hw:
        cmd += hw_input(hw)
    cmd += [*media.input_args(src), "-map", "0", "-map", "-0:d", "-c", "copy", "-map_metadata", "0", "-map_chapters", "0",
            "-max_muxing_queue_size", "4096"]
    for sub in probe.get("subs") or []:
        if sub.get("codec") == "mov_text":  # sous-titres d'un MP4 : du texte que le MKV n'accepte qu'en SRT
            cmd += [f"-c:{sub['index']}", "srt"]
    vf = [f"scale=-2:{height}"] if height and (v.get("height") or 0) > height else []
    if hw and hw["engine"] == "vaapi":
        vf += ["format=p010" if ten else "format=nv12", "hwupload"]
    if vf:
        cmd += [f"-filter:{vi}", ",".join(vf)]
    if hw and hw["engine"] == "nvenc":
        cmd += [f"-c:{vi}", "hevc_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(cq), "-b:v", "0"]
        if ten:
            cmd += [f"-pix_fmt:{vi}", "p010le"]
    elif hw:
        cmd += [f"-c:{vi}", "hevc_vaapi", "-rc_mode", "CQP", "-qp", str(cq)]
    else:
        cmd += [f"-c:{vi}", "libx265", "-preset", "medium", "-crf", str(crf), "-x265-params", "log-level=error",
                f"-pix_fmt:{vi}", "yuv420p10le" if ten else "yuv420p"]
    return cmd + [out]


def _file_blocks(path, start=0, end=None):
    with open(path, "rb") as f:
        f.seek(start)
        left = None if end is None else end - start + 1
        while left is None or left > 0:
            b = f.read(BLOCK if left is None else min(BLOCK, left))
            if not b:
                break
            if left is not None:
                left -= len(b)
            yield b


# ====================================================================== le service
class Cold:
    def __init__(self, ctx):
        self.ctx = ctx
        self.lock = threading.Lock()
        self.tokens = {}       # jeton -> {store, name, size, expires}
        self.running = None    # tâche en cours (dict partagé avec _Reader : done, cancel)
        self.worker = None
        self.wake = threading.Event()
        self.fetch_queue = []  # titres à ramener en local
        self.fetching = None
        self.fetcher = None

    # ---------------------------------------------------------------- stockages
    def stores(self, enabled_only=False):
        rows = self.ctx.db.all("SELECT * FROM cold_stores" + (" WHERE enabled=1" if enabled_only else "")
                               + " ORDER BY position, id")
        for r in rows:
            r["conf"] = json.loads(r["conf"] or "{}")
        return rows

    def store(self, sid):
        r = self.ctx.db.one("SELECT * FROM cold_stores WHERE id=?", (sid,))
        if r:
            r["conf"] = json.loads(r["conf"] or "{}")
        return r

    def backend(self, store):
        cls = BACKENDS.get(store["kind"])
        if not cls:
            raise ColdError("Type de stockage inconnu")
        return cls(store["conf"], self)

    def available(self):
        """Types utilisables sur cette installation (et pourquoi pas)."""
        smb_ok = os.path.exists(os.environ.get("SEANCE_MOUNTD_SOCK") or mountd.SOCK) and not os.environ.get("SEANCE_DOCKER")
        return {"local": None,
                "smb": None if smb_ok else i18n.tr("Indisponible ici (Docker, ou service de montage absent) : montez le "
                                                   "partage sur la machine, puis choisissez « Dossier de cette machine »."),
                "sftp": None if curl_sftp() else i18n.tr("curl avec SFTP est nécessaire (apt install curl)."),
                "webdav": None, "s3": None,
                "rclone": None if rclone_exe() else i18n.tr("rclone n'est pas installé (apt install rclone, puis rclone config)."),
                "peer": None if self.ctx.db.scalar("SELECT 1 FROM peers LIMIT 1") else i18n.tr("Aucun serveur lié : reliez d'abord un autre serveur Nexis.")}

    # ---------------------------------------------------------------- copies
    def copies(self, item_id):
        return self.ctx.db.all(
            "SELECT c.*, s.name AS store_name, s.kind, s.enabled, s.position FROM cold_copies c "
            "JOIN cold_stores s ON s.id=c.store_id WHERE c.item_id=? ORDER BY s.position, s.id", (item_id,))

    def local_missing(self, row):
        key = row.get("key") if isinstance(row, dict) else row["key"]
        return bool(key) and not media.is_strm(key) and not os.path.isfile(key)

    def source(self, row, prefer_cold=False):
        """Ce que ffmpeg doit lire pour cet élément : son fichier, sinon sa copie ramenée en local (cache), sinon une
        copie froide (chemin d'un dossier, ou « cold:http://127.0.0.1:…/cold/<jeton> »). None si rien n'est lisible."""
        return self.resolve(row, prefer_cold)[0]

    def resolve(self, row, prefer_cold=False):
        """-> (source, description) : description (probe) propre à une copie compressée, None sinon (celle de la fiche)."""
        key = row["key"]
        if not prefer_cold and key and (media.is_strm(key) or os.path.isfile(key)):
            return key, None
        if not prefer_cold:
            cached = self.cached(row["id"])
            if cached:
                return cached, self._probe_of(row, self.ctx.db.scalar("SELECT probe FROM cold_cache WHERE item_id=?", (row["id"],)))
        copies = [c for c in self.copies(row["id"]) if c["enabled"] and c["verified_at"]]
        # dossiers (disque, partage monté) d'abord : lecture directe, la plus rapide
        copies.sort(key=lambda c: (c["kind"] not in ("local", "smb"), c["position"]))
        for c in copies:
            store = self.store(c["store_id"])
            try:
                b = self.backend(store)
                lp = b.local_path(c["name"])
            except ColdError:
                continue
            if lp is not None:
                if os.path.isfile(lp):
                    return lp, self._probe_of(row, c["probe"])
                continue
            return "cold:" + self.stream_url(c["store_id"], c["name"], c["size"]), self._probe_of(row, c["probe"])
        return (key, None) if key and os.path.isfile(key) else (None, None)

    @staticmethod
    def _probe_of(row, copy_probe):
        """Description d'une copie compressée, avec les sous-titres externes de la fiche (fichiers à part)."""
        if not copy_probe:
            return None
        p = json.loads(copy_probe) if isinstance(copy_probe, str) else dict(copy_probe)
        try:
            item = json.loads(row["probe"] or "{}") if isinstance(row["probe"], str) else (row["probe"] or {})
        except (KeyError, IndexError, TypeError, ValueError):
            item = {}
        p["external"] = item.get("external", [])
        return json.dumps(p)

    def stream_url(self, store_id, name, size):
        now = time.time()
        with self.lock:
            for t in [t for t, v in self.tokens.items() if v["expires"] < now]:
                del self.tokens[t]
            tok = secrets.token_urlsafe(24)
            self.tokens[tok] = {"store": store_id, "name": name, "size": size, "expires": now + TOKEN_TTL}
        port = getattr(self.ctx, "http_port", None) or int(os.environ.get("SEANCE_PORT", "8420"))
        return f"http://127.0.0.1:{port}/cold/{tok}"

    def token(self, tok):
        with self.lock:
            v = self.tokens.get(tok)
        return v if v and v["expires"] >= time.time() else None


    # ---------------------------------------------------------------- cache local (titres ramenés)
    def cache_conf(self):
        db = self.ctx.db
        return {"gb": float(db.setting("cold_cache_gb", "20") or 0), "keep_free_gb": float(db.setting("cold_cache_free_gb", "10") or 0),
                "next": int(db.setting("cold_prefetch", "2") or 0)}

    def cache_dir(self):
        return os.path.join(self.ctx.data_dir, "cold-cache")

    def cached(self, item_id):
        r = self.ctx.db.one("SELECT * FROM cold_cache WHERE item_id=?", (item_id,))
        if not r:
            return None
        if not os.path.isfile(r["path"]) or os.path.getsize(r["path"]) != r["size"]:
            self.ctx.db.run("DELETE FROM cold_cache WHERE item_id=?", (item_id,))
            return None
        self.ctx.db.run("UPDATE cold_cache SET last_used=? WHERE item_id=?", (int(time.time()), item_id))
        return r["path"]

    def prefetch(self, row):
        """Lecture depuis un stockage en ligne : ce titre, et les épisodes suivants de la série, ramenés en local."""
        c = self.cache_conf()
        if c["gb"] <= 0:
            return 0
        db = self.ctx.db
        ids = [row["id"]]
        if row.get("type") == "episode" and row.get("show_id") and c["next"] > 0:
            eps = db.all("SELECT e.id FROM items e LEFT JOIN items s ON s.id=e.parent_id WHERE e.show_id=? AND e.type='episode' "
                         "ORDER BY COALESCE(s.idx, 0)=0, s.idx, e.idx", (row["show_id"],))
            order = [e["id"] for e in eps]
            if row["id"] in order:
                ids += order[order.index(row["id"]) + 1:order.index(row["id"]) + 1 + c["next"]]
        added = 0
        with self.lock:
            for i in ids:
                if i not in self.fetch_queue and i != self.fetching:
                    self.fetch_queue.append(i)
                    added += 1
            if self.fetch_queue and not (self.fetcher and self.fetcher.is_alive()):
                self.fetcher = threading.Thread(target=self._fetch_loop, daemon=True, name="cold-cache")
                self.fetcher.start()
        return added

    def _fetch_loop(self):
        db = self.ctx.db
        try:
            while True:
                with self.lock:
                    if not self.fetch_queue:
                        self.fetching = None
                        return
                    self.fetching = self.fetch_queue.pop(0)
                try:
                    self._fetch(self.fetching)
                except Exception as e:
                    log.info("Titre %s non ramené en local : %s", self.fetching, e)
        finally:
            db.release()

    def _fetch(self, item_id):
        db = self.ctx.db
        row = db.one("SELECT id, key FROM items WHERE id=?", (item_id,))
        if not row or not self.local_missing(row) or self.cached(item_id):
            return
        # seulement les copies en ligne : un dossier ou un partage monté se lit déjà directement
        copies = [cp for cp in self.copies(item_id) if cp["enabled"] and cp["verified_at"] and cp["kind"] not in ("local", "smb")]
        if not copies:
            return
        cp = copies[0]
        if not self._make_room(cp["size"]):
            log.info("Pas de place pour ramener %s en local", cp["name"])
            return
        b = self.backend(self.store(cp["store_id"]))
        os.makedirs(self.cache_dir(), exist_ok=True)
        path = os.path.join(self.cache_dir(), f"{item_id}{os.path.splitext(cp['name'])[1] or '.mkv'}")
        tmp = path + ".part"
        sha = hashlib.sha256()
        try:
            with open(tmp, "wb") as out:
                for blk in b.open(cp["name"], 0, cp["size"] - 1):
                    out.write(blk)
                    sha.update(blk)
            if os.path.getsize(tmp) != cp["size"] or (cp["sha256"] and sha.hexdigest() != cp["sha256"]):
                raise ColdError("Copie ramenée incomplète ou altérée")
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        now = int(time.time())
        db.run("INSERT OR REPLACE INTO cold_cache(item_id, path, size, fetched_at, last_used, probe) VALUES(?,?,?,?,?,?)",
               (item_id, path, cp["size"], now, now, cp["probe"]))

    def _make_room(self, need):
        """Place pour `need` octets dans le budget du cache (et en gardant de la place libre sur le disque) :
        les titres ramenés depuis le plus longtemps sans lecture repartent d'abord."""
        c = self.cache_conf()
        db = self.ctx.db
        budget = c["gb"] * 1e9
        if need > budget:
            return False
        playing = {int(r["rating_key"]) for r in db.all("SELECT rating_key FROM streams WHERE last_seen>?", (int(time.time()) - 300,))
                   if str(r["rating_key"]).isdigit()}
        rows = db.all("SELECT * FROM cold_cache ORDER BY last_used")
        used = sum(r["size"] for r in rows)
        os.makedirs(self.cache_dir(), exist_ok=True)

        def free_disk():
            try:
                return shutil.disk_usage(self.cache_dir()).free
            except OSError:
                return 0
        for r in rows:
            if used + need <= budget and free_disk() - need >= c["keep_free_gb"] * 1e9:
                break
            if r["item_id"] in playing:
                continue
            self.uncache(r["item_id"])
            used -= r["size"]
        return used + need <= budget and free_disk() - need >= c["keep_free_gb"] * 1e9

    def uncache(self, item_id):
        r = self.ctx.db.one("SELECT path FROM cold_cache WHERE item_id=?", (item_id,))
        if r:
            try:
                os.remove(r["path"])
            except OSError:
                pass
            self.ctx.db.run("DELETE FROM cold_cache WHERE item_id=?", (item_id,))

    def cache_status(self):
        r = self.ctx.db.one("SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS bytes FROM cold_cache")
        return {**self.cache_conf(), "count": r["n"], "bytes": r["bytes"], "fetching": self.fetching, "queued": len(self.fetch_queue)}

    # ---------------------------------------------------------------- archives d'autres serveurs (hébergées ici)
    def hosted_dir(self, peer_id):
        return os.path.join(self.ctx.data_dir, "cold-hosted", str(int(peer_id)))

    def hosted_used(self):
        root = os.path.join(self.ctx.data_dir, "cold-hosted")
        total = 0
        for d, _, files in os.walk(root):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(d, f))
                except OSError:
                    pass
        return total

    def hosted_quota(self):
        return float(self.ctx.db.setting("cold_host_gb", "0") or 0) * 1e9


    # ---------------------------------------------------------------- compression des archives (HEVC)
    def compress_conf(self):
        db = self.ctx.db
        q = db.setting("cold_compress_quality", "balanced")
        return {"enabled": db.setting("cold_compress", "0") == "1", "quality": q if q in QUALITY else "balanced",
                "height": int(db.setting("cold_compress_height", "0") or 0)}

    @staticmethod
    def compressible(probe):
        """(oui ?, raison) : vidéo déjà compacte (HEVC, AV1, VP9) ou HDR (gardée telle quelle pour ne rien abîmer)."""
        from .transcoder import HDR_TRANSFERS
        v = (probe or {}).get("video")
        if not v:
            return False, i18n.tr("pas de vidéo")
        if v.get("codec") in ("hevc", "av1", "vp9"):
            return False, i18n.tr("déjà en {codec}").format(codec=v["codec"].upper())
        if v.get("transfer") in HDR_TRANSFERS or "dovi" in (v.get("profile") or "").lower():
            return False, i18n.tr("vidéo HDR, gardée telle quelle")
        return True, None

    def _viewers_converting(self):
        try:
            return bool(self.ctx.db.scalar("SELECT 1 FROM streams WHERE last_seen>? AND json_extract(params, '$.mode')='transcode' LIMIT 1",
                                           (int(time.time()) - 40,)))
        except Exception:
            return False

    def compress(self, src, row, job):
        """Réencode la vidéo en HEVC (carte graphique si configurée, sinon processeur), sons, sous-titres, chapitres et
        polices recopiés tels quels. -> (fichier, description) ; None si ce n'est pas utile (raison dans job["note"])."""
        probe = json.loads(row["probe"] or "{}") if isinstance(row["probe"], str) else (row["probe"] or {})
        ok, why = self.compressible(probe)
        if not ok:
            job["note"] = i18n.tr("Sans compression : {why}").format(why=why)
            return None
        work = os.path.join(self.ctx.data_dir, "cold-work")
        os.makedirs(work, exist_ok=True)
        need = (row["file_size"] or 0) + 2 * 1024 ** 3
        if shutil.disk_usage(work).free < need:
            job["note"] = i18n.tr("Sans compression : pas assez de place pour le fichier de travail ({n} Go)").format(n=round(need / 1e9, 1))
            return None
        c = self.compress_conf()
        hw = self.ctx.hw() if hasattr(self.ctx, "hw") else None
        out = os.path.join(work, f"{row['id']}-{secrets.token_hex(4)}.mkv")
        cmd = compress_cmd(src, out, probe, hw, c["quality"], c["height"])
        job.update(phase="compress", pct=0, hw=bool(hw))
        duration = float(probe.get("duration") or 0) or 1
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                 preexec_fn=lambda: os.nice(19))
        except OSError as e:
            job["note"] = i18n.tr("Sans compression : {why}").format(why=e)
            return None
        err = []
        threading.Thread(target=lambda: err.append(p.stderr.read()), daemon=True).start()
        stop = threading.Event()

        def watch():
            # annulation ; et par le processeur, pause pendant qu'une lecture est convertie (elle passe d'abord)
            paused = False
            while not stop.wait(2):
                if job.get("cancel"):
                    p.kill()
                    return
                busy = not hw and self._viewers_converting()
                if busy != paused:
                    try:
                        os.kill(p.pid, 19 if busy else 18)  # SIGSTOP / SIGCONT
                        paused = busy
                        job["paused"] = busy
                    except OSError:
                        return
        threading.Thread(target=watch, daemon=True).start()
        try:
            for line in p.stdout:
                m = re.match(rb"out_time_us=(\d+)", line)
                if m:
                    job["pct"] = min(1.0, int(m.group(1)) / 1e6 / duration)
            p.wait()
        finally:
            stop.set()
            if p.poll() is None:
                p.kill()
                p.wait()
            p.stdout.close()
        job["phase"] = "copy"
        if job.get("cancel"):
            self._rm(out)
            raise ColdError("Archivage annulé")
        if p.returncode != 0 or not os.path.isfile(out):
            msg = (err[0] if err else b"").decode("utf-8", "replace").strip().splitlines()[-1:] or ["?"]
            log.warning("Compression de %s impossible : %s", row["key"], msg[0])
            job["note"] = i18n.tr("Sans compression : {why}").format(why=msg[0][:200])
            self._rm(out)
            return None
        src_size = row["file_size"] or 0
        if src_size and os.path.getsize(out) > MIN_GAIN * src_size:
            job["note"] = i18n.tr("Sans compression : gain trop faible")
            self._rm(out)
            return None
        try:
            new = media.probe(out)
        except media.MediaError as e:
            self._rm(out)
            job["note"] = i18n.tr("Sans compression : {why}").format(why=e)
            return None
        return out, new

    @staticmethod
    def _rm(path):
        try:
            os.remove(path)
        except OSError:
            pass

    def recompress(self, item_id, store_id, job):
        """Compresse une copie déjà archivée : relue depuis le stockage, réencodée, renvoyée, l'ancienne effacée."""
        db = self.ctx.db
        c = db.one("SELECT * FROM cold_copies WHERE item_id=? AND store_id=?", (item_id, store_id))
        row = db.one("SELECT id, key, library_id, file_size, probe FROM items WHERE id=?", (item_id,))
        if not c or not row or c["compressed"]:
            return None
        b = self.backend(self.store(store_id))
        lp = b.local_path(c["name"])
        src = lp if lp else "cold:" + self.stream_url(store_id, c["name"], c["size"])
        res = self.compress(src, {**dict(row), "file_size": c["size"]}, job)
        if not res:
            return None
        tmp, probe = res
        try:
            name = os.path.splitext(c["name"])[0] + ".mkv"
            if name == c["name"]:
                name = os.path.splitext(c["name"])[0] + ".hevc.mkv"
            size, sha = self._put_verified(b, name, tmp, job)
            now = int(time.time())
            db.run("UPDATE cold_copies SET name=?, size=?, sha256=?, compressed=1, orig_size=?, probe=?, verified_at=? WHERE id=?",
                   (name, size, sha, c["orig_size"] or c["size"], json.dumps(probe), now, c["id"]))
            try:
                b.delete(c["name"])
            except ColdError as e:
                log.info("Ancienne copie %s non effacée : %s", c["name"], e)
            self.uncache(item_id)
            return name
        finally:
            self._rm(tmp)

    def _put_verified(self, b, name, path, job):
        size = os.path.getsize(path)
        job["total"], job["done"] = size, 0
        reader = _Reader(_file_blocks(path), job, self)
        b.put(name, reader, size)
        if reader.done != size or b.size(name) != size:
            raise ColdError("Copie incomplète sur le stockage")
        for off in sorted({0, max(0, size - SAMPLE)} | {random.randrange(0, max(1, size - SAMPLE)) for _ in range(SAMPLES)}):
            end = min(size - 1, off + SAMPLE - 1)
            if b"".join(b.open(name, off, end)) != b"".join(_file_blocks(path, off, end)):
                raise ColdError("La copie relue ne correspond pas au fichier")
        return size, reader.sha.hexdigest()

    def queue_compress(self, store_id=None):
        """Compresser les archives déjà faites (celles qui en valent la peine)."""
        db = self.ctx.db
        now = int(time.time())
        n = 0
        for c in db.all("SELECT c.item_id, c.store_id, i.probe FROM cold_copies c JOIN items i ON i.id=c.item_id "
                        "WHERE c.compressed=0 AND c.verified_at IS NOT NULL" + (" AND c.store_id=?" if store_id else ""),
                        (store_id,) if store_id else ()):
            if not self.compressible(json.loads(c["probe"] or "{}"))[0]:
                continue
            db.run("INSERT INTO cold_jobs(item_id, store_id, state, kind, created_at, updated_at) VALUES(?,?,'queued','compress',?,?) "
                   "ON CONFLICT(item_id, store_id) DO UPDATE SET state='queued', kind='compress', error=NULL, updated_at=excluded.updated_at",
                   (c["item_id"], c["store_id"], now, now))
            n += 1
        if n:
            self.start()
        return n

    # ---------------------------------------------------------------- débit
    def rate_limit(self):
        """Octets par seconde permis pour une copie (0 : sans limite). Plus bas pendant une lecture."""
        db = self.ctx.db
        try:
            playing = db.scalar("SELECT COUNT(*) FROM streams WHERE last_seen>?", (int(time.time()) - 90,))
        except Exception:
            playing = 0
        mb = float(db.setting("cold_rate_play" if playing else "cold_rate", "10" if playing else "0") or 0)
        return int(mb * 1024 * 1024)

    # ---------------------------------------------------------------- noms sur le stockage
    def name_for(self, row, store_id):
        db = self.ctx.db
        lib = db.one("SELECT name, paths FROM libraries WHERE id=?", (row["library_id"],)) or {"name": "Nexis", "paths": "[]"}
        key = row["key"]
        rel = os.path.basename(key)
        for root in json.loads(lib["paths"] or "[]"):
            if root and key.startswith(os.path.join(root, "")):
                rel = os.path.relpath(key, root)
                break
        name = safe_name(f"{lib['name']}/{rel}")
        taken = db.scalar("SELECT item_id FROM cold_copies WHERE store_id=? AND name=?", (store_id, name))
        if taken and taken != row["id"]:
            stem, ext = os.path.splitext(name)
            name = f"{stem} ({row['id']}){ext}"
        return name

    @staticmethod
    def sidecars(key):
        """Sous-titres posés à côté du fichier (même nom) : copiés avec lui."""
        d, base = os.path.split(key)
        stem = os.path.splitext(base)[0]
        try:
            names = os.listdir(d)
        except OSError:
            return []
        return sorted(os.path.join(d, n) for n in names
                      if n != base and n.startswith(stem) and n.lower().endswith(SIDECAR_EXT) and os.path.isfile(os.path.join(d, n)))

    # ---------------------------------------------------------------- file d'archivage
    def queue(self, item_ids, store_id):
        db = self.ctx.db
        if not self.store(store_id):
            raise ColdError("Stockage inconnu")
        ids = set()
        for i in item_ids:
            r = db.one("SELECT id, type FROM items WHERE id=? AND peer_id IS NULL", (int(i),))
            if not r:
                continue
            if r["type"] in ("movie", "episode", "extra"):
                ids.add(r["id"])
            elif r["type"] in ("show", "season"):
                col = "show_id" if r["type"] == "show" else "parent_id"
                ids.update(x["id"] for x in db.all(f"SELECT id FROM items WHERE {col}=? AND type='episode' AND peer_id IS NULL",
                                                   (r["id"],)))
        now = int(time.time())
        added = 0
        for i in sorted(ids):
            if db.scalar("SELECT 1 FROM cold_copies WHERE item_id=? AND store_id=?", (i, store_id)):
                continue
            db.run("INSERT INTO cold_jobs(item_id, store_id, state, created_at, updated_at) VALUES(?,?,'queued',?,?) "
                   "ON CONFLICT(item_id, store_id) DO UPDATE SET state='queued', error=NULL, updated_at=excluded.updated_at",
                   (i, store_id, now, now))
            added += 1
        if added:
            self.start()
        return added

    def start(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                self.wake.set()
                return
            self.worker = threading.Thread(target=self._loop, daemon=True, name="cold")
            self.worker.start()

    def cancel(self, job_id):
        db = self.ctx.db
        if self.running and self.running.get("id") == job_id:
            self.running["cancel"] = True
        db.run("DELETE FROM cold_jobs WHERE id=? AND state!='running'", (job_id,))

    def _loop(self):
        db = self.ctx.db
        try:
            db.run("UPDATE cold_jobs SET state='queued' WHERE state='running'")  # arrêt de Nexis pendant une copie
            while True:
                job = db.one("SELECT * FROM cold_jobs WHERE state='queued' ORDER BY created_at, id LIMIT 1")
                if not job:
                    break
                db.run("UPDATE cold_jobs SET state='running', updated_at=? WHERE id=?", (int(time.time()), job["id"]))
                self.running = {"id": job["id"], "item_id": job["item_id"], "store_id": job["store_id"], "done": 0,
                                "total": 0, "cancel": False}
                try:
                    if job["kind"] == "compress":
                        self.recompress(job["item_id"], job["store_id"], self.running)
                    else:
                        self.archive(job["item_id"], job["store_id"], self.running)
                    if self.running.get("note"):
                        log.info("Archivage de l'élément %s : %s", job["item_id"], self.running["note"])
                    db.run("DELETE FROM cold_jobs WHERE id=?", (job["id"],))
                    free = getattr(self.ctx, "coldfree", None)
                    if free:
                        free.on_archived(job["item_id"])
                except Exception as e:
                    msg = str(e) if isinstance(e, (ColdError, OSError)) else i18n.tr("Erreur inattendue : {e}").format(e=e)
                    if not isinstance(e, (ColdError, OSError)):
                        log.exception("Archivage de l'élément %s", job["item_id"])
                    if self.running.get("cancel"):
                        db.run("DELETE FROM cold_jobs WHERE id=?", (job["id"],))
                    else:
                        log.warning("Archivage de l'élément %s : %s", job["item_id"], msg)
                        db.run("UPDATE cold_jobs SET state='error', error=?, updated_at=? WHERE id=?",
                               (msg[:500], int(time.time()), job["id"]))
                finally:
                    self.running = None
        finally:
            db.release()

    # ---------------------------------------------------------------- une copie
    def archive(self, item_id, store_id, job=None):
        """Copie le fichier de l'élément (et ses sous-titres voisins) sur le stockage, puis la vérifie."""
        job = job if job is not None else {"done": 0, "total": 0, "cancel": False}
        db = self.ctx.db
        row = db.one("SELECT id, key, library_id, file_size, probe FROM items WHERE id=?", (item_id,))
        store = self.store(store_id)
        if not row or not store:
            raise ColdError("Élément ou stockage introuvable")
        if media.is_strm(row["key"] or ""):
            raise ColdError("Un fichier .strm n'a rien à archiver")
        b = self.backend(store)
        name = self.name_for(row, store_id)
        meta = {"compressed": 0, "orig_size": None, "probe": None}
        # source : le fichier de la bibliothèque, sinon une copie déjà faite sur un autre stockage
        if row["key"] and os.path.isfile(row["key"]):
            src = row["key"]
            size = os.path.getsize(row["key"])
            blocks = _file_blocks(row["key"])
            reread = lambda s, e: b"".join(_file_blocks(row["key"], s, e))
            sidecars = self.sidecars(row["key"])
        else:
            other = next((c for c in self.copies(item_id) if c["store_id"] != store_id and c["verified_at"]), None)
            if not other:
                raise ColdError("Fichier introuvable (partage non monté ?) et aucune autre copie")
            ob = self.backend(self.store(other["store_id"]))
            size = other["size"]
            olp = ob.local_path(other["name"])
            src = olp if olp else "cold:" + self.stream_url(other["store_id"], other["name"], size)
            blocks = ob.open(other["name"], 0, size - 1)
            reread = lambda s, e: b"".join(ob.open(other["name"], s, e))
            sidecars = []
            if other["compressed"]:
                meta = {"compressed": 1, "orig_size": other["orig_size"], "probe": other["probe"]}
                name = os.path.splitext(name)[0] + ".mkv"
        tmp = None
        if self.compress_conf()["enabled"] and not meta["compressed"]:
            res = self.compress(src, row, job)
            if res:
                tmp, probe = res
                orig = size
                size = os.path.getsize(tmp)
                blocks = _file_blocks(tmp)
                reread = lambda s, e: b"".join(_file_blocks(tmp, s, e))
                name = os.path.splitext(name)[0] + ".mkv"
                meta = {"compressed": 1, "orig_size": orig, "probe": json.dumps(probe)}
        try:
            return self._archive_put(b, name, size, blocks, reread, sidecars, job, item_id, store_id, meta)
        finally:
            if tmp:
                self._rm(tmp)

    def _archive_put(self, b, name, size, blocks, reread, sidecars, job, item_id, store_id, meta):
        db = self.ctx.db
        job["total"] = size
        reader = _Reader(blocks, job, self)
        b.put(name, reader, size)
        if reader.done != size:
            raise ColdError("Lecture du fichier interrompue")
        # vérification : taille sur le stockage, et morceaux relus (début, fin, quelques-uns au hasard)
        if b.size(name) != size:
            raise ColdError("Copie incomplète sur le stockage")
        offsets = {0, max(0, size - SAMPLE)} | {random.randrange(0, max(1, size - SAMPLE)) for _ in range(SAMPLES)}
        for off in sorted(offsets):
            end = min(size - 1, off + SAMPLE - 1)
            if b"".join(b.open(name, off, end)) != reread(off, end):
                raise ColdError("La copie relue ne correspond pas au fichier")
        extras = []
        for sc in sidecars:
            try:
                with open(sc, "rb") as f:
                    data = f.read()
                sname = safe_name(os.path.dirname(name) + "/" + os.path.basename(sc)) if "/" in name else safe_name(os.path.basename(sc))
                b.put(sname, _BytesReader(data), len(data))
                extras.append({"name": sname, "file": os.path.basename(sc), "size": len(data)})
            except (OSError, ColdError) as e:
                log.info("Sous-titres %s non archivés : %s", sc, e)
        now = int(time.time())
        db.run("INSERT INTO cold_copies(item_id, store_id, name, size, sha256, extras, archived_at, verified_at, compressed, orig_size, probe) "
               "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(item_id, store_id) DO UPDATE SET name=excluded.name, size=excluded.size, "
               "sha256=excluded.sha256, extras=excluded.extras, archived_at=excluded.archived_at, verified_at=excluded.verified_at, "
               "compressed=excluded.compressed, orig_size=excluded.orig_size, probe=excluded.probe",
               (item_id, store_id, name, size, reader.sha.hexdigest(), json.dumps(extras), now, now,
                meta["compressed"], meta["orig_size"], meta["probe"]))
        return name

    def forget(self, item_id, store_id, delete_file=True):
        """Retire une copie froide (et son fichier sur le stockage)."""
        c = self.ctx.db.one("SELECT * FROM cold_copies WHERE item_id=? AND store_id=?", (item_id, store_id))
        if not c:
            return False
        row = self.ctx.db.one("SELECT key FROM items WHERE id=?", (item_id,))
        others = self.ctx.db.scalar("SELECT COUNT(*) FROM cold_copies WHERE item_id=? AND store_id!=?", (item_id, store_id))
        if row and self.local_missing(row) and not others:
            raise ColdError("C'est la seule copie de ce titre : le fichier n'est plus dans la bibliothèque")
        if delete_file:
            b = self.backend(self.store(store_id))
            b.delete(c["name"])
            for x in json.loads(c["extras"] or "[]"):
                try:
                    b.delete(x["name"])
                except ColdError:
                    pass
        self.ctx.db.run("DELETE FROM cold_copies WHERE id=?", (c["id"],))
        return True

    # ---------------------------------------------------------------- état pour l'administration
    def status(self):
        db = self.ctx.db
        jobs = db.all("SELECT j.*, i.title, i.type, s.title AS show_title, i.idx, p.idx AS season FROM cold_jobs j "
                      "JOIN items i ON i.id=j.item_id LEFT JOIN items s ON s.id=i.show_id LEFT JOIN items p ON p.id=i.parent_id "
                      "ORDER BY j.state='running' DESC, j.state='error', j.created_at, j.id LIMIT 200")
        run = self.running
        out = []
        for j in jobs:
            label = (f"{j['show_title']} · S{j['season'] or 0}E{j['idx'] or 0} · {j['title']}" if j["type"] == "episode"
                     else j["title"])
            d = {"id": j["id"], "item_id": j["item_id"], "store_id": j["store_id"], "state": j["state"], "title": label,
                 "error": j["error"]}
            d["kind"] = j["kind"]
            if run and run.get("id") == j["id"]:
                d.update(done=run.get("done", 0), total=run.get("total", 0), phase=run.get("phase") or "copy",
                         pct=run.get("pct", 0), paused=bool(run.get("paused")), hw=run.get("hw"))
            out.append(d)
        counts = {r["store_id"]: r for r in db.all("SELECT store_id, COUNT(*) AS n, SUM(size) AS bytes FROM cold_copies GROUP BY store_id")}
        return {"jobs": out, "queued": db.scalar("SELECT COUNT(*) FROM cold_jobs WHERE state='queued'"), "counts": counts}


# ====================================================================== routes
def register(app, ctx, x):
    from .web import HTTPError, Response
    db = ctx.db
    cold = ctx.cold

    def err(e):
        return HTTPError(400, str(e))

    def store_out(s, counts):
        c = counts.get(s["id"]) or {}
        return {"id": s["id"], "name": s["name"], "kind": s["kind"], "enabled": bool(s["enabled"]), "position": s["position"],
                "conf": public_conf(s["conf"]), "copies": c.get("n") or 0, "bytes": c.get("bytes") or 0}

    @app.route("GET", "/api/admin/cold", auth="admin")
    def cold_status(req):
        st = cold.status()
        return {"stores": [store_out(s, st["counts"]) for s in cold.stores()], "available": cold.available(),
                "jobs": st["jobs"], "queued": st["queued"],
                "settings": {"cold_rate": float(db.setting("cold_rate", "0") or 0),
                             "cold_rate_play": float(db.setting("cold_rate_play", "10") or 10)}}

    @app.route("GET", r"/api/admin/cold/stores/(?P<sid>\d+)/free", auth="admin")
    def cold_free(req):
        s = cold.store(int(req.params["sid"]))
        if not s:
            raise HTTPError(404, "Stockage inconnu")
        db.release()
        try:
            return {"free": cold.backend(s).free()}
        except ColdError:
            return {"free": None}

    def prepare(kind, b, old=None):
        if kind not in KINDS:
            raise HTTPError(400, "Type de stockage inconnu")
        why = cold.available().get(kind)
        if why:
            raise HTTPError(400, why)
        try:
            return valid_conf(kind, b.get("conf") or {}, old)
        except ColdError as e:
            raise err(e)

    def test_conf(kind, conf):
        """Essai réel (écrire, relire, effacer un petit fichier). SFTP : l'empreinte du serveur d'abord."""
        b = BACKENDS[kind](conf, cold)
        if kind == "sftp" and not conf.get("hostkey"):
            return {"ok": False, "fingerprint": b.fingerprint()}
        b.check()
        return {"ok": True}

    def mount_smb(sid, conf):
        args = {"name": f"cold-{sid}", "host": conf["host"], "share": conf["share"], "subdir": conf["subdir"],
                "username": conf["username"], "password": conf.get("password", ""), "domain": conf.get("domain", ""),
                "version": conf.get("version", "auto"), "writable": True}
        try:
            r = mountd.call("add", args, sock_path=os.environ.get("SEANCE_MOUNTD_SOCK"), timeout=90)
        except mountd.MountError as e:
            raise HTTPError(400, str(e))
        conf = {k: v for k, v in conf.items() if k != "password"}  # le mot de passe reste chez seance-mountd
        conf["mount"] = r.get("path") or os.path.join(mountd.MOUNT_ROOT, f"cold-{sid}")
        return conf

    @app.route("POST", "/api/admin/cold/test", auth="admin")
    def cold_test(req):
        b = req.json()
        kind = str(b.get("kind") or "")
        old = None
        if b.get("id"):
            s = cold.store(int(b["id"]))
            old = s["conf"] if s and s["kind"] == kind else None
        conf = prepare(kind, b, old)
        if kind == "smb":
            return {"ok": True, "note": i18n.tr("Le partage sera monté et essayé à l'enregistrement.")}
        db.release()
        try:
            return test_conf(kind, conf)
        except ColdError as e:
            raise err(e)

    @app.route("POST", "/api/admin/cold/stores", auth="admin")
    def cold_add(req):
        b = req.json()
        kind = str(b.get("kind") or "")
        name = str(b.get("name") or "").strip()[:80] or kind
        conf = prepare(kind, b)
        now = int(time.time())
        pos = (db.scalar("SELECT MAX(position) FROM cold_stores") or 0) + 1
        sid = db.run("INSERT INTO cold_stores(name, kind, conf, enabled, position, created_at) VALUES(?,?,?,1,?,?)",
                     (name, kind, json.dumps({}), pos, now)).lastrowid
        try:
            if kind == "smb":
                db.release()
                conf = mount_smb(sid, conf)
            if kind != "smb":
                db.release()
            r = test_conf(kind, conf) if kind != "smb" else (SMB(conf, cold).check() or {"ok": True})
            if not r.get("ok"):
                db.run("DELETE FROM cold_stores WHERE id=?", (sid,))
                return {"ok": False, "fingerprint": r.get("fingerprint")}
        except (ColdError, HTTPError) as e:
            db.run("DELETE FROM cold_stores WHERE id=?", (sid,))
            if kind == "smb":
                try:
                    mountd.call("remove", {"name": f"cold-{sid}"}, sock_path=os.environ.get("SEANCE_MOUNTD_SOCK"))
                except mountd.MountError:
                    pass
            raise e if isinstance(e, HTTPError) else err(e)
        db.run("UPDATE cold_stores SET conf=? WHERE id=?", (json.dumps(conf), sid))
        log.warning("Stockage froid « %s » (%s) ajouté par %s", name, kind, req.user["username"])
        return {"ok": True, "id": sid}

    @app.route("PUT", r"/api/admin/cold/stores/(?P<sid>\d+)", auth="admin")
    def cold_edit(req):
        s = cold.store(int(req.params["sid"]))
        if not s:
            raise HTTPError(404, "Stockage inconnu")
        b = req.json()
        if "name" in b:
            db.run("UPDATE cold_stores SET name=? WHERE id=?", (str(b["name"]).strip()[:80] or s["kind"], s["id"]))
        if "enabled" in b:
            db.run("UPDATE cold_stores SET enabled=? WHERE id=?", (1 if b["enabled"] else 0, s["id"]))
        if "position" in b:
            db.run("UPDATE cold_stores SET position=? WHERE id=?", (int(b["position"]), s["id"]))
        if b.get("conf") and s["kind"] != "smb":
            conf = prepare(s["kind"], b, s["conf"])
            db.release()
            try:
                r = test_conf(s["kind"], conf)
            except ColdError as e:
                raise err(e)
            if not r.get("ok"):
                return r
            db.run("UPDATE cold_stores SET conf=? WHERE id=?", (json.dumps(conf), s["id"]))
        return {"ok": True}

    @app.route("DELETE", r"/api/admin/cold/stores/(?P<sid>\d+)", auth="admin")
    def cold_remove(req):
        s = cold.store(int(req.params["sid"]))
        if not s:
            raise HTTPError(404, "Stockage inconnu")
        # un titre dont c'est la seule copie (fichier absent de la bibliothèque) ne doit pas devenir illisible
        only = [r for r in db.all("SELECT c.item_id, i.key FROM cold_copies c JOIN items i ON i.id=c.item_id WHERE c.store_id=? "
                                  "AND NOT EXISTS (SELECT 1 FROM cold_copies o WHERE o.item_id=c.item_id AND o.store_id!=c.store_id)",
                                  (s["id"],)) if cold.local_missing(r)]
        if only:
            raise HTTPError(409, i18n.tr("{n} titre(s) n'existent plus que sur ce stockage : impossible de le retirer").format(n=len(only)))
        db.run("DELETE FROM cold_jobs WHERE store_id=?", (s["id"],))
        db.run("DELETE FROM cold_copies WHERE store_id=?", (s["id"],))  # les fichiers restent sur le stockage
        db.run("DELETE FROM cold_stores WHERE id=?", (s["id"],))
        if s["kind"] == "smb":
            db.release()
            try:
                mountd.call("remove", {"name": f"cold-{s['id']}"}, sock_path=os.environ.get("SEANCE_MOUNTD_SOCK"))
            except mountd.MountError:
                pass
        return {"ok": True}

    @app.route("PUT", "/api/admin/cold/settings", auth="admin")
    def cold_settings(req):
        b = req.json()
        for k in ("cold_rate", "cold_rate_play"):
            if k in b:
                v = float(b[k] or 0)
                if not 0 <= v <= 10000:
                    raise HTTPError(400, "Débit invalide")
                db.set_setting(k, str(v))
        if "compress" in b:
            db.set_setting("cold_compress", "1" if b["compress"] else "0")
        if "compress_quality" in b:
            if b["compress_quality"] not in QUALITY:
                raise HTTPError(400, "Réglage invalide")
            db.set_setting("cold_compress_quality", b["compress_quality"])
        if "compress_height" in b:
            if int(b["compress_height"] or 0) not in (0, 2160, 1080, 720):
                raise HTTPError(400, "Réglage invalide")
            db.set_setting("cold_compress_height", str(int(b["compress_height"] or 0)))
        return {"ok": True}

    @app.route("GET", "/api/admin/cold/compress", auth="admin")
    def cold_compress_info(req):
        """Compression des archives : réglages, carte graphique utilisée, archives qui en profiteraient."""
        hw = ctx.hw()
        todo = [c for c in db.all("SELECT c.size, i.probe FROM cold_copies c JOIN items i ON i.id=c.item_id "
                                  "WHERE c.compressed=0 AND c.verified_at IS NOT NULL") if cold.compressible(json.loads(c["probe"] or "{}"))[0]]
        done = db.one("SELECT COUNT(*) AS n, COALESCE(SUM(orig_size), 0) AS before, COALESCE(SUM(size), 0) AS after "
                      "FROM cold_copies WHERE compressed=1")
        return {**cold.compress_conf(), "hw": hw["engine"] if hw else None,
                "todo": {"count": len(todo), "bytes": sum(c["size"] for c in todo)}, "done": done}

    @app.route("POST", "/api/admin/cold/compress", auth="admin")
    def cold_compress_existing(req):
        return {"queued": cold.queue_compress(int(req.json().get("store") or 0) or None)}

    @app.route("POST", "/api/admin/cold/archive", auth="admin")
    def cold_archive(req):
        b = req.json()
        ids = [i for i in (b.get("ids") or []) if str(i).isdigit()][:5000]
        try:
            n = cold.queue(ids, int(b.get("store") or 0))
        except (ColdError, ValueError) as e:
            raise err(e)
        return {"queued": n}

    @app.route("POST", r"/api/admin/cold/jobs/(?P<jid>\d+)/retry", auth="admin")
    def cold_retry(req):
        db.run("UPDATE cold_jobs SET state='queued', error=NULL WHERE id=? AND state='error'", (int(req.params["jid"]),))
        cold.start()
        return {"ok": True}

    @app.route("DELETE", r"/api/admin/cold/jobs/(?P<jid>\d+)", auth="admin")
    def cold_cancel(req):
        cold.cancel(int(req.params["jid"]))
        return {"ok": True}

    @app.route("GET", r"/api/admin/cold/items/(?P<rk>\d+)", auth="admin")
    def cold_item(req):
        rid = int(req.params["rk"])
        return {"copies": [{"store_id": c["store_id"], "store": c["store_name"], "kind": c["kind"], "size": c["size"],
                            "archived_at": c["archived_at"], "verified_at": c["verified_at"], "name": c["name"]}
                           for c in cold.copies(rid)],
                "jobs": [j for j in cold.status()["jobs"] if j["item_id"] == rid]}

    @app.route("DELETE", r"/api/admin/cold/items/(?P<rk>\d+)/(?P<sid>\d+)", auth="admin")
    def cold_item_forget(req):
        db.release()
        try:
            ok = cold.forget(int(req.params["rk"]), int(req.params["sid"]))
        except ColdError as e:
            raise HTTPError(409, str(e))
        return {"ok": ok}

    # ---------------------------------------------------- cache local et accueil des archives des serveurs liés
    @app.route("GET", "/api/admin/cold/extra", auth="admin")
    def cold_extra(req):
        peers = {p["id"]: p["name"] for p in db.all("SELECT id, name FROM peers")}
        hosted = []
        root = os.path.join(ctx.data_dir, "cold-hosted")
        for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            if d.isdigit():
                size = sum(os.path.getsize(os.path.join(a, f)) for a, _, fs in os.walk(os.path.join(root, d)) for f in fs)
                hosted.append({"peer": peers.get(int(d), f"#{d}"), "bytes": size})
        return {"cache": cold.cache_status(), "host_gb": float(db.setting("cold_host_gb", "0") or 0), "hosted": hosted,
                "peers": [{"id": k, "name": v} for k, v in peers.items()]}

    @app.route("PUT", "/api/admin/cold/extra", auth="admin")
    def cold_extra_save(req):
        b = req.json()
        for k, setting, hi in (("cache_gb", "cold_cache_gb", 100000), ("keep_free_gb", "cold_cache_free_gb", 100000),
                               ("next", "cold_prefetch", 20), ("host_gb", "cold_host_gb", 1000000)):
            if k in b:
                try:
                    v = float(b[k] or 0)
                except (TypeError, ValueError):
                    raise HTTPError(400, "Réglage invalide")
                if not 0 <= v <= hi:
                    raise HTTPError(400, "Réglage invalide")
                db.set_setting(setting, str(int(v) if k == "next" else v))
        if b.get("clear_cache"):
            for r in db.all("SELECT item_id FROM cold_cache"):
                cold.uncache(r["item_id"])
        return {"ok": True}

    def hosted_path(req, name):
        if not req.fed_peer:
            raise HTTPError(403, "Réservé aux serveurs liés")
        if cold.hosted_quota() <= 0:
            raise HTTPError(403, i18n.tr("Ce serveur n'accueille pas d'archives (réglage de son administrateur)"))
        try:
            rel = safe_name(name or "")
        except ColdError:
            raise HTTPError(400, "Nom invalide")
        root = os.path.realpath(cold.hosted_dir(req.fed_peer["id"]))
        p = os.path.realpath(os.path.join(root, rel))
        if not p.startswith(root + os.sep):
            raise HTTPError(400, "Nom invalide")
        return p

    @app.route("GET", "/fed/cold/info", auth="peer")
    def fed_cold_info(req):
        quota = cold.hosted_quota()
        used = cold.hosted_used() if quota else 0
        return {"enabled": quota > 0, "quota": quota, "used": used, "free": max(0, quota - used)}

    @app.route("PUT", "/fed/cold/blob", auth="peer")
    def fed_cold_put(req):
        """Un morceau (16 Mo au plus) d'une archive envoyée par un serveur lié, écrit à la suite des précédents."""
        p = hosted_path(req, req.arg("name"))
        part = p + ".part"
        offset = int(req.arg("offset") or 0)
        length = int(req.headers.get("Content-Length") or 0)
        if length > 2 * PEER_CHUNK or length < 0:
            raise HTTPError(413, "Morceau trop gros")
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if offset == 0:
            have = 0
        elif offset != have:
            raise HTTPError(409, i18n.tr("Reprise attendue à {n}").format(n=have))
        if cold.hosted_used() - (os.path.getsize(part) if offset == 0 and os.path.exists(part) else 0) + length > cold.hosted_quota():
            raise HTTPError(507, i18n.tr("Plus de place réservée aux archives sur ce serveur"))
        db.release()
        os.makedirs(os.path.dirname(part), exist_ok=True)
        with open(part, "r+b" if offset else "wb") as f:
            f.seek(offset)
            left = length
            while left > 0:
                b = req.handler.rfile.read(min(BLOCK, left))
                if not b:
                    req._body = b""
                    raise HTTPError(400, "Morceau incomplet")
                f.write(b)
                left -= len(b)
            f.truncate()
        req._body = b""  # corps déjà lu ici : le serveur web ne doit pas le relire
        return {"ok": True, "size": offset + length}

    @app.route("POST", "/fed/cold/commit", auth="peer")
    def fed_cold_commit(req):
        p = hosted_path(req, req.arg("name"))
        part = p + ".part"
        size = int(req.arg("size") or -1)
        if not os.path.exists(part) or os.path.getsize(part) != size:
            raise HTTPError(409, "Archive incomplète")
        os.replace(part, p)
        return {"ok": True}

    @app.route("GET", "/fed/cold/stat", auth="peer")
    def fed_cold_stat(req):
        p = hosted_path(req, req.arg("name"))
        if not os.path.isfile(p):
            raise HTTPError(404, "Introuvable")
        return {"size": os.path.getsize(p)}

    def byte_range(req, size):
        """En-tête Range -> (début, fin, statut) ; None si la plage est hors du fichier."""
        start, end, status = 0, size - 1, 200
        rng = (req.headers.get("Range") or "").strip()
        m = re.match(r"^bytes=(\d*)-(\d*)$", rng) if rng else None
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end or start >= size:
                return None
            status = 206
        return start, end, status

    def ranged(start, end, status, size, gen):
        headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return Response(stream=gen, status=status, content_type="application/octet-stream", length=end - start + 1,
                        headers=headers)

    @app.route("GET", "/fed/cold/blob", auth="peer")
    def fed_cold_get(req):
        p = hosted_path(req, req.arg("name"))
        if not os.path.isfile(p):
            raise HTTPError(404, "Introuvable")
        size = os.path.getsize(p)
        r = byte_range(req, size)
        if r is None:
            return Response(b"", 416, "text/plain", {"Content-Range": f"bytes */{size}"})
        db.release()
        return ranged(*r, size, _file_blocks(p, r[0], r[1]))

    @app.route("DELETE", "/fed/cold/blob", auth="peer")
    def fed_cold_delete(req):
        p = hosted_path(req, req.arg("name"))
        for f in (p, p + ".part"):
            try:
                os.remove(f)
            except FileNotFoundError:
                pass
        return {"ok": True}

    # lecture d'une copie froide par ffmpeg (adresse locale, jeton secret, jamais derrière un proxy)
    @app.route("GET", r"/cold/(?P<tok>[A-Za-z0-9_-]{20,64})", auth=None)
    def cold_stream(req):
        t = cold.token(req.params["tok"])
        if not t or req.client_ip not in ("127.0.0.1", "::1") or req.headers.get("X-Forwarded-For"):
            raise HTTPError(404, "Introuvable")
        store = cold.store(t["store"])
        if not store:
            raise HTTPError(404, "Introuvable")
        size = t["size"]
        r = byte_range(req, size)
        if r is None:
            return Response(b"", 416, "text/plain", {"Content-Range": f"bytes */{size}"})
        start, end, status = r
        db.release()
        b = cold.backend(store)

        def gen():
            try:
                yield from b.open(t["name"], start, end)
            except ColdError as e:
                log.warning("Lecture froide de %s : %s", t["name"], e)
        return ranged(start, end, status, size, gen())
