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

## Recommandations par priorité

1. Sécuriser le compte GitHub et la branche `main` ; mettre à jour vers des versions publiées et signées (§ 1).
2. Monter les bibliothèques en lecture seule au niveau du système, surtout en LXC sans « nesting » (§ 2).
3. Garder ffmpeg à jour (`unattended-upgrades`, image Docker reconstruite) (§ 3).
4. Figer les actions GitHub par empreinte (§ 4).
5. Page « Appareils connectés » et limitation des essais sur le changement de mot de passe (§ 6).
