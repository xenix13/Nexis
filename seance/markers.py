"""Opening (OP) et générique de fin déduits des chapitres du fichier.

Dans les sorties d'animes, « Intro » / « Prologue » désigne souvent l'avant-générique (la scène qui précède
l'OP) et « OP » / « Opening » la chanson. Le bouton « Passer » doit viser la chanson : un chapitre OP l'emporte
toujours sur un chapitre « Intro », et un « Intro » qui n'a pas la durée d'une chanson cède la place au
chapitre suivant s'il en a la durée.
"""
import re

OPENING = re.compile(r"^\s*(?:op(?:ening)?(?:\s*(?:song|theme)?\s*\d*)?(?:\b|$)|opening credits|title sequence|"
                     r"theme song|main titles?|g[ée]n[ée]rique(?!\s+de\s+fin)(?:\s+d['e ]\s*ouverture|\s+d[ée]but)?\b|"
                     r"ouverture|credits?\s+d['e ]\s*ouverture)", re.I)
INTRO = re.compile(r"^\s*(?:intro(?:duction)?|title)\b", re.I)
PROLOGUE = re.compile(r"^\s*(?:prologue|avant[- ]g[ée]n[ée]rique|cold open|teaser|recap|r[ée]sum[ée]|previously|"
                      r"pr[ée]c[ée]demment|avant[- ]titre)", re.I)
CREDITS = re.compile(r"^\s*(?:ed(?:\s*(?:song|theme)?\s*\d*)?(?:\b|$)|ending|end\s*credits|credits|outro|"
                     r"g[ée]n[ée]rique\s+de\s+fin|closing|end titles|cr[ée]dits)", re.I)
PREVIEW = re.compile(r"^\s*(?:preview|next episode|aper[çc]u|prochain [ée]pisode|yokoku|next time)", re.I)
GENERIC = re.compile(r"^\s*(?:(?:chapter|chapitre|ch\.?|kapitel|cap[ií]tulo|capitolo|part(?:ie)?)?\s*\d{1,3}|"
                     r"\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)?\s*$", re.I)
SONG = (70, 115)  # durée typique d'un opening / ending d'anime (secondes)


def _len(c):
    return c["end"] - c["start"]


def _songlike(c):
    return SONG[0] - 10 <= _len(c) <= SONG[1] + 15


def _pick_opening(chapters, duration):
    early = [c for c in chapters if c["start"] < duration * 0.5]
    ops = [c for c in early if OPENING.match(c.get("title") or "")]
    if ops:
        return next((c for c in ops if _songlike(c)), ops[0])
    for i, c in enumerate(early):
        if not INTRO.match(c.get("title") or ""):
            continue
        if _songlike(c) or _len(c) > 30 and i + 1 < len(chapters) and not _songlike(chapters[i + 1]):
            return c
        # « Intro » trop court : c'est l'avant-générique, l'OP est le chapitre suivant s'il a la bonne durée
        nxt = chapters[i + 1] if i + 1 < len(chapters) else None
        if nxt and _songlike(nxt) and not CREDITS.match(nxt.get("title") or "") \
                and not PREVIEW.match(nxt.get("title") or ""):
            return nxt
        return c
    # avant-générique nommé suivi d'un chapitre sans nom de la durée d'une chanson
    for i, c in enumerate(early[:-1]):
        nxt = chapters[i + 1]
        if PROLOGUE.match(c.get("title") or "") and _songlike(nxt) and GENERIC.match(nxt.get("title") or ""):
            return nxt
    return None


def _pick_credits(chapters, duration):
    late = [c for c in chapters if c["start"] > duration * 0.5]
    eds = [c for c in late if CREDITS.match(c.get("title") or "")]
    if not eds:
        return None
    return next((c for c in eds if _songlike(c)), eds[0])


def from_chapters(chapters, duration):
    """-> {'intro': {start, end, source}, 'credits': {...}} en millisecondes ('intro' = l'OP à passer)."""
    out = {}
    if not chapters or not duration:
        return out
    ms = lambda s: int(round(s * 1000))
    chapters = sorted(chapters, key=lambda c: c["start"])
    named = [c for c in chapters if not GENERIC.match(c.get("title") or "")]
    if named:
        op, ed = _pick_opening(chapters, duration), _pick_credits(chapters, duration)
        if op:
            out["intro"] = {"start": ms(op["start"]), "end": ms(op["end"]), "source": "chapters"}
        if ed:
            out["credits"] = {"start": ms(ed["start"]), "end": ms(ed["end"]), "source": "chapters"}
        if op and ed:
            return out
    # chapitres sans nom (ou sans OP/ED nommé) : repère les blocs de la durée d'une chanson (animes)
    songs = [c for c in chapters if SONG[0] <= _len(c) <= SONG[1] and not PREVIEW.match(c.get("title") or "")]
    if "intro" not in out:
        op = next((c for c in songs if c["start"] < min(duration * 0.4, 600)
                   and not CREDITS.match(c.get("title") or "")), None)
        if op:
            out["intro"] = {"start": ms(op["start"]), "end": ms(op["end"]), "source": "chapters-guess"}
    if "credits" not in out:
        start_op = out.get("intro", {}).get("start")
        ed = next((c for c in reversed(songs) if c["start"] > duration * 0.7 and ms(c["start"]) != start_op
                   and not OPENING.match(c.get("title") or "")), None)
        if ed:
            out["credits"] = {"start": ms(ed["start"]), "end": ms(ed["end"]), "source": "chapters-guess"}
    return out
