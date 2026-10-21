"""Reconnaissance des noms de fichiers : films, séries, animes."""
import os
import re
import unicodedata

VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".m2ts", ".mts", ".wmv", ".flv",
             ".mpg", ".mpeg", ".ogv", ".3gp"}
SUB_EXT = {".srt", ".ass", ".ssa", ".vtt", ".sub"}
IMAGE_NAMES = {
    "poster": ("poster", "folder", "cover", "movie", "show"),
    "backdrop": ("fanart", "backdrop", "background", "art"),
}

_YEAR = r"(?:19|20)\d{2}"
# tokens that end a title in scene-style names
_JUNK = re.compile(
    r"[\s._-](?:2160p|1080p|1080i|720p|576p|480p|4k|uhd|hdr10?|dv|bluray|blu-ray|bdrip|brrip|webrip|web-dl|webdl|"
    r"web|hdtv|dvdrip|remux|x264|x265|h\.?264|h\.?265|hevc|avc|10bit|8bit|aac|ac3|dts|truehd|atmos|ddp?5\.1|"
    r"multi|vff|vfi|vfq|vf2?|vostfr|vost|french|truefrench|subfrench|english|vo|proper|repack|extended|"
    r"unrated|directors\.?cut|imax)(?=$|[\s._\-\])])", re.I)
_BRACKETS = re.compile(r"\[[^\]]*\]|\{[^}]*\}")
_SEP = re.compile(r"[._]+")

LANG_WORDS = {
    "fr": "fra", "fre": "fra", "fra": "fra", "french": "fra", "francais": "fra", "français": "fra", "vf": "fra",
    "vff": "fra", "en": "eng", "eng": "eng", "english": "eng", "anglais": "eng", "ja": "jpn", "jpn": "jpn",
    "jp": "jpn", "japanese": "jpn", "de": "deu", "ger": "deu", "deu": "deu", "german": "deu", "es": "spa",
    "spa": "spa", "spanish": "spa", "it": "ita", "ita": "ita", "italian": "ita", "pt": "por", "por": "por",
    "ko": "kor", "kor": "kor", "zh": "zho", "chi": "zho", "zho": "zho", "ru": "rus", "rus": "rus",
    "ar": "ara", "ara": "ara", "nl": "nld", "dut": "nld", "nld": "nld",
}


def fold(text):
    """Minuscules sans accents, pour la recherche et les tris."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()


def sort_title(title):
    t = fold(title)
    for art in ("the ", "le ", "la ", "les ", "l'", "un ", "une ", "a ", "an "):
        if t.startswith(art) and len(t) > len(art) + 1:
            return t[len(art):]
    return t


def clean_title(raw):
    """« The.Matrix.1999.1080p.BluRay » -> (« The Matrix », 1999)."""
    s = _BRACKETS.sub(" ", raw)
    year = None
    m = re.search(rf"[\s._(\[-]({_YEAR})(?:[\s._)\]-]|$)", " " + s)
    if m:
        year = int(m.group(1))
        s = (" " + s)[:m.start()] or s
    j = _JUNK.search(" " + s)
    if j:
        s = (" " + s)[:j.start()]
    s = _SEP.sub(" ", s)
    s = re.sub(r"\s*\(\s*\)\s*", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" -–_")
    return (s or raw.strip()), year


def parse_movie(path, root):
    """Titre et année d'un film à partir du fichier et/ou de son dossier."""
    base = os.path.splitext(os.path.basename(path))[0]
    parent = os.path.basename(os.path.dirname(path))
    if os.path.normpath(os.path.dirname(path)) != os.path.normpath(root):
        t, y = clean_title(parent)
        if y:
            return t, y
    t, y = clean_title(base)
    if not y and parent and os.path.normpath(os.path.dirname(path)) != os.path.normpath(root):
        pt, py = clean_title(parent)
        if py:
            return pt, py
    return t, y


_SXE = [
    re.compile(r"(?<![a-z0-9])s(\d{1,2})[\s._-]*e(\d{1,4})(?:[\s._-]*-?e\d{1,4})*", re.I),
    re.compile(r"(?<![a-z0-9])(\d{1,2})x(\d{1,3})(?![0-9p])", re.I),
    re.compile(r"(?<![a-z])saison[\s._-]*(\d{1,2})[\s._-]*[ée]pisode[\s._-]*(\d{1,4})", re.I),
    re.compile(r"(?<![a-z])season[\s._-]*(\d{1,2})[\s._-]*episode[\s._-]*(\d{1,4})", re.I),
]
_EP_ONLY = [
    re.compile(r"(?<![a-z0-9])(?:e|ep|episode|épisode)[\s._-]*(\d{1,4})(?![0-9p])", re.I),
    # anime : « [Groupe] Titre - 12 [1080p] » ou « Titre - 012v2 »
    re.compile(r"\s-\s(\d{1,4})(?:v\d)?(?=\s|$|\[|\()"),
    re.compile(r"^(\d{1,4})(?:v\d)?(?:[\s._-]|$)"),
]
_SEASON_DIR = re.compile(r"^(?:season|saison|series|s)[\s._-]*(\d{1,3})$", re.I)
_SPECIALS = re.compile(r"^(?:specials?|sp[ée]ciaux|extras?|ova|ovas|oav|season\s*0+|saison\s*0+)$", re.I)


def season_from_dir(name):
    if _SPECIALS.match(name.strip()):
        return 0
    m = _SEASON_DIR.match(name.strip())
    return int(m.group(1)) if m else None


def parse_episode(path, root):
    """-> dict(show, season, episode) ou None si aucun numéro d'épisode n'est trouvé."""
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    base = os.path.splitext(parts[-1])[0]
    show_dir = parts[0] if len(parts) > 1 else None
    season = None
    for d in parts[1:-1]:
        s = season_from_dir(d)
        if s is not None:
            season = s
    name = _BRACKETS.sub(" ", base)
    name = re.sub(r"\((?:19|20)\d{2}\)|\b(?:2160|1080|720|576|480)p\b", " ", name, flags=re.I)
    episode = None
    title_part = name
    for rx in _SXE:
        m = rx.search(name)
        if m:
            season = int(m.group(1))
            episode = int(m.group(2))
            title_part = name[:m.start()]
            break
    if episode is None:
        cleaned = _SEP.sub(" ", name)
        for rx in _EP_ONLY:
            m = rx.search(cleaned.strip())
            if m:
                episode = int(m.group(1))
                title_part = cleaned.strip()[:m.start()]
                break
    if episode is None:
        return None
    if season is None:
        season = 1
    if show_dir:
        show, year = clean_title(show_dir)
    else:
        show, year = clean_title(title_part or base)
    return {"show": show, "year": year, "season": season, "episode": episode, "show_dir": show_dir}


def sub_language(sub_path, video_path):
    """« Film.fr.forced.srt » -> ('fra', forced=True)."""
    vbase = os.path.splitext(os.path.basename(video_path))[0]
    sbase = os.path.splitext(os.path.basename(sub_path))[0]
    rest = sbase[len(vbase):] if sbase.startswith(vbase) else sbase
    tokens = [t for t in re.split(r"[.\s_\-\[\]()]+", rest.lower()) if t]
    lang, forced, sdh = "", False, False
    for t in tokens:
        if t in LANG_WORDS and not lang:
            lang = LANG_WORDS[t]
        elif t in ("forced", "force", "forces"):
            forced = True
        elif t in ("sdh", "cc", "hi"):
            sdh = True
    return lang, forced, sdh
