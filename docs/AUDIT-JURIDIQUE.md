# Audit juridique et de faisabilité de Nexis

*27 octobre 2026, version 3.19.0. Analyse des risques juridiques liés à l'outil, du point de vue de son auteur (qui le publie) et de ses utilisateurs (qui l'installent chez eux), en droit français et européen. **Ce document n'est pas une consultation juridique** : pour un projet qui deviendrait public ou commercial, faire valider les points marqués « à faire » par un avocat en propriété intellectuelle.*

## Conclusion

**Nexis est juridiquement faisable** tel qu'il est conçu : un serveur multimédia **auto-hébergé, gratuit, sans compte externe ni télémétrie**, qui lit les fichiers que son propriétaire lui indique. C'est la même catégorie que Plex, Jellyfin, Emby ou Kodi, outils légaux et largement diffusés. Le logiciel ne contient aucune œuvre protégée, ne contourne aucune protection (DRM) et ne télécharge rien de lui-même.

Les risques réels sont ailleurs, et la plupart se corrigent facilement :

| # | Risque | Pour qui | Niveau | État |
|---|---|---|---|---|
| 1 | Nom **« Nexis »** : marque de LexisNexis | auteur | **Élevé si le projet grandit** | À faire : recherche d'antériorité, sans doute renommer |
| 2 | **Aucune licence** dans le dépôt | auteur et utilisateurs | **Élevé** | À faire : choisir une licence |
| 3 | Attribution exigée par **TMDB** (et **JustWatch**) absente | auteur / administrateurs | Moyen | **Corrigé** (« À propos », pied de page de Découvrir) |
| 4 | Mentions de licences des composants tiers incomplètes (APK, image Docker, police) | auteur | Faible à moyen | Police **corrigée** ; reste APK et Docker |
| 5 | **Usages illicites** possibles (IPTV pirate, partage hors du foyer, Radarr/Sonarr avec des torrents, copie de disques protégés) | utilisateurs (et image de l'auteur) | Moyen | Avertissement **ajouté** ; documentation à garder neutre |
| 6 | **RGPD** si le serveur est ouvert à des personnes hors du foyer | administrateurs | Faible à moyen | Recommandations |
| 7 | Brevets des codecs (H.264, HEVC, AAC) | auteur (image Docker) | Faible | Accepté (non commercial) |

## 1. Le logiciel lui-même : est-il légal de le publier ?

**Oui.** Le droit français sanctionne l'édition d'un logiciel « *manifestement destiné à la mise à disposition du public non autorisée d'œuvres* » (art. L335-2-1 du Code de la propriété intellectuelle, issu de la loi DADVSI : jusqu'à 3 ans et 300 000 €), ainsi que le fait d'inciter sciemment à l'utiliser. Nexis n'entre pas dans cette définition :
- il sert une médiathèque **privée**, derrière une authentification, sans annuaire public ni partage pair-à-pair ;
- il ne fournit aucune source de contenus : ni catalogue, ni indexeur, ni liste IPTV ;
- il ne contourne aucune mesure technique de protection (art. L335-3-1) : il lit des fichiers déjà présents.

**Ce qui pourrait faire basculer l'appréciation, c'est la présentation.** Un juge regarde la finalité « manifeste » : documentation, captures, exemples. Points de vigilance :
- **Découvrir** affiche les catalogues de Netflix, Prime Video, Disney+… avec un bouton « Demander » relié à Seerr, et Nexis s'interface avec Radarr/Sonarr, outils souvent configurés avec des sites de torrents. Ces intégrations sont neutres (Seerr et les *arr sont eux-mêmes légaux et publics), mais **ni le README ni l'interface ne doivent suggérer de les utiliser pour obtenir des copies non autorisées**. La rédaction actuelle est neutre : la garder ainsi.
- **TV en direct** : le champ d'exemple dit « Fournisseur IPTV, box TV… ». Rester sur des sources licites (box opérateur, tuner TNT réseau, chaînes gratuites) dans la documentation ; ne jamais citer de fournisseur IPTV « tout compris ».
- Les noms et logos des plateformes sont utilisés pour indiquer où un titre est disponible (usage référentiel, admis) ; les logos viennent de TMDB.

## 2. Les utilisateurs : ce qui reste de leur responsabilité

Nexis est un outil ; c'est l'usage qui peut être illicite. L'interface rappelle désormais, dans « À propos », de n'utiliser que des contenus dont on détient les droits, dans le cadre privé du foyer. Les cas à connaître :

