"""Détection des intros (opening) et génériques de fin par empreinte audio, pour les fichiers sans
chapitres exploitables — la méthode du plugin « Intro Skipper » de Jellyfin.

Les épisodes d'une même saison partagent la même chanson d'ouverture et le même générique. On calcule
l'empreinte Chromaprint (fournie par ffmpeg) du début et de la fin de chaque épisode, puis on cherche,
entre deux épisodes, le plus long passage dont les empreintes se ressemblent (à un décalage près).

Une empreinte est une suite d'entiers 32 bits, un toutes les ~0,124 s. Deux points « correspondent »
s'ils diffèrent de peu de bits. On repère d'abord les décalages plausibles grâce aux valeurs identiques
(index inversé), puis on mesure, pour chacun, la plus longue suite de points correspondants (en tolérant
de petites coupures).
"""
import json
import logging
import os
import struct
import subprocess

from . import media

log = logging.getLogger("seance.intro")

POINT = 0.1238              # durée d'un point d'empreinte (s)
MAX_BIT_DIFF = 6            # bits différents tolérés entre deux points « identiques »
MAX_GAP = int(3.5 / POINT)  # coupure tolérée dans une suite (bruitages, dialogue par-dessus…)
TOP_SHIFTS = 8


class FingerprintError(Exception):
    pass


_supported = None


def supported():
    global _supported
    if _supported is None:
        try:
            out = subprocess.run([media.FFMPEG, "-hide_banner", "-muxers"], capture_output=True, timeout=20).stdout
            _supported = b"chromaprint" in out
        except (OSError, subprocess.SubprocessError):
            _supported = False
    return _supported


def fingerprint(path, start, length):
    """Empreinte du passage [start, start+length] (secondes) de la première piste audio."""
    cmd = [media.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{max(0.0, start):.2f}", "-t", f"{length:.2f}",
           *media.LOCAL_ONLY, "-i", path, "-map", "0:a:0", "-vn", "-sn", "-ac", "1", "-f", "chromaprint",
           "-fp_format", "raw", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=max(120, int(length)), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise FingerprintError("délai dépassé")
    except OSError as e:
        raise FingerprintError(str(e))
    if p.returncode != 0:
        msg = (p.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise FingerprintError(msg[-1] if msg else "empreinte impossible")
    data = p.stdout[: len(p.stdout) // 4 * 4]
    return list(struct.unpack(f"<{len(data) // 4}I", data))


def _shifts(a, b):
    """Décalages (position dans b - position dans a) les plus fréquents parmi les valeurs identiques."""
    index = {}
    for j, v in enumerate(b):
        index.setdefault(v, []).append(j)
    counts = {}
    for i, v in enumerate(a):
        for j in index.get(v, ()):
            d = j - i
            counts[d] = counts.get(d, 0) + 1
    # valeurs presque identiques : on regroupe les décalages voisins
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    out = []
    for d, n in ranked:
        if n < 3:
            break
        if all(abs(d - o) > 2 for o in out):
            out.append(d)
        if len(out) >= TOP_SHIFTS:
            break
    return out


def _longest_run(a, b, shift):
    """Plus longue suite de points correspondants pour ce décalage -> (début dans a, fin dans a) en points."""
    lo = max(0, -shift)
    hi = min(len(a), len(b) - shift)
    best = (0, 0)
    run_start = None
    last_match = None
    for i in range(lo, hi):
        if (a[i] ^ b[i + shift]).bit_count() <= MAX_BIT_DIFF:
            if run_start is None or i - last_match > MAX_GAP:
                if run_start is not None and last_match - run_start > best[1] - best[0]:
                    best = (run_start, last_match)
                run_start = i
            last_match = i
    if run_start is not None and last_match - run_start > best[1] - best[0]:
        best = (run_start, last_match)
    return best


def common_segment(a, b, min_s, max_s):
    """Passage commun le plus long (entre min_s et max_s secondes) -> ((a_debut, a_fin), (b_debut, b_fin)) en s."""
    if not a or not b:
        return None
    found = None
    for shift in _shifts(a, b):
        s, e = _longest_run(a, b, shift)
        dur = (e - s) * POINT
        if dur < min_s:
            continue
        if dur > max_s:
            # passage commun trop long (récapitulatif, même scène) : on garde la fin, côté chanson
            s = e - int(max_s / POINT)
            dur = max_s
        if not found or dur > found[0]:
            found = (dur, (s * POINT, e * POINT), ((s + shift) * POINT, (e + shift) * POINT))
    return (found[1], found[2]) if found else None


class FingerprintCache:
    """Empreintes gardées sur disque (le calcul relit le fichier) ; clé = fichier + passage analysé."""

    def __init__(self, base):
        self.base = base
        os.makedirs(base, exist_ok=True)

    def _path(self, item, part, minutes):
        size = item["file_size"] or 0
        return os.path.join(self.base, f"f{size}_{item['file_mtime'] or 0}_{part}{minutes}.json")

    def get(self, item, part, minutes):
        """part = 'head' (début) ou 'tail' (fin). Renvoie (empreinte, début du passage en s)."""
        path = self._path(item, part, minutes)
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            return d["fp"], d["start"]
        except (OSError, ValueError, KeyError):
            pass
        duration = (item["duration_ms"] or 0) / 1000
        length = min(minutes * 60, duration)
        start = 0.0 if part == "head" else max(0.0, duration - length)
        fp = fingerprint(item["key"], start, length)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"fp": fp, "start": start}, f)
        os.replace(path + ".tmp", path)
        return fp, start

    def drop_unknown(self, keep):
        """Supprime les empreintes des fichiers qui ne sont plus dans les bibliothèques."""
        n = 0
        for name in os.listdir(self.base):
            if name.split("_head")[0].split("_tail")[0] not in keep:
                try:
                    os.remove(os.path.join(self.base, name))
                    n += 1
                except OSError:
                    pass
        return n


def detect_season(episodes, cache, part, minutes, min_s, max_s, cancelled=lambda: False):
    """Pour une saison : {id d'épisode: (début, fin) en secondes dans le fichier} du passage commun.

    Chaque épisode est comparé à ses voisins (suivant, précédent, puis un plus loin) ; on garde le plus
    long passage commun trouvé."""
    prints = {}

    def fp(ep):
        if ep["id"] not in prints:
            try:
                prints[ep["id"]] = cache.get(ep, part, minutes)
            except FingerprintError as e:
                log.info("Empreinte de %s : %s", ep["key"], e)
                prints[ep["id"]] = None
        return prints[ep["id"]]

    found = {}
    n = len(episodes)
    for i, ep in enumerate(episodes):
        if cancelled():
            break
        mine = fp(ep)
        if not mine or not mine[0]:
            continue
        best = None
        for j in (i + 1, i - 1, i + 2, i - 2):
            if not 0 <= j < n:
                continue
            other = fp(episodes[j])
            if not other or not other[0]:
                continue
            seg = common_segment(mine[0], other[0], min_s, max_s)
            if seg and (not best or seg[0][1] - seg[0][0] > best[1] - best[0]):
                best = seg[0]
            if best and best[1] - best[0] >= min(max_s, 80):
                break  # chanson complète trouvée : inutile de comparer davantage
        if best:
            found[ep["id"]] = (mine[1] + best[0], mine[1] + best[1])
    return found
