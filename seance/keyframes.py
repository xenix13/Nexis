"""Images clés d'un MKV lues dans son index (« Cues »), sans parcourir le fichier.

En remux (vidéo copiée), les segments HLS ne peuvent être
coupés que sur des images clés. Connaître leurs instants permet d'annoncer au lecteur une playlist
exacte, où chaque segment correspond vraiment à ce que ffmpeg produira.
Seuls l'en-tête et l'index sont lus (quelques Ko), même sur un partage réseau.
"""
import struct

SEGMENT = 0x18538067
SEEKHEAD, SEEK, SEEK_ID, SEEK_POS = 0x114D9B74, 0x4DBB, 0x53AB, 0x53AC
INFO, TIMESCALE, DURATION = 0x1549A966, 0x2AD7B1, 0x4489
TRACKS, TRACK_ENTRY, TRACK_NUMBER, TRACK_TYPE = 0x1654AE6B, 0xAE, 0xD7, 0x83
CUES, CUE_POINT, CUE_TIME, CUE_TRACK_POS, CUE_TRACK = 0x1C53BB6B, 0xBB, 0xB3, 0xB7, 0xF7
CLUSTER = 0x1F43B675
MAX_CUES = 64 * 1024 * 1024


class Reader:
    def __init__(self, f):
        self.f = f

    def read(self, pos, n):
        self.f.seek(pos)
        return self.f.read(n)


def _vint(buf, i, mask=True):
    if i >= len(buf):
        raise ValueError("fin de données")
    b = buf[i]
    length = 1
    m = 0x80
    while length <= 8 and not (b & m):
        m >>= 1
        length += 1
    if length > 8 or i + length > len(buf):
        raise ValueError("entier EBML invalide")
    value = b & (m - 1) if mask else b
    for k in range(1, length):
        value = (value << 8) | buf[i + k]
    unknown = mask and value == (1 << (7 * length)) - 1
    return value, length, unknown


def _elements(buf, start=0, end=None):
    end = len(buf) if end is None else end
    i = start
    while i < end:
        eid, n, _ = _vint(buf, i, mask=False)
        i += n
        size, n, unknown = _vint(buf, i)
        i += n
        if unknown:
            size = end - i
        yield eid, i, min(size, end - i)
        i += size


def _uint(buf, i, size):
    return int.from_bytes(buf[i:i + size], "big") if size else 0


def _float(buf, i, size):
    if size == 4:
        return struct.unpack(">f", buf[i:i + 4])[0]
    if size == 8:
        return struct.unpack(">d", buf[i:i + 8])[0]
    return 0.0


def _element_at(r, pos, max_size):
    head = r.read(pos, 16)
    eid, n1, _ = _vint(head, 0, mask=False)
    size, n2, unknown = _vint(head, n1)
    if unknown or size > max_size:
        return eid, None
    return eid, r.read(pos + n1 + n2, size)


def mkv_keyframes(path):
    """-> (durée en secondes, [instants des images clés de la piste vidéo, en secondes]) ou None."""
    try:
        with open(path, "rb") as f:
            return _parse(Reader(f))
    except (OSError, ValueError, IndexError, struct.error):
        return None


def _parse(r):
    head = r.read(0, 64)
    if head[:4] != b"\x1a\x45\xdf\xa3":
        return None
    # EBML header, puis Segment
    _, n, _ = _vint(head, 0, mask=False)
    size, n2, _ = _vint(head, n)
    pos = n + n2 + size
    seg_head = r.read(pos, 16)
    eid, n, _ = _vint(seg_head, 0, mask=False)
    if eid != SEGMENT:
        return None
    _, n2, _ = _vint(seg_head, n)
    seg_data = pos + n + n2
    # premiers Ko du segment : SeekHead, Info, Tracks (souvent avant le premier Cluster)
    first = r.read(seg_data, 256 * 1024)
    positions, scale, duration, video_track = {}, 1000000, None, None
    for eid, i, size in _elements(first):
        if eid == CLUSTER:
            break
        if eid == SEEKHEAD:
            for sid, si, ssize in _elements(first, i, i + size):
                if sid != SEEK:
                    continue
                target, where = None, None
                for kid, ki, ksize in _elements(first, si, si + ssize):
                    if kid == SEEK_ID:
                        target = _uint(first, ki, ksize)
                    elif kid == SEEK_POS:
                        where = _uint(first, ki, ksize)
                if target and where is not None:
                    positions.setdefault(target, where)
        elif eid == INFO:
            for kid, ki, ksize in _elements(first, i, i + size):
                if kid == TIMESCALE:
                    scale = _uint(first, ki, ksize) or 1000000
                elif kid == DURATION:
                    duration = _float(first, ki, ksize)
        elif eid == TRACKS:
            video_track = _video_track(first, i, i + size)
    if video_track is None and TRACKS in positions:
        eid, data = _element_at(r, seg_data + positions[TRACKS], 4 * 1024 * 1024)
        if eid == TRACKS and data:
            video_track = _video_track(data, 0, len(data))
    if INFO in positions and duration is None:
        eid, data = _element_at(r, seg_data + positions[INFO], 1024 * 1024)
        if eid == INFO and data:
            for kid, ki, ksize in _elements(data):
                if kid == TIMESCALE:
                    scale = _uint(data, ki, ksize) or 1000000
                elif kid == DURATION:
                    duration = _float(data, ki, ksize)
    if CUES not in positions or video_track is None:
        return None
    eid, cues = _element_at(r, seg_data + positions[CUES], MAX_CUES)
    if eid != CUES or not cues:
        return None
    times = []
    for eid, i, size in _elements(cues):
        if eid != CUE_POINT:
            continue
        t, tracks = None, set()
        for kid, ki, ksize in _elements(cues, i, i + size):
            if kid == CUE_TIME:
                t = _uint(cues, ki, ksize)
            elif kid == CUE_TRACK_POS:
                for tid, ti, tsize in _elements(cues, ki, ki + ksize):
                    if tid == CUE_TRACK:
                        tracks.add(_uint(cues, ti, tsize))
        if t is not None and video_track in tracks:
            times.append(t * scale / 1e9)
    times = sorted(set(times))
    if len(times) < 2:
        return None
    dur = duration * scale / 1e9 if duration else None
    return dur, times


def _video_track(buf, start, end):
    for eid, i, size in _elements(buf, start, end):
        if eid != TRACK_ENTRY:
            continue
        number, kind = None, None
        for kid, ki, ksize in _elements(buf, i, i + size):
            if kid == TRACK_NUMBER:
                number = _uint(buf, ki, ksize)
            elif kid == TRACK_TYPE:
                kind = _uint(buf, ki, ksize)
        if kind == 1 and number:
            return number
    return None


def segments_from_keyframes(keyframes, duration, target=6.0):
    """Découpage : on coupe sur la première image clé qui dépasse chaque multiple de `target`.
    -> liste de (début, durée)."""
    out, last, cut = [], 0.0, target
    for k in keyframes:
        if k >= cut:
            out.append((last, k - last))
            last = k
            cut += target  # comme ffmpeg : une cible par segment, sans rattrapage
    if duration and duration > last:
        out.append((last, duration - last))
    return out


def equal_segments(duration, target=6.0):
    out, t = [], 0.0
    while t < duration - 0.001:
        d = min(target, duration - t)
        out.append((t, d))
        t += target
    return out
