# Nexis

Un serveur multimédia **100 % auto-hébergé** pour tes films, séries et animes : il scanne tes dossiers, récupère affiches et résumés, et diffuse dans le navigateur avec une interface multi-utilisateurs. Aucun compte externe, aucun abonnement, pas besoin de Plex ni de Jellyfin.

- **Zéro dépendance Python** : Python 3 (bibliothèque standard) + **ffmpeg**. Le lecteur [hls.js](https://github.com/video-dev/hls.js) (1.5.15) est fourni dans le dépôt, avec son empreinte SHA-256 vérifiée à l'installation.
- Polices Outfit et Inter (licence SIL OFL, `static/fonts/`) servies par le serveur : aucune ressource externe.
- Pensé pour un **conteneur LXC** ou une VM Ubuntu/Debian, **sans GPU** : Nexis évite le transcodage dès qu'il peut.

## Fonctionnalités

| | |
|---|---|
| **Bibliothèques** | Autant que tu veux (Films, Séries, Animes…), chacune avec un ou plusieurs dossiers. Nouveautés détectées automatiquement, webhook Radarr/Sonarr, scan incrémental. Un partage non monté ne vide jamais la bibliothèque |
| **Reconnaissance** | `Titre (Année)/…`, noms « scène » (`Inception.2010.1080p.BluRay…`), `S01E02`, `1x02`, `Saison 1/…`, animes `[Groupe] Titre - 12 [1080p]`, numérotation absolue (`Episode 1071`), spéciaux (`Season 00`, `Specials`) |
| **Métadonnées** | TMDB (affiches, fonds, résumés, genres, distribution, titres et images d'épisodes, classification) en français avec repli anglais. Lit aussi les `.nfo` et `poster.jpg` / `fanart.jpg` déjà présents (ceux de Jellyfin/Kodi, par exemple). Correction manuelle avec « Identifier… » |
| **Comptes** | Comptes propres à Nexis, administrateurs, accès par bibliothèque, désactivation |
| **Espace personnel** | Reprise de lecture, « Reprendre et à suivre », historique, épisodes vus/non vus, langues audio/sous-titres préférées, thème, couleur, taille des affiches |
| **Collections et listes** | Sagas créées automatiquement depuis TMDB (*Harry Potter*, *Le Seigneur des anneaux*…), collections manuelles, liste « À regarder » par utilisateur, playlists personnelles lues à la suite |
| **Bonus** | Bandes-annonces (YouTube via TMDB ou fichiers locaux), coulisses, scènes coupées, interviews… rangés sous le film ou la série |
| **Découvrir et demandes** | Catalogues Netflix, Prime Video, Disney+, Canal+… (via TMDB) avec ce qui est déjà sur le serveur, bouton « Demander » relié à Seerr / Overseerr / Jellyseerr |
| **Lecteur** | Choix audio, sous-titres et qualité en cours de lecture, raccourcis clavier, sélection de chapitre en vignettes, bandeau « Passer l'intro », épisode suivant avec compte à rebours |
| **Regarder ensemble** | Lecture synchronisée à plusieurs, chacun sur son écran (comme SyncPlay de Jellyfin) : pauses, sauts et épisode suivant partagés, attente automatique de celui dont la connexion décroche |
| **TV en direct** | Listes de chaînes M3U (IPTV, box opérateur…) et guide des programmes XMLTV, configurées par l'administrateur et proposées à tous : grille des chaînes avec le programme en cours, guide, favoris, zapping |
| **Tâches planifiées** | Scan, actualisation des métadonnées, images de chapitres, détection des intros et génériques par empreinte audio, sous-titres, guide TV, nettoyage : chacune avec sa planification et ses options |
| **Sous-titres** | SRT, ASS/SSA, WebVTT, mov_text (internes ou fichiers à côté de la vidéo, `Film.fr.srt`, `Film.forced.srt`…) affichés par le navigateur, **sans transcodage**. Les ASS des animes sont rendus fidèlement par libass (panneaux positionnés, karaoké, polices jointes au MKV). Les PGS des Blu-ray sont dessinés par le navigateur (libpgs), sans transcodage. VobSub incrustés |
| **Administration** | Partages réseau SMB (montage depuis l'interface), bibliothèques (avec explorateur de dossiers), utilisateurs, clé TMDB, limites de lectures/transcodages, lectures en cours avec la raison d'un éventuel transcodage |

### Comment Nexis ménage le processeur

La lecture reprend les choix de **Jellyfin** (son serveur et son lecteur web), avec quelques ajouts.

**Le navigateur dit ce qu'il sait lire.** Au lancement, le lecteur annonce les codecs que le navigateur décode (H.264, HEVC 8/10 bits, AV1, VP9, AAC, AC3, E-AC3, FLAC, Opus…), comme le profil d'appareil de Jellyfin. Nexis choisit alors le mode le plus léger :

| Mode | Quand | Coût CPU |
|---|---|---|
| **Lecture directe** | MP4 dont la vidéo et le son sont lisibles par le navigateur | nul : le navigateur lit le fichier |
| **Remux** | Vidéo lisible par le navigateur (H.264 ; HEVC ou AV1 s'il les décode) dans un MKV, ou piste audio à convertir (DTS, TrueHD, 5.1…) | très faible : la vidéo est recopiée, seul l'audio est converti |
| **Transcodage** | Vidéo que ce navigateur ne décode pas (HEVC sur Firefox, H.264 10 bits…), sous-titres VobSub, qualité réduite choisie | 1 à 2 cœurs par lecture en 1080p |

**Un flux « à la Jellyfin ».** Le lecteur reçoit d'emblée une playlist de toute la vidéo, découpée en segments de 6 s (en remux, calés sur les images clés lues dans l'index du MKV). Les segments sont fabriqués à la demande au format fMP4, avec les horodatages d'origine : le navigateur n'a rien à reconvertir, la position affichée est toujours la vraie, et un saut dans la vidéo ne recrée pas le flux — si le lecteur demande un segment éloigné, ffmpeg est relancé à cet endroit.

Le menu ⚙ du lecteur indique le mode en cours et pourquoi. ffmpeg est mis en pause quand il a pris 3 minutes d'avance, et tourne avec une priorité réduite. Un nombre maximal de transcodages simultanés protège le serveur (Administration → Métadonnées et lecture).

## Installation

Sur un conteneur LXC ou une VM **Ubuntu 22.04/24.04** ou **Debian 12/13**, en root :

```bash
curl -fsSL https://raw.githubusercontent.com/xenix13/Nexis/main/deploy/install.sh | bash
```

Le script installe Python, git, ffmpeg et les outils SMB (cifs-utils, smbclient), récupère le code, l'installe dans `/opt/seance`, crée l'utilisateur système `seance` et le service `seance.service`, puis affiche l'adresse et le **code d'installation**.

**Mettre à jour** : relance la même commande. Les données (`/var/lib/seance`) et la configuration (`/etc/seance/seance.env`) sont conservées.

### Donner accès aux médias

**Méthode 1 : depuis Nexis (VM ou LXC privilégié).** L'onglet **Administration → Partages réseau** monte tes partages SMB/CIFS sans ligne de commande. Tu saisis l'adresse du NAS et les identifiants, Nexis liste les partages disponibles, tu en choisis un, il est monté en lecture seule dans `/mnt/seance/<nom>`, et tu crées la bibliothèque en un clic. Les partages sont remontés automatiquement au démarrage et toutes les 5 minutes s'ils sont tombés (NAS redémarré). Les mots de passe sont stockés dans `/etc/seance/smb/` (lisibles par root seulement), jamais dans la base de Nexis.

Le montage est fait par un petit service root séparé, `seance-mountd`. Il ne sait que monter des partages SMB en lecture seule sous `/mnt/seance/`, et n'accepte les demandes que de l'utilisateur `seance`. Nexis lui-même continue de tourner sans privilèges.

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
2. **Administration → Métadonnées et lecture** : colle ta **clé TMDB**. Crée un compte gratuit sur [themoviedb.org](https://www.themoviedb.org/), puis Paramètres → API. La clé v3 ou le jeton de lecture v4 fonctionnent tous les deux. Sans clé, Nexis se contente des noms de fichiers, des `.nfo` et des images locales.
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

Sur un partage SMB, Linux n'est pas prévenu des fichiers ajoutés depuis une autre machine. Nexis compare donc régulièrement les dates de modification des **dossiers** de chaque bibliothèque, ce qui reste très léger même avec des milliers de dossiers. Après un changement, il attend que les dossiers ne bougent plus (copie terminée) et ne revoit **que les dossiers qui ont changé** (scan partiel) : un nouvel épisode ne fait relire que le dossier de sa saison, pas toute la bibliothèque. Les fichiers déjà connus et inchangés ne sont ni réanalysés ni réécrits. Un fichier modifié il y a moins de 45 secondes est considéré comme en cours de copie : il est repris au passage suivant. La fréquence se règle dans **Administration → Métadonnées et lecture** (par défaut : toutes les minutes).

**Ajout instantané depuis Radarr / Sonarr** : copie l'adresse du webhook affichée dans ce même écran, puis dans Radarr et Sonarr va dans *Paramètres → Connect → + → Webhook*. Colle l'adresse dans « URL », choisis la méthode POST et coche *On Import*, *On Upgrade*, *On Rename* et *On Delete*. Le bouton *Test* de Radarr doit répondre « Nexis a bien reçu le test ». Radarr et Sonarr indiquent le dossier du film ou de la série : Nexis retrouve le dossier correspondant chez lui (même si les chemins diffèrent, par exemple `/movies/…` chez Radarr et `/mnt/films/…` chez Nexis) et ne scanne que celui-là.

Un scan complet de sécurité passe une fois par jour (réglable dans *Administration → Tâches planifiées*) ; il ne fait que vérifier les dates des fichiers.

## Sous-titres prêts dès la première seconde

Les sous-titres intégrés aux MKV (SRT, ASS, PGS…) ne peuvent être extraits qu'en lisant tout le fichier, ce qui prend plusieurs minutes pour un film de 30 Go sur un NAS. Nexis les **exporte une fois pour toutes** (toutes les pistes d'un fichier en une seule lecture, avec les polices jointes) et les garde dans son cache (`/var/lib/seance/cache/subs`). Ce cache suit le **fichier** (taille et date) et non sa fiche : un fichier renommé ou déplacé (par Radarr, Sonarr ou à la main) retrouve ses sous-titres sans être relu, et un rescan ne réexporte jamais ce qui l'est déjà. Seul un fichier remplacé (nouvelle version) est réexporté. L'export se fait :

- **dès le scan**, pour chaque nouveau fichier, en tâche de fond. Pendant qu'on regarde quelque chose, cette file continue d'avancer doucement (débit de lecture plafonné, un fichier à la fois) au lieu de s'arrêter ; *Administration → Métadonnées et lecture → Tout préparer maintenant* met toute la médiathèque en file ;
- **dès qu'on ouvre la fiche** d'un film, d'un épisode ou d'une série (l'épisode qui sera lancé), en priorité ;
- pour l'épisode suivant pendant qu'on regarde le précédent.

Si l'on lance un film dont les sous-titres ne sont pas encore prêts, **la lecture n'attend plus la fin de l'extraction** : elle démarre dès que les sous-titres couvrent les 4 premières minutes à regarder. L'extraction lit le fichier bien plus vite que le temps réel et reste devant ; le lecteur récupère la suite toutes les 20 secondes. Changer de langue de sous-titres est immédiat : toutes les pistes sont extraites en même temps. En cas de saut plus loin que ce qui est déjà prêt, le lecteur affiche « Sous-titres en préparation… » le temps que l'extraction y arrive.

Les vignettes d'aperçu ne sont fabriquées que lorsque personne ne regarde : une préparation de fond s'interrompt dès qu'une lecture démarre.

### Sous-titres ASS des animes

Les sous-titres ASS (fansubs, Crunchyroll, ADN…) contiennent bien plus que du texte : panneaux traduits placés sur l'image, dessins, karaoké des génériques, polices spéciales jointes au fichier MKV. Nexis les fait rendre dans le navigateur par **libass** (le moteur de mpv/VLC, compilé en WebAssembly via [JASSUB](https://github.com/ThaUnknown/jassub), fourni dans `static/vendor/jassub`, licences dans ce dossier). Seules les polices réellement utilisées par la piste sont téléchargées. Sur un navigateur trop ancien, Nexis retombe sur un affichage texte simplifié (sans dessins, panneaux en haut de l'image).

### Démarrage sans coupure

La lecture ne démarre qu’avec une réserve de vidéo déjà chargée : 5 secondes en lecture directe, 12 secondes en remux, 20 secondes en transcodage (la conversion produit à peine plus vite que le temps réel). Après un saut dans la vidéo, la réserve demandée est plus courte (3 à 10 secondes). Si le réseau ou le processeur décroche en cours de route, le lecteur se met en pause le temps de refaire une vraie réserve (15 à 25 secondes) au lieu de repartir et de recouper aussitôt. Si cela se répète en transcodage, il propose de passer en 720p.

## Accès depuis l'extérieur (HTTPS)

Place Nexis derrière un reverse proxy HTTPS (Caddy, nginx, Nginx Proxy Manager…). Dans `/etc/seance/seance.env` :

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
- **Chapitres** : repères sur la barre, et **sélection de chapitre en vignettes** dans le lecteur (bouton Chapitres ou touche `C`, flèches puis Entrée) et sur la fiche du film ou de l'épisode. Une image par chapitre, prise un peu après son début (et plus loin si l'image est noire), fabriquée à la demande puis par la tâche « Extraire les images des chapitres ».
- **Passer l'intro / épisode suivant** : l'opening et le générique de fin sont repérés grâce aux chapitres du fichier. Les chapitres nommés (`Opening`, `OP`, `Intro`, `Générique`, `Ending`, `ED`, `Credits`…) sont reconnus. Dans les animes, « Intro » ou « Prologue » désigne souvent la scène d'avant-générique : un chapitre `OP` / `Opening` a donc toujours la priorité, et un « Intro » trop court pour être une chanson laisse la place au chapitre suivant s'il en a la durée. Pour les chapitres sans nom, typiques des animes, Nexis cherche les blocs de 70 à 115 secondes en début et en fin d'épisode. Le bandeau « Passer l'intro » apparaît pendant l'intro ; la carte « Épisode suivant » apparaît au début du générique de fin. Un administrateur peut toujours corriger ces repères à la main pendant la lecture (menu ⚙), et ses repères priment.

**Fichiers sans chapitres : empreinte audio.** La tâche « Détecter les intros et génériques » compare l'empreinte audio (Chromaprint, fournie par ffmpeg) du début et de la fin des épisodes d'une même saison, comme le plugin *Intro Skipper* de Jellyfin : la chanson d'ouverture et le générique sont les passages que les épisodes ont en commun. Elle passe après chaque ajout d'épisodes et chaque nuit ; on règle la zone analysée (10 premières et 6 dernières minutes par défaut) et les durées minimale et maximale. Ordre de priorité des repères : posés à la main, puis chapitres nommés, puis empreinte audio, puis chapitres de la durée d'une chanson.

## Tâches planifiées

*Administration → Tâches planifiées* liste les travaux de fond du serveur, avec leur dernière exécution (durée, résultat) et la prochaine. Chacune se lance à la main, s'arrête, et se règle : **désactivée**, **toutes les N heures**, **chaque jour** ou **chaque semaine** à l'heure choisie, plus ses options propres.

| Tâche | Par défaut | Options |
|---|---|---|
| Analyser les bibliothèques | toutes les 24 h | bibliothèques concernées |
| Actualiser les métadonnées | le dimanche à 4 h | seulement l'incomplet / ce qui n'a pas été actualisé depuis N jours / tout ; bibliothèques |
| Extraire les images des chapitres | chaque nuit + après chaque ajout | pause pendant les lectures, bibliothèques |
| Détecter les intros et génériques | chaque nuit + après chaque ajout | minutes analysées au début et à la fin, durées min. et max., générique oui/non, ignorer les épisodes à chapitres nommés |
| Préparer les sous-titres et les aperçus | chaque nuit | — |
| Actualiser les chaînes et le guide TV | toutes les 12 h | — |
| Nettoyer les caches | chaque nuit | ancienneté des images gardées |

Une seule tâche tourne à la fois. Celles qui lisent les fichiers sur le partage réseau se mettent **en pause pendant qu'on regarde quelque chose**.

## TV en direct (IPTV)

*Administration → TV en direct* : ajoute une ou plusieurs **sources**, chacune avec une liste de chaînes **M3U / M3U8** (l'adresse donnée par ton fournisseur IPTV ou ta box, ou le chemin d'un fichier `.m3u` sur le serveur) et, si besoin, un **guide XMLTV** (`.xml` ou `.xml.gz` ; inutile si la liste l'indique déjà dans son en-tête `url-tvg`). Les attributs habituels sont lus : `tvg-id`, `tvg-name`, `tvg-logo`, `tvg-chno`, `group-title`, `#EXTVLCOPT:http-user-agent`…

- Les chaînes sont proposées **à tous les utilisateurs** dans l'onglet **TV en direct** : grille avec le programme en cours (barre de progression) et le suivant, **guide des programmes** sur plusieurs heures, recherche, groupes, **favoris** par personne. Dans le lecteur : ↑ / ↓ pour zapper, `L` pour la liste des chaînes.
- *Groupes…* masque des groupes entiers pour tout le monde (pratique pour une liste de milliers de chaînes). Une option masque la TV pour les profils avec restriction d'âge.
- Le navigateur ne lit pas un flux MPEG-TS : Nexis le reçoit avec ffmpeg et le redécoupe en HLS. La vidéo **H.264 est recopiée** telle quelle (presque aucun calcul), le son est converti en AAC stéréo ; une vidéo MPEG-2 ou HEVC est convertie en H.264 (réglable : jamais / automatique / toujours). Une chaîne regardée par plusieurs personnes **n'est reçue qu'une fois**, et ffmpeg s'arrête 45 s après le départ du dernier spectateur.
- **Chaînes simultanées (max.)** : beaucoup de fournisseurs limitent le nombre de connexions ; au-delà, Nexis l'explique au lieu d'échouer.
- Les chaînes et le guide sont rechargés toutes les 12 h (tâche planifiée), ou à la demande (*Actualiser*).

## Regarder ensemble

Comme **SyncPlay** dans Jellyfin : plusieurs personnes regardent la même vidéo en même temps, chacune depuis son écran (ou à distance, derrière le reverse proxy).

- Sur la fiche d'un film ou d'un épisode, le bouton **Regarder ensemble** crée un groupe (avec un nom, et l'option « seul l'hôte contrôle la lecture »). Dans le lecteur, le même bouton montre les membres, leur état (prêt, chargement) et copie le **lien d'invitation**. Les groupes en cours apparaissent aussi sur l'accueil et dans la barre du haut.
- Lecture, pause, sauts, choix d'un chapitre, « Passer l'intro » et **épisode suivant** sont partagés. Ceux qui rejoignent arrivent au même endroit.
- Chacun a son propre flux (sa qualité, sa langue, ses sous-titres). Un petit décalage est rattrapé en accélérant ou ralentissant à peine la lecture (± 5 %), un grand par un saut. Si la connexion de quelqu'un décroche, tout le groupe attend qu'il ait rechargé (12 s au plus), puis repart ensemble.
- Chaque membre garde sa propre reprise de lecture et son historique. Il faut avoir accès au contenu (bibliothèques, restriction d'âge) pour le regarder dans un groupe.
- Les événements passent par une connexion HTTP ouverte (*Server-Sent Events*). Derrière nginx, désactive le tampon pour `/api/together/` (`proxy_buffering off;`, déjà conseillé plus haut).

## Profils, code PIN et contrôle parental

Comme Plex Home :
- **« Qui regarde ? »** : après la connexion, chacun choisit son profil. Le menu utilisateur → *Changer de profil* le rouvre à tout moment.
- **Profils gérés** (enfants, invités) : pas de mot de passe ; on y entre depuis « Qui regarde ? », avec un **code PIN** si on en a défini un.
- **Code PIN** : chacun peut protéger son profil (*Réglages → Code PIN du profil*). Passer sur un compte complet ou administrateur sans PIN demande son mot de passe.
- **Restriction d'âge** par profil (7, 10, 12 ou 16 ans max.), d'après la classification TMDB ou `.nfo` (U, 10, 12, 16, 18, PG-13, TV-MA…). Les contenus trop classés disparaissent partout : bibliothèques, accueil, recherche, fiches et lecture. Option pour autoriser ou non les contenus sans classification.

Tout se règle dans **Administration → Utilisateurs**.

## Collections, « À regarder » et playlists

- **Collections** : quand TMDB indique qu'un film fait partie d'une saga, Nexis crée la collection tout seul (affiche, fond et résumé compris). Elles apparaissent dans l'onglet **Collections** de la bibliothèque et en bas de la fiche de chaque film. Un administrateur peut aussi créer ses propres collections (menu ⋯ → *Collections…*) et les renommer ou les supprimer.
- **À regarder** : le bouton signet sur un film ou une série l'ajoute à ta liste (onglet *À regarder* et rangée sur l'accueil). Un film marqué comme vu en sort automatiquement.
- **Playlists** : menu ⋯ → *Ajouter à une playlist…* sur un film, un épisode, une saison ou une série entière. *Tout lire* enchaîne les éléments dans l'ordre choisi (flèches ↑ ↓). Chaque utilisateur a les siennes.

### Bonus et bandes-annonces

Nexis reconnaît les bonus rangés comme pour Plex ou Jellyfin, à côté du film ou dans le dossier de la série :

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

Exemple : `Films/Inception (2010)/Trailers/Bande-annonce VF.mkv` ou `Films/Inception (2010)/Inception (2010)-trailer.mp4`. Les bonus ne sont jamais comptés comme des films ou des épisodes. Sans bande-annonce locale, le bouton *Bande-annonce* lit celle de TMDB depuis YouTube (mode sans cookies).

## Découvrir et demander (Netflix, Prime Video, Disney+…)

L'onglet **Découvrir** montre ce qui est proposé sur les plateformes de streaming en France (Netflix, Prime Video, Disney+, Canal+, Max, Apple TV+, Paramount+, Crunchyroll, ADN), les tendances de la semaine et une recherche. Chaque titre indique s'il est **déjà sur ton serveur** (le clic l'ouvre directement), **demandé** ou **en cours de téléchargement**. Il faut une clé TMDB.

Le bouton **Demander** passe par [Seerr](https://docs.seerr.dev) (le successeur d'Overseerr et de Jellyseerr), qui envoie la demande à Radarr / Sonarr. Pour une série, on choisit les saisons. Le bouton signet ajoute aussi à **À regarder** un titre absent : il est alors demandé automatiquement (désactivable) et rejoint la liste habituelle dès son arrivée sur le serveur.

### Relier Seerr

1. Installe Seerr, de préférence dans un conteneur LXC à part, en suivant [sa documentation](https://docs.seerr.dev), et branche-le sur Radarr et Sonarr (*Paramètres → Services*).
2. Dans Seerr, *Paramètres → Général*, copie la **clé d'API**.
3. Dans Nexis, *Administration → Demandes*, colle l'adresse de Seerr (ex. `http://192.168.1.20:5055`) et la clé, puis *Enregistrer et tester*.

Quand un compte Seerr porte le même nom qu'un compte Nexis (identifiant, nom affiché ou début de l'adresse e-mail), la demande est faite **à son nom**, donc les règles de Seerr s'appliquent (validation, quotas…). Sinon, elle part au nom du propriétaire de la clé d'API.

## Langue audio et sous-titres

Sur la fiche d'un film, d'un épisode ou d'une série, deux listes permettent de choisir l'audio et les sous-titres avant de lancer la lecture. Le choix est **retenu pour toute la série** (ou pour le film), et pour chaque utilisateur : les épisodes suivants se lancent dans la même langue. Un changement fait dans le lecteur est retenu de la même façon.

**Sous-titres « forcés »** (« FR Forced », « Signs & Songs »…) : ils ne traduisent que les panneaux, inscriptions et chansons, pour qui regarde déjà dans cette langue (la VF d'un anime, par exemple). Avec la VO japonaise, ils semblent vides ou très lents à apparaître. Nexis ne les propose donc que si l'audio est dans leur langue, et les choisit alors tout seul ; avec un autre audio, c'est la piste complète qui est utilisée (y compris si une ancienne préférence désignait la piste forcée).

Les pistes 5.1 / 7.1 (AC3, E-AC3, DTS, TrueHD, AAC 5.1…) sont réduites en stéréo par Nexis avec les mixages de Jellyfin (*Administration → Métadonnées et lecture → Son multicanal*) : ffmpeg par défaut, Dave750, Dialogues (nuit), RFC 7845 ou AC-4, suivis d'un gain réglable. Nexis ajoute un limiteur après le gain : le son ne peut pas saturer, quel que soit le réglage.

## Sécurité

Le rapport d'audit, les correctifs et les risques acceptés sont décrits dans [docs/AUDIT-SECURITE.md](docs/AUDIT-SECURITE.md). En bref : expose Nexis sur Internet uniquement derrière un reverse proxy HTTPS (`SEANCE_COOKIE_SECURE=1`, `SEANCE_TRUST_PROXY=1`), et n'ajoute que des listes IPTV de fournisseurs de confiance.

## Dépannage

**Son haché ou saturé, sous-titres absents** : *Administration → Lectures en cours* affiche pour chaque lecture le mode (direct, remux, transcodage), la raison, la **vitesse de conversion** (en dessous de ×1, le processeur ne suit pas : baisse la qualité dans le menu ⚙ du lecteur) et les derniers messages de ffmpeg. *Informations sur le fichier* (menu ⋯ d'un film ou d'un épisode) liste les pistes audio et sous-titres du fichier.


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
| Saccades, swap plein (LXC) | *Lectures en cours* montre la mémoire, le swap et chaque ffmpeg. Un transcodage 4K prend 0,5 à 0,7 Go : compte au moins 2 Go de mémoire par transcodage simultané, et 1 à 2 Go de swap |
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
python3 tests/ui_features.py ./captures video.webm  # chapitres, tâches, TV en direct, regarder ensemble
python3 tests/hls_real.py chemin/vers/hls.min.js     # lecture réelle hls.js (VP9/Opus) : reprise, sauts, PGS
```

Arborescence de `seance/` :
- `scanner.py`, `names.py` : scan et reconnaissance des noms de fichiers
- `metadata.py` : TMDB et `.nfo`
- `images.py` : images
- `transcoder.py` : décision de lecture, ffmpeg et HLS
- `mountd.py` : service root de montage des partages SMB
- `watcher.py` : détection des nouveautés ; `subcache.py` : préparation des sous-titres
- `tasks.py` : tâches planifiées ; `chapters.py` : images de chapitres ; `introdetect.py` : intros par empreinte audio
- `livetv.py` : TV en direct (M3U, XMLTV, flux HLS) ; `syncplay.py` : « Regarder ensemble »
- `api.py` : assemblage du serveur (contexte partagé, contrôles d'accès communs)
- `catalog.py` : outils partagés par les routes (droits, cartes, état de lecture) ; `routes/` : routes HTTP par thème (`account`, `library`, `media`, `admin`)
- `auth.py`, `db.py`, `web.py` : socle

L'interface (`static/`) est en JavaScript sans étape de compilation.

## Feuille de route

### Fait
- [x] Interface multi-utilisateurs, profils, code PIN, contrôle parental
- [x] Serveur multimédia autonome : scan, TMDB, lecture directe / remux / transcodage
- [x] Détection des intros et génériques par empreinte audio (fichiers sans chapitres)
- [x] Collections, « À regarder », playlists, bonus et bandes-annonces
- [x] Découvrir les plateformes de streaming et demander via Seerr
- [x] Tâches planifiées, TV en direct (IPTV), regarder ensemble
- [x] Nouveau nom, logo et interface « Grand écran »

### Ordre de travail
1. Corrections (ci-dessous), en commençant par les problèmes vus en « Regarder ensemble »
2. Lecteur et interface, puis « Regarder ensemble »
3. Médiathèque
4. Performances et transcodage matériel
5. Installation et mises à jour
6. Applications Nexis natives

### Corrections (retours des premiers utilisateurs)
- [ ] « Regarder ensemble » à deux (vu sur *Django*) : la mémoire d'échange (swap) du serveur se remplit et des lignes vertes apparaissent dans la vidéo
- [ ] « Regarder ensemble » : sous-titres décalés par rapport à l'audio (pas de souci en lecture seule)
- [ ] Générique de fin : ne plus passer automatiquement à l'épisode suivant ; l'utilisateur choisit (clic sur le bandeau « Épisode suivant » ou lecture jusqu'à la fin)
- [ ] TV en direct : ne pas intégrer les chaînes qui ne répondent pas
- [x] Retrait de la compatibilité avec les applications Jellyfin (trop limitée, remplacée par les applications Nexis)

### Lecteur et interface
- [ ] Lecteur réductible (mini-lecteur dans un coin, comme Plex) : on continue à naviguer sans arrêter la lecture
- [ ] « Reprendre et à suivre » : bouton pour marquer comme vu directement sur la vignette
- [ ] Personnalisation par utilisateur : ordre des rangées de l'accueil et des liens de la barre du haut
- [ ] Suggestions selon les derniers visionnages
- [ ] Animations (transitions de pages, apparition des cartes, survols) pour une interface plus vivante

### Regarder ensemble
- [x] Quand un membre change de langue, de qualité ou de sous-titres incrustés, le groupe se met en pause pendant son chargement ; les sous-titres affichés par le navigateur changent sans recharger la vidéo
- [ ] Liste des groupes mise à jour en temps réel, sans recharger la page
- [ ] Bouton dans la barre du haut pour créer un groupe à l'avance et inviter
- [ ] Optimisation générale (resynchronisation plus rapide, moins de requêtes)

### Médiathèque
- [ ] Fichiers `.strm` : lecture du flux distant qu'ils désignent, avec affiche et métadonnées comme un fichier local (source et fonctionnement à définir ensemble)
- [ ] Import de l'historique « vu » depuis Jellyfin, par utilisateur

### Performances
- [ ] Optimiser le serveur et l'interface (requêtes, cache, poids des pages, mémoire)
- [ ] Transcodage matériel optionnel : l'administrateur choisit le moteur selon sa machine (Intel Quick Sync / VAAPI, AMD VAAPI, NVIDIA NVENC), avec un test de détection

### Installation et mises à jour
- [ ] Paquet `.deb` pour machine virtuelle et conteneur LXC, et image Docker
- [ ] Mise à jour du serveur et des applications depuis l'interface (administrateur)

### Applications Nexis natives (en dernier)
Comme les applications Jellyfin : au premier lancement, elles demandent l'adresse du serveur, puis on se connecte avec son compte Nexis et on retrouve toutes les fonctions du serveur.
- [ ] Application Windows
- [ ] Application Android TV (navigation à la télécommande)
- [ ] Application installable sur téléphone (PWA)
