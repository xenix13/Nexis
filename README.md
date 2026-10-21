# Séance

Un frontend web multi-utilisateurs pour **Plex Media Server**, pensé pour un serveur Plex **sans Plex Pass**.

Tu crées toi-même les comptes de ta famille ou de tes amis. Chacun a son espace : son historique, sa reprise de lecture, ses épisodes « à suivre », ses langues audio et sous-titres préférées, et sa présentation. Séance parle à Plex avec un seul jeton administrateur, qui ne quitte jamais le serveur.

- **Zéro dépendance** : Python 3 (bibliothèque standard uniquement) + un navigateur. Seul le lecteur vidéo [hls.js](https://github.com/video-dev/hls.js) est téléchargé à l'installation.
- **Hébergeable** dans un conteneur LXC ou une VM Debian/Ubuntu, sur la même machine que Plex ou à côté.

## Fonctionnalités (version 1)

| | |
|---|---|
| **Comptes** | Comptes propres à Séance (mots de passe hachés scrypt), administrateurs, désactivation, accès par bibliothèque |
| **Accueil** | « Reprendre et à suivre » par utilisateur, derniers ajouts par bibliothèque, bannière |
| **Bibliothèques** | Tri (titre, ajout, année, sortie, note), filtre par genre, défilement infini, recherche |
| **Fiches** | Films, séries, saisons, épisodes, distribution, pistes disponibles, marquer vu / non vu (épisode, saison, série) |
| **Lecteur** | Reprise là où on s'était arrêté, choix audio / sous-titres / qualité en cours de lecture, raccourcis clavier, plein écran |
| **Sous-titres** | SRT externes affichés par Séance (pas de transcodage) ; ASS/PGS/incrustés gravés par Plex |
| **Passer l'intro** | Bannière « Passer l'intro » et option « passer automatiquement », à partir de marqueurs (voir plus bas) |
| **Épisode suivant** | Carte avec compte à rebours en fin d'épisode, enchaînement automatique (désactivable) |
| **Historique** | Par utilisateur, groupé par jour, statistiques, suppression d'entrées |
| **Personnalisation** | Couleur d'accent, thème sombre / OLED / clair, taille des affiches, bibliothèques affichées sur l'accueil, avatar |
| **Administration** | Utilisateurs, connexion Plex (y compris « Se connecter avec Plex »), lectures en cours avec arrêt à distance |

Raccourcis du lecteur : `Espace` lecture/pause, `←` `→` ±10 s, `↑` `↓` volume, `F` plein écran, `M` muet, `S` passer l'intro, `N` épisode suivant, `Échap` retour.

## Installation

Sur un conteneur LXC ou une VM **Ubuntu 22.04/24.04** ou **Debian 12/13**, en root :

```bash
curl -fsSL https://raw.githubusercontent.com/xenix13/Nexis/main/deploy/install.sh | bash
```

Le script installe Python et git si besoin, récupère le code dans `/usr/local/src/seance`, l'installe dans `/opt/seance`, télécharge hls.js, crée l'utilisateur système `seance` et le service `seance.service`, puis affiche :

```
Séance tourne sur http://192.168.1.30:8420
Code d'installation (à saisir dans la page d'accueil) : 4F2A9C1B
```

**Mettre à jour** : relance la même commande. Les données (`/var/lib/seance`) et la configuration (`/etc/seance/seance.env`) sont conservées.

### Créer le conteneur sous Proxmox

Un conteneur **non privilégié** Ubuntu avec 1 cœur, 512 Mo de RAM et 4 Go de disque suffit : Séance ne transcode rien, c'est Plex qui le fait. Depuis le shell du nœud Proxmox (adapte l'ID, le stockage et le nom exact du modèle affiché par `pveam available`) :

```bash
pveam update && pveam available | grep ubuntu-24.04
pveam download local ubuntu-24.04-standard_24.04-2_amd64.tar.zst
pct create 120 local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst \
  --hostname seance --cores 1 --memory 512 --rootfs local-lvm:4 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp --unprivileged 1 --features nesting=1 --onboot 1
pct start 120
pct exec 120 -- bash -c "apt-get update && apt-get install -y curl && curl -fsSL https://raw.githubusercontent.com/xenix13/Nexis/main/deploy/install.sh | bash"
```

`nesting=1` permet au service de garder son bac à sable systemd. Sans lui, le script le détecte et bascule sur une version allégée du service.

Installation depuis un clone :

```bash
git clone https://github.com/xenix13/Nexis.git && cd Nexis && sudo ./deploy/install.sh
```

### Premier lancement

Ouvre `http://IP:8420` et suis l'assistant :

