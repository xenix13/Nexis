"""Fabrique un petit fichier de sous-titres PGS (.sup) pour les tests : un rectangle rouge affiché un moment."""
import struct


def _seg(pts, kind, payload):
    return b"PG" + struct.pack(">IIBH", int(pts * 90000), 0, kind, len(payload)) + payload


def _rle(w, h, color):
    line = bytearray()
    left = w
    while left:
        n = min(left, 16383)
        line += bytes([0x00, 0xC0 | (n >> 8), n & 0xFF, color]) if n >= 64 else bytes([0x00, 0x80 | n, color])
        left -= n
    line += b"\x00\x00"
    return bytes(line) * h


def make_sup(path, start=1.0, end=4.0, size=(1920, 1080), box=(200, 60), pos=(860, 900)):
    w, h = size
    bw, bh = box
    x, y = pos
    rle = _rle(bw, bh, 1)
    show = b"".join([
        _seg(start, 0x16, struct.pack(">HHBHBBBB", w, h, 0x10, 0, 0x80, 0, 0, 1) + struct.pack(">HBBHH", 0, 0, 0, x, y)),
        _seg(start, 0x17, struct.pack(">BBHHHH", 1, 0, x, y, bw, bh)),
        _seg(start, 0x14, struct.pack(">BB", 0, 0) + bytes([1, 81, 240, 90, 255])),
        _seg(start, 0x15, struct.pack(">HBB", 0, 0, 0xC0) + (len(rle) + 4).to_bytes(3, "big")
             + struct.pack(">HH", bw, bh) + rle),
        _seg(start, 0x80, b""),
    ])
    hide = b"".join([
        _seg(end, 0x16, struct.pack(">HHBHBBBB", w, h, 0x10, 1, 0x00, 0, 0, 0)),
        _seg(end, 0x17, struct.pack(">BBHHHH", 1, 0, x, y, bw, bh)),
        _seg(end, 0x80, b""),
    ])
    with open(path, "wb") as f:
        f.write(show + hide)