- **Copie privée** (art. L122-5 2°) : elle suppose une **source licite** (CJUE, *ACI Adam*, 10 avril 2014, C-435/12). Un film téléchargé illégalement reste illicite même s'il n'est regardé qu'à la maison.
- **DVD et Blu-ray du commerce** : leurs protections (CSS, AACS) ne peuvent pas être contournées, même pour une copie personnelle (art. L335-3-1). Nexis n'y est pour rien, mais une médiathèque issue de disques « rippés » est juridiquement fragile.
- **Partage hors du foyer** : la représentation n'est libre que si elle est « *privée et gratuite, effectuée exclusivement dans un cercle de famille* » (art. L122-5 1°). Créer des comptes pour des amis, ou « Regarder ensemble » avec des personnes extérieures au foyer, sort de cette exception pour des œuvres protégées.
- **IPTV** : un abonnement IPTV pirate expose son utilisateur (contrefaçon, recel), et l'Arcom bloque ces services. Rediffuser le flux de sa box opérateur à d'autres foyers viole en plus les conditions de l'opérateur.
- **Radarr/Sonarr + torrents** : le téléchargement en pair-à-pair d'œuvres protégées relève de la procédure de réponse graduée de l'Arcom (ex-Hadopi), indépendamment de Nexis.

## 3. Licence du projet — **à faire**

**Le dépôt ne contient aucun fichier de licence.** Par défaut, tous les droits sont réservés à l'auteur : en toute rigueur, personne n'a le droit de copier, modifier ou redistribuer Nexis — ni même de l'installer en dehors de ce que l'auteur autorise implicitement. Cela pose aussi problème pour les contributions extérieures (à qui appartient le code proposé ?) et pour la garantie (aucune clause n'exclut la responsabilité de l'auteur).

Choisir et ajouter un fichier `LICENSE` :
- **MIT** ou **Apache 2.0** : permissives, compatibles avec tous les composants actuels. Apache 2.0 ajoute une licence de brevets.
- **AGPL 3.0** : oblige quiconque modifie Nexis et le propose à d'autres en ligne à publier ses modifications. Compatible avec les composants actuels (MIT, Apache 2.0, LGPL, OFL).

Les deux options limitent la responsabilité de l'auteur (« fourni tel quel, sans garantie »). C'est une décision d'auteur : elle n'a pas été prise à votre place.

## 4. Le nom « Nexis » — **à faire**

**NEXIS** est une marque de **LexisNexis** (RELX), exploitée depuis des décennies pour un service de bases de données et de veille en ligne, enregistrée notamment aux États-Unis et dans l'Union européenne pour des logiciels et services informatiques. Un logiciel nommé « Nexis » dans la même classe (logiciels, services en ligne) peut faire l'objet d'une mise en demeure, d'un retrait d'une boutique d'applications ou d'une action en contrefaçon de marque, même s'il est gratuit, si le projet devient visible.

- Faire une recherche d'antériorité sur **TMview** (EUIPO), la base de l'**INPI** et **WIPO Global Brand Database**, classes 9, 38, 41 et 42.
- Tant que le projet reste confidentiel, le risque est faible ; **avant toute diffusion large** (publication sur une boutique, site, communication), envisager un autre nom. Le nom interne « Seance » (paquet Python, service systemd) pose la même question à vérifier.
- L'identifiant de l'appli Android (`fr.nexis.tv`) devra suivre le nom retenu.

## 5. Services tiers utilisés

### TMDB (métadonnées, affiches, catalogues) — **corrigé**
Chaque administrateur utilise **sa propre clé API**, gratuite pour un usage non commercial : c'est lui qui accepte les conditions de TMDB. Ces conditions imposent d'**indiquer la source** : « *This product uses the TMDB API but is not endorsed or certified by TMDB* », avec le logo TMDB, dans une page de crédits. Les données de disponibilité sur les plateformes (« Sur Netflix »…) viennent de **JustWatch** via TMDB, qui exige aussi d'être cité.

