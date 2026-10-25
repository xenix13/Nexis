#!/usr/bin/env bash
# Crée un conteneur LXC Ubuntu 24.04 prêt pour Nexis et l'y installe. À lancer sur l'HÔTE Proxmox VE, en root :
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/xenix13/Nexis/main/deploy/proxmox-lxc.sh)"
# Réglages possibles (variables d'environnement) : CTID, CT_HOSTNAME, STORAGE, TEMPLATE_STORAGE, BRIDGE,
# CORES, MEMORY (Mo), SWAP (Mo), DISK (Go), PRIVILEGED (1 = partages SMB montables depuis Nexis), SEANCE_BRANCH.
set -euo pipefail

command -v pct >/dev/null || { echo "À lancer sur un hôte Proxmox VE (commande pct introuvable)." >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "Lance ce script en root." >&2; exit 1; }

CTID="${CTID:-$(pvesh get /cluster/nextid)}"
CT_HOSTNAME="${CT_HOSTNAME:-nexis}"
STORAGE="${STORAGE:-local-lvm}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
BRIDGE="${BRIDGE:-vmbr0}"
CORES="${CORES:-4}"
MEMORY="${MEMORY:-4096}"
SWAP="${SWAP:-2048}"          # un transcodage 4K prend 0,5 à 0,7 Go : de la marge évite les saccades
DISK="${DISK:-16}"
PRIVILEGED="${PRIVILEGED:-1}"
BRANCH="${SEANCE_BRANCH:-main}"

echo "==> Modèle Ubuntu 24.04"
pveam update >/dev/null
TEMPLATE=$(pveam available --section system | awk '/ubuntu-24\.04-standard/ {print $2}' | sort -V | tail -1)
[ -n "$TEMPLATE" ] || { echo "Modèle ubuntu-24.04-standard introuvable (pveam available)." >&2; exit 1; }
pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE" || pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"

if [ "$PRIVILEGED" = "1" ]; then
  UNPRIV=0; FEATURES="nesting=1,mount=cifs"   # privilégié : Nexis peut monter les partages SMB lui-même
else
  UNPRIV=1; FEATURES="nesting=1"              # non privilégié : partages à monter sur l'hôte (README)
fi

echo "==> Conteneur $CTID ($CT_HOSTNAME) : $CORES cœurs, $MEMORY Mo de mémoire, $SWAP Mo de swap, $DISK Go"
pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
  --hostname "$CT_HOSTNAME" --ostype ubuntu --cores "$CORES" --memory "$MEMORY" --swap "$SWAP" \
  --rootfs "$STORAGE:$DISK" --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp" \
  --unprivileged "$UNPRIV" --features "$FEATURES" --onboot 1 --start 1

echo "==> Attente du réseau"
for _ in $(seq 1 30); do
  pct exec "$CTID" -- getent hosts raw.githubusercontent.com >/dev/null 2>&1 && break
  sleep 2
done

echo "==> Installation de Nexis dans le conteneur"
pct exec "$CTID" -- bash -c "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates >/dev/null \
  && curl -fsSL https://raw.githubusercontent.com/xenix13/Nexis/$BRANCH/deploy/install.sh | SEANCE_BRANCH=$BRANCH bash"

echo
echo "Conteneur $CTID prêt. Pour la carte graphique (transcodage matériel), suis la section du README"
echo "« Transcodage matériel » puis relance la mise à jour de Nexis depuis son interface."
