"""Sauvegarde et restauration du serveur.

Une sauvegarde est une archive .tar.gz qui contient :
- seance.db : la base (comptes, mots de passe chiffrés, double authentification, historique, notes, playlists,
  réglages, bibliothèques, métadonnées), copiée par l'API de sauvegarde de SQLite (cohérente même en pleine
  écriture) ;
- federation/ : identité et certificats des serveurs liés (sans elle, il faudrait les lier à nouveau) ;
- push/ : clé des notifications (sans elle, chaque appareil devrait se réabonner) ;
- avatars/ : images de profil ;
- manifest.json : version de Nexis et de la base, date.
Les vidéos et les photos n'y sont pas : elles restent dans leurs dossiers. Les caches (images redimensionnées,
sous-titres extraits, aperçus) se refont tout seuls.

Restaurer se fait en deux temps, parce que la base est ouverte tant que Nexis tourne : l'archive est vérifiée
puis dépliée dans data/restore-pending, et c'est au démarrage suivant, avant d'ouvrir la base, que les
fichiers sont mis en place. Ceux d'avant sont gardés dans data/before-restore-<date>.
"""
import json
import logging
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import time

from . import __version__ as VERSION

log = logging.getLogger("seance.backup")

FORMAT = 1
DIRS = ("federation", "push", "avatars")
NAME = re.compile(r"^nexis-\d{8}-\d{6}(?:-[a-z0-9]{1,12})?\.tar\.gz$")
PENDING = "restore-pending"
KEEP_BEFORE = 2  # copies « avant restauration » gardées
MAX_UPLOAD = 8 * 1024 ** 3


class BackupError(Exception):
    pass


def directory(ctx):
    return ctx.db.setting("backup_dir") or os.path.join(ctx.data_dir, "backups")


def check_directory(path):
    """Dossier de destination choisi par l'administrateur : absolu, existant, accessible en écriture."""
    if not isinstance(path, str) or not path.startswith("/") or "\0" in path or len(path) > 400:
        raise BackupError("Chemin absolu attendu (ex. /mnt/nas/sauvegardes)")
    path = os.path.normpath(path)
    if not os.path.isdir(path):
        raise BackupError("Dossier introuvable")
    if not os.access(path, os.W_OK):
        raise BackupError("Nexis ne peut pas écrire dans ce dossier")
    return path


def _schema(db_path):
    c = sqlite3.connect(db_path)
    try:
        return c.execute("PRAGMA user_version").fetchone()[0]
    finally:
        c.close()


