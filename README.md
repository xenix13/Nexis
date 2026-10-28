# Nexis

Serveur multimédia auto-hébergé pour films, séries et animes. Nexis analyse vos dossiers, récupère affiches et résumés, et diffuse dans le navigateur, sur la TV ou sur le téléphone, avec une interface multi-utilisateurs. Aucun compte externe ni abonnement.

- **Aucune dépendance Python** : Python 3 (bibliothèque standard) et **ffmpeg**. Le lecteur [hls.js](https://github.com/video-dev/hls.js) (1.5.15) est fourni dans le dépôt ; son empreinte SHA-256 est vérifiée à l'installation.
- Polices Outfit et Inter (licence SIL OFL, `static/fonts/`) servies par le serveur : aucune ressource externe.
- Conçu pour un **conteneur LXC** ou une VM Ubuntu/Debian, **avec ou sans carte graphique** : Nexis évite le transcodage chaque fois que possible.

## Fonctionnalités

| | |
|---|---|
| **Bibliothèques** | Nombre illimité (Films, Séries, Animes…), chacune avec un ou plusieurs dossiers. Détection automatique des ajouts, webhook Radarr/Sonarr, scan incrémental. Un partage non monté ne vide jamais une bibliothèque |
| **Reconnaissance** | `Titre (Année)/…`, noms de type « scène » (`Inception.2010.1080p.BluRay…`), `S01E02`, `1x02`, `Saison 1/…`, animes `[Groupe] Titre - 12 [1080p]`, numérotation absolue (`Episode 1071`), épisodes spéciaux (`Season 00`, `Specials`) |
| **Métadonnées** | TMDB (affiches, fonds, résumés, genres, distribution, titres et images d'épisodes, classification), en français avec repli en anglais. Les fichiers `.nfo` et `poster.jpg` / `fanart.jpg` existants sont pris en compte. Correction manuelle avec « Identifier… » |
| **Comptes** | Comptes propres à Nexis, administrateurs, accès par bibliothèque, désactivation, photo de profil |
| **Espace personnel** | Reprise de lecture, « Reprendre et à suivre », suggestions affinées par « J'aime / Je n'aime pas / Ne plus suggérer », notifications de nouveaux épisodes, historique et bilan de visionnage, épisodes vus/non vus, langues préférées, apparence des sous-titres, anti-spoiler, thème, couleur d'accent, taille des affiches, page d'ouverture, rangées épinglées, ordre de l'accueil et du menu, réglages par appareil |
| **Collections et listes** | Sagas créées automatiquement depuis TMDB, collections manuelles, liste « À regarder » par utilisateur, playlists personnelles |
| **Bonus** | Bandes-annonces (YouTube via TMDB ou fichiers locaux), coulisses, scènes coupées, interviews, rangés sous le film ou la série |
| **Découvrir et demandes** | Catalogues des plateformes de streaming (via TMDB) croisés avec le contenu du serveur, bouton « Demander » relié à Seerr |
| **Lecteur** | Choix de l'audio, des sous-titres et de la qualité pendant la lecture, raccourcis clavier, chapitres en vignettes, « Passer l'intro », épisode suivant avec compte à rebours, lecteur réductible |
| **Regarder ensemble** | Lecture synchronisée à plusieurs, chacun sur son appareil : pauses, sauts et épisode suivant partagés |
| **TV en direct** | Listes M3U (IPTV, box opérateur) et guide XMLTV : grille des chaînes, guide des programmes, favoris, changement de chaîne |
| **Tâches planifiées** | Scan, métadonnées, images de chapitres, détection des intros et génériques, sous-titres, guide TV, nettoyage |
| **Sous-titres** | SRT, ASS/SSA, WebVTT, mov_text (internes ou externes : `Film.fr.srt`, `Film.forced.srt`…) affichés par le navigateur, **sans transcodage**. ASS rendus par libass (positionnement, karaoké, polices jointes), PGS rendus par le navigateur, VobSub incrustés |
| **Applications** | Web app installable (Windows, macOS, Linux, Android, iPhone, iPad), téléchargements hors ligne, application Android TV native |
| **Paramètres** | Un seul espace, organisé en Mon compte, Serveur, Médiathèque et Lecture, avec recherche |
| **Serveurs liés** | Plusieurs serveurs Nexis reliés par une liaison chiffrée qui leur est propre : comptes communs, bibliothèques de chacun visibles et lisibles depuis l'autre (ajoutées ou fusionnées : un titre une seule fois, meilleure copie choisie automatiquement), progression partagée. Un seul des deux doit être joignable |
| **Langues** | Interface en **français** ou en **anglais**, au choix de chaque utilisateur ; langue par défaut des nouveaux comptes choisie par l'administrateur. Messages du serveur et appli TV dans la même langue |

### Choix du mode de lecture

Au lancement, le lecteur indique au serveur les codecs que le navigateur sait décoder (H.264, HEVC 8/10 bits, AV1, VP9, AAC, AC3, E-AC3, FLAC, Opus…). Nexis choisit alors le mode le moins coûteux :

| Mode | Cas | Coût processeur |
|---|---|---|
| **Lecture directe** | MP4 dont la vidéo et le son sont lisibles par le navigateur | nul |
| **Remux** | Vidéo lisible par le navigateur dans un MKV, ou piste audio à convertir (DTS, TrueHD, 5.1…) | très faible : seule la piste audio est convertie |
| **Transcodage** | Vidéo non décodable par le navigateur (HEVC sur Firefox, H.264 10 bits…), sous-titres VobSub, qualité réduite | 1 à 2 cœurs par lecture en 1080p sans carte graphique |

Le lecteur reçoit une playlist couvrant toute la vidéo, découpée en segments de 6 s (calés sur les images clés en remux). Les segments sont produits à la demande au format fMP4, avec les horodatages d'origine : la position affichée est toujours exacte et un saut ne recrée pas le flux. Si le lecteur demande un segment éloigné, ffmpeg est relancé à cet endroit.

Le menu ⚙ du lecteur indique le mode utilisé et sa raison. ffmpeg est mis en pause après 3 minutes d'avance et tourne en priorité réduite. Le nombre de transcodages simultanés est limité (*Paramètres → Lecture → Transcodage*).

### Transcodage matériel

Avec une carte graphique, le décodage et l'encodage sont pris en charge par celle-ci, ce qui permet beaucoup plus de transcodages simultanés. Réglage : *Paramètres → Lecture → Transcodage → Carte graphique*.

| Moteur | Cartes | Remarques |
|---|---|---|
| **NVIDIA NVENC** | GeForce GTX 10xx et suivantes | 8 encodages simultanés au maximum sur une carte grand public (limite NVIDIA). Les GTX 10xx (Pascal) nécessitent le pilote de la **branche 580** : les versions 590 et suivantes ne les prennent plus en charge. |
| **Intel / AMD (VAAPI)** | Intel avec graphique intégré ou Arc, AMD Radeon | Périphérique `/dev/dri/renderD128` (sélectionnable s'il y en a plusieurs). |

**Tester et enregistrer** effectue un véritable essai d'encodage ; le moteur n'est enregistré qu'en cas de réussite. Si la carte refuse un fichier pendant une lecture, le processeur prend le relais sans interrompre la lecture. La *Vue d'ensemble* indique les lectures prises en charge par la carte.

La carte ne sert qu'au transcodage : remux et lecture directe ne l'utilisent pas. Le redimensionnement et l'incrustation des sous-titres image restent faits par le processeur.

**Carte NVIDIA dans un conteneur LXC (Proxmox)**

1. Sur l'hôte : installer les en-têtes du noyau (`apt install proxmox-default-headers build-essential`), puis le pilote NVIDIA (`NVIDIA-Linux-x86_64-580.xx.run`, branche 580 pour une GTX 10xx). Accepter la désactivation de `nouveau`, puis redémarrer. `nvidia-smi` doit afficher la carte.
2. Toujours sur l'hôte, pour que `/dev/nvidia-uvm` existe dès le démarrage :
   ```bash
   echo -e "nvidia\nnvidia_uvm" > /etc/modules-load.d/nvidia.conf
   echo 'KERNEL=="nvidia_uvm", RUN+="/usr/bin/nvidia-modprobe -c0 -u"' > /etc/udev/rules.d/70-nvidia.rules
   ```
   Redémarrer, puis relever les numéros majeurs avec `ls -l /dev/nvidia*` (195 pour `nvidia0`/`nvidiactl`, un autre pour `nvidia-uvm`).
3. Dans `/etc/pve/lxc/<id>.conf` (conteneur arrêté), en remplaçant `5xx` par le numéro de `nvidia-uvm` :
   ```
   lxc.cgroup2.devices.allow: c 195:* rwm
   lxc.cgroup2.devices.allow: c 5xx:* rwm
   lxc.mount.entry: /dev/nvidia0 dev/nvidia0 none bind,optional,create=file
   lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
   lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
   lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file
   lxc.mount.entry: /dev/nvidia-modeset dev/nvidia-modeset none bind,optional,create=file
   ```
4. Dans le conteneur : installer **exactement la même version** du pilote, sans module noyau : `sh NVIDIA-Linux-x86_64-580.xx.run --no-kernel-module`. `nvidia-smi` doit afficher la carte.
5. Relancer l'installation de Nexis : elle détecte la carte et donne au service l'accès aux périphériques. Choisir ensuite **NVIDIA NVENC** dans *Paramètres → Transcodage*, puis **Tester et enregistrer**.

Pour Intel ou AMD, le principe est le même avec `/dev/dri` : `lxc.cgroup2.devices.allow: c 226:* rwm` et `lxc.mount.entry: /dev/dri dev/dri none bind,optional,create=dir`, puis `apt install intel-media-va-driver-non-free` (Intel) ou `mesa-va-drivers` (AMD) dans le conteneur.

## Installation

Sur un conteneur LXC ou une VM **Ubuntu 22.04/24.04** ou **Debian 12/13**, en root :

```bash
curl -fsSL https://raw.githubusercontent.com/xenix13/Nexis/main/deploy/install.sh | bash
```

Le script installe Python, git, ffmpeg et les outils SMB (cifs-utils, smbclient), installe Nexis dans `/opt/seance`, crée l'utilisateur système `seance` et le service `seance.service`, puis affiche l'adresse et le **code d'installation**.

### Mises à jour

Depuis l'interface : *Paramètres → Serveur → Mise à jour*. Nexis vérifie la présence d'une nouvelle version toutes les 6 heures ; le bouton **Rechercher une mise à jour** force la vérification. Lorsqu'une version est disponible, une icône apparaît dans la barre du haut pour les administrateurs.

La mise à jour exécute la même installation que la commande manuelle, depuis le dépôt et la branche d'origine (enregistrés dans `/etc/seance/source`), puis redémarre le service (une à deux minutes ; les lectures en cours sont interrompues). Une barre de progression suit les étapes et la page se recharge automatiquement à la fin. Le journal est conservé dans `/var/log/seance-update.log`.

Mise à jour manuelle : relancer la commande d'installation. Les données (`/var/lib/seance`) et la configuration (`/etc/seance/seance.env`) sont conservées.

**Canal de mise à jour** : *Stable* (branche `main`, versions validées) ou *Test* (branche `test`, nouveautés en cours de validation), au choix sur la même page. Les nouveautés sont publiées d'abord sur *Test*, puis sur *Stable* une fois validées. Repasser sur *Stable* puis réinstaller ramène le serveur à la dernière version stable. À l'installation : `curl -fsSL https://raw.githubusercontent.com/xenix13/Nexis/test/deploy/install.sh | SEANCE_BRANCH=test bash`.

### Conteneur LXC en une commande (Proxmox)

Sur l'**hôte** Proxmox, en root :

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/xenix13/Nexis/main/deploy/proxmox-lxc.sh)"
```

Le script télécharge le modèle Ubuntu 24.04 et crée le conteneur (4 cœurs, 4 Go de mémoire, 2 Go de swap, 16 Go de disque, DHCP sur `vmbr0`, démarrage automatique), installe Nexis, puis affiche l'adresse et le code d'installation. Le conteneur est **privilégié** par défaut (avec `mount=cifs`) afin que Nexis puisse monter les partages SMB. Les paramètres se règlent par variables, par exemple `CTID=120 MEMORY=6144 STORAGE=local-zfs PRIVILEGED=0 bash -c "$(curl …)"` ; la liste complète figure en tête du script.

### Paquet .deb

Chaque version est publiée avec son paquet sur la page **Releases**. Sur Debian 12/13 ou Ubuntu 22.04/24.04 :

```bash
apt install ./nexis_<version>_all.deb
```

apt installe également ffmpeg, Python et les outils SMB. Le paquet prépare le système comme le script (utilisateur `seance`, `/etc/seance/seance.env`, services, accès à la carte graphique). `apt remove nexis` conserve les données et la configuration, `apt purge nexis` les supprime. Construction du paquet : `deploy/build-deb.sh`.

### Docker

L'image `ghcr.io/xenix13/nexis` (amd64 et arm64) est publiée à chaque version. Exemple complet : [`docker-compose.yml`](docker-compose.yml).

- **Tags** : `latest` pour la dernière version publiée (branche `main`). `test` est reconstruit à chaque mise à jour de la branche `test` : c'est l'image du `docker-compose.yml` de cette branche.

```bash
mkdir -p data && chown 1000:1000 data     # Nexis tourne sous l'uid 1000 dans le conteneur
docker compose up -d
docker compose logs nexis                 # code d'installation du premier lancement
```

- **Médias** : un volume par dossier, en lecture seule (`/mnt/films:/media/films:ro`), puis des bibliothèques sur `/media/...`. Les partages SMB se montent sur l'hôte ; la page *Partages réseau* n'est pas disponible avec Docker.
- **Mise à jour** : `docker compose pull && docker compose up -d`.
- **Transcodage matériel** : `devices: /dev/dri` (Intel/AMD) ou la réservation `nvidia` (avec le NVIDIA Container Toolkit sur l'hôte), en commentaire dans le fichier d'exemple.

### Accès aux médias

**Méthode 1 : montage depuis Nexis (VM ou LXC privilégié).** *Paramètres → Médiathèque → Partages réseau* monte les partages SMB/CIFS sans ligne de commande : saisie de l'adresse du NAS et des identifiants, choix du partage, montage en lecture seule dans `/mnt/seance/<nom>`, puis création de la bibliothèque. Les partages sont remontés au démarrage et toutes les 5 minutes en cas de coupure. Les mots de passe sont stockés dans `/etc/seance/smb/` (lisibles par root uniquement), jamais dans la base de Nexis.

Le montage est assuré par un service root distinct, `seance-mountd`, limité au montage en lecture seule sous `/mnt/seance/` et n'acceptant que les demandes de l'utilisateur `seance`. Nexis lui-même tourne sans privilèges.

> ⚠️ **Linux interdit le montage SMB dans un conteneur LXC non privilégié.** La méthode 1 nécessite un conteneur **privilégié** avec l'option `mount=cifs` :
> - nouveau conteneur : `pct create … --unprivileged 0 --features mount=cifs,nesting=1` ;
> - conteneur existant : *Sauvegarde* → *Restaurer* en décochant « Non privilégié », puis `pct set <ID> -features mount=cifs,nesting=1` et redémarrage.
>
> Un conteneur privilégié est moins isolé de l'hôte : à réserver à un réseau de confiance. Sinon, utiliser la méthode 2.

**Méthode 2 : montage sur l'hôte Proxmox (LXC non privilégié).** Les partages sont montés sur l'hôte, puis transmis au conteneur en lecture seule.

1. Sur l'hôte, créer un fichier d'identifiants :

   ```bash
   cat > /root/.smb-nas <<'EOF'
   username=media
   password=MOT_DE_PASSE
   EOF
   chmod 600 /root/.smb-nas
   mkdir -p /mnt/nas/films /mnt/nas/series /mnt/nas/animes
   ```

2. Ajouter dans `/etc/fstab` (le `file_mode` permet à l'utilisateur du conteneur de lire) :

   ```
   //192.168.1.5/Films  /mnt/nas/films  cifs credentials=/root/.smb-nas,ro,file_mode=0444,dir_mode=0555,vers=3.0,_netdev,nofail,x-systemd.automount 0 0
   //192.168.1.5/Series /mnt/nas/series cifs credentials=/root/.smb-nas,ro,file_mode=0444,dir_mode=0555,vers=3.0,_netdev,nofail,x-systemd.automount 0 0
   //192.168.1.5/Animes /mnt/nas/animes cifs credentials=/root/.smb-nas,ro,file_mode=0444,dir_mode=0555,vers=3.0,_netdev,nofail,x-systemd.automount 0 0
   ```

   ```bash
   apt install -y cifs-utils && systemctl daemon-reload && mount -a && ls /mnt/nas/films
   ```

3. Transmettre les dossiers au conteneur (remplacer `120` par son identifiant), puis le redémarrer :

   ```bash
   pct set 120 -mp0 /mnt/nas/films,mp=/mnt/films,ro=1
   pct set 120 -mp1 /mnt/nas/series,mp=/mnt/series,ro=1
   pct set 120 -mp2 /mnt/nas/animes,mp=/mnt/animes,ro=1
   pct reboot 120
   ```

Les médias sont alors accessibles dans `/mnt/films`, `/mnt/series` et `/mnt/animes`. Le script d'installation signale si l'utilisateur `seance` ne peut pas les lire. Sur une VM, la méthode 1 fonctionne directement.

**Ressources conseillées** : 2 à 4 cœurs (seul le transcodage est gourmand), 1 Go de mémoire, 8 Go de disque (cache d'images et segments vidéo temporaires). Prévoir environ 2 Go de mémoire par transcodage 4K simultané.

### Création manuelle du conteneur

```bash
pveam update && pveam available | grep ubuntu-24.04
pveam download local ubuntu-24.04-standard_24.04-2_amd64.tar.zst
pct create 120 local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst \
  --hostname nexis --cores 4 --memory 1024 --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp --unprivileged 1 --features nesting=1 --onboot 1
pct start 120
pct exec 120 -- bash -c "apt-get update && apt-get install -y curl && curl -fsSL https://raw.githubusercontent.com/xenix13/Nexis/main/deploy/install.sh | bash"
```

`nesting=1` permet au service de conserver son bac à sable systemd ; sans cette option, le script installe une version allégée du service.

## Premier lancement

1. Ouvrir `http://IP:8420`, saisir le **code d'installation** (`cat /var/lib/seance/setup-code.txt`) et créer le compte administrateur.
2. *Paramètres → Médiathèque → Métadonnées* : renseigner la **clé TMDB**, obtenue gratuitement sur [themoviedb.org](https://www.themoviedb.org/) (Paramètres → API ; clé v3 ou jeton de lecture v4). Sans clé, Nexis utilise les noms de fichiers, les `.nfo` et les images locales.
3. *Paramètres → Médiathèque → Bibliothèques → + Ajouter*, par exemple :
   - « Films », type *Films*, dossier `/mnt/films`
   - « Séries », type *Séries*, dossier `/mnt/series`
   - « Animes », type *Séries*, dossier `/mnt/animes`

   Le premier scan analyse chaque fichier avec ffprobe : quelques minutes pour quelques milliers de fichiers sur un partage réseau. Les fichiers non reconnus sont listés.
4. *Paramètres → Serveur → Utilisateurs* : créer les comptes et définir leurs bibliothèques.

Un titre mal identifié se corrige depuis sa fiche avec **Identifier…** ; le choix est conservé aux scans suivants.

### Nommage des fichiers

```
/mnt/films/Inception (2010)/Inception (2010).mkv
/mnt/series/Breaking Bad/Season 01/Breaking Bad - S01E01.mkv
/mnt/animes/Frieren/Saison 1/[SubsPlease] Sousou no Frieren - 01 (1080p).mkv
```

Une série correspond à un dossier à la racine de la bibliothèque. Le numéro de saison provient du dossier `Season XX` / `Saison XX` ou du `SxxEyy` du nom de fichier.

**Fichiers `.strm`** (vidéo hébergée ailleurs : VOD IPTV, autre serveur, stockage en ligne) : fichier texte contenant l'adresse `http(s)://…` de la vidéo, nommé et rangé comme une vidéo (`Inception (2010)/Inception (2010).strm`). Il est identifié sur TMDB ; le `.nfo`, les images et les `.srt` placés à côté sont pris en compte. Nexis relaie toujours le flux (remux ou transcodage), avec reconnexion en cas de coupure. Pour limiter les connexions vers le fournisseur, les analyses qui liraient toute la vidéo (aperçus, images de chapitres, détection d'intro, sous-titres internes) ne sont pas effectuées sur ces fichiers. Seules les adresses http(s) sont acceptées.

## Détection des nouveautés

Sur un partage SMB, les ajouts faits depuis une autre machine ne sont pas signalés au système. Nexis compare donc régulièrement les dates de modification des **dossiers** de chaque bibliothèque. Après un changement, il attend la fin de la copie et n'analyse **que les dossiers modifiés** : un nouvel épisode ne déclenche que la lecture du dossier de sa saison. Les fichiers connus et inchangés ne sont pas réanalysés. Un fichier modifié depuis moins de 45 secondes est considéré comme en cours de copie et repris au passage suivant. Fréquence : *Paramètres → Médiathèque → Ajout des nouveautés* (par défaut, toutes les minutes).

**Ajout immédiat depuis Radarr / Sonarr** : copier l'adresse du webhook affichée sur cette même page, puis dans Radarr et Sonarr, *Settings → Connect → + → Webhook* : coller l'adresse dans « URL », méthode POST, et cocher *On Import*, *On Upgrade*, *On Rename* et *On Delete*. Le bouton *Test* doit répondre « Nexis a bien reçu le test ». Nexis fait correspondre le dossier indiqué par Radarr avec le sien, même si les chemins diffèrent (`/movies/…` côté Radarr, `/mnt/films/…` côté Nexis), et n'analyse que celui-ci.

Un scan complet de contrôle a lieu une fois par jour (*Paramètres → Serveur → Tâches planifiées*) ; il ne vérifie que les dates des fichiers.

## Sous-titres

Les sous-titres intégrés aux MKV (SRT, ASS, PGS…) ne peuvent être extraits qu'en lisant tout le fichier, soit plusieurs minutes pour un film de 30 Go sur un NAS. Nexis les **exporte une seule fois** (toutes les pistes et les polices jointes en une lecture) et les conserve dans `/var/lib/seance/cache/subs`. Ce cache suit le **fichier** (taille et date) et non sa fiche : un fichier renommé ou déplacé retrouve ses sous-titres sans nouvelle lecture. Seul un fichier remplacé est réexporté. L'export a lieu :

- **au scan**, pour chaque nouveau fichier, en tâche de fond (à débit réduit pendant les lectures). *Paramètres → Lecture → Sous-titres et son → Tout préparer maintenant* traite toute la médiathèque ;
- **à l'ouverture d'une fiche** (film, épisode, ou épisode à reprendre d'une série), en priorité ;
- pour l'épisode suivant, pendant la lecture du précédent.

Si les sous-titres ne sont pas encore prêts au lancement, la lecture démarre dès qu'ils couvrent les 4 premières minutes ; l'extraction, bien plus rapide que le temps réel, garde de l'avance. Le changement de langue est immédiat, toutes les pistes étant extraites ensemble.

Les vignettes d'aperçu de la barre de lecture ne sont produites qu'en dehors des lectures.

### Sous-titres ASS

Les sous-titres ASS (fansubs, plateformes d'animes) contiennent des panneaux positionnés, des dessins, du karaoké et des polices jointes au MKV. Nexis les fait rendre dans le navigateur par **libass**, compilé en WebAssembly via [JASSUB](https://github.com/ThaUnknown/jassub) (fourni dans `static/vendor/jassub`, licences dans ce dossier). Seules les polices utilisées par la piste sont téléchargées. Sur un navigateur trop ancien, un affichage texte simplifié est utilisé.

### Mise en mémoire tampon

La lecture démarre avec une réserve de vidéo déjà chargée : 5 secondes en lecture directe, 12 en remux, 20 en transcodage (3 à 10 secondes après un saut). En cas de ralentissement du réseau ou du processeur, le lecteur se met en pause le temps de reconstituer une réserve de 15 à 25 secondes. Si cela se répète en transcodage, le passage en 720p est proposé.

## Applications

### Web app

Nexis s'installe comme une application, sans magasin d'applications : icône, fenêtre dédiée sans barre d'adresse, raccourcis (Regarder ensemble, TV en direct, À regarder). Un accès en **HTTPS** est nécessaire (voir « Accès depuis l'extérieur »).

- **Windows, macOS, Linux (Chrome ou Edge)** : menu du profil → **Installer l'application**, ou icône d'installation de la barre d'adresse.
- **Android (Chrome)** : menu du profil → **Installer l'application**.
- **iPhone et iPad (Safari)** : bouton **Partager** → **Sur l'écran d'accueil**.

L'application se met à jour avec le serveur : le code de l'interface est chargé depuis des adresses propres à chaque version, aucun cache (navigateur ou proxy) ne peut resservir une version précédente. Si le serveur est injoignable, l'application s'ouvre sur les téléchargements de l'appareil.

### Téléchargements hors ligne

Films, épisodes ou saisons entières peuvent être téléchargés pour être regardés sans connexion.

- **Télécharger** : bouton ⤓ sur la fiche d'un film ou d'un épisode, ou **Télécharger la saison** (épisodes non vus en priorité). Pour chaque qualité, la fenêtre indique la taille et la nécessité d'une conversion. **Automatique** (par défaut) conserve le fichier tel quel lorsque l'appareil sait le lire : environ 2 s de processeur par minute de vidéo, contre 30 s pour une conversion. 720p et 480p réduisent la taille au prix d'une conversion. La langue et les sous-titres se choisissent au même endroit.
- **Suivi** : menu du profil → **Téléchargements** (préparation, transfert, espace utilisé, suppression). Le transfert se fait par morceaux et reprend après une coupure ou à la réouverture de l'application.
- **Hors ligne** : lecture avec le lecteur habituel (sous-titres, « Passer l'intro », chapitres, épisode suivant téléchargé). La position de lecture est renvoyée au serveur au retour du réseau, sans écraser un visionnage plus récent fait ailleurs.
- **Côté serveur** : Nexis prépare un MP4 (H.264 / AAC). Un MP4 compatible est envoyé tel quel, un MKV H.264 est recopié sans perte, le reste est converti.
  - Une préparation à la fois, 4 cœurs au maximum, priorité processeur et disque la plus basse ; lecture du NAS limitée en copie.
  - Mise en **pause pendant les lectures**. Les conversions utilisent la carte graphique si elle est configurée.
  - Copie supprimée dès sa récupération, ou au bout de 48 h.
  - Activation : *Paramètres → Lecture → Téléchargements*.

**Espace disponible** : il est fixé par le navigateur, pas par l'appareil. Firefox limite chaque site à 10 Go tant que le stockage persistant n'est pas autorisé (bouton **Autoriser plus d'espace** de la page Téléchargements) ; ensuite, jusqu'à la moitié du stockage. Chrome, Edge et Safari autorisent environ 60 % du stockage. Chrome annonçant aux sites un espace fictif, Nexis n'affiche pas d'espace restant.

Les téléchargements sont rangés par profil et restent sur l'appareil ; ils nécessitent un accès HTTPS. Sur iPhone et iPad, l'installation sur l'écran d'accueil est recommandée : Safari efface au bout de quelques semaines les données d'un site simplement visité.

### Télécommande, clavier et manette

L'interface se parcourt entièrement sans souris.

- **Flèches** : élément le plus proche dans la direction ; gauche et droite restent sur la même rangée.
- **OK / Entrée** : ouvrir.
- **Retour** (télécommande, Retour arrière, ou B à la manette) : fermer le menu ouvert ou revenir à la page précédente.
- **Manettes** (Xbox, PlayStation…) : croix ou stick gauche pour se déplacer, A pour valider, B pour revenir, Start pour lecture/pause.
- **Lecteur** : gauche/droite pour reculer ou avancer ; avec une télécommande ou une manette, haut/bas affichent les commandes, puis les flèches passent d'un bouton à l'autre ; Retour masque les commandes puis quitte le lecteur ; les touches multimédia sont prises en charge.

### Application Android TV

Application native pour Android TV, Google TV et boîtiers (Shield, Chromecast avec Google TV). Au premier lancement, elle demande l'adresse du serveur (`https://nexis.mondomaine.fr` ou `192.168.1.20:8420`) et vérifie qu'il s'agit bien de Nexis. On se connecte ensuite (avec le code de double authentification si le compte en a une), puis on choisit son profil.

- **Navigation** : une barre en haut avec **Accueil** (Reprendre, une rangée par bibliothèque, Ma liste si elle n'est pas vide), une entrée par **bibliothèque** (Films, Séries, Animes…), puis **Rechercher**, **TV en direct** (si elle est configurée) et **Paramètres**. Affiches et textes sont agrandis pour la lecture à distance ; la fiche d'un titre ne garde que **Lecture** / **Reprendre**, et pour une série le choix de la saison et de l'épisode.
- **Déplacements à la télécommande** : par rangées, comme sur les applis de TV. Gauche et droite restent dans la rangée (au bout, le curseur ne saute plus dans la rangée voisine) ; haut et bas passent à la rangée voisine et retrouvent la carte qu'on y avait quittée. Sur la fiche d'une série, remonter des épisodes ramène sur la saison ouverte, sans en ouvrir une autre.
- **Lecteur natif (ExoPlayer)** : les MKV, le HEVC et le 10 bits sont lus directement quand la TV sait les décoder ; le son multicanal (Dolby Digital, Dolby Digital+, DTS, TrueHD) est transmis tel quel à l'ampli ou à la barre de son quand la sortie HDMI l'accepte. Le serveur ne convertit que ce que l'appareil ne sait pas lire.
- **Attente au lancement** : tant que l'image n'apparaît pas, le lecteur indique ce qui se passe (lecture directe, flux préparé par le serveur, conversion et sa raison) et depuis combien de secondes. Une lecture directe qui ne démarre pas au bout de 20 s bascule d'elle-même sur un flux préparé par le serveur ; après 60 s, un message propose de réessayer. En cas d'erreur, la cause technique est affichée.
- **Paramètres** : réduits au nécessaire : changer de profil, langue de l'interface, se déconnecter, changer de serveur. Les réglages de lecture (langues audio et sous-titres, qualité, épisode suivant, génériques) suivent le compte : ils se changent depuis le téléphone ou l'ordinateur et valent aussi sur la TV.
- **Télécommande** : flèches et OK pour naviguer, **Retour** pour revenir en arrière. Dans le lecteur : OK lecture/pause, gauche/droite pour reculer ou avancer (plus vite en restant appuyé), haut/bas pour les commandes (audio et sous-titres, épisode suivant). En TV en direct : haut/bas ou CH+/CH- pour changer de chaîne.
- **Installation avec Downloader** :
  1. Installer **Downloader** (AFTVnews) depuis le Play Store.
  2. Autoriser Downloader dans *Paramètres → Applications → Accès spécifiques → Installer des applis inconnues*.
  3. Dans Downloader, saisir l'adresse du serveur suivie de `/tv` (par exemple `nexis.mondomaine.fr/tv`), qui renvoie vers le dernier APK publié.
  4. Installer, puis ouvrir Nexis depuis la liste des applications.
- **Autres moyens** : l'APK `nexis-tv-<version>.apk` est joint à chaque version sur la page **Releases** (transfert par *Send Files to TV*, clé USB ou `adb install`).
- **Mise à jour de l'application** : installer le nouvel APK par-dessus. Cela suppose que chaque version soit signée avec la même clé, fournie par les secrets GitHub `NEXIS_KEYSTORE` (clé en base64) et `NEXIS_KEYSTORE_PASSWORD` (*Settings → Secrets and variables → Actions*). Sans eux, l'APK est signé avec une clé de développement et chaque mise à jour impose une désinstallation.

## Accès depuis l'extérieur (HTTPS)

Placer Nexis derrière un reverse proxy HTTPS (Caddy, nginx, Nginx Proxy Manager…), puis dans `/etc/seance/seance.env` :

```
SEANCE_COOKIE_SECURE=1
SEANCE_TRUST_PROXY=1
```

et `systemctl restart seance`. Exemple Caddy :

```
films.mondomaine.fr {
    reverse_proxy 192.168.1.30:8420
}
```

Avec nginx, ajouter `proxy_read_timeout 120s;` et `proxy_buffering off;` (nécessaire aux mises à jour en direct de « Regarder ensemble »).

## Serveurs liés

Deux serveurs Nexis (ou plus) qui vous appartiennent, par exemple la maison et une résidence secondaire, peuvent être reliés dans *Paramètres → Serveurs liés* :

1. Sur le serveur que l'autre peut joindre, **Inviter un serveur**. Vérifier l'adresse proposée (IP ou nom, suivi du port **8421**), puis copier l'invitation `NEXIS1-…`.
2. Sur l'autre serveur, **Rejoindre un serveur** et coller l'invitation, valable 15 minutes et une seule fois.

**Ce qui est partagé**

- **Comptes.** Ils sont communs : mots de passe, double authentification, profils, PIN, contrôle parental, langue, photo et accès aux bibliothèques. Une modification sur l'un arrive sur l'autre en moins d'une minute ; la plus récente l'emporte et une suppression est définitive. Un identifiant présent sur les deux serveurs au moment du jumelage devient un seul compte, avec les données du serveur qui a créé l'invitation. Les sessions ne sont pas partagées : il faut se connecter une fois sur chaque serveur.
- **Bibliothèques.** Chaque bibliothèque reçue de l'autre serveur se règle dans *Serveurs liés* :
  - **Nouvelle bibliothèque** (par défaut) : elle s'ajoute comme une bibliothèque de plus, sous son nom, avec un petit badge du serveur sur sa page ;
  - **Fusionner avec « … »** : ses titres rejoignent l'une de vos bibliothèques du même type. Un film ou une série présent sur les deux serveurs n'apparaît qu'une fois (même identifiant TMDB, IMDb ou TVDB, sinon même titre et même année), et les saisons et épisodes de chacun sont réunis sous la même série.
  - Fiches et affiches viennent du serveur d'origine, et c'est lui qui scanne ses dossiers et cherche ses métadonnées.
- **Lecture.** Un titre de l'autre serveur se lit depuis celui-ci : l'autre serveur lit le fichier, le convertit si besoin et le transmet par la liaison. Sous-titres, aperçus, chapitres et « Passer l'intro » fonctionnent comme pour un titre local. La position de lecture et « vu » sont renvoyés au serveur d'origine et reviennent dans l'autre sens. Les téléchargements hors ligne ne sont pas encore possibles pour ces titres.
- **Titre présent sur les deux serveurs.** La copie est choisie automatiquement, sans rien demander :
  1. celle qui a la langue audio voulue (choisie pour ce titre ou dans *Lecture*), ou les sous-titres demandés, si une seule l'a ;
  2. sinon, la meilleure qualité (définition, puis débit) ;
  3. à égalité, celle de ce serveur.

  Un saut ou un changement de piste garde la même copie. Si le serveur choisi ne répond pas, l'autre copie prend le relais.
- **Même identifiant des deux côtés.** Avant de lier, *Rejoindre un serveur* affiche les identifiants présents sur les deux serveurs. Pour chacun :
  - **Personnes différentes** (par défaut) : le compte du serveur qui rejoint est renommé, par exemple « lea-maison2 » (nom modifiable). Chacun garde son mot de passe et ses données, et les deux comptes existent ensuite sur les deux serveurs.
  - **Même personne** : les comptes fusionnent avec le mot de passe, la double authentification et le profil du serveur qui a créé l'invitation. Chaque serveur garde l'historique, la progression et les listes du compte.

  Un identifiant créé ensuite sur les deux serveurs avant leur synchronisation n'est jamais fusionné : l'un des comptes est renommé (« nina-2 ») sur les deux serveurs, et c'est signalé dans *Serveurs liés*. Lors d'un nouveau jumelage, les comptes déjà communs aux deux serveurs sont reconnus sans rien demander.
- **Délier un serveur.**
  - Les comptes restent sur les deux serveurs, mais ne sont plus synchronisés.
  - Ses bibliothèques et ses titres disparaissent de ce serveur, y compris ceux fusionnés dans les vôtres ; vos propres titres et leur progression ne bougent pas. Aucun fichier n'est touché.
  - L'autre serveur est prévenu s'il répond, et fait de même de son côté. S'il était éteint, il voit ce serveur comme injoignable : un nouveau jumelage, avec une nouvelle invitation, remplace alors l'ancien lien.
- **Serveur éteint ou injoignable.** L'autre continue de fonctionner normalement : ses bibliothèques, la connexion et la lecture de ses titres. Après une trentaine de secondes sans réponse, les titres que seul le serveur éteint possède sont masqués, et ses bibliothèques non fusionnées aussi. Ils reviennent seuls dès qu'il répond.

**Liaison et réseau**

- **Chiffrement.** Chaque serveur a son propre certificat (clé EC P-256, créée au premier jumelage dans `/var/lib/seance/federation`). L'invitation contient l'empreinte de ce certificat : celui qui rejoint vérifie qu'il parle bien au bon serveur, puis les deux s'échangent un secret présenté à chaque requête. Tout passe en TLS sur le port 8421, sans VPN, nom de domaine ni autorité de certification, même si l'interface reste en HTTP sur le réseau local.
- **Ports.** Un seul des deux serveurs doit être joignable par l'autre. En réseau local, rien à faire. Par Internet, il faut rediriger le port 8421 vers l'un des deux (redirection sur la box, ou reverse proxy en mode TCP/stream, car un proxy HTTPS classique casserait le certificat épinglé). L'autre serveur ouvre lui-même un tunnel chiffré vers lui : rien à ouvrir de son côté. Si aucun des deux n'est joignable, passer par un VPN de type Tailscale ou WireGuard.
- **Restrictions.** Le port n'accepte que les serveurs jumelés et ne sert pas l'interface. Au nom d'un compte, un serveur lié ne peut que lire les titres, jamais administrer.
- **Configuration.** Le port se change avec `SEANCE_FED_PORT` dans `/etc/seance/seance.env`. Avec Docker, publier aussi `8421:8421`. `openssl` est nécessaire, une seule fois, pour créer le certificat ; il est installé par le script, le paquet `.deb` et l'image Docker.
- **Débit.** À distance, la qualité dépend du débit montant du serveur qui a le fichier : choisir une qualité réduite si la lecture saccade.

> À réserver à vos propres serveurs : partager des films du commerce avec un autre foyer sort du cadre de la copie privée (voir [docs/AUDIT-JURIDIQUE.md](docs/AUDIT-JURIDIQUE.md)).

## Chapitres, aperçus, intro et générique

- **Aperçus** : au survol ou au glissement de la barre de lecture, une vignette de la scène s'affiche avec le nom du chapitre. Les vignettes sont produites à partir des images clés, pendant la même lecture du fichier que les sous-titres.
- **Chapitres** : repères sur la barre et sélection en vignettes dans le lecteur (bouton Chapitres ou touche `C`) et sur la fiche. Une image par chapitre, produite à la demande puis par la tâche « Extraire les images des chapitres ».
- **Lecteur réduit** : le bouton en haut à droite du lecteur (ou la touche `I`) le réduit dans un coin de l'écran ; la lecture continue pendant la navigation.
- **Passer l'intro / épisode suivant** : l'opening et le générique de fin sont repérés grâce aux chapitres nommés (`Opening`, `OP`, `Intro`, `Générique`, `Ending`, `ED`, `Credits`…). Un chapitre `OP` / `Opening` est prioritaire sur un « Intro » qui désigne souvent la scène d'avant-générique. Pour les chapitres sans nom, Nexis cherche les blocs de 70 à 115 secondes en début et en fin d'épisode. Un administrateur peut corriger ces repères pendant la lecture (menu ⚙) ; ses corrections sont prioritaires.

**Fichiers sans chapitres** : la tâche « Détecter les intros et génériques » compare l'empreinte audio (Chromaprint, via ffmpeg) du début et de la fin des épisodes d'une même saison ; l'opening et le générique sont les passages communs. Elle s'exécute après chaque ajout d'épisodes et chaque nuit ; la zone analysée (10 premières et 6 dernières minutes par défaut) et les durées minimale et maximale sont réglables. Ordre de priorité : repères manuels, chapitres nommés, empreinte audio, puis chapitres de la durée d'une chanson.

## Tâches planifiées

*Paramètres → Serveur → Tâches planifiées* liste les travaux de fond avec leur dernière exécution et la suivante. Chaque tâche peut être lancée, arrêtée et planifiée : **désactivée**, **toutes les N heures**, **chaque jour** ou **chaque semaine** à heure fixe, avec ses options propres.

| Tâche | Par défaut | Options |
|---|---|---|
| Analyser les bibliothèques | toutes les 24 h | bibliothèques concernées |
| Actualiser les métadonnées | le dimanche à 4 h | fiches incomplètes / non actualisées depuis N jours / toutes ; bibliothèques |
| Extraire les images des chapitres | chaque nuit et après chaque ajout | pause pendant les lectures, bibliothèques |
| Détecter les intros et génériques | chaque nuit et après chaque ajout | zones analysées, durées min. et max., générique de fin, épisodes à chapitres nommés |
| Préparer les sous-titres et les aperçus | chaque nuit | |
| Actualiser les chaînes et le guide TV | toutes les 12 h | vérification des chaînes |
| Nettoyer les caches | chaque nuit | ancienneté des images conservées |

Une seule tâche s'exécute à la fois. Celles qui lisent les fichiers du partage réseau se mettent **en pause pendant les lectures**.

## TV en direct (IPTV)

*Paramètres → Lecture → TV en direct* : ajouter une ou plusieurs **sources**, chacune composée d'une liste **M3U / M3U8** (adresse fournie par le fournisseur IPTV ou la box, ou chemin d'un fichier `.m3u` sur le serveur) et, si nécessaire, d'un **guide XMLTV** (`.xml` ou `.xml.gz` ; facultatif si la liste l'indique dans son en-tête `url-tvg`). Les attributs `tvg-id`, `tvg-name`, `tvg-logo`, `tvg-chno`, `group-title` et `#EXTVLCOPT:http-user-agent` sont pris en charge.

- Les chaînes sont proposées **à tous les utilisateurs** dans l'onglet **TV en direct** : grille avec programme en cours et suivant, **guide des programmes**, recherche, groupes, **favoris** par utilisateur. Dans le lecteur : ↑ / ↓ pour changer de chaîne, `L` pour la liste.
- *Groupes…* masque des groupes entiers pour tous les utilisateurs. Une option masque la TV pour les profils avec restriction d'âge.
- Les navigateurs ne lisent pas le MPEG-TS : Nexis reçoit le flux avec ffmpeg et le convertit en HLS. La vidéo **H.264 est recopiée** telle quelle, le son converti en AAC stéréo ; une vidéo MPEG-2 ou HEVC est convertie en H.264 (réglable : jamais / automatique / toujours). Une chaîne regardée par plusieurs personnes **n'est reçue qu'une fois** ; ffmpeg s'arrête 45 s après le départ du dernier spectateur.
- **Chaînes simultanées (max.)** : la plupart des fournisseurs limitent le nombre de connexions ; au-delà, un message l'indique.
- Les chaînes qui ne répondent pas deux fois de suite sont masquées, puis réaffichées dès qu'elles répondent à nouveau.

## Regarder ensemble

Plusieurs personnes regardent la même vidéo en même temps, chacune sur son appareil, sur place ou à distance.

- Sur la fiche d'un film ou d'un épisode, **Regarder ensemble** crée un groupe (nom, et option « seul l'hôte contrôle la lecture »). Dans le lecteur, le même bouton affiche les membres et leur état, et copie le **lien d'invitation**. Les groupes en cours apparaissent sur l'accueil et dans la barre du haut.
- **Groupe créé à l'avance** : le bouton ＋ de la barre du haut ouvre un **salon** où arrivent les membres. Depuis une fiche, *Regarder ensemble* propose alors de lancer le titre dans ce groupe, et la lecture démarre pour tous.
- Lecture, pause, sauts, chapitres, « Passer l'intro » et **épisode suivant** sont synchronisés. Un membre qui rejoint arrive au même endroit.
- Chacun conserve son propre flux (qualité, langue, sous-titres). Un faible décalage est corrigé en ajustant légèrement la vitesse de lecture, un décalage important par un saut. Si la connexion d'un membre faiblit, le groupe l'attend (12 s au plus).
- Reprise de lecture et historique restent individuels. L'accès au contenu (bibliothèques, restriction d'âge) est vérifié pour chaque membre.

## Profils, code PIN et contrôle parental

- **« Qui regarde ? »** : après la connexion, chacun choisit son profil ; *Changer de profil* (menu du profil) le rouvre à tout moment.
- **Profils gérés** (enfants, invités) : sans mot de passe, accessibles depuis « Qui regarde ? », avec un **code PIN** éventuel.
- **Code PIN** : protège un profil (*Paramètres → Profil*). Sans code, l'accès à un compte complet ou administrateur demande son mot de passe.
- **Photo de profil** : image au choix (JPEG, PNG ou WebP), recadrée en carré, ou avatar de la galerie (Cinéma, Séries et TV, Musique, Personnages, Motifs) avec le fond de son choix. Les métadonnées des photos (EXIF, position) sont supprimées par le serveur.
- **Restriction d'âge** par profil (7, 10, 12 ou 16 ans), d'après la classification TMDB ou `.nfo`. Les contenus au-delà de la limite sont masqués partout : bibliothèques, accueil, recherche, fiches et lecture. Les contenus sans classification peuvent être autorisés ou non.

Configuration : *Paramètres → Serveur → Utilisateurs*.

## Personnalisation

Chaque utilisateur règle son interface dans *Paramètres → Mon compte* :

- **Profil** : nom, photo (image envoyée ou avatar prédéfini), **langue de l'interface** (français ou anglais), code PIN, mot de passe.
- **Lecture** : qualité, langues, enchaînement des épisodes, **apparence des sous-titres** (taille, police, couleur, fond, bordure, position, avec aperçu ; ne s'applique pas aux sous-titres ASS stylés ni aux PGS) et **anti-spoiler** (vignettes floutées et résumés masqués pour les épisodes non vus ; un clic affiche le résumé).
- **Apparence** : thème, couleur d'accent, taille des affiches.
- **Cet appareil** : qualité et taille des sous-titres propres à ce navigateur ou à cette TV, animations réduites, navigation à la télécommande forcée ou désactivée. Ces réglages restent sur l'appareil et priment sur ceux du profil.
- **Accueil et menu** : **page d'ouverture** (accueil, « À regarder », Découvrir, TV en direct ou une bibliothèque), ordre et affichage des rangées et des liens. Une collection, une playlist ou un genre d'une bibliothèque s'**épingle sur l'accueil** avec le bouton *Épingler à l'accueil* de sa page (20 rangées au plus).

**Avis** : sur la fiche d'un film ou d'une série, *J'aime* et *Je n'aime pas* orientent les suggestions (les titres proches d'un titre non aimé reculent) ; *Ne plus suggérer* (menu ⋯) l'exclut des suggestions et des titres similaires.

**Notifications** : la cloche de la barre du haut signale les épisodes ajoutés depuis moins de 30 jours, et pas encore vus, des séries suivies (dont un épisode a été regardé, ou présentes dans « À regarder »).

**Bilan** : la page *Historique* présente, par année ou par mois, le temps de visionnage, le nombre de films et d'épisodes, les jours actifs, la plus longue série de jours consécutifs, le jour préféré, la répartition par mois, les genres et les titres les plus regardés.

**Langue** : chaque utilisateur choisit sa langue dans *Paramètres → Profil* ; elle le suit sur tous ses appareils, appli Android TV comprise. L'administrateur choisit la langue des nouveaux comptes et de la page de connexion dans *Paramètres → Utilisateurs* (à l'installation, c'est la langue choisie sur la page d'installation), et peut régler la langue de chaque compte. La langue des résumés et titres venant de TMDB reste un réglage du serveur (*Paramètres → Métadonnées*).

**Interface enfant** : option du compte (*Paramètres → Utilisateurs*), à combiner avec une restriction d'âge. Grandes vignettes, textes plus gros, menu limité à l'accueil, « À regarder » et aux bibliothèques ; Découvrir, la TV en direct et la création de groupes sont masqués.

## Double authentification

Chaque utilisateur qui se connecte avec un mot de passe peut l'activer dans *Paramètres → Profil* : un code à 6 chiffres, généré par une application d'authentification (Google Authenticator, Microsoft Authenticator, Aegis, 2FAS, Bitwarden…), est alors demandé à chaque connexion. Les profils gérés (sans mot de passe) ne sont pas concernés.

- **Activation** : mot de passe, QR code à scanner (ou clé à saisir), puis un premier code pour confirmer. Les autres appareils connectés à ce compte sont déconnectés.
- **Codes de secours** : 10 codes à usage unique, affichés une seule fois à l'activation (à copier, télécharger ou imprimer). Ils remplacent le téléphone en cas de perte ; de nouveaux codes se génèrent depuis la même page.
- **Changement de profil** : passer sur un compte protégé demande aussi son code.
- **Téléphone perdu** : un administrateur la réinitialise depuis la fiche de l'utilisateur (*Paramètres → Utilisateurs → Modifier → Réinitialiser*). L'utilisateur se connecte alors avec son mot de passe et peut la réactiver.
- **Administrateur sans accès** : en dernier recours, sur le serveur, en root :
  ```bash
  nexis list-users              # comptes et état de leur double authentification
  nexis reset-2fa <identifiant> # désactive la double authentification de ce compte
  ```
  Avec Docker : `docker compose exec nexis python3 -m seance reset-2fa <identifiant>`.
- La *Vue d'ensemble* indique combien d'administrateurs l'ont activée.

Les codes suivent la norme TOTP (RFC 6238) ; un code déjà utilisé est refusé, et les tentatives erronées sont limitées comme pour le mot de passe.

## Collections, « À regarder » et playlists

- **Collections** : lorsqu'un film appartient à une saga selon TMDB, la collection est créée automatiquement (affiche, fond et résumé compris). Elle apparaît dans l'onglet **Collections** de la bibliothèque et sur la fiche des films concernés. Un administrateur peut créer, renommer ou supprimer ses propres collections (menu ⋯ → *Collections…*).
- **À regarder** : le bouton signet ajoute un film ou une série à la liste personnelle (onglet *À regarder* et rangée de l'accueil). Un film marqué comme vu en est retiré.
- **Playlists** : menu ⋯ → *Ajouter à une playlist…* sur un film, un épisode, une saison ou une série. *Tout lire* enchaîne les éléments dans l'ordre défini. Chaque utilisateur a ses propres playlists.

### Bonus et bandes-annonces

Les bonus sont reconnus à côté du film ou dans le dossier de la série :

| Dossier | Ou suffixe du fichier | Type |
|---|---|---|
| `Trailers/`, `Bandes-annonces/` | `-trailer`, `-bande-annonce` | Bande-annonce |
| `Featurettes/` | `-featurette` | Featurette |
| `Behind The Scenes/`, `Making of/` | `-behindthescenes` | Coulisses |
| `Deleted Scenes/`, `Scènes coupées/` | `-deleted` | Scène coupée |
| `Interviews/` | `-interview` | Interview |
| `Scenes/` | `-scene` | Scène |
| `Shorts/` | `-short` | Court métrage |
| `Extras/`, `Bonus/`, `Other/` | `-other` | Bonus |

Exemple : `Films/Inception (2010)/Trailers/Bande-annonce VF.mkv` ou `Films/Inception (2010)/Inception (2010)-trailer.mp4`. Les bonus ne sont jamais comptés comme des films ou des épisodes. Sans bande-annonce locale, celle de TMDB est lue depuis YouTube (mode sans cookies).

## Découvrir et demander

L'onglet **Découvrir** présente les catalogues des plateformes de streaming disponibles en France (Netflix, Prime Video, Disney+, Canal+, Max, Apple TV+, Paramount+, Crunchyroll, ADN), les tendances de la semaine et une recherche. Chaque titre indique s'il est **déjà sur le serveur**, **demandé** ou **en cours de téléchargement**. Une clé TMDB est nécessaire.

Le bouton **Demander** transmet la demande à [Seerr](https://docs.seerr.dev), qui la relaie à Radarr / Sonarr ; pour une série, les saisons se choisissent. Un titre absent ajouté à **À regarder** est demandé automatiquement (désactivable) et rejoint la liste dès son arrivée sur le serveur.

### Configuration de Seerr

1. Installer Seerr ([documentation](https://docs.seerr.dev)) et le relier à Radarr et Sonarr.
2. Dans Seerr, *Settings → General* : copier la **clé d'API**.
3. Dans Nexis, *Paramètres → Médiathèque → Demandes* : saisir l'adresse de Seerr (ex. `http://192.168.1.20:5055`) et la clé, puis *Enregistrer et tester*.

Lorsqu'un compte Seerr correspond à un compte Nexis (identifiant, nom affiché ou début de l'adresse e-mail), la demande est faite **à son nom** et les règles de Seerr s'appliquent (validation, quotas). Sinon, elle est faite au nom du propriétaire de la clé d'API.

## Langue audio et sous-titres

Sur la fiche d'un film, d'un épisode ou d'une série, l'audio et les sous-titres peuvent être choisis avant la lecture. Le choix est **mémorisé pour toute la série** (ou le film) et pour chaque utilisateur ; un changement fait dans le lecteur est mémorisé de la même façon.

**Sous-titres « forcés »** (« FR Forced », « Signs & Songs »…) : ils ne traduisent que les panneaux, inscriptions et chansons. Nexis ne les propose que si l'audio est dans leur langue, et les sélectionne alors automatiquement ; avec un autre audio, la piste complète est utilisée.

Les pistes 5.1 / 7.1 (AC3, E-AC3, DTS, TrueHD, AAC 5.1…) sont mixées en stéréo selon le réglage de *Paramètres → Lecture → Sous-titres et son* (ffmpeg, Dave750, Dialogues (nuit), RFC 7845 ou AC-4), suivi d'un gain réglable. Un limiteur empêche toute saturation.

## Sécurité

L'audit, les correctifs et les risques acceptés sont décrits dans [docs/AUDIT-SECURITE.md](docs/AUDIT-SECURITE.md) (version 3.1.0) et [docs/AUDIT-SECURITE-2026-10.md](docs/AUDIT-SECURITE-2026-10.md) (version 3.19.0 : mise à jour, bibliothèques en lecture seule, composants tiers). Les questions juridiques (licences, marque, TMDB, droit d'auteur, RGPD) sont traitées dans [docs/AUDIT-JURIDIQUE.md](docs/AUDIT-JURIDIQUE.md). Monter les dossiers de médias **en lecture seule** : Nexis n'a besoin que de les lire, et signale dans *Paramètres → Bibliothèques* un dossier où il pourrait écrire. Recommandations principales : n'exposer Nexis sur Internet que derrière un reverse proxy HTTPS (`SEANCE_COOKIE_SECURE=1`, `SEANCE_TRUST_PROXY=1`), et n'ajouter que des listes IPTV de fournisseurs de confiance.

## Dépannage

*Paramètres → Serveur → Vue d'ensemble* affiche l'état du serveur (mémoire, swap, charge) et, pour chaque lecture, le mode (direct, remux, transcodage), sa raison, la **vitesse de conversion** (en dessous de ×1, le processeur ne suit pas) et les derniers messages de ffmpeg. *Informations sur le fichier* (menu ⋯ d'un film ou d'un épisode) liste les pistes du fichier.

| Symptôme | Piste |
|---|---|
| « Linux interdit le montage SMB… » | Conteneur LXC non privilégié : voir « Accès aux médias » |
| « Montage refusé (identifiants valides) » | Conteneur privilégié sans l'option : `pct set <ID> -features mount=cifs,nesting=1`, puis redémarrer |
| Partage « Non monté » | Bouton *Remonter* ; journal : `journalctl -u seance-mountd -n 50` |
| « Dossier inaccessible » au scan | Montage non visible dans le conteneur : `ls /mnt/films` puis `runuser -u seance -- ls /mnt/films` |
| Film mal reconnu | Fiche → Identifier…, ou renommer en `Titre (Année)` |
| Épisodes « non reconnus » | Ajouter `S01E02` ou ` - 02` au nom |
| « Trop de transcodages simultanés » | Augmenter la limite si le processeur le permet, ou utiliser une source H.264 |
| Lecture qui ne démarre pas | `journalctl -u seance -f` pendant l'essai ; `SEANCE_DEBUG=1` dans `/etc/seance/seance.env` affiche les commandes ffmpeg |
| Saccades, swap saturé (LXC) | Un transcodage 4K occupe 0,5 à 0,7 Go : prévoir 2 Go de mémoire par transcodage simultané et 1 à 2 Go de swap |
| Ancienne interface après une mise à jour | Recharger la page ; depuis la 3.14.1, le code de l'interface change d'adresse à chaque version |
| Double authentification perdue (administrateur) | `sudo nexis reset-2fa <identifiant>` sur le serveur |
| Code d'installation perdu | `cat /var/lib/seance/setup-code.txt` (tant qu'aucun compte n'existe) |

Réinitialisation d'un mot de passe en ligne de commande :

```bash
cd /opt/seance && sudo -u seance python3 -c "
from seance.db import DB; from seance.auth import hash_password
db = DB('/var/lib/seance/seance.db')
db.run('UPDATE users SET password_hash=? WHERE username=?', (hash_password('NouveauMotDePasse'), 'admin'))
db.run('DELETE FROM sessions')"
```

## Développement

```bash
python3 -m seance --data ./data --port 8420        # serveur local
python3 -m unittest discover -s tests -t .          # tests : fichiers générés par ffmpeg et TMDB simulé
python3 tests/ui_smoke.py ./captures video.webm     # parcours navigateur (Playwright) avec captures
python3 tests/ui_features.py ./captures video.webm  # chapitres, tâches, TV en direct, regarder ensemble, paramètres
python3 tests/hls_real.py chemin/vers/hls.min.js     # lecture réelle hls.js (VP9/Opus) : reprise, sauts, PGS
```

Organisation de `seance/` :
- `scanner.py`, `names.py` : scan et reconnaissance des noms de fichiers
- `metadata.py` : TMDB et `.nfo` ; `images.py` : images
- `transcoder.py` : décision de lecture, ffmpeg et HLS
- `mountd.py` : service root de montage des partages SMB
- `watcher.py` : détection des nouveautés ; `subcache.py` : préparation des sous-titres
- `tasks.py` : tâches planifiées ; `chapters.py` : images de chapitres ; `introdetect.py` : intros par empreinte audio
- `livetv.py` : TV en direct ; `syncplay.py` : « Regarder ensemble »
- `update.py` : mise à jour depuis l'interface ; `downloads.py` : téléchargements hors ligne ; `suggest.py` : suggestions et avis ; `notify.py` : notifications et bilan
- `api.py` : assemblage du serveur ; `catalog.py` : outils partagés par les routes ; `routes/` : routes HTTP par thème
- `twofa.py` : double authentification ; `auth.py`, `db.py`, `web.py` : socle
- `i18n.py`, `lang_en.py` : langue des messages du serveur

L'interface (`static/`) est en JavaScript, sans étape de compilation.

**Traductions.** Le texte français du code sert de clé : `tr('Paramètres')` dans l'interface (`static/js/i18n.js`, traductions dans `static/js/i18n/en.js`), `tr("Introuvable")` côté serveur (`seance/i18n.py`, traductions dans `seance/lang_en.py`, où `{}` remplace une partie variable), `Tr.t("…")` dans l'appli TV (`Tr.java`). `plural(n, '{n} film', '{n} films')` choisit la forme selon la langue ; un même mot à traduire de deux façons prend un contexte : `tr('bouton|Lecture')`. `tests/test_i18n.py` vérifie que chaque texte de l'interface et chaque message d'erreur du serveur a sa traduction. Ajouter une langue : un fichier `static/js/i18n/<code>.js`, un `seance/lang_<code>.py`, et le code dans `LANGUAGES` (`i18n.js`, `i18n.py`).

## Feuille de route

### À faire
- [ ] « Regarder ensemble » : seconde passe d'optimisation après les tests réels

### Réalisé
- Interface multi-utilisateurs, profils, code PIN, contrôle parental, photos et galerie d'avatars, interface enfant
- Double authentification (TOTP) avec codes de secours, réinitialisation par l'administrateur et en console
- Personnalisation : apparence des sous-titres, anti-spoiler, page d'ouverture, rangées épinglées, réglages par appareil, avis sur les titres, notifications de nouveaux épisodes, bilan de visionnage
- Scan, TMDB, lecture directe / remux / transcodage, transcodage matériel (NVENC, VAAPI)
- Détection des intros et génériques par empreinte audio
- Collections, « À regarder », playlists, bonus et bandes-annonces, suggestions
- Découvrir et demandes via Seerr, TV en direct, Regarder ensemble
- Fichiers `.strm`
- Paquet `.deb`, image Docker, conteneur Proxmox en une commande, mise à jour depuis l'interface
- Paramètres regroupés en un seul espace avec recherche
- Interface en français et en anglais (langue par utilisateur, langue par défaut choisie par l'administrateur)
- Serveurs liés : jumelage par invitation, liaison TLS à certificats épinglés, tunnel inverse, comptes répliqués, bibliothèques partagées ou fusionnées (un titre une seule fois, meilleure copie choisie automatiquement), lecture relayée
- Web app installable, téléchargements hors ligne, navigation à la télécommande et à la manette, application Android TV native (lecture directe des MKV, son multicanal transmis à l'ampli)