1. **Code d'installation** : affiché par le script, ou avec `cat /var/lib/seance/setup-code.txt`. Il empêche quelqu'un d'autre sur le réseau de créer le premier compte administrateur à ta place.
2. **Compte administrateur.**
3. **Serveur Plex** : son adresse vue depuis Séance (`http://127.0.0.1:32400` si c'est la même machine, sinon `http://IP-de-Plex:32400`), puis :
   - soit **« Se connecter avec Plex »** : un onglet plex.tv s'ouvre, tu autorises, c'est tout ;
   - soit colle ton **X-Plex-Token** : dans l'interface Plex, ouvre un média, puis ⋯ → *Obtenir des infos* → *Afficher le XML*. Le jeton est à la fin de l'URL (`X-Plex-Token=…`).

Clique sur **Tester la connexion**, puis **Terminer l'installation**. Va ensuite dans **Administration → Utilisateurs** pour créer les autres comptes et choisir leurs bibliothèques.

### Accès depuis l'extérieur (HTTPS)

Mets Séance derrière un reverse proxy HTTPS (Caddy, nginx, Nginx Proxy Manager…), puis dans `/etc/seance/seance.env` :

```
SEANCE_COOKIE_SECURE=1
SEANCE_TRUST_PROXY=1
```

Et `systemctl restart seance`. Exemple Caddy :

```
films.mondomaine.fr {
    reverse_proxy 192.168.1.30:8420
}
```

Pour nginx, ajoute `proxy_read_timeout 120s;` (le premier segment vidéo peut mettre quelques secondes à arriver). Séance contacte Plex en réseau local ; seul Séance est exposé.

## Comment ça marche

```
Navigateur ──(cookie de session)──▶ Séance :8420 ──(jeton admin, réseau local)──▶ Plex :32400
                                     │  comptes, historique, reprise,
                                     │  préférences, marqueurs (SQLite)
                                     └─ relaie images et flux HLS sans jamais exposer le jeton
```

- **Lecture** : Séance demande à Plex un flux HLS (`/video/:/transcode/universal`). Avec la qualité « Originale », Plex se contente de **remuxer** quand la vidéo est déjà en H.264 (presque aucun CPU). Le HEVC/x265, les débits réduits et les sous-titres gravés (ASS, PGS) imposent un **transcodage processeur**, puisque l'accélération matérielle de Plex demande le Plex Pass. C'est aussi pour ça que les SRT sont affichés par Séance.
- **Avance rapide** : dans la zone déjà chargée, c'est instantané. Au-delà, Séance relance le flux à la nouvelle position (une à deux secondes).
- **« Vu »** : un média est marqué vu à 90 % ou à la fin du flux. L'historique et la reprise sont stockés par utilisateur dans Séance, pas dans Plex.

## Passer l'intro

La bannière s'appuie sur des marqueurs de début et de fin d'intro :

1. **Ceux de Plex** s'ils existent. Plex ne les détecte que pour les serveurs Plex Pass, donc probablement aucun chez toi.
2. **Ceux de Séance** : un administrateur peut les poser à la main pendant la lecture (⚙ → *Début de l'intro ici* / *Fin de l'intro ici*).

La **détection automatique** (analyse audio des épisodes d'une saison pour repérer le générique commun) est l'étape suivante du projet. Les marqueurs qu'elle produira iront dans la même table et la bannière fonctionnera sans rien changer.

## Limites connues

- **Choix des pistes partagé** : Plex mémorise la piste audio et les sous-titres choisis par *compte Plex*. Séance les règle juste avant chaque lecture, donc tout marche normalement, sauf si deux personnes lancent **le même fichier** à la même seconde avec des pistes différentes.
- **Dolby/DTS** : les navigateurs ne lisent pas ces formats. Plex les convertit en AAC (léger).
- **Musique et photos** : non gérées (vidéo uniquement).
- **Testé contre un faux serveur Plex** : l'API et l'interface ont été validées par des tests automatisés qui simulent Plex. Le premier essai sur ton vrai serveur pourra demander un ajustement. Lance-le avec `SEANCE_DEBUG=1` en cas de souci.

## Dépannage

| Symptôme | Piste |
|---|---|
| « Plex a refusé le jeton (401) » | Jeton expiré ou erroné : Administration → Serveur Plex → « Se connecter avec Plex » |
| « Impossible de joindre Plex » | Adresse vue *depuis* la machine Séance ; si Plex est en « Connexions sécurisées : Requises », utilise l'adresse `https://…plex.direct:32400` ou passe le réglage sur « Préférées » |
| Lecture qui ne démarre pas | `journalctl -u seance -f` pendant l'essai, et dans Plex : Paramètres → Console |
| Code d'installation perdu | `cat /var/lib/seance/setup-code.txt` (il n'existe que tant qu'aucun compte n'est créé) |
| Mot de passe admin perdu | Un autre admin peut le réinitialiser ; sinon, voir la commande ci-dessous |

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
python3 -m seance --data ./data --port 8420         # serveur local
python3 -m unittest discover -s tests -t .           # tests API contre un faux Plex
python3 tests/ui_smoke.py ./captures                 # parcours navigateur (Playwright) + captures
```

Arborescence : `seance/` (serveur : `api.py` routes, `plex.py` client Plex, `auth.py`, `db.py`, `web.py` mini-framework HTTP), `static/` (interface en JavaScript sans build), `deploy/` (script d'installation et service systemd), `tests/`.

## Feuille de route

1. ~~MVP : comptes, bibliothèques, lecteur, reprise, historique~~ ✔
2. Détection automatique des intros et génériques de fin (ffmpeg + empreintes audio, tâche de fond)
3. Import de l'historique « vu » depuis Jellyfin, par utilisateur
4. Profils avancés : liste « à regarder », collections, contrôle parental, profils avec code PIN
