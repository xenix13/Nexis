# Audit de sécurité de Nexis — version 3.19.0

*27 octobre 2026. Suite de l'audit de la version 3.1.0 (`docs/AUDIT-SECURITE.md`), qui reste valable pour ce qu'il couvre. Celui-ci porte sur ce qui a changé depuis (versions 3.2 à 3.18 : double authentification, appli Android TV, téléchargements hors ligne, Découvrir, canal de mise à jour…) et sur deux questions précises : **les bibliothèques sont-elles bien en lecture seule ?** et **les composants tiers sont-ils sûrs ?** Revue du code (serveur, interface web, appli TV, scripts d'installation, CI) ; les correctifs appliqués ont un test dans `tests/test_security.py`.*

## Résumé

| # | Gravité | Domaine | Constat | État |
|---|---|---|---|---|
| 1 | **Moyenne** | Mise à jour | Le bouton « Mettre à jour » exécute **en root** `curl …/install.sh \| bash` depuis la **tête de branche** GitHub, sans signature ni version figée | Recommandation |
| 2 | Moyenne | Bibliothèques | Dans un conteneur LXC sans « nesting » (service « allégé »), plus rien n'empêche le service d'écrire dans les dossiers de médias si les droits Unix le permettent | Signalé dans l'interface + recommandation |
| 3 | Faible | Composants tiers | JASSUB (moteur de sous-titres, WebAssembly) et libpgs n'étaient pas vérifiés à l'installation, contrairement à hls.js et qrcode | **Corrigé** |
| 4 | Faible | Composants tiers | Police Liberation Sans fournie sans son texte de licence | **Corrigé** |
| 5 | Faible | Chaîne de publication | Actions GitHub référencées par étiquette (`@v5`) et non par empreinte, avec droits d'écriture sur les publications et les images | Recommandation |
| 6 | Faible | Appli TV | Cookie de session joint à toute adresse d'image, y compris extérieure ; trafic HTTP en clair autorisé | **Corrigé** (images) / accepté (HTTP local) |
| 7 | Faible | Comptes | Aucune liste des sessions ouvertes ni déconnexion à distance (seul le changement de mot de passe les ferme) | Recommandation |
| 8 | Info | Exposition | `/api/status` donne la version sans connexion ; jeton du webhook dans l'adresse | Accepté |

Aucune faille critique (exécution de code à distance, contournement de l'authentification, injection) n'a été trouvée. Les mécanismes vérifiés en 3.1.0 (mots de passe scrypt, sessions hachées, CSRF par en-tête, requêtes SQL paramétrées, commandes sans shell, `textContent` partout, CSP) sont toujours en place ; les deux seuls `innerHTML` (`settings.js`) n'insèrent que des dessins écrits dans le code.

## 1. Mise à jour depuis l'interface

**Constat.** `seance-mountd` (root) lance `curl https://raw.githubusercontent.com/<dépôt>/<branche>/deploy/install.sh | bash`, qui clone la **dernière révision** de la branche (`main`, ou `test` si ce canal est choisi). Aucune signature, aucune empreinte, aucune version figée : le code exécuté en root est celui qui se trouve sur GitHub au moment du clic. L'entrée de l'interface est bien filtrée (ni adresse ni commande ne viennent de Nexis), mais la **confiance repose entièrement sur le compte GitHub** du projet.

**Risque.** Quiconque obtient le droit de pousser sur le dépôt (vol du mot de passe ou d'un jeton, application GitHub trop permissive, action CI compromise) obtient root sur toutes les installations dont l'administrateur clique sur « Mettre à jour ». Le canal *Test* exécute en plus du code non validé. Probabilité faible, impact maximal.

**Recommandations** (par ordre d'effort) :
1. Sur GitHub : double authentification obligatoire, protection de `main` (pas de poussée directe, relecture obligatoire), jetons à durée limitée.
2. Ne mettre à jour que vers des **versions publiées** (étiquettes `v3.x.y`) plutôt que la tête de branche.
3. Signer les étiquettes (`git tag -s`) ou publier un fichier d'empreintes signé avec chaque version, et faire vérifier la signature par `install.sh` avec une clé publique installée une fois pour toutes.

## 2. Les bibliothèques sont-elles en lecture seule ?

**Dans le code : oui.** Toutes les écritures de Nexis vont dans son dossier de données (`/var/lib/seance` : base, caches d'images, sous-titres extraits, vignettes, empreintes audio, transcodages, téléchargements préparés, avatars). Aucune fonction ne modifie, ne renomme ni ne supprime un fichier de bibliothèque : la tâche « Nettoyer les caches » ne parcourt que le dossier de données ; supprimer une bibliothèque ou un partage dans l'interface ne touche pas aux fichiers ; les `.nfo`, affiches et sous-titres externes ne sont que lus.

**Par le système : oui, sauf dans un cas.**

| Installation | Garantie |
|---|---|
| Service systemd standard | `ProtectSystem=strict` + `ReadWritePaths=/var/lib/seance` : tout le reste du système, `/mnt` et `/media` compris, est **monté en lecture seule pour le service**. Même un Nexis compromis ne pourrait pas modifier les médias. |
| Partages SMB montés par Nexis | Montés `ro,nosuid,nodev,noexec`. |
| Docker | Le `docker-compose.yml` d'exemple monte les médias en `:ro`. Si l'utilisateur oublie `:ro`, seuls les droits Unix protègent. |
| **LXC sans « nesting »** (version allégée du service) | `ProtectSystem=false` : le bac à sable est retiré. Si les dossiers de médias appartiennent à `seance` ou lui sont ouverts en écriture (point de montage Proxmox sans `ro=1`, `chmod 777`…), un Nexis compromis pourrait modifier ou supprimer les médias. |

**Correctif.** La page *Paramètres → Bibliothèques* signale désormais tout dossier dans lequel le service peut écrire (« Nexis peut écrire dans ce dossier : par précaution, montez-le en lecture seule »). Test : `test_writable_library_is_flagged`.

**Recommandations.** Points de montage Proxmox avec `ro=1` (`mp0: /tank/films,mp=/mnt/films,ro=1`), volumes Docker en `:ro`, ou fichiers appartenant à un autre utilisateur avec des droits `755/644`. Ne jamais placer une bibliothèque sous `/var/lib/seance`.

## 3. Composants tiers

| Composant | Licence | Où | Vérification |
|---|---|---|---|
| Python (bibliothèque standard uniquement) | PSF | serveur | **Aucune dépendance Python** : pas de risque de paquet PyPI piégé |
| ffmpeg / ffprobe | LGPL/GPL | paquet de la distribution | Mis à jour par `apt` |
| hls.js 1.5.15 | Apache 2.0 | `static/vendor` | Empreinte SHA-256 vérifiée à l'installation |
| qrcode-generator 2.0.4 | MIT | `static/vendor/qrcode` | Empreinte vérifiée |
| JASSUB (libass, FreeType, HarfBuzz, FriBidi… en WebAssembly) | MIT + licences des projets inclus | `static/vendor/jassub` | **Empreinte ajoutée** (`jassub.sha256`) |
| libpgs | MIT | `static/vendor/libpgs` | **Empreinte ajoutée** (`libpgs.sha256`) |
| Polices Inter, Outfit, Liberation Sans | SIL OFL 1.1 | `static/fonts`, `static/vendor/jassub` | Licence de Liberation Sans **ajoutée** |
| AndroidX Media3 1.4.1, RecyclerView 1.3.2 | Apache 2.0 | appli TV (Maven Google) | Versions figées, sans vérification d'empreintes Gradle |

Les fichiers d'empreintes sont dans le même dépôt que les bibliothèques : ils détectent une copie abîmée ou modifiée sur le serveur, pas un dépôt compromis (voir § 1). Test : `test_vendored_libraries_checksums`.

**Points d'attention.**
- **ffmpeg est la principale surface d'attaque** : il analyse des fichiers potentiellement hostiles. Les protections existantes (utilisateur sans privilèges, bac à sable systemd, `-protocol_whitelist file,pipe`, formats `hls/concat/tee` refusés) limitent fortement l'impact, mais il faut que ffmpeg soit à jour : activer `unattended-upgrades` sur le conteneur ou la VM, et reconstruire régulièrement l'image Docker.
- JASSUB et libpgs ne sont pas datés dans le dépôt : noter leur version et vérifier leurs nouvelles versions de temps en temps. hls.js 1.5.15 n'a pas de faille connue à cette date ; la branche 1.6 est disponible.
- Appli TV : activer la vérification des dépendances Gradle (`gradle/verification-metadata.xml`).
- Les bibliothèques tierces tournent dans le navigateur sous une CSP stricte ; seul le moteur de sous-titres a besoin de `wasm-unsafe-eval`, limité à son dossier.

## 4. Chaîne de publication (GitHub Actions)

Les actions tierces (`docker/build-push-action@v6`, `gradle/actions/setup-gradle@v4`…) sont référencées par **étiquette mobile** dans un workflow qui a `contents: write` et `packages: write`. Si l'une d'elles était compromise, elle pourrait publier une image Docker ou un `.deb` piégé. **Recommandation :** les référencer par empreinte de commit (`uses: docker/build-push-action@<sha>  # v6`), et laisser Dependabot les mettre à jour.

## 5. Appli Android TV

- **Cookie de session** : il était joint à toute image demandée, même à une adresse extérieure. **Corrigé** : seulement vers le serveur Nexis (`Images.java`). Le lecteur ExoPlayer l'envoie à toutes ses requêtes ; elles ne visent que le serveur aujourd'hui, mais `setAllowCrossProtocolRedirects(true)` permettrait une redirection HTTPS → HTTP : à désactiver si Nexis est exposé en HTTPS.
- **HTTP en clair** autorisé (`usesCleartextTraffic`) : nécessaire pour un serveur local en `http://192.168.x.x`. Hors du réseau local, passer par HTTPS.
- Sauvegarde Android désactivée (`allowBackup=false`) : le cookie ne part pas dans les sauvegardes Google. Bien.
- Langue : l'appli envoie `X-Lang` pour recevoir les messages du serveur dans sa langue.

## 6. Autres constats

- **Sessions (faible).** Durée 30 jours, sans liste des appareils connectés ni bouton « déconnecter les autres appareils » (seuls un changement de mot de passe ou l'activation de la double authentification les ferment). Recommandation : page « Appareils connectés ».
- **Changement de mot de passe (faible).** La vérification du mot de passe actuel n'est pas limitée en nombre d'essais (il faut déjà une session ouverte). Recommandation : réutiliser la limitation de la connexion.
- **Politique de mot de passe (faible).** 8 caractères minimum, sans liste de mots de passe courants. Acceptable pour un foyer ; à renforcer pour un serveur exposé.
- **Relais d'images TMDB (info).** Tout utilisateur connecté peut faire télécharger au serveur des images TMDB (cache plafonné à 4 000 fichiers). Acceptable.
- **Version visible sans connexion (info).** `/api/status` indique la version (utile à l'interface et à l'appli TV). Un attaquant sait donc si le serveur est à jour : raison de plus pour mettre à jour.
- **Webhook (info).** Le jeton est dans l'adresse (journaux de Radarr/Sonarr ou d'un proxy). Régénérable dans l'interface.
- **Langues (nouveau, 3.19.0).** L'en-tête `X-Lang` et la langue du compte ne servent qu'à choisir un texte dans un dictionnaire figé : valeur filtrée (`fr`/`en`), jamais insérée dans du HTML ni du SQL.

## 7. Serveurs liés (nouveau, 3.20.0)

- **Liaison.** TLS 1.2 minimum sur un port dédié (8421), certificat propre à chaque serveur épinglé par l'autre à l'invitation (empreinte SHA-256 dans l'invitation, vérifiée avant tout envoi), puis secret partagé de 256 bits présenté à chaque requête et comparé en temps constant. Une invitation est à usage unique, valable 15 minutes, stockée sous forme d'empreinte ; les essais de jetons sont limités comme les connexions.
- **Surface.** Le port ne sert ni l'interface ni les routes des comptes. Au nom d'un compte, un serveur lié n'atteint que la lecture : lancer, lire, sous-titres, aperçus, progression. Toute autre route est refusée (403), et les routes entre serveurs sont introuvables sur le port de l'interface. Test : `tests/test_federation.py`.
- **Confiance.** Les empreintes des mots de passe, les secrets de double authentification et les codes de secours sont répliqués sur chaque serveur lié : la compromission de l'un expose les comptes de tous. **Ne lier que des serveurs dont vous êtes l'administrateur.**
- **Secrets.** La clé privée est dans `/var/lib/seance/federation` (droits 600) et le secret partagé dans la base (dossier en 700). Délier un serveur supprime son secret et ses bibliothèques recopiées.
- **Services prêtés (3.24.0).** Un serveur qui a une clé TMDB ou Seerr les prête aux serveurs liés sans transmettre la clé : il relaie lui-même les requêtes. TMDB : chemins de lecture seulement (`/movie`, `/tv`, `/search`, `/discover`…, sans `..`). Seerr : statuts (`GET /media`), comptes (`GET /user`) et nouvelles demandes (`POST /request`) ; toute autre route est refusée (403), notamment les réglages de Seerr. Un serveur lié peut faire une demande au nom de n'importe quel compte Seerr, comme le ferait le serveur lui-même.
- **Regarder ensemble entre serveurs (3.24.0).** Les routes `/fed/together/…` agissent au nom du compte (répliqué) indiqué par le serveur lié. Elles ne donnent accès qu'aux groupes, et l'accès au titre (bibliothèques, restriction d'âge) est vérifié sur le serveur du groupe.

## 8. Serveur DLNA et photothèque (nouveau, 3.25.0)

- **DLNA sans comptes.** Le protocole ne prévoit aucune authentification : quand le service est activé, tout appareil du réseau local peut parcourir et lire les bibliothèques partagées, sans contrôle parental. Le service est donc **désactivé par défaut**, limité aux bibliothèques choisies par l'administrateur, et refuse toute adresse qui n'est pas locale (privée, boucle locale, lien local), y compris derrière un reverse proxy (403). Les routes `/dlna/` ne servent que les fichiers des bibliothèques partagées, désignés par leur numéro : aucun chemin n'est accepté du client.
- **Événements UPnP.** L'état initial envoyé à l'abonnement ne part que vers l'adresse de l'appareil qui s'abonne (pas de requête vers une adresse tierce).
- **SOAP.** Les corps de requête sont limités à 64 Ko et lus avec le parseur XML de la bibliothèque standard (expat protège des entités récursives).
- **Photothèque.** Lecture seule : les miniatures et images redimensionnées vont dans le cache de Nexis. L'accès aux photos (vignettes, visionneuse, fichier d'origine) vérifie l'accès du compte à la bibliothèque. Les photothèques ne sont pas recopiées chez les serveurs liés.

## 9. Télécommande, notifications push, albums partagés (nouveau, 3.29.0)

- **Télécommande.** Un appareil n'est visible et pilotable que par le compte qui l'utilise. Les commandes font partie d'une liste fermée (lecture, pause, position, volume, pistes, vitesse, arrêt), avec des valeurs courtes.
- **Notifications push.** Le serveur n'appelle que les services push des navigateurs (Google, Mozilla, Apple, Microsoft), en HTTPS et sans port ni identifiants dans l'adresse : une adresse d'abonnement arbitraire ne peut pas lui faire appeler une machine du réseau local. Les messages sont vides ; l'appareil réveillé demande leur contenu avec sa session. La clé VAPID est créée par openssl dans `push/` (droits 600).
- **Albums partagés par lien.** Jeton aléatoire de 144 bits, durée de 1 à 90 jours, révocable. Seuls l'album et ses sous-albums sont accessibles, vérifiés à chaque photo, et les essais de jetons au hasard sont freinés comme les mots de passe. Le reste de l'API demande toujours un compte.

## 10. Diffusion sur une TV (nouveau, 3.30.0)

- **Flux lisible sans session.** Un flux lancé pour une TV (Chromecast, AirPlay) est servi à quiconque connaît son adresse : la TV la demande sans le cookie du navigateur. Cette adresse contient l'identifiant aléatoire du flux (96 bits) et cesse de fonctionner à l'arrêt de la lecture. Les autres flux demandent toujours la session de leur propriétaire. Ces flux portent `Access-Control-Allow-Origin: *`, sans cookies, pour le lecteur du Chromecast.
- **Script tiers.** La politique de sécurité du contenu autorise `https://www.gstatic.com` dans `script-src` pour le SDK Cast de Google. Ce SDK n'est chargé que dans Chrome et Edge, à l'ouverture du lecteur. C'est le seul code tiers de l'interface.

## 11. Sauvegardes, services externes, podcasts (nouveau, 3.32.0 à 3.37.0)

- **Archives de sauvegarde.** Elles contiennent les empreintes des mots de passe, les secrets de double authentification, l'identité des serveurs liés et les jetons des services reliés : droits 600, téléchargement réservé aux administrateurs. À la restauration, l'archive est refusée si elle contient autre chose que la base et les dossiers attendus (pas de chemin absolu, pas de `..`, pas de lien), si sa base est plus récente que la version installée ou si l'intégrité de la base échoue. Elle n'est mise en place qu'au démarrage suivant ; l'état d'avant est gardé.
- **Identifiants de services.** Le mot de passe OpenSubtitles, la clé du service Whisper et le secret de l'application Trakt sont dans la base (nécessaires pour appeler ces services) et jamais renvoyés à l'interface. Les comptes Trakt sont reliés par code d'appareil (OAuth) : Nexis ne voit pas le mot de passe Trakt, seulement un jeton révocable, retiré quand on délie le compte.
- **Podcasts (SSRF).** Le serveur lit les flux RSS, l'audio et les images à la place des appareils. Une adresse donnée par un utilisateur ordinaire est refusée si elle désigne une adresse privée, de bouclage, locale au lien ou réservée, et chaque redirection est vérifiée de même. Seul un administrateur peut ajouter un flux du réseau local. Les images ne sont relayées que si elles appartiennent à un podcast connu ou à un résultat de recherche récent ; l'audio n'est relayé qu'aux abonnés du podcast, et seulement s'il s'annonce comme de l'audio.
- **Fichiers envoyés.** Archive de sauvegarde (8 Go au plus) et export Letterboxd (20 Mo au plus) sont lus par morceaux, sans passer par la limite des requêtes ordinaires (1 Mo) ; un envoi incomplet ferme la connexion.
- **Limites et temps d'écran.** Appliqués par le serveur au lancement de chaque lecture et à chaque compte rendu de lecture, pas seulement par l'interface.

## Recommandations par priorité

1. Sécuriser le compte GitHub et la branche `main` ; mettre à jour vers des versions publiées et signées (§ 1).
2. Monter les bibliothèques en lecture seule au niveau du système, surtout en LXC sans « nesting » (§ 2).
3. Garder ffmpeg à jour (`unattended-upgrades`, image Docker reconstruite) (§ 3).
4. Figer les actions GitHub par empreinte (§ 4).
5. Page « Appareils connectés » et limitation des essais sur le changement de mot de passe (§ 6).
