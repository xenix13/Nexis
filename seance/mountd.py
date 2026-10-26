"""seance-mountd : petit service root qui monte les partages SMB demandés par Nexis.

Nexis tourne sans privilèges ; ce service ne sait faire que deux choses : monter des partages
SMB/CIFS en lecture seule sous /mnt/seance/<nom>, et lancer la mise à jour de Nexis (depuis la source
notée à l'installation, sans paramètre venant de Nexis). Il écoute sur un socket Unix accessible
uniquement à l'utilisateur « seance » (vérifié par SO_PEERCRED) et revalide chaque demande.

Lancement : python3 -m seance.mountd   (en root, via seance-mountd.service)
"""
import grp
import json
import logging
import os
import pwd
import re
import shutil
import socket
import socketserver
import struct
import subprocess
import tempfile
import threading
import time

from . import update

log = logging.getLogger("seance.mountd")

SOCK = os.environ.get("SEANCE_MOUNTD_SOCK", "/run/seance-mountd/mountd.sock")
FSTAB = os.environ.get("SEANCE_FSTAB", "/etc/fstab")
CRED_DIR = os.environ.get("SEANCE_SMB_CRED_DIR", "/etc/seance/smb")
MOUNT_ROOT = os.environ.get("SEANCE_MOUNT_ROOT", "/mnt/seance")
APP_USER = os.environ.get("SEANCE_USER", "seance")
MOUNTINFO = os.environ.get("SEANCE_MOUNTINFO", "/proc/self/mountinfo")
MARK = "# seance-smb:"
RETRY_EVERY = 300

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
HOST = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})(?:\.[A-Za-z0-9-]{1,63})*|\d{1,3}(?:\.\d{1,3}){3}|\[[0-9A-Fa-f:]{2,45}\])$")
SHARE = re.compile(r"^[A-Za-z0-9 ._$'()&+@!#-]{1,80}$")
SUBDIR = re.compile(r"^[A-Za-z0-9 ._'()&+@!#/-]{0,200}$")
CRED_VALUE = re.compile(r"^[^\n\r\0]{0,256}$")
VERSIONS = {"auto": None, "3.1.1": "3.1.1", "3.0": "3.0", "2.1": "2.1", "2.0": "2.0", "1.0": "1.0"}


class MountError(Exception):
    pass


# ------------------------------------------------------------------ helpers
def run(cmd, timeout=40, env=None):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise MountError(f"Commande introuvable : {cmd[0]} (relancer l'installation de Nexis)")
    except subprocess.TimeoutExpired:
        raise MountError("Le NAS ne répond pas (délai dépassé)")
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def environment():
    container = ""
    try:
        code, out, _ = run(["systemd-detect-virt", "-c"], timeout=5)
        container = out.strip() if code == 0 else ""
    except MountError:
        pass
    unprivileged = False
    try:
        with open("/proc/self/uid_map") as f:
            first = f.readline().split()
        unprivileged = len(first) == 3 and first[1] != "0"
    except OSError:
        pass
    apparmor = ""
    for p in ("/proc/self/attr/apparmor/current", "/proc/self/attr/current"):
        try:
            with open(p) as f:
                apparmor = f.read().strip().strip("\x00")
            if apparmor:
                break
        except OSError:
            continue
    return {"container": container if container != "none" else "", "unprivileged": unprivileged,
            "apparmor": apparmor,
            "can_mount": not unprivileged,
            "tools": {"mount.cifs": bool(shutil.which("mount.cifs")), "smbclient": bool(shutil.which("smbclient"))}}


def fstab_escape(s):
    return s.replace("\\", "\\134").replace(" ", "\\040").replace("\t", "\\011")


def fstab_unescape(s):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def app_ids():
    try:
        u = pwd.getpwnam(APP_USER)
        return u.pw_uid, u.pw_gid
    except KeyError:
        return 0, 0


def read_fstab():
    try:
        with open(FSTAB, encoding="utf-8") as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []


def write_fstab(lines):
    d = os.path.dirname(FSTAB) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".fstab.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    try:
        st = os.stat(FSTAB)
        os.chmod(tmp, st.st_mode & 0o777)
    except OSError:
        os.chmod(tmp, 0o644)
    os.replace(tmp, FSTAB)
    if shutil.which("systemctl") and os.path.isdir("/run/systemd/system"):
        run(["systemctl", "daemon-reload"], timeout=30)


def entries():
    """Partages gérés par Nexis (paires « # seance-smb:nom » + ligne fstab)."""
    lines = read_fstab()
    out = []
    for i, line in enumerate(lines):
        if line.startswith(MARK) and i + 1 < len(lines):
            name = line[len(MARK):].strip()
            fields = lines[i + 1].split()
            if len(fields) < 4:
                continue
            what = fstab_unescape(fields[0])
            m = re.match(r"^//([^/]+)/([^/]+)(?:/(.*))?$", what)
            opts = dict(o.split("=", 1) if "=" in o else (o, "") for o in fields[3].split(","))
            out.append({"name": name, "host": m.group(1) if m else "", "share": m.group(2) if m else "",
                        "subdir": (m.group(3) or "") if m else "", "path": fstab_unescape(fields[1]),
                        "version": opts.get("vers", "auto"), "credentials": opts.get("credentials", "")})
    return out


HARDENING = ("nosuid", "nodev", "noexec")


def harden_fstab():
    """Ajoute nosuid,nodev,noexec aux partages créés par une version précédente (pris en compte au prochain
    montage). Renvoie le nombre de lignes modifiées."""
    lines = read_fstab()
    changed = 0
    for i, line in enumerate(lines):
        if not (line.startswith(MARK) and i + 1 < len(lines)):
            continue
        fields = lines[i + 1].split()
        if len(fields) < 4 or fields[2] != "cifs":
            continue
        opts = fields[3].split(",")
        missing = [o for o in HARDENING if o not in opts]
        if missing:
            fields[3] = ",".join(opts[:1] + missing + opts[1:]) if opts and opts[0].startswith("credentials=") \
                else ",".join(missing + opts)
            lines[i + 1] = " ".join(fields)
            changed += 1
    if changed:
        write_fstab(lines)
    return changed


def cred_path(name):
    return os.path.join(CRED_DIR, f"{name}.cred")


def read_cred(name):
    out = {}
    try:
        with open(cred_path(name), encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    out[k.strip()] = v
    except OSError:
        pass
    return out


def write_cred(name, username, password, domain):
    os.makedirs(CRED_DIR, mode=0o700, exist_ok=True)
    os.chmod(CRED_DIR, 0o700)
    path = cred_path(name)
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"username={username}\npassword={password}\n")
        if domain:
            f.write(f"domain={domain}\n")
    os.replace(path + ".tmp", path)
    return path


def is_mounted(path):
    try:
        with open(MOUNTINFO, encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) > 4 and fstab_unescape(parts[4]) == path:
                    return True
    except OSError:
        pass
    return False


