"""Mise à jour de Nexis depuis l'interface d'administration.

- Nexis (sans privilèges) regarde sur GitHub s'il existe une version plus récente : check_latest().
- Le service root seance-mountd lance la mise à jour : la même commande que l'installation à la main
  (`curl …/deploy/install.sh | bash`), depuis le dépôt et la branche notés par install.sh dans
  /etc/seance/source. Aucune adresse ni commande ne vient de l'interface.
"""
import json
import os
import re
import shlex
import subprocess
import time
import urllib.request

SOURCE = os.environ.get("SEANCE_SOURCE_FILE", "/etc/seance/source")
LOG = os.environ.get("SEANCE_UPDATE_LOG", "/var/log/seance-update.log")
UNIT = "seance-update"
DEFAULT_REPO, DEFAULT_BRANCH = "https://github.com/xenix13/Nexis.git", "main"
REPO = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100}?)(?:\.git)?$")
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
VERSION = re.compile(r'__version__\s*=\s*"([0-9][0-9A-Za-z.+-]{0,30})"')
CHECK_EVERY = 1800


class UpdateError(Exception):
    pass


def read_source():
    """(propriétaire, dépôt, branche) d'où vient cette installation."""
    vals = {}
    try:
        with open(SOURCE, encoding="utf-8") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                vals[k] = v
    except OSError:
        pass
    repo, branch = vals.get("SEANCE_REPO") or DEFAULT_REPO, vals.get("SEANCE_BRANCH") or DEFAULT_BRANCH
    m = REPO.match(repo)
    if not m or not BRANCH.match(branch) or ".." in branch:
        raise UpdateError("Source de mise à jour invalide dans /etc/seance/source")
    return m.group(1), m.group(2), branch


def _version_key(v):
    return [int(x) if x.isdigit() else 0 for x in re.split(r"[.+-]", v)]


def newer(latest, current):
    return _version_key(latest) > _version_key(current)


_cache = {}


def forget():
    _cache.clear()


def check_latest(timeout=8):
    """Dernière version publiée sur la branche suivie, et ses derniers changements (gardé 30 min)."""
    owner, repo, branch = read_source()
    key = (owner, repo, branch)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CHECK_EVERY:
        return hit[1]
    headers = {"User-Agent": "Nexis"}
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/seance/__init__.py"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            m = VERSION.search(r.read(4096).decode("utf-8", "replace"))
    except OSError as e:
        raise UpdateError(f"GitHub injoignable ({getattr(e, 'reason', e)})")
    if not m:
        raise UpdateError("Version introuvable sur GitHub")
    changes = []
    try:  # titres des derniers changements ; facultatif (limite de l'API GitHub)
        api = f"https://api.github.com/repos/{owner}/{repo}/commits?sha={branch}&per_page=8"
        with urllib.request.urlopen(urllib.request.Request(api, headers=headers), timeout=timeout) as r:
            for c in json.loads(r.read(400_000)):
                msg = (c.get("commit") or {}).get("message") or ""
                changes.append({"title": msg.splitlines()[0][:200] if msg else "",
                                "date": ((c.get("commit") or {}).get("committer") or {}).get("date")})
    except (OSError, ValueError):
        pass
    out = {"latest": m.group(1), "changes": changes, "source": f"{owner}/{repo} ({branch})"}
    _cache[key] = (time.time(), out)
    return out


# ------------------------------------------------------------------ côté root (seance-mountd)
def running():
    # unité « oneshot » : elle reste « activating » pendant toute l'installation (jamais « active »)
    p = subprocess.run(["systemctl", "show", "-p", "ActiveState", "--value", UNIT],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return p.stdout.strip() in ("active", "activating", "reloading", "deactivating")


def start():
    """Lance l'installation à jour dans une unité systemd à part : elle survit au redémarrage de
    seance-mountd et de Nexis qu'elle provoque."""
    if running():
        raise UpdateError("Une mise à jour est déjà en cours")
    owner, repo, branch = read_source()
    script = (f"set -o pipefail; curl -fsSL {shlex.quote(f'https://raw.githubusercontent.com/{owner}/{repo}/{branch}/deploy/install.sh')}"
              f" | SEANCE_REPO={shlex.quote(f'https://github.com/{owner}/{repo}.git')} SEANCE_BRANCH={shlex.quote(branch)} bash")
    # --no-block : sans lui, systemd-run attend la FIN de l'installation (unité oneshot) et la requête expire
    cmd = ["systemd-run", f"--unit={UNIT}", "--collect", "--quiet", "--no-block", "--property=Type=oneshot", "/bin/bash", "-c",
           f"{{ date; {script}; echo \"[terminé, code $?]\"; }} > {shlex.quote(LOG)} 2>&1"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise UpdateError("systemd est introuvable : mise à jour automatique impossible sur cette installation (Docker : mettre à jour l'image)")
    if p.returncode != 0:
        raise UpdateError("Lancement impossible : " + (p.stderr.decode("utf-8", "replace").strip()[-300:] or "erreur"))
    return {"started": True}


# Étapes affichées par install.sh / setup-system.sh (« ==> … ») : de quoi montrer une progression
STEPS = [("Paquets système", 10, "Mise à jour des paquets système"),
         ("Récupération du code", 25, "Téléchargement de la nouvelle version"),
         ("Copie de l'application", 45, "Installation des fichiers"),
         ("Vérification du lecteur", 55, "Vérification des fichiers"),
         ("Utilisateur système", 65, "Configuration du système"),
         ("Service systemd", 80, "Redémarrage de Nexis")]
END = re.compile(r"^\[terminé, code (\d+)\]")


def progress(lines):
    """Où en est l'installation d'après son journal : (pourcentage, étape, terminé, réussi)."""
    percent, label, done, ok = 3, "Démarrage", False, None
    for line in lines:
        m = END.match(line)
        if m:
            done, ok = True, m.group(1) == "0"
            percent, label = (100, "Terminé") if ok else (percent, "Échec")
            continue
        if line.startswith("==> "):
            for key, pct, text in STEPS:
                if line[4:].startswith(key) and pct >= percent:
                    percent, label = pct, text
    return percent, label, done, ok


def status():
    try:
        with open(LOG, "rb") as f:
            data = f.read()[-200_000:]  # journal entier (pour les étapes), on n'en renvoie que la fin
            started = os.fstat(f.fileno()).st_ctime
    except OSError:
        data, started = b"", None
    lines = data.decode("utf-8", "replace").splitlines()
    percent, label, done, ok = progress(lines)
    return {"running": running(), "log": lines[-25:], "percent": percent, "step": label, "done": done, "ok": ok,
            "log_time": started}
