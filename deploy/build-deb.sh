#!/usr/bin/env bash
# Construit le paquet Debian/Ubuntu de Nexis : deploy/build-deb.sh [dossier de sortie]
# Installation : apt install ./nexis_<version>_all.deb   (installe aussi ffmpeg, python3…)
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="$(realpath "${1:-dist}")"
VERSION=$(python3 -c 'import re; print(re.search(r"__version__ = \"(.+?)\"", open("seance/__init__.py").read()).group(1))')
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
PKG="$WORK/nexis_${VERSION}_all"

mkdir -p "$PKG/DEBIAN" "$PKG/opt/seance" "$OUT"
cp -r seance static deploy "$PKG/opt/seance/"
find "$PKG" -name __pycache__ -type d -prune -exec rm -rf {} +
( cd "$PKG/opt/seance/static/vendor" && sha256sum -c --quiet hls.min.js.sha256 )
( cd "$PKG/opt/seance/static/vendor/qrcode" && sha256sum -c --quiet qrcode.mjs.sha256 )
( cd "$PKG/opt/seance/static/vendor/jassub" && sha256sum -c --quiet jassub.sha256 )
( cd "$PKG/opt/seance/static/vendor/libpgs" && sha256sum -c --quiet libpgs.sha256 )

cat > "$PKG/DEBIAN/control" <<CONTROL
Package: nexis
Version: $VERSION
Architecture: all
Maintainer: Nexis <https://github.com/xenix13/Nexis>
Depends: python3 (>= 3.9), ffmpeg, ca-certificates, cifs-utils, smbclient, systemd, passwd
Recommends: curl, git
Section: video
Priority: optional
Homepage: https://github.com/xenix13/Nexis
Description: Nexis - serveur multimédia auto-hébergé
 Films, séries et animes lus dans le navigateur (lecture directe, remux ou
 transcodage), profils, regarder ensemble, TV en direct, demandes via Seerr.
CONTROL

cat > "$PKG/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
  # même préparation que l'installation par script : utilisateur, dossiers, services, carte graphique
  bash /opt/seance/deploy/setup-system.sh || echo "Nexis : voir journalctl -u seance -n 50" >&2
fi
POSTINST

cat > "$PKG/DEBIAN/prerm" <<'PRERM'
#!/bin/sh
set -e
if [ "$1" = "remove" ] && command -v systemctl >/dev/null; then
  systemctl disable --now seance.service seance-mountd.service >/dev/null 2>&1 || true
fi
PRERM

cat > "$PKG/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
  rm -f /etc/systemd/system/seance.service /etc/systemd/system/seance-mountd.service
  rm -rf /etc/systemd/system/seance.service.d
  rm -f /usr/local/bin/nexis
  command -v systemctl >/dev/null && systemctl daemon-reload || true
fi
if [ "$1" = "purge" ]; then
  # « purge » efface aussi la configuration et les données (base, cache) ; « remove » les garde
  rm -rf /etc/seance /var/lib/seance
fi
POSTRM

chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm" "$PKG/DEBIAN/postrm"
dpkg-deb --root-owner-group -Zxz --build "$PKG" "$OUT/nexis_${VERSION}_all.deb" >/dev/null
echo "$OUT/nexis_${VERSION}_all.deb"
