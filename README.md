# Séance

Un serveur multimédia **100 % auto-hébergé** pour tes films, séries et animes : il scanne tes dossiers, récupère affiches et résumés, et diffuse dans le navigateur avec une interface multi-utilisateurs. Aucun compte externe, aucun abonnement, pas besoin de Plex ni de Jellyfin.

- **Zéro dépendance Python** : Python 3 (bibliothèque standard) + **ffmpeg**. Le lecteur [hls.js](https://github.com/video-dev/hls.js) est téléchargé à l'installation.
- Pensé pour un **conteneur LXC** ou une VM Ubuntu/Debian, **sans GPU** : Séance évite le transcodage dès qu'il peut.

## Fonctionnalités

| | |
|---|---|
| **Bibliothèques** | Autant que tu veux (Films, Séries, Animes…), chacune avec un ou plusieurs dossiers. Scan incrémental automatique ou manuel. Un partage non monté ne vide jamais la bibliothèque |
| **Reconnaissance** | `Titre (Année)/…`, noms « scène » (`Inception.2010.1080p.BluRay…`), `S01E02`, `1x02`, `Saison 1/…`, animes `[Groupe] Titre - 12 [1080p]`, numérotation absolue (`Episode 1071`), spéciaux (`Season 00`, `Specials`) |
| **Métadonnées** | TMDB (affiches, fonds, résumés, genres, distribution, titres et images d'épisodes, classification) en français avec repli anglais. Lit aussi les `.nfo` et `poster.jpg` / `fanart.jpg` déjà présents (ceux de Jellyfin/Kodi, par exemple). Correction manuelle avec « Identifier… » |
| **Comptes** | Comptes propres à Séance, administrateurs, accès par bibliothèque, désactivation |
| **Espace personnel** | Reprise de lecture, « Reprendre et à suivre », historique, épisodes vus/non vus, langues audio/sous-titres préférées, thème, couleur, taille des affiches |
| **Lecteur** | Choix audio, sous-titres et qualité en cours de lecture, raccourcis clavier, bandeau « Passer l'intro », épisode suivant avec compte à rebours |
| **Sous-titres** | SRT, ASS/SSA, WebVTT, mov_text (internes ou fichiers à côté de la vidéo, `Film.fr.srt`, `Film.forced.srt`…) affichés par le navigateur, **sans transcodage**. PGS/VobSub incrustés |
| **Administration** | Bibliothèques (avec explorateur de dossiers), utilisateurs, clé TMDB, limites de lectures/transcodages, lectures en cours avec la raison d'un éventuel transcodage |

### Comment Séance ménage le processeur

Pour chaque lecture, Séance choisit le mode le plus léger :

| Mode | Quand | Coût CPU |
|---|---|---|
| **Lecture directe** | MP4 en H.264 + AAC | nul : le navigateur lit le fichier |
| **Remux** | Vidéo H.264 8 bits dans un MKV, ou piste audio non lisible par le navigateur (AC3, DTS, FLAC…) | très faible : la vidéo est recopiée, seul l'audio est converti |
| **Transcodage** | HEVC/x265, H.264 10 bits (fréquent en anime), sous-titres image, qualité réduite choisie | 1 à 2 cœurs par lecture en 1080p |

Le menu ⚙ du lecteur indique le mode en cours et pourquoi. ffmpeg est mis en pause quand il a pris 3 minutes d'avance, et tourne avec une priorité réduite. Un nombre maximal de transcodages simultanés protège le serveur (Administration → Métadonnées et lecture).

## Installation

Sur un conteneur LXC ou une VM **Ubuntu 22.04/24.04** ou **Debian 12/13**, en root :

```bash
curl -fsSL https://raw.githubusercontent.com/xenix13/Nexis/main/deploy/install.sh | bash
```

Le script installe Python, git et ffmpeg, récupère le code, l'installe dans `/opt/seance`, crée l'utilisateur système `seance` et le service `seance.service`, puis affiche l'adresse et le **code d'installation**.

**Mettre à jour** : relance la même commande. Les données (`/var/lib/seance`) et la configuration (`/etc/seance/seance.env`) sont conservées.

### Donner accès aux médias dans un LXC (Proxmox)

Un conteneur non privilégié ne peut pas monter de partage SMB lui-même. On monte donc les partages **sur l'hôte Proxmox**, puis on les « prête » au conteneur, en lecture seule.

1. **Sur l'hôte Proxmox**, crée un fichier d'identifiants :

   ```bash
   cat > /root/.smb-nas <<'EOF'
   username=jellyfin
   password=TON_MOT_DE_PASSE
   EOF
   chmod 600 /root/.smb-nas
   mkdir -p /mnt/nas/films /mnt/nas/series /mnt/nas/animes
   ```

2. **Toujours sur l'hôte**, ajoute dans `/etc/fstab` (le `file_mode` permet à l'utilisateur du conteneur de lire) :

   ```
   //192.168.1.5/Films  /mnt/nas/films  cifs credentials=/root/.smb-nas,ro,file_mode=0444,dir_mode=0555,vers=3.0,_netdev,nofail,x-systemd.automount 0 0
   //192.168.1.5/Series /mnt/nas/series cifs credentials=/root/.smb-nas,ro,file_mode=0444,dir_mode=0555,vers=3.0,_netdev,nofail,x-systemd.automount 0 0
   //192.168.1.5/Animes /mnt/nas/animes cifs credentials=/root/.smb-nas,ro,file_mode=0444,dir_mode=0555,vers=3.0,_netdev,nofail,x-systemd.automount 0 0
   ```

   ```bash
   apt install -y cifs-utils && systemctl daemon-reload && mount -a && ls /mnt/nas/films
   ```

3. **Prête les dossiers au conteneur** (remplace `120` par l'ID de ton LXC), puis redémarre-le :

   ```bash
   pct set 120 -mp0 /mnt/nas/films,mp=/mnt/films,ro=1
   pct set 120 -mp1 /mnt/nas/series,mp=/mnt/series,ro=1
   pct set 120 -mp2 /mnt/nas/animes,mp=/mnt/animes,ro=1
   pct reboot 120
   ```

Dans le conteneur, tes médias sont alors dans `/mnt/films`, `/mnt/series` et `/mnt/animes`. Le script d'installation te prévient si l'utilisateur `seance` ne peut pas les lire.

Pour une VM, monte simplement les partages dans son `/etc/fstab` (lecture seule suffit).

**Ressources conseillées** : 2 à 4 cœurs (le transcodage est le seul poste gourmand), 1 Go de RAM, 8 Go de disque (cache d'images et segments vidéo temporaires).

### Créer le conteneur

```bash
pveam update && pveam available | grep ubuntu-24.04
pveam download local ubuntu-24.04-standard_24.04-2_amd64.tar.zst
pct create 120 local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst \
  --hostname seance --cores 4 --memory 1024 --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp --unprivileged 1 --features nesting=1 --onboot 1
pct start 120
pct exec 120 -- bash -c "apt-get update && apt-get install -y curl && curl -fsSL https://raw.githubusercontent.com/xenix13/Nexis/main/deploy/install.sh | bash"
```

`nesting=1` permet au service de garder son bac à sable systemd ; sans lui, le script bascule sur une version allégée.

## Premier lancement

1. Ouvre `http://IP:8420`, saisis le **code d'installation** (`cat /var/lib/seance/setup-code.txt`) et crée ton compte administrateur.
2. **Administration → Métadonnées et lecture** : colle ta **clé TMDB**. Crée un compte gratuit sur [themoviedb.org](https://www.themoviedb.org/), puis Paramètres → API. La clé v3 ou le jeton de lecture v4 fonctionnent tous les deux. Sans clé, Séance se contente des noms de fichiers, des `.nfo` et des images locales.
3. **Administration → Bibliothèques → + Ajouter** :
   - « Films », type *Films*, dossier `/mnt/films`
   - « Séries », type *Séries*, dossier `/mnt/series`
   - « Animes », type *Séries*, dossier `/mnt/animes`

   Le premier scan analyse chaque fichier avec ffprobe. Compte quelques minutes pour quelques milliers de fichiers sur un partage réseau. Les fichiers non reconnus sont listés pour que tu puisses les renommer.
4. **Administration → Utilisateurs** : crée les comptes et choisis leurs bibliothèques.

Si un film ou une série est mal identifié, ouvre sa fiche puis **Identifier…** et choisis le bon résultat. Ton choix est conservé aux scans suivants.

### Bien nommer ses fichiers

```
/mnt/films/Inception (2010)/Inception (2010).mkv
/mnt/series/Breaking Bad/Season 01/Breaking Bad - S01E01.mkv
/mnt/animes/Frieren/Saison 1/[SubsPlease] Sousou no Frieren - 01 (1080p).mkv
```

Une série = un dossier sous la racine de la bibliothèque. Les numéros de saison viennent du dossier `Season XX` / `Saison XX` ou du `SxxEyy` du nom de fichier.

## Accès depuis l'extérieur (HTTPS)

Place Séance derrière un reverse proxy HTTPS (Caddy, nginx, Nginx Proxy Manager…). Dans `/etc/seance/seance.env` :

```
SEANCE_COOKIE_SECURE=1
SEANCE_TRUST_PROXY=1
```

puis `systemctl restart seance`. Exemple Caddy :

```
films.mondomaine.fr {
    reverse_proxy 192.168.1.30:8420
}
```

Pour nginx, ajoute `proxy_read_timeout 120s;` et `proxy_buffering off;`.

## Passer l'intro

La bannière « Passer l'intro » s'appuie sur des marqueurs de début et de fin d'intro. Pour l'instant, un administrateur les pose pendant la lecture, via le menu ⚙ (*Début de l'intro ici* / *Fin de l'intro ici*). La détection automatique (empreintes audio comparées entre les épisodes d'une saison) est la prochaine étape.

## Dépannage

| Symptôme | Piste |
|---|---|
| « Dossier inaccessible » au scan | Le montage n'est pas visible dans le conteneur : `ls /mnt/films` dans le LXC, et `runuser -u seance -- ls /mnt/films` |
| Film mal reconnu | Fiche → Identifier… ; ou renommer en `Titre (Année)` |
| Épisodes « non reconnus » | Ajouter `S01E02` ou ` - 02` dans le nom |
| « Trop de transcodages simultanés » | Le processeur est déjà pris : augmente la limite si ton CPU le permet, ou choisis une source H.264 |
| Lecture qui ne démarre pas | `journalctl -u seance -f` pendant l'essai ; `SEANCE_DEBUG=1` dans `/etc/seance/seance.env` affiche les commandes ffmpeg |
| Code d'installation perdu | `cat /var/lib/seance/setup-code.txt` (tant qu'aucun compte n'existe) |

Réinitialiser un mot de passe en ligne de commande :

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
python3 -m unittest discover -s tests -t .          # tests : vrais fichiers générés par ffmpeg + faux TMDB
python3 tests/ui_smoke.py ./captures video.webm     # parcours navigateur (Playwright) + captures
```

Arborescence de `seance/` :
- `scanner.py`, `names.py` : scan et reconnaissance des noms de fichiers
- `metadata.py` : TMDB et `.nfo`
- `images.py` : images
- `transcoder.py` : décision de lecture, ffmpeg et HLS
- `api.py` : routes HTTP
- `auth.py`, `db.py`, `web.py` : socle

L'interface (`static/`) est en JavaScript sans étape de compilation.

## Feuille de route

1. ~~Interface multi-utilisateurs~~ ✔
2. ~~Serveur multimédia autonome : scan, TMDB, lecture directe / remux / transcodage~~ ✔
3. Détection automatique des intros et génériques de fin
4. Import de l'historique « vu » depuis Jellyfin, par utilisateur
5. Liste « à regarder », collections, contrôle parental
6. Accélération matérielle optionnelle (VAAPI / Quick Sync) pour les serveurs équipés d'un GPU
