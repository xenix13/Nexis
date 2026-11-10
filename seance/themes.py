"""Thèmes de couleurs partagés : ceux fournis avec Noxa et le magasin de thèmes (dossier static/themes du dépôt
GitHub, enrichi par les contributions de chacun).

Un thème n'est jamais du code : c'est une description vérifiée — des couleurs, un fond en dégradé, des effets choisis
dans une liste fixe, une police, un arrondi. Pas de CSS libre, pas d'image ni d'adresse : un thème venu d'ailleurs ne
peut ni imiter un écran de connexion, ni envoyer quoi que ce soit hors du serveur. Les couleurs doivent rester
lisibles (contraste minimal vérifié ici, à l'identique dans l'intégration continue du dépôt).

    python3 -m seance.themes check-files static/themes  # vérifie chaque thème (demande de fusion d'un contributeur)
    python3 -m seance.themes check static/themes        # et que index.json est à jour
    python3 -m seance.themes build static/themes        # régénère index.json (fait par l'intégration continue sur main)
"""
import glob
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger("seance.themes")

SCHEMA = 1
ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
AUTHOR = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")  # identifiant GitHub
TEXT = re.compile(r"^[^<>{}\\\x00-\x1f]*$")
COLORS = ("bg", "surface", "surface2", "text", "muted", "line", "accent", "accent2", "on_accent")
EFFECTS = ("neon", "scanlines", "grain", "stars", "vignette", "glow")
FONTS = ("default", "mono", "serif", "rounded")
UI_STYLES = ("classic", "glass", "sidebar", "minimal", "cinema", "rounded")
LANGS = ("fr", "en")
MAX_THEMES = 500
MAX_INDEX = 2 * 1024 * 1024
DEFAULT_INDEX = "https://raw.githubusercontent.com/xenix13/Noxa/main/static/themes/index.json"
DEFAULT_SUBMIT = "https://github.com/xenix13/Noxa/new/main/static/themes"
REFRESH = 6 * 3600


class ThemeError(ValueError):
    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = problems


