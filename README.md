# Séance

Un serveur multimédia **100 % auto-hébergé** pour tes films, séries et animes : il scanne tes dossiers, récupère affiches et résumés, et diffuse dans le navigateur avec une interface multi-utilisateurs. Aucun compte externe, aucun abonnement, pas besoin de Plex ni de Jellyfin.

- **Zéro dépendance Python** : Python 3 (bibliothèque standard) + **ffmpeg**. Le lecteur [hls.js](https://github.com/video-dev/hls.js) est téléchargé à l'installation.
- Pensé pour un **conteneur LXC** ou une VM Ubuntu/Debian, **sans GPU** : Séance évite le transcodage dès qu'il peut.

## Fonctionnalités

| | |
|---|---|
| **Bibliothèques** | Autant que tu veux (Films, Séries, Animes…), chacune avec un ou plusieurs dossiers. Nouveautés détectées automatiquement, webhook Radarr/Sonarr, scan incrémental. Un partage non monté ne vide jamais la bibliothèque |
| **Reconnaissance** | `Titre (Année)/…`, noms « scène » (`Inception.2010.1080p.BluRay…`), `S01E02`, `1x02`, `Saison 1/…`, animes `[Groupe] Titre - 12 [1080p]`, numérotation absolue (`Episode 1071`), spéciaux (`Season 00`, `Specials`) |
| **Métadonnées** | TMDB (affiches, fonds, résumés, genres, distribution, titres et images d'épisodes, classification) en français avec repli anglais. Lit aussi les `.nfo` et `poster.jpg` / `fanart.jpg` déjà présents (ceux de Jellyfin/Kodi, par exemple). Correction manuelle avec « Identifier… » |
| **Comptes** | Comptes propres à Séance, administrateurs, accès par bibliothèque, désactivation |
| **Espace personnel** | Reprise de lecture, « Reprendre et à suivre », historique, épisodes vus/non vus, langues audio/sous-titres préférées, thème, couleur, taille des affiches |
| **Lecteur** | Choix audio, sous-titres et qualité en cours de lecture, raccourcis clavier, bandeau « Passer l'intro », épisode suivant avec compte à rebours |
| **Sous-titres** | SRT, ASS/SSA, WebVTT, mov_text (internes ou fichiers à côté de la vidéo, `Film.fr.srt`, `Film.forced.srt`…) affichés par le navigateur, **sans transcodage**. PGS/VobSub incrustés |
| **Administration** | Partages réseau SMB (montage depuis l'interface), bibliothèques (avec explorateur de dossiers), utilisateurs, clé TMDB, limites de lectures/transcodages, lectures en cours avec la raison d'un éventuel transcodage |

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

Le script installe Python, git, ffmpeg et les outils SMB (cifs-utils, smbclient), récupère le code, l'installe dans `/opt/seance`, crée l'utilisateur système `seance` et le service `seance.service`, puis affiche l'adresse et le **code d'installation**.

**Mettre à jour** : relance la même commande. Les données (`/var/lib/seance`) et la configuration (`/etc/seance/seance.env`) sont conservées.

### Donner accès aux médias

**Méthode 1 : depuis Séance (VM ou LXC privilégié).** L'onglet **Administration → Partages réseau** monte tes partages SMB/CIFS sans ligne de commande. Tu saisis l'adresse du NAS et les identifiants, Séance liste les partages disponibles, tu en choisis un, il est monté en lecture seule dans `/mnt/seance/<nom>`, et tu crées la bibliothèque en un clic. Les partages sont remontés automatiquement au démarrage et toutes les 5 minutes s'ils sont tombés (NAS redémarré). Les mots de passe sont stockés dans `/etc/seance/smb/` (lisibles par root seulement), jamais dans la base de Séance.

Le montage est fait par un petit service root séparé, `seance-mountd`. Il ne sait que monter des partages SMB en lecture seule sous `/mnt/seance/`, et n'accepte les demandes que de l'utilisateur `seance`. Séance lui-même continue de tourner sans privilèges.

> ⚠️ **Linux interdit le montage SMB dans un conteneur LXC non privilégié**, quelle que soit l'application. Pour utiliser la méthode 1 dans Proxmox, il faut un conteneur **privilégié** avec l'option `mount=cifs` :
> - nouveau conteneur : `pct create … --unprivileged 0 --features mount=cifs,nesting=1` ;
> - conteneur existant : *Sauvegarde* → *Restaurer* en décochant « Non privilégié », puis `pct set <ID> -features mount=cifs,nesting=1` et redémarrage.
>
> Un conteneur privilégié est moins cloisonné de l'hôte : à réserver à un réseau domestique de confiance. Sinon, utilise la méthode 2.

**Méthode 2 : montage sur l'hôte Proxmox (LXC non privilégié).** On monte les partages **sur l'hôte**, puis on les « prête » au conteneur en lecture seule.

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

Pour une VM, la méthode 1 fonctionne directement.

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

## Nouveautés détectées automatiquement

Sur un partage SMB, Linux n'est pas prévenu des fichiers ajoutés depuis une autre machine. Séance compare donc régulièrement les dates de modification des **dossiers** de chaque bibliothèque, ce qui reste très léger même avec des milliers de dossiers. Après un changement, il attend que les dossiers ne bougent plus (copie terminée) et rescanne seulement la bibliothèque concernée. Un fichier modifié il y a moins de 45 secondes est considéré comme en cours de copie : il est repris au passage suivant. La fréquence se règle dans **Administration → Métadonnées et lecture** (par défaut : toutes les minutes).

**Ajout instantané depuis Radarr / Sonarr** : copie l'adresse du webhook affichée dans ce même écran, puis dans Radarr et Sonarr va dans *Paramètres → Connect → + → Webhook*. Colle l'adresse dans « URL », choisis la méthode POST et coche *On Import*, *On Upgrade*, *On Rename* et *On Delete*. Le bouton *Test* de Radarr doit répondre « Séance a bien reçu le test ».

## Sous-titres prêts dès la première seconde

Les sous-titres intégrés aux MKV (SRT, ASS…) ne peuvent être extraits qu'en lisant tout le fichier, ce qui prend parfois plus d'une minute sur un NAS. Séance les prépare donc à l'avance, toutes les pistes d'un fichier en **une seule lecture** :

- en tâche de fond pour les nouveaux fichiers, et pour toute la médiathèque au premier démarrage, uniquement quand personne ne regarde quelque chose ;
- en priorité quand on ouvre une fiche, et pour l'épisode suivant pendant qu'on regarde le précédent ;
- au lancement, si rien n'est encore prêt, le lecteur affiche « Préparation des sous-titres… » avec une progression et ne démarre qu'une fois les sous-titres disponibles. Le bouton *Lancer sans attendre* permet de ne pas patienter.

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

## Chapitres, aperçus, intro et générique

- **Aperçus au survol** : en survolant ou en faisant glisser la barre de lecture, une vignette de la scène s'affiche, avec le nom du chapitre. Les vignettes sont fabriquées à partir des images clés seulement, donc avec peu de calcul, et pendant la même lecture du fichier que la préparation des sous-titres.
- **Chapitres** : repères sur la barre et menu « Chapitres » dans le lecteur.
- **Passer l'intro / épisode suivant** : l'intro et le générique de fin sont repérés grâce aux chapitres du fichier. Les chapitres nommés (`Opening`, `OP`, `Intro`, `Générique`, `Ending`, `ED`, `Credits`…) sont reconnus. Pour les chapitres sans nom, typiques des animes, Séance cherche les blocs de 70 à 115 secondes en début et en fin d'épisode. Le bandeau « Passer l'intro » apparaît pendant l'intro ; la carte « Épisode suivant » apparaît au début du générique de fin. Un administrateur peut toujours corriger ces repères à la main pendant la lecture (menu ⚙), et ses repères priment.

Les fichiers sans chapitres n'ont pas encore de détection automatique : la comparaison audio entre les épisodes d'une saison est la prochaine étape.

## Profils, code PIN et contrôle parental

Comme Plex Home :
- **« Qui regarde ? »** : après la connexion, chacun choisit son profil. Le menu utilisateur → *Changer de profil* le rouvre à tout moment.
- **Profils gérés** (enfants, invités) : pas de mot de passe ; on y entre depuis « Qui regarde ? », avec un **code PIN** si on en a défini un.
- **Code PIN** : chacun peut protéger son profil (*Réglages → Code PIN du profil*). Passer sur un compte complet ou administrateur sans PIN demande son mot de passe.
- **Restriction d'âge** par profil (7, 10, 12 ou 16 ans max.), d'après la classification TMDB ou `.nfo` (U, 10, 12, 16, 18, PG-13, TV-MA…). Les contenus trop classés disparaissent partout : bibliothèques, accueil, recherche, fiches et lecture. Option pour autoriser ou non les contenus sans classification.

Tout se règle dans **Administration → Utilisateurs**.

## Langue audio et sous-titres

Sur la fiche d'un film, d'un épisode ou d'une série, deux listes permettent de choisir l'audio et les sous-titres avant de lancer la lecture. Le choix est **retenu pour toute la série** (ou pour le film), et pour chaque utilisateur : les épisodes suivants se lancent dans la même langue. Un changement fait dans le lecteur est retenu de la même façon.

Les pistes 5.1 / 7.1 (AC3, E-AC3, DTS, TrueHD, AAC 5.1…) sont réduites en stéréo par Séance avec une protection contre l'écrêtage. Le mixage par défaut de ffmpeg dépassait le niveau maximal et faisait saturer le son.

## Dépannage

| Symptôme | Piste |
|---|---|
| « Linux interdit le montage SMB… » | Conteneur LXC non privilégié : voir « Donner accès aux médias » (méthode 1 en privilégié, ou méthode 2) |
| « Montage refusé (identifiants valides) » | Conteneur privilégié sans l'option : `pct set <ID> -features mount=cifs,nesting=1` puis redémarrer |
| Partage « Non monté » | Bouton *Remonter* ; journal : `journalctl -u seance-mountd -n 50` |
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
- `mountd.py` : service root de montage des partages SMB
- `watcher.py` : détection des nouveautés ; `subcache.py` : préparation des sous-titres
- `api.py` : routes HTTP
- `auth.py`, `db.py`, `web.py` : socle

L'interface (`static/`) est en JavaScript sans étape de compilation.

## Feuille de route

1. ~~Interface multi-utilisateurs~~ ✔
2. ~~Serveur multimédia autonome : scan, TMDB, lecture directe / remux / transcodage~~ ✔
3. Détection automatique des intros et génériques de fin par empreinte audio (pour les fichiers sans chapitres)
4. Import de l'historique « vu » depuis Jellyfin, par utilisateur
5. ~~Profils, PIN, contrôle parental~~ ✔ — liste « à regarder », collections
6. Accélération matérielle optionnelle (VAAPI / Quick Sync) pour les serveurs équipés d'un GPU
