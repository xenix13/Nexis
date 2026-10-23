#!/usr/bin/env bash
# Installe ou met à jour Séance sur Ubuntu / Debian (conteneur LXC ou VM).
#
# En une commande (installation ET mise à jour), en root :
#   curl -fsSL https://raw.githubusercontent.com/xenix13/Nexis/main/deploy/install.sh | bash
# Ou depuis un clone du dépôt :
#   sudo ./deploy/install.sh
set -euo pipefail

REPO_URL="${SEANCE_REPO:-https://github.com/xenix13/Nexis.git}"
BRANCH="${SEANCE_BRANCH:-main}"
APP_DIR=/opt/seance
DATA_DIR=/var/lib/seance
CONF_DIR=/etc/seance
SRC_CACHE=/usr/local/src/seance

if [ "$(id -u)" -ne 0 ]; then
  echo "Lance ce script en root (sudo)." >&2
  exit 1
fi

echo "==> Paquets système"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 ca-certificates curl git ffmpeg cifs-utils smbclient >/dev/null
echo "   $(ffmpeg -version | head -1 | cut -d' ' -f1-3)"

PYV=$(python3 -c 'import sys; print("%d%02d" % sys.version_info[:2])')
if [ "$PYV" -lt 309 ]; then
  echo "Python 3.9 minimum requis (trouvé : $(python3 --version))." >&2
  exit 1
fi

# Lancé depuis un clone : on utilise ce dossier. Lancé via « curl | bash » : on clone / met à jour le dépôt.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/install.sh" ] && [ -f "$SCRIPT_DIR/../seance/api.py" ]; then
  SRC_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  echo "==> Récupération du code ($REPO_URL, branche $BRANCH)"
  if [ -d "$SRC_CACHE/.git" ]; then
    git -C "$SRC_CACHE" fetch -q --depth 1 origin "$BRANCH"
    git -C "$SRC_CACHE" reset -q --hard FETCH_HEAD
  else
    rm -rf "$SRC_CACHE"
    git clone -q --depth 1 --branch "$BRANCH" "$REPO_URL" "$SRC_CACHE"
  fi
  SRC_DIR="$SRC_CACHE"
fi
echo "   version : $(git -C "$SRC_DIR" rev-parse --short HEAD 2>/dev/null || echo locale)"

echo "==> Utilisateur système 'seance'"
if ! id seance >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin seance
fi

echo "==> Copie de l'application dans $APP_DIR"
mkdir -p "$APP_DIR" "$DATA_DIR" "$CONF_DIR" /mnt/seance
rm -rf "$APP_DIR/seance" "$APP_DIR/static.new"
cp -r "$SRC_DIR/seance" "$APP_DIR/seance"
cp -r "$SRC_DIR/static" "$APP_DIR/static.new"
rm -rf "$APP_DIR/static"
mv "$APP_DIR/static.new" "$APP_DIR/static"
find "$APP_DIR" -name __pycache__ -type d -prune -exec rm -rf {} +

echo "==> Vérification du lecteur vidéo (hls.js fourni dans le dépôt)"
( cd "$APP_DIR/static/vendor" && sha256sum -c --quiet hls.min.js.sha256 ) || { echo "hls.js altéré : installation arrêtée." >&2; exit 1; }

# Interface web officielle de Jellyfin (applications Jellyfin Media Player et téléphone : elles l'affichent
# depuis le serveur). Récupérée dans le dépôt APT de Jellyfin, signé : la clé est vérifiée par son empreinte,
# et rien n'est ajouté aux sources APT du système. SEANCE_JELLYFIN_WEB=0 pour s'en passer.
JF_WEB_SERIES="10.11"
JF_KEY_FPR="4918AABC486CA052358D778D49023CD01DE21A7B"
install_jellyfin_web() {
  local tmp="$1" arch version deb
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq gnupg >/dev/null
  curl -fsSL https://repo.jellyfin.org/jellyfin_team.gpg.key -o "$tmp/key.asc" || return 1
  if ! gpg --show-keys --with-colons "$tmp/key.asc" 2>/dev/null | grep -q "^fpr:::::::::${JF_KEY_FPR}:"; then
    echo "   clé du dépôt Jellyfin inattendue : interface web non installée" >&2
    return 1
  fi
  gpg --dearmor < "$tmp/key.asc" > "$tmp/jellyfin.gpg"
  arch="$(dpkg --print-architecture)"
  case "$arch" in amd64|arm64|armhf) ;; *) arch=amd64 ;; esac
  mkdir -p "$tmp/lists/partial" "$tmp/cache/archives/partial" "$tmp/etc/preferences.d"
  echo "deb [signed-by=$tmp/jellyfin.gpg arch=$arch] https://repo.jellyfin.org/debian bookworm main" > "$tmp/sources.list"
  local opts=(-o "Dir::Etc::SourceList=$tmp/sources.list" -o "Dir::Etc::SourceParts=/dev/null"
              -o "Dir::State::Lists=$tmp/lists" -o "Dir::Cache=$tmp/cache" -o "Dir::Etc::Preferences=/dev/null"
              -o "Dir::Etc::PreferencesParts=$tmp/etc/preferences.d" -o "APT::Architecture=$arch" -o "Debug::NoLocking=1"
              -o "APT::Sandbox::User=root")
  apt-get "${opts[@]}" update -qq >/dev/null || return 1
  version="$(apt-cache "${opts[@]}" madison jellyfin-web | awk -F'|' '{gsub(/ /,"",$2); print $2}' \
             | grep -E "^${JF_WEB_SERIES//./\\.}\." | sort -V | tail -1)"
  [ -n "$version" ] || return 1
  if [ "$(cat "$APP_DIR/jellyfin-web/seance-version.txt" 2>/dev/null)" = "$version" ]; then
    echo "   déjà à jour ($version)"
    return 0
  fi
  ( cd "$tmp" && apt-get "${opts[@]}" download -qq "jellyfin-web=$version" >/dev/null ) || return 1
  deb="$(ls "$tmp"/jellyfin-web_*.deb | head -1)"
  dpkg-deb -x "$deb" "$tmp/x"
  [ -f "$tmp/x/usr/share/jellyfin/web/index.html" ] || return 1
  rm -rf "$APP_DIR/jellyfin-web.new"
  cp -r "$tmp/x/usr/share/jellyfin/web" "$APP_DIR/jellyfin-web.new"
  echo "$version" > "$APP_DIR/jellyfin-web.new/seance-version.txt"
  rm -rf "$APP_DIR/jellyfin-web"
  mv "$APP_DIR/jellyfin-web.new" "$APP_DIR/jellyfin-web"
  echo "   jellyfin-web $version"
}
if [ "${SEANCE_JELLYFIN_WEB:-1}" != "0" ]; then
  echo "==> Interface web Jellyfin (applications Jellyfin pour Windows et téléphones)"
  JF_TMP="$(mktemp -d)"
  install_jellyfin_web "$JF_TMP" || echo "   Impossible de récupérer l'interface web Jellyfin (Android TV fonctionnera quand même)." >&2
  rm -rf "$JF_TMP"
