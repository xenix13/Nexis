"""Intro et générique de fin déduits des chapitres du fichier."""
import re

INTRO = re.compile(r"^\s*(?:op(?:ening)?(?:\s*\d+)?(?:\b|$)|intro(?:duction)?\b|g[ée]n[ée]rique(?!\s+de\s+fin)\b|"
                   r"opening credits|title sequence|theme song|ouverture|main titles?)", re.I)
CREDITS = re.compile(r"^\s*(?:ed(?:\s*\d+)?(?:\b|$)|ending|end\s*credits|credits|outro|g[ée]n[ée]rique\s+de\s+fin|"
                     r"closing|end titles|cr[ée]dits)", re.I)
PREVIEW = re.compile(r"^\s*(?:preview|next episode|aper[çc]u|prochain [ée]pisode|yokoku|next time)", re.I)
GENERIC = re.compile(r"^\s*(?:(?:chapter|chapitre|ch\.?|kapitel|cap[ií]tulo|capitolo)?\s*\d{1,3}|"
                     r"\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)?\s*$", re.I)
SONG = (70, 115)  # durée typique d'un opening / ending d'anime (secondes)


def from_chapters(chapters, duration):
    """-> {'intro': {start, end, source}, 'credits': {...}} en millisecondes."""
    out = {}
    if not chapters or not duration:
        return out
    ms = lambda s: int(round(s * 1000))
    named = [c for c in chapters if not GENERIC.match(c.get("title") or "")]
    if named:
        for c in chapters:
            t = c.get("title") or ""
            if "intro" not in out and INTRO.match(t) and c["start"] < duration * 0.5:
                out["intro"] = {"start": ms(c["start"]), "end": ms(c["end"]), "source": "chapters"}
            elif "credits" not in out and CREDITS.match(t) and c["start"] > duration * 0.5:
                out["credits"] = {"start": ms(c["start"]), "end": ms(c["end"]), "source": "chapters"}
        return out
    # chapitres sans nom : repère les blocs de la durée d'une chanson (animes)
    songs = [c for c in chapters if SONG[0] <= c["end"] - c["start"] <= SONG[1]]
    intro = next((c for c in songs if c["start"] < min(duration * 0.4, 600)), None)
    credits = next((c for c in reversed(songs) if c["start"] > duration * 0.7 and c is not intro), None)
    if intro:
        out["intro"] = {"start": ms(intro["start"]), "end": ms(intro["end"]), "source": "chapters-guess"}
    if credits:
        out["credits"] = {"start": ms(credits["start"]), "end": ms(credits["end"]), "source": "chapters-guess"}
    return out
