# Audit de sécurité de Séance — version 3.1.0

*23 octobre 2026. Revue complète du code (serveur Python, interface web, service de montage, scripts de déploiement), à la lecture et par des essais sur un serveur de test. Chaque correctif est couvert par un test de non-régression dans `tests/test_security.py`.*

## Périmètre et menace considérée

Séance est fait pour un **réseau domestique**, éventuellement ouvert sur Internet derrière un reverse proxy HTTPS. Les menaces étudiées :

- un **visiteur non connecté** (Internet ou réseau local) ;
- un **membre du foyer** qui veut sortir de ses droits (profil enfant, bibliothèques non autorisées) ;
- un **site malveillant** visité par un membre connecté (CSRF, navigation provoquée) ;
- un **contenu hostile** venant de l'extérieur : liste de chaînes du fournisseur IPTV, métadonnées TMDB, fichier piégé déposé dans la médiathèque ;
- l'**installation** elle-même (dépendances téléchargées, service système).

L'administrateur est considéré comme de confiance : il peut, par conception, parcourir les dossiers du serveur et choisir les adresses contactées.

## Résumé

| # | Gravité | Domaine | Problème | État |
|---|---|---|---|---|
| 1 | Moyenne | TV en direct | Une liste M3U pouvait faire lire au serveur ses propres fichiers (chemins locaux, protocoles internes de ffmpeg, logos et guide « locaux ») | Corrigé |
| 2 | Moyenne | Contrôle parental | Un profil limité par âge pouvait passer sur un profil géré sans code PIN et lever sa limite | Corrigé |
| 3 | Moyenne | Webhook Radarr/Sonarr | Un chemin contenant `..` pouvait faire indexer des fichiers hors des bibliothèques (jeton requis) | Corrigé |
| 4 | Moyenne | Médiathèque | Un faux `.mkv` contenant une playlist HLS pouvait faire ouvrir à ffmpeg des adresses réseau ou d'autres fichiers | Corrigé |
| 5 | Moyenne | Serveur HTTP | Connexions lentes jamais fermées et nombre de fils illimité : quelques clients pouvaient bloquer le serveur | Corrigé |
| 6 | Moyenne | Reverse proxy | L'adresse du client était lue au début de `X-Forwarded-For`, que le client peut inventer : contournement du blocage après mots de passe erronés | Corrigé |
| 7 | Faible | TV en direct | Décompression gzip non bornée (réponse HTTP compressée) : « bombe » de quelques Ko → plusieurs Go en mémoire | Corrigé |
| 8 | Faible | Serveur HTTP | `Content-Length` négatif accepté ; JSON très imbriqué → erreur interne | Corrigé |
| 9 | Faible | Regarder ensemble | Une simple requête GET (navigation provoquée par un autre site) faisait rejoindre un groupe ; flux d'événements illimités par personne | Corrigé |
| 10 | Faible | Installation | hls.js téléchargé depuis un CDN sans vérification, avec repli sur « la dernière 1.x » | Corrigé |
| 11 | Faible | Installation | Code d'installation sans limite d'essais | Corrigé |
| 12 | Faible | Contrôle parental | Images d'une collection visibles par un profil pour qui elle est entièrement masquée | Corrigé |
| 13 | Faible | Images TMDB | Logos SVG servis par Séance sans bac à sable | Corrigé |
| 14 | Faible | Partages SMB | Partages montés sans `nosuid,nodev,noexec` | Corrigé (partages existants mis à jour) |
| 15 | Info | Durcissement | En-têtes HTTP et bac à sable systemd complétables | Corrigé |

