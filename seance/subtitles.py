"""SRT / WebVTT parsing into simple cues rendered by the web player."""
import re

_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_TAGS = re.compile(r"<[^>]{0,200}>|\{\\[^}]{0,200}\}")


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
            body = "\n".join(_TAGS.sub("", l).replace("\\N", "\n").strip() for l in lines[i + 1:]).strip()
            if body:
                cues.append({"s": _ms(a), "e": _ms(b), "t": body})
            break
        if len(cues) >= limit:
            break
    cues.sort(key=lambda c: c["s"])
    return cues
