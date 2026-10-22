"""Noms de famille des polices TrueType / OpenType (table « name »), sans dépendance.

Le moteur de sous-titres du navigateur (libass) demande les polices par nom de famille : on indexe
les polices jointes au MKV pour ne télécharger que celles qu'un sous-titre utilise vraiment.
"""
import struct

NAME_IDS = (1, 4, 6, 16)  # famille, nom complet, nom PostScript, famille typographique


def _decode(data, platform, encoding):
    if platform in (0, 3) or (platform == 2 and encoding == 1):
        return data.decode("utf-16-be", "ignore")
    return data.decode("latin-1", "ignore")


def _names_at(buf, offset):
    if offset + 12 > len(buf):
        return set()
    num_tables = struct.unpack_from(">H", buf, offset + 4)[0]
    for i in range(min(num_tables, 64)):
        rec = offset + 12 + i * 16
        if rec + 16 > len(buf):
            break
        tag, _, toff, tlen = struct.unpack_from(">4sIII", buf, rec)
        if tag != b"name":
            continue
        if toff + 6 > len(buf):
            return set()
        _, count, strings = struct.unpack_from(">HHH", buf, toff)
        out = set()
        for j in range(min(count, 1000)):
            r = toff + 6 + j * 12
            if r + 12 > len(buf):
                break
            platform, encoding, _, name_id, length, soff = struct.unpack_from(">HHHHHH", buf, r)
            if name_id not in NAME_IDS:
                continue
            start = toff + strings + soff
            name = _decode(buf[start:start + length], platform, encoding).strip().strip("\x00").strip()
            if name and len(name) < 120:
                out.add(name.lower())
        return out
    return set()


def font_names(buf):
    """-> ensemble de noms (en minuscules) déclarés par la police ou la collection."""
    try:
        if buf[:4] == b"ttcf":
            n = struct.unpack_from(">I", buf, 8)[0]
            names = set()
            for i in range(min(n, 32)):
                names |= _names_at(buf, struct.unpack_from(">I", buf, 12 + i * 4)[0])
            return names
        if buf[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"):
            return _names_at(buf, 0)
    except struct.error:
        pass
    return set()
