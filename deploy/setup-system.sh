#!/usr/bin/env bash
# Prépare le système pour Nexis : utilisateur, dossiers, configuration, services systemd, carte graphique.
# Commun à install.sh (installation depuis GitHub) et au paquet .deb (postinst). En root.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/seance}"
DATA_DIR="${DATA_DIR:-/var/lib/seance}"
CONF_DIR="${CONF_DIR:-/etc/seance}"
UNITS="$APP_DIR/deploy"

echo "==> Utilisateur système 'seance'"
if ! id seance >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin seance
fi

mkdir -p "$DATA_DIR" "$CONF_DIR" /mnt/seance
chown -R root:root "$APP_DIR"
chmod -R a+rX "$APP_DIR"
chown -R seance:seance "$DATA_DIR"
chmod 700 "$DATA_DIR"

if [ ! -f "$CONF_DIR/seance.env" ]; then
  cat > "$CONF_DIR/seance.env" <<'ENVFILE'
# Port d'écoute de Nexis
SEANCE_PORT=8420
SEANCE_HOST=0.0.0.0
# Passe à 1 si Nexis est derrière un reverse proxy HTTPS (Caddy, nginx, NPM…)
SEANCE_COOKIE_SECURE=0
# Passe à 1 pour lire l'IP réelle dans X-Forwarded-For (uniquement derrière TON proxy)
SEANCE_TRUST_PROXY=0
# Journaux détaillés
SEANCE_DEBUG=0
ENVFILE
fi

echo "==> Service systemd"
cp "$UNITS/seance.service" /etc/systemd/system/seance.service
cp "$UNITS/seance-mountd.service" /etc/systemd/system/seance-mountd.service
# Carte graphique (transcodage matériel) : le service doit voir /dev/nvidia* ou /dev/dri, masqués sinon
if [ -e /dev/nvidia0 ] || ls /dev/dri/renderD* >/dev/null 2>&1; then
  echo "==> Carte graphique détectée : le service y a accès (transcodage matériel, à activer dans Administration)"
  GPU_GROUPS=""
  for g in video render; do getent group "$g" >/dev/null && GPU_GROUPS="$GPU_GROUPS $g"; done
  mkdir -p /etc/systemd/system/seance.service.d
  {
    echo "[Service]"
    echo "PrivateDevices=false"
    [ -n "$GPU_GROUPS" ] && echo "SupplementaryGroups=$GPU_GROUPS"
  } > /etc/systemd/system/seance.service.d/gpu.conf
else
  rm -f /etc/systemd/system/seance.service.d/gpu.conf
fi
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
echo "Nexis tourne sur http://${IP:-<ip>}:${PORT:-8420}"
for d in /mnt/*/ /mnt/seance/*/; do
  [ -d "$d" ] || continue
  if ! runuser -u seance -- ls "$d" >/dev/null 2>&1; then
    echo "Attention : l'utilisateur 'seance' ne peut pas lire $d (droits du montage)."
  fi
done
if [ -f "$DATA_DIR/setup-code.txt" ]; then
  echo "Code d'installation (à saisir dans la page d'accueil) : $(cat "$DATA_DIR/setup-code.txt")"
fi