Aucune faille critique (exécution de code à distance, contournement de l'authentification, injection SQL ou de commande) n'a été trouvée.

## Détail des problèmes et correctifs

### 1. Liste IPTV : lecture de fichiers du serveur
**Constat.** Le contenu d'une liste M3U vient du fournisseur. Une ligne `/etc/passwd`, `concat:…`, `subfile:…` ou `file://…` était transmise telle quelle à ffmpeg. Le flux produit, lisible par tout le foyer, pouvait contenir un fichier du serveur, la base de Séance par exemple. De même, un logo ou un guide (`url-tvg`) indiqué comme chemin local était lu sur le disque.
**Correctif** (`livetv.py`).
- Seules les adresses `http(s)`, `rtsp(s)`, `rtmp(s)`, `rtp` et `udp` sont gardées. Un chemin local n'est accepté que si la liste elle-même est un fichier du serveur, choisi par l'administrateur.
- ffmpeg et ffprobe reçoivent `-protocol_whitelist` limité aux protocoles de l'adresse.
- Logos : http(s) seulement. Guide indiqué par la liste : http(s) seulement.
- Une adresse refusée qui serait déjà en base l'est aussi au moment de la lecture.

### 2. Contrôle parental contourné par un profil ouvert
**Constat.** Les profils gérés sans PIN s'ouvrent sans mot de passe depuis « Qui regarde ? ». Un profil « enfant » pouvait donc passer sur un profil « invité » sans limite d'âge.
**Correctif** (`routes/account.py`). Depuis un profil limité par âge, on ne peut ouvrir sans code qu'un profil au moins aussi restreint : âge, contenus non classés, bibliothèques. Sinon, il faut le PIN, ou qu'un adulte ouvre le profil.

### 3. Webhook : sortie des bibliothèques
**Constat.** Le webhook compare la fin du chemin envoyé par Radarr/Sonarr aux dossiers des bibliothèques. Un chemin `/tv/../../etc` aboutissait à un dossier hors bibliothèque, que le scan partiel indexait. Il faut toutefois connaître le jeton secret.
**Correctif.** Les segments `.` et `..` sont ignorés, et le dossier trouvé doit rester (après résolution des liens) sous la racine de la bibliothèque (`routes/admin.py`). Le scanner normalise aussi les chemins du scan partiel (`scanner.py`).

### 4. Fichier piégé dans la médiathèque
**Constat.** ffmpeg reconnaît les formats par leur contenu. Un « film » qui est en réalité une playlist HLS ou une liste `concat` lui fait ouvrir les adresses qu'elle contient : fichiers du serveur, ou adresses du réseau local.
**Correctif** (`media.py`, `transcoder.py`, `subcache.py`, `introdetect.py`). Toutes les lectures de fichiers des bibliothèques passent `-protocol_whitelist file,pipe` (aucun accès réseau). L'analyse refuse les formats `hls`, `concat` et `tee` : un tel fichier n'est jamais indexé ni lu.

### 5. Déni de service par connexions lentes
**Constat.** Le serveur n'avait aucun délai de lecture ni d'écriture et créait un fil par connexion, sans limite. Quelques connexions ouvertes sans rien envoyer (« slowloris ») suffisaient à l'épuiser.
**Correctif** (`web.py`). Une connexion inactive est fermée au bout de 120 s, et le nombre de connexions simultanées est plafonné à 256. Les longues attentes côté serveur (préparation d'un segment) ne sont pas concernées : seul le silence réseau compte.

### 6. `X-Forwarded-For`
**Constat.** Avec `SEANCE_TRUST_PROXY=1`, l'adresse du client était lue en **première** position. Or nginx et Caddy **ajoutent** l'adresse réelle à la fin de ce qu'envoie le client. En changeant cet en-tête à chaque essai, on échappait au blocage après 8 mots de passe erronés.
**Correctif.** On lit la **dernière** adresse, celle qu'ajoute le reverse proxy.

### 7 et 8. Entrées mal bornées
- Les listes et guides compressés (réponse HTTP ou fichier `.gz`) sont décompressés avec une limite de taille ; au-delà, l'actualisation s'arrête avec une erreur.
- Un `Content-Length` négatif est refusé (400). Un JSON trop imbriqué donne une erreur 400 au lieu d'une erreur interne.

### 9. Événements « Regarder ensemble »
- Rejoindre un groupe passe obligatoirement par `POST …/join`, qu'un autre site ne peut pas envoyer à cause de l'en-tête anti-CSRF. Ouvrir le flux d'événements sans être membre renvoie 403.
- Chaque personne a au plus 6 flux ouverts en même temps (onglets, appareils) : chaque flux mobilise une connexion du serveur.

### 10. hls.js
Le lecteur hls.js 1.5.15 est désormais **fourni dans le dépôt** (`static/vendor/hls.min.js`, licence Apache 2.0 à côté), avec son empreinte SHA-256. Le script d'installation la vérifie et s'arrête si le fichier a été modifié. Il ne télécharge plus rien depuis un CDN.

### 11 à 14
- **Code d'installation :** 8 essais par adresse, puis blocage temporaire, comme pour la connexion.
- **Images de collection :** une collection dont tous les titres sont masqués pour ce profil renvoie 404 aussi pour ses images.
- **Logos SVG** (relais d'images TMDB) : servis avec `Content-Security-Policy: sandbox`. Ouverts directement, ils ne peuvent exécuter aucun script.
- **Partages SMB :** montés avec `nosuid,nodev,noexec`. Au démarrage, `seance-mountd` ajoute ces options aux partages créés par une version précédente ; elles s'appliquent au prochain montage (*Remonter*, ou au redémarrage).

### 15. Durcissement
- **En-têtes ajoutés :** `Cross-Origin-Opener-Policy`, `Permissions-Policy` (caméra, micro, localisation… désactivés), `object-src 'none'` dans la CSP, et `Strict-Transport-Security` quand `SEANCE_COOKIE_SECURE=1`.
- **Service systemd :** plus aucune capacité Linux, familles de sockets limitées, pas de nouveaux espaces de noms ni de temps réel, horloge, nom d'hôte et journaux du noyau protégés. La version allégée pour les conteneurs LXC sans « nesting » relâche les options incompatibles.

## Points vérifiés sans problème

- **Mots de passe et codes PIN** hachés avec scrypt (sel aléatoire). Comparaisons à temps constant. Durée identique qu'un identifiant existe ou non. Blocage après 8 échecs par adresse et par identifiant.
- **Sessions :** jeton aléatoire de 256 bits, stocké **haché** en base. Cookie `HttpOnly`, `SameSite=Lax`, `Secure` derrière HTTPS. Sessions supprimées quand le mot de passe change ou que le compte est désactivé. Les droits sont relus en base à chaque requête.
- **CSRF :** toute requête autre que GET exige l'en-tête `X-Seance`, qu'un autre site ne peut pas ajouter sans autorisation CORS (jamais accordée).
- **Contrôle d'accès :** chaque fiche, image, flux, sous-titre, chapitre, playlist et collection vérifie la bibliothèque et la restriction d'âge. Les flux de lecture sont liés à leur propriétaire. Les routes d'administration sont réservées aux administrateurs, sans élévation possible depuis un compte ordinaire.
- **Injections :** toutes les requêtes SQL sont paramétrées (les seuls fragments dynamiques sont des noms de colonnes écrits dans le code). Toutes les commandes (ffmpeg, mount, smbclient) sont lancées sans shell, avec des arguments séparés.
- **Fichiers :** fichiers statiques confinés au dossier `static/` (chemin résolu). Noms de polices, segments et images validés par expression régulière.
- **XSS :** l'interface n'insère jamais de HTML venant des données (`textContent` uniquement). La CSP interdit les scripts en ligne et étrangers ; seul le moteur de sous-titres (WebAssembly), dans son dossier, a besoin de `wasm-unsafe-eval`. `X-Frame-Options: DENY`.
- **Service de montage (root) :** socket Unix réservée à l'utilisateur `seance` (vérifiée par `SO_PEERCRED`). Chaque paramètre est revalidé. Mots de passe dans des fichiers 0600, jamais dans la base ni sur la ligne de commande. Lignes fstab échappées. Montage en lecture seule.
- **Données :** dossier `/var/lib/seance` en 0700, `UMask=0077`, setup-code.txt en 0600.

## Risques acceptés et recommandations

- **Exposition sur Internet :** passer impérativement par un reverse proxy HTTPS avec `SEANCE_COOKIE_SECURE=1` et `SEANCE_TRUST_PROXY=1` (voir le README). Ne pas activer `SEANCE_TRUST_PROXY` si Séance est joignable directement : l'en-tête serait alors inventable.
- **Blocage après échecs :** quelqu'un peut bloquer pendant 10 minutes la connexion d'un identifiant connu. C'est le compromis habituel face aux essais de mots de passe.
- **IPTV et réseau local :** une liste en http(s) peut désigner une adresse du réseau local. C'est voulu (box opérateur, tuner TNT réseau). N'ajoute que des listes de fournisseurs de confiance.
- **`seance-mountd` tourne en root**, sans bac à sable : monter un partage l'exige, et un bac à sable rendrait les montages invisibles à Séance. Sa surface est réduite au strict nécessaire.
- **Liens symboliques** dans les bibliothèques : suivis par le scanner. Un lien vers un dossier système rendrait ses vidéos visibles, si l'utilisateur `seance` peut les lire.
- **Clés TMDB et Seerr** stockées en clair dans la base (dossier lisible par `seance` seulement), puisqu'il faut les renvoyer aux services.
- **Messages d'erreur de ffmpeg :** montrés lors d'un échec de lecture, ils peuvent révéler un chemin de fichier à un membre du foyer.
- **Groupes « Regarder ensemble » :** visibles par tout le foyer. Le titre d'un contenu non autorisé n'est jamais montré.
