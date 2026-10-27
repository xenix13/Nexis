#!/usr/bin/env bash
# Installe ou met à jour Nexis sur Ubuntu / Debian (conteneur LXC ou VM).
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
# (jamais le dossier installé lui-même : /opt/seance/deploy/install.sh s'effacerait en se copiant)
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/install.sh" ] && [ -f "$SCRIPT_DIR/../seance/api.py" ] \
   && [ "$(cd "$SCRIPT_DIR/.." && pwd)" != "$APP_DIR" ]; then
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

echo "==> Copie de l'application dans $APP_DIR"
mkdir -p "$APP_DIR" "$DATA_DIR" "$CONF_DIR" /mnt/seance
rm -rf "$APP_DIR/seance" "$APP_DIR/static.new" "$APP_DIR/deploy"
cp -r "$SRC_DIR/seance" "$APP_DIR/seance"
cp -r "$SRC_DIR/static" "$APP_DIR/static.new"
cp -r "$SRC_DIR/deploy" "$APP_DIR/deploy"
rm -rf "$APP_DIR/static"
mv "$APP_DIR/static.new" "$APP_DIR/static"
find "$APP_DIR" -name __pycache__ -type d -prune -exec rm -rf {} +

echo "==> Vérification des bibliothèques fournies (hls.js, qrcode, JASSUB, libpgs)"
( cd "$APP_DIR/static/vendor" && sha256sum -c --quiet hls.min.js.sha256 ) || { echo "hls.js altéré : installation arrêtée." >&2; exit 1; }
( cd "$APP_DIR/static/vendor/qrcode" && sha256sum -c --quiet qrcode.mjs.sha256 ) || { echo "qrcode altéré : installation arrêtée." >&2; exit 1; }
( cd "$APP_DIR/static/vendor/jassub" && sha256sum -c --quiet jassub.sha256 ) || { echo "JASSUB altéré : installation arrêtée." >&2; exit 1; }
( cd "$APP_DIR/static/vendor/libpgs" && sha256sum -c --quiet libpgs.sha256 ) || { echo "libpgs altéré : installation arrêtée." >&2; exit 1; }

rm -rf "$APP_DIR/jellyfin-web"  # dossier d'une ancienne version (retiré)

# d'où vient cette installation : la mise à jour depuis l'interface repartira de là
printf 'SEANCE_REPO=%s\nSEANCE_BRANCH=%s\n' "$REPO_URL" "$BRANCH" > "$CONF_DIR/source"
chmod 644 "$CONF_DIR/source"

export APP_DIR DATA_DIR CONF_DIR
bash "$APP_DIR/deploy/setup-system.sh"
