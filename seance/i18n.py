"""Langue des messages du serveur (erreurs, tâches planifiées, mise à jour…).

Le texte du code est en français et sert de clé, comme dans l'interface (static/js/i18n.js) : tr("Introuvable")
renvoie la traduction de la langue de la requête en cours. Les messages composés (« Dossier introuvable
depuis le serveur : /mnt/x ») sont reconnus par des modèles où {} remplace la partie variable.

Langue d'une requête : en-tête X-Lang (envoyé par l'interface web, dans la langue qu'elle affiche), sinon la
langue du compte, sinon la langue par défaut du serveur (Paramètres → Utilisateurs). Elle est gardée pour le
fil d'exécution de la requête ; les travaux de fond utilisent la langue par défaut.
"""
import re
import threading

from .lang_en import EN

LANGUAGES = {"fr": "Français", "en": "English"}
DEFAULT = "fr"
DICTS = {"en": EN}

_local = threading.local()
_server_default = {"lang": DEFAULT}


def valid(lang):
    return lang if lang in LANGUAGES else None


def default_language(db):
    return valid(db.setting("default_language")) or DEFAULT


def set_server_default(lang):
    """Langue des travaux de fond (gardée ici pour ne pas lire la base hors requête)."""
    _server_default["lang"] = valid(lang) or DEFAULT


def set_lang(lang):
    _local.lang = valid(lang)


def current():
    return getattr(_local, "lang", None) or _server_default["lang"]


def request_language(req, db):
    """Langue d'une requête : en-tête de l'interface, compte, langue par défaut du serveur."""
    return (valid((req.headers.get("X-Lang") or "").strip()) or (req.user and valid(req.user.get("language")))
            or default_language(db))


_patterns = {}  # langue -> [(expression, traduction)] pour les clés contenant {}


def _templates(lang):
    if lang not in _patterns:
        out = []
        for k, v in DICTS[lang].items():
            if "{}" in k:
                rx = "^" + re.escape(k).replace(r"\{\}", "(.+?)") + "$"
                out.append((re.compile(rx, re.S), v))
        out.sort(key=lambda p: -len(p[0].pattern))  # le modèle le plus précis d'abord
        _patterns[lang] = out
    return _patterns[lang]


def tr(text, lang=None):
    """Traduction de text (tel quel s'il n'y en a pas, ou en français)."""
    lang = lang or current()
    d = DICTS.get(lang)
    if not d or not isinstance(text, str) or not text:
        return text
    hit = d.get(text)
    if hit is not None:
        return hit
    for rx, value in _templates(lang):
        m = rx.match(text)
        if m:
            # les parties variables peuvent être elles-mêmes des messages connus (« … : délai dépassé »)
            return value.format(*(d.get(g, g) for g in m.groups()))
    return text