def _lum(hexcolor):
    def ch(c):
        c = int(c, 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    h = hexcolor.lstrip("#")
    return 0.2126 * ch(h[0:2]) + 0.7152 * ch(h[2:4]) + 0.0722 * ch(h[4:6])


def contrast(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _label(v, field, problems, required=True, size=60):
    """Texte en plusieurs langues : {"fr": …, "en": …} (ou une simple chaîne, prise pour les deux)."""
    if v is None and not required:
        return None
    if isinstance(v, str):
        v = {"fr": v, "en": v}
    if not isinstance(v, dict) or not v or set(v) - set(LANGS):
        problems.append(f"{field} : texte attendu, ou {{\"fr\": …, \"en\": …}}")
        return None
    out = {}
    for lang, s in v.items():
        if not isinstance(s, str) or not s.strip() or len(s) > size or not TEXT.match(s):
            problems.append(f"{field}.{lang} : texte de 1 à {size} caractères, sans < > {{ }}")
            return None
        out[lang] = s.strip()
    if "fr" not in out:
        out["fr"] = next(iter(out.values()))
    if "en" not in out:
        out["en"] = out["fr"]
    return out


def validate(t):
    """Thème reçu (fichier du dépôt, import, préférences) -> thème normalisé, ou ThemeError (liste des problèmes)."""
    problems = []
    if not isinstance(t, dict):
        raise ThemeError(["un thème est un objet JSON"])
    known = {"schema", "id", "name", "description", "author", "version", "dark", "colors", "background", "effects",
             "font", "radius", "style"}
    extra = sorted(set(t) - known)
    if extra:
        problems.append("champs inconnus : " + ", ".join(extra))
    if t.get("schema", SCHEMA) != SCHEMA:
        problems.append(f"schema : {SCHEMA} attendu")
    tid = t.get("id")
    if not isinstance(tid, str) or not ID.match(tid):
        problems.append("id : 3 à 40 caractères, minuscules, chiffres et tirets")
    name = _label(t.get("name"), "name", problems, size=40)
    desc = _label(t.get("description"), "description", problems, required=False, size=160)
    author = t.get("author", "")
    if author and (not isinstance(author, str) or not AUTHOR.match(author)):
        problems.append("author : identifiant GitHub")
    version = t.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version <= 9999:
        problems.append("version : entier de 1 à 9999")
    dark = t.get("dark", True)
    if not isinstance(dark, bool):
        problems.append("dark : true ou false")
    colors = t.get("colors")
    out_colors = {}
    if not isinstance(colors, dict):
        problems.append("colors : objet de couleurs attendu")
    else:
        extra = sorted(set(colors) - set(COLORS))
        if extra:
            problems.append("colors : couleurs inconnues : " + ", ".join(extra))
        for k in COLORS:
            v = colors.get(k)
            if v is None and k in ("accent2", "line"):
                continue
            if not isinstance(v, str) or not HEX.match(v):
                problems.append(f"colors.{k} : couleur #rrggbb")
            else:
                out_colors[k] = v.lower()
        if not problems or all(k in out_colors for k in ("bg", "surface", "text", "muted", "accent", "on_accent")):
            c = out_colors
            if {"bg", "text"} <= set(c) and contrast(c["text"], c["bg"]) < 7:
                problems.append(f"contraste texte / fond trop faible ({contrast(c['text'], c['bg']):.1f}, 7 au moins)")
            if {"surface", "text"} <= set(c) and contrast(c["text"], c["surface"]) < 4.5:
                problems.append(f"contraste texte / panneaux trop faible ({contrast(c['text'], c['surface']):.1f}, 4,5 au moins)")
            if {"bg", "muted"} <= set(c) and contrast(c["muted"], c["bg"]) < 4.5:
                problems.append(f"contraste texte secondaire / fond trop faible ({contrast(c['muted'], c['bg']):.1f}, 4,5 au moins)")
            if {"accent", "on_accent"} <= set(c) and contrast(c["on_accent"], c["accent"]) < 4.5:
                problems.append(f"contraste texte des boutons / accent trop faible ({contrast(c['on_accent'], c['accent']):.1f}, 4,5 au moins)")
            if {"accent", "bg"} <= set(c) and contrast(c["accent"], c["bg"]) < 3:
                problems.append(f"accent trop peu visible sur le fond ({contrast(c['accent'], c['bg']):.1f}, 3 au moins)")
            if {"bg", "text"} <= set(c) and isinstance(dark, bool) and (_lum(c["bg"]) < _lum(c["text"])) != dark:
                problems.append("dark ne correspond pas aux couleurs (fond sombre et texte clair : dark = true)")
    bg = t.get("background")
    out_bg = None
    if bg is not None:
        if not isinstance(bg, dict) or bg.get("type") != "gradient" or set(bg) - {"type", "angle", "stops"}:
            problems.append("background : {\"type\": \"gradient\", \"angle\": 0-360, \"stops\": [2 à 4 couleurs]}")
        else:
            angle, stops = bg.get("angle", 180), bg.get("stops")
            if not isinstance(angle, int) or isinstance(angle, bool) or not 0 <= angle <= 360:
                problems.append("background.angle : entier de 0 à 360")
            elif not isinstance(stops, list) or not 2 <= len(stops) <= 4 or not all(isinstance(s, str) and HEX.match(s) for s in stops):
                problems.append("background.stops : 2 à 4 couleurs #rrggbb")
            else:
                out_bg = {"type": "gradient", "angle": angle, "stops": [s.lower() for s in stops]}
    effects = t.get("effects", [])
    if not isinstance(effects, list) or len(effects) > 4 or not all(e in EFFECTS for e in effects):
        problems.append("effects : jusqu'à 4 parmi " + ", ".join(EFFECTS))
        effects = []
    font = t.get("font", "default")
    if font not in FONTS:
        problems.append("font : " + ", ".join(FONTS))
    radius = t.get("radius", 10)
    if not isinstance(radius, int) or isinstance(radius, bool) or not 0 <= radius <= 28:
        problems.append("radius : entier de 0 à 28")
    style = t.get("style")
    if style is not None and style not in UI_STYLES:
        problems.append("style : " + ", ".join(UI_STYLES))
    if problems:
        raise ThemeError(problems)
    out = {"schema": SCHEMA, "id": tid, "name": name, "author": author or "", "version": version, "dark": dark,
           "colors": out_colors, "effects": list(dict.fromkeys(effects)), "font": font, "radius": radius}
    if desc:
        out["description"] = desc
    if out_bg:
        out["background"] = out_bg
    if style:
        out["style"] = style
    return out


def load_dir(path):
    """Thèmes d'un dossier (un fichier .json par thème, index.json mis à part) -> [thèmes], [(fichier, erreur)]."""
    themes, errors, seen = [], [], set()
    for f in sorted(glob.glob(os.path.join(path, "*.json"))):
        if os.path.basename(f) == "index.json":
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                t = validate(json.load(fh))
        except (OSError, ValueError) as e:
            errors.append((os.path.basename(f), str(e)))
            continue
        if os.path.basename(f) != t["id"] + ".json":
            errors.append((os.path.basename(f), f"le fichier doit s'appeler {t['id']}.json"))
            continue
        if t["id"] in seen:
            errors.append((os.path.basename(f), "id déjà pris"))
            continue
        seen.add(t["id"])
        themes.append(t)
    return themes, errors


def index_of(themes):
    return {"schema": SCHEMA, "themes": sorted(themes, key=lambda t: t["id"])}


def index_text(themes):
    return json.dumps(index_of(themes), ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def parse_index(raw):
    """Index du magasin (téléchargé) -> thèmes valides (les autres sont ignorés, un à un)."""
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("themes"), list):
        raise ValueError("index de thèmes invalide")
    out, seen = [], set()
    for t in data["themes"][:MAX_THEMES]:
        try:
            v = validate(t)
        except ThemeError:
            continue
        if v["id"] not in seen:
            seen.add(v["id"])
            out.append(v)
    return out


class Store:
    """Thèmes fournis (dossier static/themes) et magasin en ligne (index.json du dépôt GitHub, relu toutes les 6 h)."""

    def __init__(self, ctx, static_dir):
        self.ctx = ctx
        self.bundled, errs = load_dir(os.path.join(static_dir, "themes"))
        for f, e in errs:
            log.warning("Thème fourni ignoré (%s) : %s", f, e)
        self.cache_file = os.path.join(ctx.data_dir, "cache", "themes-index.json")
        self.lock = threading.Lock()
        self.remote, self.fetched_at, self.error = None, 0, None
        try:
            with open(self.cache_file, encoding="utf-8") as f:
                self.remote = parse_index(f.read())
                self.fetched_at = os.path.getmtime(self.cache_file)
        except (OSError, ValueError):
            pass

    def config(self):
        db = self.ctx.db
        return {"enabled": db.setting("themes_store", "1") == "1",
                "index_url": db.setting("themes_index_url") or DEFAULT_INDEX,
                "submit_url": db.setting("themes_submit_url") or DEFAULT_SUBMIT}

    def refresh(self, force=False):
        conf = self.config()
        if not conf["enabled"]:
            return
        if not force and time.time() - self.fetched_at < REFRESH:
            return
        if not self.lock.acquire(blocking=False):
            return  # déjà en cours
        try:
            self.ctx.db.release()
            req = urllib.request.Request(conf["index_url"], headers={"User-Agent": "Noxa-themes/1", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read(MAX_INDEX + 1)
            if len(raw) > MAX_INDEX:
                raise ValueError("index trop volumineux")
            themes = parse_index(raw.decode("utf-8"))
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            tmp = self.cache_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(index_text(themes))
            os.replace(tmp, self.cache_file)
            self.remote, self.error = themes, None
        except (OSError, ValueError, UnicodeDecodeError) as e:
            self.error = str(e)[:200]
            log.info("Magasin de thèmes injoignable : %s", e)
        finally:
            self.fetched_at = time.time()  # même en cas d'échec : pas de nouvel essai avant 6 h (sauf « Actualiser »)
            self.lock.release()

    def refresh_later(self):
        """Index à relire : en arrière-plan, la page s'affiche tout de suite avec ce qui est connu."""
        if not self.config()["enabled"] or time.time() - self.fetched_at < REFRESH or self.lock.locked():
            return

        def run():
            try:
                self.refresh()
            finally:
                self.ctx.db.release()
        threading.Thread(target=run, daemon=True, name="themes").start()

    def listing(self):
        conf = self.config()
        bundled = {t["id"] for t in self.bundled}
        store = [dict(t, source="store") for t in (self.remote or []) if t["id"] not in bundled] if conf["enabled"] else []
        # un thème fourni dont le magasin a une version plus récente : on montre la plus récente
        newer = {t["id"]: t for t in (self.remote or []) if conf["enabled"]}
        official = [dict(newer[t["id"]] if t["id"] in newer and newer[t["id"]]["version"] > t["version"] else t, source="bundled")
                    for t in self.bundled]
        return {"themes": official + store, "store": {"enabled": conf["enabled"], "submit_url": conf["submit_url"],
                "updated_at": int(self.fetched_at) or None, "error": self.error, "count": len(store)}}


def register(app, ctx, x):
    from .web import HTTPError
    store = ctx.themes

    @app.route("GET", "/api/themes")
    def themes_list(req):
        if req.arg("refresh") == "1" and req.user.get("is_admin"):
            store.refresh(force=True)  # « Actualiser » d'un administrateur : on attend le résultat
        else:
            store.refresh_later()
        return store.listing()

    @app.route("POST", "/api/themes/check")
    def themes_check(req):
        """Thème importé ou créé dans l'éditeur : normalisé, ou la liste de ce qui ne va pas."""
        try:
            return {"theme": validate(req.json().get("theme"))}
        except ThemeError as e:
            raise HTTPError(400, "Thème invalide", {"problems": e.problems})

    @app.route("GET", "/api/admin/themes", auth="admin")
    def themes_admin(req):
        return dict(store.config(), default_index=DEFAULT_INDEX, default_submit=DEFAULT_SUBMIT,
                    updated_at=int(store.fetched_at) or None, error=store.error, count=len(store.remote or []))

    @app.route("PUT", "/api/admin/themes", auth="admin")
    def themes_admin_set(req):
        b = req.json()
        db = ctx.db
        if "enabled" in b:
            db.set_setting("themes_store", "1" if b["enabled"] else "0")
        for key, setting in (("index_url", "themes_index_url"), ("submit_url", "themes_submit_url")):
            if key in b:
                v = (b[key] or "").strip()
                if v and (not re.match(r"^https://[A-Za-z0-9.-]+(:\d{1,5})?/[^\s<>\"']*$", v) or len(v) > 300):
                    raise HTTPError(400, "Adresse invalide (https://…)")
                db.set_setting(setting, v or None)
        store.refresh(force=True)
        return themes_admin(req)


def main(argv):
    if len(argv) != 3 or argv[1] not in ("check", "check-files", "build"):
        print(__doc__)
        return 2
    path = argv[2]
    themes, errors = load_dir(path)
    for f, e in errors:
        print(f"{f} : {e}")
    if argv[1] == "check-files":
        if not errors:
            print(f"{len(themes)} thème(s) valides")
        return 1 if errors else 0
    text = index_text(themes)
    idx = os.path.join(path, "index.json")
    if argv[1] == "build":
        if errors:
            return 1
        with open(idx, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"{len(themes)} thème(s) dans {idx}")
        return 0
    try:
        with open(idx, encoding="utf-8") as f:
            current = f.read()
    except OSError:
        current = ""
    if current != text:
        print(f"{idx} n'est pas à jour : python3 -m seance.themes build {path}")
        return 1
    if not errors:
        print(f"{len(themes)} thème(s) valides")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