fi

chown -R root:root "$APP_DIR"
chmod -R a+rX "$APP_DIR"
chown -R seance:seance "$DATA_DIR"
chmod 700 "$DATA_DIR"

if [ ! -f "$CONF_DIR/seance.env" ]; then
  cat > "$CONF_DIR/seance.env" <<'ENVFILE'
# Port d'écoute de Séance
SEANCE_PORT=8420
SEANCE_HOST=0.0.0.0
# Passe à 1 si Séance est derrière un reverse proxy HTTPS (Caddy, nginx, NPM…)
SEANCE_COOKIE_SECURE=0
# Passe à 1 pour lire l'IP réelle dans X-Forwarded-For (uniquement derrière TON proxy)
SEANCE_TRUST_PROXY=0
# Journaux détaillés
SEANCE_DEBUG=0
# Applications Jellyfin : port supplémentaire (8096 = celui qu'elles proposent), découverte UDP 7359
#SEANCE_JELLYFIN_PORT=8096
#SEANCE_JELLYFIN_DISCOVERY=1
ENVFILE
fi

echo "==> Service systemd"
cp "$SRC_DIR/deploy/seance.service" /etc/systemd/system/seance.service
cp "$SRC_DIR/deploy/seance-mountd.service" /etc/systemd/system/seance-mountd.service
systemctl daemon-reload
systemctl enable seance-mountd seance >/dev/null 2>&1
systemctl restart seance-mountd || true
systemctl restart seance || true
sleep 2

# Conteneur LXC non privilégié sans « nesting » : le bac à sable systemd échoue (226/NAMESPACE).
if ! systemctl is-active --quiet seance && \
   [ "$(systemctl show -p ExecMainStatus --value seance)" = "226" ]; then
  echo "==> Bac à sable systemd indisponible dans ce conteneur : service en version allégée"
  mkdir -p /etc/systemd/system/seance.service.d
  cat > /etc/systemd/system/seance.service.d/lxc.conf <<'DROPIN'
[Service]
PrivateTmp=false
PrivateDevices=false
ProtectSystem=false
ProtectHome=false
ProtectKernelTunables=false
ProtectKernelModules=false
ProtectControlGroups=false
ProtectHostname=false
ProtectClock=false
ProtectKernelLogs=false
RestrictNamespaces=false
ReadWritePaths=
DROPIN
  systemctl daemon-reload
  systemctl restart seance || true
  sleep 2
fi

if ! systemctl is-active --quiet seance; then
  echo "Le service n'a pas démarré. Regarde : journalctl -u seance -n 50" >&2
  exit 1
fi

PORT=$(grep -E '^SEANCE_PORT=' "$CONF_DIR/seance.env" | cut -d= -f2 || true)
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if ! systemctl is-active --quiet seance-mountd; then
  echo "Attention : le service de montage seance-mountd n'a pas démarré (journalctl -u seance-mountd -n 30)."
fi
if [ -r /proc/self/uid_map ] && [ "$(awk 'NR==1{print $2}' /proc/self/uid_map)" != "0" ]; then
  echo
  echo "Note : ce conteneur LXC est NON privilégié. Linux y interdit le montage SMB :"
  echo "l'onglet « Partages réseau » pourra lister les partages du NAS mais pas les monter."
  echo "Solutions : conteneur privilégié avec « features: mount=cifs », ou montage depuis l'hôte (README)."
fi
echo
echo "Séance tourne sur http://${IP:-<ip>}:${PORT:-8420}"
for d in /mnt/*/ /mnt/seance/*/; do
  [ -d "$d" ] || continue
  if ! runuser -u seance -- ls "$d" >/dev/null 2>&1; then
    echo "Attention : l'utilisateur 'seance' ne peut pas lire $d (droits du montage)."
  fi
done
if [ -f "$DATA_DIR/setup-code.txt" ]; then
  echo "Code d'installation (à saisir dans la page d'accueil) : $(cat "$DATA_DIR/setup-code.txt")"
fi
