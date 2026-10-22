"""SRT / WebVTT parsing into simple cues rendered by the web player."""
import re

_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_TAGS = re.compile(r"<[^>]{0,200}>|\{\\[^}]{0,200}\}")
_TOP = re.compile(r"\{\\an[789]\}|\{\\a(?:5|6|7|9|10|11)\}")
# dessins vectoriels ASS ({\p1}m 0 0 l 100 0…) : illisibles en texte, on les écarte
_DRAWING = re.compile(r"^[\s\d.\-mlbspcn]+$")


def _ms(m):
    h, mi, s, frac = m.groups()
    frac = (frac + "00")[:3]
    return ((int(h or 0) * 60 + int(mi)) * 60 + int(s)) * 1000 + int(frac)


def decode(raw):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def parse_subtitles(raw, limit=20000):
    text = decode(raw).replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", text):
        lines = [l for l in block.split("\n") if l.strip()]
        for i, line in enumerate(lines):
            if "-->" not in line:
                continue
            left, _, right = line.partition("-->")
            a, b = _TIME.search(left), _TIME.search(right)
            if not a or not b:
                break
            raw = "\n".join(lines[i + 1:])
            body = "\n".join(_TAGS.sub("", l).replace("\\N", "\n").replace("\\h", " ").strip()
                             for l in lines[i + 1:]).strip()
            if body and not (_DRAWING.match(body) and re.search(r"\bm\s+-?\d", body)):
                cue = {"s": _ms(a), "e": _ms(b), "t": body}
                if _TOP.search(raw):
                    cue["top"] = True
                cues.append(cue)
            break
        if len(cues) >= limit:
            break
    cues.sort(key=lambda c: c["s"])
    # doublons exacts (même texte, mêmes instants : effets de style empilés)
    out, seen = [], set()
    for c in cues:
        k = (c["s"], c["e"], c["t"])
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out