def smb_auth_file(username, password, domain):
    fd, path = tempfile.mkstemp(prefix="seance-smb-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"username = {username}\npassword = {password}\n")
        if domain:
            f.write(f"domain = {domain}\n")
    os.chmod(path, 0o600)
    return path


def smb_proto_args(version):
    if version == "1.0":
        return ["--option=client min protocol=NT1", "-m", "NT1"]
    if version in ("2.0", "2.1"):
        return ["-m", "SMB2"]
    if version in ("3.0", "3.1.1"):
        return ["-m", "SMB3"]
    return []


def explain_smbclient(err):
    e = err.upper()
    if "NT_STATUS_LOGON_FAILURE" in e or "NT_STATUS_ACCESS_DENIED" in e or "NT_STATUS_WRONG_PASSWORD" in e:
        return "Identifiant ou mot de passe refusé par le NAS"
    if "NT_STATUS_BAD_NETWORK_NAME" in e:
        return "Ce partage n'existe pas sur le NAS"
    if "NT_STATUS_HOST_UNREACHABLE" in e or "NT_STATUS_IO_TIMEOUT" in e or "NT_STATUS_CONNECTION_REFUSED" in e \
            or "NT_STATUS_UNSUCCESSFUL" in e:
        return "NAS injoignable (adresse, pare-feu ou SMB désactivé ?)"
    if "PROTOCOL_NEGOTIATION_FAILED" in e or "NT_STATUS_NOT_SUPPORTED" in e:
        return "Version SMB refusée : essaie une autre version dans les options"
    if "NT_STATUS_ACCOUNT_DISABLED" in e:
        return "Compte désactivé sur le NAS"
    last = [l for l in err.strip().splitlines() if l.strip()]
    return last[-1] if last else "Échec de la connexion SMB"


def explain_mount(err, env):
    e = err.lower()
    if "error(1)" in e or "operation not permitted" in e:
        if env["unprivileged"]:
            return ("Linux interdit le montage SMB dans un conteneur LXC non privilégié. "
                    "Voir « Partages réseau » dans le README pour les solutions.")
        return ("Montage refusé par le système. Dans Proxmox : pct set <ID> -features mount=cifs,nesting=1 "
                "puis redémarrer le conteneur.")
    if "error(13)" in e or "permission denied" in e:
        if env["container"]:
            prof = env.get("apparmor") or "inconnu"
            return ("Le NAS accepte les identifiants mais le noyau refuse le montage. Dans un conteneur LXC, la cause "
                    "est presque toujours AppArmor : vérifier que l'option « mount=cifs » est active sur ce conteneur "
                    "(pct config <ID> | grep features), puis arrêter et démarrer le conteneur depuis Proxmox "
                    f"(un redémarrage interne ne suffit pas toujours). Profil AppArmor actuel : {prof}")
        return "Accès refusé par le NAS (droits du compte sur ce partage, ou chiffrement SMB imposé)"
    if "error(2)" in e or "no such file" in e:
        return "Partage ou sous-dossier introuvable sur le NAS"
    if "error(112)" in e or "host is down" in e or "error(113)" in e or "no route" in e:
        return "NAS injoignable"
    if "error(95)" in e or "operation not supported" in e:
        return "Version SMB non prise en charge : essayer une autre version"
    if "wrong fs type" in e or "unknown filesystem" in e:
        return "Le module CIFS n'est pas disponible (cifs-utils absent, ou noyau sans CIFS)"
    last = [l for l in err.strip().splitlines() if l.strip()]
    return last[-1] if last else "Échec du montage"


# ----------------------------------------------------------------- service
class Mounts:
    def __init__(self):
        self.lock = threading.Lock()
        self.errors = {}
        self.env = environment()

    def validate(self, a, need_password=True):
        name = str(a.get("name") or "")
        host = str(a.get("host") or "").strip()
        share = str(a.get("share") or "").strip().strip("/")
        subdir = str(a.get("subdir") or "").strip().strip("/")
        username = str(a.get("username") or "")
        password = str(a.get("password") or "")
        domain = str(a.get("domain") or "").strip()
        version = str(a.get("version") or "auto")
        if not NAME.match(name):
            raise MountError("Nom local : minuscules, chiffres, - et _ (32 caractères max.)")
        if not HOST.match(host):
            raise MountError("Adresse du NAS invalide (ex. 192.168.1.5 ou nas.local)")
        if not SHARE.match(share):
            raise MountError("Nom de partage invalide")
        if subdir and (not SUBDIR.match(subdir) or ".." in subdir.split("/")):
            raise MountError("Sous-dossier invalide")
        for label, v in (("Identifiant", username), ("Mot de passe", password), ("Domaine", domain)):
            if not CRED_VALUE.match(v):
                raise MountError(f"{label} invalide")
        if domain and not re.match(r"^[A-Za-z0-9._-]{1,64}$", domain):
            raise MountError("Domaine invalide")
        if version not in VERSIONS:
            raise MountError("Version SMB inconnue")
        if need_password and not password:
            raise MountError("Mot de passe requis")
        return dict(name=name, host=host, share=share, subdir=subdir, username=username, password=password,
                    domain=domain, version=version)

    # -- commandes
    def status(self, _a):
        out = []
        for e in entries():
            e.pop("credentials", None)
            e["username"] = read_cred(e["name"]).get("username", "")
            e["mounted"] = is_mounted(e["path"])
            e["error"] = None if e["mounted"] else self.errors.get(e["name"])
            out.append(e)
        return {"env": self.env, "mount_root": MOUNT_ROOT, "shares": out}

    def discover(self, a):
        host = str(a.get("host") or "").strip()
        if not HOST.match(host):
            raise MountError("Adresse du NAS invalide (ex. 192.168.1.5 ou nas.local)")
        username, password, domain = (str(a.get(k) or "") for k in ("username", "password", "domain"))
        version = str(a.get("version") or "auto")
        if not all(CRED_VALUE.match(v) for v in (username, password, domain)):
            raise MountError("Identifiants invalides")
        if not password and a.get("reuse") and NAME.match(str(a.get("reuse"))):
            c = read_cred(str(a["reuse"]))
            username, password, domain = c.get("username", username), c.get("password", ""), c.get("domain", domain)
        auth = smb_auth_file(username, password, domain)
        try:
            code, out, err = run(["smbclient", "-g", "-L", f"//{host}", "-A", auth, *smb_proto_args(version)])
        finally:
            os.remove(auth)
        shares = []
        for line in out.splitlines():
            parts = line.split("|")
            if len(parts) >= 2 and parts[0] == "Disk" and not parts[1].endswith("$"):
                shares.append({"name": parts[1], "comment": parts[2] if len(parts) > 2 else ""})
        if code != 0 and not shares:
            raise MountError(explain_smbclient(err + out))
        return {"shares": sorted(shares, key=lambda s: s["name"].lower())}

    def _check_share(self, v):
        auth = smb_auth_file(v["username"], v["password"], v["domain"])
        try:
            target = f"//{v['host']}/{v['share']}"
            cmd = f'cd "{v["subdir"]}"; ls' if v["subdir"] else "ls"
            code, out, err = run(["smbclient", target, "-A", auth, *smb_proto_args(v["version"]), "-c", cmd])
        finally:
            os.remove(auth)
        if code != 0 or "NT_STATUS" in err.upper() and "NT_STATUS_OK" not in err.upper():
            raise MountError(explain_smbclient(err + out))

    def add(self, a):
        with self.lock:
            existing = {e["name"]: e for e in entries()}
            old = existing.get(str(a.get("name") or ""))
            v = self.validate(a, need_password=old is None)
            if old and not v["password"]:
                c = read_cred(v["name"])
                v["password"] = c.get("password", "")
                v["username"] = v["username"] or c.get("username", "")
                v["domain"] = v["domain"] or c.get("domain", "")
            if shutil.which("smbclient"):
                self._check_share(v)
            path = os.path.join(MOUNT_ROOT, v["name"])
            for e in existing.values():
                if e["name"] != v["name"] and e["path"] == path:
                    raise MountError("Ce nom local est déjà utilisé")
            backup_lines = read_fstab()
            old_cred = None
            try:
                with open(cred_path(v["name"]), encoding="utf-8") as f:
                    old_cred = f.read()
            except OSError:
                pass
            if old and is_mounted(old["path"]):
                run(["umount", "-l", old["path"]])
            cred = write_cred(v["name"], v["username"], v["password"], v["domain"])
            uid, gid = app_ids()
            what = f"//{v['host']}/{v['share']}" + (f"/{v['subdir']}" if v["subdir"] else "")
            base_lines = self._without(backup_lines, v["name"])
            os.makedirs(path, exist_ok=True)
            # En « automatique », on essaie aussi des versions SMB explicites : certains NAS
            # refusent la négociation par défaut du noyau (erreur 13 ou 95 malgré de bons identifiants).
            attempts = [VERSIONS[v["version"]]] if VERSIONS[v["version"]] else [None, "3.0", "2.1", "2.0"]
            failures = []
            for vers in attempts:
                # nosuid/nodev/noexec : rien de ce qui vient du NAS ne peut être exécuté sur le serveur
                opts = [f"credentials={cred}", "ro", "nosuid", "nodev", "noexec", f"uid={uid}", f"gid={gid}",
                        "file_mode=0444", "dir_mode=0555",
                        "iocharset=utf8", "_netdev", "nofail", "x-systemd.mount-timeout=30"]
                if vers:
                    opts.append(f"vers={vers}")
                write_fstab(base_lines + [f"{MARK}{v['name']}",
                                          f"{fstab_escape(what)} {fstab_escape(path)} cifs {','.join(opts)} 0 0"])
                code, out, err = run(["mount", path], timeout=45)
                if code == 0:
                    break
                raw = (err + out).strip()
                log.warning("Montage de %s (vers=%s) refusé : %s", what, vers or "auto", raw)
                failures.append(raw)
                if not re.search(r"error\((13|95|22)\)|permission denied|not supported|invalid argument", raw, re.I):
                    break  # erreur sans rapport avec la version : inutile d'insister
            if code != 0:
                raw = failures[0] if failures else ""
                msg = explain_mount(raw, self.env)
                detail = [l for l in raw.splitlines() if l.strip()]
                if detail and detail[-1] not in msg:
                    msg += f" (détail : {detail[-1][:200]})"
                # retour arrière complet
                write_fstab(backup_lines)
                if old_cred is not None:
                    fd = os.open(cred_path(v["name"]), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(old_cred)
                    if old:
                        run(["mount", old["path"]], timeout=45)
                else:
                    try:
                        os.remove(cred_path(v["name"]))
                        os.rmdir(path)
                    except OSError:
                        pass
                raise MountError(msg)
            self.errors.pop(v["name"], None)
            return {"ok": True, "path": path}

    @staticmethod
    def _without(lines, name):
        out, skip = [], False
        for line in lines:
            if skip:
                skip = False
                continue
            if line.strip() == f"{MARK}{name}":
                skip = True
                continue
            out.append(line)
        return out

    def remove(self, a):
        name = str(a.get("name") or "")
        if not NAME.match(name):
            raise MountError("Nom invalide")
        with self.lock:
            e = next((x for x in entries() if x["name"] == name), None)
            if not e:
                raise MountError("Partage inconnu")
            if is_mounted(e["path"]):
                code, out, err = run(["umount", e["path"]])
                if code != 0:
                    run(["umount", "-l", e["path"]])
            write_fstab(self._without(read_fstab(), name))
            for p in (cred_path(name),):
                try:
                    os.remove(p)
                except OSError:
                    pass
            try:
                os.rmdir(e["path"])
            except OSError:
                pass
            self.errors.pop(name, None)
            return {"ok": True}

    def remount(self, a):
        name = str(a.get("name") or "")
        e = next((x for x in entries() if x["name"] == name), None)
        if not e:
            raise MountError("Partage inconnu")
        return self._mount(e)

    def _mount(self, e):
        if is_mounted(e["path"]):
            run(["umount", "-l", e["path"]])
        os.makedirs(e["path"], exist_ok=True)
        code, out, err = run(["mount", e["path"]], timeout=45)
        if code != 0:
            msg = explain_mount(err + out, self.env)
            self.errors[e["name"]] = msg
            raise MountError(msg)
        self.errors.pop(e["name"], None)
        return {"ok": True}

    def ensure_all(self):
        """Remonte ce qui n'est pas monté (NAS redémarré, démarrage avant le réseau…)."""
        for e in entries():
            if not is_mounted(e["path"]):
                try:
                    self._mount(e)
                    log.info("Partage %s remonté", e["name"])
                except MountError as ex:
                    log.warning("Partage %s non monté : %s", e["name"], ex)

    def dispatch(self, req):
        cmd = req.get("cmd")
        if cmd in ("update", "update_status"):
            try:
                return update.start() if cmd == "update" else update.status()
            except update.UpdateError as e:
                raise MountError(str(e))
        fn = {"status": self.status, "discover": self.discover, "add": self.add, "remove": self.remove,
              "remount": self.remount}.get(cmd)
        if not fn:
            raise MountError("Commande inconnue")
        return fn(req.get("args") or {})


def make_server(mounts, sock_path=SOCK, allowed_uids=None):
    if allowed_uids is None:
        allowed_uids = {0, app_ids()[0]}

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                creds = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                _pid, uid, _gid = struct.unpack("3i", creds)
            except OSError:
                return
            if uid not in allowed_uids:
                log.warning("Connexion refusée (uid %s)", uid)
                return
            line = self.rfile.readline(65536)
            try:
                req = json.loads(line or b"{}")
                resp = {"ok": True, "result": mounts.dispatch(req)}
            except MountError as e:
                resp = {"ok": False, "error": str(e)}
            except Exception:
                log.exception("Erreur interne")
                resp = {"ok": False, "error": "Erreur interne du service de montage"}
            self.wfile.write(json.dumps(resp, ensure_ascii=False).encode() + b"\n")

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    os.makedirs(os.path.dirname(sock_path), exist_ok=True)
    try:
        os.remove(sock_path)
    except OSError:
        pass
    old = os.umask(0o117)
    try:
        server = Server(sock_path, Handler)
    finally:
        os.umask(old)
    try:
        gid = grp.getgrnam(APP_USER).gr_gid
        os.chown(sock_path, 0, gid)
        os.chown(os.path.dirname(sock_path), 0, gid)
        os.chmod(os.path.dirname(sock_path), 0o750)
    except (KeyError, PermissionError):
        pass
    os.chmod(sock_path, 0o660)
    return server


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if os.geteuid() != 0:
        log.warning("seance-mountd devrait tourner en root pour pouvoir monter des partages")
    mounts = Mounts()
    log.info("Environnement : %s", mounts.env)
    try:
        if harden_fstab():
            log.info("Options nosuid,nodev,noexec ajoutées aux partages existants")
    except OSError as e:
        log.warning("Mise à jour de fstab impossible : %s", e)
    server = make_server(mounts)
    stop = threading.Event()

    def loop():
        time.sleep(5)
        while not stop.is_set():
            try:
                mounts.ensure_all()
            except Exception:
                log.exception("Remontage")
            stop.wait(RETRY_EVERY)
    threading.Thread(target=loop, daemon=True).start()
    log.info("En écoute sur %s", SOCK)
    try:
        server.serve_forever()
    finally:
        stop.set()


# ------------------------------------------------------------------ client (côté Nexis)
def call(cmd, args=None, timeout=90, sock_path=None):
    path = sock_path or SOCK
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
    except (FileNotFoundError, ConnectionRefusedError):
        raise MountError("Le service de montage (seance-mountd) n'est pas démarré : relancer l'installation de Nexis")
    except PermissionError:
        raise MountError("Accès refusé au service de montage")
    data = b""
    try:
        with s:
            s.sendall(json.dumps({"cmd": cmd, "args": args or {}}).encode() + b"\n")
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
    except socket.timeout:
        raise MountError("Le service de montage ne répond pas (délai dépassé)")
    except OSError as e:
        raise MountError(f"Connexion au service de montage interrompue ({e.strerror or e})")
    try:
        resp = json.loads(data or b"{}")
    except json.JSONDecodeError:
        raise MountError("Réponse illisible du service de montage")
    if not resp.get("ok"):
        raise MountError(resp.get("error") or "Échec")
    return resp["result"]


if __name__ == "__main__":
    main()
