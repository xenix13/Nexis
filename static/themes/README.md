# Nexis theme store

Every `.json` file in this folder is a theme offered in *Settings → Appearance → Theme store* on every Nexis server. Servers read `index.json`, which is regenerated automatically each time a change is merged into `main`.

## Proposing a theme

**The easy way:** in Nexis, *Settings → Appearance → Theme store → Create a theme…*. Adjust the colours (the whole interface is the preview), then *Propose on GitHub*: GitHub opens this folder with your file already filled in. Confirm the creation of the file: GitHub makes your copy of the repository and opens a pull request.

**By hand:** add `static/themes/<id>.json` (the file name is the theme's `id`) in a pull request. Check it locally with `python3 -m seance.themes check-files static/themes`.

CI checks every theme (format, readable colours); a maintainer reviews it, then merges.

## Format

```json
{
 "schema": 1,
 "id": "ville-neon",
 "name": {"fr": "Ville néon", "en": "Neon city"},
 "description": {"fr": "Cyberpunk : magenta et cyan électriques.", "en": "Cyberpunk: electric magenta and cyan."},
 "author": "your-github-username",
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

| Field | Values |
|---|---|
| `id` | 3 to 40 characters: lowercase letters, digits, hyphens. Unique in the store |
| `name`, `description` | text (40 and 160 characters at most), in English and French if you can |
| `author` | your GitHub username (shown with a link to your profile) |
| `version` | increase it with every change: people who installed the theme are offered the update |
| `dark` | `true` for a dark background and light text |
| `colors` | `#rrggbb`: background, panels (`surface`, `surface2`), text, secondary text (`muted`), borders (`line`, optional), accent, second accent (`accent2`, optional), text on the accent (`on_accent`) |
| `background` | optional: a gradient of 2 to 4 colours, angle from 0 to 360 |
| `effects` | up to 4 of `neon`, `scanlines`, `grain`, `stars`, `vignette`, `glow` |
| `font` | `default`, `mono`, `serif`, `rounded` |
| `radius` | corner radius, from 0 to 28 |
| `style` | optional: suggested interface style (`classic`, `glass`, `sidebar`, `minimal`, `cinema`, `rounded`) |

**Readability (checked):** contrast of text on background at least 7, text on panels and secondary text on background at least 4.5, button text on accent at least 4.5, accent on background at least 3.

## Store rules

- **No code, no images:** a theme only contains the fields above. Any other field is refused.
- **No trademarks or protected names:** take inspiration freely from a film, a game or a universe, but give it your own name ("Hero", "Neon city", "Terminal" rather than the name of a film, a character or a brand).
- Nothing offensive in names and descriptions.
