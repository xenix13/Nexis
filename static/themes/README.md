# Magasin de thèmes Nexis

Chaque fichier `.json` de ce dossier est un thème proposé dans *Paramètres → Magasin de thèmes* de tous les serveurs
Nexis. Les serveurs lisent `index.json`, régénéré automatiquement à chaque fusion dans `main`.

## Proposer un thème

**Le plus simple :** dans Nexis, *Paramètres → Magasin de thèmes → Créer un thème…*. Réglez les couleurs (l'interface
entière sert d'aperçu), puis *Proposer sur GitHub* : GitHub s'ouvre sur ce dossier avec votre fichier déjà rempli.
Validez la création du fichier : GitHub crée votre copie du dépôt et propose une demande de fusion (*pull request*).

**À la main :** ajoutez `static/themes/<id>.json` (le nom du fichier est l'`id` du thème) dans une demande de fusion.
Vérification locale : `python3 -m seance.themes check-files static/themes`.

L'intégration continue vérifie chaque thème (format, couleurs lisibles) ; un responsable du dépôt relit puis fusionne.

## Format

```json
{
 "schema": 1,
 "id": "ville-neon",
 "name": {"fr": "Ville néon", "en": "Neon city"},
 "description": {"fr": "Cyberpunk : magenta et cyan électriques.", "en": "Cyberpunk: electric magenta and cyan."},
 "author": "votre-identifiant-github",
 "version": 1,
 "dark": true,
 "colors": {
  "bg": "#07060d", "surface": "#120f22", "surface2": "#1d1836",
  "text": "#f3f0ff", "muted": "#a9a1cf", "line": "#2c2552",
  "accent": "#ff2fd1", "accent2": "#1fe6ff", "on_accent": "#1a0016"
 },
 "background": {"type": "gradient", "angle": 160, "stops": ["#1a0638", "#07060d", "#00202c"]},
 "effects": ["neon", "scanlines"],
 "font": "default",
 "radius": 6,
 "style": "glass"
}
```

| Champ | Valeurs |
|---|---|
| `id` | 3 à 40 caractères : minuscules, chiffres, tirets. Unique dans le magasin |
| `name`, `description` | texte (40 et 160 caractères au plus), en français et en anglais si possible |
| `author` | votre identifiant GitHub (affiché avec un lien vers votre profil) |
| `version` | à augmenter à chaque modification : les personnes qui ont installé le thème se voient proposer la mise à jour |
| `dark` | `true` pour un fond sombre et un texte clair |
| `colors` | `#rrggbb` : fond, panneaux (`surface`, `surface2`), texte, texte secondaire (`muted`), bordures (`line`, facultatif), accent, second accent (`accent2`, facultatif), texte sur l'accent (`on_accent`) |
| `background` | facultatif : dégradé de 2 à 4 couleurs, angle de 0 à 360 |
| `effects` | jusqu'à 4 parmi `neon`, `scanlines`, `grain`, `stars`, `vignette`, `glow` |
| `font` | `default`, `mono`, `serif`, `rounded` |
| `radius` | arrondi des angles, de 0 à 28 |
| `style` | facultatif : style d'interface conseillé (`classic`, `glass`, `sidebar`, `minimal`, `cinema`, `rounded`) |

**Lisibilité (vérifiée) :** contraste texte / fond d'au moins 7, texte / panneaux et texte secondaire / fond d'au
moins 4,5, texte des boutons / accent d'au moins 4,5, accent / fond d'au moins 3.

## Règles du magasin

- **Pas de code, pas d'image :** un thème ne contient que les champs ci-dessus. Tout autre champ est refusé.
- **Pas de marque ni de nom protégé :** inspirez-vous librement d'un film, d'un jeu ou d'un univers, mais avec un
  nom à vous (« Héros », « Ville néon », « Terminal » plutôt que le nom d'un film, d'un personnage ou d'une marque).
- Rien d'offensant dans les noms et les descriptions.