def create(ctx, label="", keep=None):
    """Nouvelle sauvegarde -> {name, size, created, ...}. keep : nombre d'archives automatiques gardées."""
    dest = directory(ctx)
    os.makedirs(dest, mode=0o700, exist_ok=True)
    now = time.time()
    name = time.strftime("nexis-%Y%m%d-%H%M%S", time.localtime(now)) + (f"-{label}" if label else "") + ".tar.gz"
    final = os.path.join(dest, name)
    tmp_dir = tempfile.mkdtemp(prefix="backup-", dir=ctx.data_dir)
    part = final + ".partial"
    try:
        db_copy = os.path.join(tmp_dir, "seance.db")
        src = sqlite3.connect(ctx.db.path, timeout=30)
        dst = sqlite3.connect(db_copy)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        manifest = {"format": FORMAT, "version": VERSION, "schema": _schema(db_copy), "created": int(now),
                    "server": ctx.db.setting("server_name") or ""}
        man = os.path.join(tmp_dir, "manifest.json")
        with open(man, "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # mots de passe, clés : lisible par Nexis seul
        with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz", compresslevel=6) as tar:
            tar.add(man, "manifest.json")  # en premier : la liste lit seulement le début de l'archive
            tar.add(db_copy, "seance.db")
            for d in DIRS:
                p = os.path.join(ctx.data_dir, d)
                if os.path.isdir(p):
                    tar.add(p, d, filter=lambda t: t if (t.isfile() or t.isdir()) else None)
        os.replace(part, final)
    except (OSError, sqlite3.Error, tarfile.TarError) as e:
        try:
            os.remove(part)
        except OSError:
            pass
        raise BackupError(f"Sauvegarde impossible : {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    if keep:
        prune(ctx, keep)
    log.info("Sauvegarde %s (%.1f Mo)", final, os.path.getsize(final) / 1e6)
    return describe(final)


def describe(path):
    st = os.stat(path)
    out = {"name": os.path.basename(path), "size": st.st_size, "created": int(st.st_mtime), "version": None,
           "schema": None, "auto": "-auto" in os.path.basename(path)}
    try:
        with tarfile.open(path, "r:gz") as tar:
            m = tar.next()
            if m and m.name == "manifest.json" and m.isfile() and m.size < 65536:
                man = json.loads(tar.extractfile(m).read())
                out.update(version=man.get("version"), schema=man.get("schema"), created=man.get("created") or out["created"])
    except (OSError, tarfile.TarError, ValueError, EOFError):
        out["broken"] = True
    return out


def listing(ctx):
    d = directory(ctx)
    if not os.path.isdir(d):
        return []
    files = [os.path.join(d, n) for n in os.listdir(d) if NAME.match(n)]
    return sorted((describe(p) for p in files), key=lambda b: b["created"], reverse=True)


def path_of(ctx, name):
    if not isinstance(name, str) or not NAME.match(name):
        raise BackupError("Sauvegarde introuvable")
    p = os.path.join(directory(ctx), name)
    if not os.path.isfile(p):
        raise BackupError("Sauvegarde introuvable")
    return p


def prune(ctx, keep):
    """Ne garde que les `keep` sauvegardes automatiques les plus récentes (celles faites à la main restent)."""
    autos = [b for b in listing(ctx) if b["auto"]]
    for b in autos[keep:]:
        try:
            os.remove(os.path.join(directory(ctx), b["name"]))
        except OSError:
            pass


def _allowed(name):
    if name in ("manifest.json", "seance.db"):
        return True
    parts = name.split("/")
    return parts[0] in DIRS and all(p not in ("", ".", "..") for p in parts)


def validate(path):
    """Archive lisible, venue de Nexis, et d'une base que cette version sait ouvrir -> manifeste."""
    from .db import MIGRATIONS
    try:
        with tarfile.open(path, "r:gz") as tar:
            members = tar.getmembers()
            names = {m.name for m in members}
            if "manifest.json" not in names or "seance.db" not in names:
                raise BackupError("Ce fichier n'est pas une sauvegarde de Nexis")
            for m in members:
                if m.name.startswith("/") or not _allowed(m.name) or not (m.isfile() or m.isdir()):
                    raise BackupError("Archive refusée : contenu inattendu")
            man = json.loads(tar.extractfile("manifest.json").read())
    except (OSError, tarfile.TarError, ValueError, EOFError, KeyError):
        raise BackupError("Archive illisible ou incomplète")
    if not isinstance(man, dict) or man.get("format") != FORMAT:
        raise BackupError("Format de sauvegarde inconnu")
    if not isinstance(man.get("schema"), int) or man["schema"] > len(MIGRATIONS):
        raise BackupError("Sauvegarde faite par une version plus récente de Nexis : mettez Nexis à jour d'abord")
    return man


def stage(data_dir, path):
    """Vérifie l'archive et la déplie pour le prochain démarrage."""
    if not os.path.isfile(path):
        raise BackupError("Fichier introuvable")
    man = validate(path)
    pending = os.path.join(data_dir, PENDING)
    shutil.rmtree(pending, ignore_errors=True)
    tmp = pending + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, mode=0o700)
    try:
        with tarfile.open(path, "r:gz") as tar:
            for m in tar.getmembers():
                if m.isdir():
                    os.makedirs(os.path.join(tmp, m.name), mode=0o700, exist_ok=True)
                    continue
                target = os.path.join(tmp, m.name)
                os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
                with tar.extractfile(m) as src, open(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
        c = sqlite3.connect(os.path.join(tmp, "seance.db"))
        try:
            if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("La base de cette sauvegarde est abîmée")
        finally:
            c.close()
    except (OSError, tarfile.TarError, sqlite3.Error) as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise BackupError(f"Restauration impossible : {e}")
    except BackupError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    os.replace(tmp, pending)
    return man


def apply_pending(data_dir):
    """Au démarrage, avant d'ouvrir la base : met en place une restauration préparée. -> manifeste ou None."""
    pending = os.path.join(data_dir, PENDING)
    if not os.path.isfile(os.path.join(pending, "seance.db")):
        return None
    try:
        with open(os.path.join(pending, "manifest.json"), encoding="utf-8") as f:
            man = json.load(f)
    except (OSError, ValueError):
        man = {}
    before = os.path.join(data_dir, time.strftime("before-restore-%Y%m%d-%H%M%S"))
    os.makedirs(before, mode=0o700)
    for name in ("seance.db", "seance.db-wal", "seance.db-shm", *DIRS):
        p = os.path.join(data_dir, name)
        if os.path.exists(p):
            shutil.move(p, os.path.join(before, name))
    for name in ("seance.db", *DIRS):
        p = os.path.join(pending, name)
        if os.path.exists(p):
            shutil.move(p, os.path.join(data_dir, name))
    shutil.rmtree(pending, ignore_errors=True)
    olds = sorted(n for n in os.listdir(data_dir) if n.startswith("before-restore-"))
    for n in olds[:-KEEP_BEFORE]:
        shutil.rmtree(os.path.join(data_dir, n), ignore_errors=True)
    log.warning("Sauvegarde du %s restaurée (version %s) ; l'état précédent est dans %s",
                time.strftime("%d/%m/%Y %H:%M", time.localtime(man.get("created") or 0)), man.get("version"), before)
    return man


def receive(ctx, req):
    """Archive envoyée depuis le navigateur (pour restaurer sur un nouveau serveur) : écrite par morceaux dans le
    dossier des sauvegardes, puis vérifiée. -> description."""
    try:
        length = int(req.headers.get("Content-Length") or 0)
    except ValueError:
        raise BackupError("Taille invalide")
    if not 0 < length <= MAX_UPLOAD:
        raise BackupError("Fichier vide ou trop volumineux")
    dest = directory(ctx)
    os.makedirs(dest, mode=0o700, exist_ok=True)
    name = time.strftime("nexis-%Y%m%d-%H%M%S-envoi.tar.gz")
    final = os.path.join(dest, name)
    part = final + ".partial"
    left = length
    req._body = b""  # corps lu ici, par morceaux (le serveur ne doit pas le relire après la réponse)
    try:
        with open(os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
            while left:
                chunk = req.handler.rfile.read(min(left, 1024 * 1024))
                if not chunk:
                    raise BackupError("Envoi interrompu")
                f.write(chunk)
                left -= len(chunk)
        validate(part)
        os.replace(part, final)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        if left:
            req.handler.close_connection = True  # reste du corps non lu : la connexion ne peut pas resservir
        raise
    return describe(final)