**Correctif** : une fenêtre **« À propos »** (menu du profil) et le bas de la page **Découvrir** affichent ces mentions (en français ou en anglais). **Reste à faire** : ajouter le logo officiel de TMDB à côté de la mention (fichier à prendre sur leur site, selon leurs règles d'usage). Un usage commercial de Nexis nécessiterait une licence commerciale TMDB.

### YouTube (bandes-annonces)
Lecteur intégré `youtube-nocookie.com`, chargé **seulement au clic** : usage prévu par YouTube. Le navigateur de l'utilisateur contacte alors Google (adresse IP).

### Seerr, Radarr, Sonarr
Logiciels tiers libres, installés et configurés par l'administrateur. Nexis transmet à Seerr le titre demandé et le nom du compte. Aucune difficulté juridique propre.

### GitHub (vérification des mises à jour)
Le serveur interroge GitHub toutes les 6 heures (adresse IP du serveur). Pas de donnée personnelle.

## 6. Licences des composants fournis

| Composant | Licence | Obligation | État |
|---|---|---|---|
| hls.js | Apache 2.0 | Joindre la licence | Fait (`static/vendor/hls.js-LICENSE`) |
| qrcode-generator | MIT | Joindre la licence | Fait |
| libpgs | MIT | Joindre la licence | Fait |
| JASSUB (+ libass, FreeType, HarfBuzz, FriBidi, fontconfig, expat, brotli) | MIT, ISC, FTL, **LGPL 2.1** (FriBidi)… | Joindre les licences ; pour la partie LGPL, permettre d'obtenir le code source et de remplacer la bibliothèque | Licences jointes (`COPYRIGHT`). **À faire** : noter la version exacte de JASSUB et le lien vers ses sources dans le dossier |
| Inter, Outfit, Liberation Sans | SIL OFL 1.1 | Joindre la licence, ne pas vendre la police seule | Inter, Outfit : fait ; Liberation Sans : **ajouté** |
| AndroidX Media3, RecyclerView (appli TV) | Apache 2.0 | Joindre la licence aux copies distribuées | **À faire** : écran « Licences » dans l'appli, ou fichier de notices dans l'APK |
| ffmpeg, pilotes VAAPI (image Docker) | GPL/LGPL | Proposer le code source correspondant | **À faire** : une ligne dans le README indiquant que les sources sont celles des paquets Ubuntu 24.04 (`apt-get source ffmpeg`) |

Sur l'installation classique (script ou `.deb`), ffmpeg est installé par le gestionnaire de paquets de la distribution : Nexis ne le redistribue pas.

**Brevets des codecs.** H.264, HEVC et AAC sont couverts par des brevets gérés par des groupements (Via LA, Access Advance). Leurs licences visent les produits commerciaux ; la portée des brevets logiciels est limitée en Europe. Risque faible pour un projet gratuit qui s'appuie sur le ffmpeg des distributions.

## 7. Données personnelles (RGPD)

Nexis enregistre des données personnelles : comptes, photos de profil, **historique de visionnage** (qui peut révéler des informations sensibles), adresses IP et navigateurs des sessions, secrets de double authentification.

- **Usage dans le foyer** : le RGPD ne s'applique pas aux traitements « *effectués par une personne physique dans le cadre d'une activité strictement personnelle ou domestique* » (art. 2.2.c). Un serveur familial est dans ce cas.
- **Serveur ouvert à des amis ou à d'autres foyers** : l'exception s'interprète strictement (CJUE, *Ryneš*, C-212/13). L'administrateur peut alors devenir responsable de traitement : informer les utilisateurs (ce qui est enregistré, pour combien de temps), leur permettre de supprimer leurs données (la suppression d'un compte efface son historique : c'est déjà le cas) et de les **exporter** (absent).
- **L'auteur** n'est pas responsable de traitement : Nexis n'envoie aucune donnée d'utilisation au projet (pas de télémétrie). C'est un atout à préserver.
- **Cookies** : un seul cookie, celui de la session, indispensable au service : pas de bandeau de consentement nécessaire (lignes directrices de la CNIL). Les préférences locales (langue, appareil) relèvent de la même exemption.

Recommandations pour un usage au-delà du foyer : une durée de conservation réglable pour l'historique, un export des données d'un compte, et une courte page d'information.

## 8. Distribution de l'appli Android

L'APK est publié sur GitHub et installé à la main : pas de contrainte de boutique. Pour une publication sur **Google Play**, prévoir : les règles de Google sur les applis qui facilitent l'accès à des contenus sans autorisation (la TV en direct par listes M3U est souvent examinée de près), une politique de confidentialité, et un nom sans conflit de marque (§ 4).

## 9. Langues de l'interface

La version anglaise ajoutée en 3.19.0 ne change rien à cette analyse. Les mentions TMDB, JustWatch et l'avertissement d'usage existent dans les deux langues.

## À faire, par priorité

1. **Ajouter une licence** au dépôt (§ 3).
2. **Vérifier la marque « Nexis »** et, selon le résultat, choisir un autre nom avant toute diffusion large (§ 4).
3. Ajouter le logo TMDB à la mention « À propos » (§ 5).
4. Notices de licences dans l'APK ; sources de ffmpeg pour l'image Docker ; version de JASSUB (§ 6).
5. Garder une documentation neutre sur l'IPTV, Seerr et Radarr/Sonarr (§ 1).
6. Si le serveur accueille des personnes hors du foyer : information, conservation et export des données (§ 7).
